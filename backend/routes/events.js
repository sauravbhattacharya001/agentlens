const express = require("express");
const { v4: uuidv4 } = require("uuid");
const { getDb } = require("../db");

const router = express.Router();

// ── Input validation constants ──────────────────────────────────────
const MAX_BATCH_SIZE = 500; // Max events per batch to prevent memory exhaustion
const MAX_STRING_LENGTH = 1024; // Max length for identifier fields
const MAX_DATA_LENGTH = 1024 * 256; // 256KB max for JSON data fields
const VALID_EVENT_TYPES = new Set([
  "session_start",
  "session_end",
  "llm_call",
  "tool_call",
  "agent_call",
  "error",
  "generic",
]);
const SESSION_ID_RE = /^[a-zA-Z0-9_\-.:]+$/;

/**
 * Sanitize a string field: enforce max length, strip control characters.
 */
function sanitizeString(val, maxLen = MAX_STRING_LENGTH) {
  if (typeof val !== "string") return null;
  // Strip control characters (except newlines/tabs in data fields)
  const cleaned = val.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
  return cleaned.slice(0, maxLen);
}

/**
 * Validate and sanitize a session ID.
 */
function validateSessionId(id) {
  if (!id || typeof id !== "string") return null;
  const trimmed = id.slice(0, 128);
  return SESSION_ID_RE.test(trimmed) ? trimmed : null;
}

/**
 * Safely stringify JSON data with size limit.
 */
function safeJsonStringify(data, maxLen = MAX_DATA_LENGTH) {
  if (data == null) return null;
  try {
    const str = JSON.stringify(data);
    if (str.length > maxLen) {
      return JSON.stringify({ _truncated: true, _original_size: str.length });
    }
    return str;
  } catch {
    return null;
  }
}

// POST /events — Ingest events (batched)
router.post("/", (req, res) => {
  const db = getDb();
  const { events } = req.body;

  if (!events || !Array.isArray(events)) {
    return res
      .status(400)
      .json({ error: "Missing 'events' array in request body" });
  }

  // ── Security: Enforce batch size limit ────────────────────────────
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
      // ── Validate session ID ─────────────────────────────────────
      const sessionId = validateSessionId(event.session_id);
      if (!sessionId) {
        skipped++;
        continue;
      }

      // ── Validate event type ─────────────────────────────────────
      const eventType = sanitizeString(event.event_type || "generic", 64);
      if (!VALID_EVENT_TYPES.has(eventType)) {
        skipped++;
        continue;
      }

      // ── Validate numeric fields ─────────────────────────────────
      const tokensIn = Number.isFinite(event.tokens_in)
        ? Math.max(0, Math.floor(event.tokens_in))
        : 0;
      const tokensOut = Number.isFinite(event.tokens_out)
        ? Math.max(0, Math.floor(event.tokens_out))
        : 0;
      const durationMs = Number.isFinite(event.duration_ms)
        ? Math.max(0, event.duration_ms)
        : null;

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
        const totalTokIn = Number.isFinite(event.total_tokens_in)
          ? Math.max(0, Math.floor(event.total_tokens_in))
          : 0;
        const totalTokOut = Number.isFinite(event.total_tokens_out)
          ? Math.max(0, Math.floor(event.total_tokens_out))
          : 0;
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
    // ── Security: Don't leak error details to clients ─────────────
    res.status(500).json({ error: "Failed to ingest events" });
  }
});

module.exports = router;
