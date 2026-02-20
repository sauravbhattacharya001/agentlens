/**
 * Tests for backend/lib/explain.js — human-readable explanation generator.
 *
 * Covers generateExplanation, truncate, and formatDuration helpers.
 */

const { generateExplanation, truncate, formatDuration } = require("../lib/explain");

/* ================================================================
 * truncate
 * ================================================================ */
describe("truncate", () => {
  test("returns empty string for null/undefined", () => {
    expect(truncate(null, 10)).toBe("");
    expect(truncate(undefined, 10)).toBe("");
    expect(truncate("", 10)).toBe("");
  });

  test("returns short strings unchanged", () => {
    expect(truncate("hello", 10)).toBe("hello");
    expect(truncate("exact", 5)).toBe("exact");
  });

  test("truncates long strings with ellipsis", () => {
    expect(truncate("hello world", 5)).toBe("hello…");
    expect(truncate("abcdef", 3)).toBe("abc…");
  });

  test("handles zero maxLen", () => {
    expect(truncate("hello", 0)).toBe("…");
  });
});

/* ================================================================
 * formatDuration
 * ================================================================ */
describe("formatDuration", () => {
  test("returns 'unknown' for missing start", () => {
    expect(formatDuration(null, null)).toBe("unknown");
    expect(formatDuration(undefined, "2024-01-01")).toBe("unknown");
    expect(formatDuration("", "2024-01-01")).toBe("unknown");
  });

  test("returns 'ongoing' for missing end", () => {
    expect(formatDuration("2024-01-01T00:00:00Z", null)).toBe("ongoing");
    expect(formatDuration("2024-01-01T00:00:00Z", undefined)).toBe("ongoing");
  });

  test("formats millisecond durations", () => {
    const start = "2024-01-01T00:00:00.000Z";
    const end = "2024-01-01T00:00:00.500Z";
    expect(formatDuration(start, end)).toBe("500ms");
  });

  test("formats sub-millisecond as 0ms", () => {
    const start = "2024-01-01T00:00:00.000Z";
    const end = "2024-01-01T00:00:00.000Z";
    expect(formatDuration(start, end)).toBe("0ms");
  });

  test("formats second durations", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T00:00:05Z";
    expect(formatDuration(start, end)).toBe("5.0s");
  });

  test("formats fractional second durations", () => {
    const start = "2024-01-01T00:00:00.000Z";
    const end = "2024-01-01T00:00:02.500Z";
    expect(formatDuration(start, end)).toBe("2.5s");
  });

  test("formats minute durations", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T00:03:00Z";
    expect(formatDuration(start, end)).toBe("3.0m");
  });

  test("formats multi-minute durations", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T00:10:30Z";
    expect(formatDuration(start, end)).toBe("10.5m");
  });
});

/* ================================================================
 * generateExplanation
 * ================================================================ */
describe("generateExplanation", () => {
  const baseSession = {
    agent_name: "test-agent",
    started_at: "2024-01-01T00:00:00Z",
    ended_at: "2024-01-01T00:01:00Z",
    total_tokens_in: 500,
    total_tokens_out: 200,
    status: "completed",
    metadata: "{}",
  };

  test("generates explanation for empty event list", () => {
    const result = generateExplanation(baseSession, []);
    expect(result).toContain("## Agent Session: test-agent");
    expect(result).toContain("**Duration:**");
    expect(result).toContain("Total tokens used:** 700");
    expect(result).toContain("### Summary");
    expect(result).toContain("0 LLM call(s), 0 tool call(s), 0 error(s)");
    expect(result).toContain("completed");
  });

  test("generates explanation for LLM call events", () => {
    const events = [
      {
        event_type: "llm_call",
        model: "gpt-4o",
        input_data: JSON.stringify({ prompt: "What is the weather?" }),
        output_data: JSON.stringify({ response: "I cannot check real weather." }),
        tool_call: null,
        decision_trace: JSON.stringify({ reasoning: "User wants weather info" }),
        duration_ms: 450,
        tokens_in: 50,
        tokens_out: 30,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("Step 1");
    expect(result).toContain("LLM call");
    expect(result).toContain("gpt-4o");
    expect(result).toContain("What is the weather?");
    expect(result).toContain("cannot check real weather");
    expect(result).toContain("50 in / 30 out");
    expect(result).toContain("User wants weather info");
    expect(result).toContain("1 LLM call(s)");
  });

  test("generates explanation for tool call events", () => {
    const events = [
      {
        event_type: "tool_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: JSON.stringify({
          tool_name: "web_search",
          tool_input: { query: "weather NYC" },
          tool_output: { results: ["sunny, 72°F"] },
        }),
        decision_trace: null,
        duration_ms: 1200.5,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("Step 1");
    expect(result).toContain("web_search");
    expect(result).toContain("weather NYC");
    expect(result).toContain("1200.5ms");
    expect(result).toContain("1 tool call(s)");
  });

  test("generates explanation for agent_call events", () => {
    const events = [
      {
        event_type: "agent_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: null,
        decision_trace: JSON.stringify({ reasoning: "Delegating to sub-agent" }),
        duration_ms: 800,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("Agent function executed");
    expect(result).toContain("Delegating to sub-agent");
    expect(result).toContain("800.0ms");
  });

  test("numbers steps sequentially across mixed event types", () => {
    const events = [
      {
        event_type: "llm_call",
        model: "gpt-4",
        input_data: JSON.stringify({ prompt: "step one" }),
        output_data: JSON.stringify({ response: "done" }),
        tool_call: null,
        decision_trace: null,
        duration_ms: 100,
        tokens_in: 10,
        tokens_out: 5,
      },
      {
        event_type: "tool_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: JSON.stringify({ tool_name: "calculator" }),
        decision_trace: null,
        duration_ms: 50,
        tokens_in: 0,
        tokens_out: 0,
      },
      {
        event_type: "agent_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: null,
        decision_trace: null,
        duration_ms: 200,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("**Step 1:**");
    expect(result).toContain("**Step 2:**");
    expect(result).toContain("**Step 3:**");
  });

  test("skips non-actionable event types in step listing", () => {
    const events = [
      {
        event_type: "session_start",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: null,
        decision_trace: null,
        duration_ms: null,
        tokens_in: 0,
        tokens_out: 0,
      },
      {
        event_type: "error",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: null,
        decision_trace: null,
        duration_ms: null,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    // session_start and error don't generate Step entries
    expect(result).not.toContain("Step 1");
    // But they DO count in the error summary
    expect(result).toContain("1 error(s)");
  });

  test("handles missing/null fields gracefully", () => {
    const events = [
      {
        event_type: "llm_call",
        model: null, // no model
        input_data: null, // no input
        output_data: null, // no output
        tool_call: null,
        decision_trace: null,
        duration_ms: null,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    // Should not throw
    const result = generateExplanation(baseSession, events);
    expect(result).toContain("LLM call");
    expect(result).toContain("unknown prompt");
    expect(result).toContain("unknown response");
    // No model should mean it doesn't add "using <model>"
    expect(result).not.toContain("using null");
  });

  test("handles LLM call without tokens (no token line)", () => {
    const events = [
      {
        event_type: "llm_call",
        model: "gpt-4o",
        input_data: JSON.stringify({ prompt: "hi" }),
        output_data: JSON.stringify({ response: "hello" }),
        tool_call: null,
        decision_trace: null,
        duration_ms: null,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    // tokens_in and tokens_out are both 0, so the tokens line should not appear
    expect(result).not.toContain("*Tokens:*");
  });

  test("truncates long prompt/response in explanation", () => {
    const longPrompt = "x".repeat(200);
    const longResponse = "y".repeat(200);
    const events = [
      {
        event_type: "llm_call",
        model: "gpt-4",
        input_data: JSON.stringify({ prompt: longPrompt }),
        output_data: JSON.stringify({ response: longResponse }),
        tool_call: null,
        decision_trace: null,
        duration_ms: 100,
        tokens_in: 100,
        tokens_out: 50,
      },
    ];

    const result = generateExplanation(baseSession, events);
    // Input/output lines should be truncated (120 chars + ellipsis)
    const inputLine = result.split("\n").find((l) => l.includes("*Input:*"));
    const outputLine = result.split("\n").find((l) => l.includes("*Output:*"));
    // The quoted content should be <= 121 chars (120 + ellipsis)
    expect(inputLine.length).toBeLessThan(200);
    expect(outputLine.length).toBeLessThan(200);
  });

  test("calculates correct summary counts", () => {
    const events = [
      { event_type: "llm_call", model: "gpt-4", input_data: '{"prompt":"a"}', output_data: '{"response":"b"}', tool_call: null, decision_trace: null, duration_ms: null, tokens_in: 10, tokens_out: 5 },
      { event_type: "llm_call", model: "gpt-4", input_data: '{"prompt":"c"}', output_data: '{"response":"d"}', tool_call: null, decision_trace: null, duration_ms: null, tokens_in: 10, tokens_out: 5 },
      { event_type: "tool_call", model: null, input_data: null, output_data: null, tool_call: '{"tool_name":"calc"}', decision_trace: null, duration_ms: 50, tokens_in: 0, tokens_out: 0 },
      { event_type: "error", model: null, input_data: null, output_data: null, tool_call: null, decision_trace: null, duration_ms: null, tokens_in: 0, tokens_out: 0 },
      { event_type: "tool_error", model: null, input_data: null, output_data: null, tool_call: null, decision_trace: null, duration_ms: null, tokens_in: 0, tokens_out: 0 },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("2 LLM call(s), 1 tool call(s), 2 error(s)");
    expect(result).toContain("Total tokens: 700");
  });

  test("ongoing session shows ongoing duration", () => {
    const ongoingSession = { ...baseSession, ended_at: null };
    const result = generateExplanation(ongoingSession, []);
    expect(result).toContain("ongoing");
  });

  test("handles tool_call with missing tool_output and tool_input", () => {
    const events = [
      {
        event_type: "tool_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: JSON.stringify({ tool_name: "noop" }),
        decision_trace: null,
        duration_ms: null,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("noop");
    // The step should not have Input/Output/Duration lines (only the session header has Duration)
    const stepLines = result.split("\n").filter((l) => l.startsWith("- *"));
    expect(stepLines.every((l) => !l.includes("*Input:*"))).toBe(true);
    expect(stepLines.every((l) => !l.includes("*Output:*"))).toBe(true);
    // Duration should not appear in the step (only session-level "**Duration:**" is present)
    const toolStepIdx = result.indexOf("**noop**");
    const summaryIdx = result.indexOf("### Summary");
    const stepSection = result.slice(toolStepIdx, summaryIdx);
    expect(stepSection).not.toContain("*Duration:*");
  });

  test("handles tool_call with null tool_name", () => {
    const events = [
      {
        event_type: "tool_call",
        model: null,
        input_data: null,
        output_data: null,
        tool_call: JSON.stringify({}), // no tool_name
        decision_trace: null,
        duration_ms: 100,
        tokens_in: 0,
        tokens_out: 0,
      },
    ];

    const result = generateExplanation(baseSession, events);
    expect(result).toContain("unknown tool");
  });
});
