const Database = require("better-sqlite3");
const path = require("path");

const DB_PATH = process.env.DB_PATH || path.join(__dirname, "agentlens.db");

let db;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    initSchema();
  }
  return db;
}

function initSchema() {
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

    -- Indexes for analytics aggregation queries
    CREATE INDEX IF NOT EXISTS idx_events_model ON events(model) WHERE model IS NOT NULL AND model != '';
    CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
    CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_name);
    CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

    -- Composite index for session-scoped event ordering (used by session detail)
    CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, timestamp);

    -- Model pricing for cost estimation
    CREATE TABLE IF NOT EXISTS model_pricing (
      model TEXT PRIMARY KEY,
      input_cost_per_1m REAL NOT NULL DEFAULT 0,
      output_cost_per_1m REAL NOT NULL DEFAULT 0,
      currency TEXT NOT NULL DEFAULT 'USD',
      updated_at TEXT NOT NULL
    );
  `);

  // Performance: optimize for read-heavy analytics workload
  db.pragma("cache_size = -8000"); // 8 MB page cache (default is ~2 MB)
  db.pragma("temp_store = MEMORY");
  db.pragma("mmap_size = 268435456"); // 256 MB mmap for faster reads
}

module.exports = { getDb };
