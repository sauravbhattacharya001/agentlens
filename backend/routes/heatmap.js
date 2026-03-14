/**
 * Usage Heatmap — temporal activity patterns (hour × day-of-week).
 */

const express = require("express");
const { getDb } = require("../db");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

const DAY_NAMES = [
  "Sunday", "Monday", "Tuesday", "Wednesday",
  "Thursday", "Friday", "Saturday",
];

function emptyGrid() {
  const grid = [];
  for (let d = 0; d < 7; d++) grid.push(new Array(24).fill(0));
  return grid;
}

function computeStats(grid) {
  let total = 0, peakValue = -1, peakHour = 0, peakDay = 0;
  let quietValue = Infinity, quietHour = 0, quietDay = 0;
  const dayTotals = new Array(7).fill(0);
  const hourTotals = new Array(24).fill(0);

  for (let d = 0; d < 7; d++) {
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      total += v;
      dayTotals[d] += v;
      hourTotals[h] += v;
      if (v > peakValue) { peakValue = v; peakHour = h; peakDay = d; }
      if (v < quietValue) { quietValue = v; quietHour = h; quietDay = d; }
    }
  }

  const weekendTotal = dayTotals[0] + dayTotals[6];
  const weekdayTotal = total - weekendTotal;
  const weekendRatio = weekdayTotal > 0
    ? Math.round((weekendTotal / weekdayTotal) * 100) / 100 : null;

  let busiestDay = 0;
  for (let d = 1; d < 7; d++) {
    if (dayTotals[d] > dayTotals[busiestDay]) busiestDay = d;
  }

  return {
    total,
    peak: { day: DAY_NAMES[peakDay], dayIndex: peakDay, hour: peakHour, value: peakValue },
    quietest: { day: DAY_NAMES[quietDay], dayIndex: quietDay, hour: quietHour, value: quietValue },
    busiestDay: { name: DAY_NAMES[busiestDay], index: busiestDay, total: dayTotals[busiestDay] },
    dayTotals: DAY_NAMES.map((name, i) => ({ day: name, total: dayTotals[i] })),
    hourTotals,
    weekendVsWeekday: weekendRatio,
  };
}

router.get(
  "/",
  wrapRoute("fetch usage heatmap", async (req, res) => {
    const metric = req.query.metric || "events";
    const agent = req.query.agent || null;
    const from = req.query.from || null;
    const to = req.query.to || null;

    if (!["events", "sessions", "tokens"].includes(metric)) {
      return res.status(400).json({ error: 'metric must be "events", "sessions", or "tokens"' });
    }

    const db = getDb();
    const grid = emptyGrid();

    if (metric === "events" || metric === "tokens") {
      let sql = metric === "events"
        ? "SELECT timestamp FROM events e"
        : "SELECT timestamp, (tokens_in + tokens_out) AS tok FROM events e";
      const conditions = [];
      const params = {};

      if (agent) {
        sql += " JOIN sessions s ON e.session_id = s.session_id";
        conditions.push("s.agent_name = @agent");
        params.agent = agent;
      }
      if (from) { conditions.push("e.timestamp >= @from"); params.from = from; }
      if (to) { conditions.push("e.timestamp <= @to"); params.to = to; }
      if (conditions.length) sql += " WHERE " + conditions.join(" AND ");

      for (const row of db.prepare(sql).all(params)) {
        const dt = new Date(row.timestamp);
        if (isNaN(dt.getTime())) continue;
        grid[dt.getUTCDay()][dt.getUTCHours()] += metric === "tokens" ? (row.tok || 0) : 1;
      }
    } else {
      let sql = "SELECT started_at FROM sessions";
      const conditions = [];
      const params = {};

      if (agent) { conditions.push("agent_name = @agent"); params.agent = agent; }
      if (from) { conditions.push("started_at >= @from"); params.from = from; }
      if (to) { conditions.push("started_at <= @to"); params.to = to; }
      if (conditions.length) sql += " WHERE " + conditions.join(" AND ");

      for (const row of db.prepare(sql).all(params)) {
        const dt = new Date(row.started_at);
        if (isNaN(dt.getTime())) continue;
        grid[dt.getUTCDay()][dt.getUTCHours()] += 1;
      }
    }

    let max = 0;
    for (let d = 0; d < 7; d++)
      for (let h = 0; h < 24; h++)
        if (grid[d][h] > max) max = grid[d][h];

    const intensity = emptyGrid();
    if (max > 0)
      for (let d = 0; d < 7; d++)
        for (let h = 0; h < 24; h++)
          intensity[d][h] = Math.round((grid[d][h] / max) * 100) / 100;

    res.json({
      metric,
      days: DAY_NAMES,
      grid,
      intensity,
      stats: computeStats(grid),
      filters: { agent: agent || "all", from, to },
    });
  })
);

module.exports = router;
