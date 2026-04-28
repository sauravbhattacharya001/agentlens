/**
 * Lightweight schema migration system for AgentLens.
 *
 * Migrations are ordered by version number. On startup, any unapplied
 * migrations run inside a transaction so the database never ends up in
 * a partially-migrated state.
 *
 * Migration v1 captures the full schema that previously lived in
 * initSchema() (CREATE TABLE IF NOT EXISTS …). Existing databases that
 * already have the tables get v1 "adopted" automatically (see
 * adoptBaselineIfNeeded), so they are not re-created.
 *
 * To add a new migration:
 *   1. Append an entry to the `migrations` array with the next version.
 *   2. Provide a `description` and an `up(db)` function.
 *   3. The runner guarantees sequential execution and transactional safety.
 */

"use strict";

// ─── Migration definitions ──────────────────────────────────────────

const migrations = [
  {
    version: 1,
    description: "Baseline schema — sessions, events, model_pricing, session_tags, cost_budgets, session_bookmarks, indexes, covering indexes",
    up(db) {
      db.exec(`
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

        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
        CREATE INDEX IF NOT EXISTS idx_events_model ON events(model) WHERE model IS NOT NULL AND model != '';
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_name);
        CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
        CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_perf_covering
          ON events(timestamp, duration_ms, model, event_type, session_id)
          WHERE duration_ms IS NOT NULL AND duration_ms > 0;

        CREATE TABLE IF NOT EXISTS model_pricing (
          model TEXT PRIMARY KEY,
          input_cost_per_1m REAL NOT NULL DEFAULT 0,
          output_cost_per_1m REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'USD',
          updated_at TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS cost_budgets (
          scope TEXT NOT NULL,
          period TEXT NOT NULL CHECK(period IN ('daily', 'weekly', 'monthly', 'total')),
          limit_usd REAL NOT NULL,
          warn_pct REAL NOT NULL DEFAULT 80,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (scope, period)
        );
        CREATE INDEX IF NOT EXISTS idx_cost_budgets_scope ON cost_budgets(scope);

        CREATE TABLE IF NOT EXISTS session_bookmarks (
          session_id TEXT PRIMARY KEY,
          note TEXT DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
      `);
    },
  },

  // ── Future migrations go here ────────────────────────────────
  // {
  //   version: 2,
  //   description: "Add foo column to sessions",
  //   up(db) {
  //     db.exec(`ALTER TABLE sessions ADD COLUMN foo TEXT DEFAULT NULL`);
  //   },
  // },
];

// ─── Migration runner ───────────────────────────────────────────────

/**
 * Ensure the _schema_migrations bookkeeping table exists.
 * Called once before any migration logic.
 */
function ensureMigrationsTable(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS _schema_migrations (
      version INTEGER PRIMARY KEY,
      description TEXT NOT NULL,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
}

/**
 * Return the set of already-applied migration versions.
 */
function appliedVersions(db) {
  const rows = db.prepare("SELECT version FROM _schema_migrations ORDER BY version").all();
  return new Set(rows.map((r) => r.version));
}

/**
 * For existing databases that were created before the migration system
 * existed: if the `sessions` table already exists but _schema_migrations
 * is empty, mark v1 as applied so we don't attempt to re-create the
 * baseline schema.
 */
function adoptBaselineIfNeeded(db) {
  const hasSessions = db
    .prepare(
      "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
    )
    .get();

  if (hasSessions) {
    const applied = appliedVersions(db);
    if (!applied.has(1)) {
      db.prepare(
        "INSERT INTO _schema_migrations (version, description, applied_at) VALUES (?, ?, datetime('now'))"
      ).run(1, "Baseline schema (adopted — tables already existed)");
    }
  }
}

/**
 * Run all pending migrations inside a transaction.
 *
 * @param {import("better-sqlite3").Database} db
 * @returns {{ applied: number[], current: number }} Summary of what ran.
 */
function runMigrations(db) {
  ensureMigrationsTable(db);
  adoptBaselineIfNeeded(db);

  const applied = appliedVersions(db);
  const pending = migrations
    .filter((m) => !applied.has(m.version))
    .sort((a, b) => a.version - b.version);

  const justApplied = [];

  if (pending.length > 0) {
    const applyAll = db.transaction(() => {
      for (const m of pending) {
        m.up(db);
        db.prepare(
          "INSERT INTO _schema_migrations (version, description, applied_at) VALUES (?, ?, datetime('now'))"
        ).run(m.version, m.description);
        justApplied.push(m.version);
      }
    });
    applyAll();
  }

  const allApplied = appliedVersions(db);
  const current = allApplied.size > 0 ? Math.max(...allApplied) : 0;

  return { applied: justApplied, current };
}

/**
 * Return the current schema version (highest applied migration).
 *
 * @param {import("better-sqlite3").Database} db
 * @returns {number}
 */
function currentVersion(db) {
  ensureMigrationsTable(db);
  const row = db.prepare("SELECT MAX(version) AS v FROM _schema_migrations").get();
  return row?.v ?? 0;
}

module.exports = { runMigrations, currentVersion, migrations };
