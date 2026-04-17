const express = require("express");
const { getDb } = require("../db");
const { sanitizeString } = require("../lib/validation");
const { parsePagination, wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Security limits ─────────────────────────────────────────────────
const MAX_AGENT_NAME_LENGTH = 128;
const MAX_WINDOW_HOURS = 720;      // 30 days max to prevent expensive full-table scans
const MAX_SNAPSHOTS = 10000;       // cap stored snapshots per agent to prevent disk exhaustion

// Validate agent_name: alphanumeric + common separators, sanitized.
// Rejects empty/null and enforces a length cap to prevent abuse.
function validateAgentName(name) {
  if (!name || typeof name !== "string") return null;
  const sanitized = sanitizeString(name, MAX_AGENT_NAME_LENGTH);
  if (!sanitized || sanitized.trim().length === 0) return null;
  return sanitized.trim();
}

// ── Schema bootstrap ────────────────────────────────────────────────

let schemaReady = false;

function ensureSchema() {
  if (schemaReady) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS sla_targets (
      agent_name TEXT NOT NULL,
      metric TEXT NOT NULL CHECK(metric IN (
        'p50_latency_ms', 'p95_latency_ms', 'p99_latency_ms',
        'error_rate_pct', 'avg_tokens_in', 'avg_tokens_out',
        'max_duration_ms', 'min_throughput'
      )),
      threshold REAL NOT NULL,
      comparison TEXT NOT NULL DEFAULT 'lte' CHECK(comparison IN ('lte', 'gte', 'lt', 'gt', 'eq')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (agent_name, metric)
    );

    CREATE TABLE IF NOT EXISTS sla_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      agent_name TEXT NOT NULL,
      window_start TEXT NOT NULL,
      window_end TEXT NOT NULL,
      metrics TEXT NOT NULL DEFAULT '{}',
      violations TEXT NOT NULL DEFAULT '[]',
      compliance_pct REAL NOT NULL DEFAULT 100,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_sla_snapshots_agent ON sla_snapshots(agent_name);
    CREATE INDEX IF NOT EXISTS idx_sla_snapshots_created ON sla_snapshots(created_at);
  `);
  schemaReady = true;
}

// ── Helpers ─────────────────────────────────────────────────────────

const VALID_METRICS = [
  "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
  "error_rate_pct", "avg_tokens_in", "avg_tokens_out",
  "max_duration_ms", "min_throughput",
];
const VALID_COMPARISONS = ["lte", "gte", "lt", "gt", "eq"];

function validateTarget(body) {
  const agentName = validateAgentName(body.agent_name);
  if (!agentName) {
    return "agent_name is required (string, max 128 chars, no control characters)";
  }
  body.agent_name = agentName; // normalize
  if (body.agent_name.length > MAX_AGENT_NAME_LENGTH) {
    return "agent_name is required (string, max 128 chars)";
  }
  if (!body.metric || !VALID_METRICS.includes(body.metric)) {
    return "metric must be one of: " + VALID_METRICS.join(", ");
  }
  if (typeof body.threshold !== "number" || !Number.isFinite(body.threshold)) {
    return "threshold must be a finite number";
  }
  if (body.comparison && !VALID_COMPARISONS.includes(body.comparison)) {
    return "comparison must be one of: " + VALID_COMPARISONS.join(", ");
  }
  return null;
}

function checkViolation(value, threshold, comparison) {
  switch (comparison) {
    case "lte": return value > threshold;
    case "gte": return value < threshold;
    case "lt":  return value >= threshold;
    case "gt":  return value <= threshold;
    case "eq":  return value !== threshold;
    default:    return false;
  }
}

function percentile(arr, pct) {
  if (arr.length === 0) return 0;
  const idx = Math.ceil((pct / 100) * arr.length) - 1;
  return arr[Math.max(0, idx)];
}

function computeAgentMetrics(db, agentName, windowStart, windowEnd) {
  const sessions = db.prepare(`
    SELECT session_id, status FROM sessions
    WHERE agent_name = ? AND started_at >= ? AND started_at < ?
  `).all(agentName, windowStart, windowEnd);

  if (sessions.length === 0) return null;

  const sessionIds = sessions.map(s => s.session_id);
  const placeholders = sessionIds.map(() => "?").join(",");

  const events = db.prepare(`
    SELECT duration_ms, tokens_in, tokens_out, event_type FROM events
    WHERE session_id IN (${placeholders})
    ORDER BY duration_ms ASC
  `).all(...sessionIds);

  const durations = events
    .map(e => e.duration_ms)
    .filter(d => d != null && Number.isFinite(d))
    .sort((a, b) => a - b);

  const errorCount = sessions.filter(s => s.status === "error").length;
  const totalTokensIn = events.reduce((s, e) => s + (e.tokens_in || 0), 0);
  const totalTokensOut = events.reduce((s, e) => s + (e.tokens_out || 0), 0);

  const windowMs = new Date(windowEnd).getTime() - new Date(windowStart).getTime();
  const windowHours = windowMs / 3600000;

  return {
    session_count: sessions.length,
    event_count: events.length,
    p50_latency_ms: percentile(durations, 50),
    p95_latency_ms: percentile(durations, 95),
    p99_latency_ms: percentile(durations, 99),
    error_rate_pct: sessions.length > 0 ? (errorCount / sessions.length) * 100 : 0,
    avg_tokens_in: events.length > 0 ? totalTokensIn / events.length : 0,
    avg_tokens_out: events.length > 0 ? totalTokensOut / events.length : 0,
    max_duration_ms: durations.length > 0 ? durations[durations.length - 1] : 0,
    min_throughput: windowHours > 0 ? sessions.length / windowHours : 0,
  };
}

// ── CRUD: SLA Targets ───────────────────────────────────────────────

router.get("/targets", wrapRoute("list SLA targets", (req, res) => {
  ensureSchema();
  const db = getDb();
  const agent_name = req.query.agent_name ? validateAgentName(req.query.agent_name) : null;

  let rows;
  if (agent_name) {
    rows = db.prepare("SELECT * FROM sla_targets WHERE agent_name = ? ORDER BY metric").all(agent_name);
  } else {
    rows = db.prepare("SELECT * FROM sla_targets ORDER BY agent_name, metric").all();
  }
  res.json({ targets: rows });
}));

router.put("/targets", wrapRoute("upsert SLA target", (req, res) => {
  ensureSchema();
  const db = getDb();
  const err = validateTarget(req.body);
  if (err) return res.status(400).json({ error: err });

  const { agent_name, metric, threshold, comparison } = req.body;
  const comp = comparison || "lte";

  db.prepare(`
    INSERT INTO sla_targets (agent_name, metric, threshold, comparison, updated_at)
    VALUES (?, ?, ?, ?, datetime('now'))
    ON CONFLICT(agent_name, metric) DO UPDATE SET
      threshold = excluded.threshold,
      comparison = excluded.comparison,
      updated_at = datetime('now')
  `).run(agent_name, metric, threshold, comp);

  const row = db.prepare("SELECT * FROM sla_targets WHERE agent_name = ? AND metric = ?").get(agent_name, metric);
  res.json({ target: row });
}));

router.delete("/targets", wrapRoute("delete SLA target", (req, res) => {
  ensureSchema();
  const db = getDb();
  const agent_name = validateAgentName(req.query.agent_name);
  const metric = req.query.metric;
  if (!agent_name || !metric) {
    return res.status(400).json({ error: "agent_name and metric query params required" });
  }
  if (!VALID_METRICS.includes(metric)) {
    return res.status(400).json({ error: "metric must be one of: " + VALID_METRICS.join(", ") });
  }

  const result = db.prepare("DELETE FROM sla_targets WHERE agent_name = ? AND metric = ?").run(agent_name, metric);
  if (result.changes === 0) return res.status(404).json({ error: "Target not found" });
  res.json({ deleted: true });
}));

// ── Compliance Check ────────────────────────────────────────────────

router.post("/check", wrapRoute("check SLA compliance", (req, res) => {
  ensureSchema();
  const db = getDb();
  const agent_name = validateAgentName(req.body.agent_name);
  const { window_hours } = req.body;

  if (!agent_name) {
    return res.status(400).json({ error: "agent_name required (non-empty string)" });
  }
  const hours = Math.min(
    (typeof window_hours === "number" && window_hours > 0) ? window_hours : 24,
    MAX_WINDOW_HOURS
  );

  const now = new Date();
  const windowEnd = now.toISOString();
  const windowStart = new Date(now.getTime() - hours * 3600000).toISOString();

  const targets = db.prepare("SELECT * FROM sla_targets WHERE agent_name = ?").all(agent_name);
  if (targets.length === 0) {
    return res.status(404).json({ error: "No SLA targets defined for agent: " + agent_name });
  }

  const metrics = computeAgentMetrics(db, agent_name, windowStart, windowEnd);
  if (!metrics) {
    return res.json({
      agent_name,
      window_start: windowStart,
      window_end: windowEnd,
      metrics: null,
      violations: [],
      compliance_pct: 100,
      message: "No sessions found in window",
    });
  }

  const violations = [];
  for (const target of targets) {
    const actual = metrics[target.metric];
    if (actual !== undefined && checkViolation(actual, target.threshold, target.comparison)) {
      violations.push({
        metric: target.metric,
        threshold: target.threshold,
        comparison: target.comparison,
        actual: Math.round(actual * 100) / 100,
      });
    }
  }

  const compliancePct = targets.length > 0
    ? Math.round(((targets.length - violations.length) / targets.length) * 10000) / 100
    : 100;

  // Cap stored snapshots per agent to prevent unbounded disk growth.
  // An attacker repeatedly calling /check could otherwise fill the DB.
  const snapshotCount = db.prepare(
    "SELECT COUNT(*) as cnt FROM sla_snapshots WHERE agent_name = ?"
  ).get(agent_name).cnt;
  if (snapshotCount >= MAX_SNAPSHOTS) {
    // Delete oldest snapshots to make room (keep most recent MAX_SNAPSHOTS - 100)
    db.prepare(`
      DELETE FROM sla_snapshots WHERE id IN (
        SELECT id FROM sla_snapshots WHERE agent_name = ?
        ORDER BY created_at ASC LIMIT 100
      )
    `).run(agent_name);
  }

  db.prepare(`
    INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(agent_name, windowStart, windowEnd, JSON.stringify(metrics), JSON.stringify(violations), compliancePct);

  res.json({
    agent_name,
    window_start: windowStart,
    window_end: windowEnd,
    metrics,
    violations,
    compliance_pct: compliancePct,
    status: violations.length === 0 ? "compliant" : "violated",
  });
}));

// ── Snapshot History ─────────────────────────────────────────────────

router.get("/history", wrapRoute("list SLA history", (req, res) => {
  ensureSchema();
  const db = getDb();
  const agent_name = validateAgentName(req.query.agent_name);
  if (!agent_name) return res.status(400).json({ error: "agent_name query param required (non-empty string)" });

  const { limit, offset } = parsePagination(req.query, { defaultLimit: 50, maxLimit: 200 });

  const rows = db.prepare(`
    SELECT * FROM sla_snapshots
    WHERE agent_name = ?
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
  `).all(agent_name, limit, offset);

  const total = db.prepare("SELECT COUNT(*) as cnt FROM sla_snapshots WHERE agent_name = ?").get(agent_name).cnt;

  res.json({
    snapshots: rows.map(r => ({
      ...r,
      metrics: JSON.parse(r.metrics),
      violations: JSON.parse(r.violations),
    })),
    total,
    limit,
    offset,
  });
}));

// ── Summary ──────────────────────────────────────────────────────────

router.get("/summary", wrapRoute("SLA summary", (req, res) => {
  ensureSchema();
  const db = getDb();

  const agents = db.prepare("SELECT DISTINCT agent_name FROM sla_targets ORDER BY agent_name").all();

  const summary = agents.map(({ agent_name }) => {
    const latest = db.prepare(`
      SELECT * FROM sla_snapshots WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1
    `).get(agent_name);

    const targetCount = db.prepare("SELECT COUNT(*) as cnt FROM sla_targets WHERE agent_name = ?").get(agent_name).cnt;

    return {
      agent_name,
      target_count: targetCount,
      latest_check: latest ? {
        compliance_pct: latest.compliance_pct,
        violation_count: JSON.parse(latest.violations).length,
        checked_at: latest.created_at,
        window_start: latest.window_start,
        window_end: latest.window_end,
      } : null,
    };
  });

  res.json({ agents: summary });
}));

module.exports = router;
