const Database = require("better-sqlite3");

// ── Shared in-memory DB, injected via the mocked `../db` module ──────
let mockDb;
jest.mock("../db", () => ({
  getDb: () => mockDb,
}));

const express = require("express");
const request = require("supertest");
const diffRouter = require("../routes/diff");

function createSchema(db) {
  db.pragma("foreign_keys = ON");
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
      FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );
  `);
}

function insertSession(db, id, agent, status = "completed") {
  db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)"
  ).run(id, agent, "2025-01-01T00:00:00Z", status);
}

let eventSeq = 0;
function insertEvent(db, sessionId, type, opts = {}) {
  eventSeq += 1;
  db.prepare(
    `INSERT INTO events
      (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, tool_call, duration_ms)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(
    `evt-${eventSeq}`,
    sessionId,
    type,
    opts.timestamp || `2025-01-01T00:00:${String(eventSeq).padStart(2, "0")}Z`,
    opts.model || null,
    opts.tokens_in ?? 0,
    opts.tokens_out ?? 0,
    opts.tool_call ? JSON.stringify(opts.tool_call) : null,
    opts.duration_ms ?? null
  );
}

/*
 * Route tests: mount the real `routes/diff.js` behind supertest with a real
 * in-memory better-sqlite3 DB injected through the mocked `../db` module.
 * This exercises the full HTTP surface — validation, 404s, token/tool/model
 * deltas, LCS alignment, and truncation — and is measured by jest coverage
 * (the previous node:test file was excluded from jest's coverage run).
 */
describe("GET /diff", () => {
  let app;

  beforeAll(() => {
    app = express();
    app.use(express.json());
    app.use("/diff", diffRouter);
  });

  beforeEach(() => {
    if (mockDb) mockDb.close();
    mockDb = new Database(":memory:");
    createSchema(mockDb);

    insertSession(mockDb, "sess-base", "agent-a");
    insertEvent(mockDb, "sess-base", "llm_call", { tokens_in: 100, tokens_out: 50, model: "gpt-4", duration_ms: 200 });
    insertEvent(mockDb, "sess-base", "tool_call", { tokens_in: 20, tokens_out: 10, tool_call: { tool_name: "search" }, duration_ms: 50 });
    insertEvent(mockDb, "sess-base", "llm_call", { tokens_in: 80, tokens_out: 40, model: "gpt-4", duration_ms: 150 });

    insertSession(mockDb, "sess-cand", "agent-b");
    insertEvent(mockDb, "sess-cand", "llm_call", { tokens_in: 120, tokens_out: 60, model: "gpt-4o", duration_ms: 300 });
    insertEvent(mockDb, "sess-cand", "tool_call", { tokens_in: 25, tokens_out: 15, tool_call: { tool_name: "search" }, duration_ms: 50 });
    insertEvent(mockDb, "sess-cand", "tool_call", { tokens_in: 30, tokens_out: 20, tool_call: { tool_name: "calculator" }, duration_ms: 60 });
    insertEvent(mockDb, "sess-cand", "llm_call", { tokens_in: 90, tokens_out: 45, model: "gpt-4o", duration_ms: 150 });
  });

  afterAll(() => {
    if (mockDb) {
      mockDb.close();
      mockDb = null;
    }
  });

  test("returns 400 when params are missing", async () => {
    const res = await request(app).get("/diff");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/required/);
  });

  test("returns 400 when only baseline is given", async () => {
    const res = await request(app).get("/diff?baseline=sess-base");
    expect(res.status).toBe(400);
  });

  test("returns 400 for a structurally invalid session ID", async () => {
    const res = await request(app).get("/diff?baseline=bad%20id!&candidate=sess-cand");
    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid session ID format");
  });

  test("returns 400 when diffing a session with itself", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-base");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/itself/);
  });

  test("returns 404 for a missing baseline session", async () => {
    const res = await request(app).get("/diff?baseline=nonexist&candidate=sess-cand");
    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/Baseline/);
  });

  test("returns 404 for a missing candidate session", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=nonexist");
    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/Candidate/);
  });

  test("computes correct token deltas", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.status).toBe(200);
    const d = res.body.deltas;
    // Baseline: in=200, out=100. Candidate: in=265, out=140
    expect(d.tokens_in).toBe(265 - 200);
    expect(d.tokens_out).toBe(140 - 100);
    expect(d.tokens_total).toBe((265 + 140) - (200 + 100));
  });

  test("computes correct event count delta", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.deltas.event_count).toBe(1); // 4 - 3
  });

  test("reports baseline and candidate session summaries", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.baseline.session_id).toBe("sess-base");
    expect(res.body.candidate.session_id).toBe("sess-cand");
    expect(res.body.baseline.agent_name).toBe("agent-a");
    expect(res.body.candidate.agent_name).toBe("agent-b");
    expect(res.body.baseline.event_count).toBe(3);
    expect(res.body.candidate.event_count).toBe(4);
  });

  test("detects added / common tools and counts", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.tools.added).toContain("calculator");
    expect(res.body.tools.common).toContain("search");
    expect(res.body.tools.removed).toEqual([]);
    expect(res.body.tools.baseline_counts.search).toBe(1);
    expect(res.body.tools.candidate_counts.calculator).toBe(1);
  });

  test("detects removed tools when the candidate drops one", async () => {
    // Add a baseline-only tool so the candidate is missing it.
    insertEvent(mockDb, "sess-base", "tool_call", { tool_call: { tool_name: "grep" } });
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.tools.removed).toContain("grep");
  });

  test("reports per-model usage for both sessions", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.models.baseline["gpt-4"]).toBeGreaterThan(0);
    expect(res.body.models.candidate["gpt-4o"]).toBeGreaterThan(0);
  });

  test("computes a similarity ratio between 0 and 1", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    expect(res.body.similarity).toBeGreaterThanOrEqual(0);
    expect(res.body.similarity).toBeLessThanOrEqual(1);
  });

  test("aligns events and flags modified + added statuses with change details", async () => {
    const res = await request(app).get("/diff?baseline=sess-base&candidate=sess-cand");
    const statuses = new Set(res.body.alignment.map((a) => a.status));
    expect(statuses.has("added")).toBe(true);
    expect(statuses.has("modified") || statuses.has("matched")).toBe(true);

    const modified = res.body.alignment.filter((a) => a.status === "modified");
    expect(modified.length).toBeGreaterThan(0);
    // The two llm_calls changed model gpt-4 -> gpt-4o, so a model change is recorded.
    expect(modified.some((m) => m.changes && m.changes.model)).toBe(true);
  });

  test("reports matched (unchanged) events when sessions are identical in shape", async () => {
    insertSession(mockDb, "sess-x", "agent-x");
    insertSession(mockDb, "sess-y", "agent-y");
    insertEvent(mockDb, "sess-x", "llm_call", { tokens_in: 10, tokens_out: 5, model: "m", duration_ms: 100 });
    insertEvent(mockDb, "sess-y", "llm_call", { tokens_in: 10, tokens_out: 5, model: "m", duration_ms: 100 });
    const res = await request(app).get("/diff?baseline=sess-x&candidate=sess-y");
    expect(res.body.similarity).toBe(1);
    expect(res.body.alignment.every((a) => a.status === "matched")).toBe(true);
  });

  test("flags a duration change over the 10ms threshold as modified", async () => {
    insertSession(mockDb, "sess-p", "agent-p");
    insertSession(mockDb, "sess-q", "agent-q");
    insertEvent(mockDb, "sess-p", "llm_call", { model: "m", duration_ms: 100 });
    insertEvent(mockDb, "sess-q", "llm_call", { model: "m", duration_ms: 500 });
    const res = await request(app).get("/diff?baseline=sess-p&candidate=sess-q");
    const modified = res.body.alignment.filter((a) => a.status === "modified");
    expect(modified.some((m) => m.changes && m.changes.duration_ms)).toBe(true);
  });

  test("reports added and removed event types", async () => {
    insertSession(mockDb, "sess-t1", "agent-t");
    insertSession(mockDb, "sess-t2", "agent-t");
    insertEvent(mockDb, "sess-t1", "only_base", {});
    insertEvent(mockDb, "sess-t2", "only_cand", {});
    const res = await request(app).get("/diff?baseline=sess-t1&candidate=sess-t2");
    expect(res.body.event_types.added).toContain("only_cand");
    expect(res.body.event_types.removed).toContain("only_base");
  });

  test("handles empty sessions without error", async () => {
    insertSession(mockDb, "sess-empty1", "agent-e");
    insertSession(mockDb, "sess-empty2", "agent-e");
    const res = await request(app).get("/diff?baseline=sess-empty1&candidate=sess-empty2");
    expect(res.status).toBe(200);
    expect(res.body.similarity).toBe(1); // no events -> defined as identical
    expect(res.body.alignment).toEqual([]);
    expect(res.body.truncated).toBe(false);
  });
});
