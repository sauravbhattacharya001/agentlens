/**
 * Agent Competency Map — Autonomous skill profiling for AI agents.
 *
 * Analyzes each agent's behavioral data across 6 dimensions to build
 * skill profiles, identify strengths/weaknesses, and generate optimal
 * task-routing recommendations.
 *
 * Dimensions:
 *   - Reliability: success rate weighted by volume
 *   - Speed: latency efficiency (normalized inverse of avg latency)
 *   - Efficiency: token efficiency (completed sessions per token spent)
 *   - Tool Mastery: breadth and success rate of tool usage
 *   - Error Recovery: ratio of error→completed vs total errors
 *   - Consistency: inverse coefficient of variation of daily success rates
 */

const express = require("express");
const { wrapRoute, parseDays, daysAgoCutoff } = require("../lib/request-helpers");
const { createLazyStatements } = require("../lib/lazy-statements");
const { round2, clamp } = require("../lib/stats");

const router = express.Router();

// ── Helpers ─────────────────────────────────────────────────────────

function letterGrade(score) {
  if (score >= 95) return "A+";
  if (score >= 90) return "A";
  if (score >= 85) return "A-";
  if (score >= 80) return "B+";
  if (score >= 75) return "B";
  if (score >= 70) return "B-";
  if (score >= 65) return "C+";
  if (score >= 60) return "C";
  if (score >= 55) return "C-";
  if (score >= 50) return "D";
  return "F";
}

function gradeColor(grade) {
  if (grade.startsWith("A")) return "#22c55e";
  if (grade.startsWith("B")) return "#3b82f6";
  if (grade.startsWith("C")) return "#eab308";
  if (grade.startsWith("D")) return "#f97316";
  return "#ef4444";
}

/**
 * Compute percentile rank of a value within a sorted ascending array.
 */
function percentileRank(sorted, value) {
  if (sorted.length === 0) return 50;
  let below = 0;
  for (const v of sorted) {
    if (v < value) below++;
  }
  return round2((below / sorted.length) * 100);
}

/**
 * Simple linear regression slope for (x, y) pairs.
 */
function linearSlope(points) {
  const n = points.length;
  if (n < 2) return 0;
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) {
    const x = i;
    const y = points[i];
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumX2 += x * x;
  }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) return 0;
  return round2((n * sumXY - sumX * sumY) / denom);
}

// ── Task-type inference from dimension profiles ─────────────────────

const TASK_PROFILES = [
  {
    type: "long-running critical workflows",
    requires: { reliability: 80, consistency: 75 },
    description: "High reliability + consistency → trusted for critical paths",
  },
  {
    type: "high-throughput batch processing",
    requires: { speed: 80, efficiency: 75 },
    description: "Fast + token-efficient → ideal for volume work",
  },
  {
    type: "complex multi-tool orchestrations",
    requires: { tool_mastery: 75, reliability: 70 },
    description: "Broad tool usage + reliable outcomes → complex workflows",
  },
  {
    type: "error-prone exploratory tasks",
    requires: { error_recovery: 75 },
    description: "Strong recovery → handles failures gracefully",
  },
  {
    type: "token-sensitive operations",
    requires: { efficiency: 85 },
    description: "Very token-efficient → cost-conscious workloads",
  },
  {
    type: "latency-critical real-time tasks",
    requires: { speed: 85, reliability: 70 },
    description: "Fast + reliable → real-time responsiveness",
  },
  {
    type: "steady-state monitoring",
    requires: { consistency: 80, reliability: 70 },
    description: "Consistent + reliable → predictable long-term monitoring",
  },
];

function inferRecommendedTasks(dimensions) {
  const tasks = [];
  for (const profile of TASK_PROFILES) {
    let match = true;
    for (const [dim, minScore] of Object.entries(profile.requires)) {
      if (!dimensions[dim] || dimensions[dim].score < minScore) {
        match = false;
        break;
      }
    }
    if (match) tasks.push(profile.type);
  }
  // Fallback: if no profiles match, suggest based on top dimension
  if (tasks.length === 0) {
    const top = Object.entries(dimensions)
      .sort((a, b) => b[1].score - a[1].score)[0];
    if (top) tasks.push(`general tasks (strongest: ${top[0]})`);
  }
  return tasks;
}

// ── Cached prepared statements ──────────────────────────────────────

const getCompetencyStatements = createLazyStatements((db) => ({
  agentOverview: db.prepare(`
    SELECT
      agent_name,
      COUNT(*) as total_sessions,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
      COALESCE(AVG(total_tokens_in + total_tokens_out), 0) as avg_tokens,
      COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
      MAX(started_at) as last_active
    FROM sessions
    WHERE started_at >= ?
    GROUP BY agent_name
    ORDER BY total_sessions DESC
  `),

  agentLatency: db.prepare(`
    SELECT
      s.agent_name,
      AVG(e.duration_ms) as avg_latency,
      MIN(e.duration_ms) as min_latency,
      MAX(e.duration_ms) as max_latency
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE s.started_at >= ? AND e.duration_ms IS NOT NULL AND e.duration_ms > 0
    GROUP BY s.agent_name
  `),

  agentToolStats: db.prepare(`
    SELECT
      s.agent_name,
      e.tool_call,
      COUNT(*) as calls,
      AVG(e.duration_ms) as avg_latency_ms
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE s.started_at >= ? AND e.tool_call IS NOT NULL AND e.tool_call != ''
    GROUP BY s.agent_name, e.tool_call
  `),

  agentDailySuccess: db.prepare(`
    SELECT
      agent_name,
      date(started_at) as day,
      COUNT(*) as sessions,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
    FROM sessions
    WHERE started_at >= ?
    GROUP BY agent_name, day
    ORDER BY agent_name, day
  `),

  // Sessions that had at least one error event but still completed
  agentErrorRecovery: db.prepare(`
    SELECT
      s.agent_name,
      COUNT(DISTINCT s.session_id) as recovered
    FROM sessions s
    WHERE s.started_at >= ?
      AND s.status = 'completed'
      AND EXISTS (
        SELECT 1 FROM events e
        WHERE e.session_id = s.session_id AND e.event_type = 'error'
      )
    GROUP BY s.agent_name
  `),

  // Single agent detailed
  singleAgentStats: db.prepare(`
    SELECT
      COUNT(*) as total_sessions,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
      COALESCE(AVG(total_tokens_in), 0) as avg_tokens_in,
      COALESCE(AVG(total_tokens_out), 0) as avg_tokens_out,
      COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
      MIN(started_at) as first_seen,
      MAX(started_at) as last_seen
    FROM sessions
    WHERE agent_name = ? AND started_at >= ?
  `),

  singleAgentTools: db.prepare(`
    SELECT
      e.tool_call as tool,
      COUNT(*) as calls,
      AVG(e.duration_ms) as avg_latency_ms,
      MIN(e.duration_ms) as min_latency_ms,
      MAX(e.duration_ms) as max_latency_ms
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE s.agent_name = ? AND s.started_at >= ?
      AND e.tool_call IS NOT NULL AND e.tool_call != ''
    GROUP BY e.tool_call
    ORDER BY calls DESC
  `),

  singleAgentModels: db.prepare(`
    SELECT
      e.model,
      COUNT(*) as calls,
      COALESCE(SUM(e.tokens_in), 0) as tokens_in,
      COALESCE(SUM(e.tokens_out), 0) as tokens_out,
      AVG(e.duration_ms) as avg_latency_ms
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE s.agent_name = ? AND s.started_at >= ? AND e.model IS NOT NULL
    GROUP BY e.model
    ORDER BY calls DESC
  `),

  singleAgentWeekly: db.prepare(`
    SELECT
      strftime('%Y-%W', started_at) as week,
      COUNT(*) as sessions,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
      COALESCE(AVG(total_tokens_in + total_tokens_out), 0) as avg_tokens
    FROM sessions
    WHERE agent_name = ? AND started_at >= ?
    GROUP BY week
    ORDER BY week
  `),

  singleAgentDailySuccess: db.prepare(`
    SELECT
      date(started_at) as day,
      COUNT(*) as sessions,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
    FROM sessions
    WHERE agent_name = ? AND started_at >= ?
    GROUP BY day
    ORDER BY day
  `),

  singleAgentErrorRecovery: db.prepare(`
    SELECT COUNT(DISTINCT s.session_id) as recovered
    FROM sessions s
    WHERE s.agent_name = ? AND s.started_at >= ?
      AND s.status = 'completed'
      AND EXISTS (
        SELECT 1 FROM events e
        WHERE e.session_id = s.session_id AND e.event_type = 'error'
      )
  `),
}));

// ── Dimension computation ───────────────────────────────────────────

function computeDimensions(agent, latencyMap, toolMap, dailyMap, recoveryMap, allAgents) {
  const name = agent.agent_name;
  const total = agent.total_sessions;

  // 1. Reliability: success rate, volume-adjusted
  const rawReliability = total > 0 ? (agent.completed / total) * 100 : 0;
  // Volume confidence boost: small sample sizes get penalized
  const volumeFactor = clamp(Math.log10(total + 1) / 2, 0, 1);
  const reliability = round2(rawReliability * (0.7 + 0.3 * volumeFactor));

  // 2. Speed: inverse latency, normalized to 0-100
  const lat = latencyMap[name] || {};
  const avgLatency = lat.avg_latency || 0;
  // <200ms = 100, >10000ms = 0 (log scale)
  const speed = avgLatency > 0
    ? round2(clamp(100 - (Math.log10(avgLatency / 200) * 50), 0, 100))
    : 50; // no data = neutral

  // 3. Efficiency: completed sessions per 1M tokens
  const totalTokens = agent.total_tokens || 1;
  const completedPer1M = (agent.completed / totalTokens) * 1000000;
  // Normalize: 100+ per 1M tokens = 100, 0 = 0
  const efficiency = round2(clamp(completedPer1M, 0, 100));

  // 4. Tool Mastery: unique tools × usage breadth
  const tools = toolMap[name] || [];
  const uniqueTools = tools.length;
  const totalToolCalls = tools.reduce((s, t) => s + t.calls, 0);
  // Breadth score: more unique tools = higher (log scale, cap at 10+)
  const breadthScore = clamp(Math.log2(uniqueTools + 1) * 30, 0, 100);
  // Volume score: more tool calls = more experienced
  const toolVolumeScore = clamp(Math.log10(totalToolCalls + 1) * 35, 0, 100);
  const toolMastery = round2((breadthScore + toolVolumeScore) / 2);

  // 5. Error Recovery: sessions with errors that still completed
  const recovered = recoveryMap[name] || 0;
  const totalErrors = agent.errors || 0;
  const errorRecovery = totalErrors > 0
    ? round2(clamp((recovered / totalErrors) * 100, 0, 100))
    : 80; // no errors = good default

  // 6. Consistency: inverse CV of daily success rates
  const dailyRates = (dailyMap[name] || []).map(d =>
    d.sessions > 0 ? (d.completed / d.sessions) * 100 : 0
  );
  let consistency = 70; // default
  if (dailyRates.length >= 3) {
    const mean = dailyRates.reduce((s, v) => s + v, 0) / dailyRates.length;
    if (mean > 0) {
      const variance = dailyRates.reduce((s, v) => s + (v - mean) ** 2, 0) / dailyRates.length;
      const cv = Math.sqrt(variance) / mean;
      // CV of 0 = 100 (perfectly consistent), CV of 1+ = 0
      consistency = round2(clamp(100 - cv * 100, 0, 100));
    }
  }

  return { reliability, speed, efficiency, tool_mastery: toolMastery, error_recovery: errorRecovery, consistency };
}

function computeCompetencyScore(dims) {
  return round2(
    dims.reliability * 0.25 +
    dims.speed * 0.15 +
    dims.efficiency * 0.20 +
    dims.tool_mastery * 0.15 +
    dims.error_recovery * 0.10 +
    dims.consistency * 0.15
  );
}

function identifyStrengthsWeaknesses(dims) {
  const sorted = Object.entries(dims).sort((a, b) => b[1] - a[1]);
  return {
    strengths: sorted.slice(0, 2).map(([k]) => k),
    weaknesses: sorted.slice(-2).map(([k]) => k),
  };
}

// ── GET / — All agents competency overview ──────────────────────────

router.get("/", wrapRoute("list competency map", (req, res) => {
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);
  const stmts = getCompetencyStatements();

  const agents = stmts.agentOverview.all(cutoff);

  if (agents.length === 0) {
    return res.json({
      competency_map: [],
      routing_suggestions: [],
      meta: { days, generated_at: new Date().toISOString(), agent_count: 0 },
    });
  }

  // Build lookup maps
  const latencyRows = stmts.agentLatency.all(cutoff);
  const latencyMap = {};
  for (const r of latencyRows) latencyMap[r.agent_name] = r;

  const toolRows = stmts.agentToolStats.all(cutoff);
  const toolMap = {};
  for (const r of toolRows) {
    if (!toolMap[r.agent_name]) toolMap[r.agent_name] = [];
    toolMap[r.agent_name].push(r);
  }

  const dailyRows = stmts.agentDailySuccess.all(cutoff);
  const dailyMap = {};
  for (const r of dailyRows) {
    if (!dailyMap[r.agent_name]) dailyMap[r.agent_name] = [];
    dailyMap[r.agent_name].push(r);
  }

  const recoveryRows = stmts.agentErrorRecovery.all(cutoff);
  const recoveryMap = {};
  for (const r of recoveryRows) recoveryMap[r.agent_name] = r.recovered;

  // Compute dimensions for all agents
  const allDimensions = {};
  for (const agent of agents) {
    allDimensions[agent.agent_name] = computeDimensions(
      agent, latencyMap, toolMap, dailyMap, recoveryMap, agents
    );
  }

  // Build percentile arrays for each dimension
  const dimNames = ["reliability", "speed", "efficiency", "tool_mastery", "error_recovery", "consistency"];
  const dimSorted = {};
  for (const dim of dimNames) {
    dimSorted[dim] = Object.values(allDimensions)
      .map(d => d[dim])
      .sort((a, b) => a - b);
  }

  // Build competency map
  const competencyMap = agents.map(agent => {
    const dims = allDimensions[agent.agent_name];
    const score = computeCompetencyScore(dims);
    const grade = letterGrade(score);
    const { strengths, weaknesses } = identifyStrengthsWeaknesses(dims);

    const dimensions = {};
    for (const dim of dimNames) {
      dimensions[dim] = {
        score: dims[dim],
        percentile: percentileRank(dimSorted[dim], dims[dim]),
      };
    }

    const recommendedTasks = inferRecommendedTasks(dimensions);

    return {
      agent_name: agent.agent_name,
      competency_score: score,
      grade,
      grade_color: gradeColor(grade),
      dimensions,
      strengths,
      weaknesses,
      recommended_tasks: recommendedTasks,
      session_count: agent.total_sessions,
      last_active: agent.last_active,
    };
  });

  competencyMap.sort((a, b) => b.competency_score - a.competency_score);

  // Generate routing suggestions
  const taskAgentMap = {};
  for (const entry of competencyMap) {
    for (const task of entry.recommended_tasks) {
      if (!taskAgentMap[task]) taskAgentMap[task] = [];
      taskAgentMap[task].push({
        agent: entry.agent_name,
        score: entry.competency_score,
        dimensions: entry.dimensions,
      });
    }
  }

  const routingSuggestions = Object.entries(taskAgentMap).map(([taskType, candidates]) => {
    candidates.sort((a, b) => b.score - a.score);
    const best = candidates[0];
    const topDims = Object.entries(best.dimensions)
      .sort((a, b) => b[1].score - a[1].score)
      .slice(0, 2)
      .map(([k, v]) => `${k} (${v.score})`)
      .join(" and ");

    return {
      task_type: taskType,
      best_agent: best.agent,
      confidence: round2(clamp(best.score, 0, 100)),
      reason: `Highest ${topDims}`,
      alternatives: candidates.slice(1, 3).map(c => c.agent),
    };
  });

  routingSuggestions.sort((a, b) => b.confidence - a.confidence);

  res.json({
    competency_map: competencyMap,
    routing_suggestions: routingSuggestions,
    meta: { days, generated_at: new Date().toISOString(), agent_count: competencyMap.length },
  });
}));

// ── GET /routing — Autonomous routing recommendations ───────────────

router.get("/routing", wrapRoute("get routing recommendations", (req, res) => {
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);
  const stmts = getCompetencyStatements();

  const agents = stmts.agentOverview.all(cutoff);

  if (agents.length === 0) {
    return res.json({
      routing_table: [],
      coverage_score: 0,
      meta: { days, generated_at: new Date().toISOString() },
    });
  }

  const latencyMap = {};
  for (const r of stmts.agentLatency.all(cutoff)) latencyMap[r.agent_name] = r;
  const toolMap = {};
  for (const r of stmts.agentToolStats.all(cutoff)) {
    if (!toolMap[r.agent_name]) toolMap[r.agent_name] = [];
    toolMap[r.agent_name].push(r);
  }
  const dailyMap = {};
  for (const r of stmts.agentDailySuccess.all(cutoff)) {
    if (!dailyMap[r.agent_name]) dailyMap[r.agent_name] = [];
    dailyMap[r.agent_name].push(r);
  }
  const recoveryMap = {};
  for (const r of stmts.agentErrorRecovery.all(cutoff)) recoveryMap[r.agent_name] = r.recovered;

  // Compute all dimensions
  const profiles = agents.map(agent => {
    const dims = computeDimensions(agent, latencyMap, toolMap, dailyMap, recoveryMap, agents);
    const dimWithPercentile = {};
    for (const [k, v] of Object.entries(dims)) {
      dimWithPercentile[k] = { score: v, percentile: 50 }; // simplified for routing
    }
    return {
      agent_name: agent.agent_name,
      score: computeCompetencyScore(dims),
      dimensions: dimWithPercentile,
      recommended_tasks: inferRecommendedTasks(dimWithPercentile),
    };
  });

  // Build routing table
  const taskBestAgent = {};
  for (const p of profiles) {
    for (const task of p.recommended_tasks) {
      if (!taskBestAgent[task] || taskBestAgent[task].score < p.score) {
        taskBestAgent[task] = { agent: p.agent_name, score: p.score };
      }
    }
  }

  const routingTable = Object.entries(taskBestAgent).map(([taskPattern, best]) => {
    const fallbacks = profiles
      .filter(p => p.agent_name !== best.agent && p.recommended_tasks.includes(taskPattern))
      .sort((a, b) => b.score - a.score)
      .slice(0, 2)
      .map(p => p.agent_name);

    return {
      task_pattern: taskPattern,
      recommended_agent: best.agent,
      confidence: round2(clamp(best.score, 0, 100)),
      fallback_agents: fallbacks,
      reason: `Highest competency score (${best.score}) for this task type`,
    };
  });

  routingTable.sort((a, b) => b.confidence - a.confidence);

  // Coverage: what % of task types have a clearly superior agent (>10pt gap over #2)
  const totalTypes = TASK_PROFILES.length;
  let coveredTypes = 0;
  for (const profile of TASK_PROFILES) {
    const matching = profiles.filter(p => p.recommended_tasks.includes(profile.type));
    if (matching.length >= 1) {
      if (matching.length === 1 || matching[0].score - matching[1].score > 10) {
        coveredTypes++;
      }
    }
  }
  const coverageScore = round2((coveredTypes / totalTypes) * 100);

  res.json({
    routing_table: routingTable,
    coverage_score: coverageScore,
    meta: { days, generated_at: new Date().toISOString(), agent_count: profiles.length },
  });
}));

// ── GET /:agent — Detailed competency profile ───────────────────────

router.get("/:agent", wrapRoute("get agent competency", (req, res) => {
  const agent = req.params.agent;
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);
  const stmts = getCompetencyStatements();

  const stats = stmts.singleAgentStats.get(agent, cutoff);
  if (!stats || stats.total_sessions === 0) {
    return res.status(404).json({ error: `No data for agent "${agent}" in the last ${days} days` });
  }

  // Tools breakdown
  const tools = stmts.singleAgentTools.all(agent, cutoff);

  // Model affinity
  const models = stmts.singleAgentModels.all(agent, cutoff);

  // Weekly trend for growth trajectory
  const weeklyData = stmts.singleAgentWeekly.all(agent, daysAgoCutoff(Math.min(days, 90)));

  // Daily success for consistency
  const dailyData = stmts.singleAgentDailySuccess.all(agent, cutoff);

  // Error recovery
  const recovery = stmts.singleAgentErrorRecovery.get(agent, cutoff);
  const recovered = recovery ? recovery.recovered : 0;

  // Compute dimensions
  const agentObj = {
    agent_name: agent,
    total_sessions: stats.total_sessions,
    completed: stats.completed,
    errors: stats.errors,
    total_tokens: stats.total_tokens,
    avg_tokens: (stats.avg_tokens_in || 0) + (stats.avg_tokens_out || 0),
  };

  // Get latency for this agent
  const latencyMap = {};
  const allLatency = stmts.agentLatency.all(cutoff);
  for (const r of allLatency) latencyMap[r.agent_name] = r;

  const toolMap = { [agent]: tools.map(t => ({ calls: t.calls })) };
  const dailyMap = { [agent]: dailyData };
  const recoveryMap = { [agent]: recovered };

  const dims = computeDimensions(agentObj, latencyMap, toolMap, dailyMap, recoveryMap, [agentObj]);
  const score = computeCompetencyScore(dims);
  const grade = letterGrade(score);
  const { strengths, weaknesses } = identifyStrengthsWeaknesses(dims);

  // Growth trajectory: weekly success rate slopes
  const weeklySuccessRates = weeklyData.map(w =>
    w.sessions > 0 ? (w.completed / w.sessions) * 100 : 0
  );
  const weeklyTokens = weeklyData.map(w => w.avg_tokens);

  const growthTrajectory = {
    success_rate_trend: linearSlope(weeklySuccessRates),
    token_usage_trend: linearSlope(weeklyTokens),
    direction: linearSlope(weeklySuccessRates) > 0.5 ? "improving"
      : linearSlope(weeklySuccessRates) < -0.5 ? "declining" : "stable",
    weeks_analyzed: weeklyData.length,
  };

  // Peer comparison: get all agents and rank
  const allAgents = stmts.agentOverview.all(cutoff);
  const allToolRows = stmts.agentToolStats.all(cutoff);
  const allToolMap = {};
  for (const r of allToolRows) {
    if (!allToolMap[r.agent_name]) allToolMap[r.agent_name] = [];
    allToolMap[r.agent_name].push(r);
  }
  const allDailyRows = stmts.agentDailySuccess.all(cutoff);
  const allDailyMap = {};
  for (const r of allDailyRows) {
    if (!allDailyMap[r.agent_name]) allDailyMap[r.agent_name] = [];
    allDailyMap[r.agent_name].push(r);
  }
  const allRecoveryRows = stmts.agentErrorRecovery.all(cutoff);
  const allRecoveryMap = {};
  for (const r of allRecoveryRows) allRecoveryMap[r.agent_name] = r.recovered;

  const dimNames = ["reliability", "speed", "efficiency", "tool_mastery", "error_recovery", "consistency"];
  const peerRanks = {};
  for (const dim of dimNames) {
    const allScores = allAgents.map(a => {
      const d = computeDimensions(a, latencyMap, allToolMap, allDailyMap, allRecoveryMap, allAgents);
      return { agent: a.agent_name, score: d[dim] };
    }).sort((a, b) => b.score - a.score);

    const rank = allScores.findIndex(s => s.agent === agent) + 1;
    peerRanks[dim] = { rank, of: allScores.length, score: dims[dim] };
  }

  // Model affinity: which model has highest success-proxy (most calls with low latency)
  const modelAffinity = models.map(m => ({
    model: m.model,
    calls: m.calls,
    tokens_in: m.tokens_in,
    tokens_out: m.tokens_out,
    avg_latency_ms: round2(m.avg_latency_ms || 0),
    efficiency_score: m.calls > 0 && m.avg_latency_ms > 0
      ? round2(clamp(100 - Math.log10(m.avg_latency_ms / 200) * 50, 0, 100))
      : 50,
  })).sort((a, b) => b.efficiency_score - a.efficiency_score);

  const dimensions = {};
  for (const dim of dimNames) {
    dimensions[dim] = { score: dims[dim], peer_rank: peerRanks[dim] };
  }

  const recommendedTasks = inferRecommendedTasks(
    Object.fromEntries(dimNames.map(d => [d, { score: dims[d] }]))
  );

  res.json({
    agent_name: agent,
    competency_score: score,
    grade,
    grade_color: gradeColor(grade),
    dimensions,
    strengths,
    weaknesses,
    recommended_tasks: recommendedTasks,
    growth_trajectory: growthTrajectory,
    tools: tools.map(t => ({
      tool: t.tool,
      calls: t.calls,
      avg_latency_ms: round2(t.avg_latency_ms || 0),
      min_latency_ms: round2(t.min_latency_ms || 0),
      max_latency_ms: round2(t.max_latency_ms || 0),
    })),
    model_affinity: modelAffinity,
    peer_comparison: peerRanks,
    weekly_trend: weeklyData.map(w => ({
      week: w.week,
      sessions: w.sessions,
      success_rate: w.sessions > 0 ? round2((w.completed / w.sessions) * 100) : 0,
      avg_tokens: Math.round(w.avg_tokens),
    })),
    metrics: {
      total_sessions: stats.total_sessions,
      completed: stats.completed,
      errors: stats.errors,
      active: stats.active,
      avg_tokens_in: Math.round(stats.avg_tokens_in),
      avg_tokens_out: Math.round(stats.avg_tokens_out),
      total_tokens: stats.total_tokens,
    },
    first_seen: stats.first_seen,
    last_seen: stats.last_seen,
    meta: { days, generated_at: new Date().toISOString() },
  });
}));

module.exports = router;
