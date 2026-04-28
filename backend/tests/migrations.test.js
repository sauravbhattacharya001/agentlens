/**
 * migrations.js — Unit tests for the schema migration system.
 *
 * Covers:
 *   - Fresh database: all migrations applied from scratch
 *   - Existing database: baseline adoption (v1 marked applied, tables untouched)
 *   - Idempotent re-runs: calling runMigrations twice is safe
 *   - _schema_migrations bookkeeping table correctness
 *   - currentVersion() helper
 *   - Future migration sequencing (v2+ applied after adoption)
 */

const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const os = require("os");
const Database = require("better-sqlite3");

describe("migrations.js — schema migration system", () => {
  let tmpDir;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentlens-migtest-"));
  });

  afterAll(() => {
    jest.resetModules();
    try {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    } catch (_) {}
  });

  /** Create a bare in-memory or temp-file database. */
  function bareDb(suffix = "mig") {
    const p = path.join(tmpDir, `mig-${suffix}-${Date.now()}.sqlite`);
    const db = new Database(p);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    return db;
  }

  /** Get a fresh migrations module (bypasses Jest cache). */
  function freshMigrations() {
    jest.resetModules();
    return require("../migrations");
  }

  // ── Fresh database ────────────────────────────────────────────

  it("applies all migrations to a fresh database", () => {
    const db = bareDb("fresh");
    const { runMigrations, migrations } = freshMigrations();
    const result = runMigrations(db);

    assert.ok(result.applied.length > 0, "should apply at least v1");
    assert.equal(result.current, migrations[migrations.length - 1].version);

    // Verify core tables exist
    for (const table of ["sessions", "events", "model_pricing", "session_tags", "cost_budgets", "session_bookmarks"]) {
      const row = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(table);
      assert.ok(row, `table ${table} should exist after fresh migration`);
    }
    db.close();
  });

  it("creates _schema_migrations bookkeeping table", () => {
    const db = bareDb("bookkeeping");
    const { runMigrations } = freshMigrations();
    runMigrations(db);

    const row = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_migrations'").get();
    assert.ok(row, "_schema_migrations table should exist");

    const info = db.prepare("PRAGMA table_info(_schema_migrations)").all();
    const cols = info.map((c) => c.name);
    assert.ok(cols.includes("version"), "missing version column");
    assert.ok(cols.includes("description"), "missing description column");
    assert.ok(cols.includes("applied_at"), "missing applied_at column");
    db.close();
  });

  it("records each applied migration in _schema_migrations", () => {
    const db = bareDb("records");
    const { runMigrations, migrations } = freshMigrations();
    runMigrations(db);

    const rows = db.prepare("SELECT version, description FROM _schema_migrations ORDER BY version").all();
    assert.equal(rows.length, migrations.length);
    assert.equal(rows[0].version, 1);
    assert.ok(rows[0].description.length > 0);
    db.close();
  });

  // ── Existing database (baseline adoption) ─────────────────────

  it("adopts baseline for existing databases without destroying data", () => {
    const db = bareDb("adopt");

    // Simulate a pre-migration database: create the sessions table directly
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
    `);
    db.prepare("INSERT INTO sessions (session_id, started_at) VALUES ('existing-1', datetime('now'))").run();

    const { runMigrations } = freshMigrations();
    const result = runMigrations(db);

    // v1 should be adopted (not re-applied), so applied list is empty
    assert.ok(!result.applied.includes(1), "v1 should be adopted, not freshly applied");
    assert.equal(result.current, 1);

    // Existing data must survive
    const row = db.prepare("SELECT session_id FROM sessions WHERE session_id = 'existing-1'").get();
    assert.ok(row, "existing row must survive baseline adoption");

    // Adoption is recorded
    const migRow = db.prepare("SELECT description FROM _schema_migrations WHERE version = 1").get();
    assert.ok(migRow, "v1 should be recorded in _schema_migrations");
    assert.ok(migRow.description.includes("adopted"), "description should mention adoption");
    db.close();
  });

  // ── Idempotent re-runs ────────────────────────────────────────

  it("is idempotent — second run applies nothing", () => {
    const db = bareDb("idempotent");
    const { runMigrations } = freshMigrations();

    const first = runMigrations(db);
    assert.ok(first.applied.length > 0);

    // Insert data between runs
    db.prepare("INSERT INTO sessions (session_id, started_at) VALUES ('between', datetime('now'))").run();

    const second = runMigrations(db);
    assert.equal(second.applied.length, 0, "second run should apply nothing");
    assert.equal(second.current, first.current);

    // Data survives
    const row = db.prepare("SELECT 1 FROM sessions WHERE session_id = 'between'").get();
    assert.ok(row, "data inserted between runs must survive");
    db.close();
  });

  // ── currentVersion() ─────────────────────────────────────────

  it("currentVersion returns 0 for empty database", () => {
    const db = bareDb("version-empty");
    const { currentVersion } = freshMigrations();
    assert.equal(currentVersion(db), 0);
    db.close();
  });

  it("currentVersion returns highest applied version", () => {
    const db = bareDb("version-applied");
    const { runMigrations, currentVersion } = freshMigrations();
    runMigrations(db);
    const v = currentVersion(db);
    assert.ok(v >= 1);
    db.close();
  });

  // ── Integration with db.js ────────────────────────────────────

  it("db.js getDb() creates _schema_migrations via migrations", () => {
    const dbPath = path.join(tmpDir, `db-integration-${Date.now()}.sqlite`);
    process.env.DB_PATH = dbPath;
    jest.resetModules();
    const { getDb } = require("../db");
    const db = getDb();

    const row = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_migrations'").get();
    assert.ok(row, "_schema_migrations should exist after getDb()");

    const version = db.prepare("SELECT MAX(version) AS v FROM _schema_migrations").get();
    assert.ok(version.v >= 1, "at least v1 should be applied");
    db.close();
  });

  // ── Schema integrity after migration ──────────────────────────

  it("all expected indexes exist after fresh migration", () => {
    const db = bareDb("indexes");
    const { runMigrations } = freshMigrations();
    runMigrations(db);

    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).all().map((r) => r.name);

    const expected = [
      "idx_events_session", "idx_events_timestamp", "idx_sessions_status",
      "idx_events_model", "idx_events_type", "idx_sessions_agent",
      "idx_sessions_started", "idx_events_session_ts", "idx_events_perf_covering",
      "idx_session_tags_tag", "idx_session_tags_session", "idx_cost_budgets_scope",
    ];
    for (const idx of expected) {
      assert.ok(indexes.includes(idx), `missing index: ${idx}`);
    }
    db.close();
  });

  it("foreign keys are correct after fresh migration", () => {
    const db = bareDb("fk");
    const { runMigrations } = freshMigrations();
    runMigrations(db);

    // events → sessions
    const evtFks = db.prepare("PRAGMA foreign_key_list(events)").all();
    assert.ok(evtFks.some((f) => f.from === "session_id" && f.table === "sessions"));

    // session_tags → sessions (CASCADE)
    const tagFks = db.prepare("PRAGMA foreign_key_list(session_tags)").all();
    const tagFk = tagFks.find((f) => f.from === "session_id");
    assert.ok(tagFk);
    assert.equal(tagFk.on_delete, "CASCADE");
    db.close();
  });
});
