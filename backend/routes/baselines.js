const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, safeJsonParse } = require("../lib/validation");
const { computeSessionMetrics, pctDelta } = require("../lib/session-metrics");
const { wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Schema migration ────────────────────────────────────────────────
let _migrated = false;
function ensureBaselineTable() {
  if (_migrated) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS agent_baselines (
      agent_name TEXT PRIMARY KEY,
      samples INTEGER NOT NULL DEFAULT 0,
      avg_tokens_in REAL NOT NULL DEFAULT 0,
      avg_tokens_out REAL NOT NULL DEFAULT 0,
      avg_total_tokens REAL NOT NULL DEFAULT 0,
      avg_event_count REAL NOT NULL DEFAULT 0,
      avg_error_count REAL NOT NULL DEFAULT 0,
      avg_processing_ms REAL NOT NULL DEFAULT 0,
      avg_duration_ms REAL,
      p95_total_tokens REAL,
      p95_processing_ms REAL,
      recent_session_ids TEXT NOT NULL DEFAULT '[]',
      updated_at TEXT NOT NULL
    );
  `);
  _migrated = true;
}

// ── Helpers ─────────────────────────────────────────────────────────

function parseEventRow(e) {
  return {
    ...e,
    input_data: safeJsonParse(e.input_data),
    output_data: safeJsonParse(e.output_data),
    tool_call: safeJsonParse(e.tool_call, null),
    decision_trace: safeJsonParse(e.decision_trace, null),
  };
}

/**
 * Compute a running average update.
 * @param {number} oldAvg  Current average.
 * @param {number} n       Sample count BEFORE this new value.
 * @param {number} newVal  New observed value.
 * @returns {number} Updated average.
 */
function runningAvg(oldAvg, n, newVal) {
  return (oldAvg * n + newVal) / (n + 1);
}

/**
 * Classify a delta relative to baseline using thresholds.
 * @param {number} pct  Percentage change.
 * @returns {string} "normal" | "warning" | "regression" | "improvement"
 */
function classifyDelta(pct) {
  if (pct > 50) return "regression";
  if (pct > 20) return "warning";
  if (pct < -20) return "improvement";
  return "normal";
}

// ── Routes ──────────────────────────────────────────────────────────

// GET /baselines — List all agent baselines
router.get("/", wrapRoute("list baselines", (_req, res) => {
  ensureBaselineTable();
  const db = getDb();
  const rows = db.prepare("SELECT * FROM agent_baselines ORDER BY agent_name").all();
  const baselines = rows.map((r) => ({
    ...r,
    recent_session_ids: safeJsonParse(r.recent_session_ids, []),
  }));
  res.json({ baselines, count: baselines.length });
}));

// GET /baselines/:agentName — Get baseline for a specific agent
router.get("/:agentName", wrapRoute("get baseline", (req, res) => {
  ensureBaselineTable();
  const db = getDb();
  const { agentName } = req.params;

  const row = db.prepare("SELECT * FROM agent_baselines WHERE agent_name = ?").get(agentName);
  if (!row) {
    return res.status(404).json({ error: `No baseline found for agent '${agentName}'` });
  }

  res.json({
    ...row,
    recent_session_ids: safeJsonParse(row.recent_session_ids, []),
  });
}));

// POST /baselines/record — Record a completed session into its agent's baseline
// Body: { session_id: string }
router.post("/record", wrapRoute("record baseline", (req, res) => {
  ensureBaselineTable();
  const db = getDb();
  const { session_id } = req.body;

  if (!session_id) {
    return res.status(400).json({ error: "session_id is required" });
  }
  if (!isValidSessionId(session_id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(session_id);
  if (!session) {
    return res.status(404).json({ error: `Session '${session_id}' not found` });
  }

  const events = db.prepare(
    "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT 5000"
  ).all(session_id).map(parseEventRow);

  const metrics = computeSessionMetrics(session, events);
  const agentName = session.agent_name;
  const now = new Date().toISOString();

  const existing = db.prepare("SELECT * FROM agent_baselines WHERE agent_name = ?").get(agentName);

  if (existing) {
    const n = existing.samples;
    const recentIds = safeJsonParse(existing.recent_session_ids, []);
    // Keep last 20 session IDs
    recentIds.push(session_id);
    if (recentIds.length > 20) recentIds.shift();

    db.prepare(`
      UPDATE agent_baselines SET
        samples = samples + 1,
        avg_tokens_in = ?,
        avg_tokens_out = ?,
        avg_total_tokens = ?,
        avg_event_count = ?,
        avg_error_count = ?,
        avg_processing_ms = ?,
        avg_duration_ms = ?,
        recent_session_ids = ?,
        updated_at = ?
      WHERE agent_name = ?
    `).run(
      Math.round(runningAvg(existing.avg_tokens_in, n, metrics.tokens_in) * 100) / 100,
      Math.round(runningAvg(existing.avg_tokens_out, n, metrics.tokens_out) * 100) / 100,
      Math.round(runningAvg(existing.avg_total_tokens, n, metrics.total_tokens) * 100) / 100,
      Math.round(runningAvg(existing.avg_event_count, n, metrics.event_count) * 100) / 100,
      Math.round(runningAvg(existing.avg_error_count, n, metrics.error_count) * 100) / 100,
      Math.round(runningAvg(existing.avg_processing_ms, n, metrics.total_processing_ms) * 100) / 100,
      metrics.session_duration_ms != null
        ? Math.round(runningAvg(existing.avg_duration_ms || 0, n, metrics.session_duration_ms) * 100) / 100
        : existing.avg_duration_ms,
      JSON.stringify(recentIds),
      now,
      agentName,
    );
  } else {
    db.prepare(`
      INSERT INTO agent_baselines
        (agent_name, samples, avg_tokens_in, avg_tokens_out, avg_total_tokens,
         avg_event_count, avg_error_count, avg_processing_ms, avg_duration_ms,
         recent_session_ids, updated_at)
      VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      agentName,
      metrics.tokens_in,
      metrics.tokens_out,
      metrics.total_tokens,
      metrics.event_count,
      metrics.error_count,
      metrics.total_processing_ms,
      metrics.session_duration_ms,
      JSON.stringify([session_id]),
      now,
    );
  }

  res.status(201).json({
    message: `Baseline updated for agent '${agentName}'`,
    agent_name: agentName,
    samples: (existing?.samples || 0) + 1,
  });
}));

// POST /baselines/check — Check a session against its agent's baseline
// Body: { session_id: string }
// Returns per-metric deltas with classification (normal/warning/regression/improvement)
router.post("/check", wrapRoute("check against baseline", (req, res) => {
  ensureBaselineTable();
  const db = getDb();
  const { session_id } = req.body;

  if (!session_id) {
    return res.status(400).json({ error: "session_id is required" });
  }
  if (!isValidSessionId(session_id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(session_id);
  if (!session) {
    return res.status(404).json({ error: `Session '${session_id}' not found` });
  }

  const baseline = db.prepare(
    "SELECT * FROM agent_baselines WHERE agent_name = ?"
  ).get(session.agent_name);

  if (!baseline) {
    return res.status(404).json({
      error: `No baseline exists for agent '${session.agent_name}'. Record some sessions first.`,
    });
  }

  const events = db.prepare(
    "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT 5000"
  ).all(session_id).map(parseEventRow);

  const metrics = computeSessionMetrics(session, events);

  const checks = {
    total_tokens: {
      baseline: baseline.avg_total_tokens,
      actual: metrics.total_tokens,
      delta_pct: pctDelta(baseline.avg_total_tokens, metrics.total_tokens),
      status: classifyDelta(pctDelta(baseline.avg_total_tokens, metrics.total_tokens)),
    },
    tokens_in: {
      baseline: baseline.avg_tokens_in,
      actual: metrics.tokens_in,
      delta_pct: pctDelta(baseline.avg_tokens_in, metrics.tokens_in),
      status: classifyDelta(pctDelta(baseline.avg_tokens_in, metrics.tokens_in)),
    },
    tokens_out: {
      baseline: baseline.avg_tokens_out,
      actual: metrics.tokens_out,
      delta_pct: pctDelta(baseline.avg_tokens_out, metrics.tokens_out),
      status: classifyDelta(pctDelta(baseline.avg_tokens_out, metrics.tokens_out)),
    },
    event_count: {
      baseline: baseline.avg_event_count,
      actual: metrics.event_count,
      delta_pct: pctDelta(baseline.avg_event_count, metrics.event_count),
      status: classifyDelta(pctDelta(baseline.avg_event_count, metrics.event_count)),
    },
    error_count: {
      baseline: baseline.avg_error_count,
      actual: metrics.error_count,
      delta_pct: pctDelta(baseline.avg_error_count, metrics.error_count),
      status: classifyDelta(pctDelta(baseline.avg_error_count, metrics.error_count)),
    },
    processing_ms: {
      baseline: baseline.avg_processing_ms,
      actual: metrics.total_processing_ms,
      delta_pct: pctDelta(baseline.avg_processing_ms, metrics.total_processing_ms),
      status: classifyDelta(pctDelta(baseline.avg_processing_ms, metrics.total_processing_ms)),
    },
  };

  // Overall verdict
  const statuses = Object.values(checks).map((c) => c.status);
  let verdict = "healthy";
  if (statuses.includes("regression")) verdict = "regression";
  else if (statuses.includes("warning")) verdict = "warning";
  else if (statuses.every((s) => s === "improvement")) verdict = "improved";

  res.json({
    session_id,
    agent_name: session.agent_name,
    baseline_samples: baseline.samples,
    verdict,
    checks,
    checked_at: new Date().toISOString(),
  });
}));

// DELETE /baselines/:agentName — Reset baseline for an agent
router.delete("/:agentName", wrapRoute("delete baseline", (req, res) => {
  ensureBaselineTable();
  const db = getDb();
  const { agentName } = req.params;

  const result = db.prepare("DELETE FROM agent_baselines WHERE agent_name = ?").run(agentName);
  if (result.changes === 0) {
    return res.status(404).json({ error: `No baseline found for agent '${agentName}'` });
  }

  res.json({ message: `Baseline deleted for agent '${agentName}'` });
}));

module.exports = router;
