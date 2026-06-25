const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, isValidStatus, safeJsonParse, validateTag, escapeLikeWildcards } = require("../lib/validation");
const { generateExplanation } = require("../lib/explain");
const { computeSessionMetrics, computeDeltas } = require("../lib/session-metrics");
const { getTagStatements } = require("../lib/tag-statements");
const { parsePagination, requireSessionId, wrapRoute } = require("../lib/request-helpers");
const { toExportEvent, eventToCsvRow, buildJsonExport, ndjsonSessionLine, CSV_HEADERS } = require("../lib/csv-export");
const { buildPdfExport } = require("../lib/pdf-export");
const { createLazyStatements } = require("../lib/lazy-statements");
const { createStatementCache } = require("../lib/statement-cache");

const router = express.Router();

// ── LRU prepared-statement cache for dynamic SQL queries ────────────
// Session/event search endpoints build SQL dynamically based on filter
// combinations.  This cache avoids re-compiling the same SQL string on
// every request (~0.1-0.5 ms savings per repeated query).
const cachedPrepare = createStatementCache(getDb);

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
 * Mutates the row in-place to avoid the overhead of object spread ({...e})
 * which copies all 12+ columns on every call.  Since rows come from
 * better-sqlite3 and are not reused, in-place mutation is safe and
 * eliminates ~40% of GC pressure on bulk event endpoints (export,
 * search, compare) that process thousands of rows per request.
 *
 * @param {Object} e - Raw event row from the events table (mutated in-place).
 * @returns {Object} Same row reference with JSON text columns parsed.
 */
function parseEventRow(e) {
  e.input_data = safeJsonParse(e.input_data);
  e.output_data = safeJsonParse(e.output_data);
  e.tool_call = safeJsonParse(e.tool_call, null);
  e.decision_trace = safeJsonParse(e.decision_trace, null);
  return e;
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
// Uses the shared createLazyStatements factory (same pattern as every
// other route file) instead of a hand-rolled _sessionStmts singleton.
const getSessionStatements = createLazyStatements((db) => ({
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
}));

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
 * @query {string} [q] - Free-text search across agent_name and metadata (space-separated terms, up to 10, ANDed).
 * @query {string} [agent] - Filter by agent name (substring match).
 * @query {string} [status] - Filter by status (active, completed, error, timeout); invalid values are ignored.
 * @query {string} [after] - Start date (ISO 8601); matches sessions with started_at >= this.
 * @query {string} [before] - End date (ISO 8601); matches sessions with started_at <= this.
 * @query {number} [min_tokens] - Minimum total tokens (tokens_in + tokens_out); applied when > 0.
 * @query {number} [max_tokens] - Maximum total tokens (tokens_in + tokens_out); applied when > 0.
 * @query {string} [tags] - Comma-separated tags (up to 20); sessions must have ALL listed tags.
 * @query {string} [sort=started_at] - Sort field: started_at, total_tokens, agent_name, or status (others ignored).
 * @query {string} [order=desc] - Sort order (asc or desc).
 * @query {number} [limit=50] - Results per page (1-200).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ sessions: Object[], total: number, limit: number, offset: number, sort: string, order: string, filters: Object }}
 *   Filtered results (each session enriched with parsed metadata and its tags) plus the applied-filter summary.
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
      // Cap at 10 terms to prevent parameter overflow and excessive
      // query complexity (each term adds 2 LIKE parameters).
      const terms = q.trim().split(/\s+/).filter(Boolean).slice(0, 10);
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
    // Cap at 20 tags to prevent SQLite variable-count overflow (limit ~999)
    // and excessive query complexity from unbounded user input.
    const tagFilter = req.query.tags;
    let tagJoin = "";
    if (tagFilter) {
      const tags = tagFilter.split(",").map(t => t.trim()).filter(Boolean).slice(0, 20);
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

    // Batch-fetch tags for returned sessions.
    // Uses db.prepare() directly instead of cachedPrepare() because each
    // unique result-set size produces a different placeholder count,
    // polluting the LRU statement cache with one-off queries and evicting
    // frequently-used statements.  Fixed-size chunking bounds the number
    // of distinct prepared statements to ceil(maxResults / CHUNK_SIZE).
    const sessionIds = sessions.map(s => s.session_id);
    const tagMap = {};
    if (sessionIds.length > 0) {
      const db = getDb();
      const TAG_CHUNK = 50;
      // Cache prepared statements per chunk size to avoid re-compiling
      // db.prepare() on every request. Most calls use the full chunk size,
      // with at most one smaller trailing chunk.
      const tagStmtCache = Object.create(null);
      for (let i = 0; i < sessionIds.length; i += TAG_CHUNK) {
        const chunk = sessionIds.slice(i, i + TAG_CHUNK);
        const len = chunk.length;
        if (!tagStmtCache[len]) {
          const placeholders = chunk.map(() => "?").join(",");
          tagStmtCache[len] = db.prepare(
            `SELECT session_id, tag FROM session_tags WHERE session_id IN (${placeholders}) ORDER BY created_at ASC`
          );
        }
        const tagRows = tagStmtCache[len].all(...chunk);
        for (const row of tagRows) {
          if (!tagMap[row.session_id]) tagMap[row.session_id] = [];
          tagMap[row.session_id].push(row.tag);
        }
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

  if (format !== "json" && format !== "csv" && format !== "ndjson" && format !== "pdf") {
    return res.status(400).json({ error: "Invalid format. Use 'json', 'csv', 'ndjson', or 'pdf'." });
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

  // JSON and PDF formats need all events in memory for transformation
  const events = stmts.eventsBySession.all(id);
  const parsedEvents = events.map(e => toExportEvent(e, parseEventRow));

  if (format === "pdf") {
    const pdfBuffer = buildPdfExport(session, parsedEvents);
    const filename = `agentlens-${sanitizeFilename(session.agent_name)}-${id.slice(0, 8)}.pdf`;
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "application/pdf");
    res.setHeader("Content-Length", pdfBuffer.length);
    return res.end(pdfBuffer);
  }

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
 * Supports filtering by event type, model, time range, full-text search across input/output,
 * tool-call / reasoning presence, and token/duration range filters.
 *
 * Results are always ordered by timestamp ascending.
 *
 * @param {string} id - Session ID (path parameter).
 * @query {string} [q] - Free-text search (space-separated terms, up to 10) across input_data,
 *   output_data, tool_call, event_type, model, and decision_trace.
 * @query {string} [type] - Comma-separated event types (up to 20), matched case-insensitively.
 * @query {string} [model] - Comma-separated model substrings (up to 20), matched case-insensitively.
 * @query {string} [after] - Start timestamp (ISO 8601); matches events with timestamp >= this.
 * @query {string} [before] - End timestamp (ISO 8601); matches events with timestamp <= this.
 * @query {number} [min_tokens] - Minimum total tokens (tokens_in + tokens_out); applied when > 0.
 * @query {number} [max_tokens] - Maximum total tokens (tokens_in + tokens_out); applied when > 0.
 * @query {number} [min_duration_ms] - Minimum event duration in ms; applied when > 0.
 * @query {boolean} [errors] - When "true", only error/agent_error/tool_error events.
 * @query {boolean} [has_tools] - When "true", only events that carry tool_call data.
 * @query {boolean} [has_reasoning] - When "true", only events whose decision_trace has reasoning.
 * @query {number} [limit=100] - Results per page (1-500).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ session_id: string, total_events: number, matched: number, returned: number,
 *   offset: number, limit: number, summary: Object, events: Object[] }}
 *   `matched` is the count after filters; `total_events` is the session's unfiltered event count.
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
    // Cap at 20 values to prevent SQLite parameter overflow from
    // unbounded user input (SQLite limit is ~999 variables per query).
    const typeFilter = req.query.type;
    if (typeFilter) {
      const types = typeFilter.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 20);
      if (types.length > 0) {
        conditions.push(`LOWER(event_type) IN (${types.map(() => "LOWER(?)").join(",")})`);
        params.push(...types);
      }
    }

    // Filter by model (comma-separated, substring match via LIKE)
    // Cap at 20 values to prevent SQLite parameter overflow.
    const modelFilter = req.query.model;
    if (modelFilter) {
      const models = modelFilter.split(",").map((m) => m.trim()).filter(Boolean).slice(0, 20);
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
      // Cap at 10 search terms — each term adds 6 LIKE clauses with
      // bound parameters.  Unbounded input could exceed SQLite's ~999
      // variable limit or cause excessive query complexity.
      const searchTerms = q.split(/\s+/).filter(Boolean).slice(0, 10);
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
