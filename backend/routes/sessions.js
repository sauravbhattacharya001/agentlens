const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, isValidStatus, safeJsonParse } = require("../lib/validation");
const { generateExplanation } = require("../lib/explain");

const router = express.Router();

// ── Cached prepared statements ──────────────────────────────────────
// Lazily initialized once, reused across all requests to avoid
// re-compiling SQL on every call.
let _sessionStmts = null;

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

// GET /sessions — List all sessions
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
    const sessions = status
      ? stmts.listByStatus.all(status, limit, offset)
      : stmts.listAll.all(limit, offset);
    const total = status
      ? stmts.countByStatus.get(status)
      : stmts.countAll.get();

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

// GET /sessions/:id — Session detail with full event trace
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

    const parsedEvents = events.map((e) => ({
      ...e,
      input_data: safeJsonParse(e.input_data),
      output_data: safeJsonParse(e.output_data),
      tool_call: safeJsonParse(e.tool_call),
      decision_trace: safeJsonParse(e.decision_trace),
    }));

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

    const parseEvents = (events) => events.map((e) => ({
      ...e,
      input_data: safeJsonParse(e.input_data),
      output_data: safeJsonParse(e.output_data),
      tool_call: safeJsonParse(e.tool_call, null),
      decision_trace: safeJsonParse(e.decision_trace, null),
    }));

    const parsedA = parseEvents(eventsA);
    const parsedB = parseEvents(eventsB);

    // Compute metrics for a session + events
    const computeMetrics = (session, events) => {
      const totalTokensIn = session.total_tokens_in || 0;
      const totalTokensOut = session.total_tokens_out || 0;
      const totalTokens = totalTokensIn + totalTokensOut;
      const eventCount = events.length;
      const totalDuration = events.reduce((sum, e) => sum + (e.duration_ms || 0), 0);
      const avgDuration = eventCount > 0 ? totalDuration / eventCount : 0;

      // Models used
      const models = {};
      events.forEach((e) => {
        if (e.model) {
          if (!models[e.model]) models[e.model] = { calls: 0, tokens_in: 0, tokens_out: 0 };
          models[e.model].calls++;
          models[e.model].tokens_in += e.tokens_in || 0;
          models[e.model].tokens_out += e.tokens_out || 0;
        }
      });

      // Event type breakdown
      const eventTypes = {};
      events.forEach((e) => {
        eventTypes[e.event_type] = (eventTypes[e.event_type] || 0) + 1;
      });

      // Tool usage
      const tools = {};
      events.forEach((e) => {
        if (e.tool_call && e.tool_call.tool_name) {
          const name = e.tool_call.tool_name;
          if (!tools[name]) tools[name] = { calls: 0, total_duration: 0 };
          tools[name].calls++;
          tools[name].total_duration += e.duration_ms || 0;
        }
      });

      // Session duration (wall clock)
      let sessionDurationMs = null;
      if (session.started_at && session.ended_at) {
        sessionDurationMs = new Date(session.ended_at) - new Date(session.started_at);
      }

      // Error count
      const errorCount = events.filter((e) =>
        e.event_type === "error" || e.event_type === "agent_error" || e.event_type === "tool_error"
      ).length;

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
    };

    const metricsA = computeMetrics(sessA, parsedA);
    const metricsB = computeMetrics(sessB, parsedB);

    // Compute deltas (B relative to A)
    const pctDelta = (a, b) => {
      if (a === 0 && b === 0) return 0;
      if (a === 0) return b > 0 ? 100 : -100;
      return Math.round(((b - a) / a) * 10000) / 100;
    };

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

    // Get all events for the session
    const allEvents = stmts.eventsBySession.all(id).map((e) => ({
      ...e,
      input_data: safeJsonParse(e.input_data),
      output_data: safeJsonParse(e.output_data),
      tool_call: safeJsonParse(e.tool_call, null),
      decision_trace: safeJsonParse(e.decision_trace, null),
    }));

    // ── Apply filters ────────────────────────────────────────────────
    let filtered = allEvents;

    // Filter by event type (comma-separated list)
    const typeFilter = req.query.type;
    if (typeFilter) {
      const types = typeFilter.split(",").map((t) => t.trim().toLowerCase());
      filtered = filtered.filter((e) =>
        types.includes(e.event_type.toLowerCase())
      );
    }

    // Filter by model (comma-separated list, case-insensitive substring match)
    const modelFilter = req.query.model;
    if (modelFilter) {
      const models = modelFilter.split(",").map((m) => m.trim().toLowerCase());
      filtered = filtered.filter((e) =>
        e.model && models.some((m) => e.model.toLowerCase().includes(m))
      );
    }

    // Full-text search across input_data, output_data, tool_call, reasoning
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

    // Filter by minimum total tokens
    const minTokens = parseInt(req.query.min_tokens);
    if (Number.isFinite(minTokens) && minTokens > 0) {
      filtered = filtered.filter(
        (e) => (e.tokens_in || 0) + (e.tokens_out || 0) >= minTokens
      );
    }

    // Filter by maximum total tokens
    const maxTokens = parseInt(req.query.max_tokens);
    if (Number.isFinite(maxTokens) && maxTokens > 0) {
      filtered = filtered.filter(
        (e) => (e.tokens_in || 0) + (e.tokens_out || 0) <= maxTokens
      );
    }

    // Filter by time range (ISO timestamps)
    const after = req.query.after;
    if (after) {
      const afterDate = new Date(after);
      if (!isNaN(afterDate.getTime())) {
        filtered = filtered.filter(
          (e) => new Date(e.timestamp) >= afterDate
        );
      }
    }

    const before = req.query.before;
    if (before) {
      const beforeDate = new Date(before);
      if (!isNaN(beforeDate.getTime())) {
        filtered = filtered.filter(
          (e) => new Date(e.timestamp) <= beforeDate
        );
      }
    }

    // Filter by minimum duration
    const minDuration = parseFloat(req.query.min_duration_ms);
    if (Number.isFinite(minDuration) && minDuration > 0) {
      filtered = filtered.filter(
        (e) => (e.duration_ms || 0) >= minDuration
      );
    }

    // Filter for events with errors only
    if (req.query.errors === "true") {
      filtered = filtered.filter(
        (e) =>
          e.event_type === "error" ||
          e.event_type === "agent_error" ||
          e.event_type === "tool_error"
      );
    }

    // Filter for events with tool calls only
    if (req.query.has_tools === "true") {
      filtered = filtered.filter((e) => e.tool_call != null);
    }

    // Filter for events with reasoning only
    if (req.query.has_reasoning === "true") {
      filtered = filtered.filter(
        (e) => e.decision_trace?.reasoning
      );
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
      total_events: allEvents.length,
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

module.exports = router;
