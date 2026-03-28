/**
 * Tests for dashboard/diff.html — Session Diff Dashboard
 *
 * Validates HTML structure, JS functions, session comparison UI,
 * diff rendering, and visual components.
 */

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { describe, it } = require("node:test");

const htmlPath = path.join(__dirname, "..", "dashboard", "diff.html");
const html = fs.readFileSync(htmlPath, "utf8");

describe("Diff Dashboard HTML Structure", () => {
  it("contains session selector controls", () => {
    assert.ok(html.includes('id="baselineSelect"'), "missing baseline session selector");
    assert.ok(html.includes('id="candidateSelect"'), "missing candidate session selector");
    assert.ok(html.includes("runDiff()"), "missing runDiff trigger");
  });

  it("contains diff result container", () => {
    assert.ok(html.includes('id="diffResult"'), "missing diff result container");
  });

  it("has proper page title", () => {
    assert.ok(html.includes("<title>AgentLens — Session Diff</title>"), "incorrect page title");
  });

  it("has navigation links", () => {
    assert.ok(html.includes('href="scorecards.html"'), "missing link to scorecards");
    assert.ok(html.includes('href="waterfall.html"'), "missing link to waterfall");
  });

  it("shows empty state instructions", () => {
    assert.ok(html.includes("Select two sessions"), "missing empty state instructions");
    assert.ok(html.includes("Compare"), "missing Compare button text");
  });
});

describe("Diff JavaScript Functions", () => {
  it("defines loadSessions function", () => {
    assert.ok(html.includes("async function loadSessions()"), "missing loadSessions");
  });

  it("defines populateDropdowns function", () => {
    assert.ok(html.includes("function populateDropdowns()"), "missing populateDropdowns");
  });

  it("defines runDiff function", () => {
    assert.ok(html.includes("async function runDiff()"), "missing runDiff");
  });

  it("defines renderDiff function", () => {
    assert.ok(html.includes("function renderDiff(diff)"), "missing renderDiff");
  });

  it("defines formatDelta helper", () => {
    assert.ok(html.includes("function formatDelta(val"), "missing formatDelta");
  });

  it("defines similarityColor helper", () => {
    assert.ok(html.includes("function similarityColor(score)"), "missing similarityColor");
  });

  it("loads sessions on DOMContentLoaded", () => {
    assert.ok(html.includes('addEventListener("DOMContentLoaded"'), "missing DOMContentLoaded listener");
    assert.ok(html.includes("loadSessions"), "loadSessions not wired to init");
  });
});

describe("Diff API Integration", () => {
  it("fetches sessions list", () => {
    assert.ok(html.includes("/sessions"), "missing sessions API call");
  });

  it("fetches diff with baseline and candidate params", () => {
    assert.ok(html.includes("/diff?baseline="), "missing diff API call with params");
  });
});

describe("Diff URL State Management", () => {
  it("reads URL params for pre-selection", () => {
    assert.ok(html.includes('params.get("baseline")'), "missing baseline URL param reading");
    assert.ok(html.includes('params.get("candidate")'), "missing candidate URL param reading");
  });

  it("updates URL on diff", () => {
    assert.ok(html.includes("history.replaceState"), "missing URL state update");
    assert.ok(html.includes('searchParams.set("baseline"'), "missing baseline param set");
    assert.ok(html.includes('searchParams.set("candidate"'), "missing candidate param set");
  });
});

describe("Diff Validation Logic", () => {
  it("prevents diffing same session", () => {
    assert.ok(html.includes("baselineId === candidateId"), "missing same-session check");
    assert.ok(html.includes("Cannot diff a session with itself"), "missing same-session error message");
  });

  it("validates both sessions selected", () => {
    assert.ok(html.includes("!baselineId || !candidateId"), "missing empty selection check");
    assert.ok(html.includes("Please select both sessions"), "missing both-sessions error message");
  });
});

describe("Diff Rendering Components", () => {
  it("renders session pair header", () => {
    assert.ok(html.includes("session-pair"), "missing session pair container");
    assert.ok(html.includes("session-info baseline"), "missing baseline session info");
    assert.ok(html.includes("session-info candidate"), "missing candidate session info");
  });

  it("renders similarity gauge", () => {
    assert.ok(html.includes("similarity-gauge"), "missing similarity gauge");
    assert.ok(html.includes("gauge-track"), "missing gauge track");
    assert.ok(html.includes("gauge-fill"), "missing gauge fill");
  });

  it("renders summary delta cards", () => {
    assert.ok(html.includes("diff-summary"), "missing diff summary container");
    assert.ok(html.includes("diff-card"), "missing diff card class");
    assert.ok(html.includes("tokens_total"), "missing token delta");
    assert.ok(html.includes("duration_ms"), "missing duration delta");
    assert.ok(html.includes("event_count"), "missing event count delta");
  });

  it("renders tool call chips", () => {
    assert.ok(html.includes("chip-list"), "missing chip list");
    assert.ok(html.includes("chip added"), "missing added chip style");
    assert.ok(html.includes("chip removed"), "missing removed chip style");
    assert.ok(html.includes("chip common"), "missing common chip style");
  });

  it("renders model usage bar chart", () => {
    assert.ok(html.includes("bar-chart"), "missing bar chart container");
    assert.ok(html.includes("bar-fill baseline"), "missing baseline bar fill");
    assert.ok(html.includes("bar-fill candidate"), "missing candidate bar fill");
  });

  it("renders event alignment table", () => {
    assert.ok(html.includes("align-table"), "missing alignment table");
    assert.ok(html.includes("align-status"), "missing alignment status badges");
    assert.ok(html.includes("matched"), "missing matched status");
    assert.ok(html.includes("modified"), "missing modified status");
    assert.ok(html.includes("added"), "missing added status");
    assert.ok(html.includes("removed"), "missing removed status");
  });

  it("renders event type changes", () => {
    assert.ok(html.includes("event_types.added"), "missing event types added");
    assert.ok(html.includes("event_types.removed"), "missing event types removed");
  });
});

describe("Diff Delta Formatting", () => {
  it("uses color-coded delta classes", () => {
    assert.ok(html.includes("delta-pos"), "missing positive delta class");
    assert.ok(html.includes("delta-neg"), "missing negative delta class");
    assert.ok(html.includes("delta-zero"), "missing zero delta class");
  });

  it("similarity thresholds use correct colors", () => {
    // similarityColor function checks >= 0.8, >= 0.5
    assert.ok(html.includes("score >= 0.8"), "missing high similarity threshold");
    assert.ok(html.includes("score >= 0.5"), "missing medium similarity threshold");
  });
});
