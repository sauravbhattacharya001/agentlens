/**
 * Session Replay — step-by-step event playback for debugging agent sessions.
 *
 * Routes:
 *   GET /replay/:sessionId         — full replay (events with timing)
 *   GET /replay/:sessionId/frame/:index — single frame
 *   GET /replay/:sessionId/summary — replay stats & metadata
 */
const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, safeJsonParse: _sharedSafeJsonParse } = require("../lib/validation");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// Maximum events loaded for replay to prevent OOM on huge sessions
const REPLAY_EVENT_CAP = 5000;

// Thin wrapper around the shared safeJsonParse to preserve the replay
// module's original default fallback of `null` (the shared version
// defaults to `{}`).
function safeJsonParse(str, fallback = null) {
  return _sharedSafeJsonParse(str, fallback);
}

function msBetween(a, b) {
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (isNaN(ta) || isNaN(tb)) return 0;
  return Math.max(0, tb - ta);
}

function classifyEvent(event) {
  const type = (event.event_type || "").toLowerCase();
  if (type.includes("llm") || type.includes("completion")) return "llm_call";
  if (type.includes("tool")) return "tool_use";
  if (type.includes("error") || type.includes("fail")) return "error";
  if (type.includes("decision") || type.includes("plan")) return "decision";
  return type || "generic";
}

function buildFrames(events, options = {}) {
  const { speedMultiplier = 1, maxDelayMs = 30000 } = options;
  if (!events || events.length === 0) return [];

  const frames = [];
  let cumulativeMs = 0;

  for (let i = 0; i < events.length; i++) {
    const event = events[i];
    let delayMs = 0;

    if (i > 0) {
      const raw = msBetween(events[i - 1].timestamp, event.timestamp);
      delayMs = Math.min(raw, maxDelayMs);
      if (speedMultiplier > 0) {
        delayMs = Math.round(delayMs / speedMultiplier);
      }
    }

    cumulativeMs += delayMs;

    frames.push({
      index: i,
      event_id: event.event_id,
      event_type: event.event_type,
      category: classifyEvent(event),
      timestamp: event.timestamp,
      delay_ms: delayMs,
      elapsed_ms: cumulativeMs,
      model: event.model || null,
      tokens_in: event.tokens_in || 0,
      tokens_out: event.tokens_out || 0,
      duration_ms: event.duration_ms || null,
      input_data: safeJsonParse(event.input_data),
      output_data: safeJsonParse(event.output_data),
      tool_call: safeJsonParse(event.tool_call),
      decision_trace: safeJsonParse(event.decision_trace),
    });
  }

  return frames;
}

function replaySummary(frames, session) {
  if (!frames || frames.length === 0) {
    return {
      total_frames: 0,
      total_duration_ms: 0,
      event_types: {},
      categories: {},
      models_used: [],
      total_tokens_in: 0,
      total_tokens_out: 0,
      avg_delay_ms: 0,
      max_delay_ms: 0,
      speed_recommendation: "1x",
    };
  }

  const eventTypes = {};
  const categories = {};
  const modelsSet = new Set();
  let totalTokensIn = 0;
  let totalTokensOut = 0;
  let maxDelay = 0;
  let totalDelay = 0;

  for (const frame of frames) {
    eventTypes[frame.event_type] = (eventTypes[frame.event_type] || 0) + 1;
    categories[frame.category] = (categories[frame.category] || 0) + 1;
    if (frame.model) modelsSet.add(frame.model);
    totalTokensIn += frame.tokens_in;
    totalTokensOut += frame.tokens_out;
    if (frame.delay_ms > maxDelay) maxDelay = frame.delay_ms;
    totalDelay += frame.delay_ms;
  }

  const totalDurationMs = frames[frames.length - 1].elapsed_ms;
  const avgDelay = frames.length > 1 ? Math.round(totalDelay / (frames.length - 1)) : 0;

  let speedRec = "1x";
  if (totalDurationMs > 300000) speedRec = "10x";
  else if (totalDurationMs > 60000) speedRec = "5x";
  else if (totalDurationMs > 10000) speedRec = "2x";
  else if (totalDurationMs < 2000) speedRec = "0.5x";

  return {
    session_id: session ? session.session_id : null,
    agent_name: session ? session.agent_name : null,
    session_status: session ? session.status : null,
    total_frames: frames.length,
    total_duration_ms: totalDurationMs,
    event_types: eventTypes,
    categories: categories,
    models_used: [...modelsSet],
    total_tokens_in: totalTokensIn,
    total_tokens_out: totalTokensOut,
    avg_delay_ms: avgDelay,
    max_delay_ms: maxDelay,
    speed_recommendation: speedRec,
  };
}

// ── Cached prepared statements for replay ───────────────────────────
// Lazily initialized once, reused across all requests to avoid
// re-compiling SQL on every call (consistent with sessions.js,
// analytics.js, and other route modules).
let _replayStmts = null;

function getReplayStatements() {
  if (_replayStmts) return _replayStmts;
  const db = getDb();

  _replayStmts = {
    getSession: db.prepare("SELECT * FROM sessions WHERE session_id = ?"),
    getSessionId: db.prepare("SELECT session_id FROM sessions WHERE session_id = ?"),
    eventsCapped: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?"),
    countEvents: db.prepare("SELECT COUNT(*) AS total FROM events WHERE session_id = ?"),
    eventsPaged: db.prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT ? OFFSET ?"),
    firstEventTs: db.prepare("SELECT timestamp FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT 1"),
  };

  return _replayStmts;
}

// Shared session ID validation middleware for all replay routes
function validateSessionIdParam(req, res, next) {
  const { sessionId } = req.params;
  if (!isValidSessionId(sessionId)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }
  next();
}

router.get(
  "/:sessionId",
  validateSessionIdParam,
  wrapRoute("replay session", (req, res) => {
    const { sessionId } = req.params;

    const stmts = getReplayStatements();
    const session = stmts.getSession.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.eventsCapped.all(sessionId, REPLAY_EVENT_CAP + 1);

    const truncated = events.length > REPLAY_EVENT_CAP;
    const cappedEvents = truncated ? events.slice(0, REPLAY_EVENT_CAP) : events;

    const speed = Math.max(0.1, Math.min(100, parseFloat(req.query.speed) || 1));
    const maxDelay = Math.max(0, parseInt(req.query.maxDelay, 10) || 30000);

    let frames = buildFrames(cappedEvents, { speedMultiplier: speed, maxDelayMs: maxDelay });

    const from = parseInt(req.query.from, 10);
    const to = parseInt(req.query.to, 10);
    if (!isNaN(from) || !isNaN(to)) {
      const start = isNaN(from) ? 0 : Math.max(0, from);
      const end = isNaN(to) ? frames.length : Math.min(frames.length, to);
      frames = frames.slice(start, end);
    }

    const summary = replaySummary(frames, session);

    return res.json({
      session: {
        session_id: session.session_id,
        agent_name: session.agent_name,
        started_at: session.started_at,
        ended_at: session.ended_at,
        status: session.status,
      },
      replay: { speed, max_delay_ms: maxDelay, truncated, ...summary, frames },
    });
  })
);

router.get(
  "/:sessionId/frame/:index",
  validateSessionIdParam,
  wrapRoute("replay frame", (req, res) => {
    const { sessionId, index } = req.params;

    const frameIndex = parseInt(index, 10);
    if (isNaN(frameIndex) || frameIndex < 0) {
      return res.status(400).json({ error: "Invalid frame index" });
    }

    const stmts = getReplayStatements();
    const session = stmts.getSessionId.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    // Count total events without loading them all
    const totalFrames = stmts.countEvents.get(sessionId).total;

    if (frameIndex >= totalFrames) {
      return res.status(404).json({ error: "Frame index out of range", total_frames: totalFrames });
    }

    // Fetch only the target event and the previous one (for delay calculation).
    // LIMIT 2 with OFFSET (frameIndex > 0 ? frameIndex - 1 : 0) gives us at
    // most 2 rows: the predecessor (if any) and the target event, avoiding a
    // full table scan + frame build for all events.
    const offset = frameIndex > 0 ? frameIndex - 1 : 0;
    const limit = frameIndex > 0 ? 2 : 1;
    const nearby = stmts.eventsPaged.all(sessionId, limit, offset);

    // Build the single frame with correct delay
    const targetEvent = frameIndex > 0 ? nearby[1] : nearby[0];
    const prevEvent = frameIndex > 0 ? nearby[0] : null;

    let delayMs = 0;
    if (prevEvent) {
      delayMs = Math.min(msBetween(prevEvent.timestamp, targetEvent.timestamp), 30000);
    }

    // For cumulative elapsed_ms we need the sum of all delays up to this frame.
    // Compute it from the timestamps of the first event and the target event.
    let elapsedMs = 0;
    if (frameIndex > 0) {
      const firstEvent = stmts.firstEventTs.get(sessionId);
      if (firstEvent) {
        elapsedMs = msBetween(firstEvent.timestamp, targetEvent.timestamp);
      }
    }

    const frame = {
      index: frameIndex,
      event_id: targetEvent.event_id,
      event_type: targetEvent.event_type,
      category: classifyEvent(targetEvent),
      timestamp: targetEvent.timestamp,
      delay_ms: delayMs,
      elapsed_ms: elapsedMs,
      model: targetEvent.model || null,
      tokens_in: targetEvent.tokens_in || 0,
      tokens_out: targetEvent.tokens_out || 0,
      duration_ms: targetEvent.duration_ms || null,
      input_data: safeJsonParse(targetEvent.input_data),
      output_data: safeJsonParse(targetEvent.output_data),
      tool_call: safeJsonParse(targetEvent.tool_call),
      decision_trace: safeJsonParse(targetEvent.decision_trace),
    };

    return res.json({
      frame,
      total_frames: totalFrames,
      has_next: frameIndex < totalFrames - 1,
      has_previous: frameIndex > 0,
    });
  })
);

router.get(
  "/:sessionId/summary",
  validateSessionIdParam,
  wrapRoute("replay summary", (req, res) => {
    const { sessionId } = req.params;

    const stmts = getReplayStatements();
    const session = stmts.getSession.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.eventsCapped.all(sessionId, REPLAY_EVENT_CAP);

    const frames = buildFrames(events);
    const summary = replaySummary(frames, session);

    return res.json(summary);
  })
);

module.exports = router;
module.exports._internals = { buildFrames, replaySummary, msBetween, classifyEvent, safeJsonParse };
