/**
 * Tests for GET /sessions/:id/events/search — event search & filter.
 *
 * Covers full-text search, event type filtering, model filtering,
 * token thresholds, duration filtering, boolean filters (errors,
 * tools, reasoning), time range, pagination, and summary stats.
 */

const Database = require("better-sqlite3");
const express = require("express");
const sessionsRouter = require("../routes/sessions");

// ── Test helpers ────────────────────────────────────────────────────

let db;
let app;

function makeApp() {
  app = express();
  app.use(express.json());
  app.use("/sessions", sessionsRouter);
  return app;
}

function seedTestData() {
  db = require("../db").getDb();

  // Insert a session
  db.prepare(
    `INSERT OR REPLACE INTO sessions (session_id, agent_name, started_at, ended_at, metadata, total_tokens_in, total_tokens_out, status)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(
    "search-test-session",
    "test-agent",
    "2024-06-15T10:00:00Z",
    "2024-06-15T10:30:00Z",
    "{}",
    5000,
    3000,
    "completed"
  );

  // Insert diverse events
  const insertEvent = db.prepare(
    `INSERT OR REPLACE INTO events (event_id, session_id, event_type, timestamp, input_data, output_data, model, tokens_in, tokens_out, tool_call, decision_trace, duration_ms)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  );

  // Event 1: LLM call with GPT-4
  insertEvent.run(
    "evt-001",
    "search-test-session",
    "llm_call",
    "2024-06-15T10:01:00Z",
    JSON.stringify({ prompt: "Analyze this customer data" }),
    JSON.stringify({ response: "The customer data shows positive trends" }),
    "gpt-4",
    500,
    200,
    null,
    JSON.stringify({ reasoning: "Need to analyze customer metrics first" }),
    150.5
  );

  // Event 2: Tool call
  insertEvent.run(
    "evt-002",
    "search-test-session",
    "tool_call",
    "2024-06-15T10:02:00Z",
    JSON.stringify({ query: "SELECT * FROM customers" }),
    JSON.stringify({ rows: 42 }),
    null,
    0,
    0,
    JSON.stringify({
      tool_name: "database_query",
      tool_input: { sql: "SELECT * FROM customers" },
      tool_output: { rows: 42 },
    }),
    null,
    50.0
  );

  // Event 3: LLM call with Claude
  insertEvent.run(
    "evt-003",
    "search-test-session",
    "llm_call",
    "2024-06-15T10:05:00Z",
    JSON.stringify({ prompt: "Summarize the findings" }),
    JSON.stringify({ response: "Revenue increased by 15% this quarter" }),
    "claude-3-sonnet",
    1200,
    800,
    null,
    JSON.stringify({ reasoning: "Summarizing all collected data points" }),
    320.0
  );

  // Event 4: Agent error
  insertEvent.run(
    "evt-004",
    "search-test-session",
    "agent_error",
    "2024-06-15T10:08:00Z",
    JSON.stringify({ task: "Generate chart" }),
    JSON.stringify({ error: "Chart library not available" }),
    "gpt-4",
    100,
    50,
    null,
    null,
    10.0
  );

  // Event 5: Tool error
  insertEvent.run(
    "evt-005",
    "search-test-session",
    "tool_error",
    "2024-06-15T10:09:00Z",
    null,
    JSON.stringify({ error: "Connection timeout" }),
    null,
    0,
    0,
    JSON.stringify({
      tool_name: "api_fetch",
      tool_input: { url: "https://api.example.com/data" },
      tool_output: null,
    }),
    null,
    5000.0
  );

  // Event 6: LLM call with GPT-4o, high tokens
  insertEvent.run(
    "evt-006",
    "search-test-session",
    "llm_call",
    "2024-06-15T10:12:00Z",
    JSON.stringify({ prompt: "Generate a detailed quarterly report" }),
    JSON.stringify({ response: "Q3 2024 Quarterly Report: Revenue..." }),
    "gpt-4o",
    2000,
    1500,
    null,
    JSON.stringify({ reasoning: "Creating comprehensive report from all data" }),
    800.0
  );

  // Event 7: Generic event, no model
  insertEvent.run(
    "evt-007",
    "search-test-session",
    "generic",
    "2024-06-15T10:15:00Z",
    JSON.stringify({ status: "Processing complete" }),
    null,
    null,
    0,
    0,
    null,
    null,
    5.0
  );

  // Event 8: LLM call with reasoning about "quarterly report"
  insertEvent.run(
    "evt-008",
    "search-test-session",
    "llm_call",
    "2024-06-15T10:20:00Z",
    JSON.stringify({ prompt: "Review quarterly report draft" }),
    JSON.stringify({ response: "The draft looks comprehensive" }),
    "gpt-4",
    800,
    400,
    null,
    JSON.stringify({ reasoning: "Checking quarterly report for completeness and accuracy" }),
    200.0
  );
}

// ── Supertest-like helper (no dependency needed) ────────────────────

const http = require("http");

function request(app) {
  const server = http.createServer(app);
  return {
    get(path) {
      return new Promise((resolve) => {
        server.listen(0, () => {
          const port = server.address().port;
          http.get(`http://localhost:${port}${path}`, (res) => {
            let body = "";
            res.on("data", (chunk) => (body += chunk));
            res.on("end", () => {
              server.close();
              resolve({
                status: res.statusCode,
                body: JSON.parse(body),
              });
            });
          });
        });
      });
    },
  };
}

// ── Setup / Teardown ────────────────────────────────────────────────

beforeAll(() => {
  // Use in-memory DB for tests
  process.env.DB_PATH = ":memory:";
  // Clear cached module so in-memory DB is used
  delete require.cache[require.resolve("../db")];
  delete require.cache[require.resolve("../routes/sessions")];
  // Re-require to pick up in-memory DB
  const freshSessionsRouter = require("../routes/sessions");
  app = express();
  app.use(express.json());
  app.use("/sessions", freshSessionsRouter);
  seedTestData();
});

// ── Tests ───────────────────────────────────────────────────────────

describe("GET /sessions/:id/events/search", () => {
  test("returns all events when no filters are applied", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search"
    );
    expect(status).toBe(200);
    expect(body.session_id).toBe("search-test-session");
    expect(body.total_events).toBe(8);
    expect(body.matched).toBe(8);
    expect(body.returned).toBe(8);
    expect(body.events).toHaveLength(8);
    expect(body.summary).toBeDefined();
    expect(body.summary.tokens_in).toBeGreaterThan(0);
    expect(body.summary.tokens_out).toBeGreaterThan(0);
  });

  test("returns 404 for non-existent session", async () => {
    const { status, body } = await request(app).get(
      "/sessions/nonexistent-session/events/search"
    );
    expect(status).toBe(404);
    expect(body.error).toContain("not found");
  });

  test("returns 400 for invalid session ID", async () => {
    const { status, body } = await request(app).get(
      "/sessions/invalid session!!/events/search"
    );
    expect(status).toBe(400);
    expect(body.error).toContain("Invalid");
  });

  // ── Type filter ─────────────────────────────────────────────────

  test("filters by single event type", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?type=llm_call"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(4);
    body.events.forEach((e) => expect(e.event_type).toBe("llm_call"));
  });

  test("filters by multiple event types (comma-separated)", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?type=agent_error,tool_error"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(2);
    body.events.forEach((e) =>
      expect(["agent_error", "tool_error"]).toContain(e.event_type)
    );
  });

  test("filters by tool_call type", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?type=tool_call"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(1);
    expect(body.events[0].event_id).toBe("evt-002");
  });

  // ── Model filter ────────────────────────────────────────────────

  test("filters by model name (exact)", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?model=gpt-4"
    );
    expect(status).toBe(200);
    // gpt-4 matches gpt-4 and gpt-4o (substring)
    expect(body.matched).toBe(4);
  });

  test("filters by model name (substring match)", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?model=claude"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(1);
    expect(body.events[0].model).toBe("claude-3-sonnet");
  });

  test("filters by multiple models", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?model=gpt-4o,claude"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(2);
  });

  // ── Text search ─────────────────────────────────────────────────

  test("searches across input data", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=customer"
    );
    expect(status).toBe(200);
    expect(body.matched).toBeGreaterThanOrEqual(2); // evt-001 and evt-002
  });

  test("searches across output data", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=revenue"
    );
    expect(status).toBe(200);
    expect(body.matched).toBeGreaterThanOrEqual(1);
  });

  test("searches across reasoning", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=summarizing"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(1);
    expect(body.events[0].event_id).toBe("evt-003");
  });

  test("searches across tool names", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=database_query"
    );
    expect(status).toBe(200);
    expect(body.matched).toBeGreaterThanOrEqual(1);
  });

  test("multi-term search uses AND logic", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=quarterly+report"
    );
    expect(status).toBe(200);
    // Should match events that contain both "quarterly" and "report"
    expect(body.matched).toBeGreaterThanOrEqual(1);
  });

  test("search is case-insensitive", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=CUSTOMER"
    );
    expect(status).toBe(200);
    expect(body.matched).toBeGreaterThanOrEqual(1);
  });

  test("returns zero for non-matching search", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=xyznonexistent"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(0);
    expect(body.events).toHaveLength(0);
  });

  // ── Token thresholds ────────────────────────────────────────────

  test("filters by min_tokens", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?min_tokens=1000"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect((e.tokens_in || 0) + (e.tokens_out || 0)).toBeGreaterThanOrEqual(1000)
    );
  });

  test("filters by max_tokens", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?max_tokens=200"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect((e.tokens_in || 0) + (e.tokens_out || 0)).toBeLessThanOrEqual(200)
    );
  });

  test("combines min and max token filters", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?min_tokens=100&max_tokens=800"
    );
    expect(status).toBe(200);
    body.events.forEach((e) => {
      const total = (e.tokens_in || 0) + (e.tokens_out || 0);
      expect(total).toBeGreaterThanOrEqual(100);
      expect(total).toBeLessThanOrEqual(800);
    });
  });

  // ── Duration filter ─────────────────────────────────────────────

  test("filters by min_duration_ms", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?min_duration_ms=500"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect(e.duration_ms).toBeGreaterThanOrEqual(500)
    );
  });

  // ── Boolean filters ─────────────────────────────────────────────

  test("filters for errors only", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?errors=true"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(2);
    body.events.forEach((e) =>
      expect(["error", "agent_error", "tool_error"]).toContain(e.event_type)
    );
  });

  test("filters for events with tool calls", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?has_tools=true"
    );
    expect(status).toBe(200);
    body.events.forEach((e) => expect(e.tool_call).not.toBeNull());
  });

  test("filters for events with reasoning", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?has_reasoning=true"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect(e.decision_trace?.reasoning).toBeTruthy()
    );
  });

  // ── Time range filters ──────────────────────────────────────────

  test("filters by after timestamp", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?after=2024-06-15T10:10:00Z"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect(new Date(e.timestamp).getTime()).toBeGreaterThanOrEqual(
        new Date("2024-06-15T10:10:00Z").getTime()
      )
    );
  });

  test("filters by before timestamp", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?before=2024-06-15T10:05:00Z"
    );
    expect(status).toBe(200);
    body.events.forEach((e) =>
      expect(new Date(e.timestamp).getTime()).toBeLessThanOrEqual(
        new Date("2024-06-15T10:05:00Z").getTime()
      )
    );
  });

  test("filters by time range (after + before)", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?after=2024-06-15T10:05:00Z&before=2024-06-15T10:10:00Z"
    );
    expect(status).toBe(200);
    body.events.forEach((e) => {
      const t = new Date(e.timestamp).getTime();
      expect(t).toBeGreaterThanOrEqual(new Date("2024-06-15T10:05:00Z").getTime());
      expect(t).toBeLessThanOrEqual(new Date("2024-06-15T10:10:00Z").getTime());
    });
  });

  // ── Combined filters ────────────────────────────────────────────

  test("combines type + model filters", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?type=llm_call&model=gpt-4o"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(1);
    expect(body.events[0].model).toBe("gpt-4o");
    expect(body.events[0].event_type).toBe("llm_call");
  });

  test("combines search + type + min_tokens", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q=report&type=llm_call&min_tokens=1000"
    );
    expect(status).toBe(200);
    body.events.forEach((e) => {
      expect(e.event_type).toBe("llm_call");
      expect((e.tokens_in || 0) + (e.tokens_out || 0)).toBeGreaterThanOrEqual(1000);
    });
  });

  // ── Pagination ──────────────────────────────────────────────────

  test("paginates with limit", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?limit=3"
    );
    expect(status).toBe(200);
    expect(body.returned).toBe(3);
    expect(body.matched).toBe(8);
    expect(body.limit).toBe(3);
  });

  test("paginates with offset", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?limit=3&offset=3"
    );
    expect(status).toBe(200);
    expect(body.returned).toBe(3);
    expect(body.offset).toBe(3);
  });

  test("offset past end returns empty", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?offset=100"
    );
    expect(status).toBe(200);
    expect(body.returned).toBe(0);
    expect(body.events).toHaveLength(0);
  });

  // ── Summary stats ───────────────────────────────────────────────

  test("summary includes token totals for matched events", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?type=llm_call"
    );
    expect(status).toBe(200);
    expect(body.summary.tokens_in).toBeGreaterThan(0);
    expect(body.summary.tokens_out).toBeGreaterThan(0);
    expect(body.summary.total_tokens).toBe(
      body.summary.tokens_in + body.summary.tokens_out
    );
  });

  test("summary includes event type breakdown", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search"
    );
    expect(status).toBe(200);
    expect(body.summary.event_types).toBeDefined();
    expect(body.summary.event_types.llm_call).toBe(4);
    expect(body.summary.event_types.tool_call).toBe(1);
  });

  test("summary includes model breakdown", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search"
    );
    expect(status).toBe(200);
    expect(body.summary.models).toBeDefined();
    expect(body.summary.models["gpt-4"]).toBeGreaterThanOrEqual(1);
    expect(body.summary.models["claude-3-sonnet"]).toBe(1);
  });

  test("summary includes total_duration_ms", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search"
    );
    expect(status).toBe(200);
    expect(body.summary.total_duration_ms).toBeGreaterThan(0);
  });

  // ── Edge cases ──────────────────────────────────────────────────

  test("ignores invalid min_tokens (non-numeric)", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?min_tokens=abc"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(8); // no filter applied
  });

  test("ignores negative min_tokens", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?min_tokens=-10"
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(8);
  });

  test("empty search query returns all", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?q="
    );
    expect(status).toBe(200);
    expect(body.matched).toBe(8);
  });

  test("limit is clamped to max 500", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?limit=9999"
    );
    expect(status).toBe(200);
    expect(body.limit).toBe(500);
  });

  test("limit of 0 uses default", async () => {
    const { status, body } = await request(app).get(
      "/sessions/search-test-session/events/search?limit=0"
    );
    expect(status).toBe(200);
    // 0 is falsy, so falls through to default (100)
    expect(body.limit).toBe(100);
  });
});
