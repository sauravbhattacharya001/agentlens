/* ── Correlation Scheduler — scheduled runs, dedup, SSE streaming ────
 *
 * Adds three capabilities to the correlation engine (issue #32):
 *
 * 1. Scheduled auto-correlation: periodically runs enabled rules and
 *    persists new groups automatically.
 *
 * 2. Deduplication: content-hashes groups by sorted event IDs so
 *    repeated runs don't create duplicate groups for the same events.
 *
 * 3. SSE streaming: pushes new correlation groups to connected clients
 *    in real time via Server-Sent Events.
 * ──────────────────────────────────────────────────────────────────── */

var express = require("express");
var crypto = require("crypto");
var router = express.Router();
var dbMod = require("../db");
var { wrapRoute } = require("../lib/request-helpers");

// ── SSE client management ───────────────────────────────────────────

var sseClients = [];

function addClient(res) {
  sseClients.push(res);
  res.on("close", function () {
    sseClients = sseClients.filter(function (c) { return c !== res; });
  });
}

function broadcast(eventName, data) {
  var payload = "event: " + eventName + "\ndata: " + JSON.stringify(data) + "\n\n";
  for (var i = 0; i < sseClients.length; i++) {
    try { sseClients[i].write(payload); } catch (e) { /* client gone */ }
  }
}

// ── Deduplication ───────────────────────────────────────────────────

/**
 * Compute a content hash for a group based on sorted event IDs.
 * Two groups with the same set of events produce the same hash,
 * preventing duplicates across repeated correlation runs.
 */
function groupContentHash(ruleId, events) {
  var ids = [];
  for (var i = 0; i < events.length; i++) {
    ids.push(events[i].event_id);
  }
  ids.sort();
  return crypto
    .createHash("sha256")
    .update(ruleId + ":" + ids.join(","))
    .digest("hex")
    .slice(0, 32);
}

function ensureContentHashColumn() {
  var db = dbMod.getDb();
  try {
    db.exec("ALTER TABLE correlation_groups ADD COLUMN content_hash TEXT DEFAULT NULL");
  } catch (e) {
    // Column already exists — ignore
  }
  try {
    db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_corr_groups_hash ON correlation_groups(content_hash)");
  } catch (e) { /* ignore */ }
}

// ── Scheduler tables ────────────────────────────────────────────────

function ensureSchedulerTables() {
  var db = dbMod.getDb();
  db.exec(
    "CREATE TABLE IF NOT EXISTS correlation_schedules (" +
    "  rule_id TEXT PRIMARY KEY," +
    "  interval_seconds INTEGER NOT NULL DEFAULT 300," +
    "  lookback_minutes INTEGER NOT NULL DEFAULT 60," +
    "  last_run_at TEXT DEFAULT NULL," +
    "  next_run_at TEXT DEFAULT NULL," +
    "  run_count INTEGER NOT NULL DEFAULT 0," +
    "  groups_found_total INTEGER NOT NULL DEFAULT 0," +
    "  enabled INTEGER NOT NULL DEFAULT 1," +
    "  FOREIGN KEY (rule_id) REFERENCES correlation_rules(rule_id) ON DELETE CASCADE" +
    ");" +
    "CREATE INDEX IF NOT EXISTS idx_corr_sched_next ON correlation_schedules(next_run_at);"
  );
}

function now() { return new Date().toISOString(); }

// ── Dedup-aware persist ─────────────────────────────────────────────

/**
 * Persist groups with content-hash deduplication.
 * Returns only newly created groups (skips duplicates).
 */
function persistGroupsDeduped(rule, groups) {
  var db = dbMod.getDb();
  ensureContentHashColumn();

  var insertGroup = db.prepare(
    "INSERT OR IGNORE INTO correlation_groups (group_id, rule_id, label, created_at, metadata, content_hash) " +
    "VALUES (?, ?, ?, ?, ?, ?)"
  );
  var insertMember = db.prepare(
    "INSERT OR IGNORE INTO correlation_members (group_id, event_id, session_id, role, added_at) " +
    "VALUES (?, ?, ?, ?, ?)"
  );

  var timestamp = now();
  var newGroups = [];

  var txn = db.transaction(function () {
    for (var g = 0; g < groups.length; g++) {
      var hash = groupContentHash(rule.rule_id, groups[g].events);

      // Check if this exact group already exists
      var existing = db.prepare(
        "SELECT group_id FROM correlation_groups WHERE content_hash = ?"
      ).get(hash);

      if (existing) continue; // Skip duplicate

      var groupId = crypto.randomUUID();
      var result = insertGroup.run(
        groupId, rule.rule_id, groups[g].label, timestamp,
        JSON.stringify(groups[g].metadata || {}), hash
      );

      if (result.changes > 0) {
        for (var m = 0; m < groups[g].events.length; m++) {
          var evt = groups[g].events[m];
          insertMember.run(
            groupId, evt.event_id, evt.session_id,
            m === 0 ? "origin" : "member", timestamp
          );
        }
        newGroups.push({
          group_id: groupId,
          label: groups[g].label,
          member_count: groups[g].events.length,
          metadata: groups[g].metadata,
          content_hash: hash,
        });
      }
    }
  });
  txn();

  return newGroups;
}

// ── Scheduler engine ────────────────────────────────────────────────

var schedulerInterval = null;

/**
 * Run all due scheduled correlations.
 * Called periodically by the scheduler loop.
 */
function runDueCorrelations() {
  var db;
  try { db = dbMod.getDb(); } catch (e) { return; }

  ensureSchedulerTables();
  ensureContentHashColumn();

  var timestamp = now();
  var due = db.prepare(
    "SELECT s.*, r.name, r.match_type, r.config, r.agent_filter, r.enabled as rule_enabled " +
    "FROM correlation_schedules s " +
    "JOIN correlation_rules r ON s.rule_id = r.rule_id " +
    "WHERE s.enabled = 1 AND r.enabled = 1 AND (s.next_run_at IS NULL OR s.next_run_at <= ?)"
  ).all(timestamp);

  for (var i = 0; i < due.length; i++) {
    var schedule = due[i];
    var rule = {
      rule_id: schedule.rule_id,
      name: schedule.name,
      match_type: schedule.match_type,
      config: schedule.config,
      agent_filter: schedule.agent_filter,
    };

    // Import the correlation engine from the main correlations router
    var correlations;
    try { correlations = require("./correlations"); } catch (e) { continue; }
    var engine = correlations._engine;
    if (!engine || !engine.runCorrelation) continue;

    var groups = engine.runCorrelation(rule, schedule.lookback_minutes);
    var persisted = persistGroupsDeduped(rule, groups);

    // Broadcast new groups via SSE
    for (var p = 0; p < persisted.length; p++) {
      broadcast("correlation", {
        rule_id: rule.rule_id,
        rule_name: rule.name,
        match_type: rule.match_type,
        group: persisted[p],
      });
    }

    // Update schedule
    var nextRun = new Date(Date.now() + schedule.interval_seconds * 1000).toISOString();
    db.prepare(
      "UPDATE correlation_schedules SET last_run_at = ?, next_run_at = ?, " +
      "run_count = run_count + 1, groups_found_total = groups_found_total + ? " +
      "WHERE rule_id = ?"
    ).run(timestamp, nextRun, persisted.length, schedule.rule_id);
  }
}

/**
 * Start the scheduler loop. Checks every 15 seconds for due rules.
 */
function startScheduler() {
  if (schedulerInterval) return;
  ensureSchedulerTables();
  ensureContentHashColumn();
  schedulerInterval = setInterval(runDueCorrelations, 15000);
  // Run immediately on start
  runDueCorrelations();
}

function stopScheduler() {
  if (schedulerInterval) {
    clearInterval(schedulerInterval);
    schedulerInterval = null;
  }
}

// ── Routes ──────────────────────────────────────────────────────────

/** GET /stream — SSE endpoint for real-time correlation notifications */
router.get("/stream", function (req, res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.write("event: connected\ndata: {\"status\":\"ok\"}\n\n");
  addClient(res);

  // Keep-alive every 30s
  var keepAlive = setInterval(function () {
    try { res.write(":keepalive\n\n"); } catch (e) { clearInterval(keepAlive); }
  }, 30000);
  res.on("close", function () { clearInterval(keepAlive); });
});

/** POST /schedules — Create or update a schedule for a rule */
router.post("/schedules", wrapRoute("create/update correlation schedule", function (req, res) {
  ensureSchedulerTables();
  var db = dbMod.getDb();
  var body = req.body;

  if (!body.rule_id) {
    return res.status(400).json({ error: "rule_id is required" });
  }

  // Verify rule exists
  var rule = db.prepare("SELECT rule_id FROM correlation_rules WHERE rule_id = ?").get(body.rule_id);
  if (!rule) return res.status(404).json({ error: "Rule not found" });

  var interval = body.interval_seconds || 300;
  var lookback = body.lookback_minutes || 60;
  var enabled = body.enabled !== undefined ? (body.enabled ? 1 : 0) : 1;
  var nextRun = new Date(Date.now() + interval * 1000).toISOString();

  db.prepare(
    "INSERT INTO correlation_schedules (rule_id, interval_seconds, lookback_minutes, enabled, next_run_at) " +
    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(rule_id) DO UPDATE SET " +
    "interval_seconds = excluded.interval_seconds, lookback_minutes = excluded.lookback_minutes, " +
    "enabled = excluded.enabled, next_run_at = excluded.next_run_at"
  ).run(body.rule_id, interval, lookback, enabled, nextRun);

  res.status(201).json({
    rule_id: body.rule_id,
    interval_seconds: interval,
    lookback_minutes: lookback,
    enabled: !!enabled,
    next_run_at: nextRun,
  });
}));

/** GET /schedules — List all schedules */
router.get("/schedules", wrapRoute("list correlation schedules", function (req, res) {
  ensureSchedulerTables();
  var db = dbMod.getDb();
  var schedules = db.prepare(
    "SELECT s.*, r.name as rule_name, r.match_type FROM correlation_schedules s " +
    "JOIN correlation_rules r ON s.rule_id = r.rule_id ORDER BY s.next_run_at ASC"
  ).all();
  res.json({ schedules: schedules, total: schedules.length });
}));

/** DELETE /schedules/:ruleId — Remove a schedule */
router.delete("/schedules/:ruleId", wrapRoute("delete correlation schedule", function (req, res) {
  ensureSchedulerTables();
  var db = dbMod.getDb();
  var result = db.prepare("DELETE FROM correlation_schedules WHERE rule_id = ?").run(req.params.ruleId);
  if (result.changes === 0) return res.status(404).json({ error: "Schedule not found" });
  res.json({ deleted: true, rule_id: req.params.ruleId });
}));

/** POST /scheduler/start — Start the scheduler loop */
router.post("/scheduler/start", wrapRoute("start correlation scheduler", function (req, res) {
  startScheduler();
  res.json({ status: "running" });
}));

/** POST /scheduler/stop — Stop the scheduler loop */
router.post("/scheduler/stop", wrapRoute("stop correlation scheduler", function (req, res) {
  stopScheduler();
  res.json({ status: "stopped" });
}));

/** GET /scheduler/status — Get scheduler status */
router.get("/scheduler/status", wrapRoute("get scheduler status", function (req, res) {
  res.json({
    running: schedulerInterval !== null,
    connected_clients: sseClients.length,
  });
}));

// ── Auto-start on require ───────────────────────────────────────────
// Delay start to let DB initialize
setTimeout(startScheduler, 2000);

// ── Exports ─────────────────────────────────────────────────────────
router._scheduler = {
  startScheduler: startScheduler,
  stopScheduler: stopScheduler,
  runDueCorrelations: runDueCorrelations,
  persistGroupsDeduped: persistGroupsDeduped,
  groupContentHash: groupContentHash,
  broadcast: broadcast,
};

module.exports = router;
