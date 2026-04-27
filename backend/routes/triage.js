/**
 * Auto-Triage Engine — unified session diagnostics with prioritized
 * findings and automated remediation suggestions.
 *
 * Runs health scoring, anomaly detection, baseline drift analysis,
 * error grouping, and cost analysis in a single call, returning a
 * prioritized triage report that helps operators quickly identify
 * and fix issues.
 *
 * Routes:
 *   GET /triage/:sessionId  — full auto-triage for a single session
 *   GET /triage/batch       — triage multiple recent sessions
 */

const express = require("express");
const { getDb } = require("../db");
const { isValidSessionId, safeJsonParse } = require("../lib/validation");
const { wrapRoute, parseLimit } = require("../lib/request-helpers");
const { computeSessionMetrics, pctDelta } = require("../lib/session-metrics");
const { loadPricingMap, computeCost } = require("../lib/pricing");

const router = express.Router();

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

// ── Statistical helpers (mirroring anomalies.js) ────────────────────

function meanStddevFromSums(sum, sumSq, n) {
  if (n === 0) return { mean: 0, stddev: 0 };
  const m = sum / n;
  if (n < 2) return { mean: m, stddev: 0 };
  const variance = Math.max(0, (sumSq - n * m * m) / (n - 1));
  return { mean: m, stddev: Math.sqrt(variance) };
}

function zScore(value, m, sd) {
  if (sd === 0) return 0;
  return (value - m) / sd;
}

// ── Health scoring ──────────────────────────────────────────────────

function computeHealthScore(metrics) {
  const { event_count, error_count, total_processing_ms, avg_event_duration_ms } = metrics;

  // Error rate score (0-100)
  const errorRate = event_count > 0 ? error_count / event_count : 0;
  let errorScore = 100;
  if (errorRate > 0.25) errorScore = 20;
  else if (errorRate > 0.15) errorScore = 40;
  else if (errorRate > 0.10) errorScore = 60;
  else if (errorRate > 0.05) errorScore = 80;

  // Latency score (based on average event duration)
  let latencyScore = 100;
  if (avg_event_duration_ms > 10000) latencyScore = 20;
  else if (avg_event_duration_ms > 5000) latencyScore = 40;
  else if (avg_event_duration_ms > 2000) latencyScore = 60;
  else if (avg_event_duration_ms > 1000) latencyScore = 80;

  // Tool failure score
  const toolFailures = Object.values(metrics.tools || {}).reduce(
    (acc, t) => acc + (t.failures || 0), 0
  );
  const totalToolCalls = Object.values(metrics.tools || {}).reduce(
    (acc, t) => acc + (t.calls || 0), 0
  );
  const toolFailRate = totalToolCalls > 0 ? toolFailures / totalToolCalls : 0;
  let toolScore = 100;
  if (toolFailRate > 0.3) toolScore = 20;
  else if (toolFailRate > 0.2) toolScore = 40;
  else if (toolFailRate > 0.1) toolScore = 60;
  else if (toolFailRate > 0.05) toolScore = 80;

  // Weighted composite
  const overall = Math.round(errorScore * 0.4 + latencyScore * 0.35 + toolScore * 0.25);

  // Grade
  let grade;
  if (overall >= 90) grade = "A";
  else if (overall >= 80) grade = "B";
  else if (overall >= 70) grade = "C";
  else if (overall >= 60) grade = "D";
  else grade = "F";

  return {
    score: overall,
    grade,
    components: {
      error_rate: { score: errorScore, value: +(errorRate * 100).toFixed(2), weight: 0.4 },
      latency: { score: latencyScore, value: +avg_event_duration_ms.toFixed(2), weight: 0.35 },
      tool_reliability: { score: toolScore, value: +(toolFailRate * 100).toFixed(2), weight: 0.25 },
    },
  };
}

// ── Anomaly baselines (lightweight, session-level) ──────────────────

function computeAnomalyBaselines(db) {
  const rows = db.prepare(`
    SELECT
      s.session_id,
      s.agent_name,
      s.total_tokens_in + s.total_tokens_out AS total_tokens,
      CAST((julianday(COALESCE(s.ended_at, datetime('now'))) - julianday(s.started_at)) * 86400000 AS INTEGER) AS duration_ms,
      COUNT(e.event_id) AS event_count,
      SUM(CASE WHEN e.event_type = 'error' THEN 1 ELSE 0 END) AS error_count
    FROM sessions s
    LEFT JOIN events e ON e.session_id = s.session_id
    GROUP BY s.session_id
  `).all();

  if (rows.length < 3) return { baselines: null, rowIndex: new Map() };

  const n = rows.length;
  let tokSum = 0, tokSumSq = 0;
  let durSum = 0, durSumSq = 0;
  let evtSum = 0, evtSumSq = 0;
  let errSum = 0, errSumSq = 0;
  const rowIndex = new Map();

  for (const r of rows) {
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

  return {
    baselines: {
      totalTokens: meanStddevFromSums(tokSum, tokSumSq, n),
      duration_ms: meanStddevFromSums(durSum, durSumSq, n),
      eventCount: meanStddevFromSums(evtSum, evtSumSq, n),
      errorCount: meanStddevFromSums(errSum, errSumSq, n),
      sampleSize: n,
    },
    rowIndex,
  };
}

function computeAnomalyReport(sessionId, baselines, rowIndex) {
  if (!baselines) return null;
  const row = rowIndex.get(sessionId);
  if (!row) return null;

  const dims = {};
  const pairs = [
    ["totalTokens", row.total_tokens || 0],
    ["duration_ms", row.duration_ms || 0],
    ["eventCount", row.event_count || 0],
    ["errorCount", row.error_count || 0],
  ];

  let maxAbsZ = 0;
  for (const [key, val] of pairs) {
    const z = +zScore(val, baselines[key].mean, baselines[key].stddev).toFixed(3);
    dims[key] = { value: val, zScore: z, baseline_mean: +baselines[key].mean.toFixed(2) };
    if (Math.abs(z) > maxAbsZ) maxAbsZ = Math.abs(z);
  }

  return {
    isAnomaly: maxAbsZ >= 2,
    maxZScore: +maxAbsZ.toFixed(3),
    dimensions: dims,
  };
}

// ── Baseline drift ──────────────────────────────────────────────────

function checkBaselineDrift(db, agentName, metrics) {
  try {
    const baseline = db.prepare(
      "SELECT * FROM agent_baselines WHERE agent_name = ?"
    ).get(agentName);
    if (!baseline) return null;

    const checks = {};
    const fields = [
      ["total_tokens", "avg_total_tokens", metrics.total_tokens],
      ["tokens_in", "avg_tokens_in", metrics.tokens_in],
      ["tokens_out", "avg_tokens_out", metrics.tokens_out],
      ["event_count", "avg_event_count", metrics.event_count],
      ["error_count", "avg_error_count", metrics.error_count],
      ["processing_ms", "avg_processing_ms", metrics.total_processing_ms],
    ];

    for (const [name, baselineKey, actual] of fields) {
      const baseVal = baseline[baselineKey] || 0;
      const delta = pctDelta(baseVal, actual);
      let status = "normal";
      if (delta > 50) status = "regression";
      else if (delta > 20) status = "warning";
      else if (delta < -20) status = "improvement";
      checks[name] = { baseline: +baseVal.toFixed(2), actual, delta_pct: delta, status };
    }

    const statuses = Object.values(checks).map((c) => c.status);
    let verdict = "healthy";
    if (statuses.includes("regression")) verdict = "regression";
    else if (statuses.includes("warning")) verdict = "warning";

    return { samples: baseline.samples, verdict, checks };
  } catch {
    return null;
  }
}

// ── Error analysis ──────────────────────────────────────────────────

function analyzeErrors(events) {
  const errors = events.filter(
    (e) => e.event_type === "error" || e.event_type === "tool_error" || e.event_type === "agent_error"
  );
  if (errors.length === 0) return { count: 0, groups: [], rate: 0 };

  const groups = {};
  for (const e of errors) {
    const key = e.event_type;
    if (!groups[key]) groups[key] = { type: key, count: 0, examples: [] };
    groups[key].count++;
    if (groups[key].examples.length < 3) {
      const msg = typeof e.output_data === "string" ? e.output_data
        : (e.output_data?.error || e.output_data?.message || JSON.stringify(e.output_data));
      groups[key].examples.push(msg);
    }
  }

  const sorted = Object.values(groups).sort((a, b) => b.count - a.count);
  return {
    count: errors.length,
    rate: +(errors.length / events.length * 100).toFixed(2),
    groups: sorted,
  };
}

// ── Cost analysis ───────────────────────────────────────────────────

function analyzeCost(events, avgCost) {
  const pricingMap = loadPricingMap();
  let totalCost = 0;
  const modelCosts = {};

  for (const e of events) {
    if (!e.model) continue;
    const cost = computeCost(e, pricingMap);
    totalCost += cost;
    if (!modelCosts[e.model]) modelCosts[e.model] = { cost: 0, calls: 0 };
    modelCosts[e.model].cost += cost;
    modelCosts[e.model].calls++;
  }

  return {
    total_cost: +totalCost.toFixed(6),
    model_breakdown: modelCosts,
    above_average: avgCost > 0 ? totalCost > avgCost * 1.5 : false,
    avg_cost_reference: +avgCost.toFixed(6),
  };
}

// ── Finding generators ──────────────────────────────────────────────

function generateFindings(health, anomalyReport, driftReport, errorAnalysis, costAnalysis, metrics) {
  const findings = [];

  // Error findings
  if (errorAnalysis.count > 0) {
    const rate = errorAnalysis.rate;
    let severity = "low";
    if (rate > 25) severity = "critical";
    else if (rate > 15) severity = "high";
    else if (rate > 5) severity = "medium";

    findings.push({
      severity,
      category: "errors",
      title: "Elevated error rate detected",
      detail: `${errorAnalysis.count} errors in ${metrics.event_count} events (${rate}% error rate)`,
      metric: { name: "error_rate", value: rate, threshold: 5, unit: "%" },
      remediation: rate > 15
        ? "Review tool configurations and input validation. Check error logs for recurring failure patterns."
        : "Monitor error frequency. Consider adding retry logic for transient failures.",
    });
  }

  // Anomaly findings
  if (anomalyReport?.isAnomaly) {
    const dims = anomalyReport.dimensions;
    for (const [key, dim] of Object.entries(dims)) {
      if (Math.abs(dim.zScore) >= 2) {
        const direction = dim.zScore > 0 ? "above" : "below";
        let severity = "medium";
        if (Math.abs(dim.zScore) >= 4) severity = "critical";
        else if (Math.abs(dim.zScore) >= 3) severity = "high";

        const labels = {
          totalTokens: "Token usage",
          duration_ms: "Session duration",
          eventCount: "Event count",
          errorCount: "Error count",
        };
        const remediations = {
          totalTokens: "Review prompt sizes and consider using smaller models for simple tasks. Check for unnecessary context in prompts.",
          duration_ms: "Profile slow tool calls with `agentlens trace`. Consider parallelizing independent operations.",
          eventCount: "Check for retry loops or redundant operations. Compare against recent successful sessions.",
          errorCount: "Investigate error patterns with `agentlens postmortem`. Check tool availability and input validation.",
        };

        findings.push({
          severity,
          category: "anomaly",
          title: `${labels[key] || key} is anomalous (${Math.abs(dim.zScore).toFixed(1)}σ ${direction} baseline)`,
          detail: `Actual: ${dim.value}, Baseline mean: ${dim.baseline_mean}, Z-score: ${dim.zScore}`,
          metric: { name: key, value: dim.value, threshold: dim.baseline_mean, unit: key === "duration_ms" ? "ms" : "count" },
          remediation: remediations[key] || "Compare against recent successful sessions using `agentlens diff`.",
        });
      }
    }
  }

  // Drift findings
  if (driftReport && driftReport.verdict !== "healthy") {
    const regressions = Object.entries(driftReport.checks)
      .filter(([, c]) => c.status === "regression")
      .map(([name, c]) => ({ name, ...c }));

    for (const reg of regressions) {
      const remediations = {
        total_tokens: "Token usage has drifted significantly from baseline. Review recent prompt changes.",
        error_count: "Error count regression detected. Check for new failure modes in recent deployments.",
        processing_ms: "Processing time has regressed. Profile with `agentlens flamegraph` to find bottlenecks.",
      };

      findings.push({
        severity: "high",
        category: "drift",
        title: `Baseline regression: ${reg.name} (+${reg.delta_pct}%)`,
        detail: `Baseline: ${reg.baseline}, Actual: ${reg.actual}, Delta: +${reg.delta_pct}%`,
        metric: { name: reg.name, value: reg.actual, threshold: reg.baseline, unit: "" },
        remediation: remediations[reg.name] || "Investigate what changed since the baseline was established.",
      });
    }
  }

  // Cost findings
  if (costAnalysis.above_average) {
    const ratio = costAnalysis.avg_cost_reference > 0
      ? (costAnalysis.total_cost / costAnalysis.avg_cost_reference).toFixed(1)
      : "N/A";
    findings.push({
      severity: "medium",
      category: "cost",
      title: "Cost above average",
      detail: `Session cost $${costAnalysis.total_cost.toFixed(4)} is ${ratio}x the average ($${costAnalysis.avg_cost_reference.toFixed(4)})`,
      metric: { name: "cost", value: costAnalysis.total_cost, threshold: costAnalysis.avg_cost_reference, unit: "USD" },
      remediation: "Consider using a smaller model for simpler tasks. Implement caching for repeated queries. Use `agentlens forecast` to project budget impact.",
    });
  }

  // Latency findings
  if (health.components.latency.score < 60) {
    findings.push({
      severity: health.components.latency.score < 40 ? "high" : "medium",
      category: "latency",
      title: "High average latency",
      detail: `Average event duration: ${metrics.avg_event_duration_ms.toFixed(0)}ms`,
      metric: { name: "avg_duration_ms", value: metrics.avg_event_duration_ms, threshold: 2000, unit: "ms" },
      remediation: "Profile slow events with `agentlens flamegraph` or `agentlens trace`. Consider parallelizing independent tool calls.",
    });
  }

  // Sort by severity priority
  const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  findings.sort((a, b) => (severityOrder[a.severity] ?? 9) - (severityOrder[b.severity] ?? 9));

  return findings;
}

// ── Overall severity ────────────────────────────────────────────────

function overallSeverity(findings) {
  if (findings.length === 0) return "healthy";
  return findings[0].severity;
}

// ── Routes ──────────────────────────────────────────────────────────

// GET /triage/batch — triage multiple recent sessions
router.get("/batch", wrapRoute("triage batch", (req, res) => {
  const db = getDb();
  const limit = parseLimit(req.query.limit, 10, 50);
  const agentFilter = req.query.agent || null;
  const minSeverity = req.query.severity || null;

  const severityOrder = { critical: 0, high: 1, medium: 2, low: 3, healthy: 4 };
  const minSeverityIdx = minSeverity ? (severityOrder[minSeverity] ?? 4) : 4;

  let query = "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?";
  let params = [limit * 3]; // fetch extra to filter
  if (agentFilter) {
    query = "SELECT * FROM sessions WHERE agent_name = ? ORDER BY started_at DESC LIMIT ?";
    params = [agentFilter, limit * 3];
  }

  const sessions = db.prepare(query).all(...params);
  const { baselines, rowIndex } = computeAnomalyBaselines(db);

  // Compute average cost across all sessions for reference
  const allCostRows = db.prepare(
    "SELECT SUM(tokens_in + tokens_out) as total FROM events GROUP BY session_id"
  ).all();
  const pricingMap = loadPricingMap();
  let totalAllCost = 0;
  // Simple approximation: use token totals
  const avgCostPerSession = allCostRows.length > 0
    ? allCostRows.reduce((acc, r) => acc + (r.total || 0), 0) / allCostRows.length * 0.000003
    : 0;

  const results = [];

  for (const session of sessions) {
    if (results.length >= limit) break;

    const events = db.prepare(
      "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT 5000"
    ).all(session.session_id).map(parseEventRow);

    const metrics = computeSessionMetrics(session, events);
    const health = computeHealthScore(metrics);
    const anomalyReport = computeAnomalyReport(session.session_id, baselines, rowIndex);
    const errorAnalysis = analyzeErrors(events);
    const findings = generateFindings(health, anomalyReport, null, errorAnalysis, { above_average: false, total_cost: 0, avg_cost_reference: 0 }, metrics);
    const severity = overallSeverity(findings);

    if ((severityOrder[severity] ?? 4) > minSeverityIdx) continue;

    results.push({
      session_id: session.session_id,
      agent_name: session.agent_name,
      status: session.status,
      started_at: session.started_at,
      overall_severity: severity,
      health_grade: health.grade,
      health_score: health.score,
      finding_count: findings.length,
      top_finding: findings.length > 0 ? findings[0].title : null,
    });
  }

  res.json({
    triaged: results,
    count: results.length,
    triaged_at: new Date().toISOString(),
  });
}));

// GET /triage/:sessionId — full auto-triage for one session
router.get("/:sessionId", wrapRoute("auto-triage session", (req, res) => {
  const db = getDb();
  const { sessionId } = req.params;

  if (!isValidSessionId(sessionId)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(sessionId);
  if (!session) {
    return res.status(404).json({ error: `Session '${sessionId}' not found` });
  }

  const events = db.prepare(
    "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC LIMIT 5000"
  ).all(sessionId).map(parseEventRow);

  if (events.length === 0) {
    return res.json({
      session_id: sessionId,
      agent_name: session.agent_name,
      triage_at: new Date().toISOString(),
      overall_severity: "healthy",
      health_grade: "A",
      health_score: 100,
      summary: "No events to analyze",
      findings: [],
      metrics: {},
      anomaly_report: null,
      baseline_comparison: null,
    });
  }

  // 1. Compute session metrics
  const metrics = computeSessionMetrics(session, events);

  // 2. Health scoring
  const health = computeHealthScore(metrics);

  // 3. Anomaly detection
  const { baselines, rowIndex } = computeAnomalyBaselines(db);
  const anomalyReport = computeAnomalyReport(sessionId, baselines, rowIndex);

  // 4. Baseline drift
  const driftReport = checkBaselineDrift(db, session.agent_name, metrics);

  // 5. Error analysis
  const errorAnalysis = analyzeErrors(events);

  // 6. Cost analysis — compute average cost from recent sessions
  const recentCosts = db.prepare(`
    SELECT SUM(e.tokens_in) as ti, SUM(e.tokens_out) as to_out, e.model
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE s.started_at >= datetime('now', '-7 days')
    GROUP BY e.session_id
  `).all();
  let avgCost = 0;
  if (recentCosts.length > 0) {
    const pricingMap = loadPricingMap();
    let totalCostAll = 0;
    for (const row of recentCosts) {
      totalCostAll += ((row.ti || 0) + (row.to_out || 0)) * 0.000003; // rough estimate
    }
    avgCost = totalCostAll / recentCosts.length;
  }
  const costAnalysis = analyzeCost(events, avgCost);

  // 7. Generate findings and determine overall severity
  const findings = generateFindings(health, anomalyReport, driftReport, errorAnalysis, costAnalysis, metrics);
  const severity = overallSeverity(findings);

  // 8. Build summary
  const critCount = findings.filter((f) => f.severity === "critical").length;
  const highCount = findings.filter((f) => f.severity === "high").length;
  let summary;
  if (findings.length === 0) {
    summary = "Session looks healthy. No issues detected.";
  } else if (critCount > 0) {
    summary = `Session has ${critCount} critical and ${highCount} high-severity findings requiring immediate attention.`;
  } else if (highCount > 0) {
    summary = `Session has ${highCount} high-severity findings that should be investigated.`;
  } else {
    summary = `Session has ${findings.length} minor finding(s) worth reviewing.`;
  }

  res.json({
    session_id: sessionId,
    agent_name: session.agent_name,
    triage_at: new Date().toISOString(),
    overall_severity: severity,
    health_grade: health.grade,
    health_score: health.score,
    summary,
    findings,
    metrics: {
      total_tokens: metrics.total_tokens,
      tokens_in: metrics.tokens_in,
      tokens_out: metrics.tokens_out,
      total_cost: costAnalysis.total_cost,
      error_count: metrics.error_count,
      event_count: metrics.event_count,
      duration_ms: metrics.session_duration_ms,
      avg_event_duration_ms: metrics.avg_event_duration_ms,
      models_used: Object.keys(metrics.models),
      tools_used: Object.keys(metrics.tools),
    },
    anomaly_report: anomalyReport,
    baseline_comparison: driftReport,
    error_analysis: errorAnalysis,
    cost_analysis: costAnalysis,
    health_details: health,
  });
}));

module.exports = router;
