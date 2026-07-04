/**
 * Session-search filter utilities.
 *
 * Extracted from routes/sessions.js to separate the pure query-building logic
 * of GET /sessions/search from HTTP routing — the sibling of lib/event-filter
 * (which does the same for GET /sessions/:id/events/search).  The route
 * handler previously inlined ~75 lines of dynamic SQL construction: sort-field
 * whitelisting, WHERE-clause building across the free-text/agent/status/date/
 * token filters, the ALL-tags INNER JOIN sub-query, and the echoed
 * applied-filter summary.  All of it is a pure function of `req.query`, so
 * isolating it here makes the SQL-injection-safety caps, LIKE escaping, sort
 * whitelist, and clause structure directly unit-testable instead of only
 * reachable through a live HTTP + SQLite round trip.
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic; the
 * end-to-end route tests (tests/sessions.test.js, "GET /sessions/search")
 * continue to pin that equivalence.
 *
 * @module lib/session-filter
 */

const { isValidStatus, escapeLikeWildcards } = require("./validation");

// Cap dynamic user input pushed into SQL to keep well under SQLite's ~999
// bound-variable limit and to bound query complexity from unbounded input.
const MAX_SEARCH_TERMS = 10; // per free-text `q` query (2 LIKE binds each)
const MAX_TAG_VALUES = 20; // per comma-separated `tags` filter

// Sort fields the caller is allowed to order by.  Anything else falls back to
// "started_at" so user input can never reach the SQL string un-whitelisted.
const SORT_FIELDS = ["started_at", "total_tokens", "agent_name", "status"];

/**
 * Resolve the requested sort into a whitelisted `{ sortBy, sortOrder,
 * sortColumn }`.  `sortBy` is echoed back to the client; `sortColumn` is the
 * SQL expression (total_tokens maps to the summed token expression);
 * `sortOrder` is "ASC" or "DESC" (defaults to DESC).
 *
 * @param {Object} [query={}] - Express `req.query`.
 * @returns {{ sortBy: string, sortOrder: "ASC"|"DESC", sortColumn: string }}
 */
function resolveSessionSort(query = {}) {
  const sortBy = SORT_FIELDS.includes(query.sort) ? query.sort : "started_at";
  const sortOrder = query.order === "asc" ? "ASC" : "DESC";
  const sortColumn = sortBy === "total_tokens"
    ? "(s.total_tokens_in + s.total_tokens_out)"
    : `s.${sortBy}`;
  return { sortBy, sortOrder, sortColumn };
}

/**
 * Build the dynamic query pieces for a session search.
 *
 * Every SQL-compatible filter (free-text `q`, agent substring, status, date
 * range, and token thresholds) is pushed into the WHERE clause, and an
 * ALL-tags membership filter is expressed as an INNER JOIN sub-query so
 * SQLite does the work instead of loading every session into JS memory.  The
 * returned `params` are positionally aligned for both the count query
 * (`... ${tagJoin} ${whereClause}`) and the data query (which appends LIMIT /
 * OFFSET), so callers bind them in that order.
 *
 * The tag sub-query's bound values are `unshift`-ed to the front of `params`
 * because `tagJoin` textually precedes the WHERE clause in the final SQL —
 * preserving the exact ordering the route relied on.
 *
 * @param {Object} [query={}] - Express `req.query` (all values are strings).
 * @returns {{ conditions: string[], params: Array<string|number>,
 *   tagJoin: string, whereClause: string, sortBy: string,
 *   sortOrder: "ASC"|"DESC", sortColumn: string }}
 */
function buildSessionSearchFilter(query = {}) {
  const { sortBy, sortOrder, sortColumn } = resolveSessionSort(query);

  // Build dynamic WHERE clauses
  const conditions = [];
  const params = [];

  // Full-text search across agent_name and metadata
  const q = query.q;
  if (q) {
    // Cap at MAX_SEARCH_TERMS to prevent parameter overflow and excessive
    // query complexity (each term adds 2 LIKE parameters).
    const terms = q.trim().split(/\s+/).filter(Boolean).slice(0, MAX_SEARCH_TERMS);
    for (const term of terms) {
      const escaped = escapeLikeWildcards(term);
      conditions.push("(s.agent_name LIKE ? ESCAPE '\\' OR s.metadata LIKE ? ESCAPE '\\')");
      params.push(`%${escaped}%`, `%${escaped}%`);
    }
  }

  // Agent name filter (exact or substring)
  const agent = query.agent;
  if (agent) {
    conditions.push("s.agent_name LIKE ? ESCAPE '\\'");
    params.push(`%${escapeLikeWildcards(agent)}%`);
  }

  // Status filter
  const status = query.status;
  if (status && isValidStatus(status)) {
    conditions.push("s.status = ?");
    params.push(status);
  }

  // Date range filters
  const after = query.after;
  if (after) {
    conditions.push("s.started_at >= ?");
    params.push(after);
  }
  const before = query.before;
  if (before) {
    conditions.push("s.started_at <= ?");
    params.push(before);
  }

  // Token thresholds
  const minTokens = parseInt(query.min_tokens);
  if (Number.isFinite(minTokens) && minTokens > 0) {
    conditions.push("(s.total_tokens_in + s.total_tokens_out) >= ?");
    params.push(minTokens);
  }
  const maxTokens = parseInt(query.max_tokens);
  if (Number.isFinite(maxTokens) && maxTokens > 0) {
    conditions.push("(s.total_tokens_in + s.total_tokens_out) <= ?");
    params.push(maxTokens);
  }

  // Tag filter (comma-separated, sessions must have ALL specified tags)
  // Cap at MAX_TAG_VALUES to prevent SQLite variable-count overflow (limit
  // ~999) and excessive query complexity from unbounded user input.
  const tagFilter = query.tags;
  let tagJoin = "";
  if (tagFilter) {
    const tags = tagFilter.split(",").map((t) => t.trim()).filter(Boolean).slice(0, MAX_TAG_VALUES);
    if (tags.length > 0) {
      tagJoin = `INNER JOIN (
          SELECT session_id FROM session_tags
          WHERE tag IN (${tags.map(() => "?").join(",")})
          GROUP BY session_id
          HAVING COUNT(DISTINCT tag) = ?
        ) tf ON s.session_id = tf.session_id`;
      params.unshift(...tags, tags.length);
    }
  }

  const whereClause = conditions.length > 0
    ? "WHERE " + conditions.join(" AND ")
    : "";

  return { conditions, params, tagJoin, whereClause, sortBy, sortOrder, sortColumn };
}

/**
 * Build the echoed `filters` summary block returned to the client, describing
 * which filters were applied (null when absent).  Kept identical to the route's
 * previous inline object so the response shape is unchanged.
 *
 * @param {Object} [query={}] - Express `req.query`.
 * @returns {{ q: string|null, agent: string|null, status: string|null,
 *   after: string|null, before: string|null, min_tokens: number|null,
 *   max_tokens: number|null, tags: string[]|null }}
 */
function buildSessionFilterSummary(query = {}) {
  const minTokens = parseInt(query.min_tokens);
  const maxTokens = parseInt(query.max_tokens);
  const tagFilter = query.tags;
  return {
    q: query.q || null,
    agent: query.agent || null,
    status: query.status || null,
    after: query.after || null,
    before: query.before || null,
    min_tokens: Number.isFinite(minTokens) ? minTokens : null,
    max_tokens: Number.isFinite(maxTokens) ? maxTokens : null,
    tags: tagFilter ? tagFilter.split(",").map((t) => t.trim()).filter(Boolean) : null,
  };
}

module.exports = {
  MAX_SEARCH_TERMS,
  MAX_TAG_VALUES,
  SORT_FIELDS,
  resolveSessionSort,
  buildSessionSearchFilter,
  buildSessionFilterSummary,
};
