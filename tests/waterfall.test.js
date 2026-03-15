/**
 * Tests for dashboard/waterfall.html — Trace Waterfall Viewer
 *
 * Validates the self-contained waterfall viewer by checking HTML structure,
 * JavaScript functions, keyboard shortcuts, and SVG export logic.
 */

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { describe, it } = require("node:test");

const htmlPath = path.join(__dirname, "..", "dashboard", "waterfall.html");
const html = fs.readFileSync(htmlPath, "utf8");

describe("Waterfall Viewer HTML", () => {
  it("contains required structural elements", () => {
    assert.ok(html.includes('id="sessionInput"'), "missing session input");
    assert.ok(html.includes('id="waterfallPanel"'), "missing waterfall panel");
    assert.ok(html.includes('id="detailPanel"'), "missing detail panel");
    assert.ok(html.includes('id="summaryBar"'), "missing summary bar");
    assert.ok(html.includes('id="toolbar"'), "missing toolbar");
    assert.ok(html.includes('id="waterfallContent"'), "missing waterfall content");
  });

  it("has filter controls", () => {
    assert.ok(html.includes('id="filterType"'), "missing type filter");
    assert.ok(html.includes('id="filterDuration"'), "missing duration filter");
    assert.ok(html.includes("applyFilters()"), "missing applyFilters call");
  });

  it("has zoom controls", () => {
    assert.ok(html.includes("zoom(-1)"), "missing zoom out");
    assert.ok(html.includes("zoom(0)"), "missing zoom reset");
    assert.ok(html.includes("zoom(1)"), "missing zoom in");
  });

  it("has export SVG button", () => {
    assert.ok(html.includes("exportSVG()"), "missing SVG export");
    assert.ok(html.includes("trace-waterfall.svg"), "missing default SVG filename");
  });

  it("has demo data loader", () => {
    assert.ok(html.includes("loadDemo()"), "missing demo loader");
    assert.ok(html.includes("demo-abc123"), "missing demo session ID");
  });

  it("has keyboard shortcuts", () => {
    assert.ok(html.includes("ArrowDown"), "missing down arrow handler");
    assert.ok(html.includes("ArrowUp"), "missing up arrow handler");
    assert.ok(html.includes("Escape"), "missing escape handler");
  });

  it("has event type metadata for all types", () => {
    const types = ["session_start", "session_end", "llm_call", "tool_call", "error", "decision", "span_start"];
    for (const t of types) {
      assert.ok(html.includes(t), `missing type: ${t}`);
    }
  });

  it("has legend", () => {
    assert.ok(html.includes("legend"), "missing legend");
    assert.ok(html.includes("LLM"), "missing LLM legend");
    assert.ok(html.includes("Tool"), "missing Tool legend");
  });

  it("has detail panel with close button", () => {
    assert.ok(html.includes("closeDetail()"), "missing closeDetail");
    assert.ok(html.includes("detail-close"), "missing close button class");
  });

  it("links back to main dashboard", () => {
    assert.ok(html.includes('href="index.html"'), "missing link to main dashboard");
  });

  it("has minimap", () => {
    assert.ok(html.includes("minimap"), "missing minimap");
    assert.ok(html.includes("minimap-bar"), "missing minimap bars");
  });

  it("has dark theme colors", () => {
    assert.ok(html.includes("#0f172a"), "missing dark bg color");
    assert.ok(html.includes("#1e293b"), "missing surface color");
  });

  it("handles token display", () => {
    assert.ok(html.includes("tokens_in"), "missing tokens_in handling");
    assert.ok(html.includes("tokens_out"), "missing tokens_out handling");
    assert.ok(html.includes("fmtNum"), "missing number formatter");
  });

  it("uses monospace font for technical values", () => {
    assert.ok(html.includes("mono"), "missing monospace class");
    assert.ok(html.includes("SF Mono"), "missing mono font");
  });
});

describe("Waterfall JS functions", () => {
  // Extract and eval the script to test functions
  const scriptMatch = html.match(/<script>([\s\S]*)<\/script>/);
  assert.ok(scriptMatch, "no script tag found");

  // Create a minimal DOM mock
  const mockDoc = {
    _els: {},
    getElementById(id) {
      if (!this._els[id]) this._els[id] = {
        value: '', textContent: '', innerHTML: '', style: { display: '' },
        classList: { add() {}, remove() {}, contains() { return false; } }
      };
      return this._els[id];
    },
    createElement(tag) { return { textContent: '', innerHTML: '', tagName: tag }; },
    addEventListener() {},
  };

  // We can test pure functions by extracting them
  it("fmtDur formats durations correctly", () => {
    // Extract fmtDur function
    const fmtDurMatch = scriptMatch[1].match(/function fmtDur\(ms\)\s*\{[^}]+\}/);
    assert.ok(fmtDurMatch, "fmtDur not found");
    const fmtDur = new Function("ms", fmtDurMatch[0].replace(/^function fmtDur\(ms\)\s*\{/, "").replace(/\}$/, ""));

    assert.strictEqual(fmtDur(null), "-");
    assert.strictEqual(fmtDur(0.5), "<1ms");
    assert.strictEqual(fmtDur(500), "500ms");
    assert.strictEqual(fmtDur(1500), "1.5s");
    assert.strictEqual(fmtDur(90000), "1.5m");
  });

  it("fmtNum formats numbers correctly", () => {
    const fmtNumMatch = scriptMatch[1].match(/function fmtNum\(n\)\s*\{[^}]+\}/);
    assert.ok(fmtNumMatch, "fmtNum not found");
    const fmtNum = new Function("n", fmtNumMatch[0].replace(/^function fmtNum\(n\)\s*\{/, "").replace(/\}$/, ""));

    assert.strictEqual(fmtNum(500), "500");
    assert.strictEqual(fmtNum(1500), "1.5K");
    assert.strictEqual(fmtNum(2000000), "2.0M");
  });

  it("escXml escapes XML entities", () => {
    const escMatch = scriptMatch[1].match(/function escXml\(s\)\s*\{[^}]+\}/);
    assert.ok(escMatch, "escXml not found");
    const escXml = new Function("s", escMatch[0].replace(/^function escXml\(s\)\s*\{/, "").replace(/\}$/, ""));

    assert.strictEqual(escXml('<test&"value">'), "&lt;test&amp;&quot;value&quot;&gt;");
  });
});

describe("Main dashboard integration", () => {
  const mainHtml = fs.readFileSync(path.join(__dirname, "..", "dashboard", "index.html"), "utf8");

  it("has waterfall link in header", () => {
    assert.ok(mainHtml.includes('href="waterfall.html"'), "missing waterfall link in main dashboard");
    assert.ok(mainHtml.includes("Waterfall"), "missing waterfall text");
  });
});
