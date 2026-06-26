/**
 * @jest-environment node
 *
 * Tests for the Data Retention & Cleanup routes.
 */

const Database = require("better-sqlite3");
const express = require("express");
const request = require("supertest");

// ── Shared in-memory DB for tests ──────────────────────────────────

let mockDb;

jest.mock("../db", () => ({
  getDb: () => mockDb,
}));

const retentionRouter = require("../routes/retention");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/retention", retentionRouter);
  return app;
}

function seedSession(id, startedAt, status = "completed", tokens = 100) {
  mockDb.prepare(`
    INSERT OR REPLACE INTO sessions (session_id, agent_name, started_at, status, total_tokens_in, total_tokens_out)
    VALUES (?, 'test-agent', ?, ?, ?, ?)
  `).run(id, startedAt, status, tokens, tokens);
}

function seedEvent(sessionId, eventId, timestamp) {
  mockDb.prepare(`
    INSERT OR REPLACE INTO events (event_id, session_id, event_type, timestamp, tokens_in, tokens_out)
    VALUES (?, ?, 'generic', ?, 10, 10)
  `).run(eventId, sessionId, timestamp);
}

function seedTag(sessionId, tag) {
  mockDb.prepare(`
    INSERT OR REPLACE INTO session_tags (session_id, tag) VALUES (?, ?)
  `).run(sessionId, tag);
}

function daysAgo(days) {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

// ── Setup / Teardown ────────────────────────────────────────────────

beforeEach(() => {
  mockDb = new Database(":memory:");
  mockDb.pragma("journal_mode = WAL");
  mockDb.pragma("foreign_keys = ON");

  // Core schema
  mockDb.exec(`
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY,
      agent_name TEXT NOT NULL DEFAULT 'default-agent',
      started_at TEXT NOT NULL,
      ended_at TEXT,
      metadata TEXT DEFAULT '{}',
      total_tokens_in INTEGER DEFAULT 0,
      total_tokens_out INTEGER DEFAULT 0,
      status TEXT DEFAULT 'active'
    );

    CREATE TABLE events (
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

    CREATE TABLE session_tags (
      session_id TEXT NOT NULL,
      tag TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (session_id, tag),
      FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );
  `);
});

afterEach(() => {
  if (mockDb) mockDb.close();
});

// ── Tests ───────────────────────────────────────────────────────────

describe("GET /retention/config", () => {
  test("returns default config when no custom settings exist", async () => {
    const app = createApp();
    const res = await request(app).get("/retention/config");
    expect(res.status).toBe(200);
    expect(res.body.config).toBeDefined();
    expect(res.body.config.max_age_days).toBe(90);
    expect(res.body.config.max_sessions).toBe(0);
    expect(res.body.config.exempt_tags).toEqual([]);
    expect(res.body.config.auto_purge).toBe(false);
  });

  test("returns saved config after update", async () => {
    const app = createApp();
    await request(app).put("/retention/config").send({ max_age_days: 30 });
    const res = await request(app).get("/retention/config");
    expect(res.status).toBe(200);
    expect(res.body.config.max_age_days).toBe(30);
  });
});

describe("PUT /retention/config", () => {
  test("updates max_age_days", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_age_days: 60 });
    expect(res.status).toBe(200);
    expect(res.body.config.max_age_days).toBe(60);
    expect(res.body.updated).toBe(1);
  });

  test("updates max_sessions", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_sessions: 1000 });
    expect(res.status).toBe(200);
    expect(res.body.config.max_sessions).toBe(1000);
  });

  test("updates exempt_tags", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ exempt_tags: ["production", "important"] });
    expect(res.status).toBe(200);
    expect(res.body.config.exempt_tags).toEqual(["production", "important"]);
  });

  test("updates auto_purge", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ auto_purge: true });
    expect(res.status).toBe(200);
    expect(res.body.config.auto_purge).toBe(true);
  });

  test("updates multiple fields at once", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({
      max_age_days: 14,
      max_sessions: 500,
      exempt_tags: ["keep"],
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(3);
    expect(res.body.config.max_age_days).toBe(14);
    expect(res.body.config.max_sessions).toBe(500);
    expect(res.body.config.exempt_tags).toEqual(["keep"]);
  });

  test("rejects negative max_age_days", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_age_days: -1 });
    expect(res.status).toBe(400);
  });

  test("rejects max_age_days over 3650", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_age_days: 5000 });
    expect(res.status).toBe(400);
  });

  test("rejects negative max_sessions", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_sessions: -5 });
    expect(res.status).toBe(400);
  });

  test("rejects non-array exempt_tags", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ exempt_tags: "not-array" });
    expect(res.status).toBe(400);
  });

  test("rejects too many exempt_tags", async () => {
    const app = createApp();
    const tags = Array.from({ length: 51 }, (_, i) => `tag-${i}`);
    const res = await request(app).put("/retention/config").send({ exempt_tags: tags });
    expect(res.status).toBe(400);
  });

  test("rejects empty string exempt tag", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ exempt_tags: ["valid", ""] });
    expect(res.status).toBe(400);
  });

  test("rejects request with no valid fields", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ unknown_field: 42 });
    expect(res.status).toBe(400);
  });

  test("rejects non-object body", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send("not json");
    expect(res.status).toBe(400);
  });

  test("accepts max_age_days of 0 (disables age-based)", async () => {
    const app = createApp();
    const res = await request(app).put("/retention/config").send({ max_age_days: 0 });
    expect(res.status).toBe(200);
    expect(res.body.config.max_age_days).toBe(0);
  });
});

describe("GET /retention/stats", () => {
  test("returns stats for empty database", async () => {
    const app = createApp();
    const res = await request(app).get("/retention/stats");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toBe(0);
    expect(res.body.events).toBe(0);
    expect(res.body.avg_events_per_session).toBe(0);
    expect(res.body.age_breakdown).toBeDefined();
    expect(res.body.status_breakdown).toBeDefined();
    expect(res.body.eligible_for_purge).toBe(0);
    expect(res.body.config).toBeDefined();
  });

  test("returns accurate counts", async () => {
    seedSession("s1", daysAgo(1));
    seedSession("s2", daysAgo(5));
    seedEvent("s1", "e1", daysAgo(1));
    seedEvent("s1", "e2", daysAgo(1));
    seedEvent("s2", "e3", daysAgo(5));

    const app = createApp();
    const res = await request(app).get("/retention/stats");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toBe(2);
    expect(res.body.events).toBe(3);
    expect(res.body.avg_events_per_session).toBe(1.5);
  });

  test("includes age breakdown", async () => {
    seedSession("s-today", daysAgo(0));
    seedSession("s-week", daysAgo(3));
    seedSession("s-month", daysAgo(15));
    seedSession("s-old", daysAgo(100));

    const app = createApp();
    const res = await request(app).get("/retention/stats");
    expect(res.body.age_breakdown.last_24h).toBe(1);
    expect(res.body.age_breakdown.last_7d).toBe(1);
    expect(res.body.age_breakdown.last_30d).toBe(1);
    expect(res.body.age_breakdown.older).toBe(1);
  });

  test("includes status breakdown", async () => {
    seedSession("s1", daysAgo(1), "completed");
    seedSession("s2", daysAgo(1), "active");
    seedSession("s3", daysAgo(1), "completed");

    const app = createApp();
    const res = await request(app).get("/retention/stats");
    expect(res.body.status_breakdown.completed).toBe(2);
    expect(res.body.status_breakdown.active).toBe(1);
  });

  test("shows eligible_for_purge count", async () => {
    seedSession("s-old", daysAgo(100));
    seedSession("s-new", daysAgo(1));

    const app = createApp();
    const res = await request(app).get("/retention/stats");
    // Default max_age_days=90, so s-old should be eligible
    expect(res.body.eligible_for_purge).toBe(1);
  });
});

describe("POST /retention/purge", () => {
  test("returns zero when nothing to purge", async () => {
    seedSession("s1", daysAgo(1));

    const app = createApp();
    const res = await request(app).post("/retention/purge").send({});
    expect(res.status).toBe(200);
    expect(res.body.purged_sessions).toBe(0);
    expect(res.body.message).toContain("No sessions eligible");
  });

  test("dry run shows what would be purged without deleting", async () => {
    seedSession("s-old", daysAgo(100));
    seedEvent("s-old", "e1", daysAgo(100));
    seedEvent("s-old", "e2", daysAgo(100));
    seedSession("s-new", daysAgo(1));

    const app = createApp();
    const res = await request(app).post("/retention/purge?dry_run=true").send({});
    expect(res.status).toBe(200);
    expect(res.body.dry_run).toBe(true);
    expect(res.body.would_purge_sessions).toBe(1);
    expect(res.body.would_purge_events).toBe(2);
    expect(res.body.details).toHaveLength(1);
    expect(res.body.details[0].session_id).toBe("s-old");

    // Verify nothing actually deleted
    const count = mockDb.prepare("SELECT COUNT(*) AS c FROM sessions").get().c;
    expect(count).toBe(2);
  });

  test("actually purges sessions and events", async () => {
    seedSession("s-old", daysAgo(100));
    seedEvent("s-old", "e1", daysAgo(100));
    seedEvent("s-old", "e2", daysAgo(100));
    seedSession("s-new", daysAgo(1));
    seedEvent("s-new", "e3", daysAgo(1));

    const app = createApp();
    const res = await request(app).post("/retention/purge").send({});
    expect(res.status).toBe(200);
    expect(res.body.dry_run).toBe(false);
    expect(res.body.purged_sessions).toBe(1);
    expect(res.body.purged_events).toBe(2);

    // Verify old session is gone
    const sessions = mockDb.prepare("SELECT session_id FROM sessions").all();
    expect(sessions).toHaveLength(1);
    expect(sessions[0].session_id).toBe("s-new");

    // Verify events are gone
    const events = mockDb.prepare("SELECT event_id FROM events").all();
    expect(events).toHaveLength(1);
    expect(events[0].event_id).toBe("e3");
  });

  test("purges tags along with sessions", async () => {
    seedSession("s-old", daysAgo(100));
    seedTag("s-old", "test-tag");

    const app = createApp();
    await request(app).post("/retention/purge").send({});

    const tags = mockDb.prepare("SELECT * FROM session_tags WHERE session_id = ?").all("s-old");
    expect(tags).toHaveLength(0);
  });

  test("respects exempt_tags — does not purge protected sessions", async () => {
    seedSession("s-old-protected", daysAgo(100));
    seedTag("s-old-protected", "production");
    seedSession("s-old-unprotected", daysAgo(100));
    seedSession("s-new", daysAgo(1));

    const app = createApp();
    // Set production as exempt
    await request(app).put("/retention/config").send({ exempt_tags: ["production"] });

    const res = await request(app).post("/retention/purge").send({});
    expect(res.status).toBe(200);
    expect(res.body.purged_sessions).toBe(1);

    // Protected session survives
    const remaining = mockDb.prepare("SELECT session_id FROM sessions ORDER BY session_id").all();
    expect(remaining).toHaveLength(2);
    expect(remaining.map(r => r.session_id)).toContain("s-old-protected");
    expect(remaining.map(r => r.session_id)).toContain("s-new");
  });

  test("max_sessions purge removes oldest excess", async () => {
    seedSession("s1", daysAgo(5));
    seedSession("s2", daysAgo(3));
    seedSession("s3", daysAgo(1));
    seedEvent("s1", "e1", daysAgo(5));

    const app = createApp();
    // Set max_sessions=2 and disable age-based
    await request(app).put("/retention/config").send({ max_sessions: 2, max_age_days: 0 });

    const res = await request(app).post("/retention/purge").send({});
    expect(res.status).toBe(200);
    expect(res.body.purged_sessions).toBe(1);
    expect(res.body.details[0].session_id).toBe("s1");
    expect(res.body.details[0].reason).toBe("count");

    const remaining = mockDb.prepare("SELECT session_id FROM sessions ORDER BY started_at").all();
    expect(remaining).toHaveLength(2);
    expect(remaining[0].session_id).toBe("s2");
    expect(remaining[1].session_id).toBe("s3");
  });

  test("dry_run via body parameter works", async () => {
    seedSession("s-old", daysAgo(100));

    const app = createApp();
    const res = await request(app).post("/retention/purge").send({ dry_run: true });
    expect(res.status).toBe(200);
    expect(res.body.dry_run).toBe(true);
    expect(res.body.would_purge_sessions).toBe(1);

    // Not deleted
    const count = mockDb.prepare("SELECT COUNT(*) AS c FROM sessions").get().c;
    expect(count).toBe(1);
  });

  test("purges multiple old sessions at once", async () => {
    seedSession("s-old-1", daysAgo(120));
    seedSession("s-old-2", daysAgo(100));
    seedSession("s-old-3", daysAgo(95));
    seedSession("s-new", daysAgo(1));

    const app = createApp();
    const res = await request(app).post("/retention/purge").send({});
    expect(res.status).toBe(200);
    expect(res.body.purged_sessions).toBe(3);

    const remaining = mockDb.prepare("SELECT session_id FROM sessions").all();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].session_id).toBe("s-new");
  });
});
