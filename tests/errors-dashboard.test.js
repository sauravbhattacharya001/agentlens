/**
 * Tests for Error Analytics Dashboard (errors.html).
 *
 * Validates the dashboard renders correctly and handles API data,
 * empty states, and error states.
 */

const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "dashboard", "errors.html"),
  "utf-8"
);

describe("Error Analytics Dashboard", () => {
  test("HTML file exists and has correct title", () => {
    expect(html).toContain("<title>AgentLens — Error Analytics</title>");
  });

  test("includes navigation links to other dashboard pages", () => {
    expect(html).toContain('href="index.html"');
    expect(html).toContain('href="diff.html"');
    expect(html).toContain('href="scorecards.html"');
    expect(html).toContain('href="waterfall.html"');
  });

  test("references shared styles", () => {
    expect(html).toContain('href="styles.css"');
  });

  test("has summary cards section", () => {
    expect(html).toContain('id="summaryCards"');
  });

  test("has error rate chart canvas", () => {
    expect(html).toContain('id="rateChart"');
  });

  test("has by-type and by-model panels", () => {
    expect(html).toContain('id="byType"');
    expect(html).toContain('id="byModel"');
  });

  test("has by-agent panel", () => {
    expect(html).toContain('id="byAgent"');
  });

  test("has hourly heatmap", () => {
    expect(html).toContain('id="heatmap"');
    expect(html).toContain('id="heatLabels"');
  });

  test("has top errors table", () => {
    expect(html).toContain('id="topErrorsBody"');
  });

  test("has error sessions table", () => {
    expect(html).toContain('id="sessionsBody"');
  });

  test("has empty state for zero errors", () => {
    expect(html).toContain("No Errors Found");
    expect(html).toContain("running clean");
  });

  test("has loading state", () => {
    expect(html).toContain("Loading error analytics");
  });

  test("fetches from /errors API endpoint", () => {
    expect(html).toContain("/errors?limit=20&days=30");
  });

  test("displays MTBF metric", () => {
    expect(html).toContain("Mean Time Between Failures");
  });

  test("has type badges for error categories", () => {
    expect(html).toContain("badge-error");
    expect(html).toContain("badge-tool");
    expect(html).toContain("badge-agent");
  });

  test("handles window resize for chart redraw", () => {
    expect(html).toContain("addEventListener('resize'");
  });

  test("has refresh button", () => {
    expect(html).toContain("Refresh");
    expect(html).toContain("refresh-btn");
  });
});
