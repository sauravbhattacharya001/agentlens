/**
 * Alert-rule primitives — the pure catalog, comparator, and input bounds
 * that back the threshold-based alerting routes.
 *
 * Extracted from routes/alerts.js to separate the pure, IO-free decision
 * logic (which metrics/operators are valid, how a measured value compares
 * against a threshold, and how untrusted window/cooldown inputs are clamped)
 * from the Express routing and SQLite access.  These pieces were previously
 * inlined in the route module and only reachable through a live HTTP +
 * SQLite round trip: `compareValue` ran solely inside POST /alerts/evaluate,
 * and the metric/operator catalogs plus the security clamps were duplicated
 * across POST /rules and PUT /rules/:ruleId.  Isolating them here removes
 * that duplication and makes the threshold-breach comparison and the
 * SQL-injection-safety bounds directly unit-testable instead of only
 * exercisable end-to-end.
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic; the
 * end-to-end route tests (tests/alerts.test.js) pin that equivalence and the
 * direct suite (tests/alert-rules.test.js) pins each primitive in isolation.
 *
 * @module lib/alert-rules
 */

// ── Metric catalog ──────────────────────────────────────────────────
// Metrics users can alert on, each paired with the human-readable
// description surfaced by GET /alerts/metrics.  Kept as one ordered map
// so the catalog and its documentation cannot drift apart.
const METRIC_DESCRIPTIONS = {
  total_tokens: "Total tokens (in+out) across sessions in the time window",
  avg_tokens_per_session: "Average tokens per session in the time window",
  error_rate: "Percentage of error events in the time window (0-100)",
  avg_duration_ms: "Average event duration in milliseconds",
  max_duration_ms: "Maximum event duration in milliseconds",
  session_count: "Number of new sessions in the time window",
  event_count: "Number of events in the time window",
  token_rate: "Tokens per minute in the time window",
};

// Ordered list of valid metric names (the catalog keys).
const VALID_METRICS = Object.keys(METRIC_DESCRIPTIONS);

// Comparison operators a rule may use against its threshold.
const VALID_OPERATORS = ["<", ">", "<=", ">=", "==", "!="];

// ── Security limits ─────────────────────────────────────────────────
const MAX_WINDOW_MINUTES = 10080; // 7 days max — prevents expensive full-table scans
const MAX_COOLDOWN_MINUTES = 10080; // 7 days max
const MAX_NAME_LENGTH = 128;
const MAX_AGENT_FILTER_LENGTH = 256;
const MAX_ALERT_RULES = 100; // cap total rules to prevent DoS via evaluate endpoint

/**
 * Compare a measured metric value against a rule's threshold using the
 * rule's operator.  Pure decision function — an unknown operator yields
 * `false` (fail closed: never fire on a malformed rule).
 *
 * @param {number} value - The measured metric value for the window.
 * @param {string} operator - One of {@link VALID_OPERATORS}.
 * @param {number} threshold - The rule's configured threshold.
 * @returns {boolean} Whether the comparison is satisfied (i.e. the rule
 *   would trigger).
 */
function compareValue(value, operator, threshold) {
  switch (operator) {
    case "<":  return value < threshold;
    case ">":  return value > threshold;
    case "<=": return value <= threshold;
    case ">=": return value >= threshold;
    case "==": return value === threshold;
    case "!=": return value !== threshold;
    default:   return false;
  }
}

/**
 * Clamp a window-minutes input into the safe `[1, MAX_WINDOW_MINUTES]`
 * range.  Callers apply their own default (e.g. `Number(x) || 60`) before
 * clamping so the create/update semantics are preserved.
 *
 * @param {number} value - Numeric minutes (already defaulted by the caller).
 * @returns {number} The clamped window size.
 */
function clampWindowMinutes(value) {
  return Math.min(Math.max(1, value), MAX_WINDOW_MINUTES);
}

/**
 * Clamp a cooldown-minutes input into the safe `[0, MAX_COOLDOWN_MINUTES]`
 * range.  A zero cooldown (fire every evaluation) is intentionally allowed.
 *
 * @param {number} value - Numeric minutes (already defaulted by the caller).
 * @returns {number} The clamped cooldown.
 */
function clampCooldownMinutes(value) {
  return Math.min(Math.max(0, value), MAX_COOLDOWN_MINUTES);
}

module.exports = {
  METRIC_DESCRIPTIONS,
  VALID_METRICS,
  VALID_OPERATORS,
  MAX_WINDOW_MINUTES,
  MAX_COOLDOWN_MINUTES,
  MAX_NAME_LENGTH,
  MAX_AGENT_FILTER_LENGTH,
  MAX_ALERT_RULES,
  compareValue,
  clampWindowMinutes,
  clampCooldownMinutes,
};
