/**
 * Tests for lib/session-metrics.js — extracted session metric computation.
 */

const { computeSessionMetrics, pctDelta } = require("../lib/session-metrics");

// ── pctDelta ──────────────────────────────────────────────────────────

describe("pctDelta", () => {
  test("returns 0 for (0, 0)", () => {
    expect(pctDelta(0, 0)).toBe(0);
  });

  test("returns 100 for (0, positive)", () => {
    expect(pctDelta(0, 42)).toBe(100);
  });

  test("returns -100 for (0, negative)", () => {
    expect(pctDelta(0, -5)).toBe(-100);
  });

  test("computes correct positive delta", () => {
    expect(pctDelta(100, 150)).toBe(50);
  });

  test("computes correct negative delta", () => {
    expect(pctDelta(200, 100)).toBe(-50);
  });

  test("rounds to 2 decimal places", () => {
    expect(pctDelta(3, 7)).toBeCloseTo(133.33, 2);
  });

  test("handles equal non-zero values", () => {
    expect(pctDelta(42, 42)).toBe(0);
  });
});

// ── computeSessionMetrics ─────────────────────────────────────────────

describe("computeSessionMetrics", () => {
  const baseSession = {
    session_id: "sess-001",
    agent_name: "test-agent",
    status: "completed",
    started_at: "2026-01-01T00:00:00Z",
    ended_at: "2026-01-01T00:05:00Z",
    total_tokens_in: 500,
    total_tokens_out: 200,
    metadata: '{"key": "val"}',
  };

  const baseEvents = [
    {
      event_type: "llm_call",
      model: "gpt-4",
      duration_ms: 120,
      tokens_in: 300,
      tokens_out: 150,
      tool_call: null,
    },
    {
      event_type: "tool_call",
      model: "gpt-4",
      duration_ms: 80,
      tokens_in: 200,
      tokens_out: 50,
      tool_call: { tool_name: "search" },
    },
    {
      event_type: "error",
      model: null,
      duration_ms: 5,
      tokens_in: 0,
      tokens_out: 0,
      tool_call: null,
    },
  ];

  test("returns correct token totals", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.tokens_in).toBe(500);
    expect(m.tokens_out).toBe(200);
    expect(m.total_tokens).toBe(700);
  });

  test("returns correct event count", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.event_count).toBe(3);
  });

  test("calculates error count from event types", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.error_count).toBe(1);
  });

  test("counts agent_error and tool_error as errors", () => {
    const events = [
      { event_type: "agent_error", duration_ms: 1 },
      { event_type: "tool_error", duration_ms: 1 },
      { event_type: "llm_call", duration_ms: 1 },
    ];
    const m = computeSessionMetrics(baseSession, events);
    expect(m.error_count).toBe(2);
  });

  test("breaks down event types", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.event_types).toEqual({
      llm_call: 1,
      tool_call: 1,
      error: 1,
    });
  });

  test("aggregates model usage", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.models["gpt-4"]).toEqual({
      calls: 2,
      tokens_in: 500,
      tokens_out: 200,
    });
  });

  test("aggregates tool usage", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.tools["search"]).toEqual({
      calls: 1,
      total_duration: 80,
    });
  });

  test("computes session duration in ms", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.session_duration_ms).toBe(5 * 60 * 1000); // 5 minutes
  });

  test("returns null session duration when no end time", () => {
    const session = { ...baseSession, ended_at: null };
    const m = computeSessionMetrics(session, baseEvents);
    expect(m.session_duration_ms).toBeNull();
  });

  test("calculates average event duration", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    const expectedAvg = (120 + 80 + 5) / 3;
    expect(m.avg_event_duration_ms).toBeCloseTo(expectedAvg, 1);
  });

  test("handles zero events gracefully", () => {
    const m = computeSessionMetrics(baseSession, []);
    expect(m.event_count).toBe(0);
    expect(m.error_count).toBe(0);
    expect(m.avg_event_duration_ms).toBe(0);
    expect(m.total_processing_ms).toBe(0);
    expect(m.models).toEqual({});
    expect(m.tools).toEqual({});
  });

  test("handles zero token session", () => {
    const session = { ...baseSession, total_tokens_in: 0, total_tokens_out: 0 };
    const m = computeSessionMetrics(session, []);
    expect(m.total_tokens).toBe(0);
  });

  test("parses metadata JSON string", () => {
    const m = computeSessionMetrics(baseSession, []);
    expect(m.metadata).toEqual({ key: "val" });
  });

  test("returns session identity fields", () => {
    const m = computeSessionMetrics(baseSession, baseEvents);
    expect(m.session_id).toBe("sess-001");
    expect(m.agent_name).toBe("test-agent");
    expect(m.status).toBe("completed");
  });
});
