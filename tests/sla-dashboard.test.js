/**
 * SLA Dashboard (sla.html) — unit tests
 * Tests the dashboard page rendering, interactions, and API integration.
 */

const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "dashboard", "sla.html");
const htmlContent = fs.readFileSync(htmlPath, "utf-8");

describe("SLA Dashboard HTML", () => {
  test("file exists and is valid HTML", () => {
    expect(htmlContent).toContain("<!DOCTYPE html>");
    expect(htmlContent).toContain("<title>AgentLens — SLA Compliance</title>");
  });

  test("includes styles.css link", () => {
    expect(htmlContent).toContain('href="styles.css"');
  });

  test("has navigation links to other dashboards", () => {
    expect(htmlContent).toContain('href="index.html"');
    expect(htmlContent).toContain('href="errors.html"');
    expect(htmlContent).toContain('href="scorecards.html"');
    expect(htmlContent).toContain('href="costs.html"');
  });

  test("has three tabs: Overview, Targets, History", () => {
    expect(htmlContent).toContain("switchTab('overview')");
    expect(htmlContent).toContain("switchTab('targets')");
    expect(htmlContent).toContain("switchTab('history')");
    expect(htmlContent).toContain('id="tab-overview"');
    expect(htmlContent).toContain('id="tab-targets"');
    expect(htmlContent).toContain('id="tab-history"');
  });

  test("has add target form with all fields", () => {
    expect(htmlContent).toContain('id="inputAgent"');
    expect(htmlContent).toContain('id="inputMetric"');
    expect(htmlContent).toContain('id="inputThreshold"');
    expect(htmlContent).toContain('id="inputComparison"');
  });

  test("metric dropdown has all 8 SLA metrics", () => {
    const metrics = [
      "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
      "error_rate_pct", "avg_tokens_in", "avg_tokens_out",
      "max_duration_ms", "min_throughput",
    ];
    for (const m of metrics) {
      expect(htmlContent).toContain(`value="${m}"`);
    }
  });

  test("comparison dropdown has all 5 options", () => {
    for (const c of ["lte", "gte", "lt", "gt", "eq"]) {
      expect(htmlContent).toContain(`value="${c}"`);
    }
  });

  test("has METRIC_LABELS mapping for all metrics", () => {
    expect(htmlContent).toContain("METRIC_LABELS");
    expect(htmlContent).toContain("p50_latency_ms:");
    expect(htmlContent).toContain("min_throughput:");
  });

  test("has COMP_LABELS mapping", () => {
    expect(htmlContent).toContain("COMP_LABELS");
    expect(htmlContent).toContain("lte:");
    expect(htmlContent).toContain("gte:");
  });

  test("has compliance ring SVG rendering", () => {
    expect(htmlContent).toContain("compliance-ring");
    expect(htmlContent).toContain("ring-bg");
    expect(htmlContent).toContain("ring-fg");
    expect(htmlContent).toContain("ring-label");
  });

  test("has API helper function", () => {
    expect(htmlContent).toContain("async function api(");
    expect(htmlContent).toContain("fetch(API + path");
  });

  test("has loadOverview function calling /sla/summary", () => {
    expect(htmlContent).toContain("async function loadOverview");
    expect(htmlContent).toContain("/sla/summary");
  });

  test("has loadTargets function calling /sla/targets", () => {
    expect(htmlContent).toContain("async function loadTargets");
    expect(htmlContent).toContain("/sla/targets");
  });

  test("has addTarget function with PUT method", () => {
    expect(htmlContent).toContain("async function addTarget");
    expect(htmlContent).toContain("method: 'PUT'");
  });

  test("has deleteTarget function with DELETE method", () => {
    expect(htmlContent).toContain("async function deleteTarget");
    expect(htmlContent).toContain("method: 'DELETE'");
  });

  test("has runCheck function calling /sla/check with POST", () => {
    expect(htmlContent).toContain("async function runCheck");
    expect(htmlContent).toContain("/sla/check");
    expect(htmlContent).toContain("method: 'POST'");
  });

  test("has loadHistory function calling /sla/history", () => {
    expect(htmlContent).toContain("async function loadHistory");
    expect(htmlContent).toContain("/sla/history");
  });

  test("has history bar chart rendering", () => {
    expect(htmlContent).toContain("history-bar");
    expect(htmlContent).toContain("history-chart");
  });

  test("has tooltip functionality", () => {
    expect(htmlContent).toContain("function showTip");
    expect(htmlContent).toContain("function hideTip");
    expect(htmlContent).toContain('id="tooltip"');
  });

  test("has refreshAll function", () => {
    expect(htmlContent).toContain("function refreshAll");
  });

  test("has empty state messages", () => {
    expect(htmlContent).toContain("emptyOverview");
    expect(htmlContent).toContain("emptyTargets");
    expect(htmlContent).toContain("emptyHistory");
  });

  test("calls loadOverview on init", () => {
    const scriptMatch = htmlContent.match(/<script[\s\S]*<\/script>/);
    expect(scriptMatch).toBeTruthy();
    const scriptEnd = htmlContent.slice(-500);
    expect(scriptEnd).toContain("loadOverview()");
  });

  test("renderAgentCard handles null compliance", () => {
    expect(htmlContent).toContain("pct === null");
    expect(htmlContent).toContain("badge-none");
    expect(htmlContent).toContain("No data");
  });

  test("renderAgentCard shows violation count", () => {
    expect(htmlContent).toContain("violation_count");
    expect(htmlContent).toContain("violation-item");
  });

  test("targets table has correct columns", () => {
    expect(htmlContent).toContain("<th>Agent</th>");
    expect(htmlContent).toContain("<th>Metric</th>");
    expect(htmlContent).toContain("<th>Comparison</th>");
    expect(htmlContent).toContain("<th>Threshold</th>");
    expect(htmlContent).toContain("<th>Updated</th>");
  });

  test("history agent selector exists", () => {
    expect(htmlContent).toContain('id="historyAgent"');
    expect(htmlContent).toContain('onchange="loadHistory()"');
  });

  test("check log section in history", () => {
    expect(htmlContent).toContain("Check Log");
  });

  test("delete button has confirm dialog", () => {
    expect(htmlContent).toContain("confirm(");
  });

  test("uses dark theme CSS variables", () => {
    expect(htmlContent).toContain("var(--bg-secondary)");
    expect(htmlContent).toContain("var(--green)");
    expect(htmlContent).toContain("var(--red)");
    expect(htmlContent).toContain("var(--accent)");
  });

  test("badge classes for compliance levels", () => {
    expect(htmlContent).toContain("badge-ok");
    expect(htmlContent).toContain("badge-warn");
    expect(htmlContent).toContain("badge-fail");
    expect(htmlContent).toContain("pct >= 100");
    expect(htmlContent).toContain("pct >= 80");
  });

  test("window_hours defaults to 24 for checks", () => {
    expect(htmlContent).toContain("window_hours: 24");
  });
});
