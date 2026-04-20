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
const { parseLimit, wrapRoute } = require("../lib/request-helpers");
const { isValidSessionId } = require("../lib/validation");

const router = express.Router();

// ── Helpers ────────────────────────────────────────────────────────

function mean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function stddev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  // Bessel's correction: divide by (n-1) for sample standard deviation.
  // Using population stddev (n) underestimates variance and inflates
  // z-scores, causing false-positive anomaly detections.
  return Math.sqrt(arr.reduce((s, v) => s + (v - m) ** 2, 0) / (arr.length - 1));
}

/**
 * Compute mean and sample standard deviation from pre-accumulated sums.
 * Avoids the multi-pass approach (extract arrays → mean → stddev) by
 * using the algebraic identity: Var = (ΣX² - n·μ²) / (n-1).
 *
 * This lets computeBaselines() accumulate sums in a single loop over
 * all rows instead of 4 array extractions + 8 reduction passes.
 *
 * @param {number} sum   - Sum of values (ΣX).
 * @param {number} sumSq - Sum of squared values (ΣX²).
 * @param {number} n     - Number of values.
 * @returns {{ mean: number, stddev: number }}
 */
function meanStddevFromSums(sum, sumSq, n) {
  if (n === 0) return { mean: 0, stddev: 0 };
  const m = sum / n;
  if (n < 2) return { mean: m, stddev: 0 };
  // Var = (sumSq - n * mean^2) / (n - 1)  [Bessel's correction]
  const variance = Math.max(0, (sumSq - n * m * m) / (n - 1));
  return { mean: m, stddev: Math.sqrt(variance) };
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

// ── Baseline cache ─────────────────────────────────────────────────
// Baselines change slowly (new sessions trickle in) but are expensive
// to recompute — they scan every session + join events. Cache them for
// a short window to avoid redundant full-table scans on rapid polling
// or multi-tab dashboards hitting /anomalies, /anomalies/stats, and
// /anomalies/session/:id in quick succession.

const BASELINE_CACHE_TTL_MS = 15000; // 15 seconds
const _baselineCache = new Map(); // key: agentName||"__all__" → { baselines, rows, ts }

function _baselineCacheKey(agentName) {
  return agentName || "__all__";
}

function _getCachedBaselines(agentName) {
  const key = _baselineCacheKey(agentName);
  const entry = _baselineCache.get(key);
  if (entry && Date.now() - entry.ts < BASELINE_CACHE_TTL_MS) {
    return entry;
  }
  _baselineCache.delete(key);
  return null;
}

function _setCachedBaselines(agentName, rows, baselines, rowIndex) {
  const key = _baselineCacheKey(agentName);
  // Cap cache size to prevent unbounded growth from many agent filters
  if (_baselineCache.size >= 50) {
    const oldest = _baselineCache.keys().next().value;
    _baselineCache.delete(oldest);
  }
  _baselineCache.set(key, { rows, baselines, rowIndex, ts: Date.now() });
}

// ── Shared dimension computation ───────────────────────────────────
// Extracts z-score dimensions for a single row against baselines.
// Used by both detectAnomalies() and the /session/:id endpoint,
// eliminating duplicated inline z-score calculations.

function computeDimensions(row, baselines) {
  const tokens = row.total_tokens || 0;
  const dur = row.duration_ms || 0;
  const evts = row.event_count || 0;
  const errs = row.error_count || 0;

  return {
    totalTokens: { value: tokens, zScore: +zScore(tokens, baselines.totalTokens.mean, baselines.totalTokens.stddev).toFixed(3) },
    duration_ms: { value: dur, zScore: +zScore(dur, baselines.duration_ms.mean, baselines.duration_ms.stddev).toFixed(3) },
    eventCount: { value: evts, zScore: +zScore(evts, baselines.eventCount.mean, baselines.eventCount.stddev).toFixed(3) },
    errorCount: { value: errs, zScore: +zScore(errs, baselines.errorCount.mean, baselines.errorCount.stddev).toFixed(3) },
  };
}

// ── Core detection ─────────────────────────────────────────────────

function computeBaselines(db, agentName) {
  // Return cached baselines if still fresh
  const cached = _getCachedBaselines(agentName);
  if (cached) return { rows: cached.rows, baselines: cached.baselines, rowIndex: cached.rowIndex };
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

  if (!rows.length) return { rows: [], baselines: null, rowIndex: new Map() };

  // Single-pass baseline computation: accumulate sums and sum-of-squares
  // in one iteration instead of 4 array extractions + 8 reduction passes
  // (mean + stddev each iterate the full array per dimension). Reduces
  // from ~16 passes to 1 pass over the rows — significant when session
  // count is large (thousands of sessions).
  const n = rows.length;
  let tokSum = 0, tokSumSq = 0;
  let durSum = 0, durSumSq = 0;
  let evtSum = 0, evtSumSq = 0;
  let errSum = 0, errSumSq = 0;
  const rowIndex = new Map();

  for (let i = 0; i < n; i++) {
    const r = rows[i];
    const tok = r.total_tokens || 0;
    const dur = r.duration_ms || 0;
    const evt = r.event_count || 0;
    const err = r.error_count || 0;

    tokSum += tok; tokSumSq += tok * tok;
    durSum += dur; durSumSq += dur * dur;
    evtSum += evt; evtSumSq += evt * evt;
    errSum += err; errSumSq += err * err;

    rowIndex.set(r.session_id, r);
  }

  const baselines = {
    totalTokens: meanStddevFromSums(tokSum, tokSumSq, n),
    duration_ms: meanStddevFromSums(durSum, durSumSq, n),
    eventCount: meanStddevFromSums(evtSum, evtSumSq, n),
    errorCount: meanStddevFromSums(errSum, errSumSq, n),
    sampleSize: n,
  };

  _setCachedBaselines(agentName, rows, baselines, rowIndex);
  return { rows, baselines, rowIndex };
}

function detectAnomalies(db, { threshold = 2, agentName, limit = 50 } = {}) {
  const { rows, baselines } = computeBaselines(db, agentName);
  if (!baselines || baselines.sampleSize < 3) {
    return { anomalies: [], baselines, message: "Insufficient data (need ≥3 sessions)" };
  }

  const anomalies = [];

  for (const row of rows) {
    const allDims = computeDimensions(row, baselines);
    // Filter to only dimensions exceeding the threshold
    const dimensions = {};
    for (const [key, dim] of Object.entries(allDims)) {
      if (Math.abs(dim.zScore) >= threshold) {
        dimensions[key] = dim;
      }
    }

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

router.get("/", wrapRoute("detect anomalies", (req, res) => {
  const db = getDb();
  const threshold = Math.min(Math.max(0.5, parseFloat(req.query.threshold) || 2), 10);
  const rawAgent = req.query.agent || undefined;
  const agentName = rawAgent ? validateAgentParam(rawAgent) : undefined;
  if (rawAgent && agentName === null) {
    return res.status(400).json({ error: "Invalid agent name format" });
  }
  const limit = parseLimit(req.query.limit, 50, 500);

  const result = detectAnomalies(db, { threshold, agentName, limit });
  res.json(result);
}));

router.get("/stats", wrapRoute("compute baseline stats", (req, res) => {
  const db = getDb();
  const rawAgent = req.query.agent || undefined;
  const agentName = rawAgent ? validateAgentParam(rawAgent) : undefined;
  if (rawAgent && agentName === null) {
    return res.status(400).json({ error: "Invalid agent name format" });
  }
  const { baselines } = computeBaselines(db, agentName);
  if (!baselines) return res.json({ baselines: null, message: "No sessions found" });
  res.json({ baselines });
}));

// ── Input validation ────────────────────────────────────────────────
// Agent names from query parameters are user-controlled and must be
// bounded to prevent DoS via excessively long strings that inflate
// memory usage in cache keys and SQL parameter buffers.
const MAX_AGENT_NAME_LENGTH = 128;
const SAFE_AGENT_RE = /^[\w .:\-@/]{1,128}$/;

function validateAgentParam(agent) {
  if (!agent) return undefined;
  if (typeof agent !== "string" || agent.length > MAX_AGENT_NAME_LENGTH) return null;
  return SAFE_AGENT_RE.test(agent) ? agent : null;
}

router.get("/session/:id", wrapRoute("check session anomaly", (req, res) => {
  const db = getDb();
  const sessionId = req.params.id;

  // Validate session ID format to prevent arbitrary strings from
  // reaching the database layer and polluting baseline cache keys.
  if (!isValidSessionId(sessionId)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  const rawAgent = req.query.agent || undefined;
  const agentName = rawAgent ? validateAgentParam(rawAgent) : undefined;
  if (rawAgent && agentName === null) {
    return res.status(400).json({ error: "Invalid agent name format" });
  }

  const { rows, baselines, rowIndex } = computeBaselines(db, agentName);
  if (!baselines || baselines.sampleSize < 3) {
    return res.json({ anomaly: null, baselines, message: "Insufficient data" });
  }

  const row = rowIndex.get(sessionId);
  if (!row) return res.status(404).json({ error: "Session not found" });

  const dimensions = computeDimensions(row, baselines);
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
}));

router.post("/scan", wrapRoute("scan for anomalies", (req, res) => {
  const db = getDb();
  const threshold = Math.min(Math.max(0.5, parseFloat(req.body?.threshold) || 2), 10);
  const rawAgent = req.body?.agent || undefined;
  const agentName = rawAgent ? validateAgentParam(rawAgent) : undefined;
  if (rawAgent && agentName === null) {
    return res.status(400).json({ error: "Invalid agent name format" });
  }
  // Explicit scan should bypass cache for fresh results
  _baselineCache.delete(_baselineCacheKey(agentName));
  const limit = parseLimit(String(req.body?.limit || 100), 100, 500);

  const result = detectAnomalies(db, { threshold, agentName, limit });
  res.json({ ...result, scannedAt: new Date().toISOString() });
}));

module.exports = router;
