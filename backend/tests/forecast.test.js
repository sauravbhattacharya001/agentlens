const express = require("express");
const Database = require("better-sqlite3");
const path = require("path");

// ── Test DB setup ───────────────────────────────────────────────

let mockDb;

function setupTestDb() {
  mockDb = new Database(":memory:");
  mockDb.pragma("journal_mode = WAL");
  mockDb.pragma("foreign_keys = ON");

  mockDb.exec(`
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY,
      agent_name TEXT NOT NULL DEFAULT 'default-agent',
      started_at TEXT NOT NULL,
      ended_at TEXT,
      metadata TEXT DEFAULT '{}',
      total_tokens_in INTEGER DEFAULT 0,
      total_tokens_out INTEGER DEFAULT 0,
      status TEXT DEFAULT 'active'
    );

    CREATE TABLE events (
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

    CREATE INDEX idx_events_session ON events(session_id);
    CREATE INDEX idx_events_timestamp ON events(timestamp);
    CREATE INDEX idx_events_model ON events(model);
    CREATE INDEX idx_sessions_agent ON sessions(agent_name);

    CREATE TABLE model_pricing (
      model TEXT PRIMARY KEY,
      input_cost_per_1m REAL NOT NULL DEFAULT 0,
      output_cost_per_1m REAL NOT NULL DEFAULT 0
    );
  `);

  return mockDb;
}

/**
 * Seed the test DB with realistic daily data over N days.
 */
function seedData(daysBack, opts) {
  opts = opts || {};
  const agent = opts.agent || "test-agent";
  const model = opts.model || "gpt-4o";
  const baseTokensIn = opts.baseTokensIn || 5000;
  const baseTokensOut = opts.baseTokensOut || 2000;
  const trend = opts.trend || 0; // tokens increase per day

  const insertSession = mockDb.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)"
  );
  const insertEvent = mockDb.prepare(
    "INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?, ?)"
  );

  for (let d = daysBack; d >= 0; d--) {
    const date = new Date();
    date.setDate(date.getDate() - d);
    const dateStr = date.toISOString().split("T")[0];
    const sessId = `sess-${dateStr}`;

    insertSession.run(sessId, agent, dateStr + "T10:00:00Z", "completed");

    // 2-4 events per session
    const eventCount = 2 + (d % 3);
    for (let e = 0; e < eventCount; e++) {
      const tokIn = baseTokensIn + trend * (daysBack - d) + Math.round(Math.random() * 500);
      const tokOut = baseTokensOut + Math.round(trend * (daysBack - d) * 0.4) + Math.round(Math.random() * 200);
      insertEvent.run(
        `evt-${dateStr}-${e}`,
        sessId,
        "llm_call",
        `${dateStr}T${10 + e}:00:00Z`,
        model,
        tokIn,
        tokOut
      );
    }
  }

  // Add pricing
  mockDb.prepare("INSERT OR REPLACE INTO model_pricing (model, input_cost_per_1m, output_cost_per_1m) VALUES (?, ?, ?)")
    .run("gpt-4o", 2.5, 10);
}

// ── Mock DB for the route module ────────────────────────────────

// We override the db module to return our test db
jest.mock("../db", () => ({
  getDb: () => mockDb,
}));

// ── Import route after mocking ──────────────────────────────────

const forecastRouter = require("../routes/forecast");
const { linearRegression, ema, stddev, predictionInterval, detectTrend, estimateCost, round } = forecastRouter._testExports;

// ── Supertest setup ─────────────────────────────────────────────

const request = require("supertest");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/forecast", forecastRouter);
  return app;
}

// ── Tests ───────────────────────────────────────────────────────

describe("Forecast API", () => {
  beforeEach(() => {
    setupTestDb();
  });

  // ── Unit: linearRegression ──────────────────────────────────

  describe("linearRegression", () => {
    test("empty array returns zero slope", () => {
      const r = linearRegression([]);
      expect(r.slope).toBe(0);
      expect(r.intercept).toBe(0);
    });

    test("single value returns zero slope, value as intercept", () => {
      const r = linearRegression([5]);
      expect(r.slope).toBe(0);
      expect(r.intercept).toBe(5);
    });

    test("constant values have zero slope", () => {
      const r = linearRegression([3, 3, 3, 3]);
      expect(r.slope).toBe(0);
      expect(r.intercept).toBe(3);
    });

    test("perfect linear increase", () => {
      const r = linearRegression([1, 2, 3, 4, 5]);
      expect(r.slope).toBeCloseTo(1, 5);
      expect(r.intercept).toBeCloseTo(1, 5);
      expect(r.r2).toBeCloseTo(1, 5);
    });

    test("perfect linear decrease", () => {
      const r = linearRegression([10, 8, 6, 4, 2]);
      expect(r.slope).toBeCloseTo(-2, 5);
      expect(r.intercept).toBeCloseTo(10, 5);
      expect(r.r2).toBeCloseTo(1, 5);
    });

    test("noisy data has r2 < 1", () => {
      const r = linearRegression([1, 3, 2, 5, 4, 7, 6]);
      expect(r.r2).toBeGreaterThan(0);
      expect(r.r2).toBeLessThan(1);
    });
  });

  // ── Unit: ema ───────────────────────────────────────────────

  describe("ema", () => {
    test("empty returns 0", () => {
      expect(ema([])).toBe(0);
    });

    test("single value returns that value", () => {
      expect(ema([42])).toBe(42);
    });

    test("high alpha weights recent values more", () => {
      const values = [1, 1, 1, 1, 10];
      const highAlpha = ema(values, 0.9);
      const lowAlpha = ema(values, 0.1);
      expect(highAlpha).toBeGreaterThan(lowAlpha);
    });

    test("alpha=1 returns last value", () => {
      expect(ema([1, 2, 3, 4, 5], 1.0)).toBe(5);
    });
  });

  // ── Unit: stddev ────────────────────────────────────────────

  describe("stddev", () => {
    test("< 2 values returns 0", () => {
      expect(stddev([])).toBe(0);
      expect(stddev([5])).toBe(0);
    });

    test("identical values returns 0", () => {
      expect(stddev([3, 3, 3])).toBe(0);
    });

    test("known standard deviation", () => {
      // [2, 4, 4, 4, 5, 5, 7, 9] — sample stddev = 2.138
      expect(stddev([2, 4, 4, 4, 5, 5, 7, 9])).toBeCloseTo(2.138, 2);
    });
  });

  // ── Unit: predictionInterval ────────────────────────────────

  describe("predictionInterval", () => {
    test("< 3 values uses ±50% fallback", () => {
      const { low, high } = predictionInterval([5, 10], 2.5, 5, 3);
      expect(low).toBeGreaterThanOrEqual(0);
      expect(high).toBeGreaterThan(low);
    });

    test("wider interval for noisier data", () => {
      const clean = [1, 2, 3, 4, 5];
      const noisy = [1, 5, 2, 8, 3];
      const { slope: s1, intercept: i1 } = linearRegression(clean);
      const { slope: s2, intercept: i2 } = linearRegression(noisy);
      const ci1 = predictionInterval(clean, s1, i1, 6);
      const ci2 = predictionInterval(noisy, s2, i2, 6);
      expect(ci2.high - ci2.low).toBeGreaterThan(ci1.high - ci1.low);
    });
  });

  // ── Unit: detectTrend ───────────────────────────────────────

  describe("detectTrend", () => {
    test("insufficient data", () => {
      expect(detectTrend([1, 2]).trend).toBe("insufficient_data");
    });

    test("increasing trend", () => {
      expect(detectTrend([1, 2, 4, 8, 16]).trend).toBe("increasing");
    });

    test("decreasing trend", () => {
      expect(detectTrend([16, 8, 4, 2, 1]).trend).toBe("decreasing");
    });

    test("stable trend", () => {
      expect(detectTrend([5, 5, 5, 5, 5]).trend).toBe("stable");
    });
  });

  // ── Unit: estimateCost ──────────────────────────────────────

  describe("estimateCost", () => {
    test("known model pricing", () => {
      const map = { "gpt-4o": { input: 2.5, output: 10 } };
      const cost = estimateCost({ model: "gpt-4o", tokens_in: 1000, tokens_out: 500 }, map);
      // 1000 * 2.5/1M + 500 * 10/1M = 0.0025 + 0.005 = 0.0075
      expect(cost).toBeCloseTo(0.0075, 6);
    });

    test("unknown model returns 0", () => {
      expect(estimateCost({ model: "unknown", tokens_in: 1000, tokens_out: 500 }, {})).toBe(0);
    });

    test("missing model returns 0", () => {
      expect(estimateCost({ tokens_in: 1000, tokens_out: 500 }, {})).toBe(0);
    });
  });

  // ── Unit: round ─────────────────────────────────────────────

  describe("round", () => {
    test("rounds to specified decimals", () => {
      expect(round(3.14159, 2)).toBe(3.14);
      expect(round(3.14159, 4)).toBe(3.1416);
    });
  });

  // ── Integration: GET /forecast ──────────────────────────────

  describe("GET /forecast", () => {
    test("returns empty forecast when no data", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast");
      expect(res.status).toBe(200);
      expect(res.body.dataPointsUsed).toBe(0);
      expect(res.body.forecast).toEqual([]);
    });

    test("returns forecast with seeded data", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast?forecastDays=3");
      expect(res.status).toBe(200);
      expect(res.body.forecast.length).toBe(3);
      expect(res.body.dataPointsUsed).toBeGreaterThan(0);
      expect(res.body.method).toBeTruthy();
      expect(res.body.summary.totalPredictedCost).toBeGreaterThan(0);
      expect(res.body.summary.totalPredictedTokens).toBeGreaterThan(0);
    });

    test("linear method with sufficient data", async () => {
      seedData(15);
      const app = createApp();
      const res = await request(app).get("/forecast?method=linear&forecastDays=5");
      expect(res.status).toBe(200);
      expect(res.body.method).toBe("linear");
      expect(res.body.forecast.length).toBe(5);
      for (const p of res.body.forecast) {
        expect(p.method).toBe("linear");
        expect(p.predictedCost).toBeGreaterThanOrEqual(0);
        expect(p.confidenceLow).toBeDefined();
        expect(p.confidenceHigh).toBeDefined();
        expect(p.confidenceHigh).toBeGreaterThanOrEqual(p.confidenceLow);
      }
    });

    test("ema method", async () => {
      seedData(5);
      const app = createApp();
      const res = await request(app).get("/forecast?method=ema&forecastDays=3");
      expect(res.status).toBe(200);
      expect(res.body.method).toBe("ema");
    });

    test("average method", async () => {
      seedData(3);
      const app = createApp();
      const res = await request(app).get("/forecast?method=average&forecastDays=3");
      expect(res.status).toBe(200);
      expect(res.body.method).toBe("average");
    });

    test("auto selects linear for >= 5 days", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast?method=auto");
      expect(res.status).toBe(200);
      expect(res.body.method).toBe("linear");
    });

    test("auto selects ema for 2-4 days", async () => {
      seedData(3);
      const app = createApp();
      const res = await request(app).get("/forecast?method=auto");
      expect(res.status).toBe(200);
      expect(["ema", "average"]).toContain(res.body.method);
    });

    test("rejects invalid method", async () => {
      seedData(5);
      const app = createApp();
      const res = await request(app).get("/forecast?method=invalid");
      expect(res.status).toBe(400);
      expect(res.body.error).toContain("method");
    });

    test("agent filter", async () => {
      seedData(10, { agent: "alpha-agent" });
      const app = createApp();
      const res = await request(app).get("/forecast?agent=alpha-agent");
      expect(res.status).toBe(200);
      expect(res.body.dataPointsUsed).toBeGreaterThan(0);
      expect(res.body.filters.agent).toBe("alpha-agent");

      // Non-existent agent returns no data
      const res2 = await request(app).get("/forecast?agent=nonexistent");
      expect(res2.body.dataPointsUsed).toBe(0);
    });

    test("model filter", async () => {
      seedData(10, { model: "gpt-4o" });
      const app = createApp();
      const res = await request(app).get("/forecast?model=gpt-4o");
      expect(res.status).toBe(200);
      expect(res.body.dataPointsUsed).toBeGreaterThan(0);
      expect(res.body.filters.model).toBe("gpt-4o");
    });

    test("trend detection included", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast");
      expect(res.body.trend).toBeDefined();
      expect(res.body.trend.cost).toBeDefined();
      expect(res.body.trend.tokens).toBeDefined();
      expect(res.body.trend.cost.trend).toBeTruthy();
    });

    test("historical summary included", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast");
      expect(res.body.historical).toBeDefined();
      expect(res.body.historical.daysAnalyzed).toBeGreaterThan(0);
      expect(res.body.historical.totalCost).toBeGreaterThanOrEqual(0);
      expect(res.body.historical.totalTokens).toBeGreaterThan(0);
    });

    test("forecast dates are sequential", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast?forecastDays=5");
      const dates = res.body.forecast.map(p => p.date);
      for (let i = 1; i < dates.length; i++) {
        expect(dates[i] > dates[i - 1]).toBe(true);
      }
    });
  });

  // ── Integration: GET /forecast/budget ───────────────────────

  describe("GET /forecast/budget", () => {
    test("rejects missing budget", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast/budget");
      expect(res.status).toBe(400);
      expect(res.body.error).toContain("budget");
    });

    test("rejects zero budget", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=0");
      expect(res.status).toBe(400);
    });

    test("rejects negative budget", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=-10");
      expect(res.status).toBe(400);
    });

    test("returns unknown when no data", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=100");
      expect(res.status).toBe(200);
      expect(res.body.severity).toBe("unknown");
    });

    test("safe when spending well under budget", async () => {
      seedData(10, { baseTokensIn: 100, baseTokensOut: 50 });
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=10000");
      expect(res.status).toBe(200);
      expect(res.body.severity).toBe("safe");
      expect(res.body.projectedSpend).toBeLessThan(10000);
    });

    test("critical when spending far over budget", async () => {
      seedData(10, { baseTokensIn: 500000, baseTokensOut: 200000 });
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=0.01");
      expect(res.status).toBe(200);
      expect(res.body.severity).toBe("critical");
      expect(res.body.overshootPct).toBeGreaterThan(0);
    });

    test("includes all expected fields", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=100");
      expect(res.status).toBe(200);
      expect(res.body.budget).toBe(100);
      expect(res.body.totalSpentSoFar).toBeDefined();
      expect(res.body.dailyAverageCost).toBeDefined();
      expect(res.body.projectedSpend).toBeDefined();
      expect(res.body.utilizationPct).toBeDefined();
      expect(res.body.periodDays).toBeDefined();
      expect(res.body.daysAnalyzed).toBeGreaterThan(0);
      expect(typeof res.body.message).toBe("string");
    });

    test("agent filter works", async () => {
      seedData(10, { agent: "expensive-agent" });
      const app = createApp();
      const res = await request(app).get("/forecast/budget?budget=100&agent=expensive-agent");
      expect(res.status).toBe(200);
      expect(res.body.agent).toBe("expensive-agent");
      expect(res.body.daysAnalyzed).toBeGreaterThan(0);
    });
  });

  // ── Integration: GET /forecast/spending-summary ─────────────

  describe("GET /forecast/spending-summary", () => {
    test("returns summary with seeded data", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.status).toBe(200);
      expect(res.body.totalCost).toBeGreaterThanOrEqual(0);
      expect(res.body.totalTokens).toBeGreaterThan(0);
      expect(res.body.totalTokensIn).toBeGreaterThan(0);
      expect(res.body.totalTokensOut).toBeGreaterThan(0);
      expect(res.body.daysTracked).toBeGreaterThan(0);
      expect(res.body.dailyAverageCost).toBeGreaterThanOrEqual(0);
      expect(res.body.weeklyProjection).toBeGreaterThanOrEqual(0);
      expect(res.body.monthlyProjection).toBeGreaterThanOrEqual(0);
    });

    test("model breakdown present", async () => {
      seedData(10, { model: "gpt-4o" });
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.body.modelBreakdown).toBeDefined();
      expect(Object.keys(res.body.modelBreakdown).length).toBeGreaterThan(0);
      const entry = Object.values(res.body.modelBreakdown)[0];
      expect(entry.cost).toBeDefined();
      expect(entry.tokensIn).toBeDefined();
      expect(entry.tokensOut).toBeDefined();
      expect(entry.costPct).toBeDefined();
    });

    test("trend included", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.body.trend).toBeDefined();
      expect(res.body.trend.trend).toBeTruthy();
    });

    test("busiest day identified", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.body.busiestDay).toBeTruthy();
      expect(res.body.busiestDayCost).toBeGreaterThanOrEqual(0);
    });

    test("cost per 1k tokens calculated", async () => {
      seedData(10);
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.body.costPer1kTokens).toBeGreaterThanOrEqual(0);
    });

    test("empty DB returns zeros", async () => {
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary");
      expect(res.status).toBe(200);
      expect(res.body.totalCost).toBe(0);
      expect(res.body.totalTokens).toBe(0);
      expect(res.body.daysTracked).toBe(0);
    });

    test("agent filter", async () => {
      seedData(10, { agent: "cost-agent" });
      const app = createApp();
      const res = await request(app).get("/forecast/spending-summary?agent=cost-agent");
      expect(res.status).toBe(200);
      expect(res.body.filters.agent).toBe("cost-agent");
      expect(res.body.totalTokens).toBeGreaterThan(0);
    });
  });
});
