const express = require("express");
const { v4: uuidv4 } = require("uuid");
const { getDb } = require("../db");
const {
  MAX_BATCH_SIZE,
  sanitizeString,
  validateSessionId,
  safeJsonStringify,
  isValidEventType,
  clampNonNegInt,
  clampNonNegFloat,
} = require("../lib/validation");

const router = express.Router();

// POST /events — Ingest events (batched)
router.post("/", (req, res) => {
  const db = getDb();
  const { events } = req.body;

  if (!events || !Array.isArray(events)) {
    return res
      .status(400)
      .json({ error: "Missing 'events' array in request body" });
  }

  if (events.length > MAX_BATCH_SIZE) {
    return res.status(400).json({
      error: `Batch too large: ${events.length} events (max ${MAX_BATCH_SIZE})`,
    });
  }

  if (events.length === 0) {
    return res.json({ status: "ok", processed: 0 });
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
    let skipped = 0;

    for (const event of eventList) {
      const sessionId = validateSessionId(event.session_id);
      if (!sessionId) {
        skipped++;
        continue;
      }

      const eventType = sanitizeString(event.event_type || "generic", 64);
      if (!isValidEventType(eventType)) {
        skipped++;
        continue;
      }

      const tokensIn = clampNonNegInt(event.tokens_in);
      const tokensOut = clampNonNegInt(event.tokens_out);
      const durationMs = clampNonNegFloat(event.duration_ms);

      // Handle session lifecycle events
      if (eventType === "session_start") {
        insertSession.run(
          sessionId,
          sanitizeString(event.agent_name || "default-agent", 256),
          sanitizeString(event.timestamp || new Date().toISOString(), 64),
          safeJsonStringify(event.metadata || {}),
          "active"
        );
        processed++;
        continue;
      }

      if (eventType === "session_end") {
        const totalTokIn = clampNonNegInt(event.total_tokens_in);
        const totalTokOut = clampNonNegInt(event.total_tokens_out);
        endSession.run(
          sanitizeString(event.ended_at || new Date().toISOString(), 64),
          sanitizeString(event.status || "completed", 32),
          totalTokIn,
          totalTokIn,
          totalTokOut,
          totalTokOut,
          sessionId
        );
        processed++;
        continue;
      }

      // Regular event
      const eventId =
        sanitizeString(event.event_id, 64) ||
        uuidv4().replace(/-/g, "").slice(0, 16);

      // Ensure session exists
      insertSession.run(
        sessionId,
        "default-agent",
        sanitizeString(event.timestamp || new Date().toISOString(), 64),
        "{}",
        "active"
      );

      insertEvent.run(
        eventId,
        sessionId,
        eventType,
        sanitizeString(event.timestamp || new Date().toISOString(), 64),
        safeJsonStringify(event.input_data),
        safeJsonStringify(event.output_data),
        sanitizeString(event.model, 128),
        tokensIn,
        tokensOut,
        safeJsonStringify(event.tool_call),
        safeJsonStringify(event.decision_trace),
        durationMs
      );

      // Update session token counts
      updateSession.run(tokensIn, tokensOut, sessionId);

      processed++;
    }
    return { processed, skipped };
  });

  try {
    const result = transaction(events);
    res.json({ status: "ok", ...result });
  } catch (err) {
    console.error("Error ingesting events:", err);
    res.status(500).json({ error: "Failed to ingest events" });
  }
});

module.exports = router;
