/* ── Command Center — Backend Tests ──────────────────────────────────── */

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
        CREATE TABLE IF NOT EXISTS alert_rules (
          rule_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          metric TEXT NOT NULL,
          operator TEXT NOT NULL,
          threshold REAL NOT NULL,
          window_minutes INTEGER DEFAULT 60,
          enabled INTEGER DEFAULT 1,
          created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_events (
          alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
          rule_id TEXT NOT NULL,
          triggered_at TEXT NOT NULL,
          metric_value REAL,
          details TEXT,
          acknowledged INTEGER DEFAULT 0,
          FOREIGN KEY (rule_id) REFERENCES alert_rules(rule_id)
        );
        CREATE TABLE IF NOT EXISTS budgets (
          budget_id INTEGER PRIMARY KEY AUTOINCREMENT,
          agent TEXT,
          spent REAL DEFAULT 0,
          limit_amount REAL DEFAULT 100,
          period TEXT DEFAULT 'monthly',
          updated_at TEXT
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const commandCenterRouter = require("../routes/command-center");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/command-center", commandCenterRouter);
  return app;
}

function seedData() {
  const db = require("../db").getDb();
  const now = new Date().toISOString();
  const recent = new Date(Date.now() - 2 * 86400000).toISOString();

  db.prepare(
    "INSERT OR IGNORE INTO alert_rules (rule_id, name, metric, operator, threshold) VALUES (?, ?, ?, ?, ?)"
  ).run("r1", "High Latency", "latency_ms", ">", 500);

  db.prepare(
    "INSERT INTO alert_events (rule_id, triggered_at, metric_value, details, acknowledged) VALUES (?, ?, ?, ?, ?)"
  ).run("r1", now, 750, '{"context":"test"}', 0);

  db.prepare(
    "INSERT INTO alert_events (rule_id, triggered_at, metric_value, details, acknowledged) VALUES (?, ?, ?, ?, ?)"
  ).run("r1", recent, 600, null, 1);

  db.prepare(
    "INSERT INTO budgets (agent, spent, limit_amount, updated_at) VALUES (?, ?, ?, ?)"
  ).run("gpt-4", 95, 100, now);

  db.prepare(
    "INSERT INTO budgets (agent, spent, limit_amount, updated_at) VALUES (?, ?, ?, ?)"
  ).run("claude", 110, 100, now);

  db.prepare(
    "INSERT INTO budgets (agent, spent, limit_amount, updated_at) VALUES (?, ?, ?, ?)"
  ).run("cheap", 10, 100, now);

  db.prepare(
    "INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
  ).run("s-err-1", "test-agent", now);

  db.prepare(
    "INSERT INTO events (event_id, session_id, event_type, timestamp, output_data) VALUES (?, ?, ?, ?, ?)"
  ).run("ev-err-1", "s-err-1", "error", now, '{"message":"timeout exceeded"}');
}

describe("GET /command-center/feed", () => {
  const app = createApp();
  beforeAll(() => seedData());

  test("returns aggregated feed with alerts, budgets, and health items", async () => {
    const res = await request(app).get("/command-center/feed").expect(200);
    expect(res.body.feed).toBeDefined();
    expect(Array.isArray(res.body.feed)).toBe(true);
    expect(res.body.total).toBeGreaterThan(0);
    const categories = new Set(res.body.feed.map((i) => i.category));
    expect(categories.has("alert")).toBe(true);
    expect(categories.has("budget")).toBe(true);
  });

  test("filters by category=alert", async () => {
    const res = await request(app).get("/command-center/feed?category=alert").expect(200);
    for (const item of res.body.feed) {
      expect(item.category).toBe("alert");
    }
    expect(res.body.filters.category).toBe("alert");
  });

  test("filters by severity=critical", async () => {
    const res = await request(app).get("/command-center/feed?severity=critical").expect(200);
    for (const item of res.body.feed) {
      expect(item.severity).toBe("critical");
    }
  });

  test("respects limit parameter", async () => {
    const res = await request(app).get("/command-center/feed?limit=1").expect(200);
    expect(res.body.feed.length).toBeLessThanOrEqual(1);
  });

  test("budget items exclude low-usage entries (<50%)", async () => {
    const res = await request(app).get("/command-center/feed?category=budget").expect(200);
    for (const item of res.body.feed) {
      expect(item.title).not.toContain("cheap");
    }
  });

  test("feed items are sorted by timestamp descending", async () => {
    const res = await request(app).get("/command-center/feed").expect(200);
    const timestamps = res.body.feed.map((i) => new Date(i.timestamp).getTime());
    for (let i = 1; i < timestamps.length; i++) {
      expect(timestamps[i]).toBeLessThanOrEqual(timestamps[i - 1]);
    }
  });

  test("alert items include rule metadata", async () => {
    const res = await request(app).get("/command-center/feed?category=alert").expect(200);
    const alert = res.body.feed[0];
    expect(alert.title).toBeTruthy();
    expect(alert.summary).toContain("latency_ms");
  });
});

describe("GET /command-center/summary", () => {
  const app = createApp();

  test("returns summary with all sections", async () => {
    const res = await request(app).get("/command-center/summary").expect(200);
    expect(res.body.summary).toBeDefined();
    expect(res.body.summary.alerts).toBeDefined();
    expect(res.body.summary.budgets).toBeDefined();
    expect(res.body.summary.errors).toBeDefined();
    expect(res.body.summary.sessions).toBeDefined();
  });

  test("summary counts alerts correctly", async () => {
    const res = await request(app).get("/command-center/summary").expect(200);
    expect(res.body.summary.alerts.total).toBeGreaterThanOrEqual(2);
    expect(res.body.summary.alerts.unacknowledged).toBeGreaterThanOrEqual(1);
  });

  test("summary counts budget overages", async () => {
    const res = await request(app).get("/command-center/summary").expect(200);
    expect(res.body.summary.budgets.over_limit).toBeGreaterThanOrEqual(1);
  });

  test("respects days parameter", async () => {
    const res = await request(app).get("/command-center/summary?days=1").expect(200);
    expect(res.body.days).toBe(1);
  });

  test("gracefully handles missing tables", async () => {
    const res = await request(app).get("/command-center/summary").expect(200);
    expect(res.body.summary).toBeDefined();
  });
});
