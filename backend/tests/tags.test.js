/* ── Session Tags — Backend Tests ────────────────────────────────────── */

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
        CREATE TABLE IF NOT EXISTS session_tags (
          session_id TEXT NOT NULL,
          tag TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (session_id, tag),
          FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_session_tags_session ON session_tags(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
        CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_name);
        CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
      `);
    }
    return mockDb;
  },
}));

jest.mock("../middleware", () => ({
  createHelmetMiddleware: () => (_req, _res, next) => next(),
  createCorsMiddleware: () => (_req, _res, next) => next(),
  createApiLimiter: () => (_req, _res, next) => next(),
  createIngestLimiter: () => (_req, _res, next) => next(),
  createApiKeyAuth: () => ({
    authenticateApiKey: (_req, _res, next) => next(),
    hasApiKey: false,
  }),
}));

const express = require("express");
const request = require("supertest");
const sessionsRouter = require("../routes/sessions");

let app;

function seedSessions() {
  const db = require("../db").getDb();
  // Clean up
  db.exec("DELETE FROM session_tags");
  db.exec("DELETE FROM events");
  db.exec("DELETE FROM sessions");

  // Insert test sessions
  const insert = db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status, total_tokens_in, total_tokens_out) VALUES (?, ?, ?, ?, ?, ?)"
  );
  insert.run("sess-1", "agent-a", "2026-02-22T10:00:00Z", "completed", 100, 50);
  insert.run("sess-2", "agent-a", "2026-02-22T11:00:00Z", "completed", 200, 100);
  insert.run("sess-3", "agent-b", "2026-02-22T12:00:00Z", "active", 50, 25);
}

beforeAll(() => {
  app = express();
  app.use(express.json());
  app.use("/sessions", sessionsRouter);
});

beforeEach(() => {
  seedSessions();
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

// ── Add Tags ────────────────────────────────────────────────────────

describe("POST /sessions/:id/tags", () => {
  test("should add tags to a session", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["production", "v2.1"] });

    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("sess-1");
    expect(res.body.added).toBe(2);
    expect(res.body.tags).toEqual(["production", "v2.1"]);
  });

  test("should not duplicate tags", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["production"] });

    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["production", "staging"] });

    expect(res.status).toBe(200);
    expect(res.body.added).toBe(1); // only "staging" is new
    expect(res.body.tags).toContain("production");
    expect(res.body.tags).toContain("staging");
  });

  test("should reject invalid session ID", async () => {
    const res = await request(app)
      .post("/sessions/invalid!id/tags")
      .send({ tags: ["test"] });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Invalid session ID/);
  });

  test("should reject non-existent session", async () => {
    const res = await request(app)
      .post("/sessions/nonexistent/tags")
      .send({ tags: ["test"] });

    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/not found/);
  });

  test("should reject empty tags array", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: [] });

    expect(res.status).toBe(400);
  });

  test("should reject invalid tag characters", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["valid", "<script>alert(1)</script>"] });

    // validateTags filters out invalid tags; if all are invalid, returns null
    // The valid one should still work if mixed
    // Actually validateTags dedupes and validates — let's test pure invalid
    const res2 = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["<invalid>"] });

    expect(res2.status).toBe(400);
  });

  test("should enforce tag limit per session", async () => {
    // Add 20 tags (the max)
    const tags = Array.from({ length: 20 }, (_, i) => `tag-${i}`);
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags });

    // Try to add one more
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["one-too-many"] });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/limit exceeded/i);
  });

  test("should trim and validate tag length", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["  padded  "] });

    expect(res.status).toBe(200);
    expect(res.body.tags).toContain("padded");
  });
});

// ── Get Tags ────────────────────────────────────────────────────────

describe("GET /sessions/:id/tags", () => {
  test("should return empty tags for untagged session", async () => {
    const res = await request(app).get("/sessions/sess-1/tags");

    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("sess-1");
    expect(res.body.tags).toEqual([]);
  });

  test("should return tags after adding", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["alpha", "beta"] });

    const res = await request(app).get("/sessions/sess-1/tags");

    expect(res.status).toBe(200);
    expect(res.body.tags).toEqual(["alpha", "beta"]);
  });

  test("should reject invalid session ID", async () => {
    const res = await request(app).get("/sessions/bad!id/tags");
    expect(res.status).toBe(400);
  });
});

// ── Remove Tags ─────────────────────────────────────────────────────

describe("DELETE /sessions/:id/tags", () => {
  test("should remove specific tags", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["a", "b", "c"] });

    const res = await request(app)
      .delete("/sessions/sess-1/tags")
      .send({ tags: ["b"] });

    expect(res.status).toBe(200);
    expect(res.body.removed).toBe(1);
    expect(res.body.tags).toEqual(["a", "c"]);
  });

  test("should remove all tags when no tags specified", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["x", "y", "z"] });

    const res = await request(app)
      .delete("/sessions/sess-1/tags")
      .send({});

    expect(res.status).toBe(200);
    expect(res.body.removed).toBe(3);
    expect(res.body.tags).toEqual([]);
  });

  test("should handle removing non-existent tag gracefully", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["exists"] });

    const res = await request(app)
      .delete("/sessions/sess-1/tags")
      .send({ tags: ["doesnt-exist"] });

    expect(res.status).toBe(200);
    expect(res.body.removed).toBe(0);
    expect(res.body.tags).toEqual(["exists"]);
  });
});

// ── List All Tags ───────────────────────────────────────────────────

describe("GET /sessions/tags", () => {
  test("should return empty list when no tags exist", async () => {
    const res = await request(app).get("/sessions/tags");

    expect(res.status).toBe(200);
    expect(res.body.tags).toEqual([]);
  });

  test("should return all tags with session counts", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["production", "v2"] });
    await request(app)
      .post("/sessions/sess-2/tags")
      .send({ tags: ["production", "staging"] });
    await request(app)
      .post("/sessions/sess-3/tags")
      .send({ tags: ["staging"] });

    const res = await request(app).get("/sessions/tags");

    expect(res.status).toBe(200);
    expect(res.body.tags).toHaveLength(3);

    // Ordered by session_count DESC
    const production = res.body.tags.find((t) => t.tag === "production");
    expect(production.session_count).toBe(2);

    const staging = res.body.tags.find((t) => t.tag === "staging");
    expect(staging.session_count).toBe(2);

    const v2 = res.body.tags.find((t) => t.tag === "v2");
    expect(v2.session_count).toBe(1);
  });
});

// ── Sessions By Tag ─────────────────────────────────────────────────

describe("GET /sessions/by-tag/:tag", () => {
  test("should return sessions with a specific tag", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["production"] });
    await request(app)
      .post("/sessions/sess-2/tags")
      .send({ tags: ["production"] });
    await request(app)
      .post("/sessions/sess-3/tags")
      .send({ tags: ["staging"] });

    const res = await request(app).get("/sessions/by-tag/production");

    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(2);
    expect(res.body.total).toBe(2);
    expect(res.body.tag).toBe("production");
    // Each session should have tags attached
    expect(res.body.sessions[0].tags).toContain("production");
  });

  test("should return empty for non-existent tag", async () => {
    const res = await request(app).get("/sessions/by-tag/nonexistent");

    expect(res.status).toBe(200);
    expect(res.body.sessions).toEqual([]);
    expect(res.body.total).toBe(0);
  });

  test("should support pagination", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["bulk"] });
    await request(app)
      .post("/sessions/sess-2/tags")
      .send({ tags: ["bulk"] });
    await request(app)
      .post("/sessions/sess-3/tags")
      .send({ tags: ["bulk"] });

    const res = await request(app)
      .get("/sessions/by-tag/bulk")
      .query({ limit: 2, offset: 0 });

    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(2);
    expect(res.body.total).toBe(3);

    const res2 = await request(app)
      .get("/sessions/by-tag/bulk")
      .query({ limit: 2, offset: 2 });

    expect(res2.body.sessions).toHaveLength(1);
  });

  test("should reject invalid tag", async () => {
    const res = await request(app).get("/sessions/by-tag/<script>");
    expect(res.status).toBe(400);
  });
});

// ── Tag Filtering on Session List ───────────────────────────────────

describe("GET /sessions?tag=...", () => {
  test("should filter sessions by tag query parameter", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["filtered"] });

    const res = await request(app)
      .get("/sessions")
      .query({ tag: "filtered" });

    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-1");
  });

  test("should return all sessions when no tag filter", async () => {
    const res = await request(app).get("/sessions");

    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(3);
  });
});

// ── Edge Cases ──────────────────────────────────────────────────────

describe("Tag edge cases", () => {
  test("should handle tags with allowed special characters", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["v1.2.3", "env:prod", "team/ml", "my tag"] });

    expect(res.status).toBe(200);
    expect(res.body.added).toBe(4);
    expect(res.body.tags).toContain("v1.2.3");
    expect(res.body.tags).toContain("env:prod");
    expect(res.body.tags).toContain("team/ml");
    expect(res.body.tags).toContain("my tag");
  });

  test("should deduplicate tags in single request", async () => {
    const res = await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["dup", "dup", "dup"] });

    expect(res.status).toBe(200);
    expect(res.body.added).toBe(1);
    expect(res.body.tags).toEqual(["dup"]);
  });

  test("tags should be independent per session", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["shared", "only-1"] });
    await request(app)
      .post("/sessions/sess-2/tags")
      .send({ tags: ["shared", "only-2"] });

    const tags1 = await request(app).get("/sessions/sess-1/tags");
    const tags2 = await request(app).get("/sessions/sess-2/tags");

    expect(tags1.body.tags).toContain("only-1");
    expect(tags1.body.tags).not.toContain("only-2");
    expect(tags2.body.tags).toContain("only-2");
    expect(tags2.body.tags).not.toContain("only-1");
  });

  test("removing tags from one session should not affect another", async () => {
    await request(app)
      .post("/sessions/sess-1/tags")
      .send({ tags: ["common"] });
    await request(app)
      .post("/sessions/sess-2/tags")
      .send({ tags: ["common"] });

    await request(app)
      .delete("/sessions/sess-1/tags")
      .send({ tags: ["common"] });

    const tags1 = await request(app).get("/sessions/sess-1/tags");
    const tags2 = await request(app).get("/sessions/sess-2/tags");

    expect(tags1.body.tags).not.toContain("common");
    expect(tags2.body.tags).toContain("common");
  });
});
