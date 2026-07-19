/* ── Analytics Performance — percentile latencies, throughput, efficiency ── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.pragma("journal_mode = WAL");
      mockDb.pragma("foreign_keys = ON");
      mockDb.exec(`
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          agent_name TEXT NOT NULL DEFAULT 'default-agent',
          started_at TEXT NOT NULL,
          ended_at TEXT,
          metadata TEXT DEFAULT '{}',
          total_tokens_in INTEGER DEFAULT 0,
          total_tokens_out INTEGER DEFAULT 0,
          status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          event_type TEXT NOT NULL DEFAULT 'generic',
          timestamp TEXT NOT NULL,
          input_data TEXT,
          output_data TEXT,
          model TEXT,
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          tool_call TEXT,
          decision_trace TEXT,
          duration_ms REAL,
          FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const analyticsRouter = require("../routes/analytics");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/analytics", analyticsRouter);
  return app;
}

beforeAll(() => {
  require("../db").getDb();
});

beforeEach(() => {
  if (mockDb) {
    mockDb.exec("DELETE FROM events");
    mockDb.exec("DELETE FROM sessions");
  }
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

function insertSession(id, agent = "test-agent") {
  mockDb.prepare(
    `INSERT INTO sessions (session_id, agent_name, started_at, status, total_tokens_in, total_tokens_out)
     VALUES (?, ?, datetime('now'), 'completed', 100, 50)`
  ).run(id, agent);
}

function insertEvent(id, sessionId, opts = {}) {
  const {
    model = "gpt-4o",
    duration = 100,
    tokensIn = 50,
    tokensOut = 25,
    eventType = "llm_call",
  } = opts;
  mockDb.prepare(
    `INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms)
     VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)`
  ).run(id, sessionId, eventType, model, tokensIn, tokensOut, duration);
}

describe("GET /analytics/performance", () => {
  test("returns empty result when no events exist", async () => {
    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);
    expect(res.body.sample_size).toBe(0);
    expect(res.body.latency).toBeNull();
  });

  test("computes percentiles correctly", async () => {
    insertSession("s1");
    // Insert 10 events with varying durations
    for (let i = 1; i <= 10; i++) {
      insertEvent(`e${i}`, "s1", { duration: i * 100 });
    }

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.sample_size).toBe(10);
    expect(res.body.latency.p50).toBeGreaterThan(0);
    expect(res.body.latency.p95).toBeGreaterThan(res.body.latency.p50);
    expect(res.body.latency.p99).toBeGreaterThanOrEqual(res.body.latency.p95);
    expect(res.body.latency.min).toBe(100);
    expect(res.body.latency.max).toBe(1000);
  });

  test("breaks down by model", async () => {
    insertSession("s1");
    insertEvent("e1", "s1", { model: "gpt-4o", duration: 200 });
    insertEvent("e2", "s1", { model: "claude-3-sonnet", duration: 300 });
    insertEvent("e3", "s1", { model: "gpt-4o", duration: 400 });

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.by_model["gpt-4o"]).toBeDefined();
    expect(res.body.by_model["gpt-4o"].count).toBe(2);
    expect(res.body.by_model["claude-3-sonnet"]).toBeDefined();
    expect(res.body.by_model["claude-3-sonnet"].count).toBe(1);
  });

  test("filters by agent name", async () => {
    insertSession("s1", "agent-a");
    insertSession("s2", "agent-b");
    insertEvent("e1", "s1", { duration: 100 });
    insertEvent("e2", "s2", { duration: 200 });

    const app = createApp();
    const res = await request(app)
      .get("/analytics/performance?agent=agent-a")
      .expect(200);

    expect(res.body.sample_size).toBe(1);
    expect(res.body.filters.agent).toBe("agent-a");
  });

  test("filters by model", async () => {
    insertSession("s1");
    insertEvent("e1", "s1", { model: "gpt-4o", duration: 100 });
    insertEvent("e2", "s1", { model: "claude-3-sonnet", duration: 200 });

    const app = createApp();
    const res = await request(app)
      .get("/analytics/performance?model=gpt-4o")
      .expect(200);

    expect(res.body.sample_size).toBe(1);
    expect(res.body.filters.model).toBe("gpt-4o");
  });

  test("filters by agent AND model together (combined 'both' variant)", async () => {
    // Exercises the agent+model prepared-statement variant
    // (WHERE ... AND s.agent_name = ? AND e.model = ?), which is a
    // distinct SQL path from the agent-only and model-only variants.
    insertSession("s1", "agent-a");
    insertSession("s2", "agent-b");
    // agent-a on both models; only the (agent-a, gpt-4o) row must match.
    insertEvent("e1", "s1", { model: "gpt-4o", duration: 100 });
    insertEvent("e2", "s1", { model: "claude-3-sonnet", duration: 150 });
    // agent-b on gpt-4o must be excluded by the agent filter.
    insertEvent("e3", "s2", { model: "gpt-4o", duration: 200 });

    const app = createApp();
    const res = await request(app)
      .get("/analytics/performance?agent=agent-a&model=gpt-4o")
      .expect(200);

    expect(res.body.sample_size).toBe(1);
    expect(res.body.filters).toEqual({ agent: "agent-a", model: "gpt-4o" });
    expect(Object.keys(res.body.by_model)).toEqual(["gpt-4o"]);
    expect(res.body.by_model["gpt-4o"].count).toBe(1);
  });

  test("includes throughput and efficiency metrics", async () => {
    insertSession("s1");
    insertEvent("e1", "s1", { duration: 500, tokensIn: 100, tokensOut: 50 });
    insertEvent("e2", "s1", { duration: 300, tokensIn: 80, tokensOut: 40 });

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.throughput).toBeDefined();
    expect(res.body.throughput.total_tokens).toBe(270);
    expect(res.body.throughput.tokens_per_second).toBeGreaterThan(0);

    expect(res.body.efficiency).toBeDefined();
    expect(res.body.efficiency.avg_tokens_per_event).toBe(135);
    expect(res.body.efficiency.output_input_ratio).toBeGreaterThan(0);
  });

  test("breaks down by event type", async () => {
    insertSession("s1");
    insertEvent("e1", "s1", { eventType: "llm_call", duration: 100 });
    insertEvent("e2", "s1", { eventType: "tool_call", duration: 200 });

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.by_event_type["llm_call"]).toBeDefined();
    expect(res.body.by_event_type["tool_call"]).toBeDefined();
  });

  // ── Zero-token fallback branches ──────────────────────────
  // Events are kept only when duration_ms > 0 (SQL filter), so the
  // total_dur === 0 arm is unreachable; but events can legitimately
  // carry no tokens. When total_tok_in / total tokens are zero, the
  // efficiency ratios must take their `: 0` fallback rather than
  // dividing by zero. These cover the previously branch-uncovered
  // output_input_ratio and avg_duration_per_token_ms fallbacks.
  test("falls back to 0 for token ratios when all token counts are zero", async () => {
    insertSession("s1");
    // duration > 0 (so rows survive the filter) but zero tokens →
    // g.total_tok_in === 0 and totalTokens === 0.
    insertEvent("e1", "s1", { model: "gpt-4o", duration: 100, tokensIn: 0, tokensOut: 0 });
    insertEvent("e2", "s1", { model: "gpt-4o", duration: 200, tokensIn: 0, tokensOut: 0 });

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.sample_size).toBe(2);
    expect(res.body.throughput.total_tokens).toBe(0);
    // g.total_dur > 0 so tokens_per_second is the computed arm (0/dur = 0)
    expect(res.body.throughput.tokens_per_second).toBe(0);
    // efficiency fallbacks: total_tok_in === 0 and totalTokens === 0
    expect(res.body.efficiency.output_input_ratio).toBe(0);
    expect(res.body.efficiency.avg_duration_per_token_ms).toBe(0);
    // per-model: total tokens 0 → tokens_per_second computed arm = 0
    expect(res.body.by_model["gpt-4o"].tokens_per_second).toBe(0);
  });

  test("computes output_input_ratio when input tokens exist but output is zero", async () => {
    insertSession("s1");
    // input tokens present, output zero → g.total_tok_in > 0 arm taken,
    // ratio = 0/in = 0; totalTokens > 0 so avg_duration_per_token computed.
    insertEvent("e1", "s1", { model: "gpt-4o", duration: 100, tokensIn: 60, tokensOut: 0 });

    const app = createApp();
    const res = await request(app).get("/analytics/performance").expect(200);

    expect(res.body.efficiency.output_input_ratio).toBe(0);
    // totalTokens (60) > 0 and total_dur (100) > 0
    expect(res.body.efficiency.avg_duration_per_token_ms).toBeGreaterThan(0);
    expect(res.body.by_model["gpt-4o"].tokens_per_second).toBeGreaterThan(0);
  });
});
