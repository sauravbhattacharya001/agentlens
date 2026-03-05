const express = require("express");
const { getDb } = require("../db");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// GET /leaderboard — Rank agents by performance metrics
//
// Query params:
//   sort    — ranking metric: "efficiency" | "speed" | "reliability" | "cost" | "volume" (default: "efficiency")
//   order   — "asc" | "desc" (default depends on metric)
//   days    — lookback window in days (1-365, default: 30)
//   limit   — max agents to return (1-100, default: 20)
//   min_sessions — minimum sessions to qualify (default: 2)
router.get("/", wrapRoute("build agent leaderboard", (req, res) => {
  const db = getDb();

  const sortBy = req.query.sort || "efficiency";
  const validSorts = ["efficiency", "speed", "reliability", "cost", "volume"];
  if (!validSorts.includes(sortBy)) {
    return res.status(400).json({
      error: `Invalid sort. Use one of: ${validSorts.join(", ")}`,
    });
  }

  const days = Math.min(Math.max(1, parseInt(req.query.days) || 30), 365);
  const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 20), 100);
  const minSessions = Math.max(1, parseInt(req.query.min_sessions) || 2);
  const cutoff = new Date(Date.now() - days * 86400000).toISOString();

  const defaultOrders = {
    efficiency: "desc",
    speed: "asc",
    reliability: "desc",
    cost: "asc",
    volume: "desc",
  };
  const order = req.query.order || defaultOrders[sortBy];
  if (order !== "asc" && order !== "desc") {
    return res.status(400).json({ error: "Invalid order. Use 'asc' or 'desc'." });
  }

    // Per-agent session stats
    const agentStats = db
      .prepare(
        `SELECT
          agent_name,
          COUNT(*) as total_sessions,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
          SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
          COALESCE(SUM(total_tokens_in), 0) as tokens_in,
          COALESCE(SUM(total_tokens_out), 0) as tokens_out,
          COALESCE(SUM(total_tokens_in + total_tokens_out), 0) as total_tokens,
          AVG(total_tokens_in + total_tokens_out) as avg_tokens_per_session,
          AVG(
            CASE WHEN ended_at IS NOT NULL
              THEN (julianday(ended_at) - julianday(started_at)) * 86400000
              ELSE NULL
            END
          ) as avg_duration_ms,
          MIN(started_at) as first_seen,
          MAX(started_at) as last_seen
        FROM sessions
        WHERE started_at >= ?
        GROUP BY agent_name
        HAVING COUNT(*) >= ?`
      )
      .all(cutoff, minSessions);

    if (agentStats.length === 0) {
      return res.json({
        period_days: days,
        sort: sortBy,
        order,
        min_sessions: minSessions,
        agents: [],
      });
    }

    const agentNames = agentStats.map((a) => a.agent_name);
    const placeholders = agentNames.map(() => "?").join(", ");

    // Per-agent event stats
    const eventStats = db
      .prepare(
        `SELECT
          s.agent_name,
          COUNT(e.event_id) as total_events,
          AVG(e.duration_ms) as avg_event_duration_ms,
          SUM(CASE WHEN e.tool_call IS NOT NULL THEN 1 ELSE 0 END) as tool_calls,
          SUM(CASE WHEN e.event_type IN ('error', 'agent_error', 'tool_error') THEN 1 ELSE 0 END) as error_events
        FROM events e
        INNER JOIN sessions s ON e.session_id = s.session_id
        WHERE s.started_at >= ?
          AND s.agent_name IN (${placeholders})
          AND e.duration_ms IS NOT NULL
        GROUP BY s.agent_name`
      )
      .all(cutoff, ...agentNames);

    const eventMap = {};
    for (const e of eventStats) {
      eventMap[e.agent_name] = e;
    }

    // Per-agent cost (if pricing configured)
    const costStats = db
      .prepare(
        `SELECT
          s.agent_name,
          SUM(
            COALESCE(e.tokens_in * mp.input_cost_per_1m / 1000000.0, 0) +
            COALESCE(e.tokens_out * mp.output_cost_per_1m / 1000000.0, 0)
          ) as total_cost
        FROM events e
        INNER JOIN sessions s ON e.session_id = s.session_id
        LEFT JOIN model_pricing mp ON e.model = mp.model
        WHERE s.started_at >= ?
          AND s.agent_name IN (${placeholders})
        GROUP BY s.agent_name`
      )
      .all(cutoff, ...agentNames);

    const costMap = {};
    for (const c of costStats) {
      costMap[c.agent_name] = c.total_cost || 0;
    }

    // Build leaderboard
    const agents = agentStats.map((a) => {
      const ev = eventMap[a.agent_name] || {
        total_events: 0,
        avg_event_duration_ms: 0,
        tool_calls: 0,
        error_events: 0,
      };

      const errorRate =
        a.total_sessions > 0
          ? Math.round((a.errors / a.total_sessions) * 10000) / 100
          : 0;
      const successRate =
        a.total_sessions > 0
          ? Math.round((a.completed / a.total_sessions) * 10000) / 100
          : 0;

      const cost = Math.round((costMap[a.agent_name] || 0) * 10000) / 10000;
      const costPerSession =
        a.total_sessions > 0
          ? Math.round((cost / a.total_sessions) * 10000) / 10000
          : 0;

      const efficiency =
        a.tokens_in > 0
          ? Math.round((a.tokens_out / a.tokens_in) * 1000) / 1000
          : 0;

      const tokensPerMs =
        a.avg_duration_ms > 0
          ? Math.round(((a.avg_tokens_per_session || 0) / a.avg_duration_ms) * 1000) / 1000
          : 0;

      return {
        agent_name: a.agent_name,
        total_sessions: a.total_sessions,
        completed: a.completed,
        errors: a.errors,
        success_rate: successRate,
        error_rate: errorRate,
        total_tokens: a.total_tokens,
        tokens_in: a.tokens_in,
        tokens_out: a.tokens_out,
        avg_tokens_per_session: Math.round(a.avg_tokens_per_session || 0),
        avg_session_duration_ms: Math.round(a.avg_duration_ms || 0),
        avg_event_duration_ms:
          Math.round((ev.avg_event_duration_ms || 0) * 100) / 100,
        total_events: ev.total_events,
        tool_calls: ev.tool_calls,
        error_events: ev.error_events,
        efficiency_ratio: efficiency,
        tokens_per_ms: tokensPerMs,
        total_cost_usd: cost,
        cost_per_session_usd: costPerSession,
        first_seen: a.first_seen,
        last_seen: a.last_seen,
      };
    });

    // Sort
    const sortKeys = {
      efficiency: "efficiency_ratio",
      speed: "avg_session_duration_ms",
      reliability: "success_rate",
      cost: "cost_per_session_usd",
      volume: "total_sessions",
    };

    const key = sortKeys[sortBy];
    agents.sort((a, b) =>
      order === "asc" ? a[key] - b[key] : b[key] - a[key]
    );

    const ranked = agents.slice(0, limit).map((a, i) => ({
      rank: i + 1,
      ...a,
    }));

    res.json({
      period_days: days,
      sort: sortBy,
      order,
      min_sessions: minSessions,
      total_qualifying_agents: agents.length,
      agents: ranked,
    });
}));

module.exports = router;
