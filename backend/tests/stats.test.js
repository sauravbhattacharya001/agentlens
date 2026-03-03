const { percentile, latencyStats, groupEventStats, buildGroupPerf, round2 } = require("../lib/stats");

describe("stats utility", () => {
  // ── round2 ────────────────────────────────────────────────────
  describe("round2", () => {
    test("rounds to 2 decimal places", () => {
      expect(round2(1.2345)).toBe(1.23);
      expect(round2(1.2355)).toBe(1.24);
      expect(round2(0)).toBe(0);
      expect(round2(100)).toBe(100);
    });

    test("handles negative numbers", () => {
      expect(round2(-3.456)).toBe(-3.46);
    });
  });

  // ── percentile ────────────────────────────────────────────────
  describe("percentile", () => {
    test("returns 0 for empty array", () => {
      expect(percentile([], 50)).toBe(0);
    });

    test("returns exact value for single element", () => {
      expect(percentile([42], 50)).toBe(42);
      expect(percentile([42], 0)).toBe(42);
      expect(percentile([42], 100)).toBe(42);
    });

    test("returns min at p0", () => {
      expect(percentile([1, 2, 3, 4, 5], 0)).toBe(1);
    });

    test("returns max at p100", () => {
      expect(percentile([1, 2, 3, 4, 5], 100)).toBe(5);
    });

    test("returns median at p50 for odd-length array", () => {
      expect(percentile([1, 2, 3, 4, 5], 50)).toBe(3);
    });

    test("interpolates at p50 for even-length array", () => {
      expect(percentile([1, 2, 3, 4], 50)).toBe(2.5);
    });

    test("interpolates correctly at p25", () => {
      expect(percentile([1, 2, 3, 4, 5], 25)).toBe(2);
    });

    test("interpolates between adjacent values", () => {
      const result = percentile([10, 20, 30, 40], 75);
      expect(result).toBeCloseTo(32.5, 5);
    });

    test("handles identical values", () => {
      expect(percentile([5, 5, 5, 5], 50)).toBe(5);
      expect(percentile([5, 5, 5, 5], 95)).toBe(5);
    });

    test("handles two elements", () => {
      expect(percentile([1, 100], 50)).toBe(50.5);
    });
  });

  // ── latencyStats ──────────────────────────────────────────────
  describe("latencyStats", () => {
    test("returns null for empty array", () => {
      expect(latencyStats([])).toBeNull();
    });

    test("returns all percentiles for single value", () => {
      const s = latencyStats([42]);
      expect(s.p50).toBe(42);
      expect(s.p95).toBe(42);
      expect(s.p99).toBe(42);
      expect(s.avg).toBe(42);
      expect(s.min).toBe(42);
      expect(s.max).toBe(42);
    });

    test("computes correct stats for known data", () => {
      const data = [10, 20, 30, 40, 50];
      const s = latencyStats(data);
      expect(s.min).toBe(10);
      expect(s.max).toBe(50);
      expect(s.avg).toBe(30);
      expect(s.p50).toBe(30);
    });

    test("includes p75, p90 fields", () => {
      const s = latencyStats([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
      expect(s).toHaveProperty("p75");
      expect(s).toHaveProperty("p90");
      expect(s.p75).toBeGreaterThan(s.p50);
      expect(s.p90).toBeGreaterThan(s.p75);
    });

    test("rounds values to 2 decimal places", () => {
      const s = latencyStats([1, 2, 3]);
      const str = s.avg.toString();
      const decimals = str.includes(".") ? str.split(".")[1].length : 0;
      expect(decimals).toBeLessThanOrEqual(2);
    });
  });

  // ── groupEventStats ───────────────────────────────────────────
  describe("groupEventStats", () => {
    const events = [
      { duration_ms: 100, tokens_in: 10, tokens_out: 20, model: "gpt-4" },
      { duration_ms: 200, tokens_in: 30, tokens_out: 40, model: "gpt-4" },
      { duration_ms: 50, tokens_in: 5, tokens_out: 10, model: "claude" },
    ];

    test("groups by key function", () => {
      const groups = groupEventStats(events, (e) => e.model);
      expect(Object.keys(groups)).toEqual(expect.arrayContaining(["gpt-4", "claude"]));
      expect(groups["gpt-4"].count).toBe(2);
      expect(groups["claude"].count).toBe(1);
    });

    test("accumulates tokens correctly", () => {
      const groups = groupEventStats(events, (e) => e.model);
      expect(groups["gpt-4"].tokens_in).toBe(40);
      expect(groups["gpt-4"].tokens_out).toBe(60);
    });

    test("sorts durations ascending within groups", () => {
      const groups = groupEventStats(events, (e) => e.model);
      expect(groups["gpt-4"].durations).toEqual([100, 200]);
    });

    test("handles null/undefined tokens", () => {
      const noTokens = [{ duration_ms: 100, model: "test" }];
      const groups = groupEventStats(noTokens, (e) => e.model);
      expect(groups["test"].tokens_in).toBe(0);
      expect(groups["test"].tokens_out).toBe(0);
    });

    test("returns empty object for empty events", () => {
      const groups = groupEventStats([], (e) => e.model);
      expect(Object.keys(groups)).toHaveLength(0);
    });

    test("uses Object.create(null) for prototype-safe grouping", () => {
      const groups = groupEventStats(
        [{ duration_ms: 1, model: "constructor" }],
        (e) => e.model
      );
      expect(groups["constructor"].count).toBe(1);
      expect(groups.hasOwnProperty).toBeUndefined();
    });
  });

  // ── buildGroupPerf ────────────────────────────────────────────
  describe("buildGroupPerf", () => {
    test("builds performance breakdown from grouped stats", () => {
      const events = [
        { duration_ms: 100, tokens_in: 10, tokens_out: 20, model: "gpt-4" },
        { duration_ms: 200, tokens_in: 30, tokens_out: 40, model: "gpt-4" },
        { duration_ms: 50, tokens_in: 5, tokens_out: 10, model: "claude" },
      ];
      const groups = groupEventStats(events, (e) => e.model);
      const perf = buildGroupPerf(groups);

      expect(perf["gpt-4"]).toBeDefined();
      expect(perf["claude"]).toBeDefined();
    });

    test("includes count, latency, tokens, and throughput", () => {
      const events = [
        { duration_ms: 100, tokens_in: 50, tokens_out: 100, model: "test" },
        { duration_ms: 200, tokens_in: 60, tokens_out: 120, model: "test" },
      ];
      const groups = groupEventStats(events, (e) => e.model);
      const perf = buildGroupPerf(groups);
      const p = perf["test"];

      expect(p.count).toBe(2);
      expect(p.latency).not.toBeNull();
      expect(p.latency.min).toBe(100);
      expect(p.latency.max).toBe(200);
      expect(p.tokens.total_in).toBe(110);
      expect(p.tokens.total_out).toBe(220);
      expect(p.tokens.total).toBe(330);
      expect(p.tokens.avg_per_call).toBe(165);
      expect(p.tokens_per_second).toBeGreaterThan(0);
    });

    test("tokens_per_second is 0 when total duration is 0", () => {
      const groups = {
        "test": { durations: [], tokens_in: 0, tokens_out: 0, count: 0 }
      };
      // latencyStats returns null for empty, but tokens_per_second should be 0
      const perf = buildGroupPerf(groups);
      expect(perf["test"].tokens_per_second).toBe(0);
    });

    test("returns empty object for empty groups", () => {
      const perf = buildGroupPerf({});
      expect(Object.keys(perf)).toHaveLength(0);
    });

    test("computes correct tokens_per_second", () => {
      const events = [
        { duration_ms: 1000, tokens_in: 100, tokens_out: 100, model: "m" },
      ];
      const groups = groupEventStats(events, (e) => e.model);
      const perf = buildGroupPerf(groups);
      // 200 tokens in 1 second = 200 tokens/sec
      expect(perf["m"].tokens_per_second).toBe(200);
    });
  });

  // ── Integration: end-to-end pipeline ──────────────────────────
  describe("end-to-end pipeline", () => {
    test("full flow: events → groupEventStats → buildGroupPerf", () => {
      const events = [];
      for (let i = 0; i < 100; i++) {
        events.push({
          duration_ms: 10 + i,
          tokens_in: 5 + i,
          tokens_out: 10 + i,
          model: i % 3 === 0 ? "fast" : "slow",
        });
      }

      const groups = groupEventStats(events, (e) => e.model);
      const perf = buildGroupPerf(groups);

      expect(perf["fast"]).toBeDefined();
      expect(perf["slow"]).toBeDefined();

      // Verify fast group has correct count (0,3,6,...,99 = 34 items)
      expect(perf["fast"].count).toBe(34);
      expect(perf["slow"].count).toBe(66);

      // Latency p95 should be high
      expect(perf["fast"].latency.p95).toBeGreaterThan(perf["fast"].latency.p50);
      expect(perf["slow"].latency.p95).toBeGreaterThan(perf["slow"].latency.p50);
    });
  });
});
