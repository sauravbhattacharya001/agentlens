/**
 * Cost Budgets API — Set and track spending limits per agent or globally.
 *
 * Budgets can be:
 * - Global (scope = "global") — applies to all agents combined
 * - Per-agent (scope = "agent:<name>") — applies to a specific agent
 *
 * Each budget has a period (daily/weekly/monthly/total) and a limit in USD.
 * The API calculates real-time spend against the budget using model pricing.
 */

const express = require("express");
const { getDb } = require("../db");
const { wrapRoute } = require("../lib/request-helpers");
const { loadPricingMap, findPricing } = require("../lib/pricing");

const router = express.Router();

let _budgetStmts = null;

function getBudgetStatements() {
  if (_budgetStmts) return _budgetStmts;
  const db = getDb();
  _budgetStmts = {
    getAll: db.prepare("SELECT * FROM cost_budgets ORDER BY scope, period"),
    getByScope: db.prepare("SELECT * FROM cost_budgets WHERE scope = ?"),
    upsert: db.prepare(`
      INSERT INTO cost_budgets (scope, period, limit_usd, warn_pct, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(scope, period) DO UPDATE SET
        limit_usd = excluded.limit_usd,
        warn_pct = excluded.warn_pct,
        updated_at = excluded.updated_at
    `),
    deleteOne: db.prepare("DELETE FROM cost_budgets WHERE scope = ? AND period = ?"),
    deleteByScope: db.prepare("DELETE FROM cost_budgets WHERE scope = ?"),
  };
  return _budgetStmts;
}

function getPeriodRange(period) {
  const now = new Date();
  let start;
  switch (period) {
    case "daily":
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      break;
    case "weekly":
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - now.getDay());
      break;
    case "monthly":
      start = new Date(now.getFullYear(), now.getMonth(), 1);
      break;
    case "total":
    default:
      start = new Date("2000-01-01");
  }
  return { start: start.toISOString(), end: now.toISOString() };
}

function calculateSpend(scope, startDate, endDate) {
  const db = getDb();
  const pricingMap = loadPricingMap();

  let query, params;
  if (scope === "global") {
    query = `SELECT e.model, SUM(e.tokens_in) as total_in, SUM(e.tokens_out) as total_out
      FROM events e JOIN sessions s ON e.session_id = s.session_id
      WHERE s.started_at >= ? AND s.started_at <= ? AND e.model IS NOT NULL AND e.model != ''
      GROUP BY e.model`;
    params = [startDate, endDate];
  } else if (scope.startsWith("agent:")) {
    query = `SELECT e.model, SUM(e.tokens_in) as total_in, SUM(e.tokens_out) as total_out
      FROM events e JOIN sessions s ON e.session_id = s.session_id
      WHERE s.agent_name = ? AND s.started_at >= ? AND s.started_at <= ? AND e.model IS NOT NULL AND e.model != ''
      GROUP BY e.model`;
    params = [scope.slice(6), startDate, endDate];
  } else {
    return { spend: 0, breakdown: {} };
  }

  const rows = db.prepare(query).all(...params);
  let totalSpend = 0;
  const breakdown = {};

  for (const row of rows) {
    const pricing = findPricing(row.model, pricingMap);
    if (pricing) {
      const inputCost = ((row.total_in || 0) / 1_000_000) * pricing.input;
      const outputCost = ((row.total_out || 0) / 1_000_000) * pricing.output;
      const cost = inputCost + outputCost;
      totalSpend += cost;
      breakdown[row.model] = {
        tokens_in: row.total_in || 0, tokens_out: row.total_out || 0,
        cost: Math.round(cost * 1_000_000) / 1_000_000,
      };
    }
  }
  return { spend: Math.round(totalSpend * 1_000_000) / 1_000_000, breakdown };
}

function budgetStatus(b) {
  const range = getPeriodRange(b.period);
  const { spend, breakdown } = calculateSpend(b.scope, range.start, range.end);
  const pct = b.limit_usd > 0 ? Math.round((spend / b.limit_usd) * 10000) / 100 : 0;
  return {
    scope: b.scope, period: b.period, limit_usd: b.limit_usd, warn_pct: b.warn_pct,
    current_spend: spend, usage_pct: pct,
    status: pct >= 100 ? "exceeded" : pct >= b.warn_pct ? "warning" : "ok",
    remaining: Math.round(Math.max(0, b.limit_usd - spend) * 1_000_000) / 1_000_000,
    period_start: range.start, period_end: range.end, model_breakdown: breakdown,
    created_at: b.created_at, updated_at: b.updated_at,
  };
}

// GET /budgets
router.get("/", wrapRoute("list budgets", (req, res) => {
  res.json({ budgets: getBudgetStatements().getAll.all().map(budgetStatus) });
}));

// GET /budgets/check/:sessionId — check if session's agent is over budget
router.get("/check/:sessionId", wrapRoute("check session budget", (req, res) => {
  const db = getDb();
  const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(req.params.sessionId);
  if (!session) return res.status(404).json({ error: "Session not found" });

  const stmts = getBudgetStatements();
  const all = [...stmts.getByScope.all(`agent:${session.agent_name}`), ...stmts.getByScope.all("global")];
  const results = all.map((b) => {
    const range = getPeriodRange(b.period);
    const { spend } = calculateSpend(b.scope, range.start, range.end);
    const pct = b.limit_usd > 0 ? Math.round((spend / b.limit_usd) * 10000) / 100 : 0;
    return { scope: b.scope, period: b.period, limit_usd: b.limit_usd, current_spend: spend, usage_pct: pct,
      status: pct >= 100 ? "exceeded" : pct >= b.warn_pct ? "warning" : "ok" };
  });
  res.json({ session_id: req.params.sessionId, agent_name: session.agent_name, budgets: results,
    any_exceeded: results.some((r) => r.status === "exceeded"),
    any_warning: results.some((r) => r.status === "warning" || r.status === "exceeded") });
}));

// GET /budgets/:scope
router.get("/:scope", wrapRoute("get budget", (req, res) => {
  const scope = decodeURIComponent(req.params.scope);
  if (scope !== "global" && !scope.startsWith("agent:"))
    return res.status(400).json({ error: 'Invalid scope. Use "global" or "agent:<name>"' });
  const budgets = getBudgetStatements().getByScope.all(scope);
  if (!budgets.length) return res.status(404).json({ error: `No budgets for scope '${scope}'` });
  res.json({ budgets: budgets.map(budgetStatus) });
}));

// PUT /budgets
router.put("/", wrapRoute("upsert budget", (req, res) => {
  const { scope, period, limit_usd, warn_pct } = req.body;
  if (!scope || (scope !== "global" && !scope.startsWith("agent:")))
    return res.status(400).json({ error: 'scope must be "global" or "agent:<name>"' });
  const validPeriods = ["daily", "weekly", "monthly", "total"];
  if (!period || !validPeriods.includes(period))
    return res.status(400).json({ error: `period must be one of: ${validPeriods.join(", ")}` });
  const limit = Number(limit_usd);
  if (!Number.isFinite(limit) || limit <= 0)
    return res.status(400).json({ error: "limit_usd must be a positive number" });
  const warnThreshold = warn_pct !== undefined ? Number(warn_pct) : 80;
  if (!Number.isFinite(warnThreshold) || warnThreshold < 0 || warnThreshold > 100)
    return res.status(400).json({ error: "warn_pct must be between 0 and 100" });

  const now = new Date().toISOString();
  getBudgetStatements().upsert.run(scope, period, limit, warnThreshold, now, now);
  const range = getPeriodRange(period);
  const { spend, breakdown } = calculateSpend(scope, range.start, range.end);
  const pct = limit > 0 ? Math.round((spend / limit) * 10000) / 100 : 0;
  res.json({ status: "ok", budget: { scope, period, limit_usd: limit, warn_pct: warnThreshold,
    current_spend: spend, usage_pct: pct,
    budget_status: pct >= 100 ? "exceeded" : pct >= warnThreshold ? "warning" : "ok",
    remaining: Math.round(Math.max(0, limit - spend) * 1_000_000) / 1_000_000, model_breakdown: breakdown } });
}));

// DELETE /budgets/:scope/:period
router.delete("/:scope/:period", wrapRoute("delete budget", (req, res) => {
  const scope = decodeURIComponent(req.params.scope);
  if (scope !== "global" && !scope.startsWith("agent:"))
    return res.status(400).json({ error: "Invalid scope" });
  const result = getBudgetStatements().deleteOne.run(scope, req.params.period);
  if (result.changes === 0) return res.status(404).json({ error: "Budget not found" });
  res.json({ status: "ok", deleted: { scope, period: req.params.period } });
}));

// DELETE /budgets/:scope
router.delete("/:scope", wrapRoute("delete budgets by scope", (req, res) => {
  const scope = decodeURIComponent(req.params.scope);
  if (scope !== "global" && !scope.startsWith("agent:"))
    return res.status(400).json({ error: "Invalid scope" });
  const result = getBudgetStatements().deleteByScope.run(scope);
  res.json({ status: "ok", deleted: result.changes });
}));

module.exports = router;
