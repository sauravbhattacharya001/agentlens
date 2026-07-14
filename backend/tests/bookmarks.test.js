const Database = require("better-sqlite3");

// ── Shared in-memory DB, injected via the mocked `../db` module ──────
let mockDb;
jest.mock("../db", () => ({
  getDb: () => mockDb,
}));

const express = require("express");
const request = require("supertest");
const bookmarksRouter = require("../routes/bookmarks");

function createSchema(db) {
  db.pragma("foreign_keys = ON");
  db.exec(`
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
    CREATE TABLE session_bookmarks (
      session_id TEXT PRIMARY KEY,
      note TEXT DEFAULT '',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );
  `);
}

function seedSessions(db) {
  db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
  ).run("sess-1", "test-agent", "2025-01-01T00:00:00Z");
  db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
  ).run("sess-2", "other-agent", "2025-01-02T00:00:00Z");
}

/*
 * DB-behavior tests: exercise the schema/constraints directly (no route).
 * These document the storage contract the route relies on.
 */
describe("Bookmarks (storage contract)", () => {
  let db;

  beforeEach(() => {
    db = new Database(":memory:");
    createSchema(db);
    seedSessions(db);
  });

  afterEach(() => {
    db.close();
  });

  test("can bookmark a session", () => {
    db.prepare(
      "INSERT INTO session_bookmarks (session_id, note) VALUES (?, ?)"
    ).run("sess-1", "important run");

    const row = db
      .prepare("SELECT * FROM session_bookmarks WHERE session_id = ?")
      .get("sess-1");
    expect(row).toBeTruthy();
    expect(row.note).toBe("important run");
  });

  test("bookmark is cascade-deleted when session is deleted", () => {
    db.prepare("INSERT INTO session_bookmarks (session_id) VALUES (?)").run(
      "sess-1"
    );
    db.prepare("DELETE FROM sessions WHERE session_id = ?").run("sess-1");

    const row = db
      .prepare("SELECT * FROM session_bookmarks WHERE session_id = ?")
      .get("sess-1");
    expect(row).toBeUndefined();
  });

  test("cannot bookmark non-existent session (FK constraint)", () => {
    expect(() => {
      db.prepare("INSERT INTO session_bookmarks (session_id) VALUES (?)").run(
        "no-such-session"
      );
    }).toThrow();
  });
});

/*
 * Route tests: mount the real `routes/bookmarks.js` behind supertest so the
 * HTTP surface (validation, 404, upsert, delete, list join) is exercised.
 */
describe("Bookmarks (HTTP route)", () => {
  let app;

  beforeAll(() => {
    app = express();
    app.use(express.json());
    app.use("/bookmarks", bookmarksRouter);
  });

  beforeEach(() => {
    if (mockDb) mockDb.close();
    mockDb = new Database(":memory:");
    createSchema(mockDb);
    seedSessions(mockDb);
  });

  afterAll(() => {
    if (mockDb) {
      mockDb.close();
      mockDb = null;
    }
  });

  test("GET /bookmarks returns empty list initially", async () => {
    const res = await request(app).get("/bookmarks");
    expect(res.status).toBe(200);
    expect(res.body.bookmarks).toEqual([]);
  });

  test("PUT creates a bookmark and GET reports it", async () => {
    const put = await request(app)
      .put("/bookmarks/sess-1")
      .send({ note: "important run" });
    expect(put.status).toBe(200);
    expect(put.body).toMatchObject({
      bookmarked: true,
      session_id: "sess-1",
      note: "important run",
    });

    const check = await request(app).get("/bookmarks/sess-1");
    expect(check.status).toBe(200);
    expect(check.body.bookmarked).toBe(true);
    expect(check.body.bookmark).toMatchObject({
      session_id: "sess-1",
      note: "important run",
    });
  });

  test("GET /bookmarks/:id reports false for an un-bookmarked session", async () => {
    const res = await request(app).get("/bookmarks/sess-2");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ bookmarked: false, bookmark: null });
  });

  test("PUT with a non-string note defaults to empty string", async () => {
    const res = await request(app)
      .put("/bookmarks/sess-1")
      .send({ note: 12345 });
    expect(res.status).toBe(200);
    expect(res.body.note).toBe("");
  });

  test("PUT truncates a note longer than 500 chars", async () => {
    const longNote = "x".repeat(600);
    const res = await request(app)
      .put("/bookmarks/sess-1")
      .send({ note: longNote });
    expect(res.status).toBe(200);
    expect(res.body.note).toHaveLength(500);
  });

  test("PUT upsert updates the note on conflict", async () => {
    await request(app).put("/bookmarks/sess-1").send({ note: "first" });
    const second = await request(app)
      .put("/bookmarks/sess-1")
      .send({ note: "second" });
    expect(second.body.note).toBe("second");

    const check = await request(app).get("/bookmarks/sess-1");
    expect(check.body.bookmark.note).toBe("second");
  });

  test("PUT returns 404 for an unknown session", async () => {
    const res = await request(app)
      .put("/bookmarks/nonexistent-session")
      .send({ note: "x" });
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Session not found");
  });

  test("DELETE removes an existing bookmark", async () => {
    await request(app).put("/bookmarks/sess-1").send({ note: "keep me" });
    const del = await request(app).delete("/bookmarks/sess-1");
    expect(del.status).toBe(200);
    expect(del.body).toEqual({ bookmarked: false, deleted: true });

    const check = await request(app).get("/bookmarks/sess-1");
    expect(check.body.bookmarked).toBe(false);
  });

  test("DELETE reports deleted:false when nothing was bookmarked", async () => {
    const del = await request(app).delete("/bookmarks/sess-2");
    expect(del.status).toBe(200);
    expect(del.body).toEqual({ bookmarked: false, deleted: false });
  });

  test("GET /bookmarks lists bookmarks joined with session metadata", async () => {
    await request(app).put("/bookmarks/sess-1").send({ note: "a" });
    await request(app).put("/bookmarks/sess-2").send({ note: "b" });

    const res = await request(app).get("/bookmarks");
    expect(res.status).toBe(200);
    expect(res.body.bookmarks).toHaveLength(2);
    expect(res.body.bookmarks.map((r) => r.agent_name).sort()).toEqual([
      "other-agent",
      "test-agent",
    ]);
    expect(res.body.bookmarks[0]).toHaveProperty("started_at");
    expect(res.body.bookmarks[0]).toHaveProperty("status");
  });

  test("rejects a structurally invalid session ID with 400", async () => {
    const res = await request(app).get("/bookmarks/bad%20id!");
    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid session ID format");
  });
});
