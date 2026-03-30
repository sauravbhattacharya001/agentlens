/* ── Data Retention & Cleanup ─────────────────────────────────────── */
/* GET  /retention/config     — get current retention settings         */
/* PUT  /retention/config     — update retention settings              */
/* GET  /retention/stats      — database size & age statistics         */
/* POST /retention/purge      — manually purge old data (dry-run opt)  */

const express = require("express");
const router = express.Router();
const { getDb } = require("../db");
const { wrapRoute } = require("../lib/request-helpers");

// ── Schema initialisation ───────────────────────────────────────────

let _retentionTableReady = false;
function ensureRetentionTable() {
  if (_retentionTableReady) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS retention_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  _retentionTableReady = true;
}

// ── Default config ──────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  max_age_days: 90,        // sessions older than this are eligible for purge
  max_sessions: 0,         // 0 = unlimited; otherwise purge oldest over limit
  exempt_tags: [],         // sessions with any of these tags are never purged
  auto_purge: false,       // future: cron-based auto-cleanup
};

// ── Helpers ─────────────────────────────────────────────────────────

let _retentionStmts = null;

function getRetentionStatements() {
  if (_retentionStmts) return _retentionStmts;
  ensureRetentionTable();
  const db = getDb();
  _retentionStmts = {
    getConfig: db.prepare("SELECT key, value FROM retention_config"),
    upsertConfig: db.prepare(`
      INSERT INTO retention_config (key, value, updated_at)
      VALUES (?, ?, datetime('now'))
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    `),
    sessionCount: db.prepare("SELECT COUNT(*) AS count FROM sessions"),
    eventCount: db.prepare("SELECT COUNT(*) AS count FROM events"),
    oldestSession: db.prepare("SELECT MIN(started_at) AS oldest FROM sessions"),
    newestSession: db.prepare("SELECT MAX(started_at) AS newest FROM sessions"),
    sessionsOlderThan: db.prepare(`
      SELECT session_id FROM sessions
      WHERE started_at < ? ORDER BY started_at ASC
    `),
    eventCountBySession: db.prepare(
      "SELECT COUNT(*) AS count FROM events WHERE session_id = ?"
    ),
    deleteEvents: db.prepare("DELETE FROM events WHERE session_id = ?"),
    deleteSession: db.prepare("DELETE FROM sessions WHERE session_id = ?"),
    deleteTags: db.prepare("DELETE FROM session_tags WHERE session_id = ?"),
    ageDistribution: db.prepare(`
      SELECT
        SUM(CASE WHEN julianday('now') - julianday(started_at) <= 1 THEN 1 ELSE 0 END) AS last_24h,
        SUM(CASE WHEN julianday('now') - julianday(started_at) > 1 AND julianday('now') - julianday(started_at) <= 7 THEN 1 ELSE 0 END) AS last_7d,
        SUM(CASE WHEN julianday('now') - julianday(started_at) > 7 AND julianday('now') - julianday(started_at) <= 30 THEN 1 ELSE 0 END) AS last_30d,
        SUM(CASE WHEN julianday('now') - julianday(started_at) > 30 AND julianday('now') - julianday(started_at) <= 90 THEN 1 ELSE 0 END) AS last_90d,
        SUM(CASE WHEN julianday('now') - julianday(started_at) > 90 THEN 1 ELSE 0 END) AS older
      FROM sessions
    `),
  };
  return _retentionStmts;
}

function getConfig() {
  ensureRetentionTable();
  const stmts = getRetentionStatements();
  const rows = stmts.getConfig.all();
  const config = { ...DEFAULT_CONFIG };

  for (const row of rows) {
    try {
      config[row.key] = JSON.parse(row.value);
    } catch {
      config[row.key] = row.value;
    }
  }
  return config;
}

function saveConfig(config) {
  ensureRetentionTable();
  const stmts = getRetentionStatements();
  const db = getDb();

  const saveAll = db.transaction(() => {
    for (const [key, value] of Object.entries(config)) {
      if (key in DEFAULT_CONFIG) {
        stmts.upsertConfig.run(key, JSON.stringify(value));
      }
    }
  });
  saveAll();
}

/**
 * Returns session IDs eligible for purging based on config.
 * Respects exempt_tags — any session with an exempt tag is skipped.
 */
function getEligibleSessions(config) {
  const db = getDb();
  const stmts = getRetentionStatements();
  const eligible = [];

  // Age-based eligibility
  if (config.max_age_days > 0) {
    const cutoff = new Date(
      Date.now() - config.max_age_days * 24 * 60 * 60 * 1000
    ).toISOString();
    const oldSessions = stmts.sessionsOlderThan.all(cutoff);

    for (const row of oldSessions) {
      eligible.push({ session_id: row.session_id, reason: "age" });
    }
  }

  // Count-based eligibility (oldest sessions beyond limit)
  if (config.max_sessions > 0) {
    const total = stmts.sessionCount.get().count;
    if (total > config.max_sessions) {
      const overflow = total - config.max_sessions;
      const oldest = db.prepare(
        "SELECT session_id FROM sessions ORDER BY started_at ASC LIMIT ?"
      ).all(overflow);

      // Use Set for O(1) deduplication instead of O(n) find()
      const existingIds = new Set(eligible.map(e => e.session_id));
      for (const row of oldest) {
        if (!existingIds.has(row.session_id)) {
          eligible.push({ session_id: row.session_id, reason: "count" });
        }
      }
    }
  }

  // Filter out exempt sessions (by tag) — batch query instead of N+1
  if (config.exempt_tags && config.exempt_tags.length > 0 && eligible.length > 0) {
    const sessionIds = eligible.map(e => e.session_id);
    const sessionPlaceholders = sessionIds.map(() => "?").join(",");
    const tagPlaceholders = config.exempt_tags.map(() => "?").join(",");

    const exemptRows = db
      .prepare(
        `SELECT DISTINCT session_id FROM session_tags
         WHERE session_id IN (${sessionPlaceholders})
           AND tag IN (${tagPlaceholders})`
      )
      .all(...sessionIds, ...config.exempt_tags);

    const exemptIds = new Set(exemptRows.map(r => r.session_id));
    return eligible.filter(e => !exemptIds.has(e.session_id));
  }

  return eligible;
}

/**
 * Purge a single session and all related data.
 * Returns count of deleted events.
 */
function purgeSession(sessionId) {
  const stmts = getRetentionStatements();
  const db = getDb();

  let eventCount = 0;
  const doPurge = db.transaction(() => {
    eventCount = stmts.eventCountBySession.get(sessionId).count;
    stmts.deleteEvents.run(sessionId);
    stmts.deleteTags.run(sessionId);

    // Also delete annotations if table exists
    try {
      db.prepare("DELETE FROM annotations WHERE session_id = ?").run(sessionId);
    } catch {
      // annotations table may not exist yet
    }

    stmts.deleteSession.run(sessionId);
  });
  doPurge();
  return eventCount;
}

/**
 * Batch-purge multiple sessions in a single transaction.
 * Returns array of { session_id, events_deleted } and total event count.
 *
 * Compared to calling purgeSession() in a loop, this runs one transaction
 * instead of N, avoiding per-session WAL sync overhead.
 */
function purgeSessions(sessionIds) {
  if (sessionIds.length === 0) return { details: [], totalEvents: 0 };

  const stmts = getRetentionStatements();
  const db = getDb();

  // Check if annotations table exists (once, not per-session)
  let hasAnnotations = false;
  try {
    db.prepare("SELECT 1 FROM annotations LIMIT 0").get();
    hasAnnotations = true;
  } catch {
    // table doesn't exist
  }
  const deleteAnnotations = hasAnnotations
    ? db.prepare("DELETE FROM annotations WHERE session_id = ?")
    : null;

  const details = [];
  let totalEvents = 0;

  const doPurge = db.transaction(() => {
    for (const sid of sessionIds) {
      const evCount = stmts.eventCountBySession.get(sid).count;
      stmts.deleteEvents.run(sid);
      stmts.deleteTags.run(sid);
      if (deleteAnnotations) deleteAnnotations.run(sid);
      stmts.deleteSession.run(sid);
      details.push({ session_id: sid, events_deleted: evCount });
      totalEvents += evCount;
    }
  });
  doPurge();

  return { details, totalEvents };
}

/**
 * Batch-count events for multiple sessions using a single query.
 * Returns a Map of session_id -> event count.
 *
 * Falls back to per-session queries if the batch is too large for
 * a single IN clause (SQLite limit: ~999 params).
 */
function batchEventCounts(sessionIds) {
  if (sessionIds.length === 0) return new Map();

  const db = getDb();
  const result = new Map();

  // SQLite max variables per statement is ~999; batch in chunks
  const CHUNK = 900;
  for (let i = 0; i < sessionIds.length; i += CHUNK) {
    const chunk = sessionIds.slice(i, i + CHUNK);
    const placeholders = chunk.map(() => "?").join(",");
    const rows = db.prepare(
      `SELECT session_id, COUNT(*) AS count FROM events
       WHERE session_id IN (${placeholders}) GROUP BY session_id`
    ).all(...chunk);

    for (const row of rows) {
      result.set(row.session_id, row.count);
    }
    // Sessions with 0 events won't appear in GROUP BY
    for (const sid of chunk) {
      if (!result.has(sid)) result.set(sid, 0);
    }
  }

  return result;
}

// ── Routes ──────────────────────────────────────────────────────────

// GET /retention/config — current retention settings
router.get("/config", wrapRoute("get retention config", (req, res) => {
    const config = getConfig();
    res.json({ config });
}));

// PUT /retention/config — update retention settings
router.put("/config", wrapRoute("update retention config", (req, res) => {
    const updates = req.body;
    if (!updates || typeof updates !== "object") {
      return res.status(400).json({ error: "Request body must be a JSON object" });
    }

    const config = getConfig();
    let changed = 0;

    // Validate and apply updates
    if ("max_age_days" in updates) {
      const v = parseInt(updates.max_age_days);
      if (isNaN(v) || v < 0 || v > 3650) {
        return res.status(400).json({ error: "max_age_days must be 0-3650 (0 = disabled)" });
      }
      config.max_age_days = v;
      changed++;
    }

    if ("max_sessions" in updates) {
      const v = parseInt(updates.max_sessions);
      if (isNaN(v) || v < 0 || v > 1000000) {
        return res.status(400).json({ error: "max_sessions must be 0-1000000 (0 = unlimited)" });
      }
      config.max_sessions = v;
      changed++;
    }

    if ("exempt_tags" in updates) {
      if (!Array.isArray(updates.exempt_tags)) {
        return res.status(400).json({ error: "exempt_tags must be an array of strings" });
      }
      if (updates.exempt_tags.length > 50) {
        return res.status(400).json({ error: "Maximum 50 exempt tags allowed" });
      }
      for (const tag of updates.exempt_tags) {
        if (typeof tag !== "string" || tag.length === 0 || tag.length > 64) {
          return res.status(400).json({ error: "Each exempt tag must be a non-empty string (max 64 chars)" });
        }
      }
      config.exempt_tags = updates.exempt_tags;
      changed++;
    }

    if ("auto_purge" in updates) {
      config.auto_purge = !!updates.auto_purge;
      changed++;
    }

    if (changed === 0) {
      return res.status(400).json({ error: "No valid config fields provided" });
    }

    saveConfig(config);
    res.json({ config, updated: changed });
}));

// GET /retention/stats — database size & age statistics
router.get("/stats", wrapRoute("get retention stats", (req, res) => {
    const db = getDb();
    const stmts = getRetentionStatements();

    const sessionCount = stmts.sessionCount.get().count;
    const eventCount = stmts.eventCount.get().count;
    const oldest = stmts.oldestSession.get().oldest;
    const newest = stmts.newestSession.get().newest;

    // Age distribution
    const ageRow = stmts.ageDistribution.get();
    const ageBreakdown = {
      last_24h: ageRow.last_24h || 0,
      last_7d: ageRow.last_7d || 0,
      last_30d: ageRow.last_30d || 0,
      last_90d: ageRow.last_90d || 0,
      older: ageRow.older || 0,
    };

    // Average events per session
    const avgEvents = sessionCount > 0
      ? Math.round(eventCount / sessionCount * 10) / 10
      : 0;

    // Status breakdown
    const statusRows = db.prepare(
      "SELECT status, COUNT(*) AS count FROM sessions GROUP BY status"
    ).all();
    const statusBreakdown = {};
    for (const r of statusRows) {
      statusBreakdown[r.status] = r.count;
    }

    // Retention config for reference
    const config = getConfig();
    const eligibleForPurge = getEligibleSessions(config);

    res.json({
      sessions: sessionCount,
      events: eventCount,
      avg_events_per_session: avgEvents,
      oldest_session: oldest,
      newest_session: newest,
      age_breakdown: ageBreakdown,
      status_breakdown: statusBreakdown,
      eligible_for_purge: eligibleForPurge.length,
      config,
    });
}));

// POST /retention/purge — manually purge old data
router.post("/purge", wrapRoute("purge retention data", (req, res) => {
    const dryRun = req.query.dry_run === "true" || req.body?.dry_run === true;
    const config = getConfig();
    const eligible = getEligibleSessions(config);

    // Safety: cap destructive purges at 500 sessions per call.
    // This prevents a misconfigured retention policy from wiping the
    // entire database in a single request.  Callers can issue
    // repeated purge calls to process larger backlogs.
    const MAX_PURGE_BATCH = 500;
    const cappedEligible = eligible.slice(0, MAX_PURGE_BATCH);
    const wasCapped = eligible.length > MAX_PURGE_BATCH;

    if (cappedEligible.length === 0) {
      return res.json({
        dry_run: dryRun,
        purged_sessions: 0,
        purged_events: 0,
        details: [],
        message: "No sessions eligible for purge",
      });
    }

    if (dryRun) {
      // Batch-count events instead of N+1 per-session queries
      const sessionIds = cappedEligible.map(e => e.session_id);
      const eventCounts = batchEventCounts(sessionIds);
      const details = cappedEligible.map(e => ({
        session_id: e.session_id,
        reason: e.reason,
        events: eventCounts.get(e.session_id) || 0,
      }));
      const totalEvents = details.reduce((sum, d) => sum + d.events, 0);

      return res.json({
        dry_run: true,
        would_purge_sessions: cappedEligible.length,
        total_eligible: eligible.length,
        capped: wasCapped,
        would_purge_events: totalEvents,
        details,
        message: wasCapped
          ? `Would purge ${cappedEligible.length} of ${eligible.length} eligible sessions (batch limit ${MAX_PURGE_BATCH})`
          : `Would purge ${cappedEligible.length} sessions and ${totalEvents} events`,
      });
    }

    // Actually purge — single transaction for all sessions
    const sessionIds = cappedEligible.map(e => e.session_id);
    const { details: purgeDetails, totalEvents } = purgeSessions(sessionIds);

    // Merge reasons into details
    const reasonMap = new Map(cappedEligible.map(e => [e.session_id, e.reason]));
    const details = purgeDetails.map(d => ({
      ...d,
      reason: reasonMap.get(d.session_id) || "unknown",
    }));

    res.json({
      dry_run: false,
      purged_sessions: cappedEligible.length,
      total_eligible: eligible.length,
      remaining: eligible.length - cappedEligible.length,
      purged_events: totalEvents,
      details,
      message: wasCapped
        ? `Purged ${cappedEligible.length} of ${eligible.length} eligible sessions (${eligible.length - cappedEligible.length} remaining)`
        : `Purged ${cappedEligible.length} sessions and ${totalEvents} events`,
    });
}));

module.exports = router;
module.exports._resetStmts = function () { _retentionStmts = null; };
