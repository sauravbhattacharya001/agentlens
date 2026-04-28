/**
 * Agent Behavior Profiler – builds behavioral fingerprints for agents
 * and detects drift from established patterns over time.
 *
 * Tracks behavioral dimensions: tool-call distribution, response-time
 * patterns, error affinity, token-usage shape, and event-type mix.
 * Compares recent windows against historical baselines to surface
 * significant drift with severity classification.
 *
 * Routes:
 *   GET  /profiler              – list all agent profiles with drift status
 *   GET  /profiler/:agent       – detailed profile for one agent
 *   GET  /profiler/:agent/drift – drift timeline (daily drift scores)
 *   POST /profiler/snapshot     – force a profile snapshot for all agents
 */

const express = require("express");
const { getDb } = require("../db");
const { parseDays, daysAgoCutoff, wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Helpers ────────────────────────────────────────────────────────

function cosineSimilarity(a, b) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  let dot = 0, magA = 0, magB = 0;
  for (const k of keys) {
    const va = a[k] || 0, vb = b[k] || 0;
    dot += va * vb;
    magA += va * va;
    magB += vb * vb;
  }
  if (magA === 0 || magB === 0) return 1;
  return dot / (Math.sqrt(magA) * Math.sqrt(magB));
}

function jensenShannonDivergence(p, q) {
  const keys = new Set([...Object.keys(p), ...Object.keys(q)]);
  const m = {};
  for (const k of keys) m[k] = ((p[k] || 0) + (q[k] || 0)) / 2;
  let kl_pm = 0, kl_qm = 0;
  for (const k of keys) {
    const pk = p[k] || 0, qk = q[k] || 0, mk = m[k];
    if (pk > 0 && mk > 0) kl_pm += pk * Math.log2(pk / mk);
    if (qk > 0 && mk > 0) kl_qm += qk * Math.log2(qk / mk);
  }
  return (kl_pm + kl_qm) / 2;
}

function normalize(obj) {
  const total = Object.values(obj).reduce((s, v) => s + v, 0);
  if (total === 0) return obj;
  const out = {};
  for (const [k, v] of Object.entries(obj)) out[k] = v / total;
  return out;
}

function classifyDrift(jsd) {
  if (jsd >= 0.4) return "critical";
  if (jsd >= 0.25) return "high";
  if (jsd >= 0.1) return "medium";
  return "stable";
}

// ── Profile builder ────────────────────────────────────────────────

function buildProfile(sessions, events) {
  const eventTypeDist = {};
  const toolCallDist = {};
  let totalTokens = 0, totalDuration = 0, totalErrors = 0;
  const durations = [];
  const tokenCounts = [];

  for (const s of sessions) {
    totalTokens += s.total_tokens || 0;
    totalDuration += s.duration_ms || 0;
    totalErrors += s.error_count || 0;
    if (s.duration_ms) durations.push(s.duration_ms);
    if (s.total_tokens) tokenCounts.push(s.total_tokens);
  }

  for (const e of events) {
    const t = e.type || "unknown";
    eventTypeDist[t] = (eventTypeDist[t] || 0) + 1;
    if (t === "tool_call" || t === "function_call") {
      const name = e.name || e.tool_name || "unknown_tool";
      toolCallDist[name] = (toolCallDist[name] || 0) + 1;
    }
  }

  const n = sessions.length || 1;
  return {
    sessionCount: sessions.length,
    avgTokens: Math.round(totalTokens / n),
    avgDuration: Math.round(totalDuration / n),
    errorRate: totalErrors / n,
    eventTypeDist: normalize(eventTypeDist),
    toolCallDist: normalize(toolCallDist),
    p50Duration: percentile(durations, 0.5),
    p95Duration: percentile(durations, 0.95),
    p50Tokens: percentile(tokenCounts, 0.5),
    p95Tokens: percentile(tokenCounts, 0.95),
  };
}

function percentile(arr, p) {
  if (!arr.length) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.ceil(p * sorted.length) - 1;
  return sorted[Math.max(0, idx)];
}

// ── Data fetchers ──────────────────────────────────────────────────

function fetchSessions(db, agentName, cutoff) {
  let sql = `SELECT session_id, agent_name,
               COALESCE(total_tokens_in, 0) + COALESCE(total_tokens_out, 0) AS total_tokens,
               CAST((julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 86400000 AS INTEGER) AS duration_ms,
               0 AS error_count,
               started_at AS created_at
             FROM sessions WHERE started_at >= ?`;
  const params = [cutoff];
  if (agentName) { sql += " AND agent_name = ?"; params.push(agentName); }
  sql += " ORDER BY started_at ASC";
  return db.prepare(sql).all(...params);
}

/**
 * Fetch events for a list of session IDs, chunking to stay within
 * SQLite's ~999 variable limit per query.  Without chunking, >999
 * session IDs would crash with "SQLITE_ERROR: too many SQL variables".
 */
function fetchEvents(db, sessionIds) {
  if (!sessionIds.length) return [];
  const CHUNK = 500;
  const results = [];
  for (let i = 0; i < sessionIds.length; i += CHUNK) {
    const chunk = sessionIds.slice(i, i + CHUNK);
    const placeholders = chunk.map(() => "?").join(",");
    const rows = db.prepare(
      `SELECT session_id, event_type AS type, tool_call FROM events WHERE session_id IN (${placeholders})`
    ).all(...chunk);
    // Extract tool name from tool_call JSON for tool distribution
    for (const row of rows) {
      if (row.tool_call && typeof row.tool_call === 'string') {
        try {
          const tc = JSON.parse(row.tool_call);
          row.name = tc.name || tc.tool_name || null;
        } catch { row.name = null; }
      }
    }
    results.push(...rows);
  }
  return results;
}

// ── Routes ─────────────────────────────────────────────────────────

// GET /profiler – all agent profiles with drift summary
router.get("/", wrapRoute("list agent profiles", (req, res) => {
  const db = getDb();
  const days = parseDays(req.query.days, 30, 90);
  const recentDays = Math.max(1, Math.min(parseInt(req.query.recent) || 7, days));
  const cutoff = daysAgoCutoff(days);
  const recentCutoff = daysAgoCutoff(recentDays);

  // Get all agents
  const agents = db.prepare(
    "SELECT DISTINCT agent_name FROM sessions WHERE agent_name IS NOT NULL AND started_at >= ?"
  ).all(cutoff).map(r => r.agent_name);

  const profiles = agents.map(agent => {
    const allSessions = fetchSessions(db, agent, cutoff);
    const recentSessions = allSessions.filter(s => s.created_at >= recentCutoff);
    const historicalSessions = allSessions.filter(s => s.created_at < recentCutoff);

    if (historicalSessions.length < 3) {
      return { agent, status: "building", sessionCount: allSessions.length, message: "Need more historical data" };
    }

    const allIds = allSessions.map(s => s.session_id);
    // Use Sets for O(1) membership tests instead of Array.includes() O(n).
    // With hundreds of sessions and thousands of events, the old code was
    // O(events × sessions) per filter — quadratic in practice.  Two Set
    // lookups + a single-pass partition reduces this to O(events).
    const recentIdSet = new Set(recentSessions.map(s => s.session_id));
    const historicalIdSet = new Set(historicalSessions.map(s => s.session_id));
    const allEvents = fetchEvents(db, allIds);

    // Single-pass partition: split events into historical vs recent
    // instead of two separate .filter() passes over the full array.
    const historicalEvents = [];
    const recentEvents = [];
    for (let ei = 0; ei < allEvents.length; ei++) {
      const ev = allEvents[ei];
      if (recentIdSet.has(ev.session_id)) {
        recentEvents.push(ev);
      } else if (historicalIdSet.has(ev.session_id)) {
        historicalEvents.push(ev);
      }
    }

    const baseline = buildProfile(historicalSessions, historicalEvents);
    const recent = buildProfile(recentSessions, recentEvents);

    const eventDrift = jensenShannonDivergence(baseline.eventTypeDist, recent.eventTypeDist);
    const toolDrift = jensenShannonDivergence(baseline.toolCallDist, recent.toolCallDist);
    const tokenDrift = Math.abs(recent.avgTokens - baseline.avgTokens) / (baseline.avgTokens || 1);
    const durationDrift = Math.abs(recent.avgDuration - baseline.avgDuration) / (baseline.avgDuration || 1);
    const errorDrift = Math.abs(recent.errorRate - baseline.errorRate);

    const overallDrift = (eventDrift * 0.3 + toolDrift * 0.3 + Math.min(tokenDrift, 1) * 0.15 +
      Math.min(durationDrift, 1) * 0.15 + Math.min(errorDrift, 1) * 0.1);

    return {
      agent,
      status: classifyDrift(overallDrift),
      overallDrift: +overallDrift.toFixed(4),
      dimensions: {
        eventMix: { drift: +eventDrift.toFixed(4), severity: classifyDrift(eventDrift) },
        toolUsage: { drift: +toolDrift.toFixed(4), severity: classifyDrift(toolDrift) },
        tokenUsage: { drift: +tokenDrift.toFixed(4), severity: classifyDrift(tokenDrift) },
        duration: { drift: +durationDrift.toFixed(4), severity: classifyDrift(durationDrift) },
        errorRate: { drift: +errorDrift.toFixed(4), severity: classifyDrift(errorDrift) },
      },
      sessionCount: allSessions.length,
      recentSessionCount: recentSessions.length,
      baseline: { avgTokens: baseline.avgTokens, avgDuration: baseline.avgDuration, errorRate: +baseline.errorRate.toFixed(4) },
      recent: { avgTokens: recent.avgTokens, avgDuration: recent.avgDuration, errorRate: +recent.errorRate.toFixed(4) },
    };
  });

  profiles.sort((a, b) => (b.overallDrift || 0) - (a.overallDrift || 0));
  res.json({ profiles, meta: { days, recentDays, agentCount: agents.length } });
}));

// ── Agent name validation ──────────────────────────────────────────
// Agent names come from user-controlled URL path segments.  Limit to
// 128 chars and reject characters that could confuse SQL or logs.
const SAFE_AGENT_RE = /^[\w .:\-@/]{1,128}$/;

function validateAgent(req, res) {
  const agent = req.params.agent;
  if (!agent || !SAFE_AGENT_RE.test(agent)) {
    res.status(400).json({ error: "Invalid agent name format" });
    return null;
  }
  return agent;
}

// GET /profiler/:agent – detailed profile
router.get("/:agent", wrapRoute("get agent profile", (req, res) => {
  const db = getDb();
  const agent = validateAgent(req, res);
  if (!agent) return;
  const days = parseDays(req.query.days, 30, 90);
  const cutoff = daysAgoCutoff(days);

  const sessions = fetchSessions(db, agent, cutoff);
  if (!sessions.length) return res.status(404).json({ error: "Agent not found or no sessions in range" });

  const events = fetchEvents(db, sessions.map(s => s.session_id));
  const profile = buildProfile(sessions, events);

  // Daily breakdown — index sessions by ID for O(1) lookup instead
  // of O(n) sessions.find() per event (was O(events × sessions)).
  const dailyMap = {};
  const sessionById = {};
  for (const s of sessions) {
    sessionById[s.session_id] = s;
    const day = s.created_at.slice(0, 10);
    if (!dailyMap[day]) dailyMap[day] = { sessions: [], events: [] };
    dailyMap[day].sessions.push(s);
  }
  for (const e of events) {
    const s = sessionById[e.session_id];
    if (s) {
      const day = s.created_at.slice(0, 10);
      if (dailyMap[day]) dailyMap[day].events.push(e);
    }
  }

  const daily = Object.entries(dailyMap).sort().map(([date, data]) => ({
    date,
    ...buildProfile(data.sessions, data.events),
  }));

  res.json({ agent, profile, daily, meta: { days, totalSessions: sessions.length } });
}));

// GET /profiler/:agent/drift – drift timeline
router.get("/:agent/drift", wrapRoute("get drift timeline", (req, res) => {
  const db = getDb();
  const agent = validateAgent(req, res);
  if (!agent) return;
  const days = parseDays(req.query.days, 30, 90);
  const windowDays = Math.max(1, Math.min(parseInt(req.query.window) || 3, 14));
  const cutoff = daysAgoCutoff(days);

  const sessions = fetchSessions(db, agent, cutoff);
  if (sessions.length < 5) return res.json({ agent, timeline: [], message: "Insufficient data" });

  const events = fetchEvents(db, sessions.map(s => s.session_id));

  // Build baseline from first half
  const midpoint = Math.floor(sessions.length / 2);
  const baselineSessions = sessions.slice(0, midpoint);
  const baselineIds = new Set(baselineSessions.map(s => s.session_id));
  const baseline = buildProfile(baselineSessions, events.filter(e => baselineIds.has(e.session_id)));

  // Sliding window drift
  const timeline = [];
  for (let i = midpoint; i < sessions.length; i++) {
    const windowStart = Math.max(midpoint, i - windowDays * 10);
    const windowSessions = sessions.slice(windowStart, i + 1);
    const windowIds = new Set(windowSessions.map(s => s.session_id));
    const windowProfile = buildProfile(windowSessions, events.filter(e => windowIds.has(e.session_id)));

    const eventDrift = jensenShannonDivergence(baseline.eventTypeDist, windowProfile.eventTypeDist);
    const toolDrift = jensenShannonDivergence(baseline.toolCallDist, windowProfile.toolCallDist);

    timeline.push({
      date: sessions[i].created_at.slice(0, 10),
      eventDrift: +eventDrift.toFixed(4),
      toolDrift: +toolDrift.toFixed(4),
      severity: classifyDrift(Math.max(eventDrift, toolDrift)),
      windowSize: windowSessions.length,
    });
  }

  // Deduplicate by date (keep last entry per day)
  const byDate = {};
  for (const t of timeline) byDate[t.date] = t;
  const dedupedTimeline = Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date));

  res.json({ agent, baseline: { avgTokens: baseline.avgTokens, avgDuration: baseline.avgDuration }, timeline: dedupedTimeline });
}));

// POST /profiler/snapshot – force snapshot
router.post("/snapshot", wrapRoute("create profile snapshot", (req, res) => {
  const db = getDb();
  const cutoff = daysAgoCutoff(30);
  const agents = db.prepare(
    "SELECT DISTINCT agent_name FROM sessions WHERE agent_name IS NOT NULL AND started_at >= ?"
  ).all(cutoff).map(r => r.agent_name);

  const snapshots = agents.map(agent => {
    const sessions = fetchSessions(db, agent, cutoff);
    const events = fetchEvents(db, sessions.map(s => s.session_id));
    return { agent, profile: buildProfile(sessions, events), timestamp: new Date().toISOString() };
  });

  res.json({ snapshots, count: snapshots.length });
}));

module.exports = router;
