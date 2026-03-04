/**
 * Tests for the Service Dependency Map feature.
 *
 * Covers: lib/dependency-map.js (pure logic) — 59 tests across 9 suites.
 */

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

// Helper: create a minimal event object
function mkEvent(overrides = {}) {
  return {
    event_id: "evt-" + Math.random().toString(36).slice(2, 8),
    session_id: overrides.session_id || "sess-1",
    event_type: overrides.event_type || "tool_call",
    timestamp: overrides.timestamp || "2026-03-01T10:00:00Z",
    tool_call: overrides.tool_call || "web_search",
    duration_ms: overrides.duration_ms ?? 100,
    tokens_in: overrides.tokens_in ?? 10,
    tokens_out: overrides.tokens_out ?? 20,
    output_data: overrides.output_data || null,
    agent_name: overrides.agent_name || "agent-alpha",
    ...overrides,
  };
}

// ── extractServiceName ──────────────────────────────────────────────

describe("extractServiceName", () => {
  test("returns null for null/undefined input", () => {
    expect(extractServiceName(null)).toBeNull();
    expect(extractServiceName(undefined)).toBeNull();
  });

  test("returns null for empty string", () => {
    expect(extractServiceName("")).toBeNull();
    expect(extractServiceName("   ")).toBeNull();
  });

  test("returns plain string as service name", () => {
    expect(extractServiceName("web_search")).toBe("web_search");
  });

  test("extracts name from JSON with name field", () => {
    expect(
      extractServiceName('{"name":"calculator","args":{"x":1}}')
    ).toBe("calculator");
  });

  test("extracts tool field from JSON", () => {
    expect(extractServiceName('{"tool":"file_read"}')).toBe("file_read");
  });

  test("extracts function field from JSON", () => {
    expect(extractServiceName('{"function":"get_weather"}')).toBe("get_weather");
  });

  test("extracts service field from JSON", () => {
    expect(extractServiceName('{"service":"database_query"}')).toBe("database_query");
  });

  test("returns null for JSON without recognized fields", () => {
    expect(extractServiceName('{"foo":"bar"}')).toBeNull();
  });

  test("returns null for invalid JSON starting with {", () => {
    expect(extractServiceName("{not valid json}")).toBeNull();
  });

  test("returns null for non-string input", () => {
    expect(extractServiceName(42)).toBeNull();
    expect(extractServiceName({})).toBeNull();
  });
});

// ── isFailure ───────────────────────────────────────────────────────

describe("isFailure", () => {
  test("detects error event_type", () => {
    expect(isFailure({ event_type: "error" })).toBe(true);
  });

  test("detects tool_error event_type", () => {
    expect(isFailure({ event_type: "tool_error" })).toBe(true);
  });

  test("detects error in output_data", () => {
    expect(
      isFailure({ event_type: "tool_call", output_data: '{"error":"timeout"}' })
    ).toBe(true);
  });

  test("detects status:fail in output_data", () => {
    expect(
      isFailure({ event_type: "tool_call", output_data: '{"status":"failed"}' })
    ).toBe(true);
  });

  test("detects success:false in output_data", () => {
    expect(
      isFailure({ event_type: "tool_call", output_data: '{"success":false}' })
    ).toBe(true);
  });

  test("returns false for successful event", () => {
    expect(
      isFailure({ event_type: "tool_call", output_data: '{"result":"ok"}' })
    ).toBe(false);
  });

  test("returns false when output_data is null", () => {
    expect(isFailure({ event_type: "tool_call", output_data: null })).toBe(false);
  });
});

// ── buildDependencyMap ──────────────────────────────────────────────

describe("buildDependencyMap", () => {
  test("returns empty map for empty events", () => {
    const map = buildDependencyMap([]);
    expect(Object.keys(map).length).toBe(0);
  });

  test("groups events by service name", () => {
    const events = [
      mkEvent({ tool_call: "web_search" }),
      mkEvent({ tool_call: "web_search" }),
      mkEvent({ tool_call: "calculator" }),
    ];
    const map = buildDependencyMap(events);
    expect(Object.keys(map).length).toBe(2);
    expect(map["web_search"].callCount).toBe(2);
    expect(map["calculator"].callCount).toBe(1);
  });

  test("tracks error counts", () => {
    const events = [
      mkEvent({ tool_call: "api_call", event_type: "error" }),
      mkEvent({ tool_call: "api_call", event_type: "tool_call" }),
    ];
    const map = buildDependencyMap(events);
    expect(map["api_call"].errorCount).toBe(1);
  });

  test("accumulates durations", () => {
    const events = [
      mkEvent({ tool_call: "db_query", duration_ms: 50 }),
      mkEvent({ tool_call: "db_query", duration_ms: 150 }),
    ];
    const map = buildDependencyMap(events);
    expect(map["db_query"].totalDurationMs).toBe(200);
    expect(map["db_query"].durations).toEqual([50, 150]);
  });

  test("tracks unique agents and sessions", () => {
    const events = [
      mkEvent({ tool_call: "tool1", agent_name: "a1", session_id: "s1" }),
      mkEvent({ tool_call: "tool1", agent_name: "a2", session_id: "s1" }),
      mkEvent({ tool_call: "tool1", agent_name: "a1", session_id: "s2" }),
    ];
    const map = buildDependencyMap(events);
    expect(map["tool1"].agents.size).toBe(2);
    expect(map["tool1"].sessions.size).toBe(2);
  });

  test("tracks first and last seen timestamps", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T08:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T12:00:00Z" }),
    ];
    const map = buildDependencyMap(events);
    expect(map["svc"].firstSeen).toBe("2026-03-01T08:00:00Z");
    expect(map["svc"].lastSeen).toBe("2026-03-01T12:00:00Z");
  });

  test("accumulates tokens", () => {
    const events = [
      mkEvent({ tool_call: "llm", tokens_in: 100, tokens_out: 200 }),
      mkEvent({ tool_call: "llm", tokens_in: 50, tokens_out: 80 }),
    ];
    const map = buildDependencyMap(events);
    expect(map["llm"].totalTokensIn).toBe(150);
    expect(map["llm"].totalTokensOut).toBe(280);
  });

  test("skips events with null tool_call", () => {
    const events = [
      mkEvent({ tool_call: null }),
      mkEvent({ tool_call: "valid_tool" }),
    ];
    const map = buildDependencyMap(events);
    expect(Object.keys(map).length).toBe(1);
  });
});

// ── computeServiceStats ─────────────────────────────────────────────

describe("computeServiceStats", () => {
  test("returns empty array for empty map", () => {
    expect(computeServiceStats({})).toEqual([]);
  });

  test("computes error rate and reliability", () => {
    const events = [
      mkEvent({ tool_call: "svc", event_type: "error" }),
      mkEvent({ tool_call: "svc" }),
      mkEvent({ tool_call: "svc" }),
      mkEvent({ tool_call: "svc" }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].errorRate).toBe(25);
    expect(stats[0].reliability).toBe(75);
  });

  test("includes latency stats", () => {
    const events = [
      mkEvent({ tool_call: "svc", duration_ms: 100 }),
      mkEvent({ tool_call: "svc", duration_ms: 200 }),
      mkEvent({ tool_call: "svc", duration_ms: 300 }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].latency).toBeTruthy();
    expect(stats[0].latency.min).toBe(100);
    expect(stats[0].latency.max).toBe(300);
  });

  test("sorts by call count descending", () => {
    const events = [
      mkEvent({ tool_call: "rare" }),
      mkEvent({ tool_call: "common" }),
      mkEvent({ tool_call: "common" }),
      mkEvent({ tool_call: "common" }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].service).toBe("common");
    expect(stats[1].service).toBe("rare");
  });

  test("computes total tokens", () => {
    const events = [
      mkEvent({ tool_call: "svc", tokens_in: 100, tokens_out: 200 }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].totalTokens).toBe(300);
  });

  test("reports unique agents and sessions counts", () => {
    const events = [
      mkEvent({ tool_call: "t", agent_name: "a1", session_id: "s1" }),
      mkEvent({ tool_call: "t", agent_name: "a2", session_id: "s2" }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].uniqueAgents).toBe(2);
    expect(stats[0].uniqueSessions).toBe(2);
  });
});

// ── identifyCriticalDependencies ────────────────────────────────────

describe("identifyCriticalDependencies", () => {
  test("returns empty for empty input", () => {
    expect(identifyCriticalDependencies([])).toEqual([]);
  });

  test("flags high-volume services", () => {
    const stats = [
      { service: "big", callCount: 80, errorCount: 0, errorRate: 0, latency: null },
      { service: "small", callCount: 20, errorCount: 0, errorRate: 0, latency: null },
    ];
    const critical = identifyCriticalDependencies(stats, { criticalSharePct: 50 });
    expect(critical.length).toBe(1);
    expect(critical[0].service).toBe("big");
    expect(critical[0].criticalReasons[0]).toContain("high_volume");
  });

  test("flags high error rate services", () => {
    const stats = [
      { service: "flaky", callCount: 10, errorCount: 5, errorRate: 50, latency: null },
      { service: "solid", callCount: 90, errorCount: 0, errorRate: 0, latency: null },
    ];
    const critical = identifyCriticalDependencies(stats, {
      errorThresholdPct: 10,
      criticalSharePct: 101,
    });
    expect(critical.length).toBe(1);
    expect(critical[0].service).toBe("flaky");
  });

  test("flags high latency services", () => {
    const stats = [
      {
        service: "slow",
        callCount: 10,
        errorCount: 0,
        errorRate: 0,
        latency: { p95: 8000 },
      },
    ];
    const critical = identifyCriticalDependencies(stats, {
      latencyThresholdMs: 5000,
      criticalSharePct: 101,
      errorThresholdPct: 101,
    });
    expect(critical.length).toBe(1);
    expect(critical[0].criticalReasons[0]).toContain("high_latency");
  });

  test("includes callSharePct in result", () => {
    const stats = [
      { service: "main", callCount: 100, errorCount: 0, errorRate: 0, latency: null },
    ];
    const critical = identifyCriticalDependencies(stats, { criticalSharePct: 50 });
    expect(critical[0].callSharePct).toBe(100);
  });

  test("multiple reasons can be flagged simultaneously", () => {
    const stats = [
      {
        service: "bad",
        callCount: 100,
        errorCount: 50,
        errorRate: 50,
        latency: { p95: 10000 },
      },
    ];
    const critical = identifyCriticalDependencies(stats, {
      criticalSharePct: 50,
      errorThresholdPct: 10,
      latencyThresholdMs: 5000,
    });
    expect(critical[0].criticalReasons.length).toBe(3);
  });
});

// ── agentDependencyProfiles ─────────────────────────────────────────

describe("agentDependencyProfiles", () => {
  test("returns empty for no events", () => {
    expect(Object.keys(agentDependencyProfiles([])).length).toBe(0);
  });

  test("groups dependencies per agent", () => {
    const events = [
      mkEvent({ tool_call: "search", agent_name: "agent-a" }),
      mkEvent({ tool_call: "search", agent_name: "agent-a" }),
      mkEvent({ tool_call: "calc", agent_name: "agent-a" }),
      mkEvent({ tool_call: "search", agent_name: "agent-b" }),
    ];
    const profiles = agentDependencyProfiles(events);
    expect(Object.keys(profiles).length).toBe(2);
    expect(profiles["agent-a"].length).toBe(2);
    expect(profiles["agent-a"][0].service).toBe("search");
    expect(profiles["agent-a"][0].callCount).toBe(2);
  });

  test("tracks per-agent error counts", () => {
    const events = [
      mkEvent({ tool_call: "api", agent_name: "ag1", event_type: "error" }),
      mkEvent({ tool_call: "api", agent_name: "ag1" }),
    ];
    const profiles = agentDependencyProfiles(events);
    expect(profiles["ag1"][0].errorCount).toBe(1);
  });

  test("sorts services by call count descending within agent", () => {
    const events = [
      mkEvent({ tool_call: "rare", agent_name: "ag" }),
      mkEvent({ tool_call: "common", agent_name: "ag" }),
      mkEvent({ tool_call: "common", agent_name: "ag" }),
    ];
    const profiles = agentDependencyProfiles(events);
    expect(profiles["ag"][0].service).toBe("common");
  });

  test("skips events without agent_name", () => {
    const events = [
      mkEvent({ tool_call: "svc", agent_name: null }),
      mkEvent({ tool_call: "svc", agent_name: undefined }),
    ];
    const profiles = agentDependencyProfiles(events);
    expect(Object.keys(profiles).length).toBe(0);
  });
});

// ── detectServiceCoOccurrence ───────────────────────────────────────

describe("detectServiceCoOccurrence", () => {
  test("returns empty for no events", () => {
    expect(detectServiceCoOccurrence([])).toEqual([]);
  });

  test("detects co-occurring services", () => {
    const events = [
      mkEvent({ tool_call: "search", session_id: "s1" }),
      mkEvent({ tool_call: "calc", session_id: "s1" }),
      mkEvent({ tool_call: "search", session_id: "s2" }),
      mkEvent({ tool_call: "calc", session_id: "s2" }),
    ];
    const pairs = detectServiceCoOccurrence(events, 2);
    expect(pairs.length).toBe(1);
    expect(pairs[0].services).toEqual(["calc", "search"]);
    expect(pairs[0].coOccurrenceCount).toBe(2);
  });

  test("filters below min occurrence", () => {
    const events = [
      mkEvent({ tool_call: "a", session_id: "s1" }),
      mkEvent({ tool_call: "b", session_id: "s1" }),
    ];
    const pairs = detectServiceCoOccurrence(events, 2);
    expect(pairs.length).toBe(0);
  });

  test("handles single-service sessions", () => {
    const events = [
      mkEvent({ tool_call: "only", session_id: "s1" }),
      mkEvent({ tool_call: "only", session_id: "s2" }),
    ];
    const pairs = detectServiceCoOccurrence(events, 1);
    expect(pairs.length).toBe(0);
  });

  test("sorts by co-occurrence count descending", () => {
    const events = [];
    for (let i = 0; i < 3; i++) {
      events.push(mkEvent({ tool_call: "a", session_id: "sa" + i }));
      events.push(mkEvent({ tool_call: "b", session_id: "sa" + i }));
    }
    for (let i = 0; i < 5; i++) {
      events.push(mkEvent({ tool_call: "c", session_id: "sb" + i }));
      events.push(mkEvent({ tool_call: "d", session_id: "sb" + i }));
    }
    const pairs = detectServiceCoOccurrence(events, 1);
    expect(pairs[0].services.join(",")).toBe("c,d");
    expect(pairs[0].coOccurrenceCount).toBe(5);
  });

  test("handles three services in one session", () => {
    const events = [
      mkEvent({ tool_call: "a", session_id: "s1" }),
      mkEvent({ tool_call: "b", session_id: "s1" }),
      mkEvent({ tool_call: "c", session_id: "s1" }),
      mkEvent({ tool_call: "a", session_id: "s2" }),
      mkEvent({ tool_call: "b", session_id: "s2" }),
      mkEvent({ tool_call: "c", session_id: "s2" }),
    ];
    const pairs = detectServiceCoOccurrence(events, 2);
    expect(pairs.length).toBe(3);
  });
});

// ── serviceTrend ────────────────────────────────────────────────────

describe("serviceTrend", () => {
  test("returns empty for no matching events", () => {
    const events = [mkEvent({ tool_call: "other" })];
    expect(serviceTrend(events, "missing")).toEqual([]);
  });

  test("groups by day by default", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T14:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-02T10:00:00Z" }),
    ];
    const trend = serviceTrend(events, "svc");
    expect(trend.length).toBe(2);
    expect(trend[0].period).toBe("2026-03-01");
    expect(trend[0].calls).toBe(2);
    expect(trend[1].period).toBe("2026-03-02");
    expect(trend[1].calls).toBe(1);
  });

  test("groups by hour when specified", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:30:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T11:00:00Z" }),
    ];
    const trend = serviceTrend(events, "svc", "hour");
    expect(trend.length).toBe(2);
    expect(trend[0].period).toBe("2026-03-01T10");
    expect(trend[0].calls).toBe(2);
  });

  test("groups by week when specified", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-02T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-08T10:00:00Z" }),
    ];
    const trend = serviceTrend(events, "svc", "week");
    expect(trend.length).toBe(2);
  });

  test("computes error rate per period", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z", event_type: "error" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T11:00:00Z" }),
    ];
    const trend = serviceTrend(events, "svc");
    expect(trend[0].errors).toBe(1);
    expect(trend[0].errorRate).toBe(50);
  });

  test("sorts periods chronologically", () => {
    const events = [
      mkEvent({ tool_call: "svc", timestamp: "2026-03-05T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "svc", timestamp: "2026-03-03T10:00:00Z" }),
    ];
    const trend = serviceTrend(events, "svc");
    expect(trend[0].period).toBe("2026-03-01");
    expect(trend[1].period).toBe("2026-03-03");
    expect(trend[2].period).toBe("2026-03-05");
  });

  test("ignores events for other services", () => {
    const events = [
      mkEvent({ tool_call: "target", timestamp: "2026-03-01T10:00:00Z" }),
      mkEvent({ tool_call: "other", timestamp: "2026-03-01T10:00:00Z" }),
    ];
    const trend = serviceTrend(events, "target");
    expect(trend.length).toBe(1);
    expect(trend[0].calls).toBe(1);
  });
});

// ── Integration: end-to-end pipeline ────────────────────────────────

describe("Integration: full dependency analysis pipeline", () => {
  test("processes a realistic event set", () => {
    const events = [];
    for (let i = 0; i < 50; i++) {
      events.push(
        mkEvent({
          tool_call: '{"name":"web_search","args":{"q":"test"}}',
          duration_ms: 100 + i * 10,
          event_type: i < 5 ? "error" : "tool_call",
          agent_name: i < 25 ? "agent-1" : "agent-2",
          session_id: "s" + (i % 10),
          timestamp: "2026-03-0" + (1 + (i % 5)) + "T10:00:00Z",
        })
      );
    }
    for (let i = 0; i < 20; i++) {
      events.push(
        mkEvent({
          tool_call: "calculator",
          duration_ms: 10 + i,
          agent_name: "agent-1",
          session_id: "s" + (i % 5),
          timestamp: "2026-03-0" + (1 + (i % 3)) + "T12:00:00Z",
        })
      );
    }

    const rawMap = buildDependencyMap(events);
    const stats = computeServiceStats(rawMap);
    expect(stats.length).toBe(2);
    expect(stats[0].service).toBe("web_search");

    const critical = identifyCriticalDependencies(stats, {
      criticalSharePct: 50,
      errorThresholdPct: 5,
    });
    expect(critical.length).toBeGreaterThan(0);

    const profiles = agentDependencyProfiles(events);
    expect("agent-1" in profiles).toBe(true);
    expect("agent-2" in profiles).toBe(true);

    const coOcc = detectServiceCoOccurrence(events, 1);
    expect(coOcc.length).toBeGreaterThan(0);

    const trend = serviceTrend(events, "web_search", "day");
    expect(trend.length).toBeGreaterThan(0);
  });

  test("handles events with JSON tool_call correctly through full pipeline", () => {
    const events = [
      mkEvent({ tool_call: '{"name":"api_call","args":{}}', session_id: "s1" }),
      mkEvent({ tool_call: '{"tool":"api_call"}', session_id: "s1" }),
    ];
    const map = buildDependencyMap(events);
    expect(Object.keys(map).length).toBe(1);
    expect(map["api_call"].callCount).toBe(2);
  });

  test("handles zero-duration events gracefully", () => {
    const events = [
      mkEvent({ tool_call: "fast", duration_ms: 0 }),
      mkEvent({ tool_call: "fast", duration_ms: 0 }),
    ];
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats[0].latency).toBeNull();
  });

  test("handles large event sets without errors", () => {
    const events = [];
    for (let i = 0; i < 1000; i++) {
      events.push(
        mkEvent({
          tool_call: "service_" + (i % 20),
          session_id: "s" + (i % 50),
          agent_name: "agent_" + (i % 5),
          duration_ms: Math.random() * 1000,
          timestamp: new Date(Date.UTC(2026, 2, 1) + i * 60000).toISOString(),
        })
      );
    }
    const map = buildDependencyMap(events);
    const stats = computeServiceStats(map);
    expect(stats.length).toBe(20);

    const critical = identifyCriticalDependencies(stats);
    expect(Array.isArray(critical)).toBe(true);

    const profiles = agentDependencyProfiles(events);
    expect(Object.keys(profiles).length).toBe(5);

    const coOcc = detectServiceCoOccurrence(events, 5);
    expect(Array.isArray(coOcc)).toBe(true);
  });
});
