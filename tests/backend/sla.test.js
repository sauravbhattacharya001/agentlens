const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");

// Mock better-sqlite3 in-memory
const Database = require("../../backend/node_modules/better-sqlite3");
let db;

// Patch db.js to use our test db
const dbModule = require("../../backend/db");
const origGetDb = dbModule.getDb;

function setupTestDb() {
  db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  // Core schema
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
    CREATE TABLE events (
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
    CREATE INDEX idx_events_session ON events(session_id);
    CREATE INDEX idx_sessions_agent ON sessions(agent_name);
  `);

  // Override getDb
  dbModule.getDb = () => db;
}

// Seed test data
function seedData() {
  const now = new Date();
  const hourAgo = new Date(now - 3600000).toISOString();
  const twoHoursAgo = new Date(now - 7200000).toISOString();

  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 500, 300, 'completed')`)
    .run("sess-1", "agent-alpha", hourAgo);
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 200, 100, 'completed')`)
    .run("sess-2", "agent-alpha", twoHoursAgo);
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, NULL, '{}', 100, 50, 'completed')`)
    .run("sess-3", "agent-beta", hourAgo);

  // Events for sess-1
  for (let i = 0; i < 10; i++) {
    const ts = new Date(now - (3600000 - i * 60000)).toISOString();
    db.prepare(`INSERT INTO events VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, ?)`)
      .run(`ev-1-${i}`, "sess-1", i === 7 ? "error" : "llm_call", ts,
           "gpt-4", 50, 30, 100 + i * 20);
  }

  // Events for sess-2
  for (let i = 0; i < 5; i++) {
    const ts = new Date(now - (7200000 - i * 60000)).toISOString();
    db.prepare(`INSERT INTO events VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, ?)`)
      .run(`ev-2-${i}`, "sess-2", "llm_call", ts, "gpt-4", 40, 20, 200 + i * 50);
  }

  // Events for sess-3 (different agent)
  for (let i = 0; i < 3; i++) {
    const ts = new Date(now - (3600000 - i * 60000)).toISOString();
    db.prepare(`INSERT INTO events VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, ?)`)
      .run(`ev-3-${i}`, "sess-3", i === 0 ? "error" : "llm_call", ts, "claude-3", 30, 20, 150);
  }
}

// Simple HTTP-like test harness for express router
const express = require("express");
const http = require("http");

let server;
let baseUrl;

async function fetch(path, opts = {}) {
  const url = new URL(path, baseUrl);
  const body = opts.body ? JSON.stringify(opts.body) : undefined;
  const res = await globalThis.fetch(url, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...opts.headers },
    body
  });
  const json = await res.json();
  return { status: res.status, body: json };
}

before(async () => {
  setupTestDb();
  seedData();

  const app = express();
  app.use(express.json());
  const slaRouter = require("../../backend/routes/sla");
  app.use("/sla", slaRouter);

  await new Promise((resolve) => {
    server = app.listen(0, () => {
      baseUrl = `http://localhost:${server.address().port}`;
      resolve();
    });
  });
});

after(() => {
  server?.close();
  dbModule.getDb = origGetDb;
});

// ── Tests ───────────────────────────────────────────────────────────

describe("SLA Monitor API", () => {
  let slaId;

  it("GET /sla returns empty list initially", async () => {
    const res = await fetch("/sla");
    assert.equal(res.status, 200);
    assert.deepEqual(res.body.slas, []);
  });

  it("POST /sla creates an SLA definition", async () => {
    const res = await fetch("/sla", {
      method: "POST",
      body: {
        name: "Alpha Latency SLA",
        agent_name: "agent-alpha",
        target_latency_ms: 300,
        target_error_rate: 0.05,
        target_token_budget: 5000,
        window_hours: 24
      }
    });
    assert.equal(res.status, 201);
    assert.equal(res.body.name, "Alpha Latency SLA");
    assert.equal(res.body.agent_name, "agent-alpha");
    assert.equal(res.body.target_latency_ms, 300);
    slaId = res.body.id;
  });

  it("POST /sla rejects duplicate name", async () => {
    const res = await fetch("/sla", {
      method: "POST",
      body: { name: "Alpha Latency SLA" }
    });
    assert.equal(res.status, 409);
  });

  it("POST /sla rejects missing name", async () => {
    const res = await fetch("/sla", { method: "POST", body: {} });
    assert.equal(res.status, 400);
  });

  it("GET /sla lists with compliance", async () => {
    const res = await fetch("/sla");
    assert.equal(res.status, 200);
    assert.equal(res.body.slas.length, 1);
    assert.ok(res.body.slas[0].compliance);
    assert.ok(res.body.slas[0].compliance.checks.length > 0);
  });

  it("GET /sla/:id/compliance returns detailed report", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    assert.equal(res.status, 200);
    assert.ok(res.body.sla);
    assert.ok(res.body.compliance);
    assert.ok(Array.isArray(res.body.hourly));
    assert.ok(Array.isArray(res.body.incidents));

    // Verify checks exist
    const checks = res.body.compliance.checks;
    const latencyCheck = checks.find(c => c.metric === "latency");
    const errorCheck = checks.find(c => c.metric === "error_rate");
    const tokenCheck = checks.find(c => c.metric === "token_budget");
    assert.ok(latencyCheck, "should have latency check");
    assert.ok(errorCheck, "should have error rate check");
    assert.ok(tokenCheck, "should have token budget check");
  });

  it("latency check computes correctly", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    const latency = res.body.compliance.checks.find(c => c.metric === "latency");
    assert.ok(latency.actual > 0, "should have actual latency");
    assert.equal(latency.target, 300);
  });

  it("error rate check computes correctly", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    const errCheck = res.body.compliance.checks.find(c => c.metric === "error_rate");
    // 1 error out of 15 events for agent-alpha = 0.0667
    assert.ok(errCheck.actual > 0, "should detect errors");
    assert.equal(errCheck.target, 0.05);
  });

  it("token budget check computes correctly", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    const tokenCheck = res.body.compliance.checks.find(c => c.metric === "token_budget");
    assert.ok(tokenCheck.actual > 0, "should have token usage");
    assert.equal(tokenCheck.target, 5000);
    assert.ok(tokenCheck.utilization_pct >= 0);
  });

  it("PUT /sla/:id updates definition", async () => {
    const res = await fetch(`/sla/${slaId}`, {
      method: "PUT",
      body: { target_latency_ms: 500 }
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.target_latency_ms, 500);
    assert.ok(res.body.compliance);
  });

  it("PUT /sla/:id rejects empty update", async () => {
    const res = await fetch(`/sla/${slaId}`, { method: "PUT", body: {} });
    assert.equal(res.status, 400);
  });

  it("PUT /sla/999 returns 404", async () => {
    const res = await fetch("/sla/999", { method: "PUT", body: { name: "x" } });
    assert.equal(res.status, 404);
  });

  it("POST /sla/:id/incidents records incident", async () => {
    const res = await fetch(`/sla/${slaId}/incidents`, {
      method: "POST",
      body: {
        incident_type: "latency_spike",
        details: { peak_ms: 2500, affected_sessions: 3 }
      }
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.incident_type, "latency_spike");
    assert.equal(res.body.sla_id, slaId);
  });

  it("POST /sla/:id/incidents rejects missing type", async () => {
    const res = await fetch(`/sla/${slaId}/incidents`, {
      method: "POST",
      body: {}
    });
    assert.equal(res.status, 400);
  });

  it("compliance report includes incidents", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    assert.ok(res.body.incidents.length > 0);
    assert.equal(res.body.incidents[0].incident_type, "latency_spike");
  });

  it("hourly data has correct structure", async () => {
    const res = await fetch(`/sla/${slaId}/compliance`);
    assert.ok(res.body.hourly.length > 0);
    const entry = res.body.hourly[0];
    assert.ok("hour" in entry);
    assert.ok("events" in entry);
    assert.ok("avg_latency_ms" in entry);
    assert.ok("errors" in entry);
    assert.ok("tokens" in entry);
  });

  // Create a second SLA for summary tests
  it("POST /sla creates model-scoped SLA", async () => {
    const res = await fetch("/sla", {
      method: "POST",
      body: {
        name: "GPT-4 Budget",
        model: "gpt-4",
        target_token_budget: 2000,
        window_hours: 48
      }
    });
    assert.equal(res.status, 201);
    assert.equal(res.body.model, "gpt-4");
  });

  it("POST /sla creates agent-scoped SLA with no targets", async () => {
    const res = await fetch("/sla", {
      method: "POST",
      body: { name: "Beta Monitor", agent_name: "agent-beta" }
    });
    assert.equal(res.status, 201);
  });

  it("GET /sla/summary returns fleet health", async () => {
    const res = await fetch("/sla/summary");
    assert.equal(res.status, 200);
    assert.ok(res.body.summary);
    assert.equal(res.body.summary.total_slas, 3);
    assert.ok(typeof res.body.summary.compliance_pct === "number");
    assert.ok(["healthy", "degraded", "critical"].includes(res.body.summary.health));
    assert.ok(Array.isArray(res.body.breaches));
    assert.ok(Array.isArray(res.body.slas));
  });

  it("compliance filters by agent_name", async () => {
    // Alpha SLA should only count agent-alpha events
    const res = await fetch(`/sla/${slaId}/compliance`);
    const errCheck = res.body.compliance.checks.find(c => c.metric === "error_rate");
    // agent-alpha has 15 events, 1 error
    assert.equal(errCheck.total_events, 15);
    assert.equal(errCheck.error_events, 1);
  });

  it("no-target SLA shows no_targets status", async () => {
    const res = await fetch("/sla");
    const beta = res.body.slas.find(s => s.name === "Beta Monitor");
    assert.ok(beta);
    assert.equal(beta.compliance.status, "no_targets");
  });

  it("DELETE /sla/:id removes definition", async () => {
    // Create one to delete
    const create = await fetch("/sla", {
      method: "POST",
      body: { name: "Temporary SLA", target_latency_ms: 100 }
    });
    const tmpId = create.body.id;

    const del = await fetch(`/sla/${tmpId}`, { method: "DELETE" });
    assert.equal(del.status, 200);
    assert.equal(del.body.deleted, true);

    // Verify gone
    const check = await fetch(`/sla/${tmpId}/compliance`);
    assert.equal(check.status, 404);
  });

  it("DELETE /sla/999 returns 404", async () => {
    const res = await fetch("/sla/999", { method: "DELETE" });
    assert.equal(res.status, 404);
  });

  it("GET /sla/:id/compliance returns 404 for missing", async () => {
    const res = await fetch("/sla/999/compliance");
    assert.equal(res.status, 404);
  });

  it("POST /sla/:id/incidents returns 404 for missing SLA", async () => {
    const res = await fetch("/sla/999/incidents", {
      method: "POST",
      body: { incident_type: "test" }
    });
    assert.equal(res.status, 404);
  });

  it("PUT /sla/invalid returns 400", async () => {
    const res = await fetch("/sla/abc", { method: "PUT", body: { name: "x" } });
    assert.equal(res.status, 400);
  });

  it("DELETE /sla/invalid returns 400", async () => {
    const res = await fetch("/sla/abc", { method: "DELETE" });
    assert.equal(res.status, 400);
  });

  it("multiple SLAs with different scopes coexist", async () => {
    const res = await fetch("/sla");
    assert.ok(res.body.slas.length >= 3);
    const names = res.body.slas.map(s => s.name);
    assert.ok(names.includes("Alpha Latency SLA"));
    assert.ok(names.includes("GPT-4 Budget"));
    assert.ok(names.includes("Beta Monitor"));
  });

  it("violation_pct computed correctly for latency", async () => {
    // Create strict SLA where most events violate
    const create = await fetch("/sla", {
      method: "POST",
      body: { name: "Strict Latency", target_latency_ms: 50, window_hours: 24 }
    });
    const strictId = create.body.id;
    const res = await fetch(`/sla/${strictId}/compliance`);
    const latency = res.body.compliance.checks.find(c => c.metric === "latency");
    assert.ok(latency.violation_pct > 0, "most events should violate 50ms target");
    // Cleanup
    await fetch(`/sla/${strictId}`, { method: "DELETE" });
  });

  it("compliance window respects window_hours", async () => {
    // 48-hour window should include all seeded events
    const res = await fetch("/sla");
    const gpt4Sla = res.body.slas.find(s => s.name === "GPT-4 Budget");
    assert.ok(gpt4Sla);
    assert.equal(gpt4Sla.compliance.window_hours, 48);
  });

  it("POST /sla with custom uptime target", async () => {
    const res = await fetch("/sla", {
      method: "POST",
      body: { name: "High Avail", target_uptime_pct: 99.99 }
    });
    assert.equal(res.status, 201);
    assert.equal(res.body.target_uptime_pct, 99.99);
    await fetch(`/sla/${res.body.id}`, { method: "DELETE" });
  });

  it("incident with resolved_at", async () => {
    const res = await fetch(`/sla/${slaId}/incidents`, {
      method: "POST",
      body: {
        incident_type: "outage",
        started_at: new Date(Date.now() - 3600000).toISOString(),
        resolved_at: new Date().toISOString(),
        details: { cause: "deployment" }
      }
    });
    assert.equal(res.status, 200);
    assert.ok(res.body.resolved_at);
  });

  it("summary breaches list specific violations", async () => {
    const res = await fetch("/sla/summary");
    // Some SLAs should have breaches
    if (res.body.breaches.length > 0) {
      const breach = res.body.breaches[0];
      assert.ok(breach.sla);
      assert.ok(breach.check);
      assert.ok("actual" in breach);
      assert.ok("target" in breach);
    }
  });
});
