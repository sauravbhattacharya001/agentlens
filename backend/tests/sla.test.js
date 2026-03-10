const Database = require("better-sqlite3");

function createTestDb() {
  const db = new Database(":memory:");
  db.pragma("foreign_keys = ON");
  db.exec(`
    CREATE TABLE sessions (session_id TEXT PRIMARY KEY, agent_name TEXT NOT NULL DEFAULT 'default-agent', started_at TEXT NOT NULL, ended_at TEXT, metadata TEXT DEFAULT '{}', total_tokens_in INTEGER DEFAULT 0, total_tokens_out INTEGER DEFAULT 0, status TEXT DEFAULT 'active');
    CREATE TABLE events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT 'generic', timestamp TEXT NOT NULL, input_data TEXT, output_data TEXT, model TEXT, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, tool_call TEXT, decision_trace TEXT, duration_ms REAL, FOREIGN KEY (session_id) REFERENCES sessions(session_id));
    CREATE TABLE sla_targets (agent_name TEXT NOT NULL, metric TEXT NOT NULL CHECK(metric IN ('p50_latency_ms','p95_latency_ms','p99_latency_ms','error_rate_pct','avg_tokens_in','avg_tokens_out','max_duration_ms','min_throughput')), threshold REAL NOT NULL, comparison TEXT NOT NULL DEFAULT 'lte' CHECK(comparison IN ('lte','gte','lt','gt','eq')), created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY (agent_name, metric));
    CREATE TABLE sla_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', violations TEXT NOT NULL DEFAULT '[]', compliance_pct REAL NOT NULL DEFAULT 100, created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE INDEX IF NOT EXISTS idx_sla_snapshots_agent ON sla_snapshots(agent_name);
  `);
  return db;
}

function seedSessions(db, agentName, count, status, hoursAgo) {
  const now = Date.now();
  for (let i = 0; i < count; i++) {
    const id = `sess-${agentName}-${status}-${i}`;
    const started = new Date(now - (hoursAgo || 1) * 3600000 + i * 1000).toISOString();
    db.prepare("INSERT INTO sessions VALUES (?,?,?,null,'{}',0,0,?)").run(id, agentName, started, status || "active");
    for (let j = 0; j < 3; j++) {
      const eid = `${id}-e${j}`;
      const dur = 100 + i * 50 + j * 30;
      db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out,duration_ms) VALUES (?,?,?,?,?,?,?,?)").run(
        eid, id, "llm_call", started, "gpt-4o", 500 + i * 100, 200 + i * 50, dur
      );
    }
  }
}

function checkViolation(value, threshold, comparison) {
  switch (comparison) {
    case "lte": return value > threshold;
    case "gte": return value < threshold;
    case "lt":  return value >= threshold;
    case "gt":  return value <= threshold;
    case "eq":  return value !== threshold;
    default:    return false;
  }
}

function percentile(arr, pct) {
  if (arr.length === 0) return 0;
  const idx = Math.ceil((pct / 100) * arr.length) - 1;
  return arr[Math.max(0, idx)];
}

describe("SLA Monitoring", () => {
  let db;
  beforeEach(() => { db = createTestDb(); });
  afterEach(() => { db.close(); });

  describe("Target CRUD", () => {
    test("insert a target", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "p95_latency_ms", 5000);
      const r = db.prepare("SELECT * FROM sla_targets WHERE agent_name = ?").get("code-agent");
      expect(r).toBeTruthy();
      expect(r.threshold).toBe(5000);
      expect(r.comparison).toBe("lte");
    });

    test("upsert updates threshold", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "p95_latency_ms", 5000);
      db.prepare(`INSERT INTO sla_targets (agent_name, metric, threshold, comparison, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(agent_name, metric) DO UPDATE SET threshold=excluded.threshold, comparison=excluded.comparison, updated_at=datetime('now')
      `).run("code-agent", "p95_latency_ms", 3000, "lt");
      const r = db.prepare("SELECT * FROM sla_targets WHERE agent_name=? AND metric=?").get("code-agent", "p95_latency_ms");
      expect(r.threshold).toBe(3000);
      expect(r.comparison).toBe("lt");
    });

    test("delete a target", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "p95_latency_ms", 5000);
      db.prepare("DELETE FROM sla_targets WHERE agent_name=? AND metric=?").run("code-agent", "p95_latency_ms");
      expect(db.prepare("SELECT * FROM sla_targets WHERE agent_name=?").get("code-agent")).toBeUndefined();
    });

    test("rejects invalid metric", () => {
      expect(() => {
        db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "invalid_metric", 100);
      }).toThrow();
    });

    test("rejects invalid comparison", () => {
      expect(() => {
        db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold, comparison) VALUES (?, ?, ?, ?)").run("code-agent", "p95_latency_ms", 100, "neq");
      }).toThrow();
    });

    test("multiple targets per agent", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "p95_latency_ms", 5000);
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("code-agent", "error_rate_pct", 5);
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold, comparison) VALUES (?, ?, ?, ?)").run("code-agent", "min_throughput", 10, "gte");
      const rows = db.prepare("SELECT * FROM sla_targets WHERE agent_name=?").all("code-agent");
      expect(rows.length).toBe(3);
    });

    test("targets for different agents are independent", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("agent-a", "p95_latency_ms", 5000);
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("agent-b", "p95_latency_ms", 10000);
      expect(db.prepare("SELECT threshold FROM sla_targets WHERE agent_name='agent-a'").get().threshold).toBe(5000);
      expect(db.prepare("SELECT threshold FROM sla_targets WHERE agent_name='agent-b'").get().threshold).toBe(10000);
    });

    test("all 8 metrics accepted", () => {
      const metrics = ["p50_latency_ms","p95_latency_ms","p99_latency_ms","error_rate_pct","avg_tokens_in","avg_tokens_out","max_duration_ms","min_throughput"];
      metrics.forEach((m, i) => {
        db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("all-metrics", m, (i + 1) * 100);
      });
      expect(db.prepare("SELECT COUNT(*) as c FROM sla_targets WHERE agent_name='all-metrics'").get().c).toBe(8);
    });
  });

  describe("Violation Detection", () => {
    test("lte: no violation when equal", () => expect(checkViolation(100, 100, "lte")).toBe(false));
    test("lte: no violation when below", () => expect(checkViolation(50, 100, "lte")).toBe(false));
    test("lte: violation when above", () => expect(checkViolation(150, 100, "lte")).toBe(true));
    test("gte: no violation when equal", () => expect(checkViolation(100, 100, "gte")).toBe(false));
    test("gte: no violation when above", () => expect(checkViolation(150, 100, "gte")).toBe(false));
    test("gte: violation when below", () => expect(checkViolation(50, 100, "gte")).toBe(true));
    test("lt: violation when equal", () => expect(checkViolation(100, 100, "lt")).toBe(true));
    test("lt: no violation when below", () => expect(checkViolation(50, 100, "lt")).toBe(false));
    test("gt: violation when equal", () => expect(checkViolation(100, 100, "gt")).toBe(true));
    test("gt: no violation when above", () => expect(checkViolation(150, 100, "gt")).toBe(false));
    test("eq: no violation when equal", () => expect(checkViolation(100, 100, "eq")).toBe(false));
    test("eq: violation when not equal", () => expect(checkViolation(99, 100, "eq")).toBe(true));
    test("unknown comparison defaults to no violation", () => expect(checkViolation(999, 1, "unknown")).toBe(false));
  });

  describe("Metrics Computation", () => {
    test("computes latency percentiles from events", () => {
      seedSessions(db, "test-agent", 5, "active", 1);
      const events = db.prepare("SELECT duration_ms FROM events ORDER BY duration_ms ASC").all();
      const durations = events.map(e => e.duration_ms).sort((a, b) => a - b);
      expect(durations.length).toBe(15);
      const p50 = percentile(durations, 50);
      const p95 = percentile(durations, 95);
      const p99 = percentile(durations, 99);
      expect(p50).toBeLessThanOrEqual(p95);
      expect(p95).toBeLessThanOrEqual(p99);
    });

    test("computes error rate from session status", () => {
      seedSessions(db, "err-agent", 3, "active", 1);
      seedSessions(db, "err-agent", 2, "error", 1);
      const sessions = db.prepare("SELECT status FROM sessions WHERE agent_name='err-agent'").all();
      const errCount = sessions.filter(s => s.status === "error").length;
      expect((errCount / sessions.length) * 100).toBe(40);
    });

    test("computes avg tokens", () => {
      seedSessions(db, "tok-agent", 3, "active", 1);
      const events = db.prepare(`SELECT tokens_in, tokens_out FROM events e JOIN sessions s ON e.session_id=s.session_id WHERE s.agent_name='tok-agent'`).all();
      const avgIn = events.reduce((s, e) => s + e.tokens_in, 0) / events.length;
      expect(avgIn).toBeGreaterThan(0);
    });

    test("no sessions returns empty array", () => {
      expect(db.prepare("SELECT * FROM sessions WHERE agent_name='nonexistent'").all()).toHaveLength(0);
    });

    test("percentile of empty array returns 0", () => {
      expect(percentile([], 50)).toBe(0);
    });

    test("percentile of single element", () => {
      expect(percentile([42], 50)).toBe(42);
      expect(percentile([42], 99)).toBe(42);
    });

    test("max duration is last sorted element", () => {
      seedSessions(db, "max-agent", 4, "active", 1);
      const durations = db.prepare("SELECT duration_ms FROM events ORDER BY duration_ms ASC").all().map(e => e.duration_ms);
      expect(durations[durations.length - 1]).toBeGreaterThan(durations[0]);
    });
  });

  describe("Snapshots", () => {
    test("insert and retrieve snapshot", () => {
      const metrics = { p50_latency_ms: 200, p95_latency_ms: 800 };
      const violations = [{ metric: "p95_latency_ms", threshold: 500, actual: 800 }];
      db.prepare("INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct) VALUES (?,?,?,?,?,?)").run(
        "code-agent", "2026-03-10T00:00:00Z", "2026-03-10T12:00:00Z", JSON.stringify(metrics), JSON.stringify(violations), 50
      );
      const row = db.prepare("SELECT * FROM sla_snapshots WHERE agent_name=?").get("code-agent");
      expect(row).toBeTruthy();
      expect(JSON.parse(row.metrics).p50_latency_ms).toBe(200);
      expect(JSON.parse(row.violations)).toHaveLength(1);
      expect(row.compliance_pct).toBe(50);
    });

    test("multiple snapshots ordered by created_at desc", () => {
      for (let i = 0; i < 5; i++) {
        db.prepare("INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct, created_at) VALUES (?,?,?,?,?,?,?)").run(
          "agent", `2026-03-0${i+1}T00:00:00Z`, `2026-03-0${i+1}T12:00:00Z`, "{}", "[]", 100 - i * 5, `2026-03-0${i+1}T12:00:00Z`
        );
      }
      const rows = db.prepare("SELECT * FROM sla_snapshots WHERE agent_name=? ORDER BY created_at DESC").all("agent");
      expect(rows.length).toBe(5);
      expect(rows[0].compliance_pct).toBe(80);
    });

    test("snapshots scoped to agent", () => {
      db.prepare("INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct) VALUES (?,?,?,?,?,?)").run("a", "2026-03-10T00:00:00Z", "2026-03-10T12:00:00Z", "{}", "[]", 100);
      db.prepare("INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct) VALUES (?,?,?,?,?,?)").run("b", "2026-03-10T00:00:00Z", "2026-03-10T12:00:00Z", "{}", "[]", 50);
      expect(db.prepare("SELECT * FROM sla_snapshots WHERE agent_name='a'").all()).toHaveLength(1);
    });
  });

  describe("Compliance Calculation", () => {
    test("100% when no violations", () => {
      expect(Math.round(((3 - 0) / 3) * 10000) / 100).toBe(100);
    });
    test("75% with 1 of 4 violated", () => {
      expect(Math.round(((4 - 1) / 4) * 10000) / 100).toBe(75);
    });
    test("0% when all violated", () => {
      expect(Math.round(((3 - 3) / 3) * 10000) / 100).toBe(0);
    });
    test("33.33% with 2 of 3 violated", () => {
      expect(Math.round(((3 - 2) / 3) * 10000) / 100).toBeCloseTo(33.33);
    });
    test("50% with 1 of 2 violated", () => {
      expect(Math.round(((2 - 1) / 2) * 10000) / 100).toBe(50);
    });
  });

  describe("End-to-End Flow", () => {
    test("define targets, seed data, check compliance, read history", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("e2e-agent", "p95_latency_ms", 500);
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("e2e-agent", "error_rate_pct", 10);
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold, comparison) VALUES (?, ?, ?, ?)").run("e2e-agent", "min_throughput", 1, "gte");

      seedSessions(db, "e2e-agent", 8, "active", 1);
      seedSessions(db, "e2e-agent", 2, "error", 1);

      const sessions = db.prepare("SELECT status FROM sessions WHERE agent_name='e2e-agent'").all();
      expect(sessions.length).toBe(10);
      const errRate = (sessions.filter(s => s.status === "error").length / sessions.length) * 100;
      expect(errRate).toBe(20);

      // error_rate_pct violates (20 > 10)
      expect(checkViolation(errRate, 10, "lte")).toBe(true);

      const violations = [{ metric: "error_rate_pct", threshold: 10, actual: 20 }];
      const compliancePct = Math.round(((3 - 1) / 3) * 10000) / 100;
      db.prepare("INSERT INTO sla_snapshots (agent_name, window_start, window_end, metrics, violations, compliance_pct) VALUES (?,?,?,?,?,?)").run(
        "e2e-agent", "2026-03-10T00:00:00Z", "2026-03-10T12:00:00Z", JSON.stringify({ error_rate_pct: errRate }), JSON.stringify(violations), compliancePct
      );

      const history = db.prepare("SELECT * FROM sla_snapshots WHERE agent_name='e2e-agent'").all();
      expect(history.length).toBe(1);
      expect(history[0].compliance_pct).toBeCloseTo(66.67);
      expect(JSON.parse(history[0].violations)).toHaveLength(1);
    });

    test("compliant agent has 100% and no violations", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold) VALUES (?, ?, ?)").run("good-agent", "error_rate_pct", 50);
      seedSessions(db, "good-agent", 10, "active", 1);

      const sessions = db.prepare("SELECT status FROM sessions WHERE agent_name='good-agent'").all();
      const errRate = (sessions.filter(s => s.status === "error").length / sessions.length) * 100;
      expect(errRate).toBe(0);
      expect(checkViolation(errRate, 50, "lte")).toBe(false);
    });

    test("gte comparison for min_throughput", () => {
      db.prepare("INSERT INTO sla_targets (agent_name, metric, threshold, comparison) VALUES (?, ?, ?, ?)").run("tp-agent", "min_throughput", 100, "gte");
      // 5 sessions in 1 hour = 5/hr throughput, threshold is 100 → violation
      expect(checkViolation(5, 100, "gte")).toBe(true);
      // 200 sessions/hr → no violation
      expect(checkViolation(200, 100, "gte")).toBe(false);
    });
  });
});
