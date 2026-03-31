/**
 * Session metrics computation — shared utility.
 *
 * Extracted from the compare handler in routes/sessions.js so the
 * same logic is available to other routes, exports, or background
 * jobs without duplicating the metric-calculation code.
 */

const { safeJsonParse } = require("./validation");

/**
 * Compute aggregate metrics for a session and its events.
 *
 * @param {Object} session  - Session row from the database.
 * @param {Array}  events   - Parsed event rows (input_data/output_data
 *                            already parsed via safeJsonParse).
 * @returns {Object} Metrics object with token counts, event breakdown,
 *                   model usage, tool usage, timing, and error stats.
 */
function computeSessionMetrics(session, events) {
  const totalTokensIn = session.total_tokens_in || 0;
  const totalTokensOut = session.total_tokens_out || 0;
  const totalTokens = totalTokensIn + totalTokensOut;
  const eventCount = events.length;
  const totalDuration = events.reduce((sum, e) => sum + (e.duration_ms || 0), 0);
  const avgDuration = eventCount > 0 ? totalDuration / eventCount : 0;

  // Single-pass aggregation: models, event types, tools, error count
  const models = {};
  const eventTypes = {};
  const tools = {};
  let errorCount = 0;

  for (let i = 0; i < eventCount; i++) {
    const e = events[i];

    // Models used
    if (e.model) {
      let m = models[e.model];
      if (!m) {
        m = { calls: 0, tokens_in: 0, tokens_out: 0 };
        models[e.model] = m;
      }
      m.calls++;
      m.tokens_in += e.tokens_in || 0;
      m.tokens_out += e.tokens_out || 0;
    }

    // Event type breakdown
    eventTypes[e.event_type] = (eventTypes[e.event_type] || 0) + 1;

    // Tool usage
    const tc = e.tool_call;
    if (tc && tc.tool_name) {
      let t = tools[tc.tool_name];
      if (!t) {
        t = { calls: 0, total_duration: 0 };
        tools[tc.tool_name] = t;
      }
      t.calls++;
      t.total_duration += e.duration_ms || 0;
    }

    // Error count
    const et = e.event_type;
    if (et === "error" || et === "agent_error" || et === "tool_error") {
      errorCount++;
    }
  }

  // Session duration (wall clock)
  let sessionDurationMs = null;
  if (session.started_at && session.ended_at) {
    sessionDurationMs = new Date(session.ended_at) - new Date(session.started_at);
  }

  return {
    session_id: session.session_id,
    agent_name: session.agent_name,
    status: session.status,
    started_at: session.started_at,
    ended_at: session.ended_at,
    session_duration_ms: sessionDurationMs,
    tokens_in: totalTokensIn,
    tokens_out: totalTokensOut,
    total_tokens: totalTokens,
    event_count: eventCount,
    error_count: errorCount,
    total_processing_ms: Math.round(totalDuration * 100) / 100,
    avg_event_duration_ms: Math.round(avgDuration * 100) / 100,
    models,
    event_types: eventTypes,
    tools,
    metadata: safeJsonParse(session.metadata),
  };
}

/**
 * Compute percentage delta between two values.
 *
 * @param {number} a - Baseline value.
 * @param {number} b - Comparison value.
 * @returns {number} Percentage change (B relative to A), rounded to 2 decimals.
 */
function pctDelta(a, b) {
  if (a === 0 && b === 0) return 0;
  if (a === 0) return b > 0 ? 100 : -100;
  return Math.round(((b - a) / a) * 10000) / 100;
}

/**
 * Compute deltas between two metrics objects (B relative to A).
 * Extracts the repetitive per-field delta logic from the compare endpoint.
 *
 * @param {Object} metricsA - Baseline metrics (from computeSessionMetrics).
 * @param {Object} metricsB - Comparison metrics.
 * @returns {Object} Deltas with absolute and percent change for each numeric field.
 */
function computeDeltas(metricsA, metricsB) {
  const fields = [
    "total_tokens",
    "tokens_in",
    "tokens_out",
    "event_count",
    "error_count",
    "total_processing_ms",
    "avg_event_duration_ms",
  ];

  const deltas = {};
  for (const field of fields) {
    const a = metricsA[field] || 0;
    const b = metricsB[field] || 0;
    const raw = b - a;
    // Round ms fields to 2 decimal places
    const absolute = field.endsWith("_ms") ? Math.round(raw * 100) / 100 : raw;
    deltas[field] = { absolute, percent: pctDelta(a, b) };
  }
  return deltas;
}

module.exports = { computeSessionMetrics, pctDelta, computeDeltas };
