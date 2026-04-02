const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, isValidStatus, safeJsonParse, validateTag, escapeLikeWildcards } = require("../lib/validation");
const { generateExplanation } = require("../lib/explain");
const { computeSessionMetrics, pctDelta, computeDeltas } = require("../lib/session-metrics");
const { getTagStatements } = require("../lib/tag-statements");
const { parsePagination, requireSessionId, wrapRoute } = require("../lib/request-helpers");
const { toExportEvent, eventsToCsv, eventToCsvRow, buildJsonExport, ndjsonSessionLine, CSV_HEADERS } = require("../lib/csv-export");

const router = express.Router();

// ── LRU prepared-statement cache for dynamic SQL queries ────────────
// The event-search and session-search endpoints build SQL dynamically
// based on filter combinations.  Calling db.prepare() re-compiles the
// SQL every time, which adds ~0.1-0.5ms per call.  Since the number of
// distinct filter combos is small (users repeat the same searches), we
// cache the last N compiled statements keyed by their SQL string.
const STMT_CACHE_MAX = 64;
const _stmtCache = new Map();

/**
 * Get or compile a prepared statement, with LRU caching.
 * @param {string} sql - The SQL string to prepare.
 * @returns {import("better-sqlite3").Statement} Compiled statement.
 */
function cachedPrepare(sql) {
  let stmt = _stmtCache.get(sql);
  if (stmt) {
    // Move to end for LRU freshness
    _stmtCache.delete(sql);
    _stmtCache.set(sql, stmt);
    return stmt;
  }
  const db = getDb();
  stmt = db.prepare(sql);
  if (_stmtCache.size >= STMT_CACHE_MAX) {
    // Evict oldest entry
    const oldest = _stmtCache.keys().next().value;
    _stmtCache.delete(oldest);
  }
  _stmtCache.set(sql, stmt);
  return stmt;
}

// Sanitize a string for use in Content-Disposition filenames.
// Strips characters that could cause header injection ("  \r  \n),
// path traversal (/ \), or shell issues, and caps length.
function sanitizeFilename(name) {
  if (!name || typeof name !== "string") return "unknown";
  return name
    .replace(/[^\w.-]/g, "_")  // only alphanum, underscore, dot, hyphen
    .slice(0, 64);
}

// ── Shared event-parsing helper ─────────────────────────────────────
// Consolidates 4 identical inline `.map()` blocks that each parse
// the JSON text columns of an event row. This also fixes an
// inconsistency where the first usage omitted the `null` fallback
// for tool_call and decision_trace.

/**
 * Parse JSON text columns from a raw event database row into objects.
 * Consolidates 4 identical inline `.map()` blocks. Applies `safeJsonParse`
 * to `input_data`, `output_data`, `tool_call`, and `decision_trace` fields.
 *
 * @param {Object} e - Raw event row from the events table.
 * @returns {Object} Event row with JSON text columns parsed into objects.
 */
function parseEventRow(e) {
  return {
    ...e,
    input_data: safeJsonParse(e.input_data),
    output_data: safeJsonParse(e.output_data),
    tool_call: safeJsonParse(e.tool_call, null),
    decision_trace: safeJsonParse(e.decision_trace, null),
  };
}

/**
 * Look up a session by ID using cached statements.
 * Returns the session row or null (and sends 404 on the response).
 *
 * @param {string} id - Session ID.
 * @param {Object} res - Express response object.
 * @returns {Object|null} Session row, or null if not found (404 already sent).
 */
function fetchSessionOrFail(id, res) {
  const stmts = getSessionStatements();
  const session = stmts.getById.get(id);
  if (!session) {
    res.status(404).json({ error: "Session not found" });
    return null;
  }
  return session;
}

// ── Cached prepared statements ──────────────────────────────────────
// Lazily initialized once, reused across all requests to avoid
// re-compiling SQL on every call.
let _sessionStmts = null;

/**
 * Get lazily-initialized prepared SQL statements for session queries.
 * Statements are compiled once and reused across all requests.
 *
 * @returns {{ listAll: Statement, listByStatus: Statement, countAll: Statement,
 *             countByStatus: Statement, getById: Statement, eventsBySession: Statement }}
 */
function getSessionStatements() {
  if (_sessionStmts) return _sessionStmts;
  const db = getDb();

  _sessionStmts = {
    listAll: db.prepare("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?"),
    listByStatus: db.prepare("SELECT * FROM sessions WHERE status = ? ORDER BY started_at DESC LIMIT ? OFFSET ?"),
    countAll: db.prepare("SELECT COUNT(*) as count FROM sessions"),
    countByStatus: db.prepare("SELECT COUNT(*) as count FROM sessions WHERE status = ?"),
    getById: db.prepare("SELECT * FROM sessions WHERE session_id = ?"),
    // Uses the composite index idx_events_session_ts for efficient ordered retrieval
    eventsBySession: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC"),
    // Paginated events query — avoids loading entire event history into memory
    eventsBySessionPaged: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT ? OFFSET ?"),
    // Count events for a session (used for pagination metadata)
    countEventsBySession: db.prepare("SELECT COUNT(*) as total FROM events WHERE session_id = ?"),
    // Capped events query for diff endpoint — hard limit to prevent OOM
    eventsBySessionCapped: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?"),
    // Streaming iterator for NDJSON export — avoids loading all events into memory
    iterateEventsBySession: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC"),
    // Total event count for a session (used by event search)
    totalEventsBySession: db.prepare("SELECT COUNT(*) as total FROM events WHERE session_id = ?"),
  };

  return _sessionStmts;
}

/**
 * GET /sessions — List all sessions with pagination and optional filtering.
 *
 * @query {number} [limit=50] - Results per page (1-200).
 * @query {number} [offset=0] - Pagination offset.
 * @query {string} [status] - Filter by session status (e.g., "completed", "running").
 * @query {string} [tag] - Filter sessions by tag.
 * @returns {{ sessions: Object[], total: number }} Paginated session list.
 */
router.get("/", wrapRoute("list sessions", (req, res) => {
  const db = getDb();
  const { limit, offset } = parsePagination(req.query);
  const status = req.query.status;

  if (status && !isValidStatus(status)) {
    return res.status(400).json({ error: "Invalid status filter" });
  }

  const stmts = getSessionStatements();
  const tagFilter = req.query.tag ? validateTag(req.query.tag) : null;

    let sessions, total;

    if (tagFilter) {
      // Tag-filtered query (can't use cached prepared statements due to dynamic JOIN)
      const tagStmts = getTagStatements();
      sessions = tagStmts.sessionsByTag.all(tagFilter, limit, offset);
      const countResult = tagStmts.sessionsByTagCount.get(tagFilter);
      total = { count: countResult.count };
    } else if (status) {
      sessions = stmts.listByStatus.all(status, limit, offset);
      total = stmts.countByStatus.get(status);
    } else {
      sessions = stmts.listAll.all(limit, offset);
      total = stmts.countAll.get();
    }

    const parsed = sessions.map((s) => ({
      ...s,
      metadata: safeJsonParse(s.metadata),
    }));

    res.json({ sessions: parsed, total: total.count });
}));

/**
 * GET /sessions/search — Search and filter sessions with multi-criteria matching.
 * Must be defined before /:id to avoid matching "search" as a session ID.
 *
 * @query {string} [q] - Free-text search across session_id, agent_name, and metadata.
 * @query {string} [status] - Filter by status.
 * @query {string} [agent] - Filter by agent name (exact match).
 * @query {string} [from] - Start date (ISO 8601) for date range filter.
 * @query {string} [to] - End date (ISO 8601) for date range filter.
 * @query {string} [sort] - Sort field (started_at, duration_ms, total_tokens, cost_usd).
 * @query {string} [order=desc] - Sort order (asc or desc).
 * @query {number} [minTokens] - Minimum total_tokens filter.
 * @query {number} [maxTokens] - Maximum total_tokens filter.
 * @query {number} [minCost] - Minimum cost_usd filter.
 * @query {number} [maxCost] - Maximum cost_usd filter.
 * @query {number} [limit=50] - Results per page (1-200).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ sessions: Object[], total: number, filters: Object }} Filtered results with applied filter summary.
 */
router.get("/search", wrapRoute("search sessions", (req, res) => {
  const db = getDb();

  const { limit, offset } = parsePagination(req.query);
    const sortBy = ["started_at", "total_tokens", "agent_name", "status"].includes(req.query.sort)
      ? req.query.sort
      : "started_at";
    const sortOrder = req.query.order === "asc" ? "ASC" : "DESC";

    // Build dynamic WHERE clauses
    const conditions = [];
    const params = [];

    // Full-text search across agent_name and metadata
    const q = req.query.q;
    if (q) {
      const terms = q.trim().split(/\s+/).filter(Boolean);
      for (const term of terms) {
        const escaped = escapeLikeWildcards(term);
        conditions.push("(s.agent_name LIKE ? ESCAPE '\\' OR s.metadata LIKE ? ESCAPE '\\')");
        params.push(`%${escaped}%`, `%${escaped}%`);
      }
    }

    // Agent name filter (exact or substring)
    const agent = req.query.agent;
    if (agent) {
      conditions.push("s.agent_name LIKE ? ESCAPE '\\'");
      params.push(`%${escapeLikeWildcards(agent)}%`);
    }

    // Status filter
    const status = req.query.status;
    if (status && isValidStatus(status)) {
      conditions.push("s.status = ?");
      params.push(status);
    }

    // Date range filters
    const after = req.query.after;
    if (after) {
      conditions.push("s.started_at >= ?");
      params.push(after);
    }
    const before = req.query.before;
    if (before) {
      conditions.push("s.started_at <= ?");
      params.push(before);
    }

    // Token thresholds
    const minTokens = parseInt(req.query.min_tokens);
    if (Number.isFinite(minTokens) && minTokens > 0) {
      conditions.push("(s.total_tokens_in + s.total_tokens_out) >= ?");
      params.push(minTokens);
    }
    const maxTokens = parseInt(req.query.max_tokens);
    if (Number.isFinite(maxTokens) && maxTokens > 0) {
      conditions.push("(s.total_tokens_in + s.total_tokens_out) <= ?");
      params.push(maxTokens);
    }

    // Tag filter (comma-separated, sessions must have ALL specified tags)
    const tagFilter = req.query.tags;
    let tagJoin = "";
    if (tagFilter) {
      const tags = tagFilter.split(",").map(t => t.trim()).filter(Boolean);
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

    // Sort column mapping
    const sortColumn = sortBy === "total_tokens"
      ? "(s.total_tokens_in + s.total_tokens_out)"
      : `s.${sortBy}`;

    const whereClause = conditions.length > 0
      ? "WHERE " + conditions.join(" AND ")
      : "";

    // Count query
    const countSql = `SELECT COUNT(*) as count FROM sessions s ${tagJoin} ${whereClause}`;
    const total = cachedPrepare(countSql).get(...params).count;

    // Data query
    const dataSql = `SELECT s.* FROM sessions s ${tagJoin} ${whereClause} ORDER BY ${sortColumn} ${sortOrder} LIMIT ? OFFSET ?`;
    const sessions = cachedPrepare(dataSql).all(...params, limit, offset);

    // Batch-fetch tags for returned sessions
    const sessionIds = sessions.map(s => s.session_id);
    const tagMap = {};
    if (sessionIds.length > 0) {
      const placeholders = sessionIds.map(() => "?").join(",");
      const tagRows = cachedPrepare(
        `SELECT session_id, tag FROM session_tags WHERE session_id IN (${placeholders}) ORDER BY created_at ASC`
      ).all(...sessionIds);
      for (const row of tagRows) {
        if (!tagMap[row.session_id]) tagMap[row.session_id] = [];
        tagMap[row.session_id].push(row.tag);
      }
    }

    const enriched = sessions.map(s => ({
      ...s,
      metadata: safeJsonParse(s.metadata),
      tags: tagMap[s.session_id] || [],
    }));

    res.json({
      sessions: enriched,
      total,
      limit,
      offset,
      sort: sortBy,
      order: sortOrder.toLowerCase(),
      filters: {
        q: q || null,
        agent: agent || null,
        status: status || null,
        after: after || null,
        before: before || null,
        min_tokens: Number.isFinite(minTokens) ? minTokens : null,
        max_tokens: Number.isFinite(maxTokens) ? maxTokens : null,
        tags: tagFilter ? tagFilter.split(",").map(t => t.trim()).filter(Boolean) : null,
      },
    });
}));

// GET /sessions/tags — List all tags with session counts
// Tag collection routes (GET /tags, GET /by-tag/:tag) and per-session tag routes
// (GET/POST/DELETE /:id/tags) are in ./tags.js, mounted on the same /sessions prefix.

// (Must be before /:id to avoid matching "search" as a session ID)
/**
 * GET /sessions/search — Full-text session search.
 */
// GET /sessions/:id — Session detail with full event trace
/**
 * GET /sessions/:id — Get a single session with all its events and computed metrics.
 * Returns full session detail including parsed events, cost estimation,
 * duration, token counts, and comparison metrics (if previous session exists).
 *
 * @param {string} id - Session ID (path parameter).
 * @returns {Object} Session object with events array and metrics.
 * @returns {404} If session not found.
 */
router.get("/:id", requireSessionId, wrapRoute("fetch session", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  const session = fetchSessionOrFail(id, res);
  if (!session) return;

  // Paginated event loading — defaults to 200, capped at 1000
  const { limit: eventLimit, offset: eventOffset } = parsePagination(
    { limit: req.query.event_limit, offset: req.query.event_offset },
    { defaultLimit: 200, maxLimit: 1000 }
  );

  const stmts = getSessionStatements();
  const { total: totalEvents } = stmts.countEventsBySession.get(id);
  const events = stmts.eventsBySessionPaged.all(id, eventLimit, eventOffset);

  const parsedEvents = events.map(parseEventRow);

  res.json({
    ...session,
    metadata: safeJsonParse(session.metadata),
    total_events: totalEvents,
    event_limit: eventLimit,
    event_offset: eventOffset,
    events: parsedEvents,
  });
}));

// GET /sessions/:id/export — Export session data as JSON or CSV
/**
 * GET /sessions/:id/export — Export session data in JSON, CSV, or OpenTelemetry format.
 *
 * @param {string} id - Session ID (path parameter).
 * @query {string} [format=json] - Export format: "json", "csv", or "otlp".
 * @returns {Object|string} Exported data. CSV returns text/csv content type.
 *   JSON returns full session with events. OTLP returns OpenTelemetry-compatible trace.
 * @returns {404} If session not found.
 * @returns {400} If format is invalid.
 */
router.get("/:id/export", requireSessionId, wrapRoute("export session", (req, res) => {
  const db = getDb();
  const { id } = req.params;
  const format = (req.query.format || "json").toLowerCase();

  if (format !== "json" && format !== "csv" && format !== "ndjson") {
    return res.status(400).json({ error: "Invalid format. Use 'json', 'csv', or 'ndjson'." });
  }

  const session = fetchSessionOrFail(id, res);
  if (!session) return;

  const stmts = getSessionStatements();

  if (format === "ndjson") {
    // Streaming NDJSON export — uses .iterate() to avoid loading all
    // events into memory.  Previously this branch eagerly fetched every
    // event via eventsBySession.all() *before* checking the format,
    // defeating the purpose of streaming and doubling memory usage for
    // large sessions.  The shadowed `const stmts` redeclaration has
    // also been removed.
    const filename = `agentlens-${sanitizeFilename(session.agent_name)}-${id.slice(0, 8)}.ndjson`;
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "application/x-ndjson");

    res.write(ndjsonSessionLine(session) + "\n");

    const iter = stmts.iterateEventsBySession.iterate(id);
    for (const row of iter) {
      res.write(JSON.stringify({ _type: "event", ...toExportEvent(row, parseEventRow) }) + "\n");
    }
    return res.end();
  }

  // JSON and CSV formats — JSON needs all events in memory; CSV can stream
  if (format === "csv") {
    // Streaming CSV export — uses .iterate() to avoid loading all events
    // into memory, matching the NDJSON streaming approach.
    const filename = `agentlens-${sanitizeFilename(session.agent_name)}-${id.slice(0, 8)}.csv`;
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "text/csv");

    res.write(CSV_HEADERS.join(",") + "\n");

    const iter = stmts.iterateEventsBySession.iterate(id);
    for (const row of iter) {
      res.write(eventToCsvRow(toExportEvent(row, parseEventRow)) + "\n");
    }
    return res.end();
  }

  // JSON format needs all events in memory for transformation
  const events = stmts.eventsBySession.all(id);
  const parsedEvents = events.map(e => toExportEvent(e, parseEventRow));

  if (format === "json") {
    const exportData = buildJsonExport(session, parsedEvents);

    const filename = `agentlens-${sanitizeFilename(session.agent_name)}-${id.slice(0, 8)}.json`;
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "application/json");
    return res.json(exportData);
  }
}));

// GET /sessions/:id/events — Paginated events sub-route
/**
 * GET /sessions/:id/events — Retrieve paginated events for a session.
 * Replaces the pattern of loading all events inline, keeping GET /:id lightweight.
 *
 * @param {string} id - Session ID (path parameter).
 * @query {number} [limit=100] - Max events to return (1-1000).
 * @query {number} [offset=0] - Offset for pagination.
 * @returns {{ session_id, total, returned, limit, offset, events }}
 */
router.get("/:id/events", requireSessionId, wrapRoute("fetch events", (req, res) => {
  const { id } = req.params;

  const session = fetchSessionOrFail(id, res);
  if (!session) return;

  const { limit, offset } = parsePagination(req.query, { defaultLimit: 100, maxLimit: 1000 });

  const stmts = getSessionStatements();
  const { total } = stmts.countEventsBySession.get(id);
  const events = stmts.eventsBySessionPaged.all(id, limit, offset);
  const parsedEvents = events.map(parseEventRow);

  res.json({
    session_id: id,
    total,
    returned: parsedEvents.length,
    limit,
    offset,
    events: parsedEvents,
  });
}));

// POST /sessions/compare — Compare two sessions side-by-side
/**
 * POST /sessions/compare — Compare two sessions side-by-side.
 * Computes per-field deltas for duration, tokens, cost, and events.
 * Also performs structural comparison of event sequences.
 *
 * @body {string} sessionA - First session ID.
 * @body {string} sessionB - Second session ID.
 * @returns {{ sessionA: Object, sessionB: Object, deltas: Object, eventComparison: Object }}
 * @returns {400} If either session ID is missing or invalid.
 * @returns {404} If either session is not found.
 */
router.post("/compare", wrapRoute("compare sessions", (req, res) => {
  const db = getDb();
  const { session_a, session_b } = req.body;

  if (!session_a || !session_b) {
    return res.status(400).json({ error: "Both session_a and session_b are required" });
  }

  if (!isValidSessionId(session_a) || !isValidSessionId(session_b)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  if (session_a === session_b) {
    return res.status(400).json({ error: "Cannot compare a session with itself" });
  }

  const stmts = getSessionStatements();

    // Cap events per session to prevent OOM — 5000 events max per side
    const DIFF_EVENT_CAP = 5000;
    const { sessA, sessB, eventsA, eventsB, truncatedA, truncatedB } = db.transaction(() => {
      const sessA = stmts.getById.get(session_a);
      const sessB = stmts.getById.get(session_b);
      const eventsA = sessA ? stmts.eventsBySessionCapped.all(session_a, DIFF_EVENT_CAP + 1) : [];
      const eventsB = sessB ? stmts.eventsBySessionCapped.all(session_b, DIFF_EVENT_CAP + 1) : [];
      return {
        sessA, sessB,
        eventsA: eventsA.slice(0, DIFF_EVENT_CAP),
        eventsB: eventsB.slice(0, DIFF_EVENT_CAP),
        truncatedA: eventsA.length > DIFF_EVENT_CAP,
        truncatedB: eventsB.length > DIFF_EVENT_CAP,
      };
    })();

    if (!sessA) return res.status(404).json({ error: `Session ${session_a} not found` });
    if (!sessB) return res.status(404).json({ error: `Session ${session_b} not found` });

    const parsedA = eventsA.map(parseEventRow);
    const parsedB = eventsB.map(parseEventRow);

    const metricsA = computeSessionMetrics(sessA, parsedA);
    const metricsB = computeSessionMetrics(sessB, parsedB);

    // Compute deltas (B relative to A)
    const deltas = computeDeltas(metricsA, metricsB);

    // Collect unique keys across both sessions in a single helper
    // to avoid creating 6 intermediate arrays and 3 Sets.
    function mergeKeys(objA, objB) {
      const set = new Set(Object.keys(objA));
      for (const k of Object.keys(objB)) set.add(k);
      return [...set];
    }
    const allEventTypes = mergeKeys(metricsA.event_types, metricsB.event_types);
    const allTools = mergeKeys(metricsA.tools, metricsB.tools);
    const allModels = mergeKeys(metricsA.models, metricsB.models);

    res.json({
      compared_at: new Date().toISOString(),
      truncated: { session_a: truncatedA, session_b: truncatedB },
      session_a: metricsA,
      session_b: metricsB,
      deltas,
      shared: {
        event_types: allEventTypes,
        tools: allTools,
        models: allModels,
      },
    });
}));

// GET /sessions/:id/events/search — Search and filter events within a session
//
// Performance: SQL-compatible filters (type, model, timestamp range,
// token thresholds, duration, error/tool/reasoning flags) are pushed
// into the WHERE clause so the database does the heavy lifting instead
// of loading every event into JS memory. Only the full-text `q` search
// still runs in-process since it needs parsed JSON field access.
/**
 * GET /sessions/:id/events/search — Search and filter events within a session.
 * Supports filtering by event type, time range, text search across input/output,
 * tool call filtering, and cost/token range filters.
 *
 * @param {string} id - Session ID (path parameter).
 * @query {string} [q] - Free-text search across event input_data and output_data.
 * @query {string} [type] - Filter by event_type (e.g., "llm_call", "tool_use").
 * @query {string} [from] - Start timestamp (ISO 8601).
 * @query {string} [to] - End timestamp (ISO 8601).
 * @query {boolean} [hasToolCall] - Filter events that have/lack tool_call data.
 * @query {boolean} [hasDecisionTrace] - Filter events with/without decision traces.
 * @query {number} [minTokens] - Minimum token count filter.
 * @query {number} [maxTokens] - Maximum token count filter.
 * @query {string} [sort=timestamp] - Sort field (timestamp, duration_ms, tokens, cost_usd).
 * @query {string} [order=asc] - Sort order (asc or desc).
 * @query {number} [limit=100] - Results per page (1-500).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ events: Object[], total: number, filters: Object }}
 * @returns {404} If session not found.
 */
router.get("/:id/events/search", requireSessionId, wrapRoute("search events", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  const session = fetchSessionOrFail(id, res);
  if (!session) return;

    // ── Build dynamic SQL WHERE clause ──────────────────────────────
    const conditions = ["session_id = ?"];
    const params = [id];

    // Filter by event type (comma-separated, pushed to SQL via IN)
    const typeFilter = req.query.type;
    if (typeFilter) {
      const types = typeFilter.split(",").map((t) => t.trim()).filter(Boolean);
      if (types.length > 0) {
        conditions.push(`LOWER(event_type) IN (${types.map(() => "LOWER(?)").join(",")})`);
        params.push(...types);
      }
    }

    // Filter by model (comma-separated, substring match via LIKE)
    const modelFilter = req.query.model;
    if (modelFilter) {
      const models = modelFilter.split(",").map((m) => m.trim()).filter(Boolean);
      if (models.length > 0) {
        const modelClauses = models.map(() => "LOWER(model) LIKE ? ESCAPE '\\'");
        conditions.push(`model IS NOT NULL AND (${modelClauses.join(" OR ")})`);
        params.push(...models.map((m) => `%${escapeLikeWildcards(m).toLowerCase()}%`));
      }
    }

    // Filter by minimum total tokens
    const minTokens = parseInt(req.query.min_tokens);
    if (Number.isFinite(minTokens) && minTokens > 0) {
      conditions.push("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) >= ?");
      params.push(minTokens);
    }

    // Filter by maximum total tokens
    const maxTokens = parseInt(req.query.max_tokens);
    if (Number.isFinite(maxTokens) && maxTokens > 0) {
      conditions.push("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) <= ?");
      params.push(maxTokens);
    }

    // Filter by time range (ISO timestamps)
    const after = req.query.after;
    if (after) {
      const afterDate = new Date(after);
      if (!isNaN(afterDate.getTime())) {
        conditions.push("timestamp >= ?");
        params.push(after);
      }
    }

    const before = req.query.before;
    if (before) {
      const beforeDate = new Date(before);
      if (!isNaN(beforeDate.getTime())) {
        conditions.push("timestamp <= ?");
        params.push(before);
      }
    }

    // Filter by minimum duration
    const minDuration = parseFloat(req.query.min_duration_ms);
    if (Number.isFinite(minDuration) && minDuration > 0) {
      conditions.push("COALESCE(duration_ms, 0) >= ?");
      params.push(minDuration);
    }

    // Filter for error events only
    if (req.query.errors === "true") {
      conditions.push("event_type IN ('error', 'agent_error', 'tool_error')");
    }

    // Filter for events with tool calls only
    if (req.query.has_tools === "true") {
      conditions.push("tool_call IS NOT NULL AND tool_call != 'null'");
    }

    // Filter for events with reasoning only
    if (req.query.has_reasoning === "true") {
      conditions.push("decision_trace IS NOT NULL AND decision_trace != 'null' AND decision_trace LIKE '%\"reasoning\"%'");
    }

    // ── Full-text search — push to SQL via LIKE when possible ─────
    const q = req.query.q;
    if (q) {
      const searchTerms = q.split(/\s+/).filter(Boolean);
      for (const term of searchTerms) {
        const likeTerm = `%${escapeLikeWildcards(term)}%`;
        conditions.push(
          `(LOWER(COALESCE(input_data,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(output_data,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(tool_call,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(event_type,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(model,'')) LIKE LOWER(?) ESCAPE '\\' OR LOWER(COALESCE(decision_trace,'')) LIKE LOWER(?) ESCAPE '\\')`
        );
        params.push(likeTerm, likeTerm, likeTerm, likeTerm, likeTerm, likeTerm);
      }
    }

    // ── Pagination — always apply LIMIT/OFFSET in SQL ───────────────
    const { limit, offset } = parsePagination(req.query, { defaultLimit: 100, maxLimit: 500 });

    // ── Execute SQL query with LIMIT ────────────────────────────────
    const whereClause = conditions.join(" AND ");
    const sqlCount = `SELECT COUNT(*) as total FROM events WHERE ${whereClause}`;
    const sqlData = `SELECT * FROM events WHERE ${whereClause} ORDER BY timestamp ASC LIMIT ? OFFSET ?`;

    const matched = cachedPrepare(sqlCount).get(...params).total;
    const dbResults = cachedPrepare(sqlData).all(...params, limit, offset);

    // Only run the separate total-events count if filters are active;
    // when there are no filters beyond session_id, matched IS the total.
    const hasFilters = conditions.length > 1;
    const totalEvents = hasFilters
      ? getSessionStatements().totalEventsBySession.get(id).total
      : matched;

    // Parse JSON columns
    const parsed = dbResults.map(parseEventRow);

    // ── Compute summary stats in a single pass (avoids 4 iterations) ──
    let totalTokensIn = 0;
    let totalTokensOut = 0;
    let totalDuration = 0;
    const eventTypes = {};
    const models = {};
    for (let i = 0; i < parsed.length; i++) {
      const e = parsed[i];
      totalTokensIn += e.tokens_in || 0;
      totalTokensOut += e.tokens_out || 0;
      totalDuration += e.duration_ms || 0;
      eventTypes[e.event_type] = (eventTypes[e.event_type] || 0) + 1;
      if (e.model) {
        models[e.model] = (models[e.model] || 0) + 1;
      }
    }

    res.json({
      session_id: id,
      total_events: totalEvents,
      matched,
      returned: parsed.length,
      offset,
      limit,
      summary: {
        tokens_in: totalTokensIn,
        tokens_out: totalTokensOut,
        total_tokens: totalTokensIn + totalTokensOut,
        total_duration_ms: Math.round(totalDuration * 100) / 100,
        event_types: eventTypes,
        models,
      },
      events: parsed,
    });
}));

// GET /sessions/:id/explain — Human-readable explanation
/**
 * GET /sessions/:id/explain — Generate a natural language explanation of a session.
 * Analyzes the session's events and produces a human-readable summary
 * of what happened, including key decisions, tool calls, and outcomes.
 *
 * @param {string} id - Session ID (path parameter).
 * @returns {{ explanation: string, session_id: string, event_count: number }}
 * @returns {404} If session not found.
 */
router.get("/:id/explain", requireSessionId, wrapRoute("generate explanation", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  const session = fetchSessionOrFail(id, res);
  if (!session) return;

  // Cap events for explanation to prevent OOM on huge sessions
  const stmts = getSessionStatements();
  const events = stmts.eventsBySessionCapped.all(id, 5000);

  const explanation = generateExplanation(session, events);
  res.json({ session_id: id, explanation });
}));

// Session tag routes are in routes/tags.js (mounted on the same /sessions
// prefix via server.js).  Tag-filtered session listing above uses the
// shared getTagStatements() from lib/tag-statements.js.

module.exports = router;
