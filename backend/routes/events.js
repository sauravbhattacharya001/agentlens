const express = require("express");
const crypto = require("crypto");
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
const { wrapRoute } = require("../lib/request-helpers");
const { createLazyStatements } = require("../lib/lazy-statements");

const router = express.Router();

// ── Cached prepared statements ──────────────────────────────────────
const getStatements = createLazyStatements((db) => ({
  insertSession: db.prepare(`
    INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, metadata, status)
    VALUES (?, ?, ?, ?, ?)
  `),
  updateSession: db.prepare(`
    UPDATE sessions 
    SET total_tokens_in = total_tokens_in + ?,
        total_tokens_out = total_tokens_out + ?
    WHERE session_id = ?
  `),
  endSession: db.prepare(`
    UPDATE sessions SET ended_at = ?, status = ?, 
      total_tokens_in = CASE WHEN ? > 0 THEN ? ELSE total_tokens_in END,
      total_tokens_out = CASE WHEN ? > 0 THEN ? ELSE total_tokens_out END
    WHERE session_id = ?
  `),
  insertEvent: db.prepare(`
    INSERT OR IGNORE INTO events (event_id, session_id, event_type, timestamp, input_data, output_data, model, tokens_in, tokens_out, tool_call, decision_trace, duration_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `),
}));

// POST /events — Ingest events (batched)
router.post("/", wrapRoute("ingest events", (req, res) => {
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

  const { insertSession, updateSession, endSession, insertEvent } = getStatements();

  const transaction = db.transaction((eventList) => {
    let processed = 0;
    let skipped = 0;

    // Track sessions already ensured within this batch to avoid
    // redundant INSERT OR IGNORE calls. In a 500-event batch with
    // 5 distinct sessions, this eliminates ~495 unnecessary writes.
    const ensuredSessions = new Set();
    // Accumulate token counts per session to batch UPDATE calls.
    // Instead of N updates (one per event), we do M updates (one per session).
    const sessionTokens = new Map();
    // Hoist timestamp generation outside the loop — Date construction
    // and ISO serialization are surprisingly expensive at high throughput.
    const nowIso = new Date().toISOString();

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
          sanitizeString(event.timestamp || nowIso, 64),
          safeJsonStringify(event.metadata || {}),
          "active"
        );
        ensuredSessions.add(sessionId);
        processed++;
        continue;
      }

      if (eventType === "session_end") {
        const totalTokIn = clampNonNegInt(event.total_tokens_in);
        const totalTokOut = clampNonNegInt(event.total_tokens_out);
        endSession.run(
          sanitizeString(event.ended_at || nowIso, 64),
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
        crypto.randomUUID().replace(/-/g, "").slice(0, 16);

      // Ensure session exists (only once per session per batch)
      if (!ensuredSessions.has(sessionId)) {
        insertSession.run(
          sessionId,
          "default-agent",
          sanitizeString(event.timestamp || nowIso, 64),
          "{}",
          "active"
        );
        ensuredSessions.add(sessionId);
      }

      const eventTs = sanitizeString(event.timestamp || nowIso, 64);

      insertEvent.run(
        eventId,
        sessionId,
        eventType,
        eventTs,
        safeJsonStringify(event.input_data),
        safeJsonStringify(event.output_data),
        sanitizeString(event.model, 128),
        tokensIn,
        tokensOut,
        safeJsonStringify(event.tool_call),
        safeJsonStringify(event.decision_trace),
        durationMs
      );

      // Accumulate session token counts (batched update below)
      let acc = sessionTokens.get(sessionId);
      if (!acc) {
        acc = { tokIn: 0, tokOut: 0 };
        sessionTokens.set(sessionId, acc);
      }
      acc.tokIn += tokensIn;
      acc.tokOut += tokensOut;

      processed++;
    }

    // Batch-update session token counts (one UPDATE per session, not per event)
    for (const [sid, { tokIn, tokOut }] of sessionTokens) {
      if (tokIn > 0 || tokOut > 0) {
        updateSession.run(tokIn, tokOut, sid);
      }
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
}));

module.exports = router;
