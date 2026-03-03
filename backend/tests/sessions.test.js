/* ── Sessions Route Tests ── */

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
        CREATE TABLE IF NOT EXISTS session_tags (
          session_id TEXT NOT NULL,
          tag TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (session_id, tag),
          FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const sessionsRouter = require("../routes/sessions");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/sessions", sessionsRouter);
  return app;
}

// ── Helpers ─────────────────────────────────────────────────────────

function insertSession(id, opts = {}) {
  const agent = opts.agent || "test-agent";
  const status = opts.status || "completed";
  const started = opts.started || "2026-01-15T10:00:00Z";
  const ended = opts.ended || "2026-01-15T10:05:00Z";
  const tokensIn = opts.tokensIn || 100;
  const tokensOut = opts.tokensOut || 50;
  const metadata = opts.metadata || "{}";
  mockDb
    .prepare(
      `INSERT INTO sessions (session_id, agent_name, started_at, ended_at, status, total_tokens_in, total_tokens_out, metadata)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(id, agent, started, ended, status, tokensIn, tokensOut, metadata);
}

function insertEvent(sessionId, eventId, opts = {}) {
  const type = opts.type || "llm_call";
  const ts = opts.timestamp || "2026-01-15T10:01:00Z";
  const model = opts.model || "gpt-4";
  const tokIn = opts.tokensIn || 50;
  const tokOut = opts.tokensOut || 25;
  const dur = opts.duration || 100;
  const input = opts.input ? JSON.stringify(opts.input) : '{"prompt":"hello"}';
  const output = opts.output ? JSON.stringify(opts.output) : '{"text":"world"}';
  const toolCall = opts.toolCall ? JSON.stringify(opts.toolCall) : null;
  const trace = opts.trace ? JSON.stringify(opts.trace) : null;
  mockDb
    .prepare(
      `INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms, input_data, output_data, tool_call, decision_trace)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(eventId, sessionId, type, ts, model, tokIn, tokOut, dur, input, output, toolCall, trace);
}

function addTag(sessionId, tag) {
  mockDb
    .prepare("INSERT OR IGNORE INTO session_tags (session_id, tag, created_at) VALUES (?, ?, ?)")
    .run(sessionId, tag, "2026-01-15T10:00:00Z");
}

beforeAll(() => {
  require("../db").getDb();
});

beforeEach(() => {
  if (mockDb) {
    mockDb.exec("DELETE FROM session_tags");
    mockDb.exec("DELETE FROM events");
    mockDb.exec("DELETE FROM sessions");
  }
});

// ═════════════════════════════════════════
// GET /sessions — List sessions
// ═════════════════════════════════════════

describe("GET /sessions", () => {
  const app = createApp();

  test("returns empty list when no sessions", async () => {
    const res = await request(app).get("/sessions");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toEqual([]);
    expect(res.body.total).toBe(0);
  });

  test("lists sessions with defaults", async () => {
    insertSession("sess-001");
    insertSession("sess-002");
    const res = await request(app).get("/sessions");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(2);
    expect(res.body.total).toBe(2);
  });

  test("respects limit parameter", async () => {
    for (let i = 1; i <= 5; i++) insertSession(`sess-${i}`);
    const res = await request(app).get("/sessions?limit=2");
    expect(res.body.sessions).toHaveLength(2);
    expect(res.body.total).toBe(5);
  });

  test("respects offset parameter", async () => {
    for (let i = 1; i <= 3; i++) {
      insertSession(`sess-${i}`, { started: `2026-01-${10 + i}T10:00:00Z` });
    }
    const res = await request(app).get("/sessions?limit=2&offset=2");
    expect(res.body.sessions).toHaveLength(1);
  });

  test("clamps limit to max 200", async () => {
    insertSession("sess-001");
    const res = await request(app).get("/sessions?limit=999");
    expect(res.status).toBe(200);
    // Should not error — just clamp
  });

  test("filters by status", async () => {
    insertSession("sess-a", { status: "active" });
    insertSession("sess-c", { status: "completed" });
    const res = await request(app).get("/sessions?status=active");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-a");
  });

  test("rejects invalid status", async () => {
    const res = await request(app).get("/sessions?status=<script>alert(1)</script>");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Invalid status/i);
  });

  test("parses metadata JSON in response", async () => {
    insertSession("sess-meta", { metadata: '{"key":"value"}' });
    const res = await request(app).get("/sessions");
    expect(res.body.sessions[0].metadata).toEqual({ key: "value" });
  });

  test("filters by tag", async () => {
    insertSession("sess-tagged");
    insertSession("sess-untagged");
    addTag("sess-tagged", "prod");
    const res = await request(app).get("/sessions?tag=prod");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-tagged");
  });
});

// ═════════════════════════════════════════
// GET /sessions/:id — Session detail
// ═════════════════════════════════════════

describe("GET /sessions/:id", () => {
  const app = createApp();

  test("returns session with events", async () => {
    insertSession("sess-detail");
    insertEvent("sess-detail", "evt-1");
    insertEvent("sess-detail", "evt-2", { type: "tool_call" });
    const res = await request(app).get("/sessions/sess-detail");
    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("sess-detail");
    expect(res.body.events).toHaveLength(2);
  });

  test("parses event JSON fields", async () => {
    insertSession("sess-parse");
    insertEvent("sess-parse", "evt-p1", {
      input: { prompt: "test" },
      output: { text: "response" },
      toolCall: { tool_name: "search", tool_input: "query" },
      trace: { reasoning: "because" },
    });
    const res = await request(app).get("/sessions/sess-parse");
    const evt = res.body.events[0];
    expect(evt.input_data).toEqual({ prompt: "test" });
    expect(evt.output_data).toEqual({ text: "response" });
    expect(evt.tool_call).toEqual({ tool_name: "search", tool_input: "query" });
    expect(evt.decision_trace).toEqual({ reasoning: "because" });
  });

  test("returns 404 for missing session", async () => {
    const res = await request(app).get("/sessions/nonexistent");
    expect(res.status).toBe(404);
  });

  test("rejects invalid session ID format", async () => {
    const res = await request(app).get("/sessions/invalid session id!");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Invalid session ID/i);
  });
});

// ═════════════════════════════════════════
// GET /sessions/search — Search sessions
// ═════════════════════════════════════════

describe("GET /sessions/search", () => {
  const app = createApp();

  test("returns all sessions without filters", async () => {
    insertSession("sess-s1");
    insertSession("sess-s2");
    const res = await request(app).get("/sessions/search");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(2);
    expect(res.body.total).toBe(2);
    expect(res.body.filters).toBeDefined();
  });

  test("searches by agent name", async () => {
    insertSession("sess-a1", { agent: "codex-agent" });
    insertSession("sess-a2", { agent: "other-agent" });
    const res = await request(app).get("/sessions/search?agent=codex");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].agent_name).toBe("codex-agent");
  });

  test("full-text search across agent_name and metadata", async () => {
    insertSession("sess-q1", { agent: "summarizer", metadata: '{"topic":"climate"}' });
    insertSession("sess-q2", { agent: "translator" });
    const res = await request(app).get("/sessions/search?q=climate");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-q1");
  });

  test("filters by status", async () => {
    insertSession("sess-fa", { status: "active" });
    insertSession("sess-fc", { status: "completed" });
    const res = await request(app).get("/sessions/search?status=completed");
    expect(res.body.sessions).toHaveLength(1);
  });

  test("filters by date range", async () => {
    insertSession("sess-d1", { started: "2026-01-10T10:00:00Z" });
    insertSession("sess-d2", { started: "2026-01-20T10:00:00Z" });
    const res = await request(app).get(
      "/sessions/search?after=2026-01-15T00:00:00Z"
    );
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-d2");
  });

  test("filters by token thresholds", async () => {
    insertSession("sess-t1", { tokensIn: 10, tokensOut: 5 });
    insertSession("sess-t2", { tokensIn: 500, tokensOut: 200 });
    const res = await request(app).get("/sessions/search?min_tokens=100");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-t2");
  });

  test("sorts by specified column", async () => {
    insertSession("sess-sort1", { tokensIn: 10, tokensOut: 5 });
    insertSession("sess-sort2", { tokensIn: 500, tokensOut: 200 });
    const res = await request(app).get(
      "/sessions/search?sort=total_tokens&order=asc"
    );
    expect(res.body.sessions[0].session_id).toBe("sess-sort1");
    expect(res.body.sort).toBe("total_tokens");
    expect(res.body.order).toBe("asc");
  });

  test("filters by tags (intersection)", async () => {
    insertSession("sess-tag1");
    insertSession("sess-tag2");
    addTag("sess-tag1", "prod");
    addTag("sess-tag1", "v2");
    addTag("sess-tag2", "staging");
    const res = await request(app).get("/sessions/search?tags=prod,v2");
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.sessions[0].session_id).toBe("sess-tag1");
  });

  test("returns filter summary in response", async () => {
    const res = await request(app).get(
      "/sessions/search?q=test&status=active&agent=x"
    );
    expect(res.body.filters.q).toBe("test");
    expect(res.body.filters.status).toBe("active");
    expect(res.body.filters.agent).toBe("x");
  });
});

// ═════════════════════════════════════════
// GET /sessions/:id/export — Export
// ═════════════════════════════════════════

describe("GET /sessions/:id/export", () => {
  const app = createApp();

  test("exports session as JSON", async () => {
    insertSession("sess-exp", { agent: "test-agent" });
    insertEvent("sess-exp", "evt-e1", { model: "gpt-4" });
    const res = await request(app).get("/sessions/sess-exp/export?format=json");
    expect(res.status).toBe(200);
    expect(res.body.session.session_id).toBe("sess-exp");
    expect(res.body.events).toHaveLength(1);
    expect(res.body.summary.total_events).toBe(1);
    expect(res.body.summary.models_used).toContain("gpt-4");
    expect(res.headers["content-disposition"]).toMatch(/attachment/);
  });

  test("exports session as CSV", async () => {
    insertSession("sess-csv");
    insertEvent("sess-csv", "evt-c1");
    const res = await request(app).get("/sessions/sess-csv/export?format=csv");
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toMatch(/text\/csv/);
    expect(res.text).toContain("event_id");
    expect(res.text).toContain("evt-c1");
  });

  test("rejects invalid format", async () => {
    insertSession("sess-fmt");
    const res = await request(app).get("/sessions/sess-fmt/export?format=xml");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Invalid format/i);
  });

  test("returns 404 for missing session", async () => {
    const res = await request(app).get("/sessions/nonexistent/export");
    expect(res.status).toBe(404);
  });
});

// ═════════════════════════════════════════
// POST /sessions/compare — Compare sessions
// ═════════════════════════════════════════

describe("POST /sessions/compare", () => {
  const app = createApp();

  test("compares two sessions with metrics", async () => {
    insertSession("sess-cmp-a", { tokensIn: 100, tokensOut: 50 });
    insertSession("sess-cmp-b", { tokensIn: 200, tokensOut: 100 });
    insertEvent("sess-cmp-a", "evt-ca1", { model: "gpt-4", tokensIn: 100, tokensOut: 50, duration: 200 });
    insertEvent("sess-cmp-b", "evt-cb1", { model: "gpt-4", tokensIn: 200, tokensOut: 100, duration: 300 });
    const res = await request(app)
      .post("/sessions/compare")
      .send({ session_a: "sess-cmp-a", session_b: "sess-cmp-b" });
    expect(res.status).toBe(200);
    expect(res.body.session_a.session_id).toBe("sess-cmp-a");
    expect(res.body.session_b.session_id).toBe("sess-cmp-b");
    expect(res.body.deltas.total_tokens.absolute).toBeGreaterThan(0);
    expect(res.body.shared.models).toContain("gpt-4");
  });

  test("rejects missing session IDs", async () => {
    const res = await request(app).post("/sessions/compare").send({});
    expect(res.status).toBe(400);
  });

  test("rejects comparing session with itself", async () => {
    insertSession("sess-self");
    const res = await request(app)
      .post("/sessions/compare")
      .send({ session_a: "sess-self", session_b: "sess-self" });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/itself/i);
  });

  test("returns 404 if session_a missing", async () => {
    insertSession("sess-only-b");
    const res = await request(app)
      .post("/sessions/compare")
      .send({ session_a: "missing", session_b: "sess-only-b" });
    expect(res.status).toBe(404);
  });

  test("computes percent deltas correctly", async () => {
    insertSession("sess-pa", { tokensIn: 100, tokensOut: 0 });
    insertSession("sess-pb", { tokensIn: 200, tokensOut: 0 });
    const res = await request(app)
      .post("/sessions/compare")
      .send({ session_a: "sess-pa", session_b: "sess-pb" });
    expect(res.body.deltas.tokens_in.percent).toBe(100); // doubled
  });
});

// ═════════════════════════════════════════
// GET /sessions/:id/events/search
// ═════════════════════════════════════════

describe("GET /sessions/:id/events/search", () => {
  const app = createApp();

  test("returns all events for a session", async () => {
    insertSession("sess-es");
    insertEvent("sess-es", "evt-es1");
    insertEvent("sess-es", "evt-es2");
    const res = await request(app).get("/sessions/sess-es/events/search");
    expect(res.status).toBe(200);
    expect(res.body.events).toHaveLength(2);
    expect(res.body.total_events).toBe(2);
    expect(res.body.matched).toBe(2);
  });

  test("filters events by type", async () => {
    insertSession("sess-et");
    insertEvent("sess-et", "evt-et1", { type: "llm_call" });
    insertEvent("sess-et", "evt-et2", { type: "tool_call" });
    const res = await request(app).get(
      "/sessions/sess-et/events/search?type=tool_call"
    );
    expect(res.body.matched).toBe(1);
    expect(res.body.events[0].event_type).toBe("tool_call");
  });

  test("filters events by model", async () => {
    insertSession("sess-em");
    insertEvent("sess-em", "evt-em1", { model: "gpt-4" });
    insertEvent("sess-em", "evt-em2", { model: "claude-3" });
    const res = await request(app).get(
      "/sessions/sess-em/events/search?model=claude"
    );
    expect(res.body.matched).toBe(1);
  });

  test("filters events by token thresholds", async () => {
    insertSession("sess-tok");
    insertEvent("sess-tok", "evt-tok1", { tokensIn: 10, tokensOut: 5 });
    insertEvent("sess-tok", "evt-tok2", { tokensIn: 200, tokensOut: 100 });
    const res = await request(app).get(
      "/sessions/sess-tok/events/search?min_tokens=50"
    );
    expect(res.body.matched).toBe(1);
    expect(res.body.events[0].event_id).toBe("evt-tok2");
  });

  test("filters error events only", async () => {
    insertSession("sess-err");
    insertEvent("sess-err", "evt-ok", { type: "llm_call" });
    insertEvent("sess-err", "evt-bad", { type: "error" });
    const res = await request(app).get(
      "/sessions/sess-err/events/search?errors=true"
    );
    expect(res.body.matched).toBe(1);
    expect(res.body.events[0].event_type).toBe("error");
  });

  test("filters events with tool calls only", async () => {
    insertSession("sess-tool");
    insertEvent("sess-tool", "evt-notool");
    insertEvent("sess-tool", "evt-withtool", {
      toolCall: { tool_name: "search", tool_input: "q" },
    });
    const res = await request(app).get(
      "/sessions/sess-tool/events/search?has_tools=true"
    );
    expect(res.body.matched).toBe(1);
  });

  test("full-text search across event data", async () => {
    insertSession("sess-fts");
    insertEvent("sess-fts", "evt-fts1", { input: { prompt: "climate change" } });
    insertEvent("sess-fts", "evt-fts2", { input: { prompt: "recipe ideas" } });
    const res = await request(app).get(
      "/sessions/sess-fts/events/search?q=climate"
    );
    expect(res.body.matched).toBe(1);
  });

  test("returns summary stats for filtered results", async () => {
    insertSession("sess-sum");
    insertEvent("sess-sum", "evt-sum1", { tokensIn: 100, tokensOut: 50, duration: 200 });
    insertEvent("sess-sum", "evt-sum2", { tokensIn: 200, tokensOut: 100, duration: 300 });
    const res = await request(app).get("/sessions/sess-sum/events/search");
    expect(res.body.summary.tokens_in).toBe(300);
    expect(res.body.summary.tokens_out).toBe(150);
    expect(res.body.summary.total_duration_ms).toBe(500);
  });

  test("respects pagination", async () => {
    insertSession("sess-page");
    for (let i = 1; i <= 5; i++) {
      insertEvent("sess-page", `evt-p${i}`, {
        timestamp: `2026-01-15T10:0${i}:00Z`,
      });
    }
    const res = await request(app).get(
      "/sessions/sess-page/events/search?limit=2&offset=0"
    );
    expect(res.body.returned).toBe(2);
    expect(res.body.matched).toBe(5);
  });

  test("returns 404 for missing session", async () => {
    const res = await request(app).get("/sessions/missing/events/search");
    expect(res.status).toBe(404);
  });

  test("filters by time range", async () => {
    insertSession("sess-tr");
    insertEvent("sess-tr", "evt-tr1", { timestamp: "2026-01-10T10:00:00Z" });
    insertEvent("sess-tr", "evt-tr2", { timestamp: "2026-01-20T10:00:00Z" });
    const res = await request(app).get(
      "/sessions/sess-tr/events/search?after=2026-01-15T00:00:00Z"
    );
    expect(res.body.matched).toBe(1);
    expect(res.body.events[0].event_id).toBe("evt-tr2");
  });

  test("filters by minimum duration", async () => {
    insertSession("sess-dur");
    insertEvent("sess-dur", "evt-fast", { duration: 10 });
    insertEvent("sess-dur", "evt-slow", { duration: 5000 });
    const res = await request(app).get(
      "/sessions/sess-dur/events/search?min_duration_ms=1000"
    );
    expect(res.body.matched).toBe(1);
    expect(res.body.events[0].event_id).toBe("evt-slow");
  });
});

// ═════════════════════════════════════════
// GET /sessions/:id/explain
// ═════════════════════════════════════════

describe("GET /sessions/:id/explain", () => {
  const app = createApp();

  test("returns explanation for session", async () => {
    insertSession("sess-expl");
    insertEvent("sess-expl", "evt-expl1");
    const res = await request(app).get("/sessions/sess-expl/explain");
    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("sess-expl");
    expect(res.body.explanation).toBeDefined();
  });

  test("returns 404 for missing session", async () => {
    const res = await request(app).get("/sessions/missing/explain");
    expect(res.status).toBe(404);
  });

  test("rejects invalid session ID", async () => {
    const res = await request(app).get("/sessions/bad id!/explain");
    expect(res.status).toBe(400);
  });
});

// ═════════════════════════════════════════
// Session Tags CRUD
// ═════════════════════════════════════════

describe("Session Tags", () => {
  const app = createApp();

  test("GET /:id/tags returns empty array initially", async () => {
    insertSession("sess-tags0");
    const res = await request(app).get("/sessions/sess-tags0/tags");
    expect(res.status).toBe(200);
    expect(res.body.tags).toEqual([]);
  });

  test("POST /:id/tags adds tags", async () => {
    insertSession("sess-tags1");
    const res = await request(app)
      .post("/sessions/sess-tags1/tags")
      .send({ tags: ["prod", "v2"] });
    expect(res.status).toBe(200);
    expect(res.body.added).toBe(2);
    expect(res.body.tags).toContain("prod");
    expect(res.body.tags).toContain("v2");
  });

  test("POST /:id/tags rejects invalid tags", async () => {
    insertSession("sess-tags-bad");
    const res = await request(app)
      .post("/sessions/sess-tags-bad/tags")
      .send({ tags: ["<script>"] });
    expect(res.status).toBe(400);
  });

  test("POST /:id/tags returns 404 for missing session", async () => {
    const res = await request(app)
      .post("/sessions/nonexistent/tags")
      .send({ tags: ["test"] });
    expect(res.status).toBe(404);
  });

  test("POST /:id/tags enforces tag limit", async () => {
    insertSession("sess-limit");
    // Add 20 tags (the max)
    const tags = Array.from({ length: 20 }, (_, i) => `tag-${i}`);
    await request(app).post("/sessions/sess-limit/tags").send({ tags });
    // Try adding one more
    const res = await request(app)
      .post("/sessions/sess-limit/tags")
      .send({ tags: ["tag-overflow"] });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/limit/i);
  });

  test("POST /:id/tags ignores duplicate tags", async () => {
    insertSession("sess-dup");
    await request(app).post("/sessions/sess-dup/tags").send({ tags: ["prod"] });
    const res = await request(app)
      .post("/sessions/sess-dup/tags")
      .send({ tags: ["prod"] });
    expect(res.body.added).toBe(0);
    expect(res.body.tags).toEqual(["prod"]);
  });

  test("DELETE /:id/tags removes specific tags", async () => {
    insertSession("sess-del");
    await request(app)
      .post("/sessions/sess-del/tags")
      .send({ tags: ["a", "b", "c"] });
    const res = await request(app)
      .delete("/sessions/sess-del/tags")
      .send({ tags: ["b"] });
    expect(res.status).toBe(200);
    expect(res.body.removed).toBe(1);
    expect(res.body.tags).toEqual(["a", "c"]);
  });

  test("DELETE /:id/tags removes all when no tags specified", async () => {
    insertSession("sess-delall");
    await request(app)
      .post("/sessions/sess-delall/tags")
      .send({ tags: ["x", "y"] });
    const res = await request(app)
      .delete("/sessions/sess-delall/tags")
      .send({});
    expect(res.status).toBe(200);
    expect(res.body.removed).toBe(2);
    expect(res.body.tags).toEqual([]);
  });
});

// ═════════════════════════════════════════
// GET /sessions/tags — Global tag list
// ═════════════════════════════════════════

describe("GET /sessions/tags", () => {
  const app = createApp();

  test("returns all tags with counts", async () => {
    insertSession("sess-gt1");
    insertSession("sess-gt2");
    addTag("sess-gt1", "prod");
    addTag("sess-gt2", "prod");
    addTag("sess-gt1", "v2");
    const res = await request(app).get("/sessions/tags");
    expect(res.status).toBe(200);
    expect(res.body.tags).toHaveLength(2);
    // prod has 2 sessions, v2 has 1 — sorted desc by count
    expect(res.body.tags[0].tag).toBe("prod");
    expect(res.body.tags[0].session_count).toBe(2);
  });
});

// ═════════════════════════════════════════
// GET /sessions/by-tag/:tag
// ═════════════════════════════════════════

describe("GET /sessions/by-tag/:tag", () => {
  const app = createApp();

  test("returns sessions with specific tag", async () => {
    insertSession("sess-bt1");
    insertSession("sess-bt2");
    addTag("sess-bt1", "staging");
    const res = await request(app).get("/sessions/by-tag/staging");
    expect(res.status).toBe(200);
    expect(res.body.sessions).toHaveLength(1);
    expect(res.body.tag).toBe("staging");
    expect(res.body.total).toBe(1);
  });

  test("returns tags for each session", async () => {
    insertSession("sess-bt3");
    addTag("sess-bt3", "multi");
    addTag("sess-bt3", "extra");
    const res = await request(app).get("/sessions/by-tag/multi");
    expect(res.body.sessions[0].tags).toContain("multi");
    expect(res.body.sessions[0].tags).toContain("extra");
  });

  test("rejects invalid tag format", async () => {
    const res = await request(app).get("/sessions/by-tag/<invalid>");
    expect(res.status).toBe(400);
  });
});
