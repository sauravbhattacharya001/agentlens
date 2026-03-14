const express = require("express");
const { getDb } = require("../db");
const { latencyStats, round2 } = require("../lib/stats");
const { wrapRoute } = require("../lib/request-helpers");
const { createCache, cacheMiddleware } = require("../lib/response-cache");

const router = express.Router();

// ── Response cache for analytics ────────────────────────────────────
// Analytics queries aggregate across all sessions/events — expensive
// on large datasets but results change infrequently. A 30-second TTL
// prevents redundant re-computation while keeping data reasonably fresh.
// Disabled in test environment to avoid stale data between test cases.
const analyticsCache = createCache({ ttlMs: 30000, maxEntries: 100 });
const isTest = process.env.NODE_ENV === "test";
const analyticsCacheMw = isTest
  ? function (_req, _res, next) { next(); }
  : cacheMiddleware(analyticsCache);

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
router.get("/", analyticsCacheMw, wrapRoute("fetch analytics", (req, res) => {
  const db = getDb();
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
}));

// GET /analytics/performance — Percentile latencies, throughput & efficiency
//
// Optimized: pushes aggregate computations (per-model, per-event-type stats,
// global totals, time span) to SQL instead of loading every event row into
// memory.  Only the sorted duration_ms column is fetched for percentile
// calculations that require the full distribution.  This reduces memory
// from O(rows × 6 columns) to O(rows × 1 column) for the heavy path,
// and eliminates JS-side reduce/map for totals and group stats entirely.
router.get("/performance", isTest ? analyticsCacheMw : cacheMiddleware(analyticsCache, { ttlMs: 15000 }), wrapRoute("fetch performance analytics", (req, res) => {
  const db = getDb();
  // Optional filters
  const agentName = req.query.agent;
  const model = req.query.model;
  const days = Math.min(Math.max(1, parseInt(req.query.days) || 30), 365);

    const cutoff = new Date(Date.now() - days * 86400000).toISOString();

    // ── Shared WHERE clause builder ──────────────────────────────
    let whereExtra = "";
    const params = [cutoff];
    if (agentName) {
      whereExtra += " AND s.agent_name = ?";
      params.push(agentName);
    }
    if (model) {
      whereExtra += " AND e.model = ?";
      params.push(model);
    }

    const baseWhere =
      `e.duration_ms IS NOT NULL AND e.duration_ms > 0 AND e.timestamp >= ?` +
      whereExtra;

    // ── 1. Global aggregates (single row) ────────────────────────
    const globalSql = `
      SELECT
        COUNT(*)                              AS cnt,
        COALESCE(SUM(e.duration_ms), 0)       AS total_dur,
        COALESCE(SUM(e.tokens_in), 0)         AS total_tok_in,
        COALESCE(SUM(e.tokens_out), 0)        AS total_tok_out,
        MIN(e.timestamp)                      AS min_ts,
        MAX(e.timestamp)                      AS max_ts
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${baseWhere}`;

    const g = db.prepare(globalSql).get(...params);

    if (!g || g.cnt === 0) {
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

    // ── 2. Per-model aggregates (pushed to SQL) ──────────────────
    const modelSql = `
      SELECT
        COALESCE(e.model, '(unknown)')       AS grp,
        COUNT(*)                              AS cnt,
        COALESCE(SUM(e.duration_ms), 0)       AS total_dur,
        COALESCE(SUM(e.tokens_in), 0)         AS tok_in,
        COALESCE(SUM(e.tokens_out), 0)        AS tok_out,
        MIN(e.duration_ms)                    AS min_dur,
        MAX(e.duration_ms)                    AS max_dur,
        AVG(e.duration_ms)                    AS avg_dur
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${baseWhere}
      GROUP BY grp
      ORDER BY total_dur DESC`;

    const modelRows = db.prepare(modelSql).all(...params);

    // ── 3. Single scan for global + per-group durations ──────────
    // Merges the old separate "sorted durations" query and "grouped
    // durations" query into one pass. Since the outer ORDER BY is
    // duration_ms ASC, items appended to each per-group array are
    // already sorted — no JS re-sort needed.  Eliminates a full
    // table scan (was 2 scans of events, now 1).
    const groupedDurSql = `
      SELECT COALESCE(e.model, '(unknown)') AS model_grp, e.event_type AS type_grp, e.duration_ms
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${baseWhere}
      ORDER BY e.duration_ms ASC`;

    const groupedDurRows = db.prepare(groupedDurSql).all(...params);

    // Build global durations array + per-group maps in one pass
    const durations = new Array(groupedDurRows.length);
    const modelDurMap = Object.create(null);
    const typeDurMap = Object.create(null);
    for (let i = 0; i < groupedDurRows.length; i++) {
      const r = groupedDurRows[i];
      durations[i] = r.duration_ms;
      if (!modelDurMap[r.model_grp]) modelDurMap[r.model_grp] = [];
      modelDurMap[r.model_grp].push(r.duration_ms);
      if (!typeDurMap[r.type_grp]) typeDurMap[r.type_grp] = [];
      typeDurMap[r.type_grp].push(r.duration_ms);
    }

    const byModel = Object.create(null);
    for (const row of modelRows) {
      const totalTokens = row.tok_in + row.tok_out;
      const groupDurs = modelDurMap[row.grp] || [];
      byModel[row.grp] = {
        count: row.cnt,
        latency: latencyStats(groupDurs),
        tokens: {
          total_in: row.tok_in,
          total_out: row.tok_out,
          total: totalTokens,
          avg_per_call: Math.round(totalTokens / row.cnt),
        },
        tokens_per_second: row.total_dur > 0
          ? round2(totalTokens / (row.total_dur / 1000))
          : 0,
      };
    }

    // ── 4. Per-event-type aggregates (pushed to SQL) ───────────
    const typeSql = `
      SELECT
        e.event_type                          AS grp,
        COUNT(*)                              AS cnt,
        COALESCE(SUM(e.duration_ms), 0)       AS total_dur,
        MIN(e.duration_ms)                    AS min_dur,
        MAX(e.duration_ms)                    AS max_dur,
        AVG(e.duration_ms)                    AS avg_dur
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${baseWhere}
      GROUP BY grp
      ORDER BY cnt DESC`;

    const typeRows = db.prepare(typeSql).all(...params);

    // typeDurMap already populated from the combined query above

    const byType = Object.create(null);
    for (const row of typeRows) {
      const groupDurs = typeDurMap[row.grp] || [];
      const stats = latencyStats(groupDurs);
      byType[row.grp] = { count: row.cnt, ...stats };
    }

    // ── 5. Assemble response ─────────────────────────────────────
    const totalTokens = g.total_tok_in + g.total_tok_out;
    const minTs = new Date(g.min_ts).getTime();
    const maxTs = new Date(g.max_ts).getTime();
    const timeSpanMs = Math.max(1, maxTs - minTs);
    const timeSpanHours = timeSpanMs / 3600000;

    res.json({
      period_days: days,
      filters: { agent: agentName || null, model: model || null },
      sample_size: g.cnt,
      latency: latencyStats(durations),
      throughput: {
        total_events: g.cnt,
        total_tokens: totalTokens,
        events_per_hour: round2(g.cnt / timeSpanHours),
        tokens_per_hour: Math.round(totalTokens / timeSpanHours),
        tokens_per_second: g.total_dur > 0
          ? round2(totalTokens / (g.total_dur / 1000))
          : 0,
      },
      efficiency: {
        avg_tokens_per_event: Math.round(totalTokens / g.cnt),
        avg_tokens_in_per_event: Math.round(g.total_tok_in / g.cnt),
        avg_tokens_out_per_event: Math.round(g.total_tok_out / g.cnt),
        output_input_ratio: g.total_tok_in > 0
          ? Math.round((g.total_tok_out / g.total_tok_in) * 1000) / 1000
          : 0,
        avg_duration_per_token_ms: totalTokens > 0
          ? Math.round((g.total_dur / totalTokens) * 1000) / 1000
          : 0,
      },
      by_model: byModel,
      by_event_type: byType,
    });
}));

// GET /analytics/heatmap — Day-of-week × hour-of-day activity matrix
router.get("/heatmap", isTest ? analyticsCacheMw : cacheMiddleware(analyticsCache, { ttlMs: 60000 }), wrapRoute("fetch heatmap data", (req, res) => {
  const db = getDb();
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
}));

// GET /analytics/costs — Aggregate cost breakdown by model and over time
//
// Joins event token data with model_pricing to compute estimated costs
// across all sessions.  Returns per-model cost breakdown, daily cost
// trend, and overall totals.  Optional ?days=N parameter (default 30).
router.get("/costs", analyticsCacheMw, wrapRoute("fetch cost analytics", (req, res) => {
  const db = getDb();
  const days = Math.min(Math.max(1, parseInt(req.query.days) || 30), 365);
  const cutoff = new Date(Date.now() - days * 86400000).toISOString();

  // Load pricing map (DB + defaults)
  const pricingRows = db.prepare("SELECT * FROM model_pricing ORDER BY model ASC").all();
  const pricingMap = {};
  for (const row of pricingRows) {
    pricingMap[row.model.toLowerCase()] = {
      input: row.input_cost_per_1m,
      output: row.output_cost_per_1m,
      currency: row.currency || "USD",
    };
  }

  // Default pricing fallback
  const DEFAULT_PRICING = {
    "gpt-4": { input: 30.00, output: 60.00 },
    "gpt-4-turbo": { input: 10.00, output: 30.00 },
    "gpt-4o": { input: 2.50, output: 10.00 },
    "gpt-4o-mini": { input: 0.15, output: 0.60 },
    "gpt-3.5-turbo": { input: 0.50, output: 1.50 },
    "claude-3-opus": { input: 15.00, output: 75.00 },
    "claude-3-sonnet": { input: 3.00, output: 15.00 },
    "claude-3-haiku": { input: 0.25, output: 1.25 },
    "claude-3.5-sonnet": { input: 3.00, output: 15.00 },
    "claude-4-opus": { input: 15.00, output: 75.00 },
    "claude-4-sonnet": { input: 3.00, output: 15.00 },
    "gemini-pro": { input: 0.50, output: 1.50 },
    "gemini-1.5-pro": { input: 1.25, output: 5.00 },
    "gemini-1.5-flash": { input: 0.075, output: 0.30 },
  };
  for (const [model, prices] of Object.entries(DEFAULT_PRICING)) {
    if (!pricingMap[model]) {
      pricingMap[model] = { input: prices.input, output: prices.output, currency: "USD" };
    }
  }

  // Helper: find pricing for a model (exact or fuzzy prefix match)
  function findPricing(model) {
    if (!model) return null;
    const lower = model.toLowerCase();
    if (pricingMap[lower]) return pricingMap[lower];
    const delimiters = new Set(["-", "_", ".", "/", " "]);
    let bestKey = null;
    let bestLen = 0;
    for (const key of Object.keys(pricingMap)) {
      if (lower.startsWith(key) && key.length > bestLen) {
        if (key.length === lower.length || delimiters.has(lower[key.length])) {
          bestKey = key;
          bestLen = key.length;
        }
      }
    }
    return bestKey ? pricingMap[bestKey] : null;
  }

  // Aggregate by model
  const modelRows = db.prepare(`
    SELECT
      model,
      COUNT(*) as call_count,
      COALESCE(SUM(tokens_in), 0) as total_tokens_in,
      COALESCE(SUM(tokens_out), 0) as total_tokens_out
    FROM events
    WHERE model IS NOT NULL AND model != '' AND timestamp >= ?
    GROUP BY model
    ORDER BY (COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0)) DESC
  `).all(cutoff);

  let totalCost = 0;
  let totalInputCost = 0;
  let totalOutputCost = 0;
  const modelCosts = [];
  const unmatchedModels = [];

  for (const row of modelRows) {
    const pricing = findPricing(row.model);
    if (pricing) {
      const inputCost = (row.total_tokens_in / 1_000_000) * pricing.input;
      const outputCost = (row.total_tokens_out / 1_000_000) * pricing.output;
      const cost = inputCost + outputCost;
      totalCost += cost;
      totalInputCost += inputCost;
      totalOutputCost += outputCost;
      modelCosts.push({
        model: row.model,
        call_count: row.call_count,
        tokens_in: row.total_tokens_in,
        tokens_out: row.total_tokens_out,
        input_cost: Math.round(inputCost * 10000) / 10000,
        output_cost: Math.round(outputCost * 10000) / 10000,
        total_cost: Math.round(cost * 10000) / 10000,
        percent: 0,  // filled below
      });
    } else {
      unmatchedModels.push(row.model);
    }
  }

  // Fill percent
  for (const mc of modelCosts) {
    mc.percent = totalCost > 0
      ? Math.round((mc.total_cost / totalCost) * 10000) / 100
      : 0;
  }

  // Daily cost trend
  const dailyRows = db.prepare(`
    SELECT
      DATE(timestamp) as day,
      model,
      COALESCE(SUM(tokens_in), 0) as tokens_in,
      COALESCE(SUM(tokens_out), 0) as tokens_out
    FROM events
    WHERE model IS NOT NULL AND model != '' AND timestamp >= ?
    GROUP BY DATE(timestamp), model
    ORDER BY day ASC
  `).all(cutoff);

  const dailyCosts = {};
  for (const row of dailyRows) {
    const pricing = findPricing(row.model);
    if (!pricing) continue;
    const cost = (row.tokens_in / 1_000_000) * pricing.input
               + (row.tokens_out / 1_000_000) * pricing.output;
    if (!dailyCosts[row.day]) {
      dailyCosts[row.day] = { day: row.day, cost: 0, input_cost: 0, output_cost: 0 };
    }
    dailyCosts[row.day].cost += cost;
    dailyCosts[row.day].input_cost += (row.tokens_in / 1_000_000) * pricing.input;
    dailyCosts[row.day].output_cost += (row.tokens_out / 1_000_000) * pricing.output;
  }

  const dailyTrend = Object.values(dailyCosts).map(d => ({
    day: d.day,
    cost: Math.round(d.cost * 10000) / 10000,
    input_cost: Math.round(d.input_cost * 10000) / 10000,
    output_cost: Math.round(d.output_cost * 10000) / 10000,
  }));

  // Average daily cost
  const avgDailyCost = dailyTrend.length > 0
    ? totalCost / dailyTrend.length
    : 0;

  // Projected monthly cost (30-day extrapolation)
  const projectedMonthlyCost = avgDailyCost * 30;

  res.json({
    period_days: days,
    total_cost: Math.round(totalCost * 10000) / 10000,
    total_input_cost: Math.round(totalInputCost * 10000) / 10000,
    total_output_cost: Math.round(totalOutputCost * 10000) / 10000,
    avg_daily_cost: Math.round(avgDailyCost * 10000) / 10000,
    projected_monthly_cost: Math.round(projectedMonthlyCost * 100) / 100,
    currency: "USD",
    by_model: modelCosts,
    daily_trend: dailyTrend,
    unmatched_models: unmatchedModels,
  });
}));

// GET /analytics/cache — Cache statistics (for monitoring)
router.get("/cache", wrapRoute("fetch cache stats", (req, res) => {
  res.json(analyticsCache.stats());
}));

module.exports = router;
module.exports.analyticsCache = analyticsCache;
