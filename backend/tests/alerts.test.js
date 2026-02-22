/* ── Alert Rules — Backend Tests ──────────────────────────────────────── */

// Must require Database inside the mock factory (jest restriction)
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
const alertsRouter = require("../routes/alerts");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/alerts", alertsRouter);
  return app;
}

function seedTestData() {
  const { getDb } = require("../db");
  const db = getDb(); // ensures mockDb is initialized
  const now = new Date();
  const ago30 = new Date(now - 30 * 60000).toISOString();
  const ago10 = new Date(now - 10 * 60000).toISOString();

  db.exec("DELETE FROM events; DELETE FROM sessions;");
  db.exec("DROP TABLE IF EXISTS alert_events; DROP TABLE IF EXISTS alert_rules;");

  db.prepare("INSERT INTO sessions VALUES (?, ?, ?, ?, '{}', ?, ?, 'active')").run(
    "sess-1", "agent-alpha", ago30, null, 500, 200
  );
  db.prepare("INSERT INTO sessions VALUES (?, ?, ?, ?, '{}', ?, ?, 'active')").run(
    "sess-2", "agent-beta", ago10, null, 1000, 800
  );

  db.prepare("INSERT INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)").run(
    "evt-1", "sess-1", "llm", ago30, 300, 100, 150.5
  );
  db.prepare("INSERT INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)").run(
    "evt-2", "sess-1", "error", ago30, 0, 0, 10.0
  );
  db.prepare("INSERT INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)").run(
    "evt-3", "sess-2", "llm", ago10, 800, 600, 5000.0
  );
}

describe("Alert Rules API", () => {
  let app;

  beforeAll(() => {
    app = createApp();
  });

  beforeEach(() => {
    seedTestData();
  });

  afterAll(() => {
    const { getDb } = require("../db");
    try { getDb().close(); } catch (_) {}
  });

  // ── CRUD Tests ──────────────────────────────────────────────────────

  describe("POST /alerts/rules", () => {
    it("should create a new alert rule", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "High Token Usage",
        metric: "total_tokens",
        operator: ">",
        threshold: 1000,
        window_minutes: 60,
      });
      expect(res.status).toBe(201);
      expect(res.body.rule).toBeDefined();
      expect(res.body.rule.name).toBe("High Token Usage");
      expect(res.body.rule.metric).toBe("total_tokens");
      expect(res.body.rule.enabled).toBe(true);
    });

    it("should reject invalid metric", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "Bad Rule", metric: "nonexistent", operator: ">", threshold: 100,
      });
      expect(res.status).toBe(400);
    });

    it("should reject invalid operator", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "Bad Op", metric: "total_tokens", operator: "~", threshold: 100,
      });
      expect(res.status).toBe(400);
    });

    it("should reject missing name", async () => {
      const res = await request(app).post("/alerts/rules").send({
        metric: "total_tokens", operator: ">", threshold: 100,
      });
      expect(res.status).toBe(400);
    });

    it("should reject non-numeric threshold", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "Bad", metric: "total_tokens", operator: ">", threshold: "nope",
      });
      expect(res.status).toBe(400);
    });

    it("should create rule with agent_filter", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "Alpha Tokens", metric: "total_tokens", operator: ">", threshold: 500, agent_filter: "agent-alpha",
      });
      expect(res.status).toBe(201);
      expect(res.body.rule.agent_filter).toBe("agent-alpha");
    });

    it("should create rule with custom cooldown", async () => {
      const res = await request(app).post("/alerts/rules").send({
        name: "Slow", metric: "avg_duration_ms", operator: ">", threshold: 3000, cooldown_minutes: 30,
      });
      expect(res.status).toBe(201);
      expect(res.body.rule.cooldown_minutes).toBe(30);
    });
  });

  describe("GET /alerts/rules", () => {
    it("should list all rules", async () => {
      await request(app).post("/alerts/rules").send({ name: "R1", metric: "total_tokens", operator: ">", threshold: 100 });
      await request(app).post("/alerts/rules").send({ name: "R2", metric: "error_rate", operator: ">", threshold: 50 });
      const res = await request(app).get("/alerts/rules");
      expect(res.status).toBe(200);
      expect(res.body.rules.length).toBe(2);
    });

    it("should filter by enabled status", async () => {
      const c1 = await request(app).post("/alerts/rules").send({ name: "R1", metric: "total_tokens", operator: ">", threshold: 100 });
      await request(app).put(`/alerts/rules/${c1.body.rule.rule_id}`).send({ enabled: false });
      await request(app).post("/alerts/rules").send({ name: "R2", metric: "error_rate", operator: ">", threshold: 50 });

      const enabled = await request(app).get("/alerts/rules?enabled=true");
      expect(enabled.body.rules.length).toBe(1);
      expect(enabled.body.rules[0].name).toBe("R2");
    });
  });

  describe("PUT /alerts/rules/:ruleId", () => {
    it("should update rule fields", async () => {
      const c = await request(app).post("/alerts/rules").send({
        name: "Original", metric: "total_tokens", operator: ">", threshold: 100,
      });
      const res = await request(app).put(`/alerts/rules/${c.body.rule.rule_id}`).send({
        name: "Updated", threshold: 2000,
      });
      expect(res.status).toBe(200);
      expect(res.body.rule.name).toBe("Updated");
      expect(res.body.rule.threshold).toBe(2000);
    });

    it("should disable a rule", async () => {
      const c = await request(app).post("/alerts/rules").send({
        name: "ToDisable", metric: "total_tokens", operator: ">", threshold: 100,
      });
      const res = await request(app).put(`/alerts/rules/${c.body.rule.rule_id}`).send({ enabled: false });
      expect(res.body.rule.enabled).toBe(false);
    });

    it("should return 404 for non-existent rule", async () => {
      const res = await request(app).put("/alerts/rules/nonexistent").send({ name: "test" });
      expect(res.status).toBe(404);
    });
  });

  describe("DELETE /alerts/rules/:ruleId", () => {
    it("should delete a rule", async () => {
      const c = await request(app).post("/alerts/rules").send({
        name: "ToDelete", metric: "total_tokens", operator: ">", threshold: 100,
      });
      const res = await request(app).delete(`/alerts/rules/${c.body.rule.rule_id}`);
      expect(res.status).toBe(200);
      expect(res.body.deleted).toBe(true);
    });

    it("should return 404 for non-existent rule", async () => {
      const res = await request(app).delete("/alerts/rules/nonexistent");
      expect(res.status).toBe(404);
    });
  });

  // ── Evaluation Tests ────────────────────────────────────────────────

  describe("POST /alerts/evaluate", () => {
    it("should detect threshold breach", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Token Alert", metric: "total_tokens", operator: ">", threshold: 1000, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.fired).toBe(1);
      expect(res.body.results[0].triggered).toBe(true);
    });

    it("should not fire when threshold not breached", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Low", metric: "total_tokens", operator: ">", threshold: 99999, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.fired).toBe(0);
      expect(res.body.ok).toBe(1);
    });

    it("should respect cooldown period", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Cooldown", metric: "total_tokens", operator: ">", threshold: 100, window_minutes: 120, cooldown_minutes: 60,
      });
      const first = await request(app).post("/alerts/evaluate");
      expect(first.body.fired).toBe(1);
      const second = await request(app).post("/alerts/evaluate");
      expect(second.body.cooldown).toBe(1);
      expect(second.body.fired).toBe(0);
    });

    it("should skip disabled rules", async () => {
      const c = await request(app).post("/alerts/rules").send({
        name: "Disabled", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120,
      });
      await request(app).put(`/alerts/rules/${c.body.rule.rule_id}`).send({ enabled: false });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.evaluated).toBe(0);
    });

    it("should evaluate error_rate metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Errors", metric: "error_rate", operator: ">", threshold: 20, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
      expect(res.body.results[0].current_value).toBeCloseTo(33.33, 0);
    });

    it("should evaluate avg_duration_ms metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Slow", metric: "avg_duration_ms", operator: ">", threshold: 1000, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
    });

    it("should evaluate max_duration_ms metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Max", metric: "max_duration_ms", operator: ">", threshold: 4000, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].current_value).toBe(5000);
    });

    it("should evaluate session_count metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Sessions", metric: "session_count", operator: ">=", threshold: 2, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].current_value).toBe(2);
    });

    it("should evaluate event_count metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Events", metric: "event_count", operator: ">=", threshold: 3, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].current_value).toBe(3);
    });

    it("should evaluate token_rate metric", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Rate", metric: "token_rate", operator: ">", threshold: 10, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
    });

    it("should filter by agent", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Alpha", metric: "total_tokens", operator: ">", threshold: 600, window_minutes: 120, agent_filter: "agent-alpha",
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
      expect(res.body.results[0].current_value).toBe(700);
    });
  });

  // ── Alert Events ────────────────────────────────────────────────────

  describe("GET /alerts/events", () => {
    it("should list triggered alerts", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Fire", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120,
      });
      await request(app).post("/alerts/evaluate");
      const res = await request(app).get("/alerts/events");
      expect(res.body.events.length).toBe(1);
      expect(res.body.events[0].acknowledged).toBe(false);
    });

    it("should filter by acknowledged status", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Test", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120,
      });
      await request(app).post("/alerts/evaluate");
      const unacked = await request(app).get("/alerts/events?acknowledged=false");
      expect(unacked.body.events.length).toBe(1);
      const acked = await request(app).get("/alerts/events?acknowledged=true");
      expect(acked.body.events.length).toBe(0);
    });
  });

  describe("PUT /alerts/events/:alertId/acknowledge", () => {
    it("should acknowledge an alert", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Ack", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120,
      });
      const evalRes = await request(app).post("/alerts/evaluate");
      const alertId = evalRes.body.results[0].alert_id;
      const res = await request(app).put(`/alerts/events/${alertId}/acknowledge`);
      expect(res.body.acknowledged).toBe(true);
    });

    it("should return 404 for non-existent alert", async () => {
      const res = await request(app).put("/alerts/events/nonexistent/acknowledge");
      expect(res.status).toBe(404);
    });
  });

  // ── Metrics ─────────────────────────────────────────────────────────

  describe("GET /alerts/metrics", () => {
    it("should list available metrics", async () => {
      const res = await request(app).get("/alerts/metrics");
      expect(res.body.metrics.length).toBe(8);
      expect(res.body.operators.length).toBe(6);
    });
  });

  // ── Edge cases ──────────────────────────────────────────────────────

  describe("Edge cases", () => {
    it("should handle multiple rules simultaneously", async () => {
      await request(app).post("/alerts/rules").send({ name: "R1", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120 });
      await request(app).post("/alerts/rules").send({ name: "R2", metric: "error_rate", operator: ">", threshold: 1, window_minutes: 120 });
      await request(app).post("/alerts/rules").send({ name: "R3", metric: "session_count", operator: "<", threshold: 100, window_minutes: 120 });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.evaluated).toBe(3);
      expect(res.body.fired).toBe(3);
    });

    it("should handle == operator", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Exact", metric: "session_count", operator: "==", threshold: 2, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
    });

    it("should handle != operator", async () => {
      await request(app).post("/alerts/rules").send({
        name: "Not Zero", metric: "session_count", operator: "!=", threshold: 0, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].triggered).toBe(true);
    });

    it("should handle empty database", async () => {
      const { getDb } = require("../db");
      const db = getDb();
      db.exec("DELETE FROM events; DELETE FROM sessions;");
      await request(app).post("/alerts/rules").send({
        name: "Empty", metric: "total_tokens", operator: ">", threshold: 0, window_minutes: 120,
      });
      const res = await request(app).post("/alerts/evaluate");
      expect(res.body.results[0].current_value).toBe(0);
      expect(res.body.results[0].triggered).toBe(false);
    });
  });
});
