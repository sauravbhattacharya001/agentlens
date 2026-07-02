const express = require("express");
const { getDb } = require("../db");
const { latencyStats, round2 } = require("../lib/stats");
const { wrapRoute, parseDays, daysAgoCutoff } = require("../lib/request-helpers");
const { createCache, cacheMiddleware } = require("../lib/response-cache");
const { loadPricingMap } = require("../lib/pricing");
const { createLazyStatements } = require("../lib/lazy-statements");
const { buildHeatmap } = require("../lib/heatmap");
const { rollUpCosts } = require("../lib/cost-rollup");

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

// ── Cached prepared statements for /performance endpoint ────────────
// The performance endpoint has 4 possible filter combinations (none,
// agent-only, model-only, both). Pre-compiled once via createLazyStatements
// to avoid db.prepare() re-compilation on every request.

function _buildPerfVariant(db, where) {
  return {
    global: db.prepare(`
      SELECT
        COUNT(*)                              AS cnt,
        COALESCE(SUM(e.duration_ms), 0)       AS total_dur,
        COALESCE(SUM(e.tokens_in), 0)         AS total_tok_in,
        COALESCE(SUM(e.tokens_out), 0)        AS total_tok_out,
        MIN(e.timestamp)                      AS min_ts,
        MAX(e.timestamp)                      AS max_ts
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${where}`),
    modelAgg: db.prepare(`
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
      WHERE ${where}
      GROUP BY grp
      ORDER BY total_dur DESC`),
    groupedDur: db.prepare(`
      SELECT COALESCE(e.model, '(unknown)') AS model_grp, e.event_type AS type_grp, e.duration_ms
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${where}
      ORDER BY e.duration_ms ASC`),
    typeAgg: db.prepare(`
      SELECT
        e.event_type                          AS grp,
        COUNT(*)                              AS cnt,
        COALESCE(SUM(e.duration_ms), 0)       AS total_dur,
        MIN(e.duration_ms)                    AS min_dur,
        MAX(e.duration_ms)                    AS max_dur,
        AVG(e.duration_ms)                    AS avg_dur
      FROM events e
      INNER JOIN sessions s ON e.session_id = s.session_id
      WHERE ${where}
      GROUP BY grp
      ORDER BY cnt DESC`),
  };
}

const getPerfStatements = createLazyStatements((db) => {
  const baseWhere = "e.duration_ms IS NOT NULL AND e.duration_ms > 0 AND e.timestamp >= ?";
  const variants = {
    none:  baseWhere,
    agent: baseWhere + " AND s.agent_name = ?",
    model: baseWhere + " AND e.model = ?",
    both:  baseWhere + " AND s.agent_name = ? AND e.model = ?",
  };

  const stmts = {};
  for (const [key, where] of Object.entries(variants)) {
    stmts[key] = _buildPerfVariant(db, where);
  }
  return stmts;
});

// ── Cached prepared statements for analytics (overview endpoint) ────
const getAnalyticsStatements = createLazyStatements((db) => ({
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
}));

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
  // Optional filters
  const agentName = req.query.agent;
  const model = req.query.model;
  const days = parseDays(req.query.days);

    const cutoff = daysAgoCutoff(days);

    // ── Select pre-compiled statement variant ────────────────────
    // Instead of building SQL strings and calling db.prepare() on
    // every request (which re-compiles the query each time), we use
    // one of 4 pre-compiled variants based on which filters are active.
    const allPerfStmts = getPerfStatements();
    const variantKey = agentName && model ? "both"
                     : agentName ? "agent"
                     : model ? "model"
                     : "none";
    const stmts = allPerfStmts[variantKey];

    // Build params array matching the variant's placeholder order
    const params = [cutoff];
    if (agentName) params.push(agentName);
    if (model) params.push(model);

    // ── 1. Global aggregates (single row) ────────────────────────
    const g = stmts.global.get(...params);

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

    // ── 2. Per-model aggregates ──────────────────────────────────
    const modelRows = stmts.modelAgg.all(...params);

    // ── 3. Single scan for global + per-group durations ──────────
    // Since the query is ORDER BY duration_ms ASC, items appended to
    // each per-group array are already sorted — no JS re-sort needed.
    const groupedDurRows = stmts.groupedDur.all(...params);

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
        latency: latencyStats(groupDurs, row.total_dur),
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

    // ── 4. Per-event-type aggregates ─────────────────────────────
    const typeRows = stmts.typeAgg.all(...params);

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
      latency: latencyStats(durations, g.total_dur),
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

// ── Cached prepared statements for /heatmap endpoint ────────────────
const getHeatmapStatements = createLazyStatements((db) => ({
  sessions: db.prepare(`
    SELECT
      CAST(strftime('%w', started_at) AS INTEGER) as dow,
      CAST(strftime('%H', started_at) AS INTEGER) as hour,
      COUNT(*) as value
    FROM sessions
    WHERE started_at >= ?
    GROUP BY dow, hour
  `),
  tokens: db.prepare(`
    SELECT
      CAST(strftime('%w', timestamp) AS INTEGER) as dow,
      CAST(strftime('%H', timestamp) AS INTEGER) as hour,
      COALESCE(SUM(tokens_in + tokens_out), 0) as value
    FROM events
    WHERE timestamp >= ?
    GROUP BY dow, hour
  `),
  events: db.prepare(`
    SELECT
      CAST(strftime('%w', timestamp) AS INTEGER) as dow,
      CAST(strftime('%H', timestamp) AS INTEGER) as hour,
      COUNT(*) as value
    FROM events
    WHERE timestamp >= ?
    GROUP BY dow, hour
  `),
}));

// GET /analytics/heatmap — Day-of-week × hour-of-day activity matrix
router.get("/heatmap", isTest ? analyticsCacheMw : cacheMiddleware(analyticsCache, { ttlMs: 60000 }), wrapRoute("fetch heatmap data", (req, res) => {
  const days = parseDays(req.query.days);
    const metric = ["events", "tokens", "sessions"].includes(req.query.metric)
      ? req.query.metric
      : "events";

    const cutoff = daysAgoCutoff(days);

    const rows = getHeatmapStatements()[metric].all(cutoff);

    res.json(buildHeatmap(rows, { days, metric }));
}));

// GET /analytics/costs — Aggregate cost breakdown by model and over time
//
// Joins event token data with model_pricing to compute estimated costs
// across all sessions.  Returns per-model cost breakdown, daily cost
// trend, and overall totals.  Optional ?days=N parameter (default 30).
// ── Cached prepared statements for /costs endpoint ──────────────────
const getCostStatements = createLazyStatements((db) => ({
  modelAgg: db.prepare(`
    SELECT
      model,
      COUNT(*) as call_count,
      COALESCE(SUM(tokens_in), 0) as total_tokens_in,
      COALESCE(SUM(tokens_out), 0) as total_tokens_out
    FROM events
    WHERE model IS NOT NULL AND model != '' AND timestamp >= ?
    GROUP BY model
    ORDER BY (COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0)) DESC
  `),
  dailyAgg: db.prepare(`
    SELECT
      DATE(timestamp) as day,
      model,
      COALESCE(SUM(tokens_in), 0) as tokens_in,
      COALESCE(SUM(tokens_out), 0) as tokens_out
    FROM events
    WHERE model IS NOT NULL AND model != '' AND timestamp >= ?
    GROUP BY DATE(timestamp), model
    ORDER BY day ASC
  `),
}));

router.get("/costs", analyticsCacheMw, wrapRoute("fetch cost analytics", (req, res) => {
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);

  // Load merged pricing map (DB overrides + built-in defaults)
  const pricingMap = loadPricingMap();

  const stmts = getCostStatements();

  // Fetch grouped per-model and per-(day, model) token aggregates, then shape
  // them into the cost report.  All cost math (pricing match, rounding,
  // percent share, daily bucketing, projection) lives in lib/cost-rollup.
  const modelRows = stmts.modelAgg.all(cutoff);
  const dailyRows = stmts.dailyAgg.all(cutoff);

  res.json(rollUpCosts(modelRows, dailyRows, pricingMap, { days }));
}));

// GET /analytics/cache — Cache statistics (for monitoring)
router.get("/cache", wrapRoute("fetch cache stats", (req, res) => {
  res.json(analyticsCache.stats());
}));

module.exports = router;
module.exports.analyticsCache = analyticsCache;
