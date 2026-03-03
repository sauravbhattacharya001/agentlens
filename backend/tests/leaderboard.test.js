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

  // ── Sort by cost ────────────────────────────────────────────────

  test("sort by cost ascending (default order)", async () => {
    const res = await request(app).get("/leaderboard?sort=cost&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.sort).toBe("cost");
    expect(res.body.order).toBe("asc");
    for (let i = 1; i < res.body.agents.length; i++) {
      expect(res.body.agents[i].cost_per_session_usd).toBeGreaterThanOrEqual(
        res.body.agents[i - 1].cost_per_session_usd
      );
    }
  });

  // ── Order override ──────────────────────────────────────────────

  test("order=desc overrides default ascending for speed", async () => {
    const res = await request(app).get("/leaderboard?sort=speed&order=desc&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.order).toBe("desc");
    for (let i = 1; i < res.body.agents.length; i++) {
      expect(res.body.agents[i].avg_session_duration_ms).toBeLessThanOrEqual(
        res.body.agents[i - 1].avg_session_duration_ms
      );
    }
  });

  test("order=asc overrides default descending for volume", async () => {
    const res = await request(app).get("/leaderboard?sort=volume&order=asc&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.order).toBe("asc");
    for (let i = 1; i < res.body.agents.length; i++) {
      expect(res.body.agents[i].total_sessions).toBeGreaterThanOrEqual(
        res.body.agents[i - 1].total_sessions
      );
    }
  });

  test("invalid order returns 400", async () => {
    const res = await request(app).get("/leaderboard?order=sideways");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/order/i);
  });

  // ── Days filtering ──────────────────────────────────────────────

  test("days=1 filters to recent sessions only", async () => {
    const res = await request(app).get("/leaderboard?days=1&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.period_days).toBe(1);
    // Only sessions from last 24 hours qualify
  });

  test("days=365 includes all sessions", async () => {
    const res = await request(app).get("/leaderboard?days=365&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.period_days).toBe(365);
    expect(res.body.agents.length).toBeGreaterThanOrEqual(3);
  });

  test("days is clamped to valid range", async () => {
    const res = await request(app).get("/leaderboard?days=9999&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.period_days).toBeLessThanOrEqual(365);
  });

  // ── Response structure ──────────────────────────────────────────

  test("response includes all expected top-level fields", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("period_days");
    expect(res.body).toHaveProperty("sort");
    expect(res.body).toHaveProperty("order");
    expect(res.body).toHaveProperty("min_sessions");
    expect(res.body).toHaveProperty("total_qualifying_agents");
    expect(res.body).toHaveProperty("agents");
    expect(typeof res.body.total_qualifying_agents).toBe("number");
  });

  test("each agent entry has complete metrics", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    const agent = res.body.agents[0];
    const requiredFields = [
      "rank", "agent_name", "total_sessions", "completed", "errors",
      "success_rate", "error_rate", "total_tokens", "tokens_in", "tokens_out",
      "avg_tokens_per_session", "avg_session_duration_ms", "avg_event_duration_ms",
      "total_events", "tool_calls", "error_events", "efficiency_ratio",
      "tokens_per_ms", "total_cost_usd", "cost_per_session_usd",
      "first_seen", "last_seen"
    ];
    for (const field of requiredFields) {
      expect(agent).toHaveProperty(field);
    }
  });

  // ── Error/success rates ─────────────────────────────────────────

  test("agent with errors has correct error_rate", async () => {
    const res = await request(app).get("/leaderboard?sort=reliability&order=asc&min_sessions=1");
    expect(res.status).toBe(200);
    const beta = res.body.agents.find(a => a.agent_name === "lb-agent-beta");
    expect(beta).toBeDefined();
    // beta has 3 errors out of 10 sessions = 30%
    expect(beta.error_rate).toBe(30);
    expect(beta.success_rate).toBe(70);
  });

  test("agent with no errors has 100% success rate", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    const alpha = res.body.agents.find(a => a.agent_name === "lb-agent-alpha");
    expect(alpha).toBeDefined();
    expect(alpha.success_rate).toBe(100);
    expect(alpha.error_rate).toBe(0);
  });

  // ── Token calculations ──────────────────────────────────────────

  test("total_tokens equals tokens_in + tokens_out", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    for (const agent of res.body.agents) {
      expect(agent.total_tokens).toBe(agent.tokens_in + agent.tokens_out);
    }
  });

  test("efficiency_ratio is tokens_out / tokens_in", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    const alpha = res.body.agents.find(a => a.agent_name === "lb-agent-alpha");
    // alpha: 1000 in, 3000 out → ratio = 3.0
    expect(alpha.efficiency_ratio).toBe(3);
  });

  // ── Rank ordering ──────────────────────────────────────────────

  test("ranks are sequential starting from 1", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=1");
    for (let i = 0; i < res.body.agents.length; i++) {
      expect(res.body.agents[i].rank).toBe(i + 1);
    }
  });

  // ── limit clamping ─────────────────────────────────────────────

  test("limit is clamped to maximum 100", async () => {
    const res = await request(app).get("/leaderboard?limit=999&min_sessions=1");
    expect(res.status).toBe(200);
    // Should not error, just returns up to 100
    expect(res.body.agents.length).toBeLessThanOrEqual(100);
  });

  test("limit=0 is clamped to at least 1", async () => {
    const res = await request(app).get("/leaderboard?limit=0&min_sessions=1");
    expect(res.status).toBe(200);
    expect(res.body.agents.length).toBeGreaterThanOrEqual(1);
  });

  // ── total_qualifying_agents ────────────────────────────────────

  test("total_qualifying_agents reflects actual count before limit", async () => {
    const allRes = await request(app).get("/leaderboard?min_sessions=1");
    const limitedRes = await request(app).get("/leaderboard?limit=1&min_sessions=1");
    expect(limitedRes.body.total_qualifying_agents).toBe(allRes.body.total_qualifying_agents);
    expect(limitedRes.body.agents.length).toBe(1);
  });

  // ── Edge: no qualifying agents ─────────────────────────────────

  test("high min_sessions returns empty agents array", async () => {
    const res = await request(app).get("/leaderboard?min_sessions=9999");
    expect(res.status).toBe(200);
    expect(res.body.agents).toEqual([]);
  });
});
