const express = require("express");
const { getDb } = require("../db");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Schema migration ────────────────────────────────────────────────
let _migrated = false;
function ensureSlaTable() {
  if (_migrated) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS sla_definitions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      agent_name TEXT,
      model TEXT,
      target_latency_ms REAL,
      target_error_rate REAL,
      target_token_budget INTEGER,
      target_uptime_pct REAL DEFAULT 99.9,
      window_hours INTEGER NOT NULL DEFAULT 24,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS sla_incidents (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sla_id INTEGER NOT NULL,
      incident_type TEXT NOT NULL,
      started_at TEXT NOT NULL,
      resolved_at TEXT,
      details TEXT DEFAULT '{}',
      FOREIGN KEY (sla_id) REFERENCES sla_definitions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_sla_incidents_sla ON sla_incidents(sla_id);
    CREATE INDEX IF NOT EXISTS idx_sla_incidents_started ON sla_incidents(started_at);
  `);
  _migrated = true;
}

// ── GET /sla — list all SLA definitions with current compliance ─────
router.get("/", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const defs = db.prepare("SELECT * FROM sla_definitions ORDER BY name").all();

  const results = defs.map(d => {
    const compliance = computeCompliance(db, d);
    return { ...d, compliance };
  });

  res.json({ slas: results });
}));

// ── POST /sla — create an SLA definition ────────────────────────────
router.post("/", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const { name, agent_name, model, target_latency_ms, target_error_rate,
          target_token_budget, target_uptime_pct, window_hours } = req.body;

  if (!name || typeof name !== "string" || !name.trim()) {
    return res.status(400).json({ error: "name is required" });
  }

  const existing = db.prepare("SELECT id FROM sla_definitions WHERE name = ?").get(name.trim());
  if (existing) {
    return res.status(409).json({ error: "SLA with this name already exists" });
  }

  const stmt = db.prepare(`
    INSERT INTO sla_definitions (name, agent_name, model, target_latency_ms, target_error_rate,
      target_token_budget, target_uptime_pct, window_hours)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const info = stmt.run(
    name.trim(),
    agent_name || null,
    model || null,
    target_latency_ms ?? null,
    target_error_rate ?? null,
    target_token_budget ?? null,
    target_uptime_pct ?? 99.9,
    window_hours || 24
  );

  const created = db.prepare("SELECT * FROM sla_definitions WHERE id = ?").get(info.lastInsertRowid);
  res.status(201).json(created);
}));

// ── PUT /sla/:id — update an SLA definition ─────────────────────────
router.put("/:id", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) return res.status(400).json({ error: "invalid id" });

  const existing = db.prepare("SELECT * FROM sla_definitions WHERE id = ?").get(id);
  if (!existing) return res.status(404).json({ error: "SLA not found" });

  const fields = ["name", "agent_name", "model", "target_latency_ms", "target_error_rate",
                   "target_token_budget", "target_uptime_pct", "window_hours"];
  const updates = [];
  const values = [];
  for (const f of fields) {
    if (req.body[f] !== undefined) {
      updates.push(`${f} = ?`);
      values.push(req.body[f]);
    }
  }
  if (updates.length === 0) return res.status(400).json({ error: "no fields to update" });

  updates.push("updated_at = datetime('now')");
  values.push(id);
  db.prepare(`UPDATE sla_definitions SET ${updates.join(", ")} WHERE id = ?`).run(...values);

  const updated = db.prepare("SELECT * FROM sla_definitions WHERE id = ?").get(id);
  const compliance = computeCompliance(db, updated);
  res.json({ ...updated, compliance });
}));

// ── DELETE /sla/:id — remove an SLA definition ──────────────────────
router.delete("/:id", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) return res.status(400).json({ error: "invalid id" });

  const info = db.prepare("DELETE FROM sla_definitions WHERE id = ?").run(id);
  if (info.changes === 0) return res.status(404).json({ error: "SLA not found" });
  res.json({ deleted: true });
}));

// ── GET /sla/:id/compliance — detailed compliance report ────────────
router.get("/:id/compliance", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) return res.status(400).json({ error: "invalid id" });

  const def = db.prepare("SELECT * FROM sla_definitions WHERE id = ?").get(id);
  if (!def) return res.status(404).json({ error: "SLA not found" });

  const compliance = computeCompliance(db, def);
  const incidents = db.prepare(
    "SELECT * FROM sla_incidents WHERE sla_id = ? ORDER BY started_at DESC LIMIT 50"
  ).all(id);

  // Hourly compliance over the window
  const hourlyData = computeHourlyCompliance(db, def);

  res.json({ sla: def, compliance, incidents, hourly: hourlyData });
}));

// ── POST /sla/:id/incidents — record a manual incident ──────────────
router.post("/:id/incidents", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) return res.status(400).json({ error: "invalid id" });

  const def = db.prepare("SELECT * FROM sla_definitions WHERE id = ?").get(id);
  if (!def) return res.status(404).json({ error: "SLA not found" });

  const { incident_type, started_at, resolved_at, details } = req.body;
  if (!incident_type) return res.status(400).json({ error: "incident_type is required" });

  const stmt = db.prepare(`
    INSERT INTO sla_incidents (sla_id, incident_type, started_at, resolved_at, details)
    VALUES (?, ?, ?, ?, ?)
  `);
  const info = stmt.run(
    id,
    incident_type,
    started_at || new Date().toISOString(),
    resolved_at || null,
    JSON.stringify(details || {})
  );

  const created = db.prepare("SELECT * FROM sla_incidents WHERE id = ?").get(info.lastInsertRowid);
  res.json(created);
}));

// ── GET /sla/summary — fleet-wide SLA health ────────────────────────
router.get("/summary", wrapRoute("sla", async (req, res) => {
  ensureSlaTable();
  const db = getDb();
  const defs = db.prepare("SELECT * FROM sla_definitions ORDER BY name").all();

  let totalTargets = 0;
  let metTargets = 0;
  let criticalBreaches = [];

  const results = defs.map(d => {
    const compliance = computeCompliance(db, d);
    const checks = compliance.checks || [];
    for (const c of checks) {
      totalTargets++;
      if (c.met) metTargets++;
      else criticalBreaches.push({ sla: d.name, check: c.metric, actual: c.actual, target: c.target });
    }
    return { id: d.id, name: d.name, agent_name: d.agent_name, model: d.model, compliance };
  });

  const overallPct = totalTargets > 0 ? Math.round((metTargets / totalTargets) * 1000) / 10 : 100;
  const health = overallPct >= 95 ? "healthy" : overallPct >= 80 ? "degraded" : "critical";

  res.json({
    summary: { total_slas: defs.length, total_targets: totalTargets, met_targets: metTargets,
               compliance_pct: overallPct, health },
    breaches: criticalBreaches,
    slas: results
  });
}));

// ── Compliance computation helpers ──────────────────────────────────

function computeCompliance(db, sla) {
  const windowStart = new Date(Date.now() - sla.window_hours * 3600 * 1000).toISOString();
  const checks = [];

  // Build session filter
  let sessionFilter = "s.started_at >= ?";
  const sessionParams = [windowStart];
  if (sla.agent_name) {
    sessionFilter += " AND s.agent_name = ?";
    sessionParams.push(sla.agent_name);
  }

  // Get relevant sessions
  const sessions = db.prepare(
    `SELECT s.*, (SELECT COUNT(*) FROM events e WHERE e.session_id = s.session_id) as event_count
     FROM sessions s WHERE ${sessionFilter}`
  ).all(...sessionParams);

  // Build event filter
  let eventFilter = "e.timestamp >= ?";
  const eventParams = [windowStart];
  if (sla.agent_name) {
    eventFilter += ` AND e.session_id IN (SELECT session_id FROM sessions WHERE agent_name = ?)`;
    eventParams.push(sla.agent_name);
  }
  if (sla.model) {
    eventFilter += " AND e.model = ?";
    eventParams.push(sla.model);
  }

  // Latency check
  if (sla.target_latency_ms != null) {
    const latencyStats = db.prepare(`
      SELECT AVG(duration_ms) as avg_ms, 
             MAX(duration_ms) as max_ms,
             COUNT(*) as total,
             SUM(CASE WHEN duration_ms > ? THEN 1 ELSE 0 END) as violations
      FROM events e WHERE ${eventFilter} AND duration_ms IS NOT NULL
    `).get(sla.target_latency_ms, ...eventParams);

    const avgMs = latencyStats?.avg_ms ?? 0;
    const violationPct = latencyStats?.total > 0
      ? Math.round((latencyStats.violations / latencyStats.total) * 1000) / 10
      : 0;
    checks.push({
      metric: "latency",
      target: sla.target_latency_ms,
      actual: Math.round(avgMs * 10) / 10,
      max: latencyStats?.max_ms ?? 0,
      violation_pct: violationPct,
      met: avgMs <= sla.target_latency_ms
    });
  }

  // Error rate check
  if (sla.target_error_rate != null) {
    const totalEvents = db.prepare(
      `SELECT COUNT(*) as cnt FROM events e WHERE ${eventFilter}`
    ).get(...eventParams)?.cnt ?? 0;

    const errorEvents = db.prepare(
      `SELECT COUNT(*) as cnt FROM events e WHERE ${eventFilter} AND e.event_type = 'error'`
    ).get(...eventParams)?.cnt ?? 0;

    const errorRate = totalEvents > 0 ? errorEvents / totalEvents : 0;
    checks.push({
      metric: "error_rate",
      target: sla.target_error_rate,
      actual: Math.round(errorRate * 10000) / 10000,
      total_events: totalEvents,
      error_events: errorEvents,
      met: errorRate <= sla.target_error_rate
    });
  }

  // Token budget check
  if (sla.target_token_budget != null) {
    const tokenSum = db.prepare(`
      SELECT COALESCE(SUM(e.tokens_in + e.tokens_out), 0) as total
      FROM events e WHERE ${eventFilter}
    `).get(...eventParams)?.total ?? 0;

    checks.push({
      metric: "token_budget",
      target: sla.target_token_budget,
      actual: tokenSum,
      utilization_pct: sla.target_token_budget > 0
        ? Math.round((tokenSum / sla.target_token_budget) * 1000) / 10
        : 0,
      met: tokenSum <= sla.target_token_budget
    });
  }

  // Overall
  const allMet = checks.length > 0 && checks.every(c => c.met);
  const status = checks.length === 0 ? "no_targets" : allMet ? "compliant" : "breached";

  return {
    status,
    window_start: windowStart,
    window_hours: sla.window_hours,
    sessions_in_window: sessions.length,
    checks
  };
}

function computeHourlyCompliance(db, sla) {
  const hours = Math.min(sla.window_hours, 168); // cap at 1 week
  const data = [];

  for (let h = hours - 1; h >= 0; h--) {
    const start = new Date(Date.now() - (h + 1) * 3600 * 1000).toISOString();
    const end = new Date(Date.now() - h * 3600 * 1000).toISOString();

    let eventFilter = "e.timestamp >= ? AND e.timestamp < ?";
    const params = [start, end];
    if (sla.agent_name) {
      eventFilter += " AND e.session_id IN (SELECT session_id FROM sessions WHERE agent_name = ?)";
      params.push(sla.agent_name);
    }
    if (sla.model) {
      eventFilter += " AND e.model = ?";
      params.push(sla.model);
    }

    const stats = db.prepare(`
      SELECT COUNT(*) as events,
             AVG(duration_ms) as avg_latency,
             SUM(CASE WHEN event_type = 'error' THEN 1 ELSE 0 END) as errors,
             SUM(tokens_in + tokens_out) as tokens
      FROM events e WHERE ${eventFilter}
    `).get(...params);

    data.push({
      hour: end,
      events: stats?.events ?? 0,
      avg_latency_ms: stats?.avg_latency ? Math.round(stats.avg_latency * 10) / 10 : null,
      errors: stats?.errors ?? 0,
      tokens: stats?.tokens ?? 0
    });
  }

  return data;
}

module.exports = router;

