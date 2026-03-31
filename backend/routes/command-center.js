/**
 * Command Center — unified activity feed aggregating alerts, anomalies,
 * budget warnings, and session health into a single prioritized stream.
 *
 * Routes:
 *   GET /command-center/feed   — aggregated activity feed
 *   GET /command-center/summary — quick stats overview
 */

const express = require("express");
const { getDb } = require("../db");
const { wrapRoute, parseLimit, parseDays, daysAgoCutoff } = require("../lib/request-helpers");

const router = express.Router();

// ── Feed ────────────────────────────────────────────────────────────

router.get(
  "/feed",
  wrapRoute("fetch command center feed", (req, res) => {
    const db = getDb();
    const limit = parseLimit(req.query.limit, 50, 200);
    const days = parseDays(req.query.days, 7, 90);
    const cutoff = daysAgoCutoff(days);
    const severity = req.query.severity; // critical, warning, info
    const category = req.query.category; // alert, anomaly, budget, health

    const items = [];

    // 1. Alert events
    if (!category || category === "alert") {
      try {
        const alerts = db
          .prepare(
            `SELECT ae.alert_id, ae.rule_id, ae.triggered_at, ae.metric_value,
                    ae.details, ae.acknowledged,
                    ar.name AS rule_name, ar.metric, ar.operator, ar.threshold
             FROM alert_events ae
             LEFT JOIN alert_rules ar ON ae.rule_id = ar.rule_id
             WHERE ae.triggered_at >= ?
             ORDER BY ae.triggered_at DESC
             LIMIT ?`
          )
          .all(cutoff, limit);

        for (const a of alerts) {
          items.push({
            id: a.alert_id,
            category: "alert",
            severity: a.acknowledged ? "info" : "critical",
            title: a.rule_name || `Alert ${a.rule_id}`,
            summary: `${a.metric} ${a.operator} ${a.threshold} (actual: ${a.metric_value})`,
            timestamp: a.triggered_at,
            acknowledged: !!a.acknowledged,
            details: JSON.parse(a.details || "{}"),
          });
        }
      } catch (_) {
        // alert_events table may not exist yet
      }
    }

    // 2. Budget overages
    if (!category || category === "budget") {
      try {
        const budgets = db
          .prepare(
            `SELECT * FROM budgets WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT ?`
          )
          .all(cutoff, limit);

        for (const b of budgets) {
          const pct = b.limit_amount > 0 ? (b.spent / b.limit_amount) * 100 : 0;
          if (pct < 50) continue; // only show notable budget usage
          const sev = pct >= 100 ? "critical" : pct >= 80 ? "warning" : "info";
          items.push({
            id: `budget-${b.budget_id}`,
            category: "budget",
            severity: sev,
            title: `Budget: ${b.agent || "global"}`,
            summary: `$${b.spent.toFixed(2)} / $${b.limit_amount.toFixed(2)} (${pct.toFixed(0)}%)`,
            timestamp: b.updated_at,
            details: { spent: b.spent, limit: b.limit_amount, pct },
          });
        }
      } catch (_) {
        // budgets table may not exist yet
      }
    }

    // 3. Recent errors (aggregate as health signals)
    if (!category || category === "health") {
      try {
        const errors = db
          .prepare(
            `SELECT session_id, type, data, timestamp
             FROM events
             WHERE type = 'error' AND timestamp >= ?
             ORDER BY timestamp DESC
             LIMIT ?`
          )
          .all(cutoff, limit);

        for (const e of errors) {
          const data = JSON.parse(e.data || "{}");
          items.push({
            id: `error-${e.session_id}-${e.timestamp}`,
            category: "health",
            severity: "warning",
            title: `Error in session ${e.session_id.substring(0, 12)}…`,
            summary: data.message || data.error || "Agent error occurred",
            timestamp: e.timestamp,
            details: { session_id: e.session_id, ...data },
          });
        }
      } catch (_) {
        // events table may not exist yet
      }
    }

    // Filter by severity if requested
    let filtered = items;
    if (severity) {
      filtered = items.filter((i) => i.severity === severity);
    }

    // Sort by timestamp descending, then by severity priority
    const sevOrder = { critical: 0, warning: 1, info: 2 };
    filtered.sort((a, b) => {
      const ta = new Date(a.timestamp).getTime();
      const tb = new Date(b.timestamp).getTime();
      if (tb !== ta) return tb - ta;
      return (sevOrder[a.severity] || 2) - (sevOrder[b.severity] || 2);
    });

    res.json({
      feed: filtered.slice(0, limit),
      total: filtered.length,
      cutoff,
      filters: { severity: severity || null, category: category || null, days },
    });
  })
);

// ── Summary ─────────────────────────────────────────────────────────

router.get(
  "/summary",
  wrapRoute("fetch command center summary", (req, res) => {
    const db = getDb();
    const days = parseDays(req.query.days, 7, 90);
    const cutoff = daysAgoCutoff(days);

    const summary = {
      alerts: { total: 0, unacknowledged: 0 },
      budgets: { over_limit: 0, warning: 0 },
      errors: { total: 0 },
      sessions: { total: 0 },
    };

    try {
      const alertRow = db
        .prepare(
          `SELECT COUNT(*) as total,
                  SUM(CASE WHEN acknowledged = 0 THEN 1 ELSE 0 END) as unack
           FROM alert_events WHERE triggered_at >= ?`
        )
        .get(cutoff);
      summary.alerts.total = alertRow?.total || 0;
      summary.alerts.unacknowledged = alertRow?.unack || 0;
    } catch (_) {}

    try {
      const budgetRows = db
        .prepare(`SELECT spent, limit_amount FROM budgets WHERE updated_at >= ?`)
        .all(cutoff);
      for (const b of budgetRows) {
        const pct = b.limit_amount > 0 ? (b.spent / b.limit_amount) * 100 : 0;
        if (pct >= 100) summary.budgets.over_limit++;
        else if (pct >= 80) summary.budgets.warning++;
      }
    } catch (_) {}

    try {
      const errRow = db
        .prepare(
          `SELECT COUNT(*) as total FROM events WHERE type = 'error' AND timestamp >= ?`
        )
        .get(cutoff);
      summary.errors.total = errRow?.total || 0;
    } catch (_) {}

    try {
      const sessRow = db
        .prepare(`SELECT COUNT(*) as total FROM sessions WHERE created_at >= ?`)
        .get(cutoff);
      summary.sessions.total = sessRow?.total || 0;
    } catch (_) {}

    res.json({ summary, days, cutoff });
  })
);

module.exports = router;
