const Database = require("better-sqlite3");
const path = require("path");
const { runMigrations } = require("./migrations");

const DB_PATH = process.env.DB_PATH || path.join(__dirname, "agentlens.db");

let db;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    runMigrations(db);
    setPragmas();
  }
  return db;
}

/**
 * Performance pragmas for read-heavy analytics workload.
 * Separated from schema init so they can be set after migrations.
 */
function setPragmas() {
  db.pragma("cache_size = -8000"); // 8 MB page cache (default is ~2 MB)
  db.pragma("temp_store = MEMORY");
  db.pragma("mmap_size = 268435456"); // 256 MB mmap for faster reads
}

/**
 * Close the database connection gracefully.
 * Checkpoints WAL journal and releases the file handle.
 * Safe to call multiple times (no-op if already closed).
 */
function closeDb() {
  if (db) {
    try {
      db.pragma("wal_checkpoint(TRUNCATE)");
      db.close();
    } catch (_) {
      // Ignore errors during shutdown (db may already be closed)
    }
    db = null;
  }
}

module.exports = { getDb, closeDb };
