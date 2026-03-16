const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, safeJsonParse } = require("../lib/validation");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

/**
 * Parse raw event rows into objects with parsed JSON fields.
 */
function parseEventRow(row) {
  return {
    id: row.id,
    session_id: row.session_id,
    event_type: row.event_type,
    timestamp: row.timestamp,
    duration_ms: row.duration_ms,
    model: row.model,
    tokens_in: row.tokens_in || 0,
    tokens_out: row.tokens_out || 0,
    tool_call: safeJsonParse(row.tool_call, null),
    input_data: safeJsonParse(row.input_data, null),
    output_data: safeJsonParse(row.output_data, null),
  };
}

/**
 * Load a session and its events by session_id.
 */
function loadSession(db, sessionId) {
  const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(sessionId);
  if (!session) return null;
  const events = db.prepare(
    "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC"
  ).all(sessionId);
  return {
    ...session,
    events: events.map(parseEventRow),
    metadata: safeJsonParse(session.metadata, {}),
  };
}

/**
 * Count tool calls by tool name from events.
 */
function toolCounts(events) {
  const counts = {};
  for (const e of events) {
    if (e.tool_call && e.tool_call.tool_name) {
      const name = e.tool_call.tool_name;
      counts[name] = (counts[name] || 0) + 1;
    }
  }
  return counts;
}

/**
 * Count model usage from events.
 */
function modelCounts(events) {
  const counts = {};
  for (const e of events) {
    if (e.model) {
      counts[e.model] = (counts[e.model] || 0) + 1;
    }
  }
  return counts;
}

/**
 * Create an event key for alignment.
 */
function eventKey(e) {
  if (e.tool_call && e.tool_call.tool_name) {
    return `${e.event_type}:${e.tool_call.tool_name}`;
  }
  return e.event_type;
}

/**
 * Align two event lists using LCS on event keys.
 */
function alignEvents(baseline, candidate) {
  const n = baseline.length;
  const m = candidate.length;

  // DP table
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (eventKey(baseline[i]) === eventKey(candidate[j])) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  const pairs = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (eventKey(baseline[i]) === eventKey(candidate[j])) {
      const changes = {};
      if (baseline[i].tokens_in !== candidate[j].tokens_in) {
        changes.tokens_in = `${baseline[i].tokens_in}→${candidate[j].tokens_in}`;
      }
      if (baseline[i].tokens_out !== candidate[j].tokens_out) {
        changes.tokens_out = `${baseline[i].tokens_out}→${candidate[j].tokens_out}`;
      }
      if (baseline[i].model !== candidate[j].model) {
        changes.model = `${baseline[i].model}→${candidate[j].model}`;
      }
      if (baseline[i].duration_ms && candidate[j].duration_ms) {
        if (Math.abs(baseline[i].duration_ms - candidate[j].duration_ms) > 10) {
          changes.duration_ms = `${baseline[i].duration_ms}→${candidate[j].duration_ms}`;
        }
      }
      const status = Object.keys(changes).length > 0 ? "modified" : "matched";
      pairs.push({
        baseline: baseline[i],
        candidate: candidate[j],
        status,
        changes,
        label: eventKey(baseline[i]),
      });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      pairs.push({
        baseline: baseline[i],
        candidate: null,
        status: "removed",
        changes: {},
        label: eventKey(baseline[i]),
      });
      i++;
    } else {
      pairs.push({
        baseline: null,
        candidate: candidate[j],
        status: "added",
        changes: {},
        label: eventKey(candidate[j]),
      });
      j++;
    }
  }
  while (i < n) {
    pairs.push({ baseline: baseline[i], candidate: null, status: "removed", changes: {}, label: eventKey(baseline[i]) });
    i++;
  }
  while (j < m) {
    pairs.push({ baseline: null, candidate: candidate[j], status: "added", changes: {}, label: eventKey(candidate[j]) });
    j++;
  }
  return pairs;
}

// GET /api/diff?baseline=ID&candidate=ID
router.get(
  "/",
  wrapRoute("compute session diff", async (req, res) => {
    const { baseline: baselineId, candidate: candidateId } = req.query;

    if (!baselineId || !candidateId) {
      return res.status(400).json({ error: "baseline and candidate query params required" });
    }
    if (!isValidSessionId(baselineId) || !isValidSessionId(candidateId)) {
      return res.status(400).json({ error: "Invalid session ID format" });
    }
    if (baselineId === candidateId) {
      return res.status(400).json({ error: "Cannot diff a session with itself" });
    }

    const db = getDb();
    const baselineSession = loadSession(db, baselineId);
    const candidateSession = loadSession(db, candidateId);

    if (!baselineSession) {
      return res.status(404).json({ error: `Baseline session ${baselineId} not found` });
    }
    if (!candidateSession) {
      return res.status(404).json({ error: `Candidate session ${candidateId} not found` });
    }

    const bEvents = baselineSession.events;
    const cEvents = candidateSession.events;

    // Token deltas
    const bTokensIn = bEvents.reduce((s, e) => s + e.tokens_in, 0);
    const bTokensOut = bEvents.reduce((s, e) => s + e.tokens_out, 0);
    const cTokensIn = cEvents.reduce((s, e) => s + e.tokens_in, 0);
    const cTokensOut = cEvents.reduce((s, e) => s + e.tokens_out, 0);

    // Duration
    const bDur = bEvents.reduce((s, e) => s + (e.duration_ms || 0), 0);
    const cDur = cEvents.reduce((s, e) => s + (e.duration_ms || 0), 0);

    // Tool deltas
    const bTools = toolCounts(bEvents);
    const cTools = toolCounts(cEvents);
    const allTools = [...new Set([...Object.keys(bTools), ...Object.keys(cTools)])].sort();

    // Model deltas
    const bModels = modelCounts(bEvents);
    const cModels = modelCounts(cEvents);

    // Event alignment
    const alignment = alignEvents(bEvents, cEvents);
    const matched = alignment.filter(p => p.status === "matched" || p.status === "modified").length;
    const similarity = alignment.length > 0 ? matched / alignment.length : 1;

    // Event types
    const bTypes = new Set(bEvents.map(e => e.event_type));
    const cTypes = new Set(cEvents.map(e => e.event_type));

    res.json({
      baseline: {
        session_id: baselineId,
        agent_name: baselineSession.agent_name,
        status: baselineSession.status,
        event_count: bEvents.length,
        tokens_in: bTokensIn,
        tokens_out: bTokensOut,
        duration_ms: bDur,
      },
      candidate: {
        session_id: candidateId,
        agent_name: candidateSession.agent_name,
        status: candidateSession.status,
        event_count: cEvents.length,
        tokens_in: cTokensIn,
        tokens_out: cTokensOut,
        duration_ms: cDur,
      },
      deltas: {
        tokens_in: cTokensIn - bTokensIn,
        tokens_out: cTokensOut - bTokensOut,
        tokens_total: (cTokensIn + cTokensOut) - (bTokensIn + bTokensOut),
        duration_ms: cDur - bDur,
        event_count: cEvents.length - bEvents.length,
      },
      tools: {
        added: allTools.filter(t => !(t in bTools)),
        removed: allTools.filter(t => !(t in cTools)),
        common: allTools.filter(t => t in bTools && t in cTools),
        baseline_counts: bTools,
        candidate_counts: cTools,
      },
      models: {
        baseline: bModels,
        candidate: cModels,
      },
      event_types: {
        added: [...cTypes].filter(t => !bTypes.has(t)).sort(),
        removed: [...bTypes].filter(t => !cTypes.has(t)).sort(),
      },
      alignment: alignment.map(p => ({
        label: p.label,
        status: p.status,
        changes: p.changes,
        baseline_tokens: p.baseline ? p.baseline.tokens_in + p.baseline.tokens_out : null,
        candidate_tokens: p.candidate ? p.candidate.tokens_in + p.candidate.tokens_out : null,
        baseline_duration: p.baseline ? p.baseline.duration_ms : null,
        candidate_duration: p.candidate ? p.candidate.duration_ms : null,
      })),
      similarity,
    });
  })
);

module.exports = router;
