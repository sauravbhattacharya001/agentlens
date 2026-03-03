const express = require("express");
const { getDb } = require("../db");
const { safeJsonParse } = require("../lib/validation");
const { percentile, latencyStats, groupEventStats, buildGroupPerf, round2 } = require("../lib/stats");

const router = express.Router();

// ── Cached prepared statements for analytics ────────────────────────
// These are read-only aggregation queries — safe to prepare once and
// reuse on every request, avoiding repeated SQL compilation overhead.
let _analyticsStmts = null;

function getAnalyticsStatements() {
  if (_analyticsStmts) return _analyticsStmts;
  const db = getDb();

  _analyticsStmts = {
    sessionStats: db.prepare(
      `SELECT
        COUNT(*) as total_sessions,
        COALESCE(SUM(total_tokens_in), 0) as total_tokens_in,
        COALESCE(SUM(total_tokens_out), 0) as total_tokens_out,
        COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
        AVG(total_tokens_in + total_tokens_out) as avg_tokens_per_session,
        SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_sessions,
        MIN(started_at) as earliest_session,
        MAX(started_at) as latest_session
      FROM sessions`
    ),
    topAgents: db.prepare(
      `SELECT
        agent_name,
        COUNT(*) as session_count,
        COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
        AVG(total_tokens_in + total_tokens_out) as avg_tokens
      FROM sessions
      GROUP BY agent_name
      ORDER BY total_tokens DESC
      LIMIT 10`
    ),
    modelUsage: db.prepare(
      `SELECT
        model,
        COUNT(*) as call_count,
        COALESCE(SUM(tokens_in), 0) as total_tokens_in,
        COALESCE(SUM(tokens_out), 0) as total_tokens_out,
        COALESCE(SUM(tokens_in + tokens_out), 0) as total_tokens,
        AVG(duration_ms) as avg_duration_ms
      FROM events
      WHERE model IS NOT NULL AND model != ''
      GROUP BY model
      ORDER BY total_tokens DESC`
    ),
    eventTypes: db.prepare(
      `SELECT
        event_type,
        COUNT(*) as count
      FROM events
      WHERE event_type NOT IN ('session_start', 'session_end')
      GROUP BY event_type
      ORDER BY count DESC`
    ),
    sessionsOverTime: db.prepare(
      `SELECT
        DATE(started_at) as day,
        COUNT(*) as session_count,
        COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens
      FROM sessions
      GROUP BY DATE(started_at)
      ORDER BY day DESC
      LIMIT 90`
    ),
    hourlyActivity: db.prepare(
      `SELECT
        CAST(strftime('%H', timestamp) AS INTEGER) as hour,
        COUNT(*) as event_count
      FROM events
      GROUP BY hour
      ORDER BY hour ASC`
    ),
    durationStats: db.prepare(
      `SELECT
        AVG(
          CASE WHEN ended_at IS NOT NULL
            THEN (julianday(ended_at) - julianday(started_at)) * 86400000
            ELSE NULL
          END
        ) as avg_duration_ms,
        MIN(
          CASE WHEN ended_at IS NOT NULL
            THEN (julianday(ended_at) - julianday(started_at)) * 86400000
            ELSE NULL
          END
        ) as min_duration_ms,
        MAX(
          CASE WHEN ended_at IS NOT NULL
            THEN (julianday(ended_at) - julianday(started_at)) * 86400000
            ELSE NULL
          END
        ) as max_duration_ms
      FROM sessions
      WHERE ended_at IS NOT NULL`
    ),
    eventCount: db.prepare(`SELECT COUNT(*) as total FROM events`),
  };

  return _analyticsStmts;
}

// GET /analytics — Aggregate statistics across all sessions
router.get("/", (req, res) => {
  const db = getDb();

  try {
    const stmts = getAnalyticsStatements();

    // Run all queries inside a single deferred transaction for a
    // consistent snapshot and to avoid acquiring/releasing the WAL
    // read-lock 8 separate times.
    const result = db.transaction(() => {
      const sessionStats = stmts.sessionStats.get();
      const topAgents = stmts.topAgents.all();
      const modelUsage = stmts.modelUsage.all();
      const eventTypes = stmts.eventTypes.all();
      const sessionsOverTime = stmts.sessionsOverTime.all();
      const hourlyActivity = stmts.hourlyActivity.all();
      const durationStats = stmts.durationStats.get();
      const eventCount = stmts.eventCount.get();

      return { sessionStats, topAgents, modelUsage, eventTypes, sessionsOverTime, hourlyActivity, durationStats, eventCount };
    })();

    const { sessionStats, topAgents, modelUsage, eventTypes, sessionsOverTime, hourlyActivity, durationStats, eventCount } = result;

    // ── Error rate ─────────────────────────────────────────────────
    const errorRate =
      sessionStats.total_sessions > 0
        ? Math.round(
            (sessionStats.error_sessions / sessionStats.total_sessions) * 10000
          ) / 100
        : 0;

    res.json({
      overview: {
        total_sessions: sessionStats.total_sessions || 0,
        active_sessions: sessionStats.active_sessions || 0,
        completed_sessions: sessionStats.completed_sessions || 0,
        error_sessions: sessionStats.error_sessions || 0,
        error_rate: errorRate,
        total_events: eventCount.total || 0,
        total_tokens: sessionStats.total_tokens || 0,
        total_tokens_in: sessionStats.total_tokens_in || 0,
        total_tokens_out: sessionStats.total_tokens_out || 0,
        avg_tokens_per_session: Math.round(
          sessionStats.avg_tokens_per_session || 0
        ),
        earliest_session: sessionStats.earliest_session,
        latest_session: sessionStats.latest_session,
      },
      duration: {
        avg_ms: Math.round(durationStats.avg_duration_ms || 0),
        min_ms: Math.round(durationStats.min_duration_ms || 0),
        max_ms: Math.round(durationStats.max_duration_ms || 0),
      },
      top_agents: topAgents.map((a) => ({
        agent_name: a.agent_name,
        session_count: a.session_count,
        total_tokens: a.total_tokens || 0,
        avg_tokens: Math.round(a.avg_tokens || 0),
      })),
      model_usage: modelUsage.map((m) => ({
        model: m.model,
        call_count: m.call_count,
        total_tokens: m.total_tokens || 0,
        total_tokens_in: m.total_tokens_in || 0,
        total_tokens_out: m.total_tokens_out || 0,
        avg_duration_ms: Math.round((m.avg_duration_ms || 0) * 100) / 100,
      })),
      event_types: eventTypes,
      sessions_over_time: sessionsOverTime.reverse(),
      hourly_activity: hourlyActivity,
    });
  } catch (err) {
    console.error("Error fetching analytics:", err);
    res.status(500).json({ error: "Failed to fetch analytics" });
  }
});

// GET /analytics/performance — Percentile latencies, throughput & efficiency
router.get("/performance", (req, res) => {
  const db = getDb();

  try {
    // Optional filters
    const agentName = req.query.agent;
    const model = req.query.model;
    const days = Math.min(Math.max(1, parseInt(req.query.days) || 30), 365);

    const cutoff = new Date(Date.now() - days * 86400000).toISOString();

    // Build dynamic query for events with duration
    let eventsQuery = `
      SELECT e.duration_ms, e.tokens_in, e.tokens_out, e.model, e.timestamp, e.event_type
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE e.duration_ms IS NOT NULL AND e.duration_ms > 0
        AND e.timestamp >= ?
    `;
    const params = [cutoff];

    if (agentName) {
      eventsQuery += " AND s.agent_name = ?";
      params.push(agentName);
    }
    if (model) {
      eventsQuery += " AND e.model = ?";
      params.push(model);
    }

    eventsQuery += " ORDER BY e.duration_ms ASC";

    const events = db.prepare(eventsQuery).all(...params);

    if (events.length === 0) {
      return res.json({
        period_days: days,
        filters: { agent: agentName || null, model: model || null },
        sample_size: 0,
        latency: null,
        throughput: null,
        efficiency: null,
        by_model: {},
        by_event_type: {},
      });
    }

    // ── Aggregate stats using shared utility ──────────────────────
    const durations = events.map((e) => e.duration_ms);
    const totalDuration = durations.reduce((a, b) => a + b, 0);
    const totalTokensIn = events.reduce((s, e) => s + (e.tokens_in || 0), 0);
    const totalTokensOut = events.reduce((s, e) => s + (e.tokens_out || 0), 0);
    const totalTokens = totalTokensIn + totalTokensOut;

    // Time span for throughput calculation
    const timestamps = events.map((e) => new Date(e.timestamp).getTime());
    const timeSpanMs = Math.max(1, Math.max(...timestamps) - Math.min(...timestamps));
    const timeSpanHours = timeSpanMs / 3600000;

    // Per-model and per-event-type breakdowns via shared helpers
    const modelPerf = buildGroupPerf(
      groupEventStats(events, (e) => e.model || "(unknown)")
    );

    const typeGroups = groupEventStats(events, (e) => e.event_type);
    const typePerf = {};
    for (const [t, data] of Object.entries(typeGroups)) {
      const stats = latencyStats(data.durations);
      typePerf[t] = { count: data.count, ...stats };
    }

    res.json({
      period_days: days,
      filters: { agent: agentName || null, model: model || null },
      sample_size: events.length,
      latency: latencyStats(durations),
      throughput: {
        total_events: events.length,
        total_tokens: totalTokens,
        events_per_hour: round2(events.length / timeSpanHours),
        tokens_per_hour: Math.round(totalTokens / timeSpanHours),
        tokens_per_second: totalDuration > 0
          ? round2(totalTokens / (totalDuration / 1000))
          : 0,
      },
      efficiency: {
        avg_tokens_per_event: Math.round(totalTokens / events.length),
        avg_tokens_in_per_event: Math.round(totalTokensIn / events.length),
        avg_tokens_out_per_event: Math.round(totalTokensOut / events.length),
        output_input_ratio: totalTokensIn > 0
          ? Math.round((totalTokensOut / totalTokensIn) * 1000) / 1000
          : 0,
        avg_duration_per_token_ms: totalTokens > 0
          ? Math.round((totalDuration / totalTokens) * 1000) / 1000
          : 0,
      },
      by_model: modelPerf,
      by_event_type: typePerf,
    });
  } catch (err) {
    console.error("Error fetching performance analytics:", err);
    res.status(500).json({ error: "Failed to fetch performance analytics" });
  }
});

// GET /analytics/heatmap — Day-of-week × hour-of-day activity matrix
router.get("/heatmap", (req, res) => {
  const db = getDb();

  try {
    const days = Math.min(Math.max(1, parseInt(req.query.days) || 30), 365);
    const metric = ["events", "tokens", "sessions"].includes(req.query.metric)
      ? req.query.metric
      : "events";

    const cutoff = new Date(Date.now() - days * 86400000).toISOString();

    let sql;
    if (metric === "sessions") {
      sql = `
        SELECT
          CAST(strftime('%w', started_at) AS INTEGER) as dow,
          CAST(strftime('%H', started_at) AS INTEGER) as hour,
          COUNT(*) as value
        FROM sessions
        WHERE started_at >= ?
        GROUP BY dow, hour
      `;
    } else if (metric === "tokens") {
      sql = `
        SELECT
          CAST(strftime('%w', timestamp) AS INTEGER) as dow,
          CAST(strftime('%H', timestamp) AS INTEGER) as hour,
          COALESCE(SUM(tokens_in + tokens_out), 0) as value
        FROM events
        WHERE timestamp >= ?
        GROUP BY dow, hour
      `;
    } else {
      sql = `
        SELECT
          CAST(strftime('%w', timestamp) AS INTEGER) as dow,
          CAST(strftime('%H', timestamp) AS INTEGER) as hour,
          COUNT(*) as value
        FROM events
        WHERE timestamp >= ?
        GROUP BY dow, hour
      `;
    }

    const rows = db.prepare(sql).all(cutoff);

    // Build 7×24 matrix (Sun=0 .. Sat=6, hours 0-23)
    const matrix = Array.from({ length: 7 }, () => Array(24).fill(0));
    let maxValue = 0;

    for (const row of rows) {
      matrix[row.dow][row.hour] = row.value;
      if (row.value > maxValue) maxValue = row.value;
    }

    const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

    // Flatten for easy consumption
    const cells = [];
    for (let d = 0; d < 7; d++) {
      for (let h = 0; h < 24; h++) {
        if (matrix[d][h] > 0) {
          cells.push({
            day: d,
            day_name: dayNames[d],
            hour: h,
            value: matrix[d][h],
            intensity: maxValue > 0 ? Math.round((matrix[d][h] / maxValue) * 100) / 100 : 0,
          });
        }
      }
    }

    // Day and hour totals
    const dayTotals = dayNames.map((name, i) => ({
      day: i,
      day_name: name,
      total: matrix[i].reduce((a, b) => a + b, 0),
    }));

    const hourTotals = Array.from({ length: 24 }, (_, h) => ({
      hour: h,
      total: matrix.reduce((sum, row) => sum + row[h], 0),
    }));

    // Peak detection
    let peakDay = 0, peakHour = 0, peakValue = 0;
    for (let d = 0; d < 7; d++) {
      for (let h = 0; h < 24; h++) {
        if (matrix[d][h] > peakValue) {
          peakValue = matrix[d][h];
          peakDay = d;
          peakHour = h;
        }
      }
    }

    res.json({
      period_days: days,
      metric,
      max_value: maxValue,
      peak: {
        day: peakDay,
        day_name: dayNames[peakDay],
        hour: peakHour,
        value: peakValue,
      },
      matrix,
      cells,
      day_totals: dayTotals,
      hour_totals: hourTotals,
    });
  } catch (err) {
    console.error("Error fetching heatmap:", err);
    res.status(500).json({ error: "Failed to fetch heatmap data" });
  }
});

module.exports = router;
