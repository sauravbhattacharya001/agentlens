/**
 * Tests for Agent Collaboration Analyzer routes.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

// Inline analyzeSession for unit testing (extracted logic)
function giniCoefficient(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  if (n === 0) return 0;
  const total = sorted.reduce((s, v) => s + v, 0);
  if (total === 0) return 0;
  let num = 0;
  for (let i = 0; i < n; i++) num += (2 * (i + 1) - n - 1) * sorted[i];
  return Math.max(0, Math.min(1, num / (n * total)));
}

function classifyHandoff(latencyMs, contextLoss) {
  if (contextLoss > 0.5 || latencyMs > 10000) return "failed";
  if (contextLoss > 0.2 || latencyMs > 5000) return "lossy";
  if (contextLoss > 0.05) return "acceptable";
  return "clean";
}

function classifyGrade(score) {
  if (score >= 90) return "elite";
  if (score >= 75) return "strong";
  if (score >= 60) return "functional";
  if (score >= 40) return "struggling";
  return "dysfunctional";
}

describe("Collaboration helpers", () => {
  it("giniCoefficient — perfect equality", () => {
    assert.equal(giniCoefficient([5, 5, 5, 5]), 0);
  });

  it("giniCoefficient — perfect inequality", () => {
    const g = giniCoefficient([0, 0, 0, 100]);
    assert.ok(g > 0.5);
  });

  it("giniCoefficient — empty", () => {
    assert.equal(giniCoefficient([]), 0);
  });

  it("classifyHandoff — clean", () => {
    assert.equal(classifyHandoff(100, 0.01), "clean");
  });

  it("classifyHandoff — lossy", () => {
    assert.equal(classifyHandoff(6000, 0.3), "lossy");
  });

  it("classifyHandoff — failed", () => {
    assert.equal(classifyHandoff(15000, 0.6), "failed");
  });

  it("classifyHandoff — acceptable", () => {
    assert.equal(classifyHandoff(100, 0.1), "acceptable");
  });

  it("classifyGrade — elite", () => {
    assert.equal(classifyGrade(95), "elite");
  });

  it("classifyGrade — strong", () => {
    assert.equal(classifyGrade(80), "strong");
  });

  it("classifyGrade — functional", () => {
    assert.equal(classifyGrade(65), "functional");
  });

  it("classifyGrade — struggling", () => {
    assert.equal(classifyGrade(45), "struggling");
  });

  it("classifyGrade — dysfunctional", () => {
    assert.equal(classifyGrade(20), "dysfunctional");
  });

  it("giniCoefficient — two agents balanced", () => {
    assert.equal(giniCoefficient([10, 10]), 0);
  });

  it("giniCoefficient — two agents imbalanced", () => {
    const g = giniCoefficient([1, 99]);
    assert.ok(g > 0.3);
  });

  it("classifyHandoff — edge exactly at threshold", () => {
    assert.equal(classifyHandoff(5000, 0.2), "acceptable");
  });
});
