/**
 * db.js — Unit tests for database initialization, schema, and pragmas.
 *
 * Uses node:test + in-memory SQLite (via DB_PATH=:memory: on each init)
 * to test schema creation, index presence, pragma settings, and
 * getDb() singleton behavior.
 */

const { describe, it, before, after, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const os = require("os");

describe("db.js — schema and initialization", () => {
  let tmpDir;
  let dbPath;

  before(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentlens-dbtest-"));
  });

  after(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  /**
   * Helper: get a fresh db module by clearing the require cache and
   * pointing DB_PATH to a temp file.
   */
  function freshDb(suffix = "test") {
    dbPath = path.join(tmpDir, `db-${suffix}-${Date.now()}.sqlite`);
    process.env.DB_PATH = dbPath;
    // Clear cached module so getDb() reinitializes
    delete require.cache[require.resolve("../db")];
    const { getDb } = require("../db");
    return getDb();
  }

  // ── Table creation ──────────────────────────────────────────────

  it("creates sessions table", () => {
    const db = freshDb("sessions");
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").all();
    assert.equal(tables.length, 1);
    db.close();
  });

  it("creates events table", () => {
    const db = freshDb("events");
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").all();
    assert.equal(tables.length, 1);
    db.close();
  });

  it("creates model_pricing table", () => {
    const db = freshDb("pricing");
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='model_pricing'").all();
    assert.equal(tables.length, 1);
    db.close();
  });

  it("creates session_tags table", () => {
    const db = freshDb("tags");
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='session_tags'").all();
    assert.equal(tables.length, 1);
    db.close();
  });

  // ── Table schemas ─────────────────────────────────────────────

  it("sessions table has expected columns", () => {
    const db = freshDb("sess-cols");
    const info = db.prepare("PRAGMA table_info(sessions)").all();
    const cols = info.map(c => c.name);
    assert.ok(cols.includes("session_id"), "missing session_id");
    assert.ok(cols.includes("agent_name"), "missing agent_name");
    assert.ok(cols.includes("started_at"), "missing started_at");
    assert.ok(cols.includes("ended_at"), "missing ended_at");
    assert.ok(cols.includes("metadata"), "missing metadata");
    assert.ok(cols.includes("total_tokens_in"), "missing total_tokens_in");
    assert.ok(cols.includes("total_tokens_out"), "missing total_tokens_out");
    assert.ok(cols.includes("status"), "missing status");
    db.close();
  });

  it("events table has expected columns", () => {
    const db = freshDb("evt-cols");
    const info = db.prepare("PRAGMA table_info(events)").all();
    const cols = info.map(c => c.name);
    assert.ok(cols.includes("event_id"), "missing event_id");
    assert.ok(cols.includes("session_id"), "missing session_id");
    assert.ok(cols.includes("event_type"), "missing event_type");
    assert.ok(cols.includes("timestamp"), "missing timestamp");
    assert.ok(cols.includes("model"), "missing model");
    assert.ok(cols.includes("tokens_in"), "missing tokens_in");
    assert.ok(cols.includes("tokens_out"), "missing tokens_out");
    assert.ok(cols.includes("duration_ms"), "missing duration_ms");
    assert.ok(cols.includes("tool_call"), "missing tool_call");
    assert.ok(cols.includes("decision_trace"), "missing decision_trace");
    db.close();
  });

  it("model_pricing table has expected columns", () => {
    const db = freshDb("pr-cols");
    const info = db.prepare("PRAGMA table_info(model_pricing)").all();
    const cols = info.map(c => c.name);
    assert.ok(cols.includes("model"), "missing model");
    assert.ok(cols.includes("input_cost_per_1m"), "missing input_cost_per_1m");
    assert.ok(cols.includes("output_cost_per_1m"), "missing output_cost_per_1m");
    assert.ok(cols.includes("currency"), "missing currency");
    assert.ok(cols.includes("updated_at"), "missing updated_at");
    db.close();
  });

  it("session_tags table has expected columns", () => {
    const db = freshDb("tag-cols");
    const info = db.prepare("PRAGMA table_info(session_tags)").all();
    const cols = info.map(c => c.name);
    assert.ok(cols.includes("session_id"), "missing session_id");
    assert.ok(cols.includes("tag"), "missing tag");
    assert.ok(cols.includes("created_at"), "missing created_at");
    db.close();
  });

  // ── Primary keys ──────────────────────────────────────────────

  it("sessions primary key is session_id", () => {
    const db = freshDb("sess-pk");
    const info = db.prepare("PRAGMA table_info(sessions)").all();
    const pk = info.filter(c => c.pk > 0);
    assert.equal(pk.length, 1);
    assert.equal(pk[0].name, "session_id");
    db.close();
  });

  it("events primary key is event_id", () => {
    const db = freshDb("evt-pk");
    const info = db.prepare("PRAGMA table_info(events)").all();
    const pk = info.filter(c => c.pk > 0);
    assert.equal(pk.length, 1);
    assert.equal(pk[0].name, "event_id");
    db.close();
  });

  it("session_tags has composite primary key", () => {
    const db = freshDb("tag-pk");
    const info = db.prepare("PRAGMA table_info(session_tags)").all();
    const pk = info.filter(c => c.pk > 0).map(c => c.name).sort();
    assert.deepEqual(pk, ["session_id", "tag"]);
    db.close();
  });

  // ── Indexes ───────────────────────────────────────────────────

  it("creates expected indexes on events table", () => {
    const db = freshDb("evt-idx");
    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events' AND name NOT LIKE 'sqlite_%'"
    ).all().map(r => r.name).sort();

    const expected = [
      "idx_events_model",
      "idx_events_session",
      "idx_events_session_ts",
      "idx_events_timestamp",
      "idx_events_type",
    ];
    for (const idx of expected) {
      assert.ok(indexes.includes(idx), `missing index: ${idx}`);
    }
    db.close();
  });

  it("creates expected indexes on sessions table", () => {
    const db = freshDb("sess-idx");
    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions' AND name NOT LIKE 'sqlite_%'"
    ).all().map(r => r.name).sort();

    assert.ok(indexes.includes("idx_sessions_status"), "missing idx_sessions_status");
    assert.ok(indexes.includes("idx_sessions_agent"), "missing idx_sessions_agent");
    assert.ok(indexes.includes("idx_sessions_started"), "missing idx_sessions_started");
    db.close();
  });

  it("creates expected indexes on session_tags table", () => {
    const db = freshDb("tag-idx");
    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='session_tags' AND name NOT LIKE 'sqlite_%'"
    ).all().map(r => r.name).sort();

    assert.ok(indexes.includes("idx_session_tags_tag"), "missing idx_session_tags_tag");
    assert.ok(indexes.includes("idx_session_tags_session"), "missing idx_session_tags_session");
    db.close();
  });

  // ── Pragmas ───────────────────────────────────────────────────

  it("sets WAL journal mode", () => {
    const db = freshDb("wal");
    const mode = db.pragma("journal_mode", { simple: true });
    assert.equal(mode, "wal");
    db.close();
  });

  it("enables foreign keys", () => {
    const db = freshDb("fk");
    const fk = db.pragma("foreign_keys", { simple: true });
    assert.equal(fk, 1);
    db.close();
  });

  it("sets cache_size to at least 4000 pages", () => {
    const db = freshDb("cache");
    const cacheSize = db.pragma("cache_size", { simple: true });
    // Negative value = KiB; -8000 = 8 MB
    assert.ok(cacheSize <= -4000 || cacheSize >= 4000,
      `cache_size should be >= 4000 pages or <= -4000 KiB, got ${cacheSize}`);
    db.close();
  });

  it("sets temp_store to MEMORY", () => {
    const db = freshDb("temp");
    const ts = db.pragma("temp_store", { simple: true });
    assert.equal(ts, 2, "temp_store should be 2 (MEMORY)");
    db.close();
  });

  // ── Foreign keys ──────────────────────────────────────────────

  it("events.session_id references sessions.session_id", () => {
    const db = freshDb("fk-check");
    const fks = db.prepare("PRAGMA foreign_key_list(events)").all();
    const sessionFk = fks.find(f => f.from === "session_id");
    assert.ok(sessionFk, "no FK from events.session_id");
    assert.equal(sessionFk.table, "sessions");
    assert.equal(sessionFk.to, "session_id");
    db.close();
  });

  it("session_tags.session_id has ON DELETE CASCADE", () => {
    const db = freshDb("fk-cascade");
    const fks = db.prepare("PRAGMA foreign_key_list(session_tags)").all();
    const sessionFk = fks.find(f => f.from === "session_id");
    assert.ok(sessionFk, "no FK from session_tags.session_id");
    assert.equal(sessionFk.on_delete, "CASCADE");
    db.close();
  });

  // ── Default values ────────────────────────────────────────────

  it("sessions.status defaults to 'active'", () => {
    const db = freshDb("default-status");
    db.prepare(
      "INSERT INTO sessions (session_id, started_at) VALUES ('test-1', datetime('now'))"
    ).run();
    const row = db.prepare("SELECT status FROM sessions WHERE session_id = 'test-1'").get();
    assert.equal(row.status, "active");
    db.close();
  });

  it("sessions.agent_name defaults to 'default-agent'", () => {
    const db = freshDb("default-agent");
    db.prepare(
      "INSERT INTO sessions (session_id, started_at) VALUES ('test-2', datetime('now'))"
    ).run();
    const row = db.prepare("SELECT agent_name FROM sessions WHERE session_id = 'test-2'").get();
    assert.equal(row.agent_name, "default-agent");
    db.close();
  });

  it("events.tokens_in defaults to 0", () => {
    const db = freshDb("default-tokens");
    db.prepare(
      "INSERT INTO sessions (session_id, started_at) VALUES ('s1', datetime('now'))"
    ).run();
    db.prepare(
      "INSERT INTO events (event_id, session_id, timestamp) VALUES ('e1', 's1', datetime('now'))"
    ).run();
    const row = db.prepare("SELECT tokens_in, tokens_out FROM events WHERE event_id = 'e1'").get();
    assert.equal(row.tokens_in, 0);
    assert.equal(row.tokens_out, 0);
    db.close();
  });

  it("model_pricing.currency defaults to 'USD'", () => {
    const db = freshDb("default-currency");
    db.prepare(
      "INSERT INTO model_pricing (model, updated_at) VALUES ('test-model', datetime('now'))"
    ).run();
    const row = db.prepare("SELECT currency FROM model_pricing WHERE model = 'test-model'").get();
    assert.equal(row.currency, "USD");
    db.close();
  });

  // ── Idempotent re-init ────────────────────────────────────────

  it("schema creation is idempotent (IF NOT EXISTS)", () => {
    const db1 = freshDb("idempotent");
    // Insert a row
    db1.prepare(
      "INSERT INTO sessions (session_id, started_at) VALUES ('persist', datetime('now'))"
    ).run();
    db1.close();

    // Re-require with same DB_PATH — should NOT destroy existing data
    delete require.cache[require.resolve("../db")];
    const { getDb: getDb2 } = require("../db");
    const db2 = getDb2();
    const row = db2.prepare("SELECT session_id FROM sessions WHERE session_id = 'persist'").get();
    assert.ok(row, "existing data should survive schema re-init");
    db2.close();
  });

  // ── Singleton behavior ────────────────────────────────────────

  it("getDb() returns the same instance on repeated calls", () => {
    const suffix = "singleton-" + Date.now();
    dbPath = path.join(tmpDir, `db-${suffix}.sqlite`);
    process.env.DB_PATH = dbPath;
    delete require.cache[require.resolve("../db")];
    const { getDb } = require("../db");

    const a = getDb();
    const b = getDb();
    assert.equal(a, b, "getDb() should return the same DB instance");
    a.close();
  });
});
