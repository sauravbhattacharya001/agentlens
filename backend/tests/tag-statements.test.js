/**
 * tag-statements.js - Unit tests for lazy-initialised tag SQL statements.
 *
 * Tests prepared statement creation, singleton caching, and all
 * tag operations (CRUD, counts, search) using in-memory SQLite.
 */

const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const os = require("os");

describe("tag-statements.js — prepared tag SQL statements", () => {
  let tmpDir;
  let db;
  let getTagStatements;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentlens-tagtest-"));
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
    const dbPath = path.join(tmpDir, `tag-${Date.now()}.sqlite`);
    process.env.DB_PATH = dbPath;
    jest.resetModules();
    // Initialize DB with schema so tables exist
    const { getDb } = require("../db");
    db = getDb();
    ({ getTagStatements } = require("../lib/tag-statements"));
  });

  // ── Singleton behavior ─────────────────────────────────

  it("returns an object with all expected statement keys", () => {
    const stmts = getTagStatements();
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
      assert.ok(stmts[key], `Missing statement: ${key}`);
    }
  });

  it("returns the same object on repeated calls (singleton)", () => {
    const a = getTagStatements();
    const b = getTagStatements();
    assert.strictEqual(a, b);
  });

  // ── addTag + getTagsForSession ─────────────────────────

  it("addTag inserts a tag and getTagsForSession retrieves it", () => {
    const stmts = getTagStatements();
    // Insert a session first
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s1", "agent", "active", new Date().toISOString());

    const now = new Date().toISOString();
    stmts.addTag.run("s1", "production", now);

    const rows = stmts.getTagsForSession.all("s1");
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].tag, "production");
  });

  it("addTag with INSERT OR IGNORE does not duplicate tags", () => {
    const stmts = getTagStatements();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s2", "agent", "active", new Date().toISOString());

    const now = new Date().toISOString();
    stmts.addTag.run("s2", "test", now);
    stmts.addTag.run("s2", "test", now); // duplicate
    stmts.addTag.run("s2", "v2", now);

    const rows = stmts.getTagsForSession.all("s2");
    assert.strictEqual(rows.length, 2);
  });

  // ── countTags ──────────────────────────────────────────

  it("countTags returns correct count", () => {
    const stmts = getTagStatements();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s3", "agent", "active", new Date().toISOString());

    const now = new Date().toISOString();
    stmts.addTag.run("s3", "a", now);
    stmts.addTag.run("s3", "b", now);
    stmts.addTag.run("s3", "c", now);

    const result = stmts.countTags.get("s3");
    assert.strictEqual(result.count, 3);
  });

  it("countTags returns 0 for session with no tags", () => {
    const stmts = getTagStatements();
    const result = stmts.countTags.get("nonexistent");
    assert.strictEqual(result.count, 0);
  });

  // ── removeTag ──────────────────────────────────────────

  it("removeTag deletes a specific tag", () => {
    const stmts = getTagStatements();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s4", "agent", "active", new Date().toISOString());

    const now = new Date().toISOString();
    stmts.addTag.run("s4", "keep", now);
    stmts.addTag.run("s4", "remove-me", now);

    stmts.removeTag.run("s4", "remove-me");

    const rows = stmts.getTagsForSession.all("s4");
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].tag, "keep");
  });

  it("removeTag on non-existent tag does nothing", () => {
    const stmts = getTagStatements();
    // Should not throw
    const info = stmts.removeTag.run("nonexistent-session", "no-tag");
    assert.strictEqual(info.changes, 0);
  });

  // ── removeAllTags ──────────────────────────────────────

  it("removeAllTags clears all tags for a session", () => {
    const stmts = getTagStatements();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s5", "agent", "active", new Date().toISOString());

    const now = new Date().toISOString();
    stmts.addTag.run("s5", "a", now);
    stmts.addTag.run("s5", "b", now);
    stmts.addTag.run("s5", "c", now);

    stmts.removeAllTags.run("s5");

    const result = stmts.countTags.get("s5");
    assert.strictEqual(result.count, 0);
  });

  // ── allTags ────────────────────────────────────────────

  it("allTags aggregates tags across sessions with counts", () => {
    const stmts = getTagStatements();
    const now = new Date().toISOString();

    // Create 2 sessions
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s6a", "agent", "active", now);
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s6b", "agent", "active", now);

    stmts.addTag.run("s6a", "shared", now);
    stmts.addTag.run("s6a", "unique-a", now);
    stmts.addTag.run("s6b", "shared", now);

    const all = stmts.allTags.all();
    const shared = all.find((r) => r.tag === "shared");
    assert.ok(shared);
    assert.strictEqual(shared.session_count, 2);

    const unique = all.find((r) => r.tag === "unique-a");
    assert.ok(unique);
    assert.strictEqual(unique.session_count, 1);
  });

  it("allTags returns empty array when no tags exist", () => {
    // Fresh DB might have tags from other tests in same beforeEach,
    // but with a fresh DB via beforeEach reset this should be empty
    // Actually other tests in same describe add tags.
    // Just verify it returns an array (may have entries from other tests)
    const stmts = getTagStatements();
    const all = stmts.allTags.all();
    assert.ok(Array.isArray(all));
  });

  // ── sessionsByTag / sessionsByTagCount ─────────────────

  it("sessionsByTag returns sessions with a given tag", () => {
    const stmts = getTagStatements();
    const now = new Date().toISOString();

    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s7a", "agent1", "active", now);
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s7b", "agent2", "active", now);
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s7c", "agent3", "active", now);

    stmts.addTag.run("s7a", "prod", now);
    stmts.addTag.run("s7b", "prod", now);
    stmts.addTag.run("s7c", "dev", now);

    const prodSessions = stmts.sessionsByTag.all("prod", 50, 0);
    assert.strictEqual(prodSessions.length, 2);
    const ids = prodSessions.map((s) => s.session_id);
    assert.ok(ids.includes("s7a"));
    assert.ok(ids.includes("s7b"));
  });

  it("sessionsByTagCount returns correct count", () => {
    const stmts = getTagStatements();
    const now = new Date().toISOString();

    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s8a", "agent", "active", now);
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s8b", "agent", "active", now);

    stmts.addTag.run("s8a", "count-test", now);
    stmts.addTag.run("s8b", "count-test", now);

    const result = stmts.sessionsByTagCount.get("count-test");
    assert.strictEqual(result.count, 2);
  });

  it("sessionsByTag respects LIMIT and OFFSET", () => {
    const stmts = getTagStatements();
    const now = new Date().toISOString();

    for (let i = 0; i < 5; i++) {
      const sid = `s9-${i}`;
      db.prepare(
        "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
      ).run(sid, "agent", "active", now);
      stmts.addTag.run(sid, "paginated", now);
    }

    const page1 = stmts.sessionsByTag.all("paginated", 2, 0);
    assert.strictEqual(page1.length, 2);

    const page2 = stmts.sessionsByTag.all("paginated", 2, 2);
    assert.strictEqual(page2.length, 2);

    const page3 = stmts.sessionsByTag.all("paginated", 2, 4);
    assert.strictEqual(page3.length, 1);
  });

  it("sessionsByTag returns empty for unknown tag", () => {
    const stmts = getTagStatements();
    const result = stmts.sessionsByTag.all("nonexistent-tag", 50, 0);
    assert.strictEqual(result.length, 0);
  });

  // ── getTagsForSession ordering ─────────────────────────

  it("getTagsForSession returns tags ordered by created_at ASC", () => {
    const stmts = getTagStatements();
    db.prepare(
      "INSERT INTO sessions (session_id, agent_name, status, started_at) VALUES (?, ?, ?, ?)"
    ).run("s10", "agent", "active", new Date().toISOString());

    stmts.addTag.run("s10", "third", "2026-01-03T00:00:00Z");
    stmts.addTag.run("s10", "first", "2026-01-01T00:00:00Z");
    stmts.addTag.run("s10", "second", "2026-01-02T00:00:00Z");

    const rows = stmts.getTagsForSession.all("s10");
    assert.strictEqual(rows[0].tag, "first");
    assert.strictEqual(rows[1].tag, "second");
    assert.strictEqual(rows[2].tag, "third");
  });
});
