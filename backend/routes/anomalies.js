/**
 * Anomaly Detector – statistical outlier detection for agent sessions.
 *
 * Computes z-scores across multiple dimensions (tokens, duration, cost,
 * error rate, event count) and flags sessions that exceed configurable
 * thresholds.  Supports per-agent baselines and severity classification.
 *
 * Routes:
 *   GET  /anomalies              – list detected anomalies
 *   GET  /anomalies/stats        – baseline statistics used for detection
 *   GET  /anomalies/session/:id  – anomaly report for a single session
 *   POST /anomalies/scan         – trigger a full scan and return results
 */

const express = require("express");
const { getDb } = require("../db");

const router = express.Router();

// ── Helpers ────────────────────────────────────────────────────────

function mean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function stddev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  return Math.sqrt(arr.reduce((s, v) => s + (v - m) ** 2, 0) / arr.length);
}

function zScore(value, m, sd) {
  if (sd === 0) return 0;
  return (value - m) / sd;
}

function classifySeverity(maxAbsZ) {
  if (maxAbsZ >= 4) return "critical";
  if (maxAbsZ >= 3) return "high";
  if (maxAbsZ >= 2) return "medium";
  return "low";
}

// ── Core detection ─────────────────────────────────────────────────

function computeBaselines(db, agentName) {
  const filter = agentName ? "WHERE s.agent_name = ?" : "";
  const params = agentName ? [agentName] : [];

  const rows = db
    .prepare(
      `SELECT
         s.session_id,
         s.agent_name,
         s.total_tokens_in + s.total_tokens_out AS total_tokens,
         CAST((julianday(COALESCE(s.ended_at, datetime('now'))) - julianday(s.started_at)) * 86400000 AS INTEGER) AS duration_ms,
         COUNT(e.event_id) AS event_count,
         SUM(CASE WHEN e.event_type = 'error' THEN 1 ELSE 0 END) AS error_count
       FROM sessions s
       LEFT JOIN events e ON e.session_id = s.session_id
       ${filter}
       GROUP BY s.session_id`
    )
    .all(...params);

  if (!rows.length) return { rows: [], baselines: null };

  const totalTokens = rows.map((r) => r.total_tokens || 0);
  const durations = rows.map((r) => r.duration_ms || 0);
  const eventCounts = rows.map((r) => r.event_count || 0);
  const errorCounts = rows.map((r) => r.error_count || 0);

  const baselines = {
    totalTokens: { mean: mean(totalTokens), stddev: stddev(totalTokens) },
    duration_ms: { mean: mean(durations), stddev: stddev(durations) },
    eventCount: { mean: mean(eventCounts), stddev: stddev(eventCounts) },
    errorCount: { mean: mean(errorCounts), stddev: stddev(errorCounts) },
    sampleSize: rows.length,
  };

  return { rows, baselines };
}

function detectAnomalies(db, { threshold = 2, agentName, limit = 50 } = {}) {
  const { rows, baselines } = computeBaselines(db, agentName);
  if (!baselines || baselines.sampleSize < 3) {
    return { anomalies: [], baselines, message: "Insufficient data (need ≥3 sessions)" };
  }

  const anomalies = [];

  for (const row of rows) {
    const dimensions = {};
    const tokens = row.total_tokens || 0;
    const dur = row.duration_ms || 0;
    const evts = row.event_count || 0;
    const errs = row.error_count || 0;

    const zTokens = zScore(tokens, baselines.totalTokens.mean, baselines.totalTokens.stddev);
    const zDur = zScore(dur, baselines.duration_ms.mean, baselines.duration_ms.stddev);
    const zEvents = zScore(evts, baselines.eventCount.mean, baselines.eventCount.stddev);
    const zErrors = zScore(errs, baselines.errorCount.mean, baselines.errorCount.stddev);

    if (Math.abs(zTokens) >= threshold) dimensions.totalTokens = { value: tokens, zScore: +zTokens.toFixed(3) };
    if (Math.abs(zDur) >= threshold) dimensions.duration_ms = { value: dur, zScore: +zDur.toFixed(3) };
    if (Math.abs(zEvents) >= threshold) dimensions.eventCount = { value: evts, zScore: +zEvents.toFixed(3) };
    if (Math.abs(zErrors) >= threshold) dimensions.errorCount = { value: errs, zScore: +zErrors.toFixed(3) };

    if (Object.keys(dimensions).length > 0) {
      const maxAbsZ = Math.max(...Object.values(dimensions).map((d) => Math.abs(d.zScore)));
      anomalies.push({
        session_id: row.session_id,
        agent_name: row.agent_name,
        severity: classifySeverity(maxAbsZ),
        maxZScore: +maxAbsZ.toFixed(3),
        dimensions,
      });
    }
  }

  anomalies.sort((a, b) => b.maxZScore - a.maxZScore);
  const limited = anomalies.slice(0, limit);

  return { anomalies: limited, baselines, total: anomalies.length };
}

// ── Routes ─────────────────────────────────────────────────────────

router.get("/", (req, res) => {
  try {
    const db = getDb();
    const threshold = parseFloat(req.query.threshold) || 2;
    const agentName = req.query.agent || undefined;
    const limit = parseInt(req.query.limit, 10) || 50;

    const result = detectAnomalies(db, { threshold, agentName, limit });
    res.json(result);
  } catch (err) {
    console.error("Anomaly detection error:", err);
    res.status(500).json({ error: "Anomaly detection failed" });
  }
});

router.get("/stats", (req, res) => {
  try {
    const db = getDb();
    const agentName = req.query.agent || undefined;
    const { baselines } = computeBaselines(db, agentName);
    if (!baselines) return res.json({ baselines: null, message: "No sessions found" });
    res.json({ baselines });
  } catch (err) {
    console.error("Baseline stats error:", err);
    res.status(500).json({ error: "Failed to compute baselines" });
  }
});

router.get("/session/:id", (req, res) => {
  try {
    const db = getDb();
    const sessionId = req.params.id;
    const agentName = req.query.agent || undefined;

    const { rows, baselines } = computeBaselines(db, agentName);
    if (!baselines || baselines.sampleSize < 3) {
      return res.json({ anomaly: null, baselines, message: "Insufficient data" });
    }

    const row = rows.find((r) => r.session_id === sessionId);
    if (!row) return res.status(404).json({ error: "Session not found" });

    const tokens = row.total_tokens || 0;
    const dur = row.duration_ms || 0;
    const evts = row.event_count || 0;
    const errs = row.error_count || 0;

    const dimensions = {
      totalTokens: { value: tokens, zScore: +zScore(tokens, baselines.totalTokens.mean, baselines.totalTokens.stddev).toFixed(3) },
      duration_ms: { value: dur, zScore: +zScore(dur, baselines.duration_ms.mean, baselines.duration_ms.stddev).toFixed(3) },
      eventCount: { value: evts, zScore: +zScore(evts, baselines.eventCount.mean, baselines.eventCount.stddev).toFixed(3) },
      errorCount: { value: errs, zScore: +zScore(errs, baselines.errorCount.mean, baselines.errorCount.stddev).toFixed(3) },
    };

    const maxAbsZ = Math.max(...Object.values(dimensions).map((d) => Math.abs(d.zScore)));

    res.json({
      session_id: sessionId,
      agent_name: row.agent_name,
      isAnomaly: maxAbsZ >= 2,
      severity: classifySeverity(maxAbsZ),
      maxZScore: +maxAbsZ.toFixed(3),
      dimensions,
      baselines,
    });
  } catch (err) {
    console.error("Session anomaly error:", err);
    res.status(500).json({ error: "Session anomaly check failed" });
  }
});

router.post("/scan", (req, res) => {
  try {
    const db = getDb();
    const threshold = parseFloat(req.body?.threshold) || 2;
    const agentName = req.body?.agent || undefined;
    const limit = parseInt(req.body?.limit, 10) || 100;

    const result = detectAnomalies(db, { threshold, agentName, limit });
    res.json({ ...result, scannedAt: new Date().toISOString() });
  } catch (err) {
    console.error("Anomaly scan error:", err);
    res.status(500).json({ error: "Anomaly scan failed" });
  }
});

module.exports = router;
