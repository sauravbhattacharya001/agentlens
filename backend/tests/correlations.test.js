/* ── Trace Correlation Rules — Backend Tests ─────────────────────────── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.pragma("journal_mode = WAL");
      mockDb.pragma("foreign_keys = ON");
      mockDb.exec(`
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
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const correlationsRouter = require("../routes/correlations");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/correlations", correlationsRouter);
  return app;
}

const now = new Date();
function minutesAgo(n) {
  return new Date(now - n * 60000).toISOString();
}

function seedCorrelationData() {
  const { getDb } = require("../db");
  const db = getDb();

  // Clean up previous test data
  db.exec("DELETE FROM events; DELETE FROM sessions;");

  // Two agents
  db.prepare("INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 0, 0, 'active')").run("s1", "agent-alpha", minutesAgo(50));
  db.prepare("INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 0, 0, 'active')").run("s2", "agent-beta", minutesAgo(50));
  db.prepare("INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 0, 0, 'active')").run("s3", "agent-gamma", minutesAgo(50));

  // Events with shared request_id in input_data
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 10, 20, NULL, NULL, 100)").run(
    "e1", "s1", "llm_call", minutesAgo(10), '{"request_id":"req-42"}', '{"result":"ok"}', "gpt-4"
  );
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 10, 20, NULL, NULL, 200)").run(
    "e2", "s2", "llm_call", minutesAgo(9), '{"request_id":"req-42"}', '{"result":"done"}', "gpt-4"
  );
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 5, 10, NULL, NULL, 50)").run(
    "e3", "s1", "tool_call", minutesAgo(8), '{"request_id":"req-99"}', '{}', "gpt-4"
  );

  // Error events for cascade detection
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, 0)").run(
    "e4", "s1", "error", minutesAgo(5), '{}', '{}', null
  );
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, 0)").run(
    "e5", "s2", "error", minutesAgo(4.5), '{}', '{}', null
  );
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, 0)").run(
    "e6", "s3", "exception", minutesAgo(4), '{}', '{}', null
  );

  // Non-error events at similar times
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 10, 20, NULL, NULL, 100)").run(
    "e7", "s1", "llm_call", minutesAgo(5), '{}', '{}', "gpt-4"
  );
  db.prepare("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, 10, 20, NULL, NULL, 100)").run(
    "e8", "s2", "llm_call", minutesAgo(4.9), '{}', '{}', "gpt-4"
  );
}

let app;

beforeAll(() => {
  app = createApp();
  // Force table creation
  correlationsRouter._engine.ensureCorrelationTables();
  seedCorrelationData();
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

// ── Rule CRUD ───────────────────────────────────────────────────────

describe("POST /correlations/rules", () => {
  test("creates a metadata_key rule", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "Shared Request ID", match_type: "metadata_key", config: { key: "request_id" } });
    expect(res.status).toBe(201);
    expect(res.body.rule_id).toBeTruthy();
    expect(res.body.name).toBe("Shared Request ID");
    expect(res.body.match_type).toBe("metadata_key");
  });

  test("creates a time_window rule", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "Burst Detector", match_type: "time_window", config: { window_seconds: 120, min_events: 2 } });
    expect(res.status).toBe(201);
  });

  test("creates an error_cascade rule", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "Error Cascade", match_type: "error_cascade", config: { cascade_window_seconds: 60 } });
    expect(res.status).toBe(201);
  });

  test("creates a custom rule", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "By Model", match_type: "custom", config: { event_types: ["llm_call"], group_by: "model" } });
    expect(res.status).toBe(201);
  });

  test("rejects missing name", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ match_type: "metadata_key" });
    expect(res.status).toBe(400);
  });

  test("rejects missing match_type", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "Test" });
    expect(res.status).toBe(400);
  });

  test("rejects invalid match_type", async () => {
    const res = await request(app)
      .post("/correlations/rules")
      .send({ name: "Test", match_type: "invalid_type" });
    expect(res.status).toBe(400);
  });
});

describe("GET /correlations/rules", () => {
  test("lists all rules", async () => {
    const res = await request(app).get("/correlations/rules");
    expect(res.status).toBe(200);
    expect(res.body.rules.length).toBeGreaterThanOrEqual(4);
    expect(res.body.total).toBe(res.body.rules.length);
  });

  test("filters by enabled", async () => {
    const res = await request(app).get("/correlations/rules?enabled=true");
    expect(res.status).toBe(200);
    expect(res.body.rules.every(function(r) { return r.enabled === 1; })).toBe(true);
  });
});

describe("GET /correlations/rules/:ruleId", () => {
  test("gets a specific rule", async () => {
    const list = await request(app).get("/correlations/rules");
    const ruleId = list.body.rules[0].rule_id;

    const res = await request(app).get("/correlations/rules/" + ruleId);
    expect(res.status).toBe(200);
    expect(res.body.rule_id).toBe(ruleId);
    expect(res.body.group_count).toBeDefined();
  });

  test("returns 404 for unknown rule", async () => {
    const res = await request(app).get("/correlations/rules/nonexistent");
    expect(res.status).toBe(404);
  });
});

describe("PATCH /correlations/rules/:ruleId", () => {
  test("updates rule name", async () => {
    const list = await request(app).get("/correlations/rules");
    var ruleId = list.body.rules[0].rule_id;

    const res = await request(app)
      .patch("/correlations/rules/" + ruleId)
      .send({ name: "Updated Name" });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(true);

    const get = await request(app).get("/correlations/rules/" + ruleId);
    expect(get.body.name).toBe("Updated Name");
  });

  test("disables a rule", async () => {
    const list = await request(app).get("/correlations/rules");
    var ruleId = list.body.rules[0].rule_id;

    await request(app).patch("/correlations/rules/" + ruleId).send({ enabled: 0 });
    const get = await request(app).get("/correlations/rules/" + ruleId);
    expect(get.body.enabled).toBe(0);

    // Re-enable for other tests
    await request(app).patch("/correlations/rules/" + ruleId).send({ enabled: 1 });
  });

  test("rejects empty update", async () => {
    const list = await request(app).get("/correlations/rules");
    var ruleId = list.body.rules[0].rule_id;
    const res = await request(app).patch("/correlations/rules/" + ruleId).send({});
    expect(res.status).toBe(400);
  });

  test("returns 404 for unknown rule", async () => {
    const res = await request(app).patch("/correlations/rules/nonexistent").send({ name: "x" });
    expect(res.status).toBe(404);
  });
});

describe("DELETE /correlations/rules/:ruleId", () => {
  test("deletes a rule", async () => {
    const create = await request(app)
      .post("/correlations/rules")
      .send({ name: "Temp", match_type: "custom", config: {} });
    var ruleId = create.body.rule_id;

    const res = await request(app).delete("/correlations/rules/" + ruleId);
    expect(res.status).toBe(200);
    expect(res.body.deleted).toBe(true);

    const get = await request(app).get("/correlations/rules/" + ruleId);
    expect(get.status).toBe(404);
  });

  test("returns 404 for unknown rule", async () => {
    const res = await request(app).delete("/correlations/rules/nonexistent");
    expect(res.status).toBe(404);
  });
});

// ── Correlation execution ───────────────────────────────────────────

describe("POST /correlations/rules/:ruleId/run", () => {
  test("runs metadata_key correlation and finds groups", async () => {
    const list = await request(app).get("/correlations/rules");
    var metaRule = list.body.rules.find(function(r) { return r.match_type === "metadata_key"; });

    const res = await request(app)
      .post("/correlations/rules/" + metaRule.rule_id + "/run")
      .send({ lookback_minutes: 60, persist: true });
    expect(res.status).toBe(200);
    expect(res.body.groups_found).toBeGreaterThanOrEqual(1);
    expect(res.body.total_events_correlated).toBeGreaterThanOrEqual(2);
  });

  test("runs error_cascade correlation", async () => {
    const list = await request(app).get("/correlations/rules");
    var cascadeRule = list.body.rules.find(function(r) { return r.match_type === "error_cascade"; });

    const res = await request(app)
      .post("/correlations/rules/" + cascadeRule.rule_id + "/run")
      .send({ lookback_minutes: 60 });
    expect(res.status).toBe(200);
    // Should find the error cascade across agents
    expect(res.body.groups_found).toBeGreaterThanOrEqual(1);
  });

  test("runs time_window correlation", async () => {
    const list = await request(app).get("/correlations/rules");
    var twRule = list.body.rules.find(function(r) { return r.match_type === "time_window"; });

    const res = await request(app)
      .post("/correlations/rules/" + twRule.rule_id + "/run")
      .send({ lookback_minutes: 60 });
    expect(res.status).toBe(200);
    expect(res.body.groups_found).toBeGreaterThanOrEqual(0);
  });

  test("runs custom correlation by model", async () => {
    const list = await request(app).get("/correlations/rules");
    var customRule = list.body.rules.find(function(r) { return r.match_type === "custom"; });

    const res = await request(app)
      .post("/correlations/rules/" + customRule.rule_id + "/run")
      .send({ lookback_minutes: 60 });
    expect(res.status).toBe(200);
    expect(res.body.groups_found).toBeGreaterThanOrEqual(1);
  });

  test("returns 404 for unknown rule", async () => {
    const res = await request(app)
      .post("/correlations/rules/nonexistent/run")
      .send({});
    expect(res.status).toBe(404);
  });
});

// ── Groups ──────────────────────────────────────────────────────────

describe("GET /correlations/groups", () => {
  test("lists persisted groups", async () => {
    const res = await request(app).get("/correlations/groups");
    expect(res.status).toBe(200);
    expect(res.body.groups.length).toBeGreaterThanOrEqual(1);
    expect(res.body.total).toBeGreaterThanOrEqual(1);
  });

  test("filters by rule_id", async () => {
    const rules = await request(app).get("/correlations/rules");
    var ruleId = rules.body.rules[0].rule_id;

    const res = await request(app).get("/correlations/groups?rule_id=" + ruleId);
    expect(res.status).toBe(200);
    for (var i = 0; i < res.body.groups.length; i++) {
      expect(res.body.groups[i].rule_id).toBe(ruleId);
    }
  });

  test("respects limit and offset", async () => {
    const res = await request(app).get("/correlations/groups?limit=1&offset=0");
    expect(res.status).toBe(200);
    expect(res.body.groups.length).toBeLessThanOrEqual(1);
    expect(res.body.limit).toBe(1);
    expect(res.body.offset).toBe(0);
  });
});

describe("GET /correlations/groups/:groupId", () => {
  test("gets group with members", async () => {
    const groups = await request(app).get("/correlations/groups");
    if (groups.body.groups.length === 0) return; // Skip if no groups
    var groupId = groups.body.groups[0].group_id;

    const res = await request(app).get("/correlations/groups/" + groupId);
    expect(res.status).toBe(200);
    expect(res.body.group_id).toBe(groupId);
    expect(res.body.members).toBeDefined();
    expect(res.body.member_count).toBeGreaterThanOrEqual(2);
  });

  test("returns 404 for unknown group", async () => {
    const res = await request(app).get("/correlations/groups/nonexistent");
    expect(res.status).toBe(404);
  });
});

describe("DELETE /correlations/groups/:groupId", () => {
  test("returns 404 for unknown group", async () => {
    const res = await request(app).delete("/correlations/groups/nonexistent");
    expect(res.status).toBe(404);
  });
});

// ── Stats ───────────────────────────────────────────────────────────

describe("GET /correlations/stats", () => {
  test("returns correlation statistics", async () => {
    const res = await request(app).get("/correlations/stats");
    expect(res.status).toBe(200);
    expect(res.body.total_rules).toBeGreaterThanOrEqual(4);
    expect(res.body.enabled_rules).toBeGreaterThanOrEqual(1);
    expect(res.body.total_groups).toBeGreaterThanOrEqual(0);
    expect(res.body.by_match_type).toBeDefined();
  });
});

// ── Event lookup ────────────────────────────────────────────────────

describe("GET /correlations/event/:eventId", () => {
  test("finds correlations for a correlated event", async () => {
    const res = await request(app).get("/correlations/event/e1");
    expect(res.status).toBe(200);
    expect(res.body.event_id).toBe("e1");
    // e1 should be in at least one group (shared request_id)
    expect(res.body.total).toBeGreaterThanOrEqual(1);
  });

  test("returns empty for uncorrelated event", async () => {
    const res = await request(app).get("/correlations/event/nonexistent");
    expect(res.status).toBe(200);
    expect(res.body.total).toBe(0);
  });
});

// ── Engine unit tests ───────────────────────────────────────────────

describe("correlateByMetadata", () => {
  var engine = correlationsRouter._engine;

  test("groups events by shared key value", () => {
    var events = [
      { event_id: "a", session_id: "s1", input_data: '{"user":"alice"}' },
      { event_id: "b", session_id: "s2", input_data: '{"user":"alice"}' },
      { event_id: "c", session_id: "s1", input_data: '{"user":"bob"}' },
    ];
    var groups = engine.correlateByMetadata(events, { key: "user" });
    expect(groups.length).toBe(1);
    expect(groups[0].label).toBe("user=alice");
    expect(groups[0].events.length).toBe(2);
  });

  test("returns empty for no shared values", () => {
    var events = [
      { event_id: "a", session_id: "s1", input_data: '{"user":"alice"}' },
      { event_id: "b", session_id: "s2", input_data: '{"user":"bob"}' },
    ];
    var groups = engine.correlateByMetadata(events, { key: "user" });
    expect(groups.length).toBe(0);
  });

  test("returns empty for missing key", () => {
    var events = [
      { event_id: "a", session_id: "s1", input_data: '{}' },
    ];
    var groups = engine.correlateByMetadata(events, { key: "missing" });
    expect(groups.length).toBe(0);
  });

  test("searches output_data and decision_trace too", () => {
    var events = [
      { event_id: "a", session_id: "s1", input_data: '{}', output_data: '{"trace_id":"t1"}' },
      { event_id: "b", session_id: "s2", input_data: '{}', decision_trace: '{"trace_id":"t1"}' },
    ];
    var groups = engine.correlateByMetadata(events, { key: "trace_id" });
    expect(groups.length).toBe(1);
    expect(groups[0].events.length).toBe(2);
  });
});

describe("correlateByTimeWindow", () => {
  var engine = correlationsRouter._engine;

  test("groups events in the same time window", () => {
    var base = Date.now();
    var events = [
      { event_id: "a", session_id: "s1", event_type: "llm_call", timestamp: new Date(base).toISOString() },
      { event_id: "b", session_id: "s2", event_type: "llm_call", timestamp: new Date(base + 3000).toISOString() },
      { event_id: "c", session_id: "s1", event_type: "llm_call", timestamp: new Date(base + 60000).toISOString() },
    ];
    var groups = engine.correlateByTimeWindow(events, { window_seconds: 10, min_events: 2 });
    expect(groups.length).toBe(1);
    expect(groups[0].events.length).toBe(2);
  });

  test("filters by event_type", () => {
    var base = Date.now();
    var events = [
      { event_id: "a", session_id: "s1", event_type: "error", timestamp: new Date(base).toISOString() },
      { event_id: "b", session_id: "s2", event_type: "llm_call", timestamp: new Date(base + 1000).toISOString() },
      { event_id: "c", session_id: "s1", event_type: "error", timestamp: new Date(base + 2000).toISOString() },
    ];
    var groups = engine.correlateByTimeWindow(events, { window_seconds: 10, event_type_filter: "error", min_events: 2 });
    expect(groups.length).toBe(1);
    expect(groups[0].events.length).toBe(2);
  });
});

describe("correlateByErrorCascade", () => {
  var engine = correlationsRouter._engine;

  test("detects cross-agent error cascades", () => {
    var base = Date.now();
    var events = [
      { event_id: "a", session_id: "s1", agent_name: "alpha", event_type: "error", timestamp: new Date(base).toISOString() },
      { event_id: "b", session_id: "s2", agent_name: "beta", event_type: "error", timestamp: new Date(base + 5000).toISOString() },
      { event_id: "c", session_id: "s3", agent_name: "gamma", event_type: "error", timestamp: new Date(base + 10000).toISOString() },
    ];
    var groups = engine.correlateByErrorCascade(events, { cascade_window_seconds: 30 });
    expect(groups.length).toBe(1);
    expect(groups[0].events.length).toBe(3);
    expect(groups[0].metadata.source_agent).toBe("alpha");
    expect(groups[0].metadata.affected_agents.length).toBe(2);
  });

  test("does not group same-agent errors", () => {
    var base = Date.now();
    var events = [
      { event_id: "a", session_id: "s1", agent_name: "alpha", event_type: "error", timestamp: new Date(base).toISOString() },
      { event_id: "b", session_id: "s1", agent_name: "alpha", event_type: "error", timestamp: new Date(base + 5000).toISOString() },
    ];
    var groups = engine.correlateByErrorCascade(events, { cascade_window_seconds: 30 });
    expect(groups.length).toBe(0);
  });
});

describe("correlateByCustom", () => {
  var engine = correlationsRouter._engine;

  test("groups by field value", () => {
    var events = [
      { event_id: "a", session_id: "s1", event_type: "llm_call", model: "gpt-4" },
      { event_id: "b", session_id: "s2", event_type: "llm_call", model: "gpt-4" },
      { event_id: "c", session_id: "s1", event_type: "llm_call", model: "claude" },
    ];
    var groups = engine.correlateByCustom(events, { event_types: ["llm_call"], group_by: "model" });
    expect(groups.length).toBe(1); // only gpt-4 has 2+
    expect(groups[0].label).toBe("model=gpt-4");
  });

  test("returns all matching events without group_by", () => {
    var events = [
      { event_id: "a", session_id: "s1", event_type: "llm_call" },
      { event_id: "b", session_id: "s2", event_type: "llm_call" },
    ];
    var groups = engine.correlateByCustom(events, { event_types: ["llm_call"] });
    expect(groups.length).toBe(1);
    expect(groups[0].events.length).toBe(2);
  });
});
