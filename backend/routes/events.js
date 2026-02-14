const express = require("express");
const { v4: uuidv4 } = require("uuid");
const { getDb } = require("../db");

const router = express.Router();

// POST /events — Ingest events (batched)
router.post("/", (req, res) => {
  const db = getDb();
  const { events } = req.body;

  if (!events || !Array.isArray(events)) {
    return res.status(400).json({ error: "Missing 'events' array in request body" });
  }

  const insertSession = db.prepare(`
    INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, metadata, status)
    VALUES (?, ?, ?, ?, ?)
  `);

  const updateSession = db.prepare(`
    UPDATE sessions 
    SET total_tokens_in = total_tokens_in + ?,
        total_tokens_out = total_tokens_out + ?
    WHERE session_id = ?
  `);

  const endSession = db.prepare(`
    UPDATE sessions SET ended_at = ?, status = ?, 
      total_tokens_in = CASE WHEN ? > 0 THEN ? ELSE total_tokens_in END,
      total_tokens_out = CASE WHEN ? > 0 THEN ? ELSE total_tokens_out END
    WHERE session_id = ?
  `);

  const insertEvent = db.prepare(`
    INSERT OR IGNORE INTO events (event_id, session_id, event_type, timestamp, input_data, output_data, model, tokens_in, tokens_out, tool_call, decision_trace, duration_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const transaction = db.transaction((eventList) => {
    let processed = 0;
    for (const event of eventList) {
      const sessionId = event.session_id || "unknown";

      // Handle session lifecycle events
      if (event.event_type === "session_start") {
        insertSession.run(
          sessionId,
          event.agent_name || "default-agent",
          event.timestamp || new Date().toISOString(),
          JSON.stringify(event.metadata || {}),
          "active"
        );
        processed++;
        continue;
      }

      if (event.event_type === "session_end") {
        const tokIn = event.total_tokens_in || 0;
        const tokOut = event.total_tokens_out || 0;
        endSession.run(
          event.ended_at || new Date().toISOString(),
          event.status || "completed",
          tokIn, tokIn,
          tokOut, tokOut,
          sessionId
        );
        processed++;
        continue;
      }

      // Regular event
      const eventId = event.event_id || uuidv4().replace(/-/g, "").slice(0, 16);
      
      // Ensure session exists
      insertSession.run(
        sessionId,
        "default-agent",
        event.timestamp || new Date().toISOString(),
        "{}",
        "active"
      );

      insertEvent.run(
        eventId,
        sessionId,
        event.event_type || "generic",
        event.timestamp || new Date().toISOString(),
        event.input_data ? JSON.stringify(event.input_data) : null,
        event.output_data ? JSON.stringify(event.output_data) : null,
        event.model || null,
        event.tokens_in || 0,
        event.tokens_out || 0,
        event.tool_call ? JSON.stringify(event.tool_call) : null,
        event.decision_trace ? JSON.stringify(event.decision_trace) : null,
        event.duration_ms || null
      );

      // Update session token counts
      updateSession.run(event.tokens_in || 0, event.tokens_out || 0, sessionId);

      processed++;
    }
    return processed;
  });

  try {
    const processed = transaction(events);
    res.json({ status: "ok", processed });
  } catch (err) {
    console.error("Error ingesting events:", err);
    res.status(500).json({ error: "Failed to ingest events", details: err.message });
  }
});

module.exports = router;
