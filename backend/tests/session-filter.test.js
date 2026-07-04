/**
 * Tests for lib/session-filter.js - the pure query-building logic extracted
 * from GET /sessions/search (the sibling of lib/event-filter).
 *
 * These exercise the sort whitelist, WHERE-clause builder, tag INNER-JOIN
 * sub-query, and applied-filter summary directly, so the SQL-injection-safety
 * caps, LIKE escaping, and clause/param ordering are asserted as pure units
 * rather than only through the end-to-end route tests (tests/sessions.test.js,
 * "GET /sessions/search"), which continue to pin overall behaviour.
 */

const {
  resolveSessionSort,
  buildSessionSearchFilter,
  buildSessionFilterSummary,
  MAX_SEARCH_TERMS,
  MAX_TAG_VALUES,
  SORT_FIELDS,
} = require("../lib/session-filter");

// --- resolveSessionSort ------------------------------------------------

describe("resolveSessionSort", () => {
  test("defaults to started_at DESC when unspecified", () => {
    expect(resolveSessionSort({})).toEqual({
      sortBy: "started_at",
      sortOrder: "DESC",
      sortColumn: "s.started_at",
    });
  });

  test("missing query object does not crash", () => {
    expect(resolveSessionSort().sortBy).toBe("started_at");
  });

  test("each whitelisted field is accepted and prefixed with s.", () => {
    for (const field of ["started_at", "agent_name", "status"]) {
      const r = resolveSessionSort({ sort: field });
      expect(r.sortBy).toBe(field);
      expect(r.sortColumn).toBe(`s.${field}`);
    }
  });

  test("total_tokens maps to the summed-token SQL expression", () => {
    const r = resolveSessionSort({ sort: "total_tokens" });
    expect(r.sortBy).toBe("total_tokens");
    expect(r.sortColumn).toBe("(s.total_tokens_in + s.total_tokens_out)");
  });

  test("an un-whitelisted sort field falls back to started_at (no injection)", () => {
    const r = resolveSessionSort({ sort: "started_at; DROP TABLE sessions" });
    expect(r.sortBy).toBe("started_at");
    expect(r.sortColumn).toBe("s.started_at");
  });

  test("order=asc yields ASC; anything else yields DESC", () => {
    expect(resolveSessionSort({ order: "asc" }).sortOrder).toBe("ASC");
    expect(resolveSessionSort({ order: "ASC" }).sortOrder).toBe("DESC"); // case-sensitive
    expect(resolveSessionSort({ order: "descending" }).sortOrder).toBe("DESC");
    expect(resolveSessionSort({}).sortOrder).toBe("DESC");
  });

  test("SORT_FIELDS is the exact whitelist", () => {
    expect(SORT_FIELDS).toEqual(["started_at", "total_tokens", "agent_name", "status"]);
  });
});

// --- buildSessionSearchFilter: base case -------------------------------

describe("buildSessionSearchFilter - base", () => {
  test("no filters yields empty conditions, params, tagJoin, whereClause", () => {
    const r = buildSessionSearchFilter({});
    expect(r.conditions).toEqual([]);
    expect(r.params).toEqual([]);
    expect(r.tagJoin).toBe("");
    expect(r.whereClause).toBe("");
  });

  test("missing query object defaults to empty (no crash)", () => {
    const r = buildSessionSearchFilter();
    expect(r.conditions).toEqual([]);
    expect(r.params).toEqual([]);
  });

  test("carries the resolved sort fields through", () => {
    const r = buildSessionSearchFilter({ sort: "total_tokens", order: "asc" });
    expect(r.sortBy).toBe("total_tokens");
    expect(r.sortOrder).toBe("ASC");
    expect(r.sortColumn).toBe("(s.total_tokens_in + s.total_tokens_out)");
  });

  test("whereClause is prefixed with WHERE and ANDs conditions", () => {
    const r = buildSessionSearchFilter({ agent: "codex", status: "completed" });
    expect(r.whereClause).toBe(
      "WHERE s.agent_name LIKE ? ESCAPE '\\' AND s.status = ?"
    );
  });
});

// --- full-text q -------------------------------------------------------

describe("buildSessionSearchFilter - q", () => {
  test("single term adds one OR clause with two LIKE params", () => {
    const { conditions, params } = buildSessionSearchFilter({ q: "climate" });
    expect(conditions).toEqual([
      "(s.agent_name LIKE ? ESCAPE '\\' OR s.metadata LIKE ? ESCAPE '\\')",
    ]);
    expect(params).toEqual(["%climate%", "%climate%"]);
  });

  test("multiple terms each add a clause (ANDed), two params each", () => {
    const { conditions, params } = buildSessionSearchFilter({ q: "  foo   bar " });
    expect(conditions).toHaveLength(2);
    expect(params).toEqual(["%foo%", "%foo%", "%bar%", "%bar%"]);
  });

  test("LIKE wildcards in the term are escaped", () => {
    const { params } = buildSessionSearchFilter({ q: "50%_off" });
    expect(params).toEqual(["%50\\%\\_off%", "%50\\%\\_off%"]);
  });

  test("terms are capped at MAX_SEARCH_TERMS", () => {
    const many = Array.from({ length: MAX_SEARCH_TERMS + 5 }, (_, i) => `t${i}`).join(" ");
    const { conditions, params } = buildSessionSearchFilter({ q: many });
    expect(conditions).toHaveLength(MAX_SEARCH_TERMS);
    expect(params).toHaveLength(MAX_SEARCH_TERMS * 2);
  });

  test("whitespace-only q contributes no clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ q: "   " });
    expect(conditions).toEqual([]);
    expect(params).toEqual([]);
  });
});

// --- agent / status ----------------------------------------------------

describe("buildSessionSearchFilter - agent & status", () => {
  test("agent adds a substring LIKE, escaped", () => {
    const { conditions, params } = buildSessionSearchFilter({ agent: "gpt%4" });
    expect(conditions).toEqual(["s.agent_name LIKE ? ESCAPE '\\'"]);
    expect(params).toEqual(["%gpt\\%4%"]);
  });

  test("valid status adds an equality clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ status: "completed" });
    expect(conditions).toEqual(["s.status = ?"]);
    expect(params).toEqual(["completed"]);
  });

  test("invalid status is ignored (no clause)", () => {
    const { conditions, params } = buildSessionSearchFilter({ status: "not-a-status" });
    expect(conditions).toEqual([]);
    expect(params).toEqual([]);
  });
});

// --- date range --------------------------------------------------------

describe("buildSessionSearchFilter - date range", () => {
  test("after adds a >= clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ after: "2026-01-15T00:00:00Z" });
    expect(conditions).toEqual(["s.started_at >= ?"]);
    expect(params).toEqual(["2026-01-15T00:00:00Z"]);
  });

  test("before adds a <= clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ before: "2026-02-01T00:00:00Z" });
    expect(conditions).toEqual(["s.started_at <= ?"]);
    expect(params).toEqual(["2026-02-01T00:00:00Z"]);
  });

  test("after + before combine in order", () => {
    const { conditions, params } = buildSessionSearchFilter({
      after: "2026-01-01",
      before: "2026-02-01",
    });
    expect(conditions).toEqual(["s.started_at >= ?", "s.started_at <= ?"]);
    expect(params).toEqual(["2026-01-01", "2026-02-01"]);
  });
});

// --- token thresholds --------------------------------------------------

describe("buildSessionSearchFilter - token thresholds", () => {
  test("min_tokens > 0 adds a summed-token >= clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ min_tokens: "100" });
    expect(conditions).toEqual(["(s.total_tokens_in + s.total_tokens_out) >= ?"]);
    expect(params).toEqual([100]);
  });

  test("max_tokens > 0 adds a summed-token <= clause", () => {
    const { conditions, params } = buildSessionSearchFilter({ max_tokens: "5000" });
    expect(conditions).toEqual(["(s.total_tokens_in + s.total_tokens_out) <= ?"]);
    expect(params).toEqual([5000]);
  });

  test("zero and negative thresholds are ignored", () => {
    expect(buildSessionSearchFilter({ min_tokens: "0" }).conditions).toEqual([]);
    expect(buildSessionSearchFilter({ max_tokens: "-1" }).conditions).toEqual([]);
  });

  test("non-numeric thresholds are ignored", () => {
    expect(buildSessionSearchFilter({ min_tokens: "abc" }).conditions).toEqual([]);
  });
});

// --- tag INNER JOIN ----------------------------------------------------

describe("buildSessionSearchFilter - tags", () => {
  test("tags build an ALL-tags INNER JOIN and unshift their params (+count)", () => {
    const r = buildSessionSearchFilter({ tags: "prod,v2" });
    expect(r.tagJoin).toContain("INNER JOIN");
    expect(r.tagJoin).toContain("WHERE tag IN (?,?)");
    expect(r.tagJoin).toContain("HAVING COUNT(DISTINCT tag) = ?");
    // params: the two tags, then the count, all at the front
    expect(r.params).toEqual(["prod", "v2", 2]);
  });

  test("tag params precede the WHERE-clause params (join is textually first)", () => {
    const r = buildSessionSearchFilter({ tags: "prod", status: "completed" });
    // tag values + count come first, then the status equality param
    expect(r.params).toEqual(["prod", 1, "completed"]);
  });

  test("tags are trimmed and blanks dropped", () => {
    const r = buildSessionSearchFilter({ tags: " a , , b " });
    expect(r.params).toEqual(["a", "b", 2]);
  });

  test("tags are capped at MAX_TAG_VALUES", () => {
    const many = Array.from({ length: MAX_TAG_VALUES + 5 }, (_, i) => `t${i}`).join(",");
    const r = buildSessionSearchFilter({ tags: many });
    // MAX_TAG_VALUES tag params + 1 count param
    expect(r.params).toHaveLength(MAX_TAG_VALUES + 1);
    expect(r.params[MAX_TAG_VALUES]).toBe(MAX_TAG_VALUES);
  });

  test("empty/whitespace-only tags contribute no join", () => {
    const r = buildSessionSearchFilter({ tags: " , , " });
    expect(r.tagJoin).toBe("");
    expect(r.params).toEqual([]);
  });
});

// --- combined ordering -------------------------------------------------

describe("buildSessionSearchFilter - combined", () => {
  test("q + agent + status + range + tokens order is stable and aligned", () => {
    const r = buildSessionSearchFilter({
      q: "hi",
      agent: "codex",
      status: "completed",
      after: "2026-01-01",
      before: "2026-02-01",
      min_tokens: "10",
      max_tokens: "99",
    });
    expect(r.conditions).toEqual([
      "(s.agent_name LIKE ? ESCAPE '\\' OR s.metadata LIKE ? ESCAPE '\\')",
      "s.agent_name LIKE ? ESCAPE '\\'",
      "s.status = ?",
      "s.started_at >= ?",
      "s.started_at <= ?",
      "(s.total_tokens_in + s.total_tokens_out) >= ?",
      "(s.total_tokens_in + s.total_tokens_out) <= ?",
    ]);
    expect(r.params).toEqual([
      "%hi%", "%hi%", "%codex%", "completed", "2026-01-01", "2026-02-01", 10, 99,
    ]);
  });
});

// --- buildSessionFilterSummary -----------------------------------------

describe("buildSessionFilterSummary", () => {
  test("empty query yields an all-null summary", () => {
    expect(buildSessionFilterSummary({})).toEqual({
      q: null,
      agent: null,
      status: null,
      after: null,
      before: null,
      min_tokens: null,
      max_tokens: null,
      tags: null,
    });
  });

  test("missing query object does not crash", () => {
    expect(buildSessionFilterSummary().q).toBeNull();
  });

  test("echoes provided string filters verbatim", () => {
    const s = buildSessionFilterSummary({
      q: "test",
      agent: "x",
      status: "active",
      after: "2026-01-01",
      before: "2026-02-01",
    });
    expect(s.q).toBe("test");
    expect(s.agent).toBe("x");
    expect(s.status).toBe("active");
    expect(s.after).toBe("2026-01-01");
    expect(s.before).toBe("2026-02-01");
  });

  test("numeric token filters are parsed; invalid become null", () => {
    expect(buildSessionFilterSummary({ min_tokens: "100", max_tokens: "5000" })).toMatchObject({
      min_tokens: 100,
      max_tokens: 5000,
    });
    expect(buildSessionFilterSummary({ min_tokens: "abc" }).min_tokens).toBeNull();
  });

  test("tags are split, trimmed, and blanks dropped into an array", () => {
    expect(buildSessionFilterSummary({ tags: " prod , v2 , " }).tags).toEqual(["prod", "v2"]);
  });

  test("absent tags yield null (not an empty array)", () => {
    expect(buildSessionFilterSummary({}).tags).toBeNull();
  });
});
