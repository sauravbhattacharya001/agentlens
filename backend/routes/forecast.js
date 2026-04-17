const express = require("express");
const { getDb } = require("../db");
const { wrapRoute, parseDays, daysAgoCutoff } = require("../lib/request-helpers");
const { loadPricingMap, computeCost } = require("../lib/pricing");

const router = express.Router();

// ── Cached prepared statements for forecast queries ─────────────
// Pre-compile all 4 filter variants (none, agent-only, model-only, both)
// once per process lifetime, avoiding repeated SQL compilation on every
// request — same pattern used by analytics.js getPerfStatements().
let _forecastStmts = null;

function getForecastStatements() {
  if (_forecastStmts) return _forecastStmts;
  const db = getDb();

  const baseSelect = `
    SELECT
      DATE(e.timestamp) AS date,
      SUM(e.tokens_in) AS tokens_in,
      SUM(e.tokens_out) AS tokens_out,
      SUM(e.tokens_in + e.tokens_out) AS tokens_total,
      COUNT(*) AS event_count,
      COUNT(DISTINCT e.session_id) AS session_count,
      e.model
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id`;
  const baseWhere = "e.timestamp >= datetime('now', '-' || ? || ' days')";
  const groupOrder = " GROUP BY DATE(e.timestamp), e.model ORDER BY date ASC";

  // Separate session count queries without model grouping — the main
  // dailyAgg query groups by (date, model), so COUNT(DISTINCT session_id)
  // per model group doesn't reflect the true daily session count when
  // sessions use multiple models (taking max across groups undercounts).
  const sessionCountBase = `
    SELECT DATE(e.timestamp) AS date, COUNT(DISTINCT e.session_id) AS session_count
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id`;
  const sessionCountGroup = " GROUP BY DATE(e.timestamp)";

  _forecastStmts = {
    dailyAgg: {
      none:  db.prepare(`${baseSelect} WHERE ${baseWhere}${groupOrder}`),
      agent: db.prepare(`${baseSelect} WHERE ${baseWhere} AND s.agent_name = ?${groupOrder}`),
      model: db.prepare(`${baseSelect} WHERE ${baseWhere} AND LOWER(e.model) = LOWER(?)${groupOrder}`),
      both:  db.prepare(`${baseSelect} WHERE ${baseWhere} AND s.agent_name = ? AND LOWER(e.model) = LOWER(?)${groupOrder}`),
    },
    dailySessionCount: {
      none:  db.prepare(`${sessionCountBase} WHERE ${baseWhere}${sessionCountGroup}`),
      agent: db.prepare(`${sessionCountBase} WHERE ${baseWhere} AND s.agent_name = ?${sessionCountGroup}`),
      model: db.prepare(`${sessionCountBase} WHERE ${baseWhere} AND LOWER(e.model) = LOWER(?)${sessionCountGroup}`),
      both:  db.prepare(`${sessionCountBase} WHERE ${baseWhere} AND s.agent_name = ? AND LOWER(e.model) = LOWER(?)${sessionCountGroup}`),
    },
    modelBreakdown: {
      none:  db.prepare(`SELECT e.model, SUM(e.tokens_in) AS tokens_in, SUM(e.tokens_out) AS tokens_out, COUNT(*) AS event_count, COUNT(DISTINCT e.session_id) AS session_count FROM events e JOIN sessions s ON e.session_id = s.session_id WHERE e.timestamp >= datetime('now', '-' || ? || ' days') GROUP BY e.model ORDER BY tokens_in + tokens_out DESC`),
      agent: db.prepare(`SELECT e.model, SUM(e.tokens_in) AS tokens_in, SUM(e.tokens_out) AS tokens_out, COUNT(*) AS event_count, COUNT(DISTINCT e.session_id) AS session_count FROM events e JOIN sessions s ON e.session_id = s.session_id WHERE e.timestamp >= datetime('now', '-' || ? || ' days') AND s.agent_name = ? GROUP BY e.model ORDER BY tokens_in + tokens_out DESC`),
    },
  };

  return _forecastStmts;
}

// ── Helpers ─────────────────────────────────────────────────────

// parseDays now imported from request-helpers

/**
 * Parse and clamp forecastDays query parameter (1-90, default 7).
 */
function parseForecastDays(raw) {
  const n = parseInt(raw) || 7;
  return Math.min(Math.max(1, n), 90);
}

/**
 * Estimate cost for a single event using the shared pricing module.
 * Wraps computeCost() to maintain the same interface used by callers.
 */
function estimateCost(event, pricingMap) {
  const result = computeCost(event.model, event.tokens_in || 0, event.tokens_out || 0, pricingMap);
  return result ? result.totalCost : 0;
}

/**
 * Fetch daily aggregated usage from the events table.
 * Uses pre-compiled prepared statements to avoid SQL recompilation per request.
 *
 * @param {object} db    - Database connection
 * @param {number} days  - Lookback window in days
 * @param {string|null} agent - Optional agent filter
 * @param {string|null} model - Optional model filter
 * @returns {Array<{date,tokens_in,tokens_out,tokens_total,event_count,session_count,cost}>}
 */
function fetchDailyAggregates(db, days, agent, model, pricingMap) {
  const stmts = getForecastStatements();
  const variant = agent && model ? "both" : agent ? "agent" : model ? "model" : "none";
  const stmt = stmts.dailyAgg[variant];

  const params = [days];
  if (agent) params.push(agent);
  if (model) params.push(model);

  const rows = stmt.all(...params);

  // Fetch accurate per-day session counts from a separate query that
  // counts DISTINCT session_id without model grouping.  The main query
  // groups by (date, model), so per-group session counts don't reflect
  // sessions that span multiple models — taking max undercounted, and
  // summing overcounted.  This dedicated query is the only correct way.
  const sessionStmt = stmts.dailySessionCount[variant];
  const sessionRows = sessionStmt.all(...params);
  const sessionCountByDate = Object.create(null);
  for (const sr of sessionRows) {
    sessionCountByDate[sr.date] = sr.session_count;
  }

  // Aggregate per day (across models), computing cost per row
  const dailyMap = {};
  for (const row of rows) {
    const d = row.date;
    if (!dailyMap[d]) {
      dailyMap[d] = {
        date: d,
        tokens_in: 0,
        tokens_out: 0,
        tokens_total: 0,
        event_count: 0,
        session_count: sessionCountByDate[d] || 0,
        cost: 0,
      };
    }
    dailyMap[d].tokens_in += row.tokens_in || 0;
    dailyMap[d].tokens_out += row.tokens_out || 0;
    dailyMap[d].tokens_total += row.tokens_total || 0;
    dailyMap[d].event_count += row.event_count || 0;
    dailyMap[d].cost += estimateCost(row, pricingMap);
  }

  return Object.values(dailyMap).sort((a, b) => a.date.localeCompare(b.date));
}

// ── Math utilities ──────────────────────────────────────────────

/**
 * Simple OLS linear regression: y = slope * x + intercept.
 * x-values are 0, 1, 2, ...
 *
 * Single-pass implementation: computes slope, intercept, and R² in one
 * loop over the data (previously required 2-3 separate passes).
 * For x = 0..n-1, xMean = (n-1)/2 and Σ(x-xMean)² = n(n²-1)/12,
 * both computed analytically to avoid a pass over x values.
 *
 * @param {number[]} values - y-values
 * @returns {{ slope: number, intercept: number, r2: number }}
 */
function linearRegression(values) {
  const n = values.length;
  if (n === 0) return { slope: 0, intercept: 0, r2: 0 };
  if (n === 1) return { slope: 0, intercept: values[0], r2: 1 };

  const xMean = (n - 1) / 2;
  // Σ(x - xMean)² for x = 0..n-1 has a closed-form: n(n²-1)/12
  const den = n * (n * n - 1) / 12;
  if (den === 0) return { slope: 0, intercept: values[0], r2: 0 };

  // Single pass: accumulate ySum, cross-product, and sum-of-squares
  // for residuals simultaneously.
  let ySum = 0;
  let crossSum = 0;
  for (let i = 0; i < n; i++) {
    ySum += values[i];
    crossSum += (i - xMean) * values[i];
  }
  const yMean = ySum / n;
  const slope = crossSum / den;
  const intercept = yMean - slope * xMean;

  // R² in one more pass (combined with ssTot)
  let ssTot = 0, ssRes = 0;
  for (let i = 0; i < n; i++) {
    const dy = values[i] - yMean;
    ssTot += dy * dy;
    const residual = values[i] - (slope * i + intercept);
    ssRes += residual * residual;
  }
  const r2 = ssTot > 0 ? Math.max(0, 1 - ssRes / ssTot) : 0;

  return { slope, intercept, r2 };
}

/**
 * Exponential moving average — weights recent values more heavily.
 * @param {number[]} values
 * @param {number} alpha - smoothing factor (0-1), higher = more recent
 * @returns {number}
 */
function ema(values, alpha) {
  if (alpha === undefined) alpha = 0.3;
  if (values.length === 0) return 0;
  let val = values[0];
  for (let i = 1; i < values.length; i++) {
    val = alpha * values[i] + (1 - alpha) * val;
  }
  return val;
}

/**
 * Sample standard deviation (Bessel's correction).
 */
function stddev(values) {
  const n = values.length;
  if (n < 2) return 0;
  let sum = 0;
  for (let i = 0; i < n; i++) sum += values[i];
  const mean = sum / n;
  let ss = 0;
  for (let i = 0; i < n; i++) ss += (values[i] - mean) ** 2;
  return Math.sqrt(ss / (n - 1));
}

/**
 * Prediction interval (approximate) using residual standard error.
 * @returns {{ low: number, high: number }}
 */
function predictionInterval(values, slope, intercept, futureX) {
  const n = values.length;
  if (n < 3) {
    const predicted = Math.max(0, slope * futureX + intercept);
    return { low: predicted * 0.5, high: predicted * 1.5 };
  }
  let ssRes = 0;
  for (let i = 0; i < n; i++) {
    ssRes += (values[i] - (slope * i + intercept)) ** 2;
  }
  const se = Math.sqrt(ssRes / (n - 2));
  const z = 1.645; // ~90% confidence
  const predicted = slope * futureX + intercept;
  const margin = z * se * Math.sqrt(1 + 1 / n);
  return {
    low: Math.max(0, predicted - margin),
    high: predicted + margin,
  };
}

/**
 * Detect trend: "increasing", "decreasing", "stable", or "insufficient_data".
 *
 * Accepts an optional pre-computed regression result to avoid redundant
 * linearRegression() calls when the caller already has it.
 *
 * @param {number[]} values
 * @param {{ slope: number }} [regression] - Pre-computed regression result
 */
function detectTrend(values, regression) {
  if (values.length < 3) return { trend: "insufficient_data", pctPerDay: 0 };
  const { slope } = regression || linearRegression(values);
  let sum = 0;
  for (let i = 0; i < values.length; i++) sum += values[i];
  const avg = sum / values.length || 1;
  const pct = (slope / avg) * 100;

  if (pct > 5) return { trend: "increasing", pctPerDay: round(pct, 2) };
  if (pct < -5) return { trend: "decreasing", pctPerDay: round(pct, 2) };
  return { trend: "stable", pctPerDay: round(pct, 2) };
}

function round(val, decimals) {
  const factor = 10 ** (decimals || 2);
  return Math.round(val * factor) / factor;
}

// ── Routes ──────────────────────────────────────────────────────

/**
 * GET /forecast
 *
 * Forecast future daily cost & token usage based on historical data.
 *
 * @query {number} [days=30]          - Lookback window for historical data (1-365)
 * @query {number} [forecastDays=7]   - Number of days to forecast (1-90)
 * @query {string} [agent]            - Filter by agent name
 * @query {string} [model]            - Filter by model name
 * @query {string} [method=auto]      - Forecast method: "linear", "ema", "average", "auto"
 * @returns {object} Forecast with daily predictions, summary, trend
 */
router.get("/", wrapRoute("forecast usage", (req, res) => {
  const db = getDb();
  const days = parseDays(req.query.days);
  const forecastDays = parseForecastDays(req.query.forecastDays);
  const agent = req.query.agent || null;
  const model = req.query.model || null;
  let method = req.query.method || "auto";

  if (!["linear", "ema", "average", "auto"].includes(method)) {
    return res.status(400).json({ error: "method must be 'linear', 'ema', 'average', or 'auto'" });
  }

  const pricingMap = loadPricingMap();
  const dailyData = fetchDailyAggregates(db, days, agent, model, pricingMap);

  if (dailyData.length === 0) {
    return res.json({
      forecast: [],
      summary: { totalPredictedCost: 0, totalPredictedTokens: 0 },
      method: "none",
      dataPointsUsed: 0,
      message: "No historical data found for the given filters",
    });
  }

  const costValues = dailyData.map(d => d.cost);
  const tokenValues = dailyData.map(d => d.tokens_total);
  const sessionValues = dailyData.map(d => d.session_count);
  const n = costValues.length;

  // Auto-select method based on data availability
  if (method === "auto") {
    if (n >= 5) method = "linear";
    else if (n >= 2) method = "ema";
    else method = "average";
  }

  // Last date in the dataset
  const lastDate = new Date(dailyData[n - 1].date + "T00:00:00Z");

  const predictions = [];
  let totalPredictedCost = 0;
  let totalPredictedTokens = 0;

  // Coerce to numbers once, reused by regression and trend detection
  const numTokenValues = tokenValues.map(Number);
  const numSessionValues = sessionValues.map(Number);

  if (method === "linear") {
    const costReg = linearRegression(costValues);
    const tokenReg = linearRegression(numTokenValues);
    const sessionReg = linearRegression(numSessionValues);

    for (let d = 1; d <= forecastDays; d++) {
      const futureX = n - 1 + d;
      const predCost = Math.max(0, costReg.slope * futureX + costReg.intercept);
      const predTokens = Math.max(0, Math.round(tokenReg.slope * futureX + tokenReg.intercept));
      const predSessions = Math.max(0, Math.round(sessionReg.slope * futureX + sessionReg.intercept));
      const ci = predictionInterval(costValues, costReg.slope, costReg.intercept, futureX);

      const predDate = new Date(lastDate);
      predDate.setUTCDate(predDate.getUTCDate() + d);

      predictions.push({
        date: predDate.toISOString().split("T")[0],
        predictedCost: round(predCost, 6),
        predictedTokens: predTokens,
        predictedSessions: predSessions,
        confidenceLow: round(ci.low, 6),
        confidenceHigh: round(ci.high, 6),
        method: "linear",
      });
      totalPredictedCost += predCost;
      totalPredictedTokens += predTokens;
    }
  } else if (method === "ema") {
    const emaCost = ema(costValues);
    const emaTokens = ema(numTokenValues);
    const emaSessions = ema(numSessionValues);
    const std = n >= 2 ? stddev(costValues) : emaCost * 0.5;

    for (let d = 1; d <= forecastDays; d++) {
      const predDate = new Date(lastDate);
      predDate.setUTCDate(predDate.getUTCDate() + d);

      predictions.push({
        date: predDate.toISOString().split("T")[0],
        predictedCost: round(Math.max(0, emaCost), 6),
        predictedTokens: Math.max(0, Math.round(emaTokens)),
        predictedSessions: Math.max(0, Math.round(emaSessions)),
        confidenceLow: round(Math.max(0, emaCost - 1.5 * std), 6),
        confidenceHigh: round(emaCost + 1.5 * std, 6),
        method: "ema",
      });
      totalPredictedCost += emaCost;
      totalPredictedTokens += Math.round(emaTokens);
    }
  } else {
    // average
    let costSum = 0, tokenSum = 0, sessionSum = 0;
    for (let i = 0; i < n; i++) {
      costSum += costValues[i];
      tokenSum += tokenValues[i];
      sessionSum += sessionValues[i];
    }
    const avgCost = costSum / n;
    const avgTokens = Math.round(tokenSum / n);
    const avgSessions = Math.round(sessionSum / n);
    const std = n >= 2 ? stddev(costValues) : avgCost * 0.5;

    for (let d = 1; d <= forecastDays; d++) {
      const predDate = new Date(lastDate);
      predDate.setUTCDate(predDate.getUTCDate() + d);

      predictions.push({
        date: predDate.toISOString().split("T")[0],
        predictedCost: round(Math.max(0, avgCost), 6),
        predictedTokens: Math.max(0, avgTokens),
        predictedSessions: Math.max(0, avgSessions),
        confidenceLow: round(Math.max(0, avgCost - 1.5 * std), 6),
        confidenceHigh: round(avgCost + 1.5 * std, 6),
        method: "average",
      });
      totalPredictedCost += avgCost;
      totalPredictedTokens += avgTokens;
    }
  }

  // Trend detection — reuse pre-computed regression results when
  // available (linear method) to avoid redundant linearRegression() calls
  const costTrend = method === "linear"
    ? detectTrend(costValues, costReg)
    : detectTrend(costValues);
  const tokenTrend = method === "linear"
    ? detectTrend(numTokenValues, tokenReg)
    : detectTrend(numTokenValues);

  // Historical summary
  let histTotalCost = 0, histTotalTokens = 0, histTotalSessions = 0;
  for (let i = 0; i < n; i++) {
    histTotalCost += costValues[i];
    histTotalTokens += tokenValues[i];
    histTotalSessions += sessionValues[i];
  }
  const dailyAvgCost = histTotalCost / n;

  return res.json({
    forecast: predictions,
    summary: {
      totalPredictedCost: round(totalPredictedCost, 4),
      totalPredictedTokens: totalPredictedTokens,
      averageDailyCost: round(totalPredictedCost / forecastDays, 4),
      averageDailyTokens: Math.round(totalPredictedTokens / forecastDays),
      weeklyProjection: round(dailyAvgCost * 7, 4),
      monthlyProjection: round(dailyAvgCost * 30, 4),
    },
    trend: {
      cost: costTrend,
      tokens: tokenTrend,
    },
    historical: {
      daysAnalyzed: n,
      totalCost: round(histTotalCost, 4),
      totalTokens: histTotalTokens,
      totalSessions: histTotalSessions,
      dailyAverageCost: round(dailyAvgCost, 4),
      dailyAverageTokens: Math.round(histTotalTokens / n),
    },
    method,
    dataPointsUsed: n,
    filters: {
      lookbackDays: days,
      forecastDays,
      agent: agent || "all",
      model: model || "all",
    },
  });
}));

/**
 * GET /forecast/budget
 *
 * Check if current spending pace will exceed a budget within a given period.
 *
 * @query {number} budget   - Monthly budget in USD (required)
 * @query {number} [days=30]     - Lookback window for historical data
 * @query {number} [period=30]   - Budget period in days (default 30)
 * @query {string} [agent]       - Filter by agent name
 * @returns {object} Budget alert with severity and projection
 */
router.get("/budget", wrapRoute("forecast budget check", (req, res) => {
  const db = getDb();
  const budget = parseFloat(req.query.budget);
  if (!Number.isFinite(budget) || budget <= 0) {
    return res.status(400).json({ error: "budget must be a positive number (USD)" });
  }

  const days = parseDays(req.query.days);
  const period = Math.min(Math.max(1, parseInt(req.query.period) || 30), 365);
  const agent = req.query.agent || null;

  const pricingMap = loadPricingMap();
  const dailyData = fetchDailyAggregates(db, days, agent, null, pricingMap);

  if (dailyData.length === 0) {
    return res.json({
      severity: "unknown",
      message: "No historical data — cannot assess budget",
      budget,
      projected: 0,
    });
  }

  const costValues = dailyData.map(d => d.cost);
  const n = costValues.length;

  let totalSpent = 0;
  for (let i = 0; i < n; i++) totalSpent += costValues[i];
  const dailyAvg = totalSpent / n;

  const projected = dailyAvg * period;
  const remaining = budget - totalSpent;
  const daysUntilExceeded = dailyAvg > 0 && remaining > 0
    ? Math.floor(remaining / dailyAvg)
    : dailyAvg > 0 ? 0 : null;

  const overshootPct = Math.max(0, (projected - budget) / budget * 100);
  const utilizationPct = projected / budget * 100;

  let severity, message;
  if (projected <= budget * 0.8) {
    severity = "safe";
    message = `On track: projected $${projected.toFixed(2)} of $${budget.toFixed(2)} budget (${utilizationPct.toFixed(0)}%)`;
  } else if (projected <= budget) {
    severity = "warning";
    message = `Approaching limit: projected $${projected.toFixed(2)} of $${budget.toFixed(2)} (${utilizationPct.toFixed(0)}%). Consider reducing usage.`;
  } else {
    severity = "critical";
    const exceed = daysUntilExceeded === 0
      ? "Already exceeded!"
      : `~${daysUntilExceeded} days until exceeded.`;
    message = `Budget overrun likely: projected $${projected.toFixed(2)} vs $${budget.toFixed(2)} limit (+${overshootPct.toFixed(0)}%). ${exceed}`;
  }

  return res.json({
    severity,
    message,
    budget: round(budget, 2),
    totalSpentSoFar: round(totalSpent, 4),
    dailyAverageCost: round(dailyAvg, 4),
    projectedSpend: round(projected, 4),
    overshootPct: round(overshootPct, 2),
    utilizationPct: round(utilizationPct, 2),
    daysUntilExceeded,
    periodDays: period,
    daysAnalyzed: n,
    agent: agent || "all",
  });
}));

/**
 * GET /forecast/spending-summary
 *
 * Aggregated spending statistics with model breakdown and trend.
 *
 * @query {number} [days=30] - Lookback window
 * @query {string} [agent]   - Optional agent filter
 * @returns {object} Spending summary
 */
router.get("/spending-summary", wrapRoute("forecast spending summary", (req, res) => {
  const db = getDb();
  const days = parseDays(req.query.days);
  const agent = req.query.agent || null;

  const pricingMap = loadPricingMap();

  // Fetch per-model daily data for model breakdown (using cached statements)
  const stmts = getForecastStatements();
  const variant = agent ? "agent" : "none";
  const modelStmt = stmts.modelBreakdown[variant];
  const params = agent ? [days, agent] : [days];
  const modelRows = modelStmt.all(...params);

  const modelBreakdown = {};
  let totalCost = 0, totalTokensIn = 0, totalTokensOut = 0, totalEvents = 0;

  for (const row of modelRows) {
    const modelName = row.model || "unknown";
    const cost = estimateCost(row, pricingMap);
    totalCost += cost;
    totalTokensIn += row.tokens_in || 0;
    totalTokensOut += row.tokens_out || 0;
    totalEvents += row.event_count;

    modelBreakdown[modelName] = {
      cost: round(cost, 6),
      tokensIn: row.tokens_in || 0,
      tokensOut: row.tokens_out || 0,
      totalTokens: (row.tokens_in || 0) + (row.tokens_out || 0),
      eventCount: row.event_count,
      sessionCount: row.session_count,
      costPct: 0, // filled below
    };
  }

  // Fill cost percentages
  for (const m in modelBreakdown) {
    modelBreakdown[m].costPct = totalCost > 0
      ? round(modelBreakdown[m].cost / totalCost * 100, 2)
      : 0;
  }

  // Daily aggregates for trend
  const dailyData = fetchDailyAggregates(db, days, agent, null, pricingMap);
  const n = dailyData.length;
  const costTrend = detectTrend(dailyData.map(d => d.cost));

  const totalTokens = totalTokensIn + totalTokensOut;
  const dailyAvgCost = n > 0 ? totalCost / n : 0;
  const costPer1kTokens = totalTokens > 0 ? totalCost / totalTokens * 1000 : 0;

  // Busiest day
  let busiestDay = null, busiestDayCost = 0;
  for (const d of dailyData) {
    if (d.cost > busiestDayCost) {
      busiestDayCost = d.cost;
      busiestDay = d.date;
    }
  }

  return res.json({
    totalCost: round(totalCost, 4),
    totalTokens,
    totalTokensIn,
    totalTokensOut,
    totalEvents,
    daysTracked: n,
    dailyAverageCost: round(dailyAvgCost, 4),
    dailyAverageTokens: n > 0 ? Math.round(totalTokens / n) : 0,
    weeklyProjection: round(dailyAvgCost * 7, 4),
    monthlyProjection: round(dailyAvgCost * 30, 4),
    costPer1kTokens: round(costPer1kTokens, 6),
    busiestDay,
    busiestDayCost: round(busiestDayCost, 6),
    modelBreakdown,
    trend: costTrend,
    filters: { lookbackDays: days, agent: agent || "all" },
  });
}));

// ── Exported for testing ────────────────────────────────────────

router._testExports = {
  linearRegression,
  ema,
  stddev,
  predictionInterval,
  detectTrend,
  estimateCost,
  round,
};

module.exports = router;
