const express = require("express");
const { getDb } = require("../db");
const { safeJsonParse } = require("../lib/validation");

const router = express.Router();

// ── Cached prepared statements ──────────────────────────────────────
let _stmts = null;

function getStatements() {
  if (_stmts) return _stmts;
  const db = getDb();

  _stmts = {
    // Overall error summary
    errorSummary: db.prepare(`
      SELECT
        COUNT(*) as total_errors,
        COUNT(DISTINCT session_id) as affected_sessions,
        (SELECT COUNT(*) FROM events) as total_events,
        (SELECT COUNT(*) FROM sessions) as total_sessions,
        MIN(timestamp) as first_error,
        MAX(timestamp) as last_error
      FROM events
      WHERE event_type IN ('error', 'tool_error', 'agent_error')
    `),

    // Error rate over time (daily buckets) — uses a JOIN instead of
    // a correlated subquery to count total events per day.
    errorRateDaily: db.prepare(`
      SELECT
        err.day,
        err.error_count,
        COALESCE(dc.total_events, 0) as total_events
      FROM (
        SELECT DATE(timestamp) as day, COUNT(*) as error_count
        FROM events
        WHERE event_type IN ('error', 'tool_error', 'agent_error')
        GROUP BY DATE(timestamp)
      ) err
      LEFT JOIN (
        SELECT DATE(timestamp) as day, COUNT(*) as total_events
        FROM events
        GROUP BY DATE(timestamp)
      ) dc ON err.day = dc.day
      ORDER BY err.day DESC
      LIMIT ?
    `),

    // Errors by type
    errorsByType: db.prepare(`
      SELECT
        event_type,
        COUNT(*) as count,
        COUNT(DISTINCT session_id) as affected_sessions,
        AVG(duration_ms) as avg_duration_ms,
        MIN(timestamp) as first_seen,
        MAX(timestamp) as last_seen
      FROM events
      WHERE event_type IN ('error', 'tool_error', 'agent_error')
      GROUP BY event_type
      ORDER BY count DESC
    `),

    // Errors by model — uses a JOIN to compute total calls per model
    // instead of a correlated subquery (which counted all-time calls
    // rather than being scoped to any window).
    errorsByModel: db.prepare(`
      SELECT
        COALESCE(e.model, 'unknown') as model,
        COUNT(*) as error_count,
        COALESCE(mc.total_calls, 0) as total_calls,
        COUNT(DISTINCT e.session_id) as affected_sessions
      FROM events e
      LEFT JOIN (
        SELECT model, COUNT(*) as total_calls
        FROM events
        WHERE model IS NOT NULL
        GROUP BY model
      ) mc ON e.model = mc.model
      WHERE e.event_type IN ('error', 'tool_error', 'agent_error')
      GROUP BY e.model
      ORDER BY error_count DESC
      LIMIT ?
    `),

    // Errors by agent
    errorsByAgent: db.prepare(`
      SELECT
        s.agent_name,
        COUNT(e.event_id) as error_count,
        COUNT(DISTINCT e.session_id) as error_sessions,
        (SELECT COUNT(*) FROM sessions s2
         WHERE s2.agent_name = s.agent_name) as total_sessions
      FROM events e
      JOIN sessions s ON e.session_id = s.session_id
      WHERE e.event_type IN ('error', 'tool_error', 'agent_error')
      GROUP BY s.agent_name
      ORDER BY error_count DESC
      LIMIT ?
    `),

    // Top error messages (extracted from output_data)
    topErrors: db.prepare(`
      SELECT
        event_type,
        output_data,
        model,
        COUNT(*) as occurrences,
        MIN(timestamp) as first_seen,
        MAX(timestamp) as last_seen,
        COUNT(DISTINCT session_id) as affected_sessions
      FROM events
      WHERE event_type IN ('error', 'tool_error', 'agent_error')
      GROUP BY event_type, output_data, model
      ORDER BY occurrences DESC
      LIMIT ?
    `),

    // Error sessions — sessions that ended in error
    errorSessions: db.prepare(`
      SELECT
        s.session_id,
        s.agent_name,
        s.started_at,
        s.ended_at,
        s.total_tokens_in,
        s.total_tokens_out,
        COALESCE(err.error_count, 0) as error_count,
        COALESCE(evt.total_events, 0) as total_events
      FROM sessions s
      LEFT JOIN (
        SELECT session_id, COUNT(*) as error_count
        FROM events
        WHERE event_type IN ('error', 'tool_error', 'agent_error')
        GROUP BY session_id
      ) err ON err.session_id = s.session_id
      LEFT JOIN (
        SELECT session_id, COUNT(*) as total_events
        FROM events
        GROUP BY session_id
      ) evt ON evt.session_id = s.session_id
      WHERE s.status = 'error'
      ORDER BY s.started_at DESC
      LIMIT ?
    `),

    // Hourly error distribution
    errorsByHour: db.prepare(`
      SELECT
        CAST(strftime('%H', timestamp) AS INTEGER) as hour,
        COUNT(*) as error_count
      FROM events
      WHERE event_type IN ('error', 'tool_error', 'agent_error')
      GROUP BY hour
      ORDER BY hour ASC
    `),

    // Mean time between errors (for MTBF calculation)
    errorTimestamps: db.prepare(`
      SELECT timestamp
      FROM events
      WHERE event_type IN ('error', 'tool_error', 'agent_error')
      ORDER BY timestamp ASC
    `),
  };

  return _stmts;
}

// ── Helper: extract error message from output_data ──────────────────
function extractErrorMessage(outputData, maxLen = 200) {
  const parsed = safeJsonParse(outputData, null);
  if (!parsed) {
    return typeof outputData === "string"
      ? outputData.slice(0, maxLen)
      : null;
  }
  // Try common error fields
  const msg =
    parsed.error ||
    parsed.message ||
    parsed.error_message ||
    parsed.detail ||
    parsed.reason;
  if (typeof msg === "string") return msg.slice(0, maxLen);
  return JSON.stringify(parsed).slice(0, maxLen);
}

// ── Helper: compute MTBF from sorted timestamps ─────────────────────
function computeMtbf(timestamps) {
  if (timestamps.length < 2) return null;
  let totalGapMs = 0;
  for (let i = 1; i < timestamps.length; i++) {
    const prev = new Date(timestamps[i - 1]).getTime();
    const curr = new Date(timestamps[i]).getTime();
    if (!isNaN(prev) && !isNaN(curr)) {
      totalGapMs += curr - prev;
    }
  }
  const avgGapMs = totalGapMs / (timestamps.length - 1);
  return {
    mean_ms: Math.round(avgGapMs),
    mean_seconds: Math.round(avgGapMs / 1000),
    mean_minutes: Math.round(avgGapMs / 60000 * 10) / 10,
  };
}

// ── GET /errors — Full error analytics dashboard ────────────────────
router.get("/", (req, res) => {
  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit) || 10, 1), 100);
    const days = Math.min(Math.max(parseInt(req.query.days) || 30, 1), 365);
    const stmts = getStatements();

    // Summary
    const summary = stmts.errorSummary.get();
    const errorRate =
      summary.total_events > 0
        ? Math.round((summary.total_errors / summary.total_events) * 10000) / 100
        : 0;
    const sessionErrorRate =
      summary.total_sessions > 0
        ? Math.round(
            (summary.affected_sessions / summary.total_sessions) * 10000
          ) / 100
        : 0;

    // Error rate over time
    const rateOverTime = stmts.errorRateDaily.all(days).map((row) => ({
      day: row.day,
      error_count: row.error_count,
      total_events: row.total_events,
      error_rate:
        row.total_events > 0
          ? Math.round((row.error_count / row.total_events) * 10000) / 100
          : 0,
    }));

    // By type
    const byType = stmts.errorsByType.all();

    // By model
    const byModel = stmts.errorsByModel.all(limit).map((row) => ({
      ...row,
      error_rate:
        row.total_calls > 0
          ? Math.round((row.error_count / row.total_calls) * 10000) / 100
          : 0,
    }));

    // By agent
    const byAgent = stmts.errorsByAgent.all(limit).map((row) => ({
      ...row,
      error_rate:
        row.total_sessions > 0
          ? Math.round(
              (row.error_sessions / row.total_sessions) * 10000
            ) / 100
          : 0,
    }));

    // Top error patterns
    const topErrors = stmts.topErrors.all(limit).map((row) => ({
      event_type: row.event_type,
      message: extractErrorMessage(row.output_data),
      model: row.model || "unknown",
      occurrences: row.occurrences,
      first_seen: row.first_seen,
      last_seen: row.last_seen,
      affected_sessions: row.affected_sessions,
    }));

    // Error sessions
    const errorSessions = stmts.errorSessions.all(limit);

    // Hourly distribution
    const hourlyDistribution = stmts.errorsByHour.all();

    // MTBF
    const timestamps = stmts.errorTimestamps
      .all()
      .map((r) => r.timestamp);
    const mtbf = computeMtbf(timestamps);

    res.json({
      summary: {
        total_errors: summary.total_errors,
        affected_sessions: summary.affected_sessions,
        error_rate_percent: errorRate,
        session_error_rate_percent: sessionErrorRate,
        first_error: summary.first_error,
        last_error: summary.last_error,
        mtbf,
      },
      rate_over_time: rateOverTime,
      by_type: byType,
      by_model: byModel,
      by_agent: byAgent,
      top_errors: topErrors,
      error_sessions: errorSessions,
      hourly_distribution: hourlyDistribution,
    });
  } catch (err) {
    console.error("Error analytics failed:", err);
    res.status(500).json({ error: "Failed to compute error analytics" });
  }
});

// ── GET /errors/summary — Lightweight error summary ─────────────────
router.get("/summary", (req, res) => {
  try {
    const stmts = getStatements();
    const summary = stmts.errorSummary.get();
    const errorRate =
      summary.total_events > 0
        ? Math.round((summary.total_errors / summary.total_events) * 10000) / 100
        : 0;

    res.json({
      total_errors: summary.total_errors,
      affected_sessions: summary.affected_sessions,
      error_rate_percent: errorRate,
      first_error: summary.first_error,
      last_error: summary.last_error,
    });
  } catch (err) {
    console.error("Error summary failed:", err);
    res.status(500).json({ error: "Failed to compute error summary" });
  }
});

// ── GET /errors/by-type — Error breakdown by event type ─────────────
router.get("/by-type", (req, res) => {
  try {
    const stmts = getStatements();
    const byType = stmts.errorsByType.all();
    res.json({ by_type: byType });
  } catch (err) {
    console.error("Error by-type query failed:", err);
    res.status(500).json({ error: "Failed to query errors by type" });
  }
});

// ── GET /errors/by-model — Error breakdown by model ─────────────────
router.get("/by-model", (req, res) => {
  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit) || 10, 1), 100);
    const stmts = getStatements();
    const byModel = stmts.errorsByModel.all(limit).map((row) => ({
      ...row,
      error_rate:
        row.total_calls > 0
          ? Math.round((row.error_count / row.total_calls) * 10000) / 100
          : 0,
    }));
    res.json({ by_model: byModel });
  } catch (err) {
    console.error("Error by-model query failed:", err);
    res.status(500).json({ error: "Failed to query errors by model" });
  }
});

// ── GET /errors/by-agent — Error breakdown by agent ─────────────────
router.get("/by-agent", (req, res) => {
  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit) || 10, 1), 100);
    const stmts = getStatements();
    const byAgent = stmts.errorsByAgent.all(limit).map((row) => ({
      ...row,
      error_rate:
        row.total_sessions > 0
          ? Math.round(
              (row.error_sessions / row.total_sessions) * 10000
            ) / 100
          : 0,
    }));
    res.json({ by_agent: byAgent });
  } catch (err) {
    console.error("Error by-agent query failed:", err);
    res.status(500).json({ error: "Failed to query errors by agent" });
  }
});

module.exports = router;
