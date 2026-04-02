const express = require("express");
const router = express.Router();
const { getDb } = require("../db");

// GET /heatmap/data?days=90&agent=...
// Returns daily aggregated health metrics for a calendar heatmap
router.get("/data", (req, res) => {
  const db = getDb();
  const days = Math.min(parseInt(req.query.days) || 90, 365);
  const agent = req.query.agent || null;

  const since = new Date();
  since.setDate(since.getDate() - days);
  const sinceISO = since.toISOString();

  // Aggregate daily metrics from events table
  const query = `
    SELECT
      DATE(timestamp) as day,
      COUNT(*) as total_events,
      SUM(CASE WHEN status = 'error' OR level = 'error' THEN 1 ELSE 0 END) as errors,
      SUM(CASE WHEN status = 'success' OR level = 'info' THEN 1 ELSE 0 END) as successes,
      AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms ELSE NULL END) as avg_latency_ms,
      COUNT(DISTINCT session_id) as unique_sessions,
      SUM(CASE WHEN total_tokens IS NOT NULL THEN total_tokens ELSE 0 END) as total_tokens
    FROM events
    WHERE timestamp >= ?
    ${agent ? "AND (agent_id = ? OR agent_name = ?)" : ""}
    GROUP BY DATE(timestamp)
    ORDER BY day ASC
  `;

  const params = agent ? [sinceISO, agent, agent] : [sinceISO];

  try {
    const rows = db.prepare(query).all(...params);

    // Calculate health score per day (0-100)
    const data = rows.map((row) => {
      const errorRate = row.total_events > 0 ? row.errors / row.total_events : 0;
      // Health = 100 - (error_rate * 80) - latency_penalty
      const latencyPenalty = row.avg_latency_ms
        ? Math.min((row.avg_latency_ms / 10000) * 20, 20)
        : 0;
      const health = Math.max(0, Math.round(100 - errorRate * 80 - latencyPenalty));

      return {
        date: row.day,
        health,
        totalEvents: row.total_events,
        errors: row.errors,
        successes: row.successes,
        avgLatencyMs: row.avg_latency_ms ? Math.round(row.avg_latency_ms) : null,
        uniqueSessions: row.unique_sessions,
        totalTokens: row.total_tokens,
      };
    });

    res.json({ days, data });
  } catch (err) {
    // Table may not exist yet — return empty
    res.json({ days, data: [] });
  }
});

// GET /heatmap/agents — list agents with activity
router.get("/agents", (req, res) => {
  const db = getDb();
  try {
    const rows = db
      .prepare(
        `SELECT DISTINCT COALESCE(agent_name, agent_id) as agent
         FROM events
         WHERE agent_name IS NOT NULL OR agent_id IS NOT NULL
         ORDER BY agent`
      )
      .all();
    res.json(rows.map((r) => r.agent));
  } catch {
    res.json([]);
  }
});

// GET /heatmap/hourly?date=YYYY-MM-DD&agent=...
// Hourly breakdown for a specific day
router.get("/hourly", (req, res) => {
  const db = getDb();
  const date = req.query.date;
  const agent = req.query.agent || null;

  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ error: "date parameter required (YYYY-MM-DD)" });
  }

  const query = `
    SELECT
      CAST(strftime('%H', timestamp) AS INTEGER) as hour,
      COUNT(*) as total_events,
      SUM(CASE WHEN status = 'error' OR level = 'error' THEN 1 ELSE 0 END) as errors,
      AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms ELSE NULL END) as avg_latency_ms
    FROM events
    WHERE DATE(timestamp) = ?
    ${agent ? "AND (agent_id = ? OR agent_name = ?)" : ""}
    GROUP BY hour
    ORDER BY hour ASC
  `;

  const params = agent ? [date, agent, agent] : [date];

  try {
    const rows = db.prepare(query).all(...params);
    // Fill all 24 hours
    const hourly = Array.from({ length: 24 }, (_, i) => {
      const row = rows.find((r) => r.hour === i);
      return {
        hour: i,
        totalEvents: row ? row.total_events : 0,
        errors: row ? row.errors : 0,
        avgLatencyMs: row && row.avg_latency_ms ? Math.round(row.avg_latency_ms) : null,
      };
    });
    res.json({ date, hourly });
  } catch {
    res.json({ date, hourly: Array.from({ length: 24 }, (_, i) => ({ hour: i, totalEvents: 0, errors: 0, avgLatencyMs: null })) });
  }
});

module.exports = router;
