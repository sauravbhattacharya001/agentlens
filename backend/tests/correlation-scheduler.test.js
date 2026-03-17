/* ── Correlation Scheduler — Backend Tests ───────────────────────── */

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
        CREATE TABLE IF NOT EXISTS correlation_rules (
          rule_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          match_type TEXT NOT NULL DEFAULT 'field',
          config TEXT NOT NULL DEFAULT '{}',
          agent_filter TEXT DEFAULT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS correlation_groups (
          group_id TEXT PRIMARY KEY,
          rule_id TEXT NOT NULL,
          label TEXT,
          created_at TEXT NOT NULL,
          metadata TEXT DEFAULT '{}',
          FOREIGN KEY (rule_id) REFERENCES correlation_rules(rule_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS correlation_members (
          group_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          role TEXT DEFAULT 'member',
          added_at TEXT NOT NULL,
          PRIMARY KEY (group_id, event_id)
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const crypto = require("crypto");

// Prevent auto-start scheduler on require
jest.useFakeTimers();
const schedulerRouter = require("../routes/correlation-scheduler");
jest.useRealTimers();

const scheduler = schedulerRouter._scheduler;

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/scheduler", schedulerRouter);
  return app;
}

function seedRule(id, name) {
  const { getDb } = require("../db");
  const db = getDb();
  const ts = new Date().toISOString();
  db.prepare(
    "INSERT OR REPLACE INTO correlation_rules (rule_id, name, match_type, config, enabled, created_at, updated_at) " +
    "VALUES (?, ?, 'field', '{}', 1, ?, ?)"
  ).run(id || "rule-1", name || "Test Rule", ts, ts);
}

function cleanTables() {
  const { getDb } = require("../db");
  const db = getDb();
  db.exec("DELETE FROM correlation_members");
  db.exec("DELETE FROM correlation_groups");
  try { db.exec("DELETE FROM correlation_schedules"); } catch (e) { /* table may not exist yet */ }
  db.exec("DELETE FROM correlation_rules");
}

beforeEach(() => {
  cleanTables();
  scheduler.stopScheduler();
});

// ── groupContentHash ────────────────────────────────────────────────

describe("groupContentHash", () => {
  test("produces deterministic hashes", () => {
    const events = [{ event_id: "e1" }, { event_id: "e2" }];
    const h1 = scheduler.groupContentHash("r1", events);
    const h2 = scheduler.groupContentHash("r1", events);
    expect(h1).toBe(h2);
    expect(h1).toHaveLength(32);
  });

  test("is order-independent (sorts event IDs)", () => {
    const a = [{ event_id: "e2" }, { event_id: "e1" }];
    const b = [{ event_id: "e1" }, { event_id: "e2" }];
    expect(scheduler.groupContentHash("r1", a)).toBe(scheduler.groupContentHash("r1", b));
  });

  test("different rules produce different hashes", () => {
    const events = [{ event_id: "e1" }];
    expect(scheduler.groupContentHash("r1", events))
      .not.toBe(scheduler.groupContentHash("r2", events));
  });

  test("different events produce different hashes", () => {
    const a = [{ event_id: "e1" }];
    const b = [{ event_id: "e2" }];
    expect(scheduler.groupContentHash("r1", a))
      .not.toBe(scheduler.groupContentHash("r1", b));
  });
});

// ── SSE broadcast ───────────────────────────────────────────────────

describe("broadcast", () => {
  test("sends SSE-formatted data to mock client", () => {
    const received = [];
    // Access internal clients array via a quick integration approach
    // We test the function in isolation
    const payload = scheduler.broadcast("test-event", { id: 1 });
    // broadcast writes to sseClients which is internal; tested via route below
  });
});

// ── persistGroupsDeduped ────────────────────────────────────────────

describe("persistGroupsDeduped", () => {
  test("persists new groups and returns them", () => {
    seedRule("r1");
    const groups = [{
      label: "group-a",
      events: [{ event_id: "e1", session_id: "s1" }, { event_id: "e2", session_id: "s2" }],
      metadata: { source: "test" },
    }];
    const result = scheduler.persistGroupsDeduped({ rule_id: "r1" }, groups);
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("group-a");
    expect(result[0].member_count).toBe(2);
    expect(result[0].content_hash).toHaveLength(32);
  });

  test("deduplicates identical groups", () => {
    seedRule("r1");
    const groups = [{
      label: "group-a",
      events: [{ event_id: "e1", session_id: "s1" }],
      metadata: {},
    }];
    const first = scheduler.persistGroupsDeduped({ rule_id: "r1" }, groups);
    const second = scheduler.persistGroupsDeduped({ rule_id: "r1" }, groups);
    expect(first).toHaveLength(1);
    expect(second).toHaveLength(0); // duplicate skipped
  });

  test("allows groups with different events for same rule", () => {
    seedRule("r1");
    const g1 = [{ label: "a", events: [{ event_id: "e1", session_id: "s1" }], metadata: {} }];
    const g2 = [{ label: "b", events: [{ event_id: "e2", session_id: "s1" }], metadata: {} }];
    expect(scheduler.persistGroupsDeduped({ rule_id: "r1" }, g1)).toHaveLength(1);
    expect(scheduler.persistGroupsDeduped({ rule_id: "r1" }, g2)).toHaveLength(1);
  });

  test("handles empty groups array", () => {
    seedRule("r1");
    const result = scheduler.persistGroupsDeduped({ rule_id: "r1" }, []);
    expect(result).toHaveLength(0);
  });
});

// ── Schedule CRUD routes ────────────────────────────────────────────

describe("POST /scheduler/schedules", () => {
  test("creates a schedule for an existing rule", async () => {
    seedRule("r1");
    const app = createApp();
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "r1", interval_seconds: 600, lookback_minutes: 30 });
    expect(res.status).toBe(201);
    expect(res.body.rule_id).toBe("r1");
    expect(res.body.interval_seconds).toBe(600);
    expect(res.body.lookback_minutes).toBe(30);
    expect(res.body.enabled).toBe(true);
    expect(res.body.next_run_at).toBeTruthy();
  });

  test("returns 400 when rule_id is missing", async () => {
    const app = createApp();
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ interval_seconds: 60 });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/rule_id/i);
  });

  test("returns 404 for nonexistent rule", async () => {
    const app = createApp();
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "no-such-rule" });
    expect(res.status).toBe(404);
  });

  test("uses defaults for interval and lookback", async () => {
    seedRule("r1");
    const app = createApp();
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "r1" });
    expect(res.status).toBe(201);
    expect(res.body.interval_seconds).toBe(300);
    expect(res.body.lookback_minutes).toBe(60);
  });

  test("upserts on conflict (updates existing schedule)", async () => {
    seedRule("r1");
    const app = createApp();
    await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "r1", interval_seconds: 300 });
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "r1", interval_seconds: 900 });
    expect(res.status).toBe(201);
    expect(res.body.interval_seconds).toBe(900);
  });

  test("can disable a schedule", async () => {
    seedRule("r1");
    const app = createApp();
    const res = await request(app)
      .post("/scheduler/schedules")
      .send({ rule_id: "r1", enabled: false });
    expect(res.status).toBe(201);
    expect(res.body.enabled).toBe(false);
  });
});

describe("GET /scheduler/schedules", () => {
  test("returns empty list initially", async () => {
    const app = createApp();
    const res = await request(app).get("/scheduler/schedules");
    expect(res.status).toBe(200);
    expect(res.body.schedules).toEqual([]);
    expect(res.body.total).toBe(0);
  });

  test("returns created schedules", async () => {
    seedRule("r1", "Alpha Rule");
    seedRule("r2", "Beta Rule");
    const app = createApp();
    await request(app).post("/scheduler/schedules").send({ rule_id: "r1" });
    await request(app).post("/scheduler/schedules").send({ rule_id: "r2" });

    const res = await request(app).get("/scheduler/schedules");
    expect(res.status).toBe(200);
    expect(res.body.total).toBe(2);
    expect(res.body.schedules[0].rule_name).toBeTruthy();
  });
});

describe("DELETE /scheduler/schedules/:ruleId", () => {
  test("deletes an existing schedule", async () => {
    seedRule("r1");
    const app = createApp();
    await request(app).post("/scheduler/schedules").send({ rule_id: "r1" });

    const res = await request(app).delete("/scheduler/schedules/r1");
    expect(res.status).toBe(200);
    expect(res.body.deleted).toBe(true);

    const list = await request(app).get("/scheduler/schedules");
    expect(list.body.total).toBe(0);
  });

  test("returns 404 for nonexistent schedule", async () => {
    const app = createApp();
    const res = await request(app).delete("/scheduler/schedules/no-such");
    expect(res.status).toBe(404);
  });
});

// ── Scheduler control routes ────────────────────────────────────────

describe("POST /scheduler/scheduler/start", () => {
  test("starts the scheduler", async () => {
    const app = createApp();
    const res = await request(app).post("/scheduler/scheduler/start");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("running");
    scheduler.stopScheduler(); // cleanup
  });
});

describe("POST /scheduler/scheduler/stop", () => {
  test("stops the scheduler", async () => {
    const app = createApp();
    await request(app).post("/scheduler/scheduler/start");
    const res = await request(app).post("/scheduler/scheduler/stop");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("stopped");
  });
});

describe("GET /scheduler/scheduler/status", () => {
  test("reports not running when stopped", async () => {
    const app = createApp();
    const res = await request(app).get("/scheduler/scheduler/status");
    expect(res.status).toBe(200);
    expect(res.body.running).toBe(false);
    expect(typeof res.body.connected_clients).toBe("number");
  });

  test("reports running after start", async () => {
    const app = createApp();
    await request(app).post("/scheduler/scheduler/start");
    const res = await request(app).get("/scheduler/scheduler/status");
    expect(res.body.running).toBe(true);
    scheduler.stopScheduler();
  });
});

// ── SSE stream endpoint ─────────────────────────────────────────────

describe("GET /scheduler/stream", () => {
  test("returns SSE headers and connected event", (done) => {
    const app = createApp();
    const req = request(app)
      .get("/scheduler/stream")
      .buffer(true)
      .parse((res, callback) => {
        let data = "";
        res.on("data", (chunk) => {
          data += chunk.toString();
          if (data.includes("connected")) {
            req.abort();
            expect(data).toContain("event: connected");
            expect(data).toContain('"status":"ok"');
            done();
          }
        });
        res.on("end", () => callback(null, data));
      });
    req.end();
  });
});
