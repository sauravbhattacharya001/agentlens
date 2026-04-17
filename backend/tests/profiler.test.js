/* ── Profiler — Backend Tests ────────────────────────────────────────── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.pragma("journal_mode = WAL");
      mockDb.exec(`
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          agent_name TEXT NOT NULL DEFAULT 'default-agent',
          started_at TEXT NOT NULL,
          ended_at TEXT,
          metadata TEXT DEFAULT '{}',
          total_tokens_in INTEGER DEFAULT 0,
          total_tokens_out INTEGER DEFAULT 0,
          status TEXT DEFAULT 'active',
          created_at TEXT
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
const profilerRouter = require("../routes/profiler");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/profiler", profilerRouter);
  return app;
}

function seedProfilerData() {
  const db = require("../db").getDb();
  for (let i = 0; i < 20; i++) {
    const date = new Date(Date.now() - (29 - i) * 86400000);
    const sid = `prof-s-${i}`;
    db.prepare(
      "INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).run(sid, "alpha", date.toISOString(), new Date(date.getTime() + 60000).toISOString(), 500 + i * 10, 200 + i * 5, date.toISOString());

    const types = ["llm_call", "tool_call", "tool_call", "generic"];
    for (let j = 0; j < types.length; j++) {
      const toolCall = types[j] === "tool_call" ? JSON.stringify({ name: j === 1 ? "search" : "code_edit" }) : null;
      db.prepare(
        "INSERT INTO events (event_id, session_id, event_type, timestamp, tool_call) VALUES (?, ?, ?, ?, ?)"
      ).run(`prof-e-${i}-${j}`, sid, types[j], date.toISOString(), toolCall);
    }
  }
  for (let i = 0; i < 2; i++) {
    const date = new Date(Date.now() - i * 86400000);
    db.prepare(
      "INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, total_tokens_in, total_tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?)"
    ).run(`beta-s-${i}`, "beta", date.toISOString(), 100, 50, date.toISOString());
  }
}

describe("GET /profiler", () => {
  const app = createApp();
  beforeAll(() => seedProfilerData());

  test("returns profiles for all agents", async () => {
    const res = await request(app).get("/profiler").expect(200);
    expect(res.body.profiles).toBeDefined();
    expect(Array.isArray(res.body.profiles)).toBe(true);
    expect(res.body.meta.agentCount).toBeGreaterThanOrEqual(2);
  });

  test("alpha has drift dimensions", async () => {
    const res = await request(app).get("/profiler").expect(200);
    const alpha = res.body.profiles.find((p) => p.agent === "alpha");
    expect(alpha).toBeDefined();
    expect(alpha.dimensions).toBeDefined();
    expect(alpha.dimensions.eventMix).toBeDefined();
    expect(alpha.dimensions.toolUsage).toBeDefined();
    expect(alpha.overallDrift).toBeGreaterThanOrEqual(0);
  });

  test("beta shows building status (insufficient data)", async () => {
    const res = await request(app).get("/profiler").expect(200);
    const beta = res.body.profiles.find((p) => p.agent === "beta");
    expect(beta).toBeDefined();
    expect(beta.status).toBe("building");
  });

  test("profiles sorted by drift descending", async () => {
    const res = await request(app).get("/profiler").expect(200);
    const drifts = res.body.profiles.filter((p) => p.overallDrift != null).map((p) => p.overallDrift);
    for (let i = 1; i < drifts.length; i++) {
      expect(drifts[i]).toBeLessThanOrEqual(drifts[i - 1]);
    }
  });
});

describe("GET /profiler/:agent", () => {
  const app = createApp();

  test("returns detailed profile for alpha", async () => {
    const res = await request(app).get("/profiler/alpha").expect(200);
    expect(res.body.agent).toBe("alpha");
    expect(res.body.profile).toBeDefined();
    expect(res.body.profile.sessionCount).toBe(20);
    expect(res.body.profile.avgTokens).toBeGreaterThan(0);
    expect(res.body.profile.eventTypeDist).toBeDefined();
    expect(res.body.profile.toolCallDist).toBeDefined();
  });

  test("includes daily breakdown", async () => {
    const res = await request(app).get("/profiler/alpha").expect(200);
    expect(res.body.daily).toBeDefined();
    expect(Array.isArray(res.body.daily)).toBe(true);
    expect(res.body.daily.length).toBeGreaterThan(0);
    const day = res.body.daily[0];
    expect(day.date).toBeDefined();
    expect(day.sessionCount).toBeGreaterThanOrEqual(1);
  });

  test("returns 404 for unknown agent", async () => {
    const res = await request(app).get("/profiler/nonexistent").expect(404);
    expect(res.body.error).toBeTruthy();
  });

  test("rejects invalid agent name characters", async () => {
    const res = await request(app).get("/profiler/'; DROP TABLE sessions;--").expect(400);
    expect(res.body.error).toContain("Invalid agent name");
  });
});

describe("GET /profiler/:agent/drift", () => {
  const app = createApp();

  test("returns drift timeline for alpha", async () => {
    const res = await request(app).get("/profiler/alpha/drift").expect(200);
    expect(res.body.agent).toBe("alpha");
    expect(res.body.timeline).toBeDefined();
    expect(Array.isArray(res.body.timeline)).toBe(true);
    expect(res.body.baseline).toBeDefined();
  });

  test("timeline entries have drift scores and severity", async () => {
    const res = await request(app).get("/profiler/alpha/drift").expect(200);
    if (res.body.timeline.length > 0) {
      const entry = res.body.timeline[0];
      expect(entry.date).toBeDefined();
      expect(typeof entry.eventDrift).toBe("number");
      expect(typeof entry.toolDrift).toBe("number");
      expect(["stable", "medium", "high", "critical"]).toContain(entry.severity);
    }
  });

  test("returns insufficient data for beta", async () => {
    const res = await request(app).get("/profiler/beta/drift").expect(200);
    expect(res.body.timeline).toEqual([]);
    expect(res.body.message).toContain("Insufficient");
  });
});

describe("POST /profiler/snapshot", () => {
  const app = createApp();

  test("creates snapshots for all agents", async () => {
    const res = await request(app).post("/profiler/snapshot").expect(200);
    expect(res.body.snapshots).toBeDefined();
    expect(res.body.count).toBeGreaterThanOrEqual(1);
    const snap = res.body.snapshots[0];
    expect(snap.agent).toBeDefined();
    expect(snap.profile).toBeDefined();
    expect(snap.timestamp).toBeDefined();
  });
});
