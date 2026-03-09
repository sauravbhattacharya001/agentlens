/**
 * tag-statements.js - Unit tests for lazy-initialised tag prepared statements.
 *
 * Validates that getTagStatements() returns working prepared statements
 * for tag CRUD operations on an in-memory SQLite database.
 */

const path = require("path");
const fs = require("fs");
const os = require("os");

describe("tag-statements", () => {
  let tmpDir;
  let db;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentlens-tagstmt-"));
    const dbPath = path.join(tmpDir, `tag-test-${Date.now()}.sqlite`);
    process.env.DB_PATH = dbPath;
    jest.resetModules();
    const { getDb } = require("../db");
    db = getDb();
  });

  afterAll(() => {
    jest.resetModules();
    try {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    } catch (_) {
      // Ignore EBUSY on Windows
    }
  });

  beforeEach(() => {
    // Seed a session for FK-free testing (session_tags references session_id)
    db.prepare(
      "INSERT OR IGNORE INTO sessions (session_id, started_at) VALUES (?, ?)"
    ).run("sess-1", new Date().toISOString());
    // Clean tags before each test
    db.prepare("DELETE FROM session_tags").run();
  });

  function getStmts() {
    jest.resetModules();
    process.env.DB_PATH = path.join(tmpDir, fs.readdirSync(tmpDir).find((f) => f.endsWith(".sqlite")));
    const { getTagStatements } = require("../lib/tag-statements");
    return getTagStatements();
  }

  test("getTagStatements returns object with all expected keys", () => {
    const stmts = getStmts();
    const expected = [
      "getTagsForSession",
      "addTag",
      "removeTag",
      "removeAllTags",
      "countTags",
      "sessionsByTag",
      "sessionsByTagCount",
      "allTags",
    ];
    for (const key of expected) {
      expect(stmts).toHaveProperty(key);
      expect(typeof stmts[key].run === "function" || typeof stmts[key].all === "function").toBe(true);
    }
  });

  test("addTag inserts a tag and getTagsForSession retrieves it", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "bug", now);

    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(1);
    expect(tags[0].tag).toBe("bug");
    expect(tags[0].created_at).toBe(now);
  });

  test("addTag is idempotent (INSERT OR IGNORE)", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "bug", now);
    stmts.addTag.run("sess-1", "bug", now);

    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(1);
  });

  test("countTags returns correct count", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "bug", now);
    stmts.addTag.run("sess-1", "feature", now);

    const result = stmts.countTags.get("sess-1");
    expect(result.count).toBe(2);
  });

  test("countTags returns 0 for session with no tags", () => {
    const stmts = getStmts();
    const result = stmts.countTags.get("sess-1");
    expect(result.count).toBe(0);
  });

  test("removeTag deletes a specific tag", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "bug", now);
    stmts.addTag.run("sess-1", "feature", now);

    stmts.removeTag.run("sess-1", "bug");

    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(1);
    expect(tags[0].tag).toBe("feature");
  });

  test("removeAllTags deletes all tags for a session", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "bug", now);
    stmts.addTag.run("sess-1", "feature", now);
    stmts.addTag.run("sess-1", "urgent", now);

    stmts.removeAllTags.run("sess-1");

    const result = stmts.countTags.get("sess-1");
    expect(result.count).toBe(0);
  });

  test("getTagsForSession returns tags in creation order", () => {
    const stmts = getStmts();
    stmts.addTag.run("sess-1", "zebra", "2026-01-01T00:00:00Z");
    stmts.addTag.run("sess-1", "alpha", "2026-01-02T00:00:00Z");
    stmts.addTag.run("sess-1", "middle", "2026-01-01T12:00:00Z");

    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags.map((t) => t.tag)).toEqual(["zebra", "middle", "alpha"]);
  });

  test("allTags aggregates tags across sessions", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();

    // Add a second session
    db.prepare(
      "INSERT OR IGNORE INTO sessions (session_id, started_at) VALUES (?, ?)"
    ).run("sess-2", now);

    stmts.addTag.run("sess-1", "bug", now);
    stmts.addTag.run("sess-2", "bug", now);
    stmts.addTag.run("sess-1", "feature", now);

    const all = stmts.allTags.all();
    expect(all.length).toBeGreaterThanOrEqual(2);

    const bugEntry = all.find((t) => t.tag === "bug");
    expect(bugEntry.session_count).toBe(2);

    const featureEntry = all.find((t) => t.tag === "feature");
    expect(featureEntry.session_count).toBe(1);
  });

  test("allTags orders by session_count DESC then tag ASC", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();

    db.prepare(
      "INSERT OR IGNORE INTO sessions (session_id, started_at) VALUES (?, ?)"
    ).run("sess-3", now);

    stmts.addTag.run("sess-1", "zzz", now);
    stmts.addTag.run("sess-1", "aaa", now);
    stmts.addTag.run("sess-3", "aaa", now);

    const all = stmts.allTags.all();
    // aaa has 2 sessions, zzz has 1 → aaa should come first
    const aIdx = all.findIndex((t) => t.tag === "aaa");
    const zIdx = all.findIndex((t) => t.tag === "zzz");
    expect(aIdx).toBeLessThan(zIdx);
  });

  test("sessionsByTag returns matching sessions with pagination", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "debug", now);

    const sessions = stmts.sessionsByTag.all("debug", 10, 0);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].session_id).toBe("sess-1");
  });

  test("sessionsByTag returns empty for non-existent tag", () => {
    const stmts = getStmts();
    const sessions = stmts.sessionsByTag.all("nonexistent", 10, 0);
    expect(sessions).toHaveLength(0);
  });

  test("sessionsByTagCount returns correct count", () => {
    const stmts = getStmts();
    const now = new Date().toISOString();
    stmts.addTag.run("sess-1", "perf", now);

    const result = stmts.sessionsByTagCount.get("perf");
    expect(result.count).toBe(1);
  });

  test("sessionsByTagCount returns 0 for non-existent tag", () => {
    const stmts = getStmts();
    const result = stmts.sessionsByTagCount.get("nonexistent");
    expect(result.count).toBe(0);
  });

  test("getTagStatements returns same instance on repeated calls (memoization)", () => {
    // Use a fresh require to test caching within a single module load
    jest.resetModules();
    process.env.DB_PATH = path.join(tmpDir, fs.readdirSync(tmpDir).find((f) => f.endsWith(".sqlite")));
    const { getTagStatements } = require("../lib/tag-statements");
    const a = getTagStatements();
    const b = getTagStatements();
    expect(a).toBe(b);
  });
});
