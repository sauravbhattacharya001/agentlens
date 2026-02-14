const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, isValidStatus, safeJsonParse } = require("../lib/validation");
const { generateExplanation } = require("../lib/explain");

const router = express.Router();

// GET /sessions — List all sessions
router.get("/", (req, res) => {
  const db = getDb();
  const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 50), 200);
  const offset = Math.max(0, parseInt(req.query.offset) || 0);
  const status = req.query.status;

  let query = "SELECT * FROM sessions";
  const params = [];

  if (status) {
    if (!isValidStatus(status)) {
      return res.status(400).json({ error: "Invalid status filter" });
    }
    query += " WHERE status = ?";
    params.push(status);
  }

  query += " ORDER BY started_at DESC LIMIT ? OFFSET ?";
  params.push(limit, offset);

  try {
    const sessions = db.prepare(query).all(...params);
    const total = db
      .prepare(`SELECT COUNT(*) as count FROM sessions${status ? " WHERE status = ?" : ""}`)
      .get(...(status ? [status] : []));

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
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(id);

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
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(id);

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

// GET /sessions/:id/explain — Human-readable explanation
router.get("/:id/explain", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  if (!isValidSessionId(id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  try {
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(id);

    const explanation = generateExplanation(session, events);
    res.json({ session_id: id, explanation });
  } catch (err) {
    console.error("Error generating explanation:", err);
    res.status(500).json({ error: "Failed to generate explanation" });
  }
});

module.exports = router;
