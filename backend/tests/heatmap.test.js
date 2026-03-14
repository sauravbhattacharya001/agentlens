/* ── Usage Heatmap Route Tests ── */

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
const heatmapRouter = require("../routes/heatmap");

let app;

function seedData(db) {
  db.exec("DELETE FROM events; DELETE FROM sessions;");
  const insertSession = db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)"
  );
  const insertEvent = db.prepare(
    "INSERT INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?)"
  );

  insertSession.run("s1", "agent-a", "2025-01-06T10:00:00Z", "completed");
  insertEvent.run("e1", "s1", "llm_call", "2025-01-06T10:30:00Z", 100, 200);
  insertEvent.run("e2", "s1", "tool_call", "2025-01-06T10:45:00Z", 50, 100);

  insertSession.run("s2", "agent-b", "2025-01-08T15:00:00Z", "completed");
  insertEvent.run("e3", "s2", "llm_call", "2025-01-08T15:30:00Z", 200, 300);

  insertSession.run("s3", "agent-a", "2025-01-11T22:00:00Z", "active");
  insertEvent.run("e4", "s3", "llm_call", "2025-01-11T22:15:00Z", 150, 250);

  insertEvent.run("e5", "s1", "llm_call", "2025-01-06T10:55:00Z", 80, 120);
}

beforeAll(() => {
  app = express();
  app.use(express.json());
  app.use("/heatmap", heatmapRouter);
});

beforeEach(() => {
  const { getDb } = require("../db");
  seedData(getDb());
});

afterAll(() => { if (mockDb) mockDb.close(); });

test("returns 7x24 grid", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.status).toBe(200);
  expect(res.body.grid).toHaveLength(7);
  for (let d = 0; d < 7; d++) expect(res.body.grid[d]).toHaveLength(24);
});

test("default metric is events", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.metric).toBe("events");
});

test("events counted per slot", async () => {
  const res = await request(app).get("/heatmap?metric=events");
  expect(res.body.grid[1][10]).toBe(3);
  expect(res.body.grid[3][15]).toBe(1);
  expect(res.body.grid[6][22]).toBe(1);
  expect(res.body.grid[0][0]).toBe(0);
});

test("tokens summed per slot", async () => {
  const res = await request(app).get("/heatmap?metric=tokens");
  expect(res.body.grid[1][10]).toBe(650);
  expect(res.body.grid[3][15]).toBe(500);
});

test("sessions counted by started_at", async () => {
  const res = await request(app).get("/heatmap?metric=sessions");
  expect(res.body.grid[1][10]).toBe(1);
  expect(res.body.grid[3][15]).toBe(1);
});

test("intensity normalized", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.intensity[1][10]).toBe(1);
  expect(res.body.intensity[0][0]).toBe(0);
});

test("stats peak", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.stats.peak.dayIndex).toBe(1);
  expect(res.body.stats.peak.hour).toBe(10);
  expect(res.body.stats.total).toBe(5);
});

test("weekend ratio", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.stats.weekendVsWeekday).toBe(0.25);
});

test("agent filter", async () => {
  const res = await request(app).get("/heatmap?agent=agent-a");
  expect(res.body.grid[1][10]).toBe(3);
  expect(res.body.grid[3][15]).toBe(0);
});

test("date range filter", async () => {
  const res = await request(app).get("/heatmap?from=2025-01-08T00:00:00Z&to=2025-01-09T00:00:00Z");
  expect(res.body.grid[3][15]).toBe(1);
  expect(res.body.grid[1][10]).toBe(0);
});

test("invalid metric 400", async () => {
  const res = await request(app).get("/heatmap?metric=invalid");
  expect(res.status).toBe(400);
});

test("empty db zero grid", async () => {
  require("../db").getDb().exec("DELETE FROM events; DELETE FROM sessions;");
  const res = await request(app).get("/heatmap");
  expect(res.body.stats.total).toBe(0);
});

test("filter metadata", async () => {
  const res = await request(app).get("/heatmap?agent=test&from=2025-01-01&to=2025-12-31");
  expect(res.body.filters).toEqual({ agent: "test", from: "2025-01-01", to: "2025-12-31" });
});

test("combined filters", async () => {
  const res = await request(app).get(
    "/heatmap?metric=tokens&agent=agent-a&from=2025-01-06T00:00:00Z&to=2025-01-07T00:00:00Z"
  );
  expect(res.body.grid[1][10]).toBe(650);
  expect(res.body.grid[6][22]).toBe(0);
});

test("correct day names", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.days[0]).toBe("Sunday");
  expect(res.body.days[6]).toBe("Saturday");
});

test("hourTotals", async () => {
  const res = await request(app).get("/heatmap");
  expect(res.body.stats.hourTotals[10]).toBe(3);
  expect(res.body.stats.hourTotals[15]).toBe(1);
});
