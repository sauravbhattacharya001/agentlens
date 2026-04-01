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

const express = require("express");
const crypto = require("crypto");
const router = express.Router();
const dbMod = require("../db");
const { wrapRoute, parseLimit, parseOffset } = require("../lib/request-helpers");
const { sanitizeString, safeJsonParse, safeJsonStringify } = require("../lib/validation");

// ── Input limits ────────────────────────────────────────────────────
const MAX_NAME_LENGTH = 128;
const MAX_DESCRIPTION_LENGTH = 1024;
const MAX_CONFIG_SIZE = 8192; // 8 KB serialized config
const MAX_AGENT_FILTER_LENGTH = 128;

// ── Schema ──────────────────────────────────────────────────────────

let _corrReady = false;
function ensureCorrelationTables() {
  if (_corrReady) return;
  const db = dbMod.getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS correlation_rules (
      rule_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      match_type TEXT NOT NULL,
      config TEXT NOT NULL DEFAULT '{}',
      agent_filter TEXT DEFAULT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      priority INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS correlation_groups (
      group_id TEXT PRIMARY KEY,
      rule_id TEXT NOT NULL,
      label TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      metadata TEXT DEFAULT '{}',
      FOREIGN KEY (rule_id) REFERENCES correlation_rules(rule_id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS correlation_members (
      group_id TEXT NOT NULL,
      event_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      role TEXT DEFAULT 'member',
      added_at TEXT NOT NULL,
      PRIMARY KEY (group_id, event_id),
      FOREIGN KEY (group_id) REFERENCES correlation_groups(group_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_corr_groups_rule ON correlation_groups(rule_id);
    CREATE INDEX IF NOT EXISTS idx_corr_members_event ON correlation_members(event_id);
    CREATE INDEX IF NOT EXISTS idx_corr_members_session ON correlation_members(session_id);
    CREATE INDEX IF NOT EXISTS idx_corr_rules_enabled ON correlation_rules(enabled);
  `);
  _corrReady = true;
}

// ── Helpers ─────────────────────────────────────────────────────────

const now = () => new Date().toISOString();
const uid = () => crypto.randomUUID();

/** Pre-compute _ts on each event to avoid repeated Date parsing in hot loops. */
function ensureTimestamps(events) {
  for (let i = 0; i < events.length; i++) {
    if (events[i]._ts === undefined) {
      events[i]._ts = new Date(events[i].timestamp).getTime();
    }
  }
}

// ── Correlation strategy registry ───────────────────────────────────

const CORRELATION_STRATEGIES = {
  metadata_key:  correlateByMetadata,
  time_window:   correlateByTimeWindow,
  error_cascade: correlateByErrorCascade,
  causal_chain:  correlateByCausalChain,
  custom:        correlateByCustom,
};

const VALID_TYPES = Object.keys(CORRELATION_STRATEGIES);

// ── Correlation engine ──────────────────────────────────────────────

const MAX_LOOKBACK_MINUTES = 10080; // 7 days
const EVENT_CAP = 50000;

function runCorrelation(rule, lookbackMinutes) {
  const db = dbMod.getDb();
  const config = safeJsonParse(rule.config);
  let lookback = lookbackMinutes || config.lookback_minutes || 60;
  if (lookback > MAX_LOOKBACK_MINUTES) lookback = MAX_LOOKBACK_MINUTES;
  const cutoff = new Date(Date.now() - lookback * 60000).toISOString();

  let events;
  if (rule.agent_filter) {
    events = db.prepare(
      `SELECT e.*, s.agent_name FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ? AND s.agent_name = ?
       ORDER BY e.timestamp ASC LIMIT ?`
    ).all(cutoff, rule.agent_filter, EVENT_CAP);
  } else {
    events = db.prepare(
      `SELECT e.*, s.agent_name FROM events e
       JOIN sessions s ON e.session_id = s.session_id
       WHERE e.timestamp >= ?
       ORDER BY e.timestamp ASC LIMIT ?`
    ).all(cutoff, EVENT_CAP);
  }

  if (events.length === 0) return [];

  ensureTimestamps(events);

  const strategy = CORRELATION_STRATEGIES[rule.match_type];
  return strategy ? strategy(events, config) : [];
}

/** Group events that share the same value for a metadata key. */
function correlateByMetadata(events, config) {
  const key = config.key;
  if (!key) return [];

  const groups = {};
  const fields = ["input_data", "output_data", "decision_trace"];
  const PARSE_CACHE_MAX = 10000;
  const parseCache = new Map();

  for (const evt of events) {
    let val;
    for (const field of fields) {
      if (val !== undefined) break;
      const raw = evt[field];
      if (!raw) continue;

      let parsed;
      if (parseCache.has(raw)) {
        parsed = parseCache.get(raw);
      } else {
        try { parsed = JSON.parse(raw); } catch { parsed = null; }
        if (parseCache.size < PARSE_CACHE_MAX) {
          parseCache.set(raw, parsed);
        }
      }
      if (parsed && parsed[key] !== undefined) val = parsed[key];
    }

    if (val !== undefined && val !== null && val !== "") {
      const strVal = String(val);
      if (!groups[strVal]) groups[strVal] = [];
      groups[strVal].push(evt);
    }
  }

  const result = [];
  for (const [groupKey, groupEvents] of Object.entries(groups)) {
    if (groupEvents.length >= 2) {
      result.push({
        label: `${key}=${groupKey}`,
        events: groupEvents,
        metadata: { key, value: groupKey },
      });
    }
  }
  return result;
}

/** Group events occurring within a sliding time window. */
function correlateByTimeWindow(events, config) {
  const windowMs = (config.window_seconds || 10) * 1000;
  const minEvents = config.min_events || 2;
  const typeFilter = config.event_type_filter || null;

  let filtered = events;
  if (typeFilter) {
    filtered = events.filter(e => e.event_type === typeFilter);
  }
  if (filtered.length < minEvents) return [];

  ensureTimestamps(filtered);

  const groups = [];
  let i = 0;
  while (i < filtered.length) {
    const windowStart = filtered[i]._ts;
    const windowEnd = windowStart + windowMs;
    const group = [filtered[i]];
    let j = i + 1;
    while (j < filtered.length && filtered[j]._ts <= windowEnd) {
      group.push(filtered[j]);
      j++;
    }
    if (group.length >= minEvents) {
      const sessionSet = new Set();
      for (const evt of group) sessionSet.add(evt.session_id);

      if (sessionSet.size >= 2 || !config.require_cross_session) {
        groups.push({
          label: `window@${filtered[i].timestamp}`,
          events: group,
          metadata: {
            window_start: filtered[i].timestamp,
            window_seconds: config.window_seconds || 10,
            session_count: sessionSet.size,
          },
        });
      }
    }
    i = j > i + 1 ? j : i + 1;
  }
  return groups;
}

const ERROR_EVENT_TYPES = new Set(["error", "agent_error", "tool_error"]);

/** Find error cascades: errors in one agent followed by errors in another. */
function correlateByErrorCascade(events, config) {
  const windowMs = (config.cascade_window_seconds || 30) * 1000;

  ensureTimestamps(events);
  const errors = events.filter(e => ERROR_EVENT_TYPES.has(e.event_type));
  if (errors.length < 2) return [];

  const groups = [];
  const used = new Set();

  for (let i = 0; i < errors.length; i++) {
    if (used.has(errors[i].event_id)) continue;
    const cascade = [errors[i]];
    const startTs = errors[i]._ts;
    const sourceAgent = errors[i].agent_name;

    for (let j = i + 1; j < errors.length; j++) {
      if (used.has(errors[j].event_id)) continue;
      if (errors[j]._ts - startTs > windowMs) break;
      if (errors[j].agent_name !== sourceAgent) {
        cascade.push(errors[j]);
        used.add(errors[j].event_id);
      }
    }

    if (cascade.length >= 2) {
      used.add(errors[i].event_id);
      const agents = [...new Set(cascade.map(e => e.agent_name))];
      const affected = agents.filter(a => a !== sourceAgent);

      groups.push({
        label: `cascade:${agents.join("\u2192")}`,
        events: cascade,
        metadata: {
          source_agent: sourceAgent,
          affected_agents: affected,
          cascade_duration_ms: cascade[cascade.length - 1]._ts - startTs,
        },
      });
    }
  }
  return groups;
}

/** Find causal chains: output of one event matches input of another.
 *  Uses an inverted index from input_data tokens to avoid O(n²) string scans. */
function correlateByCausalChain(events, config) {
  const maxGapMs = (config.max_gap_seconds || 60) * 1000;
  const matchFields = config.match_fields || ["output_data"];
  const groups = [];

  // Build index: map input_data values to events for fast lookup.
  // Only index values up to 4 KB to prevent memory exhaustion.
  const INPUT_INDEX_MAX_VALUE_LEN = 4096;
  const inputIndex = {};
  for (let idx = 0; idx < events.length; idx++) {
    const inputVal = events[idx].input_data;
    if (inputVal && inputVal.length <= INPUT_INDEX_MAX_VALUE_LEN) {
      if (!inputIndex[inputVal]) inputIndex[inputVal] = [];
      inputIndex[inputVal].push({ evt: events[idx], idx });
    }
  }

  for (let i = 0; i < events.length; i++) {
    const chain = [events[i]];
    const ts1 = events[i]._ts;

    for (const field of matchFields) {
      const val1 = events[i][field] || "";
      if (!val1) continue;

      // Fast path: exact match via index
      const candidates = inputIndex[val1];
      if (candidates) {
        for (const cand of candidates) {
          if (cand.idx <= i) continue;
          if (cand.evt._ts - ts1 > maxGapMs) continue;
          if (cand.evt.session_id === events[i].session_id) continue;
          chain.push(cand.evt);
        }
      }

      // Slow path for substring matches (only if no exact matches found)
      if (chain.length < 2) {
        for (let j = i + 1; j < events.length; j++) {
          if (events[j]._ts - ts1 > maxGapMs) break;
          if (events[j].session_id === events[i].session_id) continue;
          const val2 = events[j].input_data || "";
          if (val2 && val2 !== val1 && val2.indexOf(val1) >= 0) {
            chain.push(events[j]);
          }
        }
      }
    }

    if (chain.length >= 2) {
      groups.push({
        label: `chain:${events[i].agent_name || "?"}\u2192${chain[chain.length - 1].agent_name || "?"}`,
        events: chain,
        metadata: { match_fields: matchFields, chain_length: chain.length },
      });
    }
  }
  return groups;
}

/** Custom correlation by event_type and metadata pattern match. */
function correlateByCustom(events, config) {
  const types = config.event_types || [];

  let filtered = events;
  if (types.length > 0) {
    const typeSet = new Set(types);
    filtered = events.filter(e => typeSet.has(e.event_type));
  }
  if (filtered.length < 2) return [];

  const groupBy = config.group_by;
  if (groupBy) {
    const buckets = {};
    for (const evt of filtered) {
      const val = evt[groupBy] || "unknown";
      if (!buckets[val]) buckets[val] = [];
      buckets[val].push(evt);
    }

    const result = [];
    for (const [key, bucket] of Object.entries(buckets)) {
      if (bucket.length >= 2) {
        result.push({
          label: `${groupBy}=${key}`,
          events: bucket,
          metadata: { group_by: groupBy, value: key },
        });
      }
    }
    return result;
  }

  return [{
    label: `custom:${types.join("+")}`,
    events: filtered,
    metadata: { event_types: types },
  }];
}

// ── Persist correlation results ─────────────────────────────────────

function persistGroups(rule, groups) {
  const db = dbMod.getDb();
  const insertGroup = db.prepare(
    `INSERT OR IGNORE INTO correlation_groups (group_id, rule_id, label, created_at, metadata)
     VALUES (?, ?, ?, ?, ?)`
  );
  const insertMember = db.prepare(
    `INSERT OR IGNORE INTO correlation_members (group_id, event_id, session_id, role, added_at)
     VALUES (?, ?, ?, ?, ?)`
  );

  const timestamp = now();
  const persisted = [];

  const txn = db.transaction(() => {
    for (const group of groups) {
      const groupId = uid();
      insertGroup.run(groupId, rule.rule_id, group.label, timestamp, safeJsonStringify(group.metadata));
      for (let m = 0; m < group.events.length; m++) {
        const evt = group.events[m];
        insertMember.run(groupId, evt.event_id, evt.session_id, m === 0 ? "origin" : "member", timestamp);
      }
      persisted.push({ group_id: groupId, label: group.label, member_count: group.events.length });
    }
  });
  txn();

  return persisted;
}

// ── Routes ──────────────────────────────────────────────────────────

/** POST /rules — Create a correlation rule */
router.post("/rules", wrapRoute("create correlation rule", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const { body } = req;

  if (!body.name || typeof body.name !== "string" || !body.name.trim()) {
    return res.status(400).json({ error: "name is required and must be a non-empty string" });
  }
  if (!body.match_type) {
    return res.status(400).json({ error: "match_type is required" });
  }
  if (!CORRELATION_STRATEGIES[body.match_type]) {
    return res.status(400).json({ error: `match_type must be one of: ${VALID_TYPES.join(", ")}` });
  }

  const safeName = sanitizeString(body.name, MAX_NAME_LENGTH) || "unnamed";
  const safeDesc = sanitizeString(body.description || "", MAX_DESCRIPTION_LENGTH) || "";
  const safeAgentFilter = body.agent_filter
    ? sanitizeString(body.agent_filter, MAX_AGENT_FILTER_LENGTH)
    : null;

  const configStr = safeJsonStringify(body.config);
  if (configStr.length > MAX_CONFIG_SIZE) {
    return res.status(400).json({ error: `config is too large (max ${MAX_CONFIG_SIZE} bytes)` });
  }

  let priority = 0;
  if (body.priority !== undefined) {
    priority = parseInt(body.priority);
    if (!Number.isFinite(priority)) priority = 0;
    priority = Math.max(-100, Math.min(100, priority));
  }

  const ruleId = uid();
  const timestamp = now();
  db.prepare(
    `INSERT INTO correlation_rules (rule_id, name, description, match_type, config, agent_filter, priority, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(ruleId, safeName, safeDesc, body.match_type, configStr, safeAgentFilter, priority, timestamp, timestamp);

  res.status(201).json({ rule_id: ruleId, name: safeName, match_type: body.match_type, created_at: timestamp });
}));

/** GET /rules — List all correlation rules */
router.get("/rules", wrapRoute("list correlation rules", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  let query = "SELECT * FROM correlation_rules";
  const params = [];
  if (req.query.enabled !== undefined) {
    query += " WHERE enabled = ?";
    params.push(req.query.enabled === "true" ? 1 : 0);
  }
  query += " ORDER BY priority DESC, created_at DESC";

  const rules = db.prepare(query).all(...params);
  for (const rule of rules) rule.config = safeJsonParse(rule.config);
  res.json({ rules, total: rules.length });
}));

/** GET /rules/:ruleId — Get a specific rule */
router.get("/rules/:ruleId", wrapRoute("get correlation rule", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });
  rule.config = safeJsonParse(rule.config);
  const stats = db.prepare("SELECT COUNT(*) as group_count FROM correlation_groups WHERE rule_id = ?").get(rule.rule_id);
  rule.group_count = stats.group_count;
  res.json(rule);
}));

/** PATCH /rules/:ruleId — Update a rule */
router.patch("/rules/:ruleId", wrapRoute("update correlation rule", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });

  const fields = [];
  const values = [];

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
    if (!CORRELATION_STRATEGIES[req.body.match_type]) {
      return res.status(400).json({ error: `match_type must be one of: ${VALID_TYPES.join(", ")}` });
    }
    fields.push("match_type = ?");
    values.push(req.body.match_type);
  }
  if (req.body.config !== undefined) {
    const configStr = safeJsonStringify(req.body.config);
    if (configStr.length > MAX_CONFIG_SIZE) {
      return res.status(400).json({ error: `config is too large (max ${MAX_CONFIG_SIZE} bytes)` });
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
    let priority = parseInt(req.body.priority);
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
  db.prepare(`UPDATE correlation_rules SET ${fields.join(", ")} WHERE rule_id = ?`).run(...values);
  res.json({ updated: true, rule_id: req.params.ruleId });
}));

/** DELETE /rules/:ruleId — Delete a rule (cascades groups) */
router.delete("/rules/:ruleId", wrapRoute("delete correlation rule", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const result = db.prepare("DELETE FROM correlation_rules WHERE rule_id = ?").run(req.params.ruleId);
  if (result.changes === 0) return res.status(404).json({ error: "Rule not found" });
  res.json({ deleted: true, rule_id: req.params.ruleId });
}));

const MAX_GROUPS_PER_RUN = 500;

/** POST /rules/:ruleId/run — Execute a correlation rule */
router.post("/rules/:ruleId/run", wrapRoute("run correlation rule", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const rule = db.prepare("SELECT * FROM correlation_rules WHERE rule_id = ?").get(req.params.ruleId);
  if (!rule) return res.status(404).json({ error: "Rule not found" });

  let lookback = null;
  if (req.body.lookback_minutes !== undefined) {
    lookback = parseInt(req.body.lookback_minutes);
    if (!Number.isFinite(lookback) || lookback < 1 || lookback > MAX_LOOKBACK_MINUTES) {
      return res.status(400).json({ error: `lookback_minutes must be 1-${MAX_LOOKBACK_MINUTES} (max 7 days)` });
    }
  }

  let groups = runCorrelation(rule, lookback);
  if (groups.length > MAX_GROUPS_PER_RUN) {
    groups = groups.slice(0, MAX_GROUPS_PER_RUN);
  }

  let persisted = [];
  if (req.body.persist !== false) persisted = persistGroups(rule, groups);

  let totalEvts = 0;
  for (const g of groups) totalEvts += g.events.length;

  const outGroups = persisted.length > 0
    ? persisted
    : groups.map(g => ({ label: g.label, member_count: g.events.length, metadata: g.metadata }));

  res.json({
    rule_id: rule.rule_id, rule_name: rule.name, match_type: rule.match_type,
    groups_found: groups.length, total_events_correlated: totalEvts,
    groups: outGroups,
  });
}));

/** GET /groups — List correlation groups
 *  Member counts computed via LEFT JOIN subquery (eliminates N+1 queries). */
router.get("/groups", wrapRoute("list correlation groups", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();

  let whereClause = "";
  const filterParams = [];
  if (req.query.rule_id) {
    whereClause = " WHERE g.rule_id = ?";
    filterParams.push(req.query.rule_id);
  }

  const limit = parseLimit(req.query.limit, 50, 200);
  const offset = parseOffset(req.query.offset);

  const query = `
    SELECT g.*, r.name as rule_name, r.match_type,
      COALESCE(mc.member_count, 0) as member_count
    FROM correlation_groups g
    JOIN correlation_rules r ON g.rule_id = r.rule_id
    LEFT JOIN (
      SELECT group_id, COUNT(*) as member_count FROM correlation_members GROUP BY group_id
    ) mc ON g.group_id = mc.group_id
    ${whereClause}
    ORDER BY g.created_at DESC LIMIT ? OFFSET ?`;

  const groups = db.prepare(query).all(...filterParams, limit, offset);
  for (const g of groups) g.metadata = safeJsonParse(g.metadata);

  const totalQuery = `SELECT COUNT(*) as cnt FROM correlation_groups${req.query.rule_id ? " WHERE rule_id = ?" : ""}`;
  const total = req.query.rule_id
    ? db.prepare(totalQuery).get(req.query.rule_id).cnt
    : db.prepare(totalQuery).get().cnt;

  res.json({ groups, total, limit, offset });
}));

/** GET /groups/:groupId — Get group details with members */
router.get("/groups/:groupId", wrapRoute("get correlation group", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const group = db.prepare(
    `SELECT g.*, r.name as rule_name, r.match_type FROM correlation_groups g
     JOIN correlation_rules r ON g.rule_id = r.rule_id WHERE g.group_id = ?`
  ).get(req.params.groupId);
  if (!group) return res.status(404).json({ error: "Group not found" });
  group.metadata = safeJsonParse(group.metadata);

  const members = db.prepare(
    `SELECT m.*, e.event_type, e.timestamp, e.model, e.duration_ms, s.agent_name
     FROM correlation_members m
     JOIN events e ON m.event_id = e.event_id
     JOIN sessions s ON m.session_id = s.session_id
     WHERE m.group_id = ? ORDER BY e.timestamp ASC`
  ).all(req.params.groupId);

  group.members = members;
  group.member_count = members.length;
  res.json(group);
}));

/** DELETE /groups/:groupId — Delete a correlation group */
router.delete("/groups/:groupId", wrapRoute("delete correlation group", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const result = db.prepare("DELETE FROM correlation_groups WHERE group_id = ?").run(req.params.groupId);
  if (result.changes === 0) return res.status(404).json({ error: "Group not found" });
  res.json({ deleted: true, group_id: req.params.groupId });
}));

/** GET /stats — Correlation statistics */
router.get("/stats", wrapRoute("correlation stats", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const ruleCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_rules").get().cnt;
  const enabledCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_rules WHERE enabled = 1").get().cnt;
  const groupCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_groups").get().cnt;
  const memberCount = db.prepare("SELECT COUNT(*) as cnt FROM correlation_members").get().cnt;

  const byType = db.prepare(
    `SELECT r.match_type, COUNT(g.group_id) as groups FROM correlation_rules r
     LEFT JOIN correlation_groups g ON r.rule_id = g.rule_id GROUP BY r.match_type`
  ).all();

  res.json({
    total_rules: ruleCount, enabled_rules: enabledCount,
    total_groups: groupCount, total_correlated_events: memberCount,
    by_match_type: byType,
  });
}));

/** GET /event/:eventId — Find all correlations for an event */
router.get("/event/:eventId", wrapRoute("find event correlations", (req, res) => {
  ensureCorrelationTables();
  const db = dbMod.getDb();
  const memberships = db.prepare(
    `SELECT m.group_id, m.role, g.label, g.created_at, g.metadata, r.name as rule_name, r.match_type
     FROM correlation_members m JOIN correlation_groups g ON m.group_id = g.group_id
     JOIN correlation_rules r ON g.rule_id = r.rule_id WHERE m.event_id = ? ORDER BY g.created_at DESC`
  ).all(req.params.eventId);
  for (const m of memberships) m.metadata = safeJsonParse(m.metadata);
  res.json({ event_id: req.params.eventId, correlations: memberships, total: memberships.length });
}));

// ── Export for testing ──────────────────────────────────────────────
router._engine = {
  correlateByMetadata,
  correlateByTimeWindow,
  correlateByErrorCascade,
  correlateByCausalChain,
  correlateByCustom,
  runCorrelation,
  persistGroups,
  ensureCorrelationTables,
};

module.exports = router;
