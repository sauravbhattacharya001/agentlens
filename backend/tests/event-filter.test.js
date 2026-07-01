/**
 * Tests for lib/event-filter.js - the pure query-building and result
 * aggregation logic extracted from GET /sessions/:id/events/search.
 *
 * These exercise the WHERE-clause builder and summary reducer directly, so
 * the SQL-injection-safety caps, LIKE escaping, and clause structure are
 * asserted as pure units rather than only through the end-to-end route tests
 * (tests/event-search.test.js), which continue to pin overall behaviour.
 */

const {
  buildEventSearchFilter,
  summarizeEvents,
  MAX_FILTER_VALUES,
  MAX_SEARCH_TERMS,
} = require("../lib/event-filter");

// --- buildEventSearchFilter: base case ---------------------------------

describe("buildEventSearchFilter - base", () => {
  test("no filters yields only the session_id condition", () => {
    const { conditions, params } = buildEventSearchFilter("sess-1", {});
    expect(conditions).toEqual(["session_id = ?"]);
    expect(params).toEqual(["sess-1"]);
  });

  test("missing query object defaults to empty (no crash)", () => {
    const { conditions, params } = buildEventSearchFilter("sess-1");
    expect(conditions).toEqual(["session_id = ?"]);
    expect(params).toEqual(["sess-1"]);
  });

  test("session_id is always the first bound parameter", () => {
    const { params } = buildEventSearchFilter("abc", { type: "llm_call", q: "hi" });
    expect(params[0]).toBe("abc");
  });
});

// --- type filter -------------------------------------------------------

describe("buildEventSearchFilter - type", () => {
  test("single type adds a LOWER(...) IN clause with one placeholder", () => {
    const { conditions, params } = buildEventSearchFilter("s", { type: "llm_call" });
    expect(conditions).toContain("LOWER(event_type) IN (LOWER(?))");
    expect(params).toEqual(["s", "llm_call"]);
  });

  test("comma-separated types expand to one placeholder each, trimmed", () => {
    const { conditions, params } = buildEventSearchFilter("s", { type: " llm_call , tool_call ,tool_result" });
    expect(conditions).toContain("LOWER(event_type) IN (LOWER(?),LOWER(?),LOWER(?))");
    expect(params).toEqual(["s", "llm_call", "tool_call", "tool_result"]);
  });

  test("empty/whitespace-only type contributes no clause", () => {
    const { conditions, params } = buildEventSearchFilter("s", { type: " , , " });
    expect(conditions).toEqual(["session_id = ?"]);
    expect(params).toEqual(["s"]);
  });

  test("type values are capped at MAX_FILTER_VALUES", () => {
    const many = Array.from({ length: MAX_FILTER_VALUES + 15 }, (_, i) => `t${i}`).join(",");
    const { conditions, params } = buildEventSearchFilter("s", { type: many });
    // session_id param + exactly MAX_FILTER_VALUES type params
    expect(params.length).toBe(1 + MAX_FILTER_VALUES);
    const placeholders = (conditions[1].match(/LOWER\(\?\)/g) || []).length;
    expect(placeholders).toBe(MAX_FILTER_VALUES);
  });
});

// --- model filter ------------------------------------------------------

describe("buildEventSearchFilter - model", () => {
  test("single model becomes a lowercased LIKE substring with ESCAPE", () => {
    const { conditions, params } = buildEventSearchFilter("s", { model: "GPT-4o" });
    expect(conditions.some((c) => c.includes("LOWER(model) LIKE ? ESCAPE '\\'"))).toBe(true);
    expect(params).toEqual(["s", "%gpt-4o%"]);
  });

  test("multiple models are OR'd together", () => {
    const { conditions, params } = buildEventSearchFilter("s", { model: "gpt,claude" });
    const clause = conditions.find((c) => c.startsWith("model IS NOT NULL"));
    expect(clause).toBe("model IS NOT NULL AND (LOWER(model) LIKE ? ESCAPE '\\' OR LOWER(model) LIKE ? ESCAPE '\\')");
    expect(params).toEqual(["s", "%gpt%", "%claude%"]);
  });

  test("model wildcards are escaped so they match literally", () => {
    const { params } = buildEventSearchFilter("s", { model: "gp%_t" });
    expect(params).toEqual(["s", "%gp\\%\\_t%"]);
  });

  test("model values are capped at MAX_FILTER_VALUES", () => {
    const many = Array.from({ length: MAX_FILTER_VALUES + 5 }, (_, i) => `m${i}`).join(",");
    const { params } = buildEventSearchFilter("s", { model: many });
    expect(params.length).toBe(1 + MAX_FILTER_VALUES);
  });
});

// --- numeric threshold filters -----------------------------------------

describe("buildEventSearchFilter - numeric thresholds", () => {
  test("min_tokens > 0 adds a COALESCE sum >= clause", () => {
    const { conditions, params } = buildEventSearchFilter("s", { min_tokens: "100" });
    expect(conditions).toContain("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) >= ?");
    expect(params).toEqual(["s", 100]);
  });

  test("max_tokens > 0 adds a COALESCE sum <= clause", () => {
    const { conditions, params } = buildEventSearchFilter("s", { max_tokens: "500" });
    expect(conditions).toContain("(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) <= ?");
    expect(params).toEqual(["s", 500]);
  });

  test("min and max tokens combine", () => {
    const { params } = buildEventSearchFilter("s", { min_tokens: "10", max_tokens: "20" });
    expect(params).toEqual(["s", 10, 20]);
  });

  test("non-numeric min_tokens is ignored", () => {
    const { conditions } = buildEventSearchFilter("s", { min_tokens: "abc" });
    expect(conditions).toEqual(["session_id = ?"]);
  });

  test("zero and negative token thresholds are ignored", () => {
    expect(buildEventSearchFilter("s", { min_tokens: "0" }).conditions).toEqual(["session_id = ?"]);
    expect(buildEventSearchFilter("s", { max_tokens: "-5" }).conditions).toEqual(["session_id = ?"]);
  });

  test("min_duration_ms > 0 adds a COALESCE duration clause (float ok)", () => {
    const { conditions, params } = buildEventSearchFilter("s", { min_duration_ms: "12.5" });
    expect(conditions).toContain("COALESCE(duration_ms, 0) >= ?");
    expect(params).toEqual(["s", 12.5]);
  });

  test("non-positive / non-numeric duration is ignored", () => {
    expect(buildEventSearchFilter("s", { min_duration_ms: "0" }).conditions).toEqual(["session_id = ?"]);
    expect(buildEventSearchFilter("s", { min_duration_ms: "nope" }).conditions).toEqual(["session_id = ?"]);
  });
});

// --- time range --------------------------------------------------------

describe("buildEventSearchFilter - time range", () => {
  test("valid after/before add timestamp comparisons with the raw ISO value", () => {
    const { conditions, params } = buildEventSearchFilter("s", {
      after: "2026-01-01T00:00:00Z",
      before: "2026-02-01T00:00:00Z",
    });
    expect(conditions).toContain("timestamp >= ?");
    expect(conditions).toContain("timestamp <= ?");
    expect(params).toEqual(["s", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"]);
  });

  test("invalid dates are ignored", () => {
    const { conditions } = buildEventSearchFilter("s", { after: "not-a-date", before: "also-bad" });
    expect(conditions).toEqual(["session_id = ?"]);
  });
});

// --- boolean flag filters ----------------------------------------------

describe("buildEventSearchFilter - flags", () => {
  test("errors=true restricts to the three error event types", () => {
    const { conditions } = buildEventSearchFilter("s", { errors: "true" });
    expect(conditions).toContain("event_type IN ('error', 'agent_error', 'tool_error')");
  });

  test("has_tools=true requires a non-null tool_call", () => {
    const { conditions } = buildEventSearchFilter("s", { has_tools: "true" });
    expect(conditions).toContain("tool_call IS NOT NULL AND tool_call != 'null'");
  });

  test("has_reasoning=true requires a decision_trace with a reasoning key", () => {
    const { conditions } = buildEventSearchFilter("s", { has_reasoning: "true" });
    expect(conditions).toContain(
      "decision_trace IS NOT NULL AND decision_trace != 'null' AND decision_trace LIKE '%\"reasoning\"%'"
    );
  });

  test("flags only trigger on the exact string 'true'", () => {
    const { conditions } = buildEventSearchFilter("s", { errors: "1", has_tools: "yes", has_reasoning: "TRUE" });
    expect(conditions).toEqual(["session_id = ?"]);
  });
});

// --- full-text q search ------------------------------------------------

describe("buildEventSearchFilter - full-text q", () => {
  test("a single term adds one 6-column OR clause and 6 bound params", () => {
    const { conditions, params } = buildEventSearchFilter("s", { q: "hello" });
    const clause = conditions.find((c) => c.includes("input_data"));
    expect(clause).toBeDefined();
    expect((clause.match(/LIKE LOWER\(\?\) ESCAPE/g) || []).length).toBe(6);
    // session_id + 6 like binds, all the same escaped term
    expect(params).toEqual(["s", "%hello%", "%hello%", "%hello%", "%hello%", "%hello%", "%hello%"]);
  });

  test("multiple terms are AND'd (one clause each) - 6 binds per term", () => {
    const { conditions, params } = buildEventSearchFilter("s", { q: "foo bar" });
    const searchClauses = conditions.filter((c) => c.includes("input_data"));
    expect(searchClauses.length).toBe(2);
    expect(params.length).toBe(1 + 2 * 6);
  });

  test("q wildcards are escaped in every bound parameter", () => {
    const { params } = buildEventSearchFilter("s", { q: "50%_off" });
    for (const p of params.slice(1)) {
      expect(p).toBe("%50\\%\\_off%");
    }
  });

  test("search terms are capped at MAX_SEARCH_TERMS", () => {
    const many = Array.from({ length: MAX_SEARCH_TERMS + 8 }, (_, i) => `w${i}`).join(" ");
    const { conditions, params } = buildEventSearchFilter("s", { q: many });
    const searchClauses = conditions.filter((c) => c.includes("input_data"));
    expect(searchClauses.length).toBe(MAX_SEARCH_TERMS);
    expect(params.length).toBe(1 + MAX_SEARCH_TERMS * 6);
  });

  test("whitespace-only q contributes no clause", () => {
    const { conditions } = buildEventSearchFilter("s", { q: "   " });
    expect(conditions).toEqual(["session_id = ?"]);
  });
});

// --- combinations & ordering -------------------------------------------

describe("buildEventSearchFilter - combinations", () => {
  test("conditions and params stay positionally aligned across mixed filters", () => {
    const { conditions, params } = buildEventSearchFilter("s", {
      type: "llm_call",
      model: "gpt",
      min_tokens: "10",
      q: "term",
    });
    // 1 base + type + model + min_tokens + q(1 term) = 5 conditions
    expect(conditions.length).toBe(5);
    // params: s, llm_call, %gpt%, 10, then 6 q binds = 10
    expect(params).toEqual(["s", "llm_call", "%gpt%", 10, "%term%", "%term%", "%term%", "%term%", "%term%", "%term%"]);
  });

  test("conditions.length > 1 signals active filters (route uses this)", () => {
    expect(buildEventSearchFilter("s", {}).conditions.length).toBe(1);
    expect(buildEventSearchFilter("s", { errors: "true" }).conditions.length).toBe(2);
  });
});

// --- summarizeEvents ---------------------------------------------------

describe("summarizeEvents", () => {
  test("empty list yields zeroed totals and empty histograms", () => {
    expect(summarizeEvents([])).toEqual({
      tokens_in: 0,
      tokens_out: 0,
      total_tokens: 0,
      total_duration_ms: 0,
      event_types: {},
      models: {},
    });
  });

  test("sums tokens and durations and builds histograms", () => {
    const events = [
      { event_type: "llm_call", model: "gpt-4o", tokens_in: 10, tokens_out: 5, duration_ms: 100 },
      { event_type: "llm_call", model: "gpt-4o", tokens_in: 20, tokens_out: 7, duration_ms: 50.25 },
      { event_type: "tool_call", model: "claude", tokens_in: 0, tokens_out: 0, duration_ms: 0 },
    ];
    const s = summarizeEvents(events);
    expect(s.tokens_in).toBe(30);
    expect(s.tokens_out).toBe(12);
    expect(s.total_tokens).toBe(42);
    expect(s.total_duration_ms).toBe(150.25);
    expect(s.event_types).toEqual({ llm_call: 2, tool_call: 1 });
    expect(s.models).toEqual({ "gpt-4o": 2, claude: 1 });
  });

  test("missing token/duration fields count as 0", () => {
    const s = summarizeEvents([{ event_type: "note" }, { event_type: "note", tokens_in: 3 }]);
    expect(s.tokens_in).toBe(3);
    expect(s.tokens_out).toBe(0);
    expect(s.total_duration_ms).toBe(0);
    expect(s.event_types).toEqual({ note: 2 });
  });

  test("events without a model are excluded from the model histogram", () => {
    const s = summarizeEvents([
      { event_type: "x", model: "" },
      { event_type: "x", model: null },
      { event_type: "x" },
      { event_type: "x", model: "m" },
    ]);
    expect(s.models).toEqual({ m: 1 });
    expect(s.event_types).toEqual({ x: 4 });
  });

  test("total_duration_ms is rounded to two decimal places", () => {
    const s = summarizeEvents([{ event_type: "x", duration_ms: 1.005 }, { event_type: "x", duration_ms: 2.006 }]);
    // 3.011 -> rounded to 3.01
    expect(s.total_duration_ms).toBe(3.01);
  });
});
