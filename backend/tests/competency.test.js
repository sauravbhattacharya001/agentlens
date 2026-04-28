/* ── Competency Map Route Tests ── */

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
const competencyRouter = require("../routes/competency");
const { getDb } = require("../db");

const app = express();
app.use(express.json());
app.use("/competency", competencyRouter);

function seed() {
  const db = getDb();
  db.exec("DELETE FROM events");
  db.exec("DELETE FROM sessions");

  const now = new Date();
  const insertSession = db.prepare(
    "INSERT INTO sessions (session_id, agent_name, status, total_tokens_in, total_tokens_out, started_at) VALUES (?, ?, ?, ?, ?, ?)"
  );
  const insertEvent = db.prepare(
    "INSERT INTO events (event_id, session_id, event_type, model, tokens_in, tokens_out, tool_call, duration_ms, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
  );

  // Alpha: 15 sessions, 12 completed, 2 errors, 1 active — high reliability
  for (let i = 0; i < 15; i++) {
    const sid = `alpha-${i}`;
    const status = i < 12 ? "completed" : (i < 14 ? "error" : "active");
    const d = new Date(now - i * 86400000).toISOString();
    insertSession.run(sid, "alpha-agent", status, 500 + i * 10, 200 + i * 5, d);
    // LLM call event
    insertEvent.run(`ev-alpha-llm-${i}`, sid, "llm_call", "gpt-4", 500, 200, null, 300 + i * 20, d);
    // Tool call events — alpha uses many tools
    if (i < 10) {
      const tools = ["web_search", "file_read", "code_exec", "api_call"];
      insertEvent.run(`ev-alpha-tool-${i}`, sid, "tool_call", null, 0, 0, tools[i % tools.length], 150 + i * 10, d);
    }
  }

  // Beta: 5 sessions, all completed — high consistency, low volume
  for (let i = 0; i < 5; i++) {
    const sid = `beta-${i}`;
    const d = new Date(now - i * 86400000).toISOString();
    insertSession.run(sid, "beta-agent", "completed", 100, 50, d);
    insertEvent.run(`ev-beta-llm-${i}`, sid, "llm_call", "claude-3", 100, 50, null, 200, d);
    insertEvent.run(`ev-beta-tool-${i}`, sid, "tool_call", null, 0, 0, "web_search", 100, d);
  }

  // Gamma: 8 sessions, 4 completed, 3 errors, 1 active — poor reliability
  for (let i = 0; i < 8; i++) {
    const sid = `gamma-${i}`;
    const status = i < 4 ? "completed" : (i < 7 ? "error" : "active");
    const d = new Date(now - i * 86400000).toISOString();
    insertSession.run(sid, "gamma-agent", status, 800, 400, d);
    insertEvent.run(`ev-gamma-llm-${i}`, sid, "llm_call", "gpt-3.5", 800, 400, null, 1500 + i * 100, d);
    // Add error events to some completed sessions (for recovery testing)
    if (i < 2) {
      insertEvent.run(`ev-gamma-err-${i}`, sid, "error", null, 0, 0, null, 0, d);
    }
  }
}

beforeEach(() => {
  seed();
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

describe("GET /competency", () => {
  test("returns competency map with correct structure", async () => {
    const res = await request(app).get("/competency?days=30");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("competency_map");
    expect(res.body).toHaveProperty("routing_suggestions");
    expect(res.body).toHaveProperty("meta");
    expect(Array.isArray(res.body.competency_map)).toBe(true);
    expect(res.body.competency_map.length).toBe(3);
  });

  test("each agent has required fields", async () => {
    const res = await request(app).get("/competency?days=30");
    for (const agent of res.body.competency_map) {
      expect(agent).toHaveProperty("agent_name");
      expect(agent).toHaveProperty("competency_score");
      expect(agent).toHaveProperty("grade");
      expect(agent).toHaveProperty("grade_color");
      expect(agent).toHaveProperty("dimensions");
      expect(agent).toHaveProperty("strengths");
      expect(agent).toHaveProperty("weaknesses");
      expect(agent).toHaveProperty("recommended_tasks");
      expect(agent).toHaveProperty("session_count");
      expect(agent).toHaveProperty("last_active");
    }
  });

  test("dimension scores are 0-100", async () => {
    const res = await request(app).get("/competency?days=30");
    const dims = ["reliability", "speed", "efficiency", "tool_mastery", "error_recovery", "consistency"];
    for (const agent of res.body.competency_map) {
      for (const dim of dims) {
        expect(agent.dimensions[dim]).toBeDefined();
        expect(agent.dimensions[dim].score).toBeGreaterThanOrEqual(0);
        expect(agent.dimensions[dim].score).toBeLessThanOrEqual(100);
        expect(agent.dimensions[dim]).toHaveProperty("percentile");
      }
    }
  });

  test("strengths and weaknesses are valid dimension names", async () => {
    const dims = ["reliability", "speed", "efficiency", "tool_mastery", "error_recovery", "consistency"];
    const res = await request(app).get("/competency?days=30");
    for (const agent of res.body.competency_map) {
      expect(agent.strengths.length).toBe(2);
      expect(agent.weaknesses.length).toBe(2);
      for (const s of agent.strengths) expect(dims).toContain(s);
      for (const w of agent.weaknesses) expect(dims).toContain(w);
    }
  });

  test("agents are sorted by competency score descending", async () => {
    const res = await request(app).get("/competency?days=30");
    const scores = res.body.competency_map.map(a => a.competency_score);
    for (let i = 1; i < scores.length; i++) {
      expect(scores[i]).toBeLessThanOrEqual(scores[i - 1]);
    }
  });

  test("routing suggestions are generated", async () => {
    const res = await request(app).get("/competency?days=30");
    expect(Array.isArray(res.body.routing_suggestions)).toBe(true);
    for (const r of res.body.routing_suggestions) {
      expect(r).toHaveProperty("task_type");
      expect(r).toHaveProperty("best_agent");
      expect(r).toHaveProperty("confidence");
      expect(r).toHaveProperty("reason");
      expect(r).toHaveProperty("alternatives");
    }
  });

  test("returns empty map gracefully for no data", async () => {
    const db = getDb();
    db.exec("DELETE FROM events");
    db.exec("DELETE FROM sessions");
    const res = await request(app).get("/competency?days=30");
    expect(res.status).toBe(200);
    expect(res.body.competency_map).toEqual([]);
    expect(res.body.routing_suggestions).toEqual([]);
    expect(res.body.meta.agent_count).toBe(0);
  });

  test("alpha-agent has higher reliability than gamma-agent", async () => {
    const res = await request(app).get("/competency?days=30");
    const alpha = res.body.competency_map.find(a => a.agent_name === "alpha-agent");
    const gamma = res.body.competency_map.find(a => a.agent_name === "gamma-agent");
    expect(alpha.dimensions.reliability.score).toBeGreaterThan(gamma.dimensions.reliability.score);
  });

  test("meta includes days and agent_count", async () => {
    const res = await request(app).get("/competency?days=7");
    expect(res.body.meta.days).toBe(7);
    expect(res.body.meta).toHaveProperty("generated_at");
    expect(res.body.meta).toHaveProperty("agent_count");
  });
});

describe("GET /competency/:agent", () => {
  test("returns detailed profile for existing agent", async () => {
    const res = await request(app).get("/competency/alpha-agent?days=30");
    expect(res.status).toBe(200);
    expect(res.body.agent_name).toBe("alpha-agent");
    expect(res.body).toHaveProperty("competency_score");
    expect(res.body).toHaveProperty("grade");
    expect(res.body).toHaveProperty("dimensions");
    expect(res.body).toHaveProperty("strengths");
    expect(res.body).toHaveProperty("weaknesses");
    expect(res.body).toHaveProperty("recommended_tasks");
    expect(res.body).toHaveProperty("growth_trajectory");
    expect(res.body).toHaveProperty("tools");
    expect(res.body).toHaveProperty("model_affinity");
    expect(res.body).toHaveProperty("peer_comparison");
    expect(res.body).toHaveProperty("weekly_trend");
    expect(res.body).toHaveProperty("metrics");
  });

  test("returns 404 for unknown agent", async () => {
    const res = await request(app).get("/competency/nonexistent-agent?days=30");
    expect(res.status).toBe(404);
    expect(res.body).toHaveProperty("error");
  });

  test("growth trajectory has direction", async () => {
    const res = await request(app).get("/competency/alpha-agent?days=30");
    expect(["improving", "declining", "stable"]).toContain(res.body.growth_trajectory.direction);
    expect(res.body.growth_trajectory).toHaveProperty("success_rate_trend");
    expect(res.body.growth_trajectory).toHaveProperty("weeks_analyzed");
  });

  test("tools array has expected fields", async () => {
    const res = await request(app).get("/competency/alpha-agent?days=30");
    expect(res.body.tools.length).toBeGreaterThan(0);
    for (const t of res.body.tools) {
      expect(t).toHaveProperty("tool");
      expect(t).toHaveProperty("calls");
      expect(t).toHaveProperty("avg_latency_ms");
    }
  });

  test("model affinity has efficiency scores", async () => {
    const res = await request(app).get("/competency/alpha-agent?days=30");
    expect(res.body.model_affinity.length).toBeGreaterThan(0);
    for (const m of res.body.model_affinity) {
      expect(m).toHaveProperty("model");
      expect(m).toHaveProperty("efficiency_score");
      expect(m.efficiency_score).toBeGreaterThanOrEqual(0);
      expect(m.efficiency_score).toBeLessThanOrEqual(100);
    }
  });

  test("peer comparison has rank info", async () => {
    const res = await request(app).get("/competency/alpha-agent?days=30");
    const dims = ["reliability", "speed", "efficiency", "tool_mastery", "error_recovery", "consistency"];
    for (const dim of dims) {
      expect(res.body.peer_comparison[dim]).toHaveProperty("rank");
      expect(res.body.peer_comparison[dim]).toHaveProperty("of");
      expect(res.body.peer_comparison[dim].rank).toBeGreaterThanOrEqual(1);
    }
  });
});

describe("GET /competency/routing", () => {
  test("returns routing table", async () => {
    const res = await request(app).get("/competency/routing?days=30");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("routing_table");
    expect(res.body).toHaveProperty("coverage_score");
    expect(res.body).toHaveProperty("meta");
    expect(Array.isArray(res.body.routing_table)).toBe(true);
  });

  test("routing entries have required fields", async () => {
    const res = await request(app).get("/competency/routing?days=30");
    for (const rule of res.body.routing_table) {
      expect(rule).toHaveProperty("task_pattern");
      expect(rule).toHaveProperty("recommended_agent");
      expect(rule).toHaveProperty("confidence");
      expect(rule).toHaveProperty("fallback_agents");
      expect(rule).toHaveProperty("reason");
    }
  });

  test("coverage score is 0-100", async () => {
    const res = await request(app).get("/competency/routing?days=30");
    expect(res.body.coverage_score).toBeGreaterThanOrEqual(0);
    expect(res.body.coverage_score).toBeLessThanOrEqual(100);
  });

  test("returns empty routing gracefully for no data", async () => {
    const db = getDb();
    db.exec("DELETE FROM events");
    db.exec("DELETE FROM sessions");
    const res = await request(app).get("/competency/routing?days=30");
    expect(res.status).toBe(200);
    expect(res.body.routing_table).toEqual([]);
    expect(res.body.coverage_score).toBe(0);
  });
});
