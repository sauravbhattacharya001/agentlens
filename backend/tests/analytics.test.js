/* ── Analytics — sessionsOverTime returns most recent 90 days (#19) ── */

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
        CREATE TABLE IF NOT EXISTS model_pricing (
          model TEXT PRIMARY KEY,
          input_cost_per_1m REAL NOT NULL,
          output_cost_per_1m REAL NOT NULL,
          currency TEXT DEFAULT 'USD',
          updated_at TEXT
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

// Helper: insert a session on a given date
function insertSession(id, date) {
  mockDb.prepare(
    `INSERT INTO sessions (session_id, agent_name, started_at, status, total_tokens_in, total_tokens_out)
     VALUES (?, 'test-agent', ?, 'completed', 100, 50)`
  ).run(id, `${date}T12:00:00Z`);
}

// Helper: generate a date string N days before a base date
function daysAgo(n, base = new Date("2026-01-01")) {
  const d = new Date(base);
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

beforeAll(() => {
  // Trigger lazy initialization of mockDb
  require("../db").getDb();
});

beforeEach(() => {
  if (mockDb) {
    mockDb.exec("DELETE FROM events");
    mockDb.exec("DELETE FROM sessions");
    mockDb.exec("DELETE FROM model_pricing");
  }
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

describe("GET /analytics — sessionsOverTime", () => {
  test("returns data in chronological (ASC) order for the frontend", async () => {
    // Insert sessions on 3 known dates (out of order to be thorough)
    insertSession("s1", "2025-12-01");
    insertSession("s2", "2025-12-15");
    insertSession("s3", "2025-12-10");

    const app = createApp();
    const res = await request(app).get("/analytics").expect(200);

    const days = res.body.sessions_over_time.map((r) => r.day);
    // Should be sorted chronologically (oldest first)
    expect(days).toEqual([...days].sort());
  });

  test("limits to 90 entries even when more days exist", async () => {
    // Insert 100 distinct days of sessions
    for (let i = 0; i < 100; i++) {
      insertSession(`s-${i}`, daysAgo(i));
    }

    const app = createApp();
    const res = await request(app).get("/analytics").expect(200);

    expect(res.body.sessions_over_time.length).toBeLessThanOrEqual(90);
  });

  test("returns the MOST RECENT 90 days, not the oldest (#19)", async () => {
    // Insert 100 distinct days of sessions — days 0..99 ago from 2026-01-01
    for (let i = 0; i < 100; i++) {
      insertSession(`s-${i}`, daysAgo(i));
    }

    const app = createApp();
    const res = await request(app).get("/analytics").expect(200);

    const days = res.body.sessions_over_time.map((r) => r.day);

    // The most recent day (daysAgo(0) = "2026-01-01") must be included
    expect(days).toContain(daysAgo(0));

    // The oldest day (daysAgo(99) = "2025-09-24") must NOT be included
    // because we only keep the 90 most recent
    expect(days).not.toContain(daysAgo(99));
    expect(days).not.toContain(daysAgo(98));
    expect(days).not.toContain(daysAgo(97));

    // The 90th most recent day (daysAgo(89)) SHOULD be included
    expect(days).toContain(daysAgo(89));

    // Verify chronological order (ASC) for the chart
    expect(days).toEqual([...days].sort());
  });
});

// ── Cost Analytics ─────────────────────────────────────────────────

function insertEvent(id, sessionId, model, tokensIn, tokensOut, timestamp) {
  mockDb.prepare(
    `INSERT INTO events (event_id, session_id, event_type, model, tokens_in, tokens_out, timestamp, duration_ms)
     VALUES (?, ?, 'llm_call', ?, ?, ?, ?, 100)`
  ).run(id, sessionId, model, tokensIn, tokensOut, timestamp);
}

function insertPricing(model, inputCost, outputCost) {
  mockDb.prepare(
    `INSERT INTO model_pricing (model, input_cost_per_1m, output_cost_per_1m, currency, updated_at)
     VALUES (?, ?, ?, 'USD', ?)`
  ).run(model, inputCost, outputCost, new Date().toISOString());
}

describe("GET /analytics/costs", () => {
  test("returns empty costs when no events exist", async () => {
    const app = createApp();
    const res = await request(app).get("/analytics/costs").expect(200);

    expect(res.body.total_cost).toBe(0);
    expect(res.body.by_model).toEqual([]);
    expect(res.body.daily_trend).toEqual([]);
    expect(res.body.currency).toBe("USD");
  });

  test("calculates cost for a single model", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s1", today);
    insertEvent("cost-e1", "cost-s1", "gpt-4o", 1000, 500, `${today}T10:00:00Z`);

    // gpt-4o default: input $2.50/1M, output $10.00/1M
    // input cost: 1000/1M * 2.50 = $0.0025
    // output cost: 500/1M * 10.00 = $0.005
    // total: $0.0075

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.total_cost).toBeGreaterThan(0);
    expect(res.body.by_model.length).toBe(1);
    expect(res.body.by_model[0].model).toBe("gpt-4o");
    expect(res.body.by_model[0].total_cost).toBeCloseTo(0.0075, 3);
    expect(res.body.by_model[0].percent).toBe(100);
  });

  test("calculates costs for multiple models", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s2", today);
    insertEvent("cost-e2", "cost-s2", "gpt-4o", 2000, 1000, `${today}T10:00:00Z`);
    insertEvent("cost-e3", "cost-s2", "gpt-4o-mini", 5000, 2000, `${today}T11:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.by_model.length).toBe(2);
    expect(res.body.total_cost).toBeGreaterThan(0);
    // Both models should sum to total
    const modelSum = res.body.by_model.reduce((s, m) => s + m.total_cost, 0);
    expect(modelSum).toBeCloseTo(res.body.total_cost, 3);
  });

  test("returns daily_trend with day-level granularity", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    insertSession("cost-s3", yesterday);
    insertEvent("cost-e4", "cost-s3", "gpt-4o", 1000, 500, `${yesterday}T10:00:00Z`);
    insertEvent("cost-e5", "cost-s3", "gpt-4o", 2000, 1000, `${today}T10:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.daily_trend.length).toBe(2);
    const days = res.body.daily_trend.map(d => d.day);
    expect(days).toContain(today);
    expect(days).toContain(yesterday);
  });

  test("uses custom pricing from model_pricing table", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s4", today);
    insertEvent("cost-e6", "cost-s4", "custom-model", 1000000, 0, `${today}T10:00:00Z`);

    // Insert custom pricing: $5/1M input, $10/1M output
    insertPricing("custom-model", 5.0, 10.0);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.by_model.length).toBe(1);
    expect(res.body.by_model[0].model).toBe("custom-model");
    // 1M tokens * $5/1M = $5.00
    expect(res.body.by_model[0].input_cost).toBeCloseTo(5.0, 2);
    expect(res.body.by_model[0].total_cost).toBeCloseTo(5.0, 2);
  });

  test("reports unmatched models", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s5", today);
    insertEvent("cost-e7", "cost-s5", "unknown-model-xyz", 1000, 500, `${today}T10:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.unmatched_models).toContain("unknown-model-xyz");
  });

  test("respects days parameter", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const longAgo = new Date(Date.now() - 60 * 86400000).toISOString().slice(0, 10);
    insertSession("cost-s6", today);
    insertSession("cost-s7", longAgo);
    insertEvent("cost-e8", "cost-s6", "gpt-4o", 1000, 500, `${today}T10:00:00Z`);
    insertEvent("cost-e9", "cost-s7", "gpt-4o", 1000, 500, `${longAgo}T10:00:00Z`);

    const app = createApp();
    const res7 = await request(app).get("/analytics/costs?days=7").expect(200);
    const res90 = await request(app).get("/analytics/costs?days=90").expect(200);

    // 7-day window should only include today's event
    expect(res7.body.by_model.length).toBeLessThanOrEqual(1);
    // 90-day window should include both
    expect(res90.body.total_cost).toBeGreaterThanOrEqual(res7.body.total_cost);
  });

  test("includes projected_monthly_cost", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s8", today);
    insertEvent("cost-e10", "cost-s8", "gpt-4o", 1000, 500, `${today}T10:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.projected_monthly_cost).toBeGreaterThan(0);
    expect(res.body.avg_daily_cost).toBeGreaterThan(0);
  });

  test("splits input and output costs", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s9", today);
    insertEvent("cost-e11", "cost-s9", "gpt-4o", 1000, 500, `${today}T10:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    expect(res.body.total_input_cost).toBeGreaterThan(0);
    expect(res.body.total_output_cost).toBeGreaterThan(0);
    expect(res.body.total_cost).toBeCloseTo(
      res.body.total_input_cost + res.body.total_output_cost, 3
    );
  });

  test("percent sums to ~100 for by_model entries", async () => {
    const today = new Date().toISOString().slice(0, 10);
    insertSession("cost-s10", today);
    insertEvent("cost-e12", "cost-s10", "gpt-4o", 1000, 500, `${today}T10:00:00Z`);
    insertEvent("cost-e13", "cost-s10", "gpt-4o-mini", 2000, 1000, `${today}T11:00:00Z`);

    const app = createApp();
    const res = await request(app).get("/analytics/costs?days=30").expect(200);

    const totalPercent = res.body.by_model.reduce((s, m) => s + m.percent, 0);
    expect(totalPercent).toBeCloseTo(100, 0);
  });
});
