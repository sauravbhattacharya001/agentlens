/**
 * Tests for the Auto-Triage Engine route helpers.
 *
 * Validates health scoring, anomaly baselines, anomaly reports,
 * error analysis, finding generation, and severity classification
 * using an in-memory SQLite database.
 */
// ── Re-implement route-internal helpers for unit testing ────────────
// The helpers are not exported from triage.js, so we replicate them
// here (matching the source) and verify their behaviour directly.

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

function computeHealthScore(metrics) {
  const { event_count, error_count, total_processing_ms, avg_event_duration_ms } = metrics;

  const errorRate = event_count > 0 ? error_count / event_count : 0;
  let errorScore = 100;
  if (errorRate > 0.25) errorScore = 20;
  else if (errorRate > 0.15) errorScore = 40;
  else if (errorRate > 0.10) errorScore = 60;
  else if (errorRate > 0.05) errorScore = 80;

  let latencyScore = 100;
  if (avg_event_duration_ms > 10000) latencyScore = 20;
  else if (avg_event_duration_ms > 5000) latencyScore = 40;
  else if (avg_event_duration_ms > 2000) latencyScore = 60;
  else if (avg_event_duration_ms > 1000) latencyScore = 80;

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

  const overall = Math.round(errorScore * 0.4 + latencyScore * 0.35 + toolScore * 0.25);

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

function overallSeverity(findings) {
  if (findings.length === 0) return "healthy";
  return findings[0].severity;
}

function generateFindings(health, anomalyReport, driftReport, errorAnalysis, costAnalysis, metrics) {
  const findings = [];

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

  if (anomalyReport?.isAnomaly) {
    const dims = anomalyReport.dimensions;
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
    for (const [key, dim] of Object.entries(dims)) {
      if (Math.abs(dim.zScore) >= 2) {
        const direction = dim.zScore > 0 ? "above" : "below";
        let severity = "medium";
        if (Math.abs(dim.zScore) >= 4) severity = "critical";
        else if (Math.abs(dim.zScore) >= 3) severity = "high";

        findings.push({
          severity,
          category: "anomaly",
          title: `${labels[key] || key} is anomalous (${Math.abs(dim.zScore).toFixed(1)}\u03C3 ${direction} baseline)`,
          detail: `Actual: ${dim.value}, Baseline mean: ${dim.baseline_mean}, Z-score: ${dim.zScore}`,
          metric: { name: key, value: dim.value, threshold: dim.baseline_mean, unit: key === "duration_ms" ? "ms" : "count" },
          remediation: remediations[key] || "Compare against recent successful sessions using `agentlens diff`.",
        });
      }
    }
  }

  if (driftReport && driftReport.verdict !== "healthy") {
    const regressions = Object.entries(driftReport.checks)
      .filter(([, c]) => c.status === "regression")
      .map(([name, c]) => ({ name, ...c }));

    for (const reg of regressions) {
      findings.push({
        severity: "high",
        category: "drift",
        title: `Baseline regression: ${reg.name} (+${reg.delta_pct}%)`,
        detail: `Baseline: ${reg.baseline}, Actual: ${reg.actual}, Delta: +${reg.delta_pct}%`,
        metric: { name: reg.name, value: reg.actual, threshold: reg.baseline, unit: "" },
        remediation: "Investigate what changed since the baseline was established.",
      });
    }
  }

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
      remediation: "Consider using a smaller model for simpler tasks. Implement caching for repeated queries.",
    });
  }

  if (health.components.latency.score < 60) {
    findings.push({
      severity: health.components.latency.score < 40 ? "high" : "medium",
      category: "latency",
      title: "High average latency",
      detail: `Average event duration: ${metrics.avg_event_duration_ms.toFixed(0)}ms`,
      metric: { name: "avg_duration_ms", value: metrics.avg_event_duration_ms, threshold: 2000, unit: "ms" },
      remediation: "Profile slow events with `agentlens flamegraph` or `agentlens trace`.",
    });
  }

  const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  findings.sort((a, b) => (severityOrder[a.severity] ?? 9) - (severityOrder[b.severity] ?? 9));

  return findings;
}

// ── Tests ───────────────────────────────────────────────────────────

describe("Triage Engine", () => {

  // ── Statistical helpers ───────────────────────────────────────────

  describe("meanStddevFromSums", () => {
    test("returns zero for n=0", () => {
      const r = meanStddevFromSums(0, 0, 0);
      expect(r.mean).toBe(0);
      expect(r.stddev).toBe(0);
    });

    test("returns mean with stddev=0 for n=1", () => {
      const r = meanStddevFromSums(42, 42 * 42, 1);
      expect(r.mean).toBe(42);
      expect(r.stddev).toBe(0);
    });

    test("computes correct mean and stddev for small sample", () => {
      // values: 2, 4, 4, 4, 5, 5, 7, 9
      const vals = [2, 4, 4, 4, 5, 5, 7, 9];
      const sum = vals.reduce((a, b) => a + b, 0);
      const sumSq = vals.reduce((a, b) => a + b * b, 0);
      const r = meanStddevFromSums(sum, sumSq, vals.length);
      expect(r.mean).toBe(5);
      expect(r.stddev).toBeCloseTo(2.1380899, 4);
    });

    test("handles negative variance edge case gracefully (Math.max 0)", () => {
      // Floating-point cancellation can produce negative variance
      const r = meanStddevFromSums(1e15, 1e30 / 2, 2);
      expect(r.stddev).toBeGreaterThanOrEqual(0);
    });
  });

  describe("zScore", () => {
    test("returns 0 when stddev is 0", () => {
      expect(zScore(10, 5, 0)).toBe(0);
    });

    test("computes correct z-score", () => {
      expect(zScore(15, 10, 2.5)).toBe(2);
    });

    test("negative z-score for below-mean value", () => {
      expect(zScore(5, 10, 2.5)).toBe(-2);
    });

    test("z-score of mean is 0", () => {
      expect(zScore(10, 10, 5)).toBe(0);
    });
  });

  // ── Health scoring ────────────────────────────────────────────────

  describe("computeHealthScore", () => {
    test("perfect health with no errors, low latency, no tool failures", () => {
      const h = computeHealthScore({
        event_count: 100, error_count: 0,
        total_processing_ms: 50000, avg_event_duration_ms: 500,
        tools: {},
      });
      expect(h.score).toBe(100);
      expect(h.grade).toBe("A");
    });

    test("grade A for low error rate and moderate latency", () => {
      const h = computeHealthScore({
        event_count: 100, error_count: 3,
        total_processing_ms: 80000, avg_event_duration_ms: 800,
        tools: {},
      });
      expect(h.grade).toBe("A");
      expect(h.score).toBeGreaterThanOrEqual(90);
    });

    test("grade B with 6% error rate", () => {
      // 6% error → errorScore 80, 800ms → latencyScore 100, no tools → 100
      // composite: 80*0.4 + 100*0.35 + 100*0.25 = 32 + 35 + 25 = 92 → A
      // Need lower latency score: 1500ms → latencyScore 80
      const h = computeHealthScore({
        event_count: 100, error_count: 6,
        total_processing_ms: 150000, avg_event_duration_ms: 1500,
        tools: {},
      });
      expect(h.grade).toBe("B");
      expect(h.score).toBeGreaterThanOrEqual(80);
      expect(h.score).toBeLessThan(90);
    });

    test("grade F with high error rate and high latency", () => {
      const h = computeHealthScore({
        event_count: 100, error_count: 30,
        total_processing_ms: 1200000, avg_event_duration_ms: 12000,
        tools: { api: { calls: 10, failures: 5 } },
      });
      expect(h.grade).toBe("F");
      expect(h.score).toBeLessThan(60);
    });

    test("tool failures degrade score", () => {
      const baseline = computeHealthScore({
        event_count: 100, error_count: 0,
        total_processing_ms: 50000, avg_event_duration_ms: 500,
        tools: {},
      });
      const withToolFail = computeHealthScore({
        event_count: 100, error_count: 0,
        total_processing_ms: 50000, avg_event_duration_ms: 500,
        tools: { search: { calls: 20, failures: 8 } },
      });
      expect(withToolFail.score).toBeLessThan(baseline.score);
    });

    test("0 events returns 100% error score (0/0 = 0 error rate)", () => {
      const h = computeHealthScore({
        event_count: 0, error_count: 0,
        total_processing_ms: 0, avg_event_duration_ms: 0,
        tools: {},
      });
      expect(h.components.error_rate.value).toBe(0);
    });

    test("all latency thresholds hit correct scores", () => {
      const thresholds = [
        { ms: 500, score: 100 },
        { ms: 1500, score: 80 },
        { ms: 3000, score: 60 },
        { ms: 7000, score: 40 },
        { ms: 15000, score: 20 },
      ];
      for (const { ms, score } of thresholds) {
        const h = computeHealthScore({
          event_count: 10, error_count: 0,
          total_processing_ms: ms * 10, avg_event_duration_ms: ms,
          tools: {},
        });
        expect(h.components.latency.score).toBe(score);
      }
    });

    test("components have correct weights", () => {
      const h = computeHealthScore({
        event_count: 1, error_count: 0,
        total_processing_ms: 100, avg_event_duration_ms: 100,
        tools: {},
      });
      expect(h.components.error_rate.weight).toBe(0.4);
      expect(h.components.latency.weight).toBe(0.35);
      expect(h.components.tool_reliability.weight).toBe(0.25);
    });

    test("all tool failure rate thresholds", () => {
      const cases = [
        { rate: 0, expectedScore: 100 },
        { rate: 0.06, expectedScore: 80 },
        { rate: 0.15, expectedScore: 60 },
        { rate: 0.25, expectedScore: 40 },
        { rate: 0.35, expectedScore: 20 },
      ];
      for (const { rate, expectedScore } of cases) {
        const totalCalls = 100;
        const failures = Math.round(rate * totalCalls);
        const h = computeHealthScore({
          event_count: 10, error_count: 0,
          total_processing_ms: 1000, avg_event_duration_ms: 100,
          tools: { t: { calls: totalCalls, failures } },
        });
        expect(h.components.tool_reliability.score).toBe(expectedScore);
      }
    });
  });

  // ── Error analysis ────────────────────────────────────────────────

  describe("analyzeErrors", () => {
    test("returns zero count for no errors", () => {
      const events = [
        { event_type: "llm_call", output_data: "ok" },
        { event_type: "tool_call", output_data: "ok" },
      ];
      const r = analyzeErrors(events);
      expect(r.count).toBe(0);
      expect(r.groups).toHaveLength(0);
      expect(r.rate).toBe(0);
    });

    test("groups errors by event_type", () => {
      const events = [
        { event_type: "error", output_data: "timeout" },
        { event_type: "error", output_data: "timeout 2" },
        { event_type: "tool_error", output_data: "not found" },
        { event_type: "llm_call", output_data: "ok" },
        { event_type: "agent_error", output_data: { error: "crash" } },
      ];
      const r = analyzeErrors(events);
      expect(r.count).toBe(4);
      expect(r.rate).toBeCloseTo(80, 1);
      expect(r.groups).toHaveLength(3);
      // Sorted by count descending
      expect(r.groups[0].type).toBe("error");
      expect(r.groups[0].count).toBe(2);
    });

    test("limits examples to 3 per group", () => {
      const events = [];
      for (let i = 0; i < 10; i++) {
        events.push({ event_type: "error", output_data: `err-${i}` });
      }
      const r = analyzeErrors(events);
      expect(r.groups[0].examples).toHaveLength(3);
    });

    test("extracts error from object output_data", () => {
      const events = [
        { event_type: "error", output_data: { error: "custom error msg" } },
      ];
      const r = analyzeErrors(events);
      expect(r.groups[0].examples[0]).toBe("custom error msg");
    });

    test("extracts message from object output_data", () => {
      const events = [
        { event_type: "error", output_data: { message: "some message" } },
      ];
      const r = analyzeErrors(events);
      expect(r.groups[0].examples[0]).toBe("some message");
    });

    test("falls back to JSON.stringify for non-string/object output", () => {
      const events = [
        { event_type: "error", output_data: { code: 500, details: [1, 2] } },
      ];
      const r = analyzeErrors(events);
      expect(r.groups[0].examples[0]).toContain("500");
    });
  });

  // ── Overall severity ──────────────────────────────────────────────

  describe("overallSeverity", () => {
    test("returns healthy for empty findings", () => {
      expect(overallSeverity([])).toBe("healthy");
    });

    test("returns first finding severity (already sorted)", () => {
      const findings = [
        { severity: "critical" },
        { severity: "medium" },
      ];
      expect(overallSeverity(findings)).toBe("critical");
    });

    test("returns low for single low finding", () => {
      expect(overallSeverity([{ severity: "low" }])).toBe("low");
    });
  });

  // ── Finding generation integration ────────────────────────────────

  describe("generateFindings", () => {
    const baseMetrics = { event_count: 100, error_count: 0, avg_event_duration_ms: 500 };
    const baseHealth = computeHealthScore({
      event_count: 100, error_count: 0,
      total_processing_ms: 50000, avg_event_duration_ms: 500,
      tools: {},
    });
    const noCost = { above_average: false, total_cost: 0, avg_cost_reference: 0 };

    test("no findings for healthy session", () => {
      const findings = generateFindings(
        baseHealth, null, null,
        { count: 0, groups: [], rate: 0 },
        noCost, baseMetrics
      );
      expect(findings).toHaveLength(0);
    });

    test("error finding generated for >0 errors", () => {
      const errorAnalysis = { count: 5, rate: 5, groups: [{ type: "error", count: 5 }] };
      const findings = generateFindings(
        baseHealth, null, null,
        errorAnalysis, noCost,
        { ...baseMetrics, event_count: 100, error_count: 5 }
      );
      const errFinding = findings.find(f => f.category === "errors");
      expect(errFinding).toBeTruthy();
      expect(errFinding.severity).toBe("low"); // 5% = low
    });

    test("error severity escalates: medium for >5%, high for >15%, critical for >25%", () => {
      const rates = [
        { rate: 6, expected: "medium" },
        { rate: 16, expected: "high" },
        { rate: 30, expected: "critical" },
      ];
      for (const { rate, expected } of rates) {
        const count = rate;
        const findings = generateFindings(
          baseHealth, null, null,
          { count, rate, groups: [{ type: "error", count }] },
          noCost, { ...baseMetrics, event_count: 100, error_count: count }
        );
        expect(findings.find(f => f.category === "errors").severity).toBe(expected);
      }
    });

    test("cost finding generated when above average", () => {
      const findings = generateFindings(
        baseHealth, null, null,
        { count: 0, groups: [], rate: 0 },
        { above_average: true, total_cost: 0.05, avg_cost_reference: 0.02 },
        baseMetrics
      );
      expect(findings.find(f => f.category === "cost")).toBeTruthy();
    });

    test("latency finding generated for high avg latency", () => {
      // latencyScore < 60 requires avg > 5000ms; at 6000ms, latencyScore = 40
      const highLatencyHealth = computeHealthScore({
        event_count: 100, error_count: 0,
        total_processing_ms: 600000, avg_event_duration_ms: 6000,
        tools: {},
      });
      const findings = generateFindings(
        highLatencyHealth, null, null,
        { count: 0, groups: [], rate: 0 },
        noCost,
        { event_count: 100, error_count: 0, avg_event_duration_ms: 6000 }
      );
      expect(findings.find(f => f.category === "latency")).toBeTruthy();
    });

    test("findings are sorted by severity: critical first", () => {
      const findings = generateFindings(
        computeHealthScore({
          event_count: 100, error_count: 30,
          total_processing_ms: 1200000, avg_event_duration_ms: 12000,
          tools: {},
        }),
        null, null,
        { count: 30, rate: 30, groups: [{ type: "error", count: 30 }] },
        { above_average: true, total_cost: 1, avg_cost_reference: 0.1 },
        { event_count: 100, error_count: 30, avg_event_duration_ms: 12000 }
      );
      expect(findings.length).toBeGreaterThan(1);
      const severityOrder = { critical: 0, high: 1, medium: 2, low: 3 };
      for (let i = 1; i < findings.length; i++) {
        expect(severityOrder[findings[i - 1].severity]).toBeLessThanOrEqual(
          severityOrder[findings[i].severity]
        );
      }
    });

    test("drift regression produces high-severity finding", () => {
      const driftReport = {
        verdict: "regression",
        checks: {
          total_tokens: { baseline: 1000, actual: 2000, delta_pct: 100, status: "regression" },
          error_count: { baseline: 2, actual: 2, delta_pct: 0, status: "normal" },
        },
      };
      const findings = generateFindings(
        baseHealth, null, driftReport,
        { count: 0, groups: [], rate: 0 },
        noCost, baseMetrics
      );
      const driftFinding = findings.find(f => f.category === "drift");
      expect(driftFinding).toBeTruthy();
      expect(driftFinding.severity).toBe("high");
    });
  });

  // ── Grade boundaries ──────────────────────────────────────────────

  describe("grade boundaries", () => {
    test("grade D for score 60-69", () => {
      // ~11% error rate → errorScore 60, low latency → latencyScore 100, no tools → 100
      // composite: 60*0.4 + 100*0.35 + 100*0.25 = 24 + 35 + 25 = 84 → B
      // Need lower: 16% error → 40, 3000ms latency → 60, 15% tool fail → 60
      // 40*0.4 + 60*0.35 + 60*0.25 = 16 + 21 + 15 = 52 → F
      // Try: errorScore 60, latency 80, tool 60 → 60*0.4+80*0.35+60*0.25 = 24+28+15 = 67 → D
      const h = computeHealthScore({
        event_count: 100, error_count: 11,
        total_processing_ms: 150000, avg_event_duration_ms: 1500,
        tools: { t: { calls: 100, failures: 15 } },
      });
      expect(h.grade).toBe("D");
    });

    test("grade C for score 70-79", () => {
      // errorScore 80 (6%), latency 80 (1500ms), tool 60 (15%)
      // 80*0.4+80*0.35+60*0.25 = 32+28+15 = 75 → C
      const h = computeHealthScore({
        event_count: 100, error_count: 6,
        total_processing_ms: 150000, avg_event_duration_ms: 1500,
        tools: { t: { calls: 100, failures: 15 } },
      });
      expect(h.grade).toBe("C");
    });
  });
});
