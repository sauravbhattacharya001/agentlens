const express = require("express");
const request = require("supertest");
const { getDb } = require("../db");

process.env.DB_PATH = ":memory:";

const leaderboardRouter = require("../routes/leaderboard");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/leaderboard", leaderboardRouter);
  return app;
}

function seedData() {
  const db = getDb();

  const agents = [
    { name: "lb-agent-alpha", sessions: 5, tokensIn: 1000, tokensOut: 3000, errors: 0 },
    { name: "lb-agent-beta", sessions: 10, tokensIn: 5000, tokensOut: 2000, errors: 3 },
    { name: "lb-agent-gamma", sessions: 3, tokensIn: 200, tokensOut: 800, errors: 0 },
    { name: "lb-agent-solo", sessions: 1, tokensIn: 100, tokensOut: 50, errors: 0 },
  ];

  const insertSession = db.prepare(
    `INSERT INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  const insertEvent = db.prepare(
    `INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  );

  let eventIdx = 0;
  for (const agent of agents) {
    for (let i = 0; i < agent.sessions; i++) {
      const sid = `${agent.name}-s-${i}`;
      const isError = i < agent.errors;
      const start = new Date(Date.now() - 86400000 * (i + 1));
      const end = new Date(start.getTime() + 60000 * (i + 1));
      const perSessionIn = Math.round(agent.tokensIn / agent.sessions);
      const perSessionOut = Math.round(agent.tokensOut / agent.sessions);

      insertSession.run(
        sid, agent.name, start.toISOString(), end.toISOString(),
        perSessionIn, perSessionOut, isError ? "error" : "completed"
      );

      for (let j = 0; j < 3; j++) {
        insertEvent.run(
          `lb-evt-${eventIdx++}`, sid, "llm_call",
          new Date(start.getTime() + j * 10000).toISOString(),
          "gpt-4", Math.round(perSessionIn / 3), Math.round(perSessionOut / 3),
          100 + j * 50
        );
      }
    }
  }
}

describe("GET /leaderboard", () => {
  let app;

  beforeAll(() => {
    app = createApp();
    const db = getDb();
    const existing = db.prepare("SELECT COUNT(*) as c FROM sessions WHERE session_id LIKE 'lb-agent-%'").get();
    if (existing.c === 0) seedData();
  });

  test("returns ranked agents with default sort", async () => {
    const res = await request(app).get("/leaderboard");
    expect(res.status).toBe(200);
    expect(res.body.agents.length).toBeGreaterThan(0);
    expect(res.body.agents[0].rank).toBe(1);
    const names = res.body.agents.map((a) => a.agent_name);
    expect(names).not.toContain("lb-agent-solo");
    const first = res.body.agents[0];
    expect(first).toHaveProperty("efficiency_ratio");
    expect(first).toHaveProperty("success_rate");
    expect(first).toHaveProperty("total_cost_usd");
  });

  test("sort by reliability", async () => {
    const res = await request(app).get("/leaderboard?sort=reliability");
    expect(res.status).toBe(200);
    expect(res.body.agents[0].success_rate).toBe(100);
  });

  test("sort by volume", async () => {
    const res = await request(app).get("/leaderboard?sort=volume");
    expect(res.status).toBe(200);
    expect(res.body.agents[0].agent_name).toBe("lb-agent-beta");
  });

  test("sort by speed ascending", async () => {
    const res = await request(app).get("/leaderboard?sort=speed");
    expect(res.status).toBe(200);
    expect(res.body.order).toBe("asc");
    for (let i = 1; i < res.body.agents.length; i++) {
      expect(res.body.agents[i].avg_session_duration_ms).toBeGreaterThanOrEqual(
        res.body.agents[i - 1].avg_session_duration_ms
      );
    }
  });

  test("invalid sort returns 400", async () => {
    const res = await request(app).get("/leaderboard?sort=invalid");
    expect(res.status).toBe(400);
  });

  test("min_sessions filter works", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    expect(res.status).toBe(200);
    const names = res.body.agents.map((a) => a.agent_name);
    expect(names).toContain("lb-agent-solo");
  });

  test("limit works", async () => {
    const res = await request(app).get("/leaderboard?limit=1&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.agents.length).toBe(1);
  });
});
