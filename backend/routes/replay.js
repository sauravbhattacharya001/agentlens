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
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

function safeJsonParse(str, fallback = null) {
  if (!str) return fallback;
  try {
    return JSON.parse(str);
  } catch {
    return fallback;
  }
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

router.get(
  "/:sessionId",
  wrapRoute((req, res) => {
    const { sessionId } = req.params;
    if (!sessionId || typeof sessionId !== "string" || sessionId.length > 128) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const db = getDb();
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(sessionId);

    const speed = Math.max(0.1, Math.min(100, parseFloat(req.query.speed) || 1));
    const maxDelay = Math.max(0, parseInt(req.query.maxDelay, 10) || 30000);

    let frames = buildFrames(events, { speedMultiplier: speed, maxDelayMs: maxDelay });

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
      replay: { speed, max_delay_ms: maxDelay, ...summary, frames },
    });
  })
);

router.get(
  "/:sessionId/frame/:index",
  wrapRoute((req, res) => {
    const { sessionId, index } = req.params;
    if (!sessionId || typeof sessionId !== "string" || sessionId.length > 128) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const frameIndex = parseInt(index, 10);
    if (isNaN(frameIndex) || frameIndex < 0) {
      return res.status(400).json({ error: "Invalid frame index" });
    }

    const db = getDb();
    const session = db.prepare("SELECT session_id FROM sessions WHERE session_id = ?").get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(sessionId);

    if (frameIndex >= events.length) {
      return res.status(404).json({ error: "Frame index out of range", total_frames: events.length });
    }

    const frames = buildFrames(events);
    const frame = frames[frameIndex];

    return res.json({
      frame,
      total_frames: frames.length,
      has_next: frameIndex < frames.length - 1,
      has_previous: frameIndex > 0,
    });
  })
);

router.get(
  "/:sessionId/summary",
  wrapRoute((req, res) => {
    const { sessionId } = req.params;
    if (!sessionId || typeof sessionId !== "string" || sessionId.length > 128) {
      return res.status(400).json({ error: "Invalid session ID" });
    }

    const db = getDb();
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(sessionId);

    const frames = buildFrames(events);
    const summary = replaySummary(frames, session);

    return res.json(summary);
  })
);

module.exports = router;
module.exports._internals = { buildFrames, replaySummary, msBetween, classifyEvent, safeJsonParse };
