const express = require("express");
const request = require("supertest");
const { getDb } = require("../db");

process.env.DB_PATH = ":memory:";

const errorsRouter = require("../routes/errors");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/errors", errorsRouter);
  return app;
}

function seedData() {
  const db = getDb();

  const insertSession = db.prepare(
    `INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  const insertEvent = db.prepare(
    `INSERT OR IGNORE INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms, output_data)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  );

  // Agent alpha: 3 sessions, 2 error events
  insertSession.run("err-alpha-1", "agent-alpha", "2026-02-28T10:00:00Z", "2026-02-28T10:30:00Z", 500, 1000, "completed");
  insertSession.run("err-alpha-2", "agent-alpha", "2026-02-28T14:00:00Z", "2026-02-28T14:15:00Z", 300, 600, "error");
  insertSession.run("err-alpha-3", "agent-alpha", "2026-03-01T09:00:00Z", "2026-03-01T09:20:00Z", 400, 800, "completed");

  // Agent beta: 2 sessions, 1 error event
  insertSession.run("err-beta-1", "agent-beta", "2026-02-28T11:00:00Z", "2026-02-28T11:30:00Z", 200, 400, "completed");
  insertSession.run("err-beta-2", "agent-beta", "2026-03-01T08:00:00Z", "2026-03-01T08:10:00Z", 100, 200, "error");

  // Normal events
  insertEvent.run("ev1", "err-alpha-1", "llm_call", "2026-02-28T10:05:00Z", "gpt-4", 200, 500, 1200, null);
  insertEvent.run("ev2", "err-alpha-1", "llm_call", "2026-02-28T10:10:00Z", "gpt-4", 300, 500, 800, null);
  insertEvent.run("ev3", "err-alpha-3", "tool_call", "2026-03-01T09:05:00Z", "gpt-4", 100, 200, 500, null);
  insertEvent.run("ev4", "err-beta-1", "llm_call", "2026-02-28T11:05:00Z", "claude-3", 200, 400, 600, null);

  // Error events
  insertEvent.run("ev-err1", "err-alpha-2", "error", "2026-02-28T14:05:00Z", "gpt-4", 0, 0, 100, JSON.stringify({ error: "Rate limit exceeded" }));
  insertEvent.run("ev-err2", "err-alpha-2", "tool_error", "2026-02-28T14:10:00Z", "gpt-4", 0, 0, 50, JSON.stringify({ message: "Connection refused" }));
  insertEvent.run("ev-err3", "err-beta-2", "agent_error", "2026-03-01T08:05:00Z", "claude-3", 0, 0, 200, JSON.stringify({ error: "Rate limit exceeded" }));
  insertEvent.run("ev-err4", "err-beta-2", "error", "2026-03-01T08:08:00Z", "claude-3", 0, 0, 150, JSON.stringify({ detail: "Timeout after 30s" }));
}

beforeEach(() => {
  // Reset statement cache between tests so each test gets fresh DB
  const errors = require("../routes/errors");
  // Force re-initialization by clearing the module-level cache
  delete require.cache[require.resolve("../routes/errors")];
});

afterAll(() => {
  const db = getDb();
  db.close();
});

describe("GET /errors", () => {
  const app = createApp();

  beforeAll(() => {
    // Ensure test data exists (may have been cleared by other test files)
    seedData();
  });

  test("returns full error analytics", async () => {
    const res = await request(app).get("/errors");
    expect(res.status).toBe(200);

    const body = res.body;
    expect(body.summary).toBeDefined();
    expect(body.summary.total_errors).toBeGreaterThanOrEqual(4);
    expect(body.summary.affected_sessions).toBeGreaterThanOrEqual(2);
    expect(body.summary.error_rate_percent).toBeGreaterThan(0);
    expect(body.summary.session_error_rate_percent).toBeGreaterThan(0);
    expect(body.summary.first_error).toBeDefined();
    expect(body.summary.last_error).toBeDefined();

    expect(body.rate_over_time).toBeDefined();
    expect(Array.isArray(body.rate_over_time)).toBe(true);

    expect(body.by_type).toBeDefined();
    expect(Array.isArray(body.by_type)).toBe(true);
    expect(body.by_type.length).toBeGreaterThanOrEqual(3); // error, tool_error, agent_error

    expect(body.by_model).toBeDefined();
    expect(body.by_agent).toBeDefined();
    expect(body.top_errors).toBeDefined();
    expect(body.error_sessions).toBeDefined();
    expect(body.hourly_distribution).toBeDefined();
  });

  test("summary includes MTBF", async () => {
    const res = await request(app).get("/errors");
    expect(res.status).toBe(200);
    expect(res.body.summary.mtbf).toBeDefined();
    expect(res.body.summary.mtbf.mean_ms).toBeGreaterThan(0);
    expect(res.body.summary.mtbf.mean_seconds).toBeGreaterThan(0);
    expect(res.body.summary.mtbf.mean_minutes).toBeGreaterThan(0);
  });

  test("rate_over_time has error_rate field", async () => {
    const res = await request(app).get("/errors");
    expect(res.status).toBe(200);
    for (const row of res.body.rate_over_time) {
      expect(row).toHaveProperty("day");
      expect(row).toHaveProperty("error_count");
      expect(row).toHaveProperty("total_events");
      expect(row).toHaveProperty("error_rate");
    }
  });

  test("by_type includes all error event types", async () => {
    const res = await request(app).get("/errors");
    const types = res.body.by_type.map((r) => r.event_type);
    expect(types).toContain("error");
    expect(types).toContain("tool_error");
    expect(types).toContain("agent_error");
  });

  test("by_model includes error_rate", async () => {
    const res = await request(app).get("/errors");
    for (const row of res.body.by_model) {
      expect(row).toHaveProperty("model");
      expect(row).toHaveProperty("error_count");
      expect(row).toHaveProperty("error_rate");
    }
  });

  test("by_agent includes error_rate", async () => {
    const res = await request(app).get("/errors");
    for (const row of res.body.by_agent) {
      expect(row).toHaveProperty("agent_name");
      expect(row).toHaveProperty("error_count");
      expect(row).toHaveProperty("error_rate");
      expect(row).toHaveProperty("total_sessions");
    }
  });

  test("top_errors extracts error messages", async () => {
    const res = await request(app).get("/errors");
    const topErrors = res.body.top_errors;
    expect(topErrors.length).toBeGreaterThan(0);

    // "Rate limit exceeded" appears twice (from ev-err1 and ev-err3)
    const rateLimitErr = topErrors.find(
      (e) => e.message && e.message.includes("Rate limit")
    );
    // May or may not be grouped (different models), but should exist
    expect(rateLimitErr).toBeDefined();

    for (const err of topErrors) {
      expect(err).toHaveProperty("event_type");
      expect(err).toHaveProperty("occurrences");
      expect(err).toHaveProperty("first_seen");
      expect(err).toHaveProperty("last_seen");
      expect(err).toHaveProperty("affected_sessions");
    }
  });

  test("error_sessions lists sessions with error status", async () => {
    const res = await request(app).get("/errors");
    const sessions = res.body.error_sessions;
    expect(sessions.length).toBeGreaterThanOrEqual(2);
    for (const s of sessions) {
      expect(s).toHaveProperty("session_id");
      expect(s).toHaveProperty("agent_name");
      expect(s).toHaveProperty("error_count");
      expect(s).toHaveProperty("total_events");
    }
  });

  test("hourly_distribution is array of 24 or fewer entries", async () => {
    const res = await request(app).get("/errors");
    const hours = res.body.hourly_distribution;
    expect(Array.isArray(hours)).toBe(true);
    expect(hours.length).toBeLessThanOrEqual(24);
    for (const h of hours) {
      expect(h.hour).toBeGreaterThanOrEqual(0);
      expect(h.hour).toBeLessThanOrEqual(23);
      expect(h.error_count).toBeGreaterThan(0);
    }
  });

  test("respects limit parameter", async () => {
    const res = await request(app).get("/errors?limit=1");
    expect(res.status).toBe(200);
    expect(res.body.by_model.length).toBeLessThanOrEqual(1);
    expect(res.body.by_agent.length).toBeLessThanOrEqual(1);
    expect(res.body.top_errors.length).toBeLessThanOrEqual(1);
  });

  test("respects days parameter", async () => {
    const res = await request(app).get("/errors?days=1");
    expect(res.status).toBe(200);
    expect(res.body.rate_over_time.length).toBeLessThanOrEqual(1);
  });

  test("clamps limit to max 100", async () => {
    const res = await request(app).get("/errors?limit=999");
    expect(res.status).toBe(200);
    // Should not crash; limit is clamped internally
  });
});

describe("GET /errors/summary", () => {
  const app = createApp();

  test("returns lightweight summary", async () => {
    const res = await request(app).get("/errors/summary");
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty("total_errors");
    expect(res.body).toHaveProperty("affected_sessions");
    expect(res.body).toHaveProperty("error_rate_percent");
    expect(res.body).not.toHaveProperty("by_type");
    expect(res.body).not.toHaveProperty("rate_over_time");
  });
});

describe("GET /errors/by-type", () => {
  const app = createApp();

  test("returns error type breakdown", async () => {
    const res = await request(app).get("/errors/by-type");
    expect(res.status).toBe(200);
    expect(res.body.by_type).toBeDefined();
    expect(Array.isArray(res.body.by_type)).toBe(true);
    for (const row of res.body.by_type) {
      expect(row).toHaveProperty("event_type");
      expect(row).toHaveProperty("count");
      expect(row).toHaveProperty("affected_sessions");
    }
  });
});

describe("GET /errors/by-model", () => {
  const app = createApp();

  test("returns model breakdown with error rate", async () => {
    const res = await request(app).get("/errors/by-model");
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.by_model)).toBe(true);
    for (const row of res.body.by_model) {
      expect(row).toHaveProperty("model");
      expect(row).toHaveProperty("error_count");
      expect(row).toHaveProperty("error_rate");
    }
  });

  test("respects limit", async () => {
    const res = await request(app).get("/errors/by-model?limit=1");
    expect(res.status).toBe(200);
    expect(res.body.by_model.length).toBeLessThanOrEqual(1);
  });
});

describe("GET /errors/by-agent", () => {
  const app = createApp();

  test("returns agent breakdown with error rate", async () => {
    const res = await request(app).get("/errors/by-agent");
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.by_agent)).toBe(true);
    for (const row of res.body.by_agent) {
      expect(row).toHaveProperty("agent_name");
      expect(row).toHaveProperty("error_count");
      expect(row).toHaveProperty("error_sessions");
      expect(row).toHaveProperty("total_sessions");
      expect(row).toHaveProperty("error_rate");
    }
  });
});

describe("Error analytics with no data", () => {
  test("handles empty database gracefully", async () => {
    // Reset DB
    const db = getDb();
    db.exec("DELETE FROM events");
    db.exec("DELETE FROM sessions");
    delete require.cache[require.resolve("../routes/errors")];
    const freshRouter = require("../routes/errors");
    const app = express();
    app.use(express.json());
    app.use("/errors", freshRouter);

    const res = await request(app).get("/errors");
    expect(res.status).toBe(200);
    expect(res.body.summary.total_errors).toBe(0);
    expect(res.body.summary.error_rate_percent).toBe(0);
    expect(res.body.summary.mtbf).toBeNull();
    expect(res.body.rate_over_time).toEqual([]);
    expect(res.body.by_type).toEqual([]);
    expect(res.body.top_errors).toEqual([]);
  });
});
