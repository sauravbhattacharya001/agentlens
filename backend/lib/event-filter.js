/**
 * Event-search filter and summary utilities.
 *
 * Extracted from routes/sessions.js to separate the pure query-building and
 * result-aggregation logic of GET /sessions/:id/events/search from HTTP
 * routing.  The route handler previously inlined ~90 lines of dynamic SQL
 * WHERE-clause construction plus a single-pass summary reducer; both are
 * pure functions of their inputs, so isolating them here makes the
 * SQL-injection-safety caps, LIKE escaping, and clause structure directly
 * unit-testable instead of only reachable through a live HTTP + SQLite round
 * trip.
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic; the
 * end-to-end route tests (tests/event-search.test.js) pin that equivalence.
 *
 * @module lib/event-filter
 */

const { escapeLikeWildcards } = require("./validation");

// Cap dynamic user input pushed into SQL to keep well under SQLite's ~999
// bound-variable limit and to bound query complexity from unbounded input.
const MAX_FILTER_VALUES = 20; // per comma-separated `type` / `model` filter
const MAX_SEARCH_TERMS = 10; // per free-text `q` query (6 LIKE binds each)

/**
 * Build the dynamic SQL WHERE fragment for an event search.
 *
 * Every SQL-compatible filter (type, model, token/duration thresholds, time
 * range, error/tool/reasoning flags, and full-text `q`) is pushed into the
 * WHERE clause so SQLite does the filtering instead of loading every event
 * into JS memory.  The returned `conditions` always starts with
 * `"session_id = ?"`, so `conditions.length > 1` means "extra filters are
 * active" (used by the route to decide whether a separate total-count query
 * is needed).
 *
 * @param {string} sessionId - Session ID (first bound parameter).
 * @param {Object} [query={}] - Express `req.query` (all values are strings).
 * @returns {{ conditions: string[], params: Array<string|number> }}
 *   SQL condition fragments (to be joined with " AND ") and their bound
 *   parameters, positionally aligned.
 */
function buildEventSearchFilter(sessionId, query = {}) {
  const conditions = ["session_id = ?"];
  const params = [sessionId];

  // Filter by event type (comma-separated, pushed to SQL via IN).
  // Cap at MAX_FILTER_VALUES to prevent SQLite parameter overflow from
  // unbounded user input.
  const typeFilter = query.type;
  if (typeFilter) {
    const types = typeFilter.split(",").map((t) => t.trim()).filter(Boolean).slice(0, MAX_FILTER_VALUES);
    if (types.length > 0) {
      conditions.push(`LOWER(event_type) IN (${types.map(() => "LOWER(?)").join(",")})`);
      params.push(...types);
    }
  }

  // Filter by model (comma-separated, substring match via LIKE).
  // Cap at MAX_FILTER_VALUES to prevent SQLite parameter overflow.
  const modelFilter = query.model;
  if (modelFilter) {
    const models = modelFilter.split(",").map((m) => m.trim()).filter(Boolean).slice(0, MAX_FILTER_VALUES);
    if (models.length > 0) {
      const modelClauses = models.map(() => "LOWER(model) LIKE ? ESCAPE '\\'");
      conditions.push(`model IS NOT NULL AND (${modelClauses.join(" OR ")})`);
      params.push(...models.map((m) => `%${escapeLikeWildcards(m).toLowerCase()}%`));
    }
  }

  // Filter by minimum total tokens.
  const minTokens = parseInt(query.min_tokens);
  if (Number.isFinite(minTokens) && minTokens > 0) {
    conditions.push("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) >= ?");
    params.push(minTokens);
  }

  // Filter by maximum total tokens.
  const maxTokens = parseInt(query.max_tokens);
  if (Number.isFinite(maxTokens) && maxTokens > 0) {
    conditions.push("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) <= ?");
    params.push(maxTokens);
  }

  // Filter by time range (ISO timestamps). Invalid dates are ignored.
  const after = query.after;
  if (after) {
    const afterDate = new Date(after);
    if (!isNaN(afterDate.getTime())) {
      conditions.push("timestamp >= ?");
      params.push(after);
    }
  }

  const before = query.before;
  if (before) {
    const beforeDate = new Date(before);
    if (!isNaN(beforeDate.getTime())) {
      conditions.push("timestamp <= ?");
      params.push(before);
    }
  }

  // Filter by minimum duration.
  const minDuration = parseFloat(query.min_duration_ms);
  if (Number.isFinite(minDuration) && minDuration > 0) {
    conditions.push("COALESCE(duration_ms, 0) >= ?");
    params.push(minDuration);
  }

  // Filter for error events only.
  if (query.errors === "true") {
    conditions.push("event_type IN ('error', 'agent_error', 'tool_error')");
  }

  // Filter for events with tool calls only.
  if (query.has_tools === "true") {
    conditions.push("tool_call IS NOT NULL AND tool_call != 'null'");
  }

  // Filter for events with reasoning only.
  if (query.has_reasoning === "true") {
    conditions.push("decision_trace IS NOT NULL AND decision_trace != 'null' AND decision_trace LIKE '%\"reasoning\"%'");
  }

  // Full-text search — push to SQL via LIKE across the searchable columns.
  // Cap at MAX_SEARCH_TERMS; each term adds 6 LIKE clauses with bound
  // parameters, so unbounded input could exceed SQLite's variable limit or
  // cause excessive query complexity.
  const q = query.q;
  if (q) {
    const searchTerms = q.split(/\s+/).filter(Boolean).slice(0, MAX_SEARCH_TERMS);
    for (const term of searchTerms) {
      const likeTerm = `%${escapeLikeWildcards(term)}%`;
      conditions.push(
        `(LOWER(COALESCE(input_data,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(output_data,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(tool_call,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(event_type,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(model,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(decision_trace,'')) LIKE LOWER(?) ESCAPE '\\')`
      );
      params.push(likeTerm, likeTerm, likeTerm, likeTerm, likeTerm, likeTerm);
    }
  }

  return { conditions, params };
}

/**
 * Reduce a list of parsed event rows into the search summary block in a
 * single pass (token totals, rounded total duration, and event-type / model
 * histograms).
 *
 * @param {Object[]} events - Parsed event rows (JSON columns already parsed).
 * @returns {{ tokens_in: number, tokens_out: number, total_tokens: number,
 *   total_duration_ms: number, event_types: Object<string,number>,
 *   models: Object<string,number> }}
 */
function summarizeEvents(events) {
  let totalTokensIn = 0;
  let totalTokensOut = 0;
  let totalDuration = 0;
  const eventTypes = {};
  const models = {};
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    totalTokensIn += e.tokens_in || 0;
    totalTokensOut += e.tokens_out || 0;
    totalDuration += e.duration_ms || 0;
    eventTypes[e.event_type] = (eventTypes[e.event_type] || 0) + 1;
    if (e.model) {
      models[e.model] = (models[e.model] || 0) + 1;
    }
  }
  return {
    tokens_in: totalTokensIn,
    tokens_out: totalTokensOut,
    total_tokens: totalTokensIn + totalTokensOut,
    total_duration_ms: Math.round(totalDuration * 100) / 100,
    event_types: eventTypes,
    models,
  };
}

module.exports = {
  MAX_FILTER_VALUES,
  MAX_SEARCH_TERMS,
  buildEventSearchFilter,
  summarizeEvents,
};
