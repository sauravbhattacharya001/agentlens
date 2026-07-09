/**
 * Route-level tests for GET /analytics/heatmap and GET /analytics/cache.
 *
 * The pure matrix builder (lib/heatmap.buildHeatmap) is already covered by
 * heatmap.test.js, but that suite calls the function directly and never goes
 * through the HTTP route. The route itself owns wiring that no other test
 * exercised end-to-end:
 *
 *   /analytics/heatmap
 *     - the metric whitelist: only "events" | "tokens" | "sessions" are
 *       honored; anything else (or an omitted param) falls back to "events".
 *     - the metric -> prepared-statement mapping (getHeatmapStatements()[metric]):
 *       each metric hits a DIFFERENT SQL source (events count vs. token SUM vs.
 *       sessions count), so the three must yield genuinely different values.
 *     - the days window: parseDays clamps to 1..365, and daysAgoCutoff filters
 *       stale rows out of the matrix.
 *
 *   /analytics/cache
 *     - returns analyticsCache.stats() verbatim: { size, hits, misses,
 *       hitRate, maxEntries, ttlMs }, with the maxEntries=100 the route
 *       configured.
 *
 * These pin the HTTP contract so a refactor that drops the whitelist, swaps a
 * metric->statement wire, or reshapes the cache-stats body fails loudly.
 */

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

// Build an ISO timestamp `n` days before now. Heatmap rows derive dow/hour
// from the stored text, so we pin the hour explicitly (14:MM UTC).
function daysAgoTs(n, hh = 14, mm = 30) {
  const d = new Date(Date.now() - n * 86400000);
  const date = d.toISOString().slice(0, 10);
  const HH = String(hh).padStart(2, "0");
  const MM = String(mm).padStart(2, "0");
  return { ts: `${date}T${HH}:${MM}:00Z`, dow: d.getUTCDay(), hour: hh };
}

function insertSession(id, ts, tokensIn = 0, tokensOut = 0) {
  mockDb
    .prepare(
      `INSERT INTO sessions (session_id, agent_name, started_at, status, total_tokens_in, total_tokens_out)
       VALUES (?, 'test-agent', ?, 'completed', ?, ?)`
    )
    .run(id, ts, tokensIn, tokensOut);
}

function insertEvent(id, sessionId, ts, tokensIn = 0, tokensOut = 0) {
  mockDb
    .prepare(
      `INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms)
       VALUES (?, ?, 'llm_call', ?, 'gpt-4o', ?, ?, 100)`
    )
    .run(id, sessionId, ts, tokensIn, tokensOut);
}

// Sum every populated matrix cell (a quick "how much landed" check).
function matrixSum(matrix) {
  return matrix.flat().reduce((a, b) => a + b, 0);
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

describe("GET /analytics/heatmap — shape", () => {
  test("returns a full 7×24 matrix even with no data", async () => {
    const res = await request(createApp()).get("/analytics/heatmap").expect(200);
    expect(res.body.matrix).toHaveLength(7);
    for (const row of res.body.matrix) expect(row).toHaveLength(24);
    expect(res.body.max_value).toBe(0);
    expect(res.body.cells).toEqual([]);
    // period_days/metric are echoed from the request context
    expect(res.body.period_days).toBe(30);
    expect(res.body.metric).toBe("events");
  });

  test("places a bucketed value at the [dow][hour] slot it was recorded in", async () => {
    const a = daysAgoTs(2, 14);
    insertSession("s1", a.ts);
    insertEvent("e1", "s1", a.ts, 3, 4);

    const res = await request(createApp())
      .get("/analytics/heatmap?metric=events")
      .expect(200);

    expect(res.body.matrix[a.dow][a.hour]).toBe(1);
    expect(res.body.max_value).toBe(1);
    expect(res.body.peak).toMatchObject({ day: a.dow, hour: a.hour, value: 1 });
  });
});

describe("GET /analytics/heatmap — metric selects a different statement", () => {
  // One session + two events at the same dow/hour, recent enough to be inside
  // the default 30-day window. Each metric hits a different SQL source so the
  // three totals MUST differ: events=2, tokens=(10+20+5+5)=40, sessions=1.
  function seedOneBucket() {
    const a = daysAgoTs(2, 14);
    insertSession("s1", a.ts, 10, 20);
    insertEvent("e1", "s1", a.ts, 10, 20);
    insertEvent("e2", "s1", a.ts, 5, 5);
    return a;
  }

  test('metric="events" counts events (2)', async () => {
    seedOneBucket();
    const res = await request(createApp())
      .get("/analytics/heatmap?metric=events")
      .expect(200);
    expect(res.body.metric).toBe("events");
    expect(res.body.max_value).toBe(2);
    expect(matrixSum(res.body.matrix)).toBe(2);
  });

  test('metric="tokens" sums tokens_in+tokens_out (40)', async () => {
    seedOneBucket();
    const res = await request(createApp())
      .get("/analytics/heatmap?metric=tokens")
      .expect(200);
    expect(res.body.metric).toBe("tokens");
    expect(res.body.max_value).toBe(40);
    expect(matrixSum(res.body.matrix)).toBe(40);
  });

  test('metric="sessions" counts sessions (1)', async () => {
    seedOneBucket();
    const res = await request(createApp())
      .get("/analytics/heatmap?metric=sessions")
      .expect(200);
    expect(res.body.metric).toBe("sessions");
    expect(res.body.max_value).toBe(1);
    expect(matrixSum(res.body.matrix)).toBe(1);
  });
});

describe("GET /analytics/heatmap — metric whitelist fallback", () => {
  // An unrecognized or omitted metric must fall back to "events" (not error,
  // not pass an arbitrary string through to getHeatmapStatements[...]).
  test.each([
    ["omitted", "/analytics/heatmap"],
    ["unknown value", "/analytics/heatmap?metric=bogus"],
    ["empty value", "/analytics/heatmap?metric="],
    ["numeric-ish", "/analytics/heatmap?metric=123"],
  ])("%s falls back to metric=events", async (_label, url) => {
    const a = daysAgoTs(2, 14);
    insertSession("s1", a.ts, 10, 20);
    insertEvent("e1", "s1", a.ts, 10, 20);
    insertEvent("e2", "s1", a.ts, 5, 5);

    const res = await request(createApp()).get(url).expect(200);
    // events => count is 2, distinct from the tokens (40) / sessions (1) totals
    expect(res.body.metric).toBe("events");
    expect(res.body.max_value).toBe(2);
  });
});

describe("GET /analytics/heatmap — days window", () => {
  test("clamps the days parameter to 1..365", async () => {
    const app = createApp();
    // parseDays: parseInt(raw) || default, then Math.max(1, ...), then Math.min(.., 365).
    // A NaN or a falsy-0 value takes the default (30); a negative value clamps up to 1.
    const neg = await request(app).get("/analytics/heatmap?days=-5").expect(200);
    const hi = await request(app).get("/analytics/heatmap?days=9999").expect(200);
    const zero = await request(app).get("/analytics/heatmap?days=0").expect(200);
    const bad = await request(app).get("/analytics/heatmap?days=abc").expect(200);
    expect(neg.body.period_days).toBe(1); // lower clamp
    expect(hi.body.period_days).toBe(365); // upper clamp
    expect(zero.body.period_days).toBe(30); // 0 is falsy -> default
    expect(bad.body.period_days).toBe(30); // NaN -> default
  });

  test("excludes rows older than the cutoff", async () => {
    insertSession("s1", daysAgoTs(2, 14).ts);
    insertEvent("recent", "s1", daysAgoTs(2, 14).ts, 1, 1);
    insertEvent("stale", "s1", daysAgoTs(100, 14).ts, 1, 1);

    const app = createApp();
    const wide = await request(app)
      .get("/analytics/heatmap?metric=events&days=365")
      .expect(200);
    const narrow = await request(app)
      .get("/analytics/heatmap?metric=events&days=7")
      .expect(200);

    expect(matrixSum(wide.body.matrix)).toBe(2); // both events in a 365-day window
    expect(matrixSum(narrow.body.matrix)).toBe(1); // stale event dropped at 7 days
  });
});

describe("GET /analytics/cache", () => {
  test("returns the cache stats shape with the route's configured caps", async () => {
    const res = await request(createApp()).get("/analytics/cache").expect(200);
    expect(res.body).toEqual(
      expect.objectContaining({
        size: expect.any(Number),
        hits: expect.any(Number),
        misses: expect.any(Number),
        hitRate: expect.any(Number),
        maxEntries: 100, // createCache({ ttlMs: 30000, maxEntries: 100 })
        ttlMs: 30000,
      })
    );
    // In the test env the cache middleware is disabled, so nothing is counted.
    expect(res.body.hitRate).toBe(0);
  });
});
