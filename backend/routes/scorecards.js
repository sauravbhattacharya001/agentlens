const express = require("express");
const { wrapRoute, parseDays, daysAgoCutoff } = require("../lib/request-helpers");
const { createLazyStatements } = require("../lib/lazy-statements");

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

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function round2(v) { return Math.round(v * 100) / 100; }

// ── Cached prepared statements for scorecards ───────────────────────
// Uses createLazyStatements for consistent lazy-init pattern across
// all route files (same approach as analytics.js, sessions.js, etc.).
const getScorecardStatements = createLazyStatements((db) => ({
    agentStats: db.prepare(`
      SELECT
        agent_name,
        COUNT(*) as total_sessions,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
        COALESCE(AVG(total_tokens_in + total_tokens_out), 0) as avg_tokens,
        COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
        MIN(started_at) as first_seen,
        MAX(started_at) as last_seen
      FROM sessions
      WHERE started_at >= ?
      GROUP BY agent_name
      ORDER BY total_sessions DESC
    `),
    agentLatency: db.prepare(`
      SELECT
        s.agent_name,
        AVG(e.duration_ms) as avg_latency,
        MAX(e.duration_ms) as max_latency
      FROM events e
      JOIN sessions s ON e.session_id = s.session_id
      WHERE s.started_at >= ? AND e.duration_ms IS NOT NULL
      GROUP BY s.agent_name
    `),
    weeklyTrend: db.prepare(`
      SELECT
        agent_name,
        strftime('%Y-%W', started_at) as week,
        COUNT(*) as sessions,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
      FROM sessions
      WHERE started_at >= ?
      GROUP BY agent_name, week
      ORDER BY agent_name, week
    `),
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
    singleAgentTools: db.prepare(`
      SELECT
        e.event_type as tool,
        COUNT(*) as calls,
        AVG(e.duration_ms) as avg_latency_ms
      FROM events e
      JOIN sessions s ON e.session_id = s.session_id
      WHERE s.agent_name = ? AND s.started_at >= ? AND e.event_type = 'tool_call'
      GROUP BY e.event_type
      ORDER BY calls DESC
      LIMIT 20
    `),
    singleAgentDaily: db.prepare(`
      SELECT
        date(started_at) as day,
        COUNT(*) as sessions,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
        COALESCE(AVG(total_tokens_in + total_tokens_out), 0) as avg_tokens
      FROM sessions
      WHERE agent_name = ? AND started_at >= ?
      GROUP BY day
      ORDER BY day
    `),
}));

// ── GET /scorecards ─────────────────────────────────────────────────
// Returns per-agent scorecards with composite score, letter grade,
// and metric breakdowns.

router.get("/", wrapRoute("list scorecards", async (req, res) => {
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);

  const stmts = getScorecardStatements();

  // Aggregate per-agent stats
  const agents = stmts.agentStats.all(cutoff);

  // Get per-agent latency from events
  const latencyRows = stmts.agentLatency.all(cutoff);

  const latencyMap = {};
  for (const r of latencyRows) {
    latencyMap[r.agent_name] = { avg: r.avg_latency, max: r.max_latency };
  }

  // Weekly trend (last 8 weeks) per agent for sparklines
  const trendRows = stmts.weeklyTrend.all(daysAgoCutoff(56));

  const trendMap = {};
  for (const r of trendRows) {
    if (!trendMap[r.agent_name]) trendMap[r.agent_name] = [];
    trendMap[r.agent_name].push({
      week: r.week,
      sessions: r.sessions,
      errorRate: r.sessions > 0 ? round2((r.errors / r.sessions) * 100) : 0,
    });
  }

  const scorecards = agents.map(a => {
    const successRate = a.total_sessions > 0
      ? ((a.completed / a.total_sessions) * 100) : 100;
    const errorRate = a.total_sessions > 0
      ? ((a.errors / a.total_sessions) * 100) : 0;
    const lat = latencyMap[a.agent_name] || { avg: 0, max: 0 };

    // Composite score: 40% success rate + 30% latency efficiency + 30% volume
    const successScore = clamp(successRate, 0, 100);
    // Latency score: <500ms=100, >5000ms=0 (linear)
    const latencyScore = lat.avg > 0
      ? clamp(100 - ((lat.avg - 500) / 45), 0, 100)
      : 80; // no data = neutral
    // Volume score: more sessions = higher confidence (log scale)
    const volumeScore = clamp(Math.log10(a.total_sessions + 1) * 40, 0, 100);

    const composite = round2(successScore * 0.4 + latencyScore * 0.3 + volumeScore * 0.3);
    const grade = letterGrade(composite);

    return {
      agent_name: a.agent_name,
      composite_score: composite,
      grade,
      grade_color: gradeColor(grade),
      metrics: {
        total_sessions: a.total_sessions,
        completed: a.completed,
        errors: a.errors,
        success_rate: round2(successRate),
        error_rate: round2(errorRate),
        avg_tokens: Math.round(a.avg_tokens),
        total_tokens: a.total_tokens,
        avg_latency_ms: round2(lat.avg),
        max_latency_ms: round2(lat.max),
      },
      first_seen: a.first_seen,
      last_seen: a.last_seen,
      trend: trendMap[a.agent_name] || [],
    };
  });

  // Sort by composite score descending
  scorecards.sort((a, b) => b.composite_score - a.composite_score);

  res.json({
    scorecards,
    meta: { days, generated_at: new Date().toISOString(), agent_count: scorecards.length },
  });
}));

// ── GET /scorecards/:agent ──────────────────────────────────────────
// Detailed scorecard for a single agent

router.get("/:agent", wrapRoute("get agent scorecard", async (req, res) => {
  const agent = req.params.agent;
  const days = parseDays(req.query.days);
  const cutoff = daysAgoCutoff(days);

  const stmts = getScorecardStatements();

  const stats = stmts.singleAgentStats.get(agent, cutoff);

  if (!stats || stats.total_sessions === 0) {
    return res.status(404).json({ error: `No data for agent "${agent}" in the last ${days} days` });
  }

  // Model usage breakdown
  const models = stmts.singleAgentModels.all(agent, cutoff);

  // Tool usage
  const tools = stmts.singleAgentTools.all(agent, cutoff);

  // Daily trend
  const daily = stmts.singleAgentDaily.all(agent, cutoff);

  const successRate = (stats.completed / stats.total_sessions) * 100;
  const errorRate = (stats.errors / stats.total_sessions) * 100;

  res.json({
    agent_name: agent,
    days,
    metrics: {
      total_sessions: stats.total_sessions,
      completed: stats.completed,
      errors: stats.errors,
      active: stats.active,
      success_rate: round2(successRate),
      error_rate: round2(errorRate),
      avg_tokens_in: Math.round(stats.avg_tokens_in),
      avg_tokens_out: Math.round(stats.avg_tokens_out),
      total_tokens: stats.total_tokens,
    },
    models,
    tools,
    daily_trend: daily,
    first_seen: stats.first_seen,
    last_seen: stats.last_seen,
  });
}));

module.exports = router;
