/**
 * Agent Collaboration Analyzer — multi-agent teamwork analysis.
 *
 * Analyzes sessions involving multiple agents to detect collaboration
 * patterns, handoff quality, communication bottlenecks, delegation
 * chains, workload balance, and collective intelligence.
 *
 * Routes:
 *   GET  /collaboration                      — list multi-agent sessions with scores
 *   GET  /collaboration/:session_id          — detailed collaboration analysis
 *   GET  /collaboration/:session_id/handoffs — handoff timeline and quality
 *   GET  /collaboration/:session_id/bottlenecks — bottleneck analysis
 *   POST /collaboration/analyze              — trigger analysis for a session
 */

const express = require("express");
const { getDb } = require("../db");
const { parseDays, daysAgoCutoff, wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Helpers ────────────────────────────────────────────────────────

function giniCoefficient(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 0) return 0;
  const total = sorted.reduce((s, v) => s + v, 0);
  if (total === 0) return 0;
  let num = 0;
  for (let i = 0; i < n; i++) num += (2 * (i + 1) - n - 1) * sorted[i];
  return Math.max(0, Math.min(1, num / (n * total)));
}

function classifyHandoff(latencyMs, contextLoss) {
  if (contextLoss > 0.5 || latencyMs > 10000) return "failed";
  if (contextLoss > 0.2 || latencyMs > 5000) return "lossy";
  if (contextLoss > 0.05) return "acceptable";
  return "clean";
}

function classifyGrade(score) {
  if (score >= 90) return "elite";
  if (score >= 75) return "strong";
  if (score >= 60) return "functional";
  if (score >= 40) return "struggling";
  return "dysfunctional";
}

function detectPattern(events, agents) {
  if (agents.size <= 1) return "solo";

  const delegators = {};
  const edges = new Set();
  const sources = new Set();
  const targets = new Set();
  let maxDelegationDepth = 0;

  for (const e of events) {
    if (e.event_type === "delegate" && e.target_agent) {
      delegators[e.agent_id] = (delegators[e.agent_id] || 0) + 1;
    }
    if ((e.event_type === "handoff" || e.event_type === "delegate") && e.target_agent) {
      edges.add(`${e.agent_id}->${e.target_agent}`);
      sources.add(e.agent_id);
      targets.add(e.target_agent);
    }
  }

  // Orchestrated: one agent delegates to all others
  const delegatorEntries = Object.entries(delegators);
  if (delegatorEntries.length > 0) {
    const topCount = Math.max(...delegatorEntries.map(([, c]) => c));
    if (topCount >= agents.size - 1) return "orchestrated";
  }

  // Pipeline: linear chain
  const startNodes = [...sources].filter(s => !targets.has(s));
  if (edges.size === agents.size - 1 && startNodes.length === 1) return "pipeline";

  // Check delegation depth for hierarchical
  const childMap = {};
  for (const e of events) {
    if (e.event_type === "delegate" && e.target_agent) {
      if (!childMap[e.agent_id]) childMap[e.agent_id] = [];
      childMap[e.agent_id].push(e.target_agent);
    }
  }

  function getDepth(agent, visited) {
    if (visited.has(agent)) return 0;
    visited.add(agent);
    const children = childMap[agent] || [];
    let max = 0;
    for (const c of children) max = Math.max(max, 1 + getDepth(c, new Set(visited)));
    return max;
  }

  for (const root of startNodes.length ? startNodes : Object.keys(childMap)) {
    maxDelegationDepth = Math.max(maxDelegationDepth, getDepth(root, new Set()));
  }
  if (maxDelegationDepth >= 2) return "hierarchical";

  if (edges.size > agents.size) return "swarm";
  return "peer_to_peer";
}

function analyzeSession(events) {
  const agents = new Set();
  for (const e of events) {
    agents.add(e.agent_id);
    if (e.target_agent) agents.add(e.target_agent);
  }

  if (events.length === 0 || agents.size === 0) {
    return {
      agent_count: 0, event_count: 0, teamwork_score: 0,
      grade: "dysfunctional", collaboration_pattern: "solo",
      handoffs: [], bottlenecks: [], workload: [], engines: [],
    };
  }

  events.sort((a, b) => a.timestamp - b.timestamp);

  // Handoff analysis
  const handoffEvents = events.filter(e => e.event_type === "handoff" && e.target_agent);
  const handoffs = handoffEvents.map(e => {
    const latencyMs = e.metadata?.latency_ms || 0;
    const ctxSize = e.metadata?.context_size || 0;
    const ctxRecv = e.metadata?.received_context || 0;
    const contextLoss = ctxSize > 0 ? Math.max(0, 1 - ctxRecv / ctxSize) : 0;
    const verdict = classifyHandoff(latencyMs, contextLoss);
    const quality = verdict === "failed" ? 20 : verdict === "lossy" ? 50 :
                    verdict === "acceptable" ? 75 : 100;
    return {
      source_agent: e.agent_id, target_agent: e.target_agent,
      timestamp: e.timestamp, latency_ms: latencyMs,
      context_loss: Math.round(contextLoss * 1000) / 1000,
      verdict, quality,
    };
  });
  const handoffScore = handoffs.length > 0
    ? handoffs.reduce((s, h) => s + h.quality, 0) / handoffs.length : 100;

  // Bottleneck analysis
  const incoming = {}, outgoing = {};
  for (const e of events) {
    if (e.target_agent && ["handoff", "delegate", "message"].includes(e.event_type)) {
      if (!incoming[e.target_agent]) incoming[e.target_agent] = new Set();
      incoming[e.target_agent].add(e.agent_id);
      if (!outgoing[e.agent_id]) outgoing[e.agent_id] = new Set();
      outgoing[e.agent_id].add(e.target_agent);
    }
  }
  const bottlenecks = [];
  for (const agent of agents) {
    const fi = incoming[agent] ? incoming[agent].size : 0;
    const fo = outgoing[agent] ? outgoing[agent].size : 0;
    if (fi >= 3) {
      const severity = fi >= 6 ? "critical" : fi >= 5 ? "severe" : "moderate";
      bottlenecks.push({
        agent_id: agent, fan_in: fi, fan_out: fo, severity,
        waiting_agents: incoming[agent] ? [...incoming[agent]] : [],
      });
    }
  }
  const bnPenalty = bottlenecks.reduce((s, b) => {
    return s + (b.severity === "critical" ? 50 : b.severity === "severe" ? 35 : 20);
  }, 0);
  const bottleneckScore = Math.max(0, 100 - bnPenalty);

  // Workload analysis
  const agentCounts = {};
  for (const e of events) agentCounts[e.agent_id] = (agentCounts[e.agent_id] || 0) + 1;
  const total = events.length;
  const avg = total / agents.size;
  const workload = [...agents].map(a => {
    const count = agentCounts[a] || 0;
    const status = count > avg * 2 ? "overloaded" : count < avg * 0.25 ? "idle" : "balanced";
    return { agent_id: a, event_count: count, load_fraction: count / total, status };
  });
  const gini = giniCoefficient([...agents].map(a => agentCounts[a] || 0));
  const workloadScore = Math.max(0, 100 * (1 - gini));

  // Delegation
  const delegations = events.filter(e => e.event_type === "delegate" && e.target_agent);
  const completions = new Set(events.filter(e => e.event_type === "complete").map(e => e.agent_id));
  const abandoned = delegations.filter(d => !completions.has(d.target_agent)).length;
  let delegationScore = 100 - abandoned * 15;
  delegationScore = Math.max(0, Math.min(100, delegationScore));

  // Rhythm
  const coordTypes = new Set(["message", "handoff", "delegate"]);
  const workTypes = new Set(["tool_call", "decision", "complete"]);
  const coordCount = events.filter(e => coordTypes.has(e.event_type)).length;
  const workCount = events.filter(e => workTypes.has(e.event_type)).length;
  const coordOverhead = (coordCount + workCount) > 0 ? coordCount / (coordCount + workCount) * 100 : 0;
  const rhythmScore = Math.max(0, Math.min(100, 75 - Math.min(coordOverhead, 80) / 80 * 40));

  // Synergy (simplified)
  let errorCorrections = 0;
  const errorEvents = events.map((e, i) => e.event_type === "error" ? i : -1).filter(i => i >= 0);
  for (const idx of errorEvents) {
    for (let j = idx + 1; j < Math.min(idx + 10, events.length); j++) {
      if (["complete", "decision"].includes(events[j].event_type) && events[j].agent_id !== events[idx].agent_id) {
        errorCorrections++;
        break;
      }
    }
  }
  const ecRate = errorEvents.length > 0 ? errorCorrections / errorEvents.length : 0.5;
  const synergyScore = agents.size >= 2
    ? Math.max(0, Math.min(100, ecRate * 50 + 50))
    : 50;

  // Composite
  const teamworkScore = Math.max(0, Math.min(100,
    handoffScore * 0.20 + bottleneckScore * 0.15 + delegationScore * 0.15 +
    workloadScore * 0.20 + rhythmScore * 0.15 + synergyScore * 0.15
  ));

  const engines = [
    { engine: "Handoff Quality", score: Math.round(handoffScore * 10) / 10 },
    { engine: "Communication Bottleneck", score: Math.round(bottleneckScore * 10) / 10 },
    { engine: "Delegation Chain", score: Math.round(delegationScore * 10) / 10 },
    { engine: "Workload Balance", score: Math.round(workloadScore * 10) / 10 },
    { engine: "Teamwork Rhythm", score: Math.round(rhythmScore * 10) / 10 },
    { engine: "Collective Intelligence", score: Math.round(synergyScore * 10) / 10 },
  ];

  return {
    agent_count: agents.size,
    event_count: events.length,
    teamwork_score: Math.round(teamworkScore * 10) / 10,
    grade: classifyGrade(teamworkScore),
    collaboration_pattern: detectPattern(events, agents),
    gini_coefficient: Math.round(gini * 10000) / 10000,
    coordination_overhead_pct: Math.round(coordOverhead * 10) / 10,
    abandoned_delegations: abandoned,
    handoffs, bottlenecks, workload, engines,
  };
}

// ── Routes ─────────────────────────────────────────────────────────

router.get("/", wrapRoute(async (req, res) => {
  const db = getDb();
  const days = parseDays(req, 30);
  const cutoff = daysAgoCutoff(days);
  const limit = Math.min(parseInt(req.query.limit) || 50, 200);

  // Find sessions with multiple agents
  const rows = db.prepare(`
    SELECT session_id,
           COUNT(DISTINCT COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name)) as agent_count,
           COUNT(*) as event_count,
           MIN(timestamp) as first_event,
           MAX(timestamp) as last_event
    FROM events
    WHERE timestamp >= ?
    GROUP BY session_id
    HAVING agent_count >= 2
    ORDER BY last_event DESC
    LIMIT ?
  `).all(cutoff, limit);

  const results = rows.map(row => {
    const events = db.prepare(`
      SELECT *, COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name) as agent_id,
             JSON_EXTRACT(metadata, '$.target_agent') as target_agent
      FROM events WHERE session_id = ?
      ORDER BY timestamp
    `).all(row.session_id);

    const parsed = events.map(e => ({
      timestamp: e.timestamp,
      agent_id: e.agent_id || e.agent_name || "unknown",
      event_type: e.event_type || e.type || "unknown",
      target_agent: e.target_agent || null,
      metadata: typeof e.metadata === "string" ? JSON.parse(e.metadata || "{}") : (e.metadata || {}),
    }));

    const analysis = analyzeSession(parsed);
    return {
      session_id: row.session_id,
      agent_count: row.agent_count,
      event_count: row.event_count,
      teamwork_score: analysis.teamwork_score,
      grade: analysis.grade,
      collaboration_pattern: analysis.collaboration_pattern,
      first_event: row.first_event,
      last_event: row.last_event,
    };
  });

  res.json(results);
}));

router.get("/:session_id", wrapRoute(async (req, res) => {
  const db = getDb();
  const { session_id } = req.params;

  const events = db.prepare(`
    SELECT *, COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name) as agent_id,
           JSON_EXTRACT(metadata, '$.target_agent') as target_agent
    FROM events WHERE session_id = ?
    ORDER BY timestamp
  `).all(session_id);

  if (events.length === 0) {
    return res.status(404).json({ error: "Session not found" });
  }

  const parsed = events.map(e => ({
    timestamp: e.timestamp,
    agent_id: e.agent_id || e.agent_name || "unknown",
    event_type: e.event_type || e.type || "unknown",
    target_agent: e.target_agent || null,
    metadata: typeof e.metadata === "string" ? JSON.parse(e.metadata || "{}") : (e.metadata || {}),
  }));

  const analysis = analyzeSession(parsed);
  res.json({ session_id, ...analysis });
}));

router.get("/:session_id/handoffs", wrapRoute(async (req, res) => {
  const db = getDb();
  const { session_id } = req.params;

  const events = db.prepare(`
    SELECT *, COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name) as agent_id,
           JSON_EXTRACT(metadata, '$.target_agent') as target_agent
    FROM events WHERE session_id = ?
    ORDER BY timestamp
  `).all(session_id);

  const parsed = events.map(e => ({
    timestamp: e.timestamp,
    agent_id: e.agent_id || e.agent_name || "unknown",
    event_type: e.event_type || e.type || "unknown",
    target_agent: e.target_agent || null,
    metadata: typeof e.metadata === "string" ? JSON.parse(e.metadata || "{}") : (e.metadata || {}),
  }));

  const analysis = analyzeSession(parsed);
  res.json({
    session_id,
    handoff_count: analysis.handoffs.length,
    handoff_quality_score: analysis.engines.find(e => e.engine === "Handoff Quality")?.score || 0,
    handoffs: analysis.handoffs,
  });
}));

router.get("/:session_id/bottlenecks", wrapRoute(async (req, res) => {
  const db = getDb();
  const { session_id } = req.params;

  const events = db.prepare(`
    SELECT *, COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name) as agent_id,
           JSON_EXTRACT(metadata, '$.target_agent') as target_agent
    FROM events WHERE session_id = ?
    ORDER BY timestamp
  `).all(session_id);

  const parsed = events.map(e => ({
    timestamp: e.timestamp,
    agent_id: e.agent_id || e.agent_name || "unknown",
    event_type: e.event_type || e.type || "unknown",
    target_agent: e.target_agent || null,
    metadata: typeof e.metadata === "string" ? JSON.parse(e.metadata || "{}") : (e.metadata || {}),
  }));

  const analysis = analyzeSession(parsed);
  res.json({
    session_id,
    bottleneck_count: analysis.bottlenecks.length,
    bottleneck_score: analysis.engines.find(e => e.engine === "Communication Bottleneck")?.score || 0,
    bottlenecks: analysis.bottlenecks,
  });
}));

router.post("/analyze", wrapRoute(async (req, res) => {
  const { session_id, events: rawEvents } = req.body || {};

  if (rawEvents && Array.isArray(rawEvents)) {
    // Direct event input
    const analysis = analyzeSession(rawEvents);
    res.json({ session_id: session_id || "inline", ...analysis });
    return;
  }

  if (!session_id) {
    return res.status(400).json({ error: "Provide session_id or events array" });
  }

  const db = getDb();
  const events = db.prepare(`
    SELECT *, COALESCE(JSON_EXTRACT(metadata, '$.agent_id'), agent_name) as agent_id,
           JSON_EXTRACT(metadata, '$.target_agent') as target_agent
    FROM events WHERE session_id = ?
    ORDER BY timestamp
  `).all(session_id);

  const parsed = events.map(e => ({
    timestamp: e.timestamp,
    agent_id: e.agent_id || e.agent_name || "unknown",
    event_type: e.event_type || e.type || "unknown",
    target_agent: e.target_agent || null,
    metadata: typeof e.metadata === "string" ? JSON.parse(e.metadata || "{}") : (e.metadata || {}),
  }));

  const analysis = analyzeSession(parsed);
  res.json({ session_id, ...analysis });
}));

module.exports = router;
