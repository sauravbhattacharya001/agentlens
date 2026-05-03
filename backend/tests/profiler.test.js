/**
 * Tests for the Agent Behavior Profiler route helpers.
 *
 * Validates cosine similarity, Jensen-Shannon divergence, distribution
 * normalization, drift classification, profile building, and percentile
 * computation — the internal helpers that power profiler.js.
 */

// ── Re-implement route-internal helpers for unit testing ────────────

function cosineSimilarity(a, b) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  let dot = 0, magA = 0, magB = 0;
  for (const k of keys) {
    const va = a[k] || 0, vb = b[k] || 0;
    dot += va * vb;
    magA += va * va;
    magB += vb * vb;
  }
  if (magA === 0 || magB === 0) return 1;
  return dot / (Math.sqrt(magA) * Math.sqrt(magB));
}

function jensenShannonDivergence(p, q) {
  const keys = new Set([...Object.keys(p), ...Object.keys(q)]);
  const m = {};
  for (const k of keys) m[k] = ((p[k] || 0) + (q[k] || 0)) / 2;
  let kl_pm = 0, kl_qm = 0;
  for (const k of keys) {
    const pk = p[k] || 0, qk = q[k] || 0, mk = m[k];
    if (pk > 0 && mk > 0) kl_pm += pk * Math.log2(pk / mk);
    if (qk > 0 && mk > 0) kl_qm += qk * Math.log2(qk / mk);
  }
  return (kl_pm + kl_qm) / 2;
}

function normalize(obj) {
  const total = Object.values(obj).reduce((s, v) => s + v, 0);
  if (total === 0) return obj;
  const out = {};
  for (const [k, v] of Object.entries(obj)) out[k] = v / total;
  return out;
}

function classifyDrift(jsd) {
  if (jsd >= 0.4) return "critical";
  if (jsd >= 0.25) return "high";
  if (jsd >= 0.1) return "medium";
  return "stable";
}

function percentile(arr, p) {
  if (!arr.length) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.ceil(p * sorted.length) - 1;
  return sorted[Math.max(0, idx)];
}

function buildProfile(sessions, events) {
  const eventTypeDist = {};
  const toolCallDist = {};
  let totalTokens = 0, totalDuration = 0, totalErrors = 0;
  const durations = [];
  const tokenCounts = [];

  for (const s of sessions) {
    totalTokens += s.total_tokens || 0;
    totalDuration += s.duration_ms || 0;
    totalErrors += s.error_count || 0;
    if (s.duration_ms) durations.push(s.duration_ms);
    if (s.total_tokens) tokenCounts.push(s.total_tokens);
  }

  for (const e of events) {
    const t = e.type || "unknown";
    eventTypeDist[t] = (eventTypeDist[t] || 0) + 1;
    if (t === "tool_call" || t === "function_call") {
      const name = e.name || e.tool_name || "unknown_tool";
      toolCallDist[name] = (toolCallDist[name] || 0) + 1;
    }
  }

  const n = sessions.length || 1;
  return {
    sessionCount: sessions.length,
    avgTokens: Math.round(totalTokens / n),
    avgDuration: Math.round(totalDuration / n),
    errorRate: totalErrors / n,
    eventTypeDist: normalize(eventTypeDist),
    toolCallDist: normalize(toolCallDist),
    p50Duration: percentile(durations, 0.5),
    p95Duration: percentile(durations, 0.95),
    p50Tokens: percentile(tokenCounts, 0.5),
    p95Tokens: percentile(tokenCounts, 0.95),
  };
}

const SAFE_AGENT_RE = /^[\w .:\-@/]{1,128}$/;

// ── Tests ───────────────────────────────────────────────────────────

describe("Profiler", () => {

  // ── Cosine similarity ─────────────────────────────────────────────

  describe("cosineSimilarity", () => {
    test("identical distributions return 1", () => {
      const d = { a: 0.5, b: 0.3, c: 0.2 };
      expect(cosineSimilarity(d, d)).toBeCloseTo(1, 6);
    });

    test("orthogonal distributions return 0", () => {
      const a = { x: 1 };
      const b = { y: 1 };
      expect(cosineSimilarity(a, b)).toBeCloseTo(0, 6);
    });

    test("returns 1 when both are empty", () => {
      expect(cosineSimilarity({}, {})).toBe(1);
    });

    test("returns 1 when one vector is empty (zero magnitude guard)", () => {
      expect(cosineSimilarity({ a: 1 }, {})).toBe(1);
    });

    test("similar distributions have high similarity", () => {
      const a = { tool_a: 0.5, tool_b: 0.3, tool_c: 0.2 };
      const b = { tool_a: 0.48, tool_b: 0.32, tool_c: 0.20 };
      expect(cosineSimilarity(a, b)).toBeGreaterThan(0.99);
    });

    test("dissimilar distributions have lower similarity", () => {
      const a = { tool_a: 0.9, tool_b: 0.1 };
      const b = { tool_a: 0.1, tool_b: 0.9 };
      expect(cosineSimilarity(a, b)).toBeLessThan(0.5);
    });
  });

  // ── Jensen-Shannon divergence ─────────────────────────────────────

  describe("jensenShannonDivergence", () => {
    test("identical distributions return 0", () => {
      const d = { a: 0.5, b: 0.3, c: 0.2 };
      expect(jensenShannonDivergence(d, d)).toBeCloseTo(0, 10);
    });

    test("completely disjoint distributions return 1 (max JSD for base-2 log)", () => {
      const a = { x: 1.0 };
      const b = { y: 1.0 };
      expect(jensenShannonDivergence(a, b)).toBeCloseTo(1, 6);
    });

    test("symmetric: JSD(p,q) = JSD(q,p)", () => {
      const a = { x: 0.7, y: 0.3 };
      const b = { x: 0.4, y: 0.6 };
      expect(jensenShannonDivergence(a, b)).toBeCloseTo(
        jensenShannonDivergence(b, a), 10
      );
    });

    test("bounded between 0 and 1", () => {
      const a = { a: 0.3, b: 0.7 };
      const b = { a: 0.8, b: 0.2 };
      const jsd = jensenShannonDivergence(a, b);
      expect(jsd).toBeGreaterThanOrEqual(0);
      expect(jsd).toBeLessThanOrEqual(1);
    });

    test("small perturbation yields small divergence", () => {
      const a = { x: 0.5, y: 0.5 };
      const b = { x: 0.51, y: 0.49 };
      expect(jensenShannonDivergence(a, b)).toBeLessThan(0.001);
    });

    test("empty distributions return 0", () => {
      expect(jensenShannonDivergence({}, {})).toBe(0);
    });
  });

  // ── Normalize ─────────────────────────────────────────────────────

  describe("normalize", () => {
    test("normalizes to sum=1", () => {
      const r = normalize({ a: 10, b: 30, c: 60 });
      expect(r.a).toBeCloseTo(0.1);
      expect(r.b).toBeCloseTo(0.3);
      expect(r.c).toBeCloseTo(0.6);
    });

    test("returns same object for zero total", () => {
      const input = { a: 0, b: 0 };
      const r = normalize(input);
      expect(r.a).toBe(0);
      expect(r.b).toBe(0);
    });

    test("single-element normalizes to 1", () => {
      const r = normalize({ only: 42 });
      expect(r.only).toBeCloseTo(1);
    });
  });

  // ── Drift classification ──────────────────────────────────────────

  describe("classifyDrift", () => {
    test("stable for jsd < 0.1", () => expect(classifyDrift(0.05)).toBe("stable"));
    test("medium for 0.1 <= jsd < 0.25", () => expect(classifyDrift(0.15)).toBe("medium"));
    test("high for 0.25 <= jsd < 0.4", () => expect(classifyDrift(0.30)).toBe("high"));
    test("critical for jsd >= 0.4", () => expect(classifyDrift(0.5)).toBe("critical"));
    test("boundary: exactly 0.1 is medium", () => expect(classifyDrift(0.1)).toBe("medium"));
    test("boundary: exactly 0.25 is high", () => expect(classifyDrift(0.25)).toBe("high"));
    test("boundary: exactly 0.4 is critical", () => expect(classifyDrift(0.4)).toBe("critical"));
    test("zero is stable", () => expect(classifyDrift(0)).toBe("stable"));
  });

  // ── Percentile ────────────────────────────────────────────────────

  describe("percentile", () => {
    test("empty array returns 0", () => {
      expect(percentile([], 0.5)).toBe(0);
    });

    test("single element returns that element", () => {
      expect(percentile([42], 0.5)).toBe(42);
      expect(percentile([42], 0.95)).toBe(42);
    });

    test("p50 returns median", () => {
      expect(percentile([1, 2, 3, 4, 5], 0.5)).toBe(3);
    });

    test("p95 returns near-max value", () => {
      const arr = Array.from({ length: 100 }, (_, i) => i + 1);
      expect(percentile(arr, 0.95)).toBe(95);
    });

    test("does not mutate input array", () => {
      const arr = [5, 3, 1, 4, 2];
      const copy = [...arr];
      percentile(arr, 0.5);
      expect(arr).toEqual(copy);
    });

    test("handles duplicate values", () => {
      expect(percentile([7, 7, 7, 7], 0.5)).toBe(7);
    });
  });

  // ── Profile building ──────────────────────────────────────────────

  describe("buildProfile", () => {
    test("builds correct profile from sessions and events", () => {
      const sessions = [
        { total_tokens: 1000, duration_ms: 5000, error_count: 0 },
        { total_tokens: 2000, duration_ms: 3000, error_count: 1 },
      ];
      const events = [
        { type: "llm_call" },
        { type: "llm_call" },
        { type: "tool_call", name: "search" },
        { type: "tool_call", name: "search" },
        { type: "tool_call", name: "write" },
      ];
      const p = buildProfile(sessions, events);
      expect(p.sessionCount).toBe(2);
      expect(p.avgTokens).toBe(1500);
      expect(p.avgDuration).toBe(4000);
      expect(p.errorRate).toBe(0.5);
      expect(p.eventTypeDist.llm_call).toBeCloseTo(0.4);
      expect(p.eventTypeDist.tool_call).toBeCloseTo(0.6);
      expect(p.toolCallDist.search).toBeCloseTo(2 / 3);
      expect(p.toolCallDist.write).toBeCloseTo(1 / 3);
    });

    test("empty sessions returns zeros", () => {
      const p = buildProfile([], []);
      expect(p.sessionCount).toBe(0);
      expect(p.avgTokens).toBe(0);
      expect(p.avgDuration).toBe(0);
      expect(p.p50Duration).toBe(0);
    });

    test("function_call events counted in toolCallDist", () => {
      const sessions = [{ total_tokens: 100, duration_ms: 100, error_count: 0 }];
      const events = [
        { type: "function_call", name: "get_weather" },
        { type: "function_call", name: "get_weather" },
        { type: "function_call", name: "send_email" },
      ];
      const p = buildProfile(sessions, events);
      expect(p.toolCallDist.get_weather).toBeCloseTo(2 / 3);
      expect(p.toolCallDist.send_email).toBeCloseTo(1 / 3);
    });

    test("events with no type classified as unknown", () => {
      const sessions = [{ total_tokens: 100, duration_ms: 100, error_count: 0 }];
      const events = [{ /* no type */ }];
      const p = buildProfile(sessions, events);
      expect(p.eventTypeDist.unknown).toBe(1);
    });

    test("tool_call with tool_name fallback", () => {
      const sessions = [{ total_tokens: 100, duration_ms: 100, error_count: 0 }];
      const events = [{ type: "tool_call", tool_name: "my_tool" }];
      const p = buildProfile(sessions, events);
      expect(p.toolCallDist.my_tool).toBe(1);
    });

    test("tool_call with no name uses unknown_tool", () => {
      const sessions = [{ total_tokens: 100, duration_ms: 100, error_count: 0 }];
      const events = [{ type: "tool_call" }];
      const p = buildProfile(sessions, events);
      expect(p.toolCallDist.unknown_tool).toBe(1);
    });

    test("percentiles computed correctly across sessions", () => {
      const sessions = [
        { total_tokens: 100, duration_ms: 1000, error_count: 0 },
        { total_tokens: 200, duration_ms: 2000, error_count: 0 },
        { total_tokens: 300, duration_ms: 3000, error_count: 0 },
        { total_tokens: 400, duration_ms: 4000, error_count: 0 },
        { total_tokens: 500, duration_ms: 5000, error_count: 0 },
      ];
      const p = buildProfile(sessions, []);
      expect(p.p50Duration).toBe(3000);
      expect(p.p95Duration).toBe(5000);
      expect(p.p50Tokens).toBe(300);
      expect(p.p95Tokens).toBe(500);
    });
  });

  // ── Agent name validation regex ───────────────────────────────────

  describe("SAFE_AGENT_RE", () => {
    test("accepts valid agent names", () => {
      expect(SAFE_AGENT_RE.test("code-agent")).toBe(true);
      expect(SAFE_AGENT_RE.test("my_agent.v2")).toBe(true);
      expect(SAFE_AGENT_RE.test("user@org/agent:latest")).toBe(true);
      expect(SAFE_AGENT_RE.test("Agent 1")).toBe(true);
    });

    test("rejects dangerous characters", () => {
      expect(SAFE_AGENT_RE.test("agent'; DROP TABLE--")).toBe(false);
      expect(SAFE_AGENT_RE.test("agent<script>")).toBe(false);
      expect(SAFE_AGENT_RE.test("")).toBe(false);
    });

    test("rejects names longer than 128 chars", () => {
      expect(SAFE_AGENT_RE.test("a".repeat(129))).toBe(false);
    });

    test("accepts exactly 128 chars", () => {
      expect(SAFE_AGENT_RE.test("a".repeat(128))).toBe(true);
    });
  });

  // ── Drift detection integration ───────────────────────────────────

  describe("drift detection integration", () => {
    test("identical baselines and recent profiles show stable drift", () => {
      const sessions = Array.from({ length: 10 }, (_, i) => ({
        total_tokens: 1000, duration_ms: 5000, error_count: 0,
      }));
      const events = Array.from({ length: 50 }, () => ({ type: "llm_call" }));

      const baseline = buildProfile(sessions, events);
      const recent = buildProfile(sessions, events);

      const jsd = jensenShannonDivergence(baseline.eventTypeDist, recent.eventTypeDist);
      expect(classifyDrift(jsd)).toBe("stable");
    });

    test("radically different profiles show critical drift", () => {
      const baselineSessions = Array.from({ length: 10 }, () => ({
        total_tokens: 1000, duration_ms: 5000, error_count: 0,
      }));
      const baselineEvents = Array.from({ length: 50 }, () => ({ type: "llm_call" }));

      const recentSessions = Array.from({ length: 10 }, () => ({
        total_tokens: 5000, duration_ms: 20000, error_count: 3,
      }));
      const recentEvents = Array.from({ length: 50 }, () => ({ type: "tool_call", name: "new_tool" }));

      const baseline = buildProfile(baselineSessions, baselineEvents);
      const recent = buildProfile(recentSessions, recentEvents);

      const eventDrift = jensenShannonDivergence(baseline.eventTypeDist, recent.eventTypeDist);
      expect(eventDrift).toBeGreaterThan(0);
      expect(classifyDrift(eventDrift)).not.toBe("stable");
    });

    test("cosine similarity and JSD agree on direction", () => {
      const baseline = { llm_call: 0.8, tool_call: 0.2 };
      const similar = { llm_call: 0.78, tool_call: 0.22 };
      const different = { llm_call: 0.2, tool_call: 0.8 };

      // Similar should have high cosine, low JSD
      expect(cosineSimilarity(baseline, similar)).toBeGreaterThan(
        cosineSimilarity(baseline, different)
      );
      expect(jensenShannonDivergence(baseline, similar)).toBeLessThan(
        jensenShannonDivergence(baseline, different)
      );
    });
  });
});
