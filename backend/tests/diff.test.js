const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const express = require("express");
const http = require("http");

// Stub db module
const dbRows = { sessions: {}, events: {} };
const mockDb = {
  prepare(sql) {
    return {
      get(id) {
        return dbRows.sessions[id] || null;
      },
      all(id) {
        return dbRows.events[id] || [];
      },
    };
  },
};

// Monkey-patch db module before requiring router
const path = require("path");
const Module = require("module");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (request === "../db") {
    return require.resolve("./diff-db-stub");
  }
  return originalResolve.call(this, request, parent, ...rest);
};

// Create a stub file for the db
const fs = require("fs");
const stubPath = path.join(__dirname, "diff-db-stub.js");
fs.writeFileSync(
  stubPath,
  `let db = null; module.exports = { getDb() { return db; }, _setDb(d) { db = d; } };`
);

const { _setDb } = require("./diff-db-stub");
const diffRouter = require("../routes/diff");

// Restore module resolution
Module._resolveFilename = originalResolve;

function makeApp() {
  const app = express();
  app.use(express.json());
  app.use("/diff", diffRouter);
  return app;
}

function fetch(app, url) {
  return new Promise((resolve, reject) => {
    const server = app.listen(0, () => {
      const port = server.address().port;
      http.get(`http://127.0.0.1:${port}${url}`, (res) => {
        let body = "";
        res.on("data", (c) => (body += c));
        res.on("end", () => {
          server.close();
          try {
            resolve({ status: res.statusCode, body: JSON.parse(body) });
          } catch {
            resolve({ status: res.statusCode, body });
          }
        });
      }).on("error", (e) => { server.close(); reject(e); });
    });
  });
}

function makeSession(id, agent, events) {
  return { session_id: id, agent_name: agent, status: "completed", metadata: "{}" };
}

function makeEvent(type, opts = {}) {
  return {
    id: Math.random(),
    session_id: opts.session_id || "s1",
    event_type: type,
    timestamp: new Date().toISOString(),
    duration_ms: opts.duration_ms || 100,
    model: opts.model || "gpt-4",
    tokens_in: opts.tokens_in || 50,
    tokens_out: opts.tokens_out || 30,
    tool_call: opts.tool_call ? JSON.stringify(opts.tool_call) : null,
    input_data: null,
    output_data: null,
    decision_trace: null,
  };
}

describe("GET /diff", () => {
  let app;

  beforeEach(() => {
    const sessions = {};
    const events = {};

    const db = {
      prepare(sql) {
        return {
          get(id) { return sessions[id] || null; },
          all(id) { return events[id] || []; },
        };
      },
    };
    _setDb(db);

    // Baseline session
    sessions["sess-base"] = makeSession("sess-base", "agent-a");
    events["sess-base"] = [
      makeEvent("llm_call", { session_id: "sess-base", tokens_in: 100, tokens_out: 50, model: "gpt-4" }),
      makeEvent("tool_call", { session_id: "sess-base", tokens_in: 20, tokens_out: 10, tool_call: { tool_name: "search" } }),
      makeEvent("llm_call", { session_id: "sess-base", tokens_in: 80, tokens_out: 40, model: "gpt-4" }),
    ];

    // Candidate session
    sessions["sess-cand"] = makeSession("sess-cand", "agent-b");
    events["sess-cand"] = [
      makeEvent("llm_call", { session_id: "sess-cand", tokens_in: 120, tokens_out: 60, model: "gpt-4o" }),
      makeEvent("tool_call", { session_id: "sess-cand", tokens_in: 25, tokens_out: 15, tool_call: { tool_name: "search" } }),
      makeEvent("tool_call", { session_id: "sess-cand", tokens_in: 30, tokens_out: 20, tool_call: { tool_name: "calculator" } }),
      makeEvent("llm_call", { session_id: "sess-cand", tokens_in: 90, tokens_out: 45, model: "gpt-4o" }),
    ];

    app = makeApp();
  });

  afterEach(() => {
    _setDb(null);
  });

  it("returns 400 when params missing", async () => {
    const res = await fetch(app, "/diff");
    assert.equal(res.status, 400);
  });

  it("returns 400 when same session", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-base");
    assert.equal(res.status, 400);
  });

  it("returns 404 for missing baseline", async () => {
    const res = await fetch(app, "/diff?baseline=nonexist&candidate=sess-cand");
    assert.equal(res.status, 404);
  });

  it("returns 404 for missing candidate", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=nonexist");
    assert.equal(res.status, 404);
  });

  it("computes correct token deltas", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.equal(res.status, 200);
    const d = res.body.deltas;
    // Baseline: in=200, out=100. Candidate: in=265, out=140
    assert.equal(d.tokens_in, 265 - 200);
    assert.equal(d.tokens_out, 140 - 100);
    assert.equal(d.tokens_total, (265 + 140) - (200 + 100));
  });

  it("computes correct event count delta", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.equal(res.body.deltas.event_count, 1); // 4 - 3
  });

  it("detects added tools", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.ok(res.body.tools.added.includes("calculator"));
  });

  it("detects common tools", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.ok(res.body.tools.common.includes("search"));
  });

  it("has no removed tools in this case", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.deepEqual(res.body.tools.removed, []);
  });

  it("detects model changes", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.ok(res.body.models.baseline["gpt-4"] > 0);
    assert.ok(res.body.models.candidate["gpt-4o"] > 0);
  });

  it("computes similarity between 0 and 1", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.ok(res.body.similarity >= 0 && res.body.similarity <= 1);
  });

  it("alignment includes all events", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    // Total alignment should cover all unique events
    assert.ok(res.body.alignment.length >= 3); // at least baseline count
  });

  it("alignment has correct statuses", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    const statuses = new Set(res.body.alignment.map(a => a.status));
    // Should have modified (llm_calls changed tokens/model) and added (calculator)
    assert.ok(statuses.has("modified") || statuses.has("matched"));
    assert.ok(statuses.has("added"));
  });

  it("returns baseline and candidate session info", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.equal(res.body.baseline.session_id, "sess-base");
    assert.equal(res.body.candidate.session_id, "sess-cand");
    assert.equal(res.body.baseline.agent_name, "agent-a");
    assert.equal(res.body.candidate.agent_name, "agent-b");
  });

  it("includes tool counts", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    assert.equal(res.body.tools.baseline_counts.search, 1);
    assert.equal(res.body.tools.candidate_counts.search, 1);
    assert.equal(res.body.tools.candidate_counts.calculator, 1);
  });

  it("modified events have change details", async () => {
    const res = await fetch(app, "/diff?baseline=sess-base&candidate=sess-cand");
    const modified = res.body.alignment.filter(a => a.status === "modified");
    assert.ok(modified.length > 0);
    // At least one should have model change
    const hasModelChange = modified.some(m => m.changes && m.changes.model);
    assert.ok(hasModelChange);
  });
});

// Cleanup stub
process.on("exit", () => {
  try { fs.unlinkSync(stubPath); } catch {}
});
