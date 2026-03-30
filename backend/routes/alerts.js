/* ── Alert Rules — threshold-based alerting for agent observability ──── */

const express = require("express");
const crypto = require("crypto");
const router = express.Router();
const { getDb } = require("../db");
const { fireWebhooks } = require("./webhooks");
const { wrapRoute, parseLimit } = require("../lib/request-helpers");

// ── Path parameter validation ───────────────────────────────────────
// IDs are generated via `Date.now().toString(36)-<12 hex chars>`, so
// they only contain alphanumeric characters and hyphens.  Reject
// anything else early to prevent SQL injection or parameter confusion.
const SAFE_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$/;

function validateIdParam(paramName) {
  return (req, res, next) => {
    const val = req.params[paramName];
    if (!val || !SAFE_ID_RE.test(val)) {
      return res.status(400).json({ error: `Invalid ${paramName} format` });
    }
    next();
  };
}

// ── Schema initialisation ───────────────────────────────────────────

function ensureAlertsTable() {
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS alert_rules (
      rule_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      metric TEXT NOT NULL,
      operator TEXT NOT NULL CHECK(operator IN ('<','>','<=','>=','==','!=')),
      threshold REAL NOT NULL,
      window_minutes INTEGER NOT NULL DEFAULT 60,
      agent_filter TEXT DEFAULT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      cooldown_minutes INTEGER NOT NULL DEFAULT 15,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS alert_events (
      alert_id TEXT PRIMARY KEY,
      rule_id TEXT NOT NULL,
      triggered_at TEXT NOT NULL,
      metric_value REAL NOT NULL,
      details TEXT DEFAULT '{}',
      acknowledged INTEGER NOT NULL DEFAULT 0,
      acknowledged_at TEXT DEFAULT NULL,
      FOREIGN KEY (rule_id) REFERENCES alert_rules(rule_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_alert_events_rule ON alert_events(rule_id);
    CREATE INDEX IF NOT EXISTS idx_alert_events_triggered ON alert_events(triggered_at);
    CREATE INDEX IF NOT EXISTS idx_alert_events_ack ON alert_events(acknowledged);
  `);
}

// Valid metrics users can alert on
const VALID_METRICS = [
  "total_tokens",         // total tokens (in+out) across sessions in window
  "avg_tokens_per_session", // average tokens per session in window
  "error_rate",           // % of events with errors in window
  "avg_duration_ms",      // average event duration in window
  "max_duration_ms",      // max event duration in window
  "session_count",        // number of new sessions in window
  "event_count",          // number of events in window
  "token_rate",           // tokens per minute in window
];

const VALID_OPERATORS = ["<", ">", "<=", ">=", "==", "!="];

// ── Security limits ─────────────────────────────────────────────────
const MAX_WINDOW_MINUTES = 10080;   // 7 days max — prevents expensive full-table scans
const MAX_COOLDOWN_MINUTES = 10080; // 7 days max
const MAX_NAME_LENGTH = 128;
const MAX_AGENT_FILTER_LENGTH = 256;
const MAX_ALERT_RULES = 100;        // cap total rules to prevent DoS via evaluate endpoint

// Validate ruleId / alertId: alphanumeric + hyphens, max 64 chars.
// Matches the pattern used by generateId() and prevents log injection
// or cache pollution from arbitrary-length / special-char params.
const RESOURCE_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$/;

function isValidResourceId(id) {
  return typeof id === "string" && RESOURCE_ID_RE.test(id);
}

// Middleware: reject invalid ruleId early (mirrors webhooks.js pattern)
router.param("ruleId", (req, res, next, val) => {
  if (!isValidResourceId(val)) {
    return res.status(400).json({ error: "Invalid rule ID format" });
  }
  next();
});

router.param("alertId", (req, res, next, val) => {
  if (!isValidResourceId(val)) {
    return res.status(400).json({ error: "Invalid alert ID format" });
  }
  next();
});

// ── Helper: generate unique ID ──────────────────────────────────────

function generateId() {
  return `${Date.now().toString(36)}-${crypto.randomBytes(6).toString('hex')}`;
}

// ── Helper: evaluate metric value for a time window ─────────────────

// Cached prepared statements for metric evaluation.  Each metric needs
// two variants: one without and one with an agent-name filter.  Lazily
// built on first use, reused across all subsequent evaluations so the
// SQL is only compiled once per process lifetime.
let _metricStmts = null;

function getMetricStatements() {
  if (_metricStmts) return _metricStmts;
  const db = getDb();

  const build = (sql) => ({
    all:   db.prepare(sql),
    agent: db.prepare(sql + " AND s.agent_name = ?"),
  });

  // Event-joined queries use "e" + "s" aliases
  const buildE = (sql) => ({
    all:   db.prepare(sql),
    agent: db.prepare(sql + " AND s.agent_name = ?"),
  });

  _metricStmts = {
    total_tokens: build(
      `SELECT COALESCE(SUM(s.total_tokens_in + s.total_tokens_out), 0) AS val
       FROM sessions s WHERE s.started_at >= ?`
    ),
    avg_tokens_per_session: build(
      `SELECT COALESCE(AVG(s.total_tokens_in + s.total_tokens_out), 0) AS val
       FROM sessions s WHERE s.started_at >= ?`
    ),
    error_rate: buildE(
      `SELECT
         COUNT(*) AS total,
         SUM(CASE WHEN e.event_type IN ('error', 'agent_error', 'tool_error') THEN 1 ELSE 0 END) AS errors
       FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ?`
    ),
    avg_duration_ms: buildE(
      `SELECT COALESCE(AVG(e.duration_ms), 0) AS val
       FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ? AND e.duration_ms IS NOT NULL`
    ),
    max_duration_ms: buildE(
      `SELECT COALESCE(MAX(e.duration_ms), 0) AS val
       FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ? AND e.duration_ms IS NOT NULL`
    ),
    session_count: build(
      `SELECT COUNT(*) AS val FROM sessions s
       WHERE s.started_at >= ?`
    ),
    event_count: buildE(
      `SELECT COUNT(*) AS val FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ?`
    ),
    token_rate: buildE(
      `SELECT COALESCE(SUM(e.tokens_in + e.tokens_out), 0) AS total
       FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ?`
    ),
  };

  return _metricStmts;
}

function evaluateMetric(metric, windowMinutes, agentFilter) {
  const windowStart = new Date(Date.now() - windowMinutes * 60 * 1000).toISOString();
  const stmts = getMetricStatements();
  const pair = stmts[metric];

  if (!pair) throw new Error(`Unknown metric: ${metric}`);

  const stmt = agentFilter ? pair.agent : pair.all;
  const params = agentFilter ? [windowStart, agentFilter] : [windowStart];

  if (metric === "error_rate") {
    const row = stmt.get(...params);
    return row.total > 0 ? (row.errors / row.total) * 100 : 0;
  }

  if (metric === "token_rate") {
    const row = stmt.get(...params);
    return windowMinutes > 0 ? row.total / windowMinutes : 0;
  }

  const row = stmt.get(...params);
  return row.val;
}

// ── Helper: compare value against threshold ─────────────────────────

function compareValue(value, operator, threshold) {
  switch (operator) {
    case "<":  return value < threshold;
    case ">":  return value > threshold;
    case "<=": return value <= threshold;
    case ">=": return value >= threshold;
    case "==": return value === threshold;
    case "!=": return value !== threshold;
    default:   return false;
  }
}

// ── GET /alerts/rules — list all alert rules ────────────────────────

router.get("/rules", wrapRoute("list alert rules", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { enabled } = req.query;

    let sql = "SELECT * FROM alert_rules";
    const params = [];
    if (enabled !== undefined) {
      sql += " WHERE enabled = ?";
      params.push(enabled === "true" ? 1 : 0);
    }
    sql += " ORDER BY created_at DESC";

    const rules = db.prepare(sql).all(...params);
    res.json({ rules: rules.map(r => ({ ...r, enabled: !!r.enabled })) });
}));

// ── POST /alerts/rules — create a new alert rule ────────────────────

router.post("/rules", wrapRoute("create alert rule", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { name, metric, operator, threshold, window_minutes, agent_filter, cooldown_minutes } = req.body;

    // Validation
    if (!name || typeof name !== "string" || name.trim().length === 0) {
      return res.status(400).json({ error: "name is required" });
    }
    if (!VALID_METRICS.includes(metric)) {
      return res.status(400).json({ error: `Invalid metric. Valid metrics: ${VALID_METRICS.join(", ")}` });
    }
    if (!VALID_OPERATORS.includes(operator)) {
      return res.status(400).json({ error: `Invalid operator. Valid operators: ${VALID_OPERATORS.join(", ")}` });
    }
    if (typeof threshold !== "number" || isNaN(threshold)) {
      return res.status(400).json({ error: "threshold must be a number" });
    }

    // Prevent unbounded rule creation — each rule is evaluated on POST /evaluate
    const ruleCount = db.prepare("SELECT COUNT(*) AS cnt FROM alert_rules").get().cnt;
    if (ruleCount >= MAX_ALERT_RULES) {
      return res.status(409).json({ error: `Maximum of ${MAX_ALERT_RULES} alert rules reached. Delete unused rules first.` });
    }

    // Validate agent_filter length
    if (agent_filter && (typeof agent_filter !== "string" || agent_filter.length > MAX_AGENT_FILTER_LENGTH)) {
      return res.status(400).json({ error: `agent_filter cannot exceed ${MAX_AGENT_FILTER_LENGTH} characters` });
    }

    const ruleId = generateId();
    const now = new Date().toISOString();
    const windowMin = Math.min(Math.max(1, Number(window_minutes) || 60), MAX_WINDOW_MINUTES);
    const cooldownMin = Math.min(Math.max(0, Number(cooldown_minutes) || 15), MAX_COOLDOWN_MINUTES);

    db.prepare(`
      INSERT INTO alert_rules (rule_id, name, metric, operator, threshold, window_minutes, agent_filter, cooldown_minutes, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(ruleId, name.trim().slice(0, MAX_NAME_LENGTH), metric, operator, threshold, windowMin, agent_filter || null, cooldownMin, now, now);

    const rule = db.prepare("SELECT * FROM alert_rules WHERE rule_id = ?").get(ruleId);
    res.status(201).json({ rule: { ...rule, enabled: !!rule.enabled } });
}));

// ── PUT /alerts/rules/:ruleId — update an alert rule ────────────────

router.put("/rules/:ruleId", validateIdParam("ruleId"), wrapRoute("update alert rule", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { ruleId } = req.params;

    const existing = db.prepare("SELECT * FROM alert_rules WHERE rule_id = ?").get(ruleId);
    if (!existing) {
      return res.status(404).json({ error: "Rule not found" });
    }

    const updates = {};
    const { name, metric, operator, threshold, window_minutes, agent_filter, enabled, cooldown_minutes } = req.body;

    if (name !== undefined) updates.name = name.trim().slice(0, MAX_NAME_LENGTH);
    if (metric !== undefined) {
      if (!VALID_METRICS.includes(metric)) {
        return res.status(400).json({ error: `Invalid metric. Valid: ${VALID_METRICS.join(", ")}` });
      }
      updates.metric = metric;
    }
    if (operator !== undefined) {
      if (!VALID_OPERATORS.includes(operator)) {
        return res.status(400).json({ error: `Invalid operator. Valid: ${VALID_OPERATORS.join(", ")}` });
      }
      updates.operator = operator;
    }
    if (threshold !== undefined) updates.threshold = threshold;
    if (window_minutes !== undefined) updates.window_minutes = Math.min(Math.max(1, Number(window_minutes)), MAX_WINDOW_MINUTES);
    if (agent_filter !== undefined) {
      if (agent_filter && (typeof agent_filter !== "string" || agent_filter.length > MAX_AGENT_FILTER_LENGTH)) {
        return res.status(400).json({ error: `agent_filter cannot exceed ${MAX_AGENT_FILTER_LENGTH} characters` });
      }
      updates.agent_filter = agent_filter || null;
    }
    if (enabled !== undefined) updates.enabled = enabled ? 1 : 0;
    if (cooldown_minutes !== undefined) updates.cooldown_minutes = Math.min(Math.max(0, Number(cooldown_minutes)), MAX_COOLDOWN_MINUTES);

    const setClauses = Object.keys(updates).map(k => `${k} = ?`);
    setClauses.push("updated_at = ?");
    const values = [...Object.values(updates), new Date().toISOString(), ruleId];

    db.prepare(`UPDATE alert_rules SET ${setClauses.join(", ")} WHERE rule_id = ?`).run(...values);

    const rule = db.prepare("SELECT * FROM alert_rules WHERE rule_id = ?").get(ruleId);
    res.json({ rule: { ...rule, enabled: !!rule.enabled } });
}));

// ── DELETE /alerts/rules/:ruleId — delete a rule ────────────────────

router.delete("/rules/:ruleId", validateIdParam("ruleId"), wrapRoute("delete alert rule", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { ruleId } = req.params;

    const result = db.prepare("DELETE FROM alert_rules WHERE rule_id = ?").run(ruleId);
    if (result.changes === 0) {
      return res.status(404).json({ error: "Rule not found" });
    }
    res.json({ deleted: true, rule_id: ruleId });
}));

// ── POST /alerts/evaluate — evaluate all enabled rules now ──────────

router.post("/evaluate", wrapRoute("evaluate alerts", async (req, res) => {
    ensureAlertsTable();
    const db = getDb();

    const rules = db.prepare("SELECT * FROM alert_rules WHERE enabled = 1").all();
    const results = [];

    for (const rule of rules) {
      const value = evaluateMetric(rule.metric, rule.window_minutes, rule.agent_filter);
      const triggered = compareValue(value, rule.operator, rule.threshold);

      const result = {
        rule_id: rule.rule_id,
        name: rule.name,
        metric: rule.metric,
        operator: rule.operator,
        threshold: rule.threshold,
        current_value: Math.round(value * 100) / 100,
        triggered,
        window_minutes: rule.window_minutes,
        agent_filter: rule.agent_filter,
      };

      if (triggered) {
        // Check cooldown — don't fire if recently triggered
        const cooldownStart = new Date(Date.now() - rule.cooldown_minutes * 60 * 1000).toISOString();
        const recentAlert = db.prepare(`
          SELECT alert_id FROM alert_events
          WHERE rule_id = ? AND triggered_at >= ?
          ORDER BY triggered_at DESC LIMIT 1
        `).get(rule.rule_id, cooldownStart);

        if (!recentAlert) {
          const alertId = generateId();
          db.prepare(`
            INSERT INTO alert_events (alert_id, rule_id, triggered_at, metric_value, details)
            VALUES (?, ?, ?, ?, ?)
          `).run(alertId, rule.rule_id, new Date().toISOString(), value,
            JSON.stringify({ threshold: rule.threshold, operator: rule.operator, window_minutes: rule.window_minutes }));
          result.alert_id = alertId;
          result.status = "fired";

          // Fire webhooks for this alert
          try {
            result.webhooks = await fireWebhooks({
              alert_id: alertId,
              rule_id: rule.rule_id,
              rule_name: rule.name,
              metric: rule.metric,
              operator: rule.operator,
              threshold: rule.threshold,
              current_value: result.current_value,
              window_minutes: rule.window_minutes,
              agent_filter: rule.agent_filter,
            });
          } catch (whErr) {
            console.error("Webhook delivery error:", whErr);
            result.webhooks = [];
          }
        } else {
          result.status = "cooldown";
        }
      } else {
        result.status = "ok";
      }

      results.push(result);
    }

    const fired = results.filter(r => r.status === "fired").length;
    const cooldown = results.filter(r => r.status === "cooldown").length;
    const ok = results.filter(r => r.status === "ok").length;

    res.json({ evaluated: results.length, fired, cooldown, ok, results });
}));

// ── GET /alerts/events — list alert events (triggered alerts) ───────

router.get("/events", wrapRoute("list alert events", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { rule_id, acknowledged, limit: limitStr, after, before } = req.query;
    const limit = parseLimit(limitStr, 50, 200);

    let sql = `
      SELECT ae.*, ar.name AS rule_name, ar.metric, ar.operator, ar.threshold
      FROM alert_events ae
      JOIN alert_rules ar ON ae.rule_id = ar.rule_id
      WHERE 1=1
    `;
    const params = [];

    if (rule_id) { sql += " AND ae.rule_id = ?"; params.push(rule_id); }
    if (acknowledged !== undefined) { sql += " AND ae.acknowledged = ?"; params.push(acknowledged === "true" ? 1 : 0); }
    if (after) { sql += " AND ae.triggered_at >= ?"; params.push(after); }
    if (before) { sql += " AND ae.triggered_at <= ?"; params.push(before); }

    sql += " ORDER BY ae.triggered_at DESC LIMIT ?";
    params.push(limit);

    const events = db.prepare(sql).all(...params);
    res.json({
      events: events.map(e => ({ ...e, acknowledged: !!e.acknowledged })),
      count: events.length,
    });
}));

// ── PUT /alerts/events/:alertId/acknowledge — ack an alert ──────────

router.put("/events/:alertId/acknowledge", validateIdParam("alertId"), wrapRoute("acknowledge alert", (req, res) => {
    ensureAlertsTable();
    const db = getDb();
    const { alertId } = req.params;

    const result = db.prepare(`
      UPDATE alert_events SET acknowledged = 1, acknowledged_at = ? WHERE alert_id = ?
    `).run(new Date().toISOString(), alertId);

    if (result.changes === 0) {
      return res.status(404).json({ error: "Alert event not found" });
    }
    res.json({ acknowledged: true, alert_id: alertId });
}));

// ── GET /alerts/metrics — list available metrics ────────────────────

router.get("/metrics", wrapRoute("list alert metrics", (req, res) => {
  res.json({
    metrics: VALID_METRICS.map(m => ({
      name: m,
      description: {
        total_tokens: "Total tokens (in+out) across sessions in the time window",
        avg_tokens_per_session: "Average tokens per session in the time window",
        error_rate: "Percentage of error events in the time window (0-100)",
        avg_duration_ms: "Average event duration in milliseconds",
        max_duration_ms: "Maximum event duration in milliseconds",
        session_count: "Number of new sessions in the time window",
        event_count: "Number of events in the time window",
        token_rate: "Tokens per minute in the time window",
      }[m],
    })),
    operators: VALID_OPERATORS,
  });
}));

module.exports = router;
