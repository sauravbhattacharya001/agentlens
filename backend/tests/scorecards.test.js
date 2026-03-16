/* ── Scorecards Route Tests ── */

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
          duration_ms REAL,
          metadata TEXT DEFAULT '{}',
          FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const scorecardsRouter = require("../routes/scorecards");
const { getDb } = require("../db");

const app = express();
app.use(express.json());
app.use("/scorecards", scorecardsRouter);

function seed() {
  const db = getDb();
  db.exec("DELETE FROM events");
  db.exec("DELETE FROM sessions");

  const now = new Date();
  const insertSession = db.prepare(
    "INSERT INTO sessions (session_id, agent_name, status, total_tokens_in, total_tokens_out, started_at) VALUES (?, ?, ?, ?, ?, ?)"
  );
  const insertEvent = db.prepare(
    "INSERT INTO events (event_id, session_id, event_type, model, tokens_in, tokens_out, duration_ms, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
  );

  // Alpha: 10 sessions, 8 completed, 2 errors
  for (let i = 0; i < 10; i++) {
    const sid = `alpha-${i}`;
    const status = i < 8 ? "completed" : "error";
    const d = new Date(now - i * 86400000).toISOString();
    insertSession.run(sid, "alpha-agent", status, 500 + i * 10, 200 + i * 5, d);
    insertEvent.run(`ev-alpha-${i}`, sid, "llm_call", "gpt-4", 500, 200, 300 + i * 50, d);
  }

  // Beta: 3 sessions, all completed
  for (let i = 0; i < 3; i++) {
    const sid = `beta-${i}`;
    const d = new Date(now - i * 86400000).toISOString();
    insertSession.run(sid, "beta-agent", "completed", 100, 50, d);
    insertEvent.run(`ev-beta-${i}`, sid, "llm_call", "claude-3", 100, 50, 200, d);
  }
}

beforeAll(() => seed());

describe("GET /scorecards", () => {
  test("returns all agents with scores", async () => {
    const res = await request(app).get("/scorecards");
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.scorecards)).toBe(true);
    expect(res.body.scorecards.length).toBe(2);
    expect(res.body.meta.agent_count).toBe(2);

    const alpha = res.body.scorecards.find((s) => s.agent_name === "alpha-agent");
    expect(alpha).toBeDefined();
    expect(alpha.composite_score).toBeGreaterThan(0);
    expect(alpha.grade).toBeDefined();
    expect(alpha.grade_color).toBeDefined();
    expect(alpha.metrics.total_sessions).toBe(10);
    expect(alpha.metrics.completed).toBe(8);
    expect(alpha.metrics.errors).toBe(2);
    expect(alpha.metrics.success_rate).toBe(80);
    expect(alpha.metrics.error_rate).toBe(20);
  });

  test("respects days param", async () => {
    const res = await request(app).get("/scorecards?days=3");
    expect(res.status).toBe(200);
    expect(res.body.meta.days).toBe(3);
  });

  test("clamps days to valid range", async () => {
    const res = await request(app).get("/scorecards?days=999");
    expect(res.body.meta.days).toBe(365);
    const res2 = await request(app).get("/scorecards?days=-5");
    expect(res2.body.meta.days).toBe(1);
  });

  test("sorted by composite_score descending", async () => {
    const res = await request(app).get("/scorecards");
    const scores = res.body.scorecards.map((s) => s.composite_score);
    for (let i = 1; i < scores.length; i++) {
      expect(scores[i - 1]).toBeGreaterThanOrEqual(scores[i]);
    }
  });

  test("grade colors match grade prefix", async () => {
    const res = await request(app).get("/scorecards");
    for (const sc of res.body.scorecards) {
      if (sc.grade.startsWith("A")) expect(sc.grade_color).toBe("#22c55e");
      if (sc.grade.startsWith("B")) expect(sc.grade_color).toBe("#3b82f6");
      if (sc.grade.startsWith("C")) expect(sc.grade_color).toBe("#eab308");
    }
  });

  test("trend data has expected shape", async () => {
    const res = await request(app).get("/scorecards");
    const alpha = res.body.scorecards.find((s) => s.agent_name === "alpha-agent");
    expect(Array.isArray(alpha.trend)).toBe(true);
    if (alpha.trend.length > 0) {
      expect(alpha.trend[0]).toHaveProperty("week");
      expect(alpha.trend[0]).toHaveProperty("sessions");
      expect(alpha.trend[0]).toHaveProperty("errorRate");
    }
  });
});

describe("GET /scorecards/:agent", () => {
  test("returns detailed scorecard", async () => {
    const res = await request(app).get("/scorecards/alpha-agent");
    expect(res.status).toBe(200);
    expect(res.body.agent_name).toBe("alpha-agent");
    expect(res.body.metrics).toBeDefined();
    expect(Array.isArray(res.body.models)).toBe(true);
    expect(Array.isArray(res.body.daily_trend)).toBe(true);
    expect(res.body.models.length).toBeGreaterThan(0);
  });

  test("returns 404 for unknown agent", async () => {
    const res = await request(app).get("/scorecards/nonexistent");
    expect(res.status).toBe(404);
  });

  test("returns detail for beta with 100% success", async () => {
    const res = await request(app).get("/scorecards/beta-agent");
    expect(res.status).toBe(200);
    expect(res.body.metrics.success_rate).toBe(100);
    expect(res.body.metrics.error_rate).toBe(0);
  });

  test("includes model breakdown", async () => {
    const res = await request(app).get("/scorecards/alpha-agent");
    expect(res.body.models.length).toBeGreaterThanOrEqual(1);
    const m = res.body.models[0];
    expect(m).toHaveProperty("model");
    expect(m).toHaveProperty("calls");
    expect(m).toHaveProperty("tokens_in");
    expect(m).toHaveProperty("avg_latency_ms");
  });
});
