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
