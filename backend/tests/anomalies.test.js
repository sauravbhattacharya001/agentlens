/* ── Anomaly Detector Route Tests ── */

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
const anomaliesRouter = require("../routes/anomalies");

let app;

function seedSessions(db) {
  const insert = db.prepare(
    `INSERT INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status) VALUES (?, ?, ?, ?, ?, ?, 'completed')`
  );
  const insertEvent = db.prepare(
    `INSERT INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?)`
  );

  // 10 normal sessions
  for (let i = 1; i <= 10; i++) {
    insert.run(`s${i}`, "agent-a", `2026-03-01T10:00:00Z`, `2026-03-01T10:05:00Z`, 100 + i, 50 + i);
    insertEvent.run(`e${i}`, `s${i}`, "llm_call", `2026-03-01T10:01:00Z`, 100 + i, 50 + i);
  }

  // 1 outlier session with massive tokens
  insert.run("s-outlier", "agent-a", "2026-03-01T10:00:00Z", "2026-03-01T10:05:00Z", 50000, 25000);
  insertEvent.run("e-out1", "s-outlier", "llm_call", "2026-03-01T10:01:00Z", 50000, 25000);

  // 1 outlier with many errors
  insert.run("s-errors", "agent-a", "2026-03-01T10:00:00Z", "2026-03-01T10:05:00Z", 100, 50);
  for (let i = 1; i <= 20; i++) {
    insertEvent.run(`e-err${i}`, "s-errors", "error", `2026-03-01T10:01:00Z`, 0, 0);
  }

  // Different agent sessions
  for (let i = 1; i <= 5; i++) {
    insert.run(`sb${i}`, "agent-b", `2026-03-01T10:00:00Z`, `2026-03-01T10:02:00Z`, 200 + i, 100 + i);
    insertEvent.run(`eb${i}`, `sb${i}`, "llm_call", `2026-03-01T10:01:00Z`, 200 + i, 100 + i);
  }
}

beforeAll(() => {
  app = express();
  app.use(express.json());
  app.use("/anomalies", anomaliesRouter);
  const db = require("../db").getDb();
  seedSessions(db);
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

// ── GET /anomalies ────────────────────────────────────────────

describe("GET /anomalies", () => {
  test("returns anomalies with default threshold", async () => {
    const res = await request(app).get("/anomalies");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("anomalies");
    expect(res.body).toHaveProperty("baselines");
    expect(Array.isArray(res.body.anomalies)).toBe(true);
  });

  test("token outlier is detected", async () => {
    const res = await request(app).get("/anomalies");
    const outlier = res.body.anomalies.find((a) => a.session_id === "s-outlier");
    expect(outlier).toBeDefined();
    expect(outlier.dimensions.totalTokens).toBeDefined();
    expect(outlier.dimensions.totalTokens.zScore).toBeGreaterThan(2);
  });

  test("error outlier is detected", async () => {
    const res = await request(app).get("/anomalies");
    const errSession = res.body.anomalies.find((a) => a.session_id === "s-errors");
    expect(errSession).toBeDefined();
    expect(errSession.dimensions.errorCount).toBeDefined();
  });

  test("normal sessions are not flagged", async () => {
    const res = await request(app).get("/anomalies");
    const normal = res.body.anomalies.find((a) => a.session_id === "s5");
    expect(normal).toBeUndefined();
  });

  test("respects threshold parameter", async () => {
    const highThreshold = await request(app).get("/anomalies?threshold=10");
    const lowThreshold = await request(app).get("/anomalies?threshold=0.5");
    expect(highThreshold.body.anomalies.length).toBeLessThanOrEqual(lowThreshold.body.anomalies.length);
  });

  test("filters by agent name", async () => {
    const res = await request(app).get("/anomalies?agent=agent-b");
    expect(res.body.baselines).toBeDefined();
    // agent-b has 5 sessions with similar tokens, no obvious outliers
    for (const a of res.body.anomalies) {
      expect(a.agent_name).toBe("agent-b");
    }
  });

  test("respects limit parameter", async () => {
    const res = await request(app).get("/anomalies?limit=1&threshold=0.1");
    expect(res.body.anomalies.length).toBeLessThanOrEqual(1);
  });

  test("anomalies are sorted by maxZScore descending", async () => {
    const res = await request(app).get("/anomalies");
    const scores = res.body.anomalies.map((a) => a.maxZScore);
    for (let i = 1; i < scores.length; i++) {
      expect(scores[i]).toBeLessThanOrEqual(scores[i - 1]);
    }
  });

  test("severity is classified correctly", async () => {
    const res = await request(app).get("/anomalies");
    for (const a of res.body.anomalies) {
      expect(["low", "medium", "high", "critical"]).toContain(a.severity);
      if (a.maxZScore >= 4) expect(a.severity).toBe("critical");
      else if (a.maxZScore >= 3) expect(a.severity).toBe("high");
      else if (a.maxZScore >= 2) expect(a.severity).toBe("medium");
    }
  });
});

// ── GET /anomalies/stats ──────────────────────────────────────

describe("GET /anomalies/stats", () => {
  test("returns baseline statistics", async () => {
    const res = await request(app).get("/anomalies/stats");
    expect(res.status).toBe(200);
    expect(res.body.baselines).toBeDefined();
    expect(res.body.baselines.totalTokens).toHaveProperty("mean");
    expect(res.body.baselines.totalTokens).toHaveProperty("stddev");
    expect(res.body.baselines.duration_ms).toHaveProperty("mean");
    expect(res.body.baselines.eventCount).toHaveProperty("mean");
    expect(res.body.baselines.errorCount).toHaveProperty("mean");
    expect(res.body.baselines.sampleSize).toBeGreaterThan(0);
  });

  test("filters stats by agent", async () => {
    const res = await request(app).get("/anomalies/stats?agent=agent-b");
    expect(res.body.baselines.sampleSize).toBe(5);
  });
});

// ── GET /anomalies/session/:id ────────────────────────────────

describe("GET /anomalies/session/:id", () => {
  test("returns anomaly report for a specific session", async () => {
    const res = await request(app).get("/anomalies/session/s-outlier");
    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("s-outlier");
    expect(res.body.isAnomaly).toBe(true);
    expect(res.body.dimensions).toBeDefined();
    expect(res.body.dimensions.totalTokens.zScore).toBeGreaterThan(2);
  });

  test("normal session shows isAnomaly false", async () => {
    const res = await request(app).get("/anomalies/session/s5");
    expect(res.status).toBe(200);
    expect(res.body.isAnomaly).toBe(false);
  });

  test("returns 404 for unknown session", async () => {
    const res = await request(app).get("/anomalies/session/nonexistent");
    expect(res.status).toBe(404);
  });

  test("all dimensions have zScore", async () => {
    const res = await request(app).get("/anomalies/session/s1");
    const dims = res.body.dimensions;
    for (const key of ["totalTokens", "duration_ms", "eventCount", "errorCount"]) {
      expect(dims[key]).toHaveProperty("value");
      expect(dims[key]).toHaveProperty("zScore");
      expect(typeof dims[key].zScore).toBe("number");
    }
  });

  test("includes baselines in response", async () => {
    const res = await request(app).get("/anomalies/session/s1");
    expect(res.body.baselines).toBeDefined();
    expect(res.body.baselines.sampleSize).toBeGreaterThan(0);
  });
});

// ── POST /anomalies/scan ──────────────────────────────────────

describe("POST /anomalies/scan", () => {
  test("triggers full scan and returns results", async () => {
    const res = await request(app).post("/anomalies/scan").send({});
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("anomalies");
    expect(res.body).toHaveProperty("scannedAt");
  });

  test("accepts threshold in body", async () => {
    const res = await request(app).post("/anomalies/scan").send({ threshold: 5 });
    expect(res.status).toBe(200);
    expect(res.body.anomalies.length).toBeLessThanOrEqual(
      (await request(app).post("/anomalies/scan").send({ threshold: 1 })).body.anomalies.length
    );
  });

  test("accepts agent filter in body", async () => {
    const res = await request(app).post("/anomalies/scan").send({ agent: "agent-b" });
    expect(res.status).toBe(200);
    for (const a of res.body.anomalies) {
      expect(a.agent_name).toBe("agent-b");
    }
  });

  test("accepts limit in body", async () => {
    const res = await request(app).post("/anomalies/scan").send({ limit: 1, threshold: 0.1 });
    expect(res.body.anomalies.length).toBeLessThanOrEqual(1);
  });

  test("scannedAt is a valid ISO timestamp", async () => {
    const res = await request(app).post("/anomalies/scan").send({});
    expect(new Date(res.body.scannedAt).toISOString()).toBe(res.body.scannedAt);
  });
});

// ── Edge cases ────────────────────────────────────────────────

describe("Edge cases", () => {
  test("agent with no sessions returns insufficient data", async () => {
    const res = await request(app).get("/anomalies?agent=nonexistent-agent");
    expect(res.status).toBe(200);
    expect(res.body.anomalies).toEqual([]);
    expect(res.body.message).toContain("Insufficient");
  });

  test("baselines for nonexistent agent returns null", async () => {
    const res = await request(app).get("/anomalies/stats?agent=nonexistent-agent");
    expect(res.status).toBe(200);
    expect(res.body.baselines).toBeNull();
  });

  test("total count is returned", async () => {
    const res = await request(app).get("/anomalies?threshold=0.1");
    expect(typeof res.body.total).toBe("number");
    expect(res.body.total).toBeGreaterThanOrEqual(res.body.anomalies.length);
  });
});
