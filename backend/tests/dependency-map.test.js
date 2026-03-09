"use strict";

const {
  extractServiceName,
  isFailure,
  buildDependencyMap,
  computeServiceStats,
  identifyCriticalDependencies,
  agentDependencyProfiles,
  detectServiceCoOccurrence,
  serviceTrend,
} = require("../lib/dependency-map");

// ── Helpers ──

function makeEvent(overrides) {
  return {
    tool_call: null,
    event_type: "tool_call",
    duration_ms: "100",
    tokens_in: "10",
    tokens_out: "20",
    session_id: "s1",
    agent_name: "agent-a",
    timestamp: "2026-03-08T12:00:00Z",
    output_data: null,
    ...overrides,
  };
}

describe("dependency-map", () => {
  // ── extractServiceName ──

  describe("extractServiceName", () => {
    test("returns null for null/undefined/empty", () => {
      expect(extractServiceName(null)).toBeNull();
      expect(extractServiceName(undefined)).toBeNull();
      expect(extractServiceName("")).toBeNull();
      expect(extractServiceName("  ")).toBeNull();
    });

    test("extracts name from JSON string", () => {
      expect(extractServiceName('{"name":"web_search","args":{}}')).toBe("web_search");
    });

    test("extracts tool field from JSON", () => {
      expect(extractServiceName('{"tool":"calculator"}')).toBe("calculator");
    });

    test("extracts function field from JSON", () => {
      expect(extractServiceName('{"function":"get_weather"}')).toBe("get_weather");
    });

    test("returns plain string as service name", () => {
      expect(extractServiceName("web_search")).toBe("web_search");
    });

    test("returns null for malformed JSON", () => {
      expect(extractServiceName("{broken json")).toBeNull();
    });

    test("returns null for JSON without name/tool/function", () => {
      expect(extractServiceName('{"args":"something"}')).toBeNull();
    });

    test("handles non-string input", () => {
      expect(extractServiceName(42)).toBeNull();
      expect(extractServiceName({})).toBeNull();
    });
  });

  // ── isFailure ──

  describe("isFailure", () => {
    test("detects error event_type", () => {
      expect(isFailure({ event_type: "error" })).toBe(true);
      expect(isFailure({ event_type: "tool_error" })).toBe(true);
    });

    test("detects error in output_data", () => {
      expect(isFailure({ event_type: "tool_call", output_data: '{"error":"timeout"}' })).toBe(true);
      expect(isFailure({ event_type: "tool_call", output_data: '{"status":"fail"}' })).toBe(true);
      expect(isFailure({ event_type: "tool_call", output_data: '{"success":false}' })).toBe(true);
    });

    test("returns false for successful events", () => {
      expect(isFailure({ event_type: "tool_call", output_data: '{"result":"ok"}' })).toBe(false);
      expect(isFailure({ event_type: "tool_call" })).toBe(false);
    });
  });

  // ── buildDependencyMap ──

  describe("buildDependencyMap", () => {
    test("returns empty object for no events", () => {
      expect(buildDependencyMap([])).toEqual({});
    });

    test("skips events without tool_call", () => {
      const events = [makeEvent({ tool_call: null })];
      expect(Object.keys(buildDependencyMap(events))).toHaveLength(0);
    });

    test("builds map with call counts", () => {
      const events = [
        makeEvent({ tool_call: "web_search" }),
        makeEvent({ tool_call: "web_search" }),
        makeEvent({ tool_call: "calculator" }),
      ];
      const map = buildDependencyMap(events);
      expect(Object.keys(map)).toHaveLength(2);
      expect(map.web_search.callCount).toBe(2);
      expect(map.calculator.callCount).toBe(1);
    });

    test("tracks errors", () => {
      const events = [
        makeEvent({ tool_call: "api", event_type: "error" }),
        makeEvent({ tool_call: "api" }),
      ];
      const map = buildDependencyMap(events);
      expect(map.api.callCount).toBe(2);
      expect(map.api.errorCount).toBe(1);
    });

    test("accumulates tokens", () => {
      const events = [
        makeEvent({ tool_call: "llm", tokens_in: "100", tokens_out: "200" }),
        makeEvent({ tool_call: "llm", tokens_in: "50", tokens_out: "75" }),
      ];
      const map = buildDependencyMap(events);
      expect(map.llm.totalTokensIn).toBe(150);
      expect(map.llm.totalTokensOut).toBe(275);
    });

    test("tracks unique agents and sessions", () => {
      const events = [
        makeEvent({ tool_call: "svc", agent_name: "a1", session_id: "s1" }),
        makeEvent({ tool_call: "svc", agent_name: "a2", session_id: "s1" }),
        makeEvent({ tool_call: "svc", agent_name: "a1", session_id: "s2" }),
      ];
      const map = buildDependencyMap(events);
      expect(map.svc.agents.size).toBe(2);
      expect(map.svc.sessions.size).toBe(2);
    });

    test("tracks first/last seen timestamps", () => {
      const events = [
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T10:00:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T14:00:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T08:00:00Z" }),
      ];
      const map = buildDependencyMap(events);
      expect(map.svc.firstSeen).toBe("2026-03-08T08:00:00Z");
      expect(map.svc.lastSeen).toBe("2026-03-08T14:00:00Z");
    });
  });

  // ── computeServiceStats ──

  describe("computeServiceStats", () => {
    test("computes stats sorted by call count", () => {
      const events = [
        makeEvent({ tool_call: "a", duration_ms: "100" }),
        makeEvent({ tool_call: "b", duration_ms: "200" }),
        makeEvent({ tool_call: "b", duration_ms: "300" }),
      ];
      const map = buildDependencyMap(events);
      const stats = computeServiceStats(map);
      expect(stats).toHaveLength(2);
      expect(stats[0].service).toBe("b"); // most calls
      expect(stats[0].callCount).toBe(2);
      expect(stats[1].service).toBe("a");
    });

    test("computes error rate and reliability", () => {
      const events = [
        makeEvent({ tool_call: "svc" }),
        makeEvent({ tool_call: "svc" }),
        makeEvent({ tool_call: "svc", event_type: "error" }),
        makeEvent({ tool_call: "svc", event_type: "error" }),
      ];
      const map = buildDependencyMap(events);
      const stats = computeServiceStats(map);
      expect(stats[0].errorRate).toBe(50);
      expect(stats[0].reliability).toBe(50);
    });

    test("computes total tokens", () => {
      const events = [
        makeEvent({ tool_call: "svc", tokens_in: "10", tokens_out: "20" }),
      ];
      const map = buildDependencyMap(events);
      const stats = computeServiceStats(map);
      expect(stats[0].totalTokens).toBe(30);
    });
  });

  // ── identifyCriticalDependencies ──

  describe("identifyCriticalDependencies", () => {
    test("returns empty for no services", () => {
      expect(identifyCriticalDependencies([])).toEqual([]);
    });

    test("flags high-volume services", () => {
      const stats = [
        { service: "big", callCount: 90, errorRate: 0, latency: { p95: 100 } },
        { service: "small", callCount: 10, errorRate: 0, latency: { p95: 100 } },
      ];
      const critical = identifyCriticalDependencies(stats);
      expect(critical).toHaveLength(1);
      expect(critical[0].service).toBe("big");
      expect(critical[0].criticalReasons[0]).toContain("high_volume");
    });

    test("flags high error rate", () => {
      const stats = [
        { service: "flaky", callCount: 5, errorRate: 50, latency: { p95: 100 } },
        { service: "stable", callCount: 95, errorRate: 1, latency: { p95: 100 } },
      ];
      const critical = identifyCriticalDependencies(stats, { errorThresholdPct: 10 });
      const flaky = critical.find(c => c.service === "flaky");
      expect(flaky).toBeDefined();
      expect(flaky.criticalReasons.some(r => r.includes("high_error_rate"))).toBe(true);
    });

    test("flags high latency", () => {
      const stats = [
        { service: "slow", callCount: 10, errorRate: 0, latency: { p95: 10000 } },
        { service: "fast", callCount: 90, errorRate: 0, latency: { p95: 50 } },
      ];
      const critical = identifyCriticalDependencies(stats, { latencyThresholdMs: 5000 });
      const slow = critical.find(c => c.service === "slow");
      expect(slow).toBeDefined();
      expect(slow.criticalReasons.some(r => r.includes("high_latency"))).toBe(true);
    });
  });

  // ── agentDependencyProfiles ──

  describe("agentDependencyProfiles", () => {
    test("groups services by agent", () => {
      const events = [
        makeEvent({ tool_call: "search", agent_name: "bot-a" }),
        makeEvent({ tool_call: "search", agent_name: "bot-a" }),
        makeEvent({ tool_call: "calc", agent_name: "bot-a" }),
        makeEvent({ tool_call: "search", agent_name: "bot-b" }),
      ];
      const profiles = agentDependencyProfiles(events);
      expect(Object.keys(profiles)).toHaveLength(2);
      expect(profiles["bot-a"]).toHaveLength(2);
      expect(profiles["bot-a"][0].service).toBe("search"); // sorted by count
      expect(profiles["bot-a"][0].callCount).toBe(2);
      expect(profiles["bot-b"]).toHaveLength(1);
    });

    test("skips events without agent_name", () => {
      const events = [
        makeEvent({ tool_call: "search", agent_name: null }),
      ];
      expect(agentDependencyProfiles(events)).toEqual({});
    });
  });

  // ── detectServiceCoOccurrence ──

  describe("detectServiceCoOccurrence", () => {
    test("detects co-occurring services", () => {
      const events = [
        makeEvent({ tool_call: "search", session_id: "s1" }),
        makeEvent({ tool_call: "browse", session_id: "s1" }),
        makeEvent({ tool_call: "search", session_id: "s2" }),
        makeEvent({ tool_call: "browse", session_id: "s2" }),
      ];
      const pairs = detectServiceCoOccurrence(events, 2);
      expect(pairs).toHaveLength(1);
      expect(pairs[0].services).toEqual(["browse", "search"]);
      expect(pairs[0].coOccurrenceCount).toBe(2);
    });

    test("filters below threshold", () => {
      const events = [
        makeEvent({ tool_call: "a", session_id: "s1" }),
        makeEvent({ tool_call: "b", session_id: "s1" }),
      ];
      // Only 1 co-occurrence, threshold 2
      expect(detectServiceCoOccurrence(events, 2)).toHaveLength(0);
    });

    test("handles empty events", () => {
      expect(detectServiceCoOccurrence([])).toEqual([]);
    });
  });

  // ── serviceTrend ──

  describe("serviceTrend", () => {
    test("groups by day", () => {
      const events = [
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T10:00:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T14:00:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-09T10:00:00Z", event_type: "error" }),
      ];
      const trend = serviceTrend(events, "svc", "day");
      expect(trend).toHaveLength(2);
      expect(trend[0].period).toBe("2026-03-08");
      expect(trend[0].calls).toBe(2);
      expect(trend[0].errors).toBe(0);
      expect(trend[1].period).toBe("2026-03-09");
      expect(trend[1].calls).toBe(1);
      expect(trend[1].errors).toBe(1);
      expect(trend[1].errorRate).toBe(100);
    });

    test("groups by hour", () => {
      const events = [
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T10:00:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T10:30:00Z" }),
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T11:00:00Z" }),
      ];
      const trend = serviceTrend(events, "svc", "hour");
      expect(trend).toHaveLength(2);
      expect(trend[0].period).toBe("2026-03-08T10");
      expect(trend[0].calls).toBe(2);
    });

    test("filters by service name", () => {
      const events = [
        makeEvent({ tool_call: "svc", timestamp: "2026-03-08T10:00:00Z" }),
        makeEvent({ tool_call: "other", timestamp: "2026-03-08T10:00:00Z" }),
      ];
      const trend = serviceTrend(events, "svc");
      expect(trend).toHaveLength(1);
      expect(trend[0].calls).toBe(1);
    });

    test("returns empty for no matching events", () => {
      expect(serviceTrend([], "svc")).toEqual([]);
    });
  });
});
