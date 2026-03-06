/* ── Baselines Route Tests ── */

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
const baselinesRouter = require("../routes/baselines");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/baselines", baselinesRouter);
  return app;
}

function seed(sessionId, agentName, tokensIn, tokensOut, events = []) {
  mockDb.prepare(`
    INSERT OR REPLACE INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status)
    VALUES (?, ?, '2026-03-01T00:00:00Z', '2026-03-01T00:05:00Z', ?, ?, 'completed')
  `).run(sessionId, agentName, tokensIn, tokensOut);

  events.forEach((e, i) => {
    mockDb.prepare(`
      INSERT OR REPLACE INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      `${sessionId}-evt-${i}`,
      sessionId,
      e.event_type || "llm_call",
      `2026-03-01T00:0${i}:00Z`,
      e.model || "gpt-4",
      e.tokens_in || 0,
      e.tokens_out || 0,
      e.duration_ms || 100,
    );
  });
}

beforeEach(() => {
  if (mockDb) {
    mockDb.exec("DELETE FROM events");
    mockDb.exec("DELETE FROM sessions");
    try { mockDb.exec("DELETE FROM agent_baselines"); } catch (_) { /* may not exist yet */ }
  }
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

describe("Baselines API", () => {
  const app = createApp();

  test("GET /baselines returns empty list initially", async () => {
    const res = await request(app).get("/baselines");
    expect(res.status).toBe(200);
    expect(res.body.baselines).toEqual([]);
    expect(res.body.count).toBe(0);
  });

  test("POST /baselines/record creates baseline from session", async () => {
    seed("sess-1", "my-agent", 500, 200, [
      { event_type: "llm_call", tokens_in: 500, tokens_out: 200, duration_ms: 150 },
    ]);

    const res = await request(app)
      .post("/baselines/record")
      .send({ session_id: "sess-1" });

    expect(res.status).toBe(201);
    expect(res.body.agent_name).toBe("my-agent");
    expect(res.body.samples).toBe(1);
  });

  test("POST /baselines/record updates running average", async () => {
    seed("sess-a", "bot", 100, 50, [
      { tokens_in: 100, tokens_out: 50, duration_ms: 100 },
    ]);
    seed("sess-b", "bot", 300, 150, [
      { tokens_in: 300, tokens_out: 150, duration_ms: 200 },
    ]);

    await request(app).post("/baselines/record").send({ session_id: "sess-a" });
    await request(app).post("/baselines/record").send({ session_id: "sess-b" });

    const res = await request(app).get("/baselines/bot");
    expect(res.status).toBe(200);
    expect(res.body.samples).toBe(2);
    expect(res.body.avg_tokens_in).toBe(200);
    expect(res.body.avg_tokens_out).toBe(100);
  });

  test("POST /baselines/record rejects missing session_id", async () => {
    const res = await request(app).post("/baselines/record").send({});
    expect(res.status).toBe(400);
  });

  test("POST /baselines/record rejects unknown session", async () => {
    const res = await request(app)
      .post("/baselines/record")
      .send({ session_id: "nonexistent-session" });
    expect(res.status).toBe(404);
  });

  test("POST /baselines/check returns healthy verdict", async () => {
    seed("sess-1", "agent-x", 1000, 500, [
      { tokens_in: 1000, tokens_out: 500, duration_ms: 200 },
    ]);
    await request(app).post("/baselines/record").send({ session_id: "sess-1" });

    seed("sess-2", "agent-x", 1100, 550, [
      { tokens_in: 1100, tokens_out: 550, duration_ms: 210 },
    ]);
    const res = await request(app)
      .post("/baselines/check")
      .send({ session_id: "sess-2" });

    expect(res.status).toBe(200);
    expect(res.body.verdict).toBe("healthy");
    expect(res.body.checks.total_tokens).toBeDefined();
    expect(res.body.checks.total_tokens.status).toBe("normal");
  });

  test("POST /baselines/check detects regression", async () => {
    seed("sess-base", "agent-r", 100, 50, [
      { tokens_in: 100, tokens_out: 50, duration_ms: 100 },
    ]);
    await request(app).post("/baselines/record").send({ session_id: "sess-base" });

    seed("sess-regress", "agent-r", 1000, 500, [
      { tokens_in: 1000, tokens_out: 500, duration_ms: 1000 },
    ]);
    const res = await request(app)
      .post("/baselines/check")
      .send({ session_id: "sess-regress" });

    expect(res.status).toBe(200);
    expect(res.body.verdict).toBe("regression");
  });

  test("POST /baselines/check returns 404 when no baseline", async () => {
    seed("sess-orphan", "no-baseline-agent", 100, 50, []);
    const res = await request(app)
      .post("/baselines/check")
      .send({ session_id: "sess-orphan" });
    expect(res.status).toBe(404);
  });

  test("DELETE /baselines/:agentName removes baseline", async () => {
    seed("sess-d", "del-agent", 100, 50, []);
    await request(app).post("/baselines/record").send({ session_id: "sess-d" });

    const del = await request(app).delete("/baselines/del-agent");
    expect(del.status).toBe(200);

    const get = await request(app).get("/baselines/del-agent");
    expect(get.status).toBe(404);
  });

  test("DELETE /baselines/:agentName returns 404 for unknown", async () => {
    const res = await request(app).delete("/baselines/ghost-agent");
    expect(res.status).toBe(404);
  });

  test("GET /baselines/:agentName returns 404 for unknown", async () => {
    const res = await request(app).get("/baselines/nope");
    expect(res.status).toBe(404);
  });
});
