const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, isValidStatus, safeJsonParse, validateTag, validateTags, MAX_TAGS_PER_SESSION } = require("../lib/validation");
const { generateExplanation } = require("../lib/explain");
const { computeSessionMetrics, pctDelta } = require("../lib/session-metrics");

const router = express.Router();

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
router.get("/", (req, res) => {
  const db = getDb();
  const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 50), 200);
  const offset = Math.max(0, parseInt(req.query.offset) || 0);
  const status = req.query.status;

  if (status && !isValidStatus(status)) {
    return res.status(400).json({ error: "Invalid status filter" });
  }

  try {
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
  } catch (err) {
    console.error("Error listing sessions:", err);
    res.status(500).json({ error: "Failed to list sessions" });
  }
});

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
router.get("/search", (req, res) => {
  const db = getDb();

  try {
    const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 50), 200);
    const offset = Math.max(0, parseInt(req.query.offset) || 0);
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
        conditions.push("(s.agent_name LIKE ? OR s.metadata LIKE ?)");
        params.push(`%${term}%`, `%${term}%`);
      }
    }

    // Agent name filter (exact or substring)
    const agent = req.query.agent;
    if (agent) {
      conditions.push("s.agent_name LIKE ?");
      params.push(`%${agent}%`);
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
    const total = db.prepare(countSql).get(...params).count;

    // Data query
    const dataSql = `SELECT s.* FROM sessions s ${tagJoin} ${whereClause} ORDER BY ${sortColumn} ${sortOrder} LIMIT ? OFFSET ?`;
    const sessions = db.prepare(dataSql).all(...params, limit, offset);

    // Batch-fetch tags for returned sessions
    const sessionIds = sessions.map(s => s.session_id);
    const tagMap = {};
    if (sessionIds.length > 0) {
      const placeholders = sessionIds.map(() => "?").join(",");
      const tagRows = db.prepare(
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
  } catch (err) {
    console.error("Error searching sessions:", err);
    res.status(500).json({ error: "Failed to search sessions" });
  }
});

// GET /sessions/tags — List all tags with session counts
// (Must be before /:id to avoid matching "tags" as a session ID)
/**
 * GET /sessions/tags — List all distinct tags used across sessions.
 *
 * @returns {{ tags: string[] }} Array of unique tag strings, sorted alphabetically.
 */
router.get("/tags", (req, res) => {
  try {
    const stmts = getTagStatements();
    const tags = stmts.allTags.all();
    res.json({ tags });
  } catch (err) {
    console.error("Error listing tags:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// GET /sessions/by-tag/:tag — List sessions with a specific tag
// (Must be before /:id to avoid matching "by-tag" as a session ID)
/**
 * GET /sessions/by-tag/:tag — List sessions that have a specific tag.
 *
 * @param {string} tag - The tag to filter by (URL-encoded path parameter).
 * @query {number} [limit=50] - Results per page (1-200).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ sessions: Object[], total: number, tag: string }} Sessions matching the tag.
 */
router.get("/by-tag/:tag", (req, res) => {
  try {
    const tag = validateTag(req.params.tag);
    if (!tag) {
      return res.status(400).json({ error: "Invalid tag" });
    }

    const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 50), 200);
    const offset = Math.max(0, parseInt(req.query.offset) || 0);

    const stmts = getTagStatements();
    const sessions = stmts.sessionsByTag.all(tag, limit, offset);
    const { count: total } = stmts.sessionsByTagCount.get(tag);

    // Batch-fetch all tags for the returned sessions in one query
    // instead of N separate getTagsForSession calls (N+1 → 2 queries)
    const sessionIds = sessions.map((s) => s.session_id);
    const tagMap = {};
    if (sessionIds.length > 0) {
      const placeholders = sessionIds.map(() => "?").join(", ");
      const batchStmt = getDb().prepare(
        `SELECT session_id, tag FROM session_tags
         WHERE session_id IN (${placeholders})
         ORDER BY created_at ASC`
      );
      const allTags = batchStmt.all(...sessionIds);
      for (const row of allTags) {
        if (!tagMap[row.session_id]) tagMap[row.session_id] = [];
        tagMap[row.session_id].push(row.tag);
      }
    }

    const enriched = sessions.map((s) => ({
      ...s,
      metadata: safeJsonParse(s.metadata),
      tags: tagMap[s.session_id] || [],
    }));

    res.json({ sessions: enriched, total, limit, offset, tag });
  } catch (err) {
    console.error("Error listing sessions by tag:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

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
router.get("/:id", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  if (!isValidSessionId(id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  try {
    const stmts = getSessionStatements();
    const session = stmts.getById.get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.eventsBySession.all(id);

    const parsedEvents = events.map(parseEventRow);

    res.json({
      ...session,
      metadata: safeJsonParse(session.metadata),
      events: parsedEvents,
    });
  } catch (err) {
    console.error("Error fetching session:", err);
    res.status(500).json({ error: "Failed to fetch session" });
  }
});

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
router.get("/:id/export", (req, res) => {
  const db = getDb();
  const { id } = req.params;
  const format = (req.query.format || "json").toLowerCase();

  if (!isValidSessionId(id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  if (format !== "json" && format !== "csv") {
    return res.status(400).json({ error: "Invalid format. Use 'json' or 'csv'." });
  }

  try {
    const stmts = getSessionStatements();
    const session = stmts.getById.get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.eventsBySession.all(id);

    const parsedEvents = events.map((e) => ({
      event_id: e.event_id,
      event_type: e.event_type,
      timestamp: e.timestamp,
      model: e.model || "",
      tokens_in: e.tokens_in || 0,
      tokens_out: e.tokens_out || 0,
      duration_ms: e.duration_ms || 0,
      input_data: safeJsonParse(e.input_data),
      output_data: safeJsonParse(e.output_data),
      tool_call: safeJsonParse(e.tool_call, null),
      decision_trace: safeJsonParse(e.decision_trace, null),
    }));

    if (format === "json") {
      const exportData = {
        exported_at: new Date().toISOString(),
        session: {
          session_id: session.session_id,
          agent_name: session.agent_name,
          status: session.status,
          started_at: session.started_at,
          ended_at: session.ended_at,
          total_tokens_in: session.total_tokens_in,
          total_tokens_out: session.total_tokens_out,
          metadata: safeJsonParse(session.metadata),
        },
        events: parsedEvents,
        summary: {
          total_events: parsedEvents.length,
          total_tokens: session.total_tokens_in + session.total_tokens_out,
          models_used: [...new Set(parsedEvents.filter(e => e.model).map(e => e.model))],
          event_types: [...new Set(parsedEvents.map(e => e.event_type))],
          total_duration_ms: parsedEvents.reduce((sum, e) => sum + (e.duration_ms || 0), 0),
        },
      };

      const filename = `agentlens-${session.agent_name}-${id.slice(0, 8)}.json`;
      res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
      res.setHeader("Content-Type", "application/json");
      return res.json(exportData);
    }

    // CSV format
    const csvHeaders = [
      "event_id", "event_type", "timestamp", "model",
      "tokens_in", "tokens_out", "duration_ms",
      "input_data", "output_data", "tool_name", "tool_input",
      "tool_output", "reasoning",
    ];

    const csvEscape = (val) => {
      if (val == null) return "";
      const str = typeof val === "object" ? JSON.stringify(val) : String(val);
      if (str.includes(",") || str.includes('"') || str.includes("\n")) {
        return `"${str.replace(/"/g, '""')}"`;
      }
      return str;
    };

    const csvRows = [csvHeaders.join(",")];
    for (const e of parsedEvents) {
      csvRows.push([
        csvEscape(e.event_id),
        csvEscape(e.event_type),
        csvEscape(e.timestamp),
        csvEscape(e.model),
        csvEscape(e.tokens_in),
        csvEscape(e.tokens_out),
        csvEscape(e.duration_ms),
        csvEscape(e.input_data),
        csvEscape(e.output_data),
        csvEscape(e.tool_call?.tool_name),
        csvEscape(e.tool_call?.tool_input),
        csvEscape(e.tool_call?.tool_output),
        csvEscape(e.decision_trace?.reasoning),
      ].join(","));
    }

    const filename = `agentlens-${session.agent_name}-${id.slice(0, 8)}.csv`;
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "text/csv");
    return res.send(csvRows.join("\n"));
  } catch (err) {
    console.error("Error exporting session:", err);
    res.status(500).json({ error: "Failed to export session" });
  }
});

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
router.post("/compare", (req, res) => {
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

  try {
    const stmts = getSessionStatements();

    // Run both lookups + event fetches in a single transaction for
    // consistency and reduced WAL lock churn.
    const { sessA, sessB, eventsA, eventsB } = db.transaction(() => {
      const sessA = stmts.getById.get(session_a);
      const sessB = stmts.getById.get(session_b);
      const eventsA = sessA ? stmts.eventsBySession.all(session_a) : [];
      const eventsB = sessB ? stmts.eventsBySession.all(session_b) : [];
      return { sessA, sessB, eventsA, eventsB };
    })();

    if (!sessA) return res.status(404).json({ error: `Session ${session_a} not found` });
    if (!sessB) return res.status(404).json({ error: `Session ${session_b} not found` });

    const parsedA = eventsA.map(parseEventRow);
    const parsedB = eventsB.map(parseEventRow);

    const metricsA = computeSessionMetrics(sessA, parsedA);
    const metricsB = computeSessionMetrics(sessB, parsedB);

    // Compute deltas (B relative to A)
    const deltas = {
      total_tokens: { absolute: metricsB.total_tokens - metricsA.total_tokens, percent: pctDelta(metricsA.total_tokens, metricsB.total_tokens) },
      tokens_in: { absolute: metricsB.tokens_in - metricsA.tokens_in, percent: pctDelta(metricsA.tokens_in, metricsB.tokens_in) },
      tokens_out: { absolute: metricsB.tokens_out - metricsA.tokens_out, percent: pctDelta(metricsA.tokens_out, metricsB.tokens_out) },
      event_count: { absolute: metricsB.event_count - metricsA.event_count, percent: pctDelta(metricsA.event_count, metricsB.event_count) },
      error_count: { absolute: metricsB.error_count - metricsA.error_count, percent: pctDelta(metricsA.error_count, metricsB.error_count) },
      total_processing_ms: { absolute: Math.round((metricsB.total_processing_ms - metricsA.total_processing_ms) * 100) / 100, percent: pctDelta(metricsA.total_processing_ms, metricsB.total_processing_ms) },
      avg_event_duration_ms: { absolute: Math.round((metricsB.avg_event_duration_ms - metricsA.avg_event_duration_ms) * 100) / 100, percent: pctDelta(metricsA.avg_event_duration_ms, metricsB.avg_event_duration_ms) },
    };

    // All unique event types across both
    const allEventTypes = [...new Set([
      ...Object.keys(metricsA.event_types),
      ...Object.keys(metricsB.event_types),
    ])];

    // All unique tools across both
    const allTools = [...new Set([
      ...Object.keys(metricsA.tools),
      ...Object.keys(metricsB.tools),
    ])];

    // All unique models across both
    const allModels = [...new Set([
      ...Object.keys(metricsA.models),
      ...Object.keys(metricsB.models),
    ])];

    res.json({
      compared_at: new Date().toISOString(),
      session_a: metricsA,
      session_b: metricsB,
      deltas,
      shared: {
        event_types: allEventTypes,
        tools: allTools,
        models: allModels,
      },
    });
  } catch (err) {
    console.error("Error comparing sessions:", err);
    res.status(500).json({ error: "Failed to compare sessions" });
  }
});

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
router.get("/:id/events/search", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  if (!isValidSessionId(id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  try {
    const stmts = getSessionStatements();
    const session = stmts.getById.get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

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
        const modelClauses = models.map(() => "LOWER(model) LIKE ?");
        conditions.push(`model IS NOT NULL AND (${modelClauses.join(" OR ")})`);
        params.push(...models.map((m) => `%${m.toLowerCase()}%`));
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

    // ── Execute SQL query ───────────────────────────────────────────
    const whereClause = conditions.join(" AND ");
    const sqlData = `SELECT * FROM events WHERE ${whereClause} ORDER BY timestamp ASC`;
    const sqlCount = `SELECT COUNT(*) as total FROM events WHERE session_id = ?`;

    const totalEvents = db.prepare(sqlCount).get(id).total;
    const dbResults = db.prepare(sqlData).all(...params);

    // Parse JSON columns
    const parsed = dbResults.map(parseEventRow);

    // ── Full-text search (must run in-process on parsed JSON) ───────
    let filtered = parsed;
    const q = req.query.q;
    if (q) {
      const searchTerms = q.toLowerCase().split(/\s+/).filter(Boolean);
      filtered = filtered.filter((e) => {
        const searchable = [
          JSON.stringify(e.input_data || ""),
          JSON.stringify(e.output_data || ""),
          e.tool_call ? JSON.stringify(e.tool_call) : "",
          e.decision_trace?.reasoning || "",
          e.event_type || "",
          e.model || "",
        ]
          .join(" ")
          .toLowerCase();
        return searchTerms.every((term) => searchable.includes(term));
      });
    }

    // ── Compute summary stats for filtered results ──────────────────
    const totalTokensIn = filtered.reduce((s, e) => s + (e.tokens_in || 0), 0);
    const totalTokensOut = filtered.reduce((s, e) => s + (e.tokens_out || 0), 0);
    const totalDuration = filtered.reduce((s, e) => s + (e.duration_ms || 0), 0);
    const eventTypes = {};
    const models = {};
    filtered.forEach((e) => {
      eventTypes[e.event_type] = (eventTypes[e.event_type] || 0) + 1;
      if (e.model) {
        models[e.model] = (models[e.model] || 0) + 1;
      }
    });

    // ── Pagination ──────────────────────────────────────────────────
    const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 100), 500);
    const offset = Math.max(0, parseInt(req.query.offset) || 0);
    const paginated = filtered.slice(offset, offset + limit);

    res.json({
      session_id: id,
      total_events: totalEvents,
      matched: filtered.length,
      returned: paginated.length,
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
      events: paginated,
    });
  } catch (err) {
    console.error("Error searching events:", err);
    res.status(500).json({ error: "Failed to search events" });
  }
});

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
router.get("/:id/explain", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  if (!isValidSessionId(id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  try {
    const stmts = getSessionStatements();
    const session = stmts.getById.get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.eventsBySession.all(id);

    const explanation = generateExplanation(session, events);
    res.json({ session_id: id, explanation });
  } catch (err) {
    console.error("Error generating explanation:", err);
    res.status(500).json({ error: "Failed to generate explanation" });
  }
});

// ── Session Tags ────────────────────────────────────────────────────

// Lazily initialized tag statements
let _tagStmts = null;

/**
 * Get lazily-initialized prepared SQL statements for session tag operations.
 * Statements are compiled once and reused across all requests.
 *
 * @returns {{ allTags: Statement, tagsBySession: Statement, addTag: Statement,
 *             removeTag: Statement, removeAllTags: Statement, countTags: Statement,
 *             sessionsByTag: Statement, sessionsByTagCount: Statement }}
 */
function getTagStatements() {
  if (_tagStmts) return _tagStmts;
  const db = getDb();

  _tagStmts = {
    getTagsForSession: db.prepare(
      "SELECT tag, created_at FROM session_tags WHERE session_id = ? ORDER BY created_at ASC"
    ),
    addTag: db.prepare(
      "INSERT OR IGNORE INTO session_tags (session_id, tag, created_at) VALUES (?, ?, ?)"
    ),
    removeTag: db.prepare(
      "DELETE FROM session_tags WHERE session_id = ? AND tag = ?"
    ),
    removeAllTags: db.prepare(
      "DELETE FROM session_tags WHERE session_id = ?"
    ),
    countTags: db.prepare(
      "SELECT COUNT(*) as count FROM session_tags WHERE session_id = ?"
    ),
    sessionsByTag: db.prepare(
      `SELECT DISTINCT s.* FROM sessions s
       INNER JOIN session_tags st ON s.session_id = st.session_id
       WHERE st.tag = ?
       ORDER BY s.started_at DESC
       LIMIT ? OFFSET ?`
    ),
    sessionsByTagCount: db.prepare(
      `SELECT COUNT(DISTINCT s.session_id) as count FROM sessions s
       INNER JOIN session_tags st ON s.session_id = st.session_id
       WHERE st.tag = ?`
    ),
    allTags: db.prepare(
      `SELECT tag, COUNT(*) as session_count FROM session_tags
       GROUP BY tag ORDER BY session_count DESC, tag ASC`
    ),
  };

  return _tagStmts;
}

// GET /sessions/:id/tags — Get tags for a session
/**
 * GET /sessions/:id/tags — List all tags for a session.
 *
 * @param {string} id - Session ID (path parameter).
 * @returns {{ tags: string[], session_id: string }}
 * @returns {404} If session not found.
 */
router.get("/:id/tags", (req, res) => {
  try {
    if (!isValidSessionId(req.params.id)) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const stmts = getTagStatements();
    const tags = stmts.getTagsForSession.all(req.params.id);
    res.json({ session_id: req.params.id, tags: tags.map((t) => t.tag) });
  } catch (err) {
    console.error("Error getting tags:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// POST /sessions/:id/tags — Add tags to a session
/**
 * POST /sessions/:id/tags — Add tags to a session.
 * Validates tag format and enforces per-session tag limit.
 *
 * @param {string} id - Session ID (path parameter).
 * @body {string[]} tags - Array of tag strings to add (max MAX_TAGS_PER_SESSION total).
 * @returns {{ tags: string[], added: number, session_id: string }}
 * @returns {400} If tags are invalid or limit exceeded.
 * @returns {404} If session not found.
 */
router.post("/:id/tags", (req, res) => {
  try {
    if (!isValidSessionId(req.params.id)) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const { tags } = req.body || {};
    const validTags = validateTags(tags);
    if (!validTags) {
      return res.status(400).json({
        error: "Invalid tags. Provide an array of strings (alphanumeric, _-.:/ , max 64 chars each).",
      });
    }

    // Check session exists
    const db = getDb();
    const session = db.prepare("SELECT session_id FROM sessions WHERE session_id = ?").get(req.params.id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    // Check tag limit
    const stmts = getTagStatements();
    const { count: existing } = stmts.countTags.get(req.params.id);
    if (existing + validTags.length > MAX_TAGS_PER_SESSION) {
      return res.status(400).json({
        error: `Tag limit exceeded. Session has ${existing} tags, adding ${validTags.length} would exceed max of ${MAX_TAGS_PER_SESSION}.`,
      });
    }

    const now = new Date().toISOString();
    const addMany = db.transaction(() => {
      let added = 0;
      for (const tag of validTags) {
        const result = stmts.addTag.run(req.params.id, tag, now);
        if (result.changes > 0) added++;
      }
      return added;
    });

    const added = addMany();
    const allTags = stmts.getTagsForSession.all(req.params.id).map((t) => t.tag);

    res.json({
      session_id: req.params.id,
      added,
      tags: allTags,
    });
  } catch (err) {
    console.error("Error adding tags:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

// DELETE /sessions/:id/tags — Remove tags from a session
/**
 * DELETE /sessions/:id/tags — Remove tags from a session.
 * If no tags specified in body, removes all tags.
 *
 * @param {string} id - Session ID (path parameter).
 * @body {string[]} [tags] - Specific tags to remove. If omitted, removes all tags.
 * @returns {{ tags: string[], removed: number, session_id: string }}
 * @returns {404} If session not found.
 */
router.delete("/:id/tags", (req, res) => {
  try {
    if (!isValidSessionId(req.params.id)) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const { tags } = req.body || {};

    const stmts = getTagStatements();

    // If no tags specified, remove all
    if (!tags || (Array.isArray(tags) && tags.length === 0)) {
      const result = stmts.removeAllTags.run(req.params.id);
      return res.json({
        session_id: req.params.id,
        removed: result.changes,
        tags: [],
      });
    }

    const validTags = validateTags(tags);
    if (!validTags) {
      return res.status(400).json({ error: "Invalid tags array" });
    }

    const db = getDb();
    const removeMany = db.transaction(() => {
      let removed = 0;
      for (const tag of validTags) {
        const result = stmts.removeTag.run(req.params.id, tag);
        removed += result.changes;
      }
      return removed;
    });

    const removed = removeMany();
    const remaining = stmts.getTagsForSession.all(req.params.id).map((t) => t.tag);

    res.json({
      session_id: req.params.id,
      removed,
      tags: remaining,
    });
  } catch (err) {
    console.error("Error removing tags:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

module.exports = router;
