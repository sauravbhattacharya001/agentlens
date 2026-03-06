const Database = require("better-sqlite3");

// Shared in-memory DB setup
function createTestDb() {
  const db = new Database(":memory:");
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
  return db;
}

describe("Bookmarks", () => {
  let db;

  beforeEach(() => {
    db = createTestDb();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
    ).run("sess-1", "test-agent", "2025-01-01T00:00:00Z");
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
    ).run("sess-2", "other-agent", "2025-01-02T00:00:00Z");
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

  test("can remove a bookmark", () => {
    db.prepare("INSERT INTO session_bookmarks (session_id) VALUES (?)").run(
      "sess-1"
    );
    db.prepare("DELETE FROM session_bookmarks WHERE session_id = ?").run(
      "sess-1"
    );

    const row = db
      .prepare("SELECT * FROM session_bookmarks WHERE session_id = ?")
      .get("sess-1");
    expect(row).toBeUndefined();
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

  test("upsert updates note on conflict", () => {
    db.prepare(
      "INSERT INTO session_bookmarks (session_id, note) VALUES (?, ?)"
    ).run("sess-1", "first note");

    db.prepare(
      `INSERT INTO session_bookmarks (session_id, note, created_at)
       VALUES (?, ?, datetime('now'))
       ON CONFLICT(session_id) DO UPDATE SET note = excluded.note`
    ).run("sess-1", "updated note");

    const row = db
      .prepare("SELECT * FROM session_bookmarks WHERE session_id = ?")
      .get("sess-1");
    expect(row.note).toBe("updated note");
  });

  test("list bookmarks with session join", () => {
    db.prepare("INSERT INTO session_bookmarks (session_id) VALUES (?)").run(
      "sess-1"
    );
    db.prepare("INSERT INTO session_bookmarks (session_id) VALUES (?)").run(
      "sess-2"
    );

    const rows = db
      .prepare(
        `SELECT b.session_id, b.note, s.agent_name
         FROM session_bookmarks b
         JOIN sessions s ON s.session_id = b.session_id
         ORDER BY b.created_at DESC`
      )
      .all();

    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.agent_name).sort()).toEqual([
      "other-agent",
      "test-agent",
    ]);
  });
});
