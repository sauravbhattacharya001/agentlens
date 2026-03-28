/**
 * Tests for dashboard/scorecards.html — Agent Scorecards Dashboard
 *
 * Validates HTML structure, JS functions, scorecard grid,
 * detail modal, filtering, and rendering helpers.
 */

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { describe, it } = require("node:test");

const htmlPath = path.join(__dirname, "..", "dashboard", "scorecards.html");
const html = fs.readFileSync(htmlPath, "utf8");

describe("Scorecards Dashboard HTML Structure", () => {
  it("contains required layout elements", () => {
    assert.ok(html.includes('id="scorecardGrid"'), "missing scorecard grid container");
    assert.ok(html.includes('id="daysSelect"'), "missing days range selector");
    assert.ok(html.includes('id="agentSearch"'), "missing agent search input");
    assert.ok(html.includes('id="agentCount"'), "missing agent count display");
  });

  it("contains the detail modal", () => {
    assert.ok(html.includes('id="detailModal"'), "missing detail modal overlay");
    assert.ok(html.includes('id="detailContent"'), "missing detail content container");
    assert.ok(html.includes("closeDetail()"), "missing closeDetail function call");
    assert.ok(html.includes("sc-modal-close"), "missing modal close button class");
  });

  it("has correct time range options", () => {
    assert.ok(html.includes('value="7"'), "missing 7 days option");
    assert.ok(html.includes('value="14"'), "missing 14 days option");
    assert.ok(html.includes('value="30"'), "missing 30 days option");
    assert.ok(html.includes('value="90"'), "missing 90 days option");
    assert.ok(html.includes('value="365"'), "missing 1 year option");
  });

  it("has navigation links", () => {
    assert.ok(html.includes('href="index.html"'), "missing link to main dashboard");
    assert.ok(html.includes('href="waterfall.html"'), "missing link to waterfall");
  });

  it("has proper page title", () => {
    assert.ok(html.includes("<title>AgentLens — Agent Scorecards</title>"), "incorrect page title");
  });
});

describe("Scorecards JavaScript Functions", () => {
  it("defines loadScorecards function", () => {
    assert.ok(html.includes("async function loadScorecards()"), "missing loadScorecards");
  });

  it("defines filterCards function", () => {
    assert.ok(html.includes("function filterCards()"), "missing filterCards");
  });

  it("defines renderCards function", () => {
    assert.ok(html.includes("function renderCards(cards)"), "missing renderCards");
  });

  it("defines renderSparkline function", () => {
    assert.ok(html.includes("function renderSparkline(trend)"), "missing renderSparkline");
  });

  it("defines openDetail function", () => {
    assert.ok(html.includes("async function openDetail(agent)"), "missing openDetail");
  });

  it("defines closeDetail function", () => {
    assert.ok(html.includes("function closeDetail()"), "missing closeDetail");
  });

  it("defines esc (escape) helper", () => {
    assert.ok(html.includes("function esc(s)"), "missing esc helper for XSS prevention");
  });

  it("calls loadScorecards on page load", () => {
    assert.ok(html.includes("loadScorecards()"), "loadScorecards not called on init");
  });
});

describe("Scorecards API Integration", () => {
  it("fetches from /scorecards endpoint with days param", () => {
    assert.ok(html.includes("/scorecards?days="), "missing scorecards API call with days parameter");
  });

  it("fetches individual scorecard detail", () => {
    assert.ok(html.includes("/scorecards/"), "missing individual scorecard detail fetch");
  });
});

describe("Scorecards Rendering Logic", () => {
  it("renders scorecard metric cards with key fields", () => {
    assert.ok(html.includes("sc-metric-label"), "missing metric label class");
    assert.ok(html.includes("sc-metric-value"), "missing metric value class");
    assert.ok(html.includes("total_sessions"), "missing total sessions metric");
    assert.ok(html.includes("success_rate"), "missing success rate metric");
    assert.ok(html.includes("avg_latency_ms"), "missing avg latency metric");
  });

  it("renders grade with color", () => {
    assert.ok(html.includes("grade_color"), "missing grade color binding");
    assert.ok(html.includes("sc-grade"), "missing grade display class");
    assert.ok(html.includes("composite_score"), "missing composite score display");
  });

  it("renders sparkline SVG for trends", () => {
    assert.ok(html.includes("<svg viewBox"), "missing SVG viewBox in sparkline");
    assert.ok(html.includes("<polyline"), "missing polyline element in sparkline");
  });

  it("renders model usage table in detail view", () => {
    assert.ok(html.includes("sc-model-table"), "missing model table class");
    assert.ok(html.includes("tokens_in"), "missing tokens_in column");
    assert.ok(html.includes("tokens_out"), "missing tokens_out column");
  });

  it("renders daily trend bar chart in detail view", () => {
    assert.ok(html.includes("sc-bar-chart"), "missing bar chart class");
    assert.ok(html.includes("sc-bar"), "missing bar class");
  });
});

describe("Scorecards Modal Interaction", () => {
  it("opens modal with open class", () => {
    assert.ok(html.includes('classList.add("open")'), "missing modal open logic");
  });

  it("closes modal on overlay click", () => {
    assert.ok(html.includes("e.target === e.currentTarget"), "missing overlay click-to-close logic");
  });

  it("closes modal on Escape key", () => {
    assert.ok(html.includes('"Escape"'), "missing Escape key handler");
  });
});

describe("Scorecards CSS Classes", () => {
  it("defines scorecard grid layout", () => {
    assert.ok(html.includes(".scorecard-grid"), "missing scorecard-grid class");
    assert.ok(html.includes("grid-template-columns"), "missing grid layout");
  });

  it("defines card hover effect", () => {
    assert.ok(html.includes(".scorecard-card:hover"), "missing card hover style");
    assert.ok(html.includes("translateY"), "missing hover transform");
  });

  it("defines success/error color coding", () => {
    assert.ok(html.includes("#22c55e"), "missing green color for success");
    assert.ok(html.includes("#ef4444"), "missing red color for errors");
  });
});
