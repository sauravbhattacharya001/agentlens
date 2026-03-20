/* ── Trace Correlation Rules — cross-trace pattern linking ────────────
 *
 * Lets users define rules that automatically correlate related traces
 * across agents and services. When a rule matches, it creates a
 * correlation group linking the matched events together.
 *
 * Use cases:
 * - Link traces that share the same user request ID across microservices
 * - Find error cascades where one agent failure triggers others
 * - Correlate slow traces with high-cost traces in the same time window
 * - Track cause-effect chains: agent A output -> agent B input
 * ──────────────────────────────────────────────────────────────────── */

var express = require("express");
var crypto = require("crypto");
var router = express.Router();
var dbMod = require("../db");
var { wrapRoute, parseLimit, parseOffset } = require("../lib/request-helpers");
var { sanitizeString } = require("../lib/validation");

// ── Input limits ────────────────────────────────────────────────────
var MAX_NAME_LENGTH = 128;
var MAX_DESCRIPTION_LENGTH = 1024;
var MAX_CONFIG_SIZE = 8192; // 8 KB serialized config
var MAX_AGENT_FILTER_LENGTH = 128;

// ── Schema ──────────────────────────────────────────────────────────

function ensureCorrelationTables() {
  var db = dbMod.getDb();
  db.exec(
    "CREATE TABLE IF NOT EXISTS correlation_rules (" +
    "  rule_id TEXT PRIMARY KEY," +
    "  name TEXT NOT NULL," +
    "  description TEXT DEFAULT ''," +
    "  match_type TEXT NOT NULL," +
    "  config TEXT NOT NULL DEFAULT '{}'," +
    "  agent_filter TEXT DEFAULT NULL," +
    "  enabled INTEGER NOT NULL DEFAULT 1," +
    "  priority INTEGER NOT NULL DEFAULT 0," +
    "  created_at TEXT NOT NULL," +
    "  updated_at TEXT NOT NULL" +
    ");" +
    "CREATE TABLE IF NOT EXISTS correlation_groups (" +
    "  group_id TEXT PRIMARY KEY," +
    "  rule_id TEXT NOT NULL," +
    "  label TEXT DEFAULT ''," +
    "  created_at TEXT NOT NULL," +
    "  metadata TEXT DEFAULT '{}'," +
    "  FOREIGN KEY (rule_id) REFERENCES correlation_rules(rule_id) ON DELETE CASCADE" +
    ");" +
    "CREATE TABLE IF NOT EXISTS correlation_members (" +
    "  group_id TEXT NOT NULL," +
    "  event_id TEXT NOT NULL," +
    "  session_id TEXT NOT NULL," +
    "  role TEXT DEFAULT 'member'," +
    "  added_at TEXT NOT NULL," +
    "  PRIMARY KEY (group_id, event_id)," +
    "  FOREIGN KEY (group_id) REFERENCES correlation_groups(group_id) ON DELETE CASCADE" +
    ");" +
    "CREATE INDEX IF NOT EXISTS idx_corr_groups_rule ON correlation_groups(rule_id);" +
    "CREATE INDEX IF NOT EXISTS idx_corr_members_event ON correlation_members(event_id);" +
    "CREATE INDEX IF NOT EXISTS idx_corr_members_session ON correlation_members(session_id);" +
    "CREATE INDEX IF NOT EXISTS idx_corr_rules_enabled ON correlation_rules(enabled);"
  );
}

// ── Helpers ─────────────────────────────────────────────────────────

function now() { return new Date().toISOString(); }
function uid() { return crypto.randomUUID(); }

function parseConfig(config) {
  if (typeof config === "string") {
    try { return JSON.parse(config); } catch (e) { return {}; }
  }
  return config || {};
}

function safeJSON(obj) { return JSON.stringify(obj || {}); }

// ── Correlation engine ──────────────────────────────────────────────

function runCorrelation(rule, lookbackMinutes) {
  var db = dbMod.getDb();
  var config = parseConfig(rule.config);
  var lookback = lookbackMinutes || config.lookback_minutes || 60;
  // Cap lookback to prevent full-table scans (max 7 days = 10080 min)
  var MAX_LOOKBACK_MINUTES = 10080;
  if (lookback > MAX_LOOKBACK_MINUTES) lookback = MAX_LOOKBACK_MINUTES;
  var cutoff = new Date(Date.now() - lookback * 60000).toISOString();

  // Cap loaded events to prevent OOM on large datasets
  var EVENT_CAP = 50000;
  var events;
  if (rule.agent_filter) {
    events = db.prepare(
      "SELECT e.*, s.agent_name FROM events e " +
      "JOIN sessions s ON e.session_id = s.session_id " +
      "WHERE e.timestamp >= ? AND s.agent_name = ? " +
      "ORDER BY e.timestamp ASC LIMIT ?"
    ).all(cutoff, rule.agent_filter, EVENT_CAP);
  } else {
    events = db.prepare(
      "SELECT e.*, s.agent_name FROM events e " +
      "JOIN sessions s ON e.session_id = s.session_id " +
      "WHERE e.timestamp >= ? " +
      "ORDER BY e.timestamp ASC LIMIT ?"
    ).all(cutoff, EVENT_CAP);
  }

  if (events.length === 0) return [];

  switch (rule.match_type) {
    case "metadata_key":   return correlateByMetadata(events, config);
    case "time_window":    return correlateByTimeWindow(events, config);
    case "error_cascade":  return correlateByErrorCascade(events, config);
    case "causal_chain":   return correlateByCausalChain(events, config);
    case "custom":         return correlateByCustom(events, config);
    default:               return [];
  }
}

/** Group events that share the same value for a metadata key. */
function correlateByMetadata(events, config) {
  var key = config.key;
  if (!key) return [];

  var groups = {};
  for (var i = 0; i < events.length; i++) {
    var evt = events[i];
    var val = undefined;
    var fields = ["input_data", "output_data", "decision_trace"];
    for (var f = 0; f < fields.length; f++) {
      if (val !== undefined) break;
      try {
        var parsed = JSON.parse(evt[fields[f]] || "{}");
        if (parsed[key] !== undefined) val = parsed[key];
      } catch (e) { /* ignore */ }
    }
    if (val !== undefined && val !== null && val !== "") {
      var strVal = String(val);
      if (!groups[strVal]) groups[strVal] = [];
      groups[strVal].push(evt);
    }
  }

  var result = [];
  var keys = Object.keys(groups);
  for (var k = 0; k < keys.length; k++) {
    if (groups[keys[k]].length >= 2) {
      result.push({
        label: key + "=" + keys[k],
        events: groups[keys[k]],
        metadata: { key: key, value: keys[k] },
      });
    }
  }
  return result;
}

/** Group events occurring within a sliding time window. */
function correlateByTimeWindow(events, config) {
  var windowMs = (config.window_seconds || 10) * 1000;
  var minEvents = config.min_events || 2;
  var typeFilter = config.event_type_filter || null;

  var filtered = events;
  if (typeFilter) {
    filtered = [];
    for (var f = 0; f < events.length; f++) {
      if (events[f].event_type === typeFilter) filtered.push(events[f]);
    }
  }
  if (filtered.length < minEvents) return [];

  // Pre-parse all timestamps to avoid repeated Date construction in the
  // inner loop (O(n) allocations → O(1) lookups per comparison).
  var timestamps = new Array(filtered.length);
  for (var t = 0; t < filtered.length; t++) {
    timestamps[t] = new Date(filtered[t].timestamp).getTime();
  }

  var groups = [];
  var i = 0;
  while (i < filtered.length) {
    var windowStart = timestamps[i];
    var windowEnd = windowStart + windowMs;
    var group = [filtered[i]];
    var j = i + 1;
    while (j < filtered.length) {
      if (timestamps[j] <= windowEnd) { group.push(filtered[j]); j++; }
      else break;
    }
    if (group.length >= minEvents) {
      var sessionSet = {};
      var sessionCount = 0;
      for (var s = 0; s < group.length; s++) {
        if (!sessionSet[group[s].session_id]) {
          sessionSet[group[s].session_id] = true;
          sessionCount++;
        }
      }
      if (sessionCount >= 2 || !config.require_cross_session) {
        groups.push({
          label: "window@" + filtered[i].timestamp,
          events: group,
          metadata: {
            window_start: filtered[i].timestamp,
            window_seconds: config.window_seconds || 10,
            session_count: sessionCount,
          },
        });
      }
    }
    i = j > i + 1 ? j : i + 1;
  }
  return groups;
}

/** Find error cascades: errors in one agent followed by errors in another. */
function correlateByErrorCascade(events, config) {
  var windowMs = (config.cascade_window_seconds || 30) * 1000;
  var errors = [];
  var errorTimestamps = [];
  for (var e = 0; e < events.length; e++) {
    if (events[e].event_type === "error" || events[e].event_type === "agent_error" || events[e].event_type === "tool_error") {
      errors.push(events[e]);
      errorTimestamps.push(new Date(events[e].timestamp).getTime());
    }
  }
  if (errors.length < 2) return [];

  var groups = [];
  var used = {};

  for (var i = 0; i < errors.length; i++) {
    if (used[errors[i].event_id]) continue;
    var cascade = [errors[i]];
    var startTs = errorTimestamps[i];
    var sourceAgent = errors[i].agent_name;

    for (var j = i + 1; j < errors.length; j++) {
      if (used[errors[j].event_id]) continue;
      if (errorTimestamps[j] - startTs > windowMs) break;
      if (errors[j].agent_name !== sourceAgent) {
        cascade.push(errors[j]);
        used[errors[j].event_id] = true;
      }
    }
    if (cascade.length >= 2) {
      used[errors[i].event_id] = true;
      var agentsSeen = {};
      var agents = [];
      for (var a = 0; a < cascade.length; a++) {
        if (!agentsSeen[cascade[a].agent_name]) {
          agentsSeen[cascade[a].agent_name] = true;
          agents.push(cascade[a].agent_name);
        }
      }
      var affected = [];
      for (var af = 0; af < agents.length; af++) {
        if (agents[af] !== sourceAgent) affected.push(agents[af]);
      }
      groups.push({
        label: "cascade:" + agents.join("\u2192"),
        events: cascade,
        metadata: {
          source_agent: sourceAgent,
          affected_agents: affected,
          cascade_duration_ms: new Date(cascade[cascade.length - 1].timestamp).getTime() - startTs,
        },
      });
    }
  }
  return groups;
}

/** Find causal chains: output of one event matches input of another. */
function correlateByCausalChain(events, config) {
  var maxGapMs = (config.max_gap_seconds || 60) * 1000;
  var matchFields = config.match_fields || ["output_data"];
  var groups = [];

  for (var i = 0; i < events.length; i++) {
    var chain = [events[i]];
    var outputData = events[i].output_data || "";
    if (!outputData) continue;

    var ts1 = new Date(events[i].timestamp).getTime();

    for (var j = i + 1; j < events.length; j++) {
      var ts2 = new Date(events[j].timestamp).getTime();
      if (ts2 - ts1 > maxGapMs) break;
      if (events[j].session_id === events[i].session_id) continue;

      var matched = false;
      for (var k = 0; k < matchFields.length; k++) {
        var val1 = events[i][matchFields[k]] || "";
        var val2 = events[j].input_data || "";
        if (val1 && val2 && val2.indexOf(val1) >= 0) { matched = true; break; }
      }
      if (matched) chain.push(events[j]);
    }

    if (chain.length >= 2) {
      groups.push({
        label: "chain:" + (events[i].agent_name || "?") + "\u2192" + (chain[chain.length - 1].agent_name || "?"),
        events: chain,
        metadata: { match_fields: matchFields, chain_length: chain.length },
      });
    }
  }
  return groups;
}

/** Custom correlation by event_type and metadata pattern match. */
function correlateByCustom(events, config) {
  var types = config.event_types || [];
  var groupBy = config.group_by;

  var filtered = events;
  if (types.length > 0) {
    filtered = [];
    for (var i = 0; i < events.length; i++) {
      if (types.indexOf(events[i].event_type) >= 0) filtered.push(events[i]);
    }
  }
  if (filtered.length < 2) return [];

  if (groupBy) {
    var buckets = {};
    for (var b = 0; b < filtered.length; b++) {
      var val = filtered[b][groupBy] || "unknown";
      if (!buckets[val]) buckets[val] = [];
      buckets[val].push(filtered[b]);
    }
    var result = [];
    var bkeys = Object.keys(buckets);
    for (var bk = 0; bk < bkeys.length; bk++) {
      if (buckets[bkeys[bk]].length >= 2) {
        result.push({
          label: groupBy + "=" + bkeys[bk],
          events: buckets[bkeys[bk]],
          metadata: { group_by: groupBy, value: bkeys[bk] },
        });
      }
    }
    return result;
  }

  return [{
    label: "custom:" + types.join("+"),
    events: filtered,
    metadata: { event_types: types },
  }];
}

// ── Persist correlation results ─────────────────────────────────────

function persistGroups(rule, groups) {
  var db = dbMod.getDb();
  var insertGroup = db.prepare(
    "INSERT OR IGNORE INTO correlation_groups (group_id, rule_id, label, created_at, metadata) " +
    "VALUES (?, ?, ?, ?, ?)"
  );
  var insertMember = db.prepare(
    "INSERT OR IGNORE INTO correlation_members (group_id, event_id, session_id, role, added_at) " +
    "VALUES (?, ?, ?, ?, ?)"
  );

  var timestamp = now();
  var persisted = [];

  var txn = db.transaction(function() {
    for (var g = 0; g < groups.length; g++) {
      var groupId = uid();
      insertGroup.run(groupId, rule.rule_id, groups[g].label, timestamp, safeJSON(groups[g].metadata));
      for (var m = 0; m < groups[g].events.length; m++) {
        var evt = groups[g].events[m];
        insertMember.run(groupId, evt.event_id, evt.session_id, m === 0 ? "origin" : "member", timestamp);
      }
      persisted.push({ group_id: groupId, label: groups[g].label, member_count: groups[g].events.length });
    }
  });
  txn();

  return persisted;
}

// ── Routes ──────────────────────────────────────────────────────────

var VALID_TYPES = ["metadata_key", "time_window", "error_cascade", "causal_chain", "custom"];

/** POST /rules — Create a correlation rule */
router.post("/rules", wrapRoute("create correlation rule", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var body = req.body;

  if (!body.name || typeof body.name !== "string" || !body.name.trim()) {
    return res.status(400).json({ error: "name is required and must be a non-empty string" });
  }
  if (!body.match_type) {
    return res.status(400).json({ error: "match_type is required" });
  }
  if (VALID_TYPES.indexOf(body.match_type) < 0) {
    return res.status(400).json({ error: "match_type must be one of: " + VALID_TYPES.join(", ") });
  }

  var safeName = sanitizeString(body.name, MAX_NAME_LENGTH) || "unnamed";
  var safeDesc = sanitizeString(body.description || "", MAX_DESCRIPTION_LENGTH) || "";
  var safeAgentFilter = body.agent_filter
    ? sanitizeString(body.agent_filter, MAX_AGENT_FILTER_LENGTH)
    : null;

  // Validate config size to prevent resource exhaustion
  var configStr = safeJSON(body.config);
  if (configStr.length > MAX_CONFIG_SIZE) {
    return res.status(400).json({ error: "config is too large (max " + MAX_CONFIG_SIZE + " bytes)" });
  }

  // Validate priority is a bounded integer
  var priority = 0;
  if (body.priority !== undefined) {
    priority = parseInt(body.priority);
    if (!Number.isFinite(priority)) priority = 0;
    priority = Math.max(-100, Math.min(100, priority));
  }

  var ruleId = uid();
  var timestamp = now();
  db.prepare(
    "INSERT INTO correlation_rules (rule_id, name, description, match_type, config, agent_filter, priority, created_at, updated_at) " +
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).run(ruleId, safeName, safeDesc, body.match_type, configStr, safeAgentFilter, priority, timestamp, timestamp);

  res.status(201).json({ rule_id: ruleId, name: safeName, match_type: body.match_type, created_at: timestamp });
}));

/** GET /rules — List all correlation rules */
router.get("/rules", wrapRoute("list correlation rules", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var query = "SELECT * FROM correlation_rules";
  var params = [];
  if (req.query.enabled !== undefined) {
    query += " WHERE enabled = ?";
    params.push(req.query.enabled === "true" ? 1 : 0);
  }
  query += " ORDER BY priority DESC, created_at DESC";

  var stmt = db.prepare(query);
  var rules = params.length > 0 ? stmt.all(params[0]) : stmt.all();
  for (var i = 0; i < rules.length; i++) { rules[i].config = parseConfig(rules[i].config); }
  res.json({ rules: rules, total: rules.length });
}));

/** GET /rules/:ruleId — Get a specific rule */
router.get("/rules/:ruleId", wrapRoute("get correlation rule", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });
  rule.config = parseConfig(rule.config);
  var stats = db.prepare("SELECT COUNT(*) as group_count FROM correlation_groups WHERE rule_id = ?").get(rule.rule_id);
  rule.group_count = stats.group_count;
  res.json(rule);
}));

/** PATCH /rules/:ruleId — Update a rule */
router.patch("/rules/:ruleId", wrapRoute("update correlation rule", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });

  var fields = [];
  var values = [];

  // Validate and sanitize each allowed field
  if (req.body.name !== undefined) {
    if (typeof req.body.name !== "string" || !req.body.name.trim()) {
      return res.status(400).json({ error: "name must be a non-empty string" });
    }
    fields.push("name = ?");
    values.push(sanitizeString(req.body.name, MAX_NAME_LENGTH));
  }
  if (req.body.description !== undefined) {
    fields.push("description = ?");
    values.push(sanitizeString(req.body.description || "", MAX_DESCRIPTION_LENGTH) || "");
  }
  if (req.body.match_type !== undefined) {
    if (VALID_TYPES.indexOf(req.body.match_type) < 0) {
      return res.status(400).json({ error: "match_type must be one of: " + VALID_TYPES.join(", ") });
    }
    fields.push("match_type = ?");
    values.push(req.body.match_type);
  }
  if (req.body.config !== undefined) {
    var configStr = safeJSON(req.body.config);
    if (configStr.length > MAX_CONFIG_SIZE) {
      return res.status(400).json({ error: "config is too large (max " + MAX_CONFIG_SIZE + " bytes)" });
    }
    fields.push("config = ?");
    values.push(configStr);
  }
  if (req.body.agent_filter !== undefined) {
    fields.push("agent_filter = ?");
    values.push(req.body.agent_filter
      ? sanitizeString(req.body.agent_filter, MAX_AGENT_FILTER_LENGTH)
      : null);
  }
  if (req.body.enabled !== undefined) {
    fields.push("enabled = ?");
    values.push(req.body.enabled ? 1 : 0);
  }
  if (req.body.priority !== undefined) {
    var priority = parseInt(req.body.priority);
    if (!Number.isFinite(priority)) {
      return res.status(400).json({ error: "priority must be an integer" });
    }
    fields.push("priority = ?");
    values.push(Math.max(-100, Math.min(100, priority)));
  }

  if (fields.length === 0) return res.status(400).json({ error: "No fields to update" });

  fields.push("updated_at = ?");
  values.push(now());
  values.push(req.params.ruleId);
  var updateStmt = db.prepare("UPDATE correlation_rules SET " + fields.join(", ") + " WHERE rule_id = ?");
  updateStmt.run.apply(updateStmt, values);
  res.json({ updated: true, rule_id: req.params.ruleId });
}));

/** DELETE /rules/:ruleId — Delete a rule (cascades groups) */
router.delete("/rules/:ruleId", wrapRoute("delete correlation rule", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var result = db.prepare("DELETE FROM correlation_rules WHERE rule_id = ?").run(req.params.ruleId);
  if (result.changes === 0) return res.status(404).json({ error: "Rule not found" });
  res.json({ deleted: true, rule_id: req.params.ruleId });
}));

/** POST /rules/:ruleId/run — Execute a correlation rule */
router.post("/rules/:ruleId/run", wrapRoute("run correlation rule", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });

  // Validate lookback_minutes if provided
  var lookback = null;
  if (req.body.lookback_minutes !== undefined) {
    lookback = parseInt(req.body.lookback_minutes);
    if (!Number.isFinite(lookback) || lookback < 1 || lookback > 10080) {
      return res.status(400).json({ error: "lookback_minutes must be 1-10080 (max 7 days)" });
    }
  }

  var groups = runCorrelation(rule, lookback);
  var persisted = [];
  if (req.body.persist !== false) persisted = persistGroups(rule, groups);

  var totalEvts = 0;
  for (var g = 0; g < groups.length; g++) totalEvts += groups[g].events.length;

  var outGroups;
  if (persisted.length > 0) {
    outGroups = persisted;
  } else {
    outGroups = [];
    for (var g2 = 0; g2 < groups.length; g2++) {
      outGroups.push({ label: groups[g2].label, member_count: groups[g2].events.length, metadata: groups[g2].metadata });
    }
  }

  res.json({
    rule_id: rule.rule_id, rule_name: rule.name, match_type: rule.match_type,
    groups_found: groups.length, total_events_correlated: totalEvts,
    groups: outGroups,
  });
}));

/** GET /groups — List correlation groups */
router.get("/groups", wrapRoute("list correlation groups", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  // Join member counts directly in the main query to eliminate N+1
  // (previously ran a separate COUNT query per group — O(N) round-trips).
  var query = "SELECT g.*, r.name as rule_name, r.match_type, " +
    "COALESCE(mc.cnt, 0) as member_count " +
    "FROM correlation_groups g " +
    "JOIN correlation_rules r ON g.rule_id = r.rule_id " +
    "LEFT JOIN (SELECT group_id, COUNT(*) as cnt FROM correlation_members GROUP BY group_id) mc " +
    "ON g.group_id = mc.group_id";
  var params = [];
  if (req.query.rule_id) { query += " WHERE g.rule_id = ?"; params.push(req.query.rule_id); }
  query += " ORDER BY g.created_at DESC";
  var limit = parseLimit(req.query.limit, 50, 200);
  var offset = parseOffset(req.query.offset);
  query += " LIMIT ? OFFSET ?";
  params.push(limit, offset);

  var stmt = db.prepare(query);
  var groups = stmt.all.apply(stmt, params);
  for (var i = 0; i < groups.length; i++) { groups[i].metadata = parseConfig(groups[i].metadata); }

  var totalQuery = "SELECT COUNT(*) as cnt FROM correlation_groups" + (req.query.rule_id ? " WHERE rule_id = ?" : "");
  var total;
  if (req.query.rule_id) { total = db.prepare(totalQuery).get(req.query.rule_id).cnt; }
  else { total = db.prepare(totalQuery).get().cnt; }

  res.json({ groups: groups, total: total, limit: limit, offset: offset });
}));

/** GET /groups/:groupId — Get group details with members */
router.get("/groups/:groupId", wrapRoute("get correlation group", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var group = db.prepare(
    "SELECT g.*, r.name as rule_name, r.match_type FROM correlation_groups g " +
    "JOIN correlation_rules r ON g.rule_id = r.rule_id WHERE g.group_id = ?"
  ).get(req.params.groupId);
  if (!group) return res.status(404).json({ error: "Group not found" });
  group.metadata = parseConfig(group.metadata);

  var members = db.prepare(
    "SELECT m.*, e.event_type, e.timestamp, e.model, e.duration_ms, s.agent_name " +
    "FROM correlation_members m " +
    "JOIN events e ON m.event_id = e.event_id " +
    "JOIN sessions s ON m.session_id = s.session_id " +
    "WHERE m.group_id = ? ORDER BY e.timestamp ASC"
  ).all(req.params.groupId);

  group.members = members;
  group.member_count = members.length;
  res.json(group);
}));

/** DELETE /groups/:groupId — Delete a correlation group */
router.delete("/groups/:groupId", wrapRoute("delete correlation group", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var result = db.prepare("DELETE FROM correlation_groups WHERE group_id = ?").run(req.params.groupId);
  if (result.changes === 0) return res.status(404).json({ error: "Group not found" });
  res.json({ deleted: true, group_id: req.params.groupId });
}));

/** GET /stats — Correlation statistics */
router.get("/stats", wrapRoute("correlation stats", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var ruleCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_rules").get().cnt;
  var enabledCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_rules WHERE enabled = 1").get().cnt;
  var groupCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_groups").get().cnt;
  var memberCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_members").get().cnt;

  var byType = db.prepare(
    "SELECT r.match_type, COUNT(g.group_id) as groups FROM correlation_rules r " +
    "LEFT JOIN correlation_groups g ON r.rule_id = g.rule_id GROUP BY r.match_type"
  ).all();

  res.json({
    total_rules: ruleCount, enabled_rules: enabledCount,
    total_groups: groupCount, total_correlated_events: memberCount,
    by_match_type: byType,
  });
}));

/** GET /event/:eventId — Find all correlations for an event */
router.get("/event/:eventId", wrapRoute("find event correlations", function(req, res) {
  ensureCorrelationTables();
  var db = dbMod.getDb();
  var memberships = db.prepare(
    "SELECT m.group_id, m.role, g.label, g.created_at, g.metadata, r.name as rule_name, r.match_type " +
    "FROM correlation_members m JOIN correlation_groups g ON m.group_id = g.group_id " +
    "JOIN correlation_rules r ON g.rule_id = r.rule_id WHERE m.event_id = ? ORDER BY g.created_at DESC"
  ).all(req.params.eventId);
  for (var i = 0; i < memberships.length; i++) { memberships[i].metadata = parseConfig(memberships[i].metadata); }
  res.json({ event_id: req.params.eventId, correlations: memberships, total: memberships.length });
}));

// ── Export for testing ──────────────────────────────────────────────
router._engine = {
  correlateByMetadata: correlateByMetadata,
  correlateByTimeWindow: correlateByTimeWindow,
  correlateByErrorCascade: correlateByErrorCascade,
  correlateByCausalChain: correlateByCausalChain,
  correlateByCustom: correlateByCustom,
  runCorrelation: runCorrelation,
  persistGroups: persistGroups,
  ensureCorrelationTables: ensureCorrelationTables,
};

module.exports = router;
