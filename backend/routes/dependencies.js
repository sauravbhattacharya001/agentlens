const express = require("express");
const { getDb } = require("../db");
const { wrapRoute, parseDays, daysAgoCutoff } = require("../lib/request-helpers");
const {
  buildDependencyMap,
  computeServiceStats,
  identifyCriticalDependencies,
  agentDependencyProfiles,
  detectServiceCoOccurrence,
  serviceTrend,
} = require("../lib/dependency-map");

const router = express.Router();

// Use shared parseDays from request-helpers

/**
 * Fetch events with tool_call data for dependency analysis.
 * @param {number} days — lookback window
 * @param {string|null} agent — optional agent filter
 * @returns {Array<object>}
 */
function fetchToolEvents(days, agent) {
  const db = getDb();
  const cutoff = daysAgoCutoff(days);

  let sql = `
    SELECT e.event_id, e.session_id, e.event_type, e.timestamp,
           e.tool_call, e.duration_ms, e.tokens_in, e.tokens_out,
           e.output_data, s.agent_name
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE e.tool_call IS NOT NULL
      AND e.tool_call != ''
      AND e.timestamp >= ?`;
  const params = [cutoff];

  if (agent) {
    sql += " AND s.agent_name = ?";
    params.push(agent);
  }

  sql += " ORDER BY e.timestamp ASC";
  return db.prepare(sql).all(...params);
}

// GET /dependencies — Full service dependency map
router.get("/", wrapRoute("fetch dependency map", (req, res) => {
  const days = parseDays(req.query.days);
  const agent = req.query.agent || null;

  const events = fetchToolEvents(days, agent);
  const rawMap = buildDependencyMap(events);
  const services = computeServiceStats(rawMap);
  const totalCalls = services.reduce((s, d) => s + d.callCount, 0);
  const totalErrors = services.reduce((s, d) => s + d.errorCount, 0);

  res.json({
    period: { days, agent: agent || "all" },
    summary: {
      totalServices: services.length,
      totalCalls,
      totalErrors,
      overallErrorRate:
        totalCalls > 0
          ? Math.round(((totalErrors / totalCalls) * 100) * 100) / 100
          : 0,
    },
    services,
  });
}));

// GET /dependencies/critical — Identify critical dependencies
router.get("/critical", wrapRoute("fetch critical dependencies", (req, res) => {
  const days = parseDays(req.query.days);
  const agent = req.query.agent || null;

  const events = fetchToolEvents(days, agent);
  const rawMap = buildDependencyMap(events);
  const services = computeServiceStats(rawMap);
  const critical = identifyCriticalDependencies(services, {
    criticalSharePct: parseFloat(req.query.critical_share_pct) || 20,
    errorThresholdPct: parseFloat(req.query.error_threshold_pct) || 10,
    latencyThresholdMs: parseFloat(req.query.latency_threshold_ms) || 5000,
  });

  res.json({
    period: { days, agent: agent || "all" },
    criticalCount: critical.length,
    totalServices: services.length,
    critical,
  });
}));

// GET /dependencies/agents — Per-agent dependency profiles
router.get("/agents", wrapRoute("fetch agent dependency profiles", (req, res) => {
  const days = parseDays(req.query.days);
  const events = fetchToolEvents(days, null);
  const profiles = agentDependencyProfiles(events);

  res.json({
    period: { days },
    agentCount: Object.keys(profiles).length,
    profiles,
  });
}));

// GET /dependencies/co-occurrence — Service co-occurrence patterns
router.get("/co-occurrence", wrapRoute("fetch service co-occurrence", (req, res) => {
  const days = parseDays(req.query.days);
  const minOcc = Math.max(1, parseInt(req.query.min_occurrence) || 2);

  const events = fetchToolEvents(days, null);
  const pairs = detectServiceCoOccurrence(events, minOcc);

  res.json({
    period: { days },
    pairCount: pairs.length,
    pairs,
  });
}));

// GET /dependencies/trend/:service — Usage trend for a specific service
router.get("/trend/:service", wrapRoute("fetch service trend", (req, res) => {
  const service = req.params.service;
  if (!service) {
    return res.status(400).json({ error: "Service name is required" });
  }

  const days = parseDays(req.query.days);
  const granularity = req.query.granularity || "day";
  if (!["hour", "day", "week"].includes(granularity)) {
    return res
      .status(400)
      .json({ error: "Invalid granularity. Use hour, day, or week." });
  }

  const events = fetchToolEvents(days, null);
  const trend = serviceTrend(events, service, granularity);

  res.json({
    service,
    period: { days, granularity },
    dataPoints: trend.length,
    trend,
  });
}));

module.exports = router;
