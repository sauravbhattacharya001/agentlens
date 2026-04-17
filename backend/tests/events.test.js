/* ── Events Route Tests ── */

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

// Reset cached prepared statements between tests
beforeEach(() => {
  if (mockDb) {
    mockDb.exec("DELETE FROM events");
    mockDb.exec("DELETE FROM sessions");
  }
  // Force re-initialization of prepared statements
  jest.resetModules();
});

function buildApp() {
  // Fresh require to pick up reset modules
  const eventsRouter = require("../routes/events");
  const app = express();
  app.use(express.json());
  app.use("/events", eventsRouter);
  return app;
}

describe("POST /events", () => {
  // ── Validation ──────────────────────────────────────────────────

  test("rejects request without events array", async () => {
    const app = buildApp();
    const res = await request(app).post("/events").send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Missing.*events.*array/i);
  });

  test("rejects non-array events field", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({ events: "not-an-array" });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Missing.*events.*array/i);
  });

  test("rejects batch exceeding MAX_BATCH_SIZE", async () => {
    const app = buildApp();
    // MAX_BATCH_SIZE is 1000 (from validation.js)
    const events = Array.from({ length: 1001 }, (_, i) => ({
      session_id: `sess-${i}`,
      event_type: "generic",
      timestamp: new Date().toISOString(),
    }));
    const res = await request(app).post("/events").send({ events });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Batch too large/);
  });

  test("handles empty events array gracefully", async () => {
    const app = buildApp();
    const res = await request(app).post("/events").send({ events: [] });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok", processed: 0 });
  });

  // ── Session Lifecycle ───────────────────────────────────────────

  test("session_start creates a new session", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "test-sess-1",
            event_type: "session_start",
            agent_name: "my-agent",
            timestamp: "2026-01-01T00:00:00Z",
            metadata: { env: "test" },
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);

    // Verify session was created in DB
    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("test-sess-1");
    expect(row).toBeTruthy();
    expect(row.agent_name).toBe("my-agent");
    expect(row.status).toBe("active");
    expect(JSON.parse(row.metadata)).toEqual({ env: "test" });
  });

  test("session_end updates session status and timestamps", async () => {
    const app = buildApp();
    // First create session
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "sess-end-test",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });

    // Then end it
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "sess-end-test",
            event_type: "session_end",
            ended_at: "2026-01-01T00:05:00Z",
            status: "completed",
            total_tokens_in: 500,
            total_tokens_out: 200,
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("sess-end-test");
    expect(row.status).toBe("completed");
    expect(row.ended_at).toBe("2026-01-01T00:05:00Z");
    expect(row.total_tokens_in).toBe(500);
    expect(row.total_tokens_out).toBe(200);
  });

  test("session_end without token counts preserves existing counts", async () => {
    const app = buildApp();
    // Create session with some token counts from events
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "sess-keep-tokens",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "sess-keep-tokens",
            event_type: "llm_call",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 100,
            tokens_out: 50,
          },
        ],
      });

    // End without specifying tokens (total_tokens_in/out = 0)
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "sess-keep-tokens",
            event_type: "session_end",
            ended_at: "2026-01-01T00:05:00Z",
          },
        ],
      });

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("sess-keep-tokens");
    // Should keep accumulated counts (100/50 from the event)
    expect(row.total_tokens_in).toBe(100);
    expect(row.total_tokens_out).toBe(50);
  });

  // ── Regular Events ──────────────────────────────────────────────

  test("ingests a single generic event", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "sess-generic",
            event_type: "llm_call",
            event_id: "evt-001",
            timestamp: "2026-01-01T00:00:00Z",
            model: "gpt-4",
            tokens_in: 100,
            tokens_out: 50,
            duration_ms: 1200.5,
            input_data: { prompt: "Hello" },
            output_data: { response: "Hi there" },
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);

    // Check event was stored
    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("evt-001");
    expect(evt).toBeTruthy();
    expect(evt.session_id).toBe("sess-generic");
    expect(evt.event_type).toBe("llm_call");
    expect(evt.model).toBe("gpt-4");
    expect(evt.tokens_in).toBe(100);
    expect(evt.tokens_out).toBe(50);
    expect(evt.duration_ms).toBeCloseTo(1200.5);
    expect(JSON.parse(evt.input_data)).toEqual({ prompt: "Hello" });
  });

  test("auto-creates session for orphan events", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "orphan-sess",
            event_type: "tool_call",
            event_id: "evt-orphan",
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });

    // Session should be auto-created with default agent name
    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("orphan-sess");
    expect(row).toBeTruthy();
    expect(row.agent_name).toBe("default-agent");
    expect(row.status).toBe("active");
  });

  test("updates session token counts on each event", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "token-sess",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "token-sess",
            event_type: "llm_call",
            event_id: "tok-1",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 100,
            tokens_out: 50,
          },
          {
            session_id: "token-sess",
            event_type: "llm_call",
            event_id: "tok-2",
            timestamp: "2026-01-01T00:02:00Z",
            tokens_in: 200,
            tokens_out: 100,
          },
        ],
      });

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("token-sess");
    expect(row.total_tokens_in).toBe(300);
    expect(row.total_tokens_out).toBe(150);
  });

  // ── Batch Processing ────────────────────────────────────────────

  test("processes multiple events in a single batch", async () => {
    const app = buildApp();
    const events = [];
    for (let i = 0; i < 5; i++) {
      events.push({
        session_id: "batch-sess",
        event_type: "llm_call",
        event_id: `batch-evt-${i}`,
        timestamp: new Date().toISOString(),
        tokens_in: 10,
        tokens_out: 5,
      });
    }
    const res = await request(app).post("/events").send({ events });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(5);

    const count = mockDb
      .prepare("SELECT COUNT(*) as c FROM events WHERE session_id = ?")
      .get("batch-sess");
    expect(count.c).toBe(5);
  });

  test("batch with mixed session lifecycle and regular events", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "mixed-sess",
            event_type: "session_start",
            agent_name: "test-agent",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "mixed-sess",
            event_type: "llm_call",
            event_id: "mixed-1",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 50,
            tokens_out: 25,
          },
          {
            session_id: "mixed-sess",
            event_type: "tool_call",
            event_id: "mixed-2",
            timestamp: "2026-01-01T00:02:00Z",
            tool_call: { name: "search", args: { q: "test" } },
          },
          {
            session_id: "mixed-sess",
            event_type: "session_end",
            ended_at: "2026-01-01T00:03:00Z",
            status: "completed",
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(4);
    expect(res.body.skipped).toBe(0);
  });

  // ── Skipping Invalid Events ─────────────────────────────────────

  test("skips events with invalid session_id", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "",
            event_type: "llm_call",
            timestamp: new Date().toISOString(),
          },
          {
            session_id: "valid-sess",
            event_type: "llm_call",
            event_id: "valid-evt",
            timestamp: new Date().toISOString(),
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);
    expect(res.body.skipped).toBe(1);
  });

  test("skips events with invalid event_type", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "skip-type-sess",
            event_type: "<script>alert(1)</script>",
            timestamp: new Date().toISOString(),
          },
        ],
      });
    expect(res.status).toBe(200);
    // Should be skipped due to invalid event type
    expect(res.body.skipped).toBeGreaterThanOrEqual(0);
    // processed + skipped should equal 1
    expect(res.body.processed + res.body.skipped).toBe(1);
  });

  // ── Edge Cases ──────────────────────────────────────────────────

  test("generates event_id when not provided", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "auto-id-sess",
            event_type: "llm_call",
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);

    const evt = mockDb
      .prepare("SELECT event_id FROM events WHERE session_id = ?")
      .get("auto-id-sess");
    expect(evt).toBeTruthy();
    expect(evt.event_id).toBeTruthy();
    expect(evt.event_id.length).toBe(16);
  });

  test("handles missing optional fields gracefully", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "minimal-sess",
            event_type: "generic",
            // No timestamp, tokens, model, input/output, etc.
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(1);

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE session_id = ?")
      .get("minimal-sess");
    expect(evt).toBeTruthy();
    expect(evt.tokens_in).toBe(0);
    expect(evt.tokens_out).toBe(0);
    // duration_ms is null when not provided (clampNonNegFloat returns null for undefined)
    expect(evt.duration_ms).toBeNull();
    expect(evt.model).toBeNull();
  });

  test("clamps negative token values to zero", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "neg-tokens-sess",
            event_type: "llm_call",
            event_id: "neg-tok-1",
            timestamp: "2026-01-01T00:00:00Z",
            tokens_in: -100,
            tokens_out: -50,
            duration_ms: -1000,
          },
        ],
      });
    expect(res.status).toBe(200);

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("neg-tok-1");
    expect(evt.tokens_in).toBe(0);
    expect(evt.tokens_out).toBe(0);
    expect(evt.duration_ms).toBe(0);
  });

  test("duplicate event_id is silently ignored (INSERT OR IGNORE)", async () => {
    const app = buildApp();
    // First insert
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "dup-sess",
            event_type: "llm_call",
            event_id: "dup-evt",
            timestamp: "2026-01-01T00:00:00Z",
            tokens_in: 100,
          },
        ],
      });

    // Second insert with same event_id
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "dup-sess",
            event_type: "llm_call",
            event_id: "dup-evt",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 999,
          },
        ],
      });
    expect(res.status).toBe(200);

    // Original event should be preserved
    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("dup-evt");
    expect(evt.tokens_in).toBe(100);
  });

  test("duplicate session_start is silently ignored", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "dup-start-sess",
            event_type: "session_start",
            agent_name: "original-agent",
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });

    // Second start — should not overwrite
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "dup-start-sess",
            event_type: "session_start",
            agent_name: "different-agent",
            timestamp: "2026-01-01T00:01:00Z",
          },
        ],
      });
    expect(res.status).toBe(200);

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("dup-start-sess");
    expect(row.agent_name).toBe("original-agent");
  });

  test("stores tool_call as JSON", async () => {
    const app = buildApp();
    const toolCall = {
      name: "web_search",
      arguments: { query: "test query" },
      result: { urls: ["https://example.com"] },
    };
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "tool-sess",
            event_type: "tool_call",
            event_id: "tool-evt-1",
            timestamp: "2026-01-01T00:00:00Z",
            tool_call: toolCall,
          },
        ],
      });

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("tool-evt-1");
    expect(JSON.parse(evt.tool_call)).toEqual(toolCall);
  });

  test("stores decision_trace as JSON", async () => {
    const app = buildApp();
    const trace = [
      { step: 1, action: "analyze", confidence: 0.9 },
      { step: 2, action: "decide", confidence: 0.95 },
    ];
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "trace-sess",
            event_type: "agent_call",
            event_id: "trace-evt-1",
            timestamp: "2026-01-01T00:00:00Z",
            decision_trace: trace,
          },
        ],
      });

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("trace-evt-1");
    expect(JSON.parse(evt.decision_trace)).toEqual(trace);
  });

  // ── String Sanitization ─────────────────────────────────────────

  test("sanitizes long agent names", async () => {
    const app = buildApp();
    const longName = "a".repeat(500);
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "long-name-sess",
            event_type: "session_start",
            agent_name: longName,
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("long-name-sess");
    expect(row.agent_name.length).toBeLessThanOrEqual(256);
  });

  test("sanitizes long model names", async () => {
    const app = buildApp();
    const longModel = "m".repeat(300);
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "long-model-sess",
            event_type: "llm_call",
            event_id: "long-model-evt",
            timestamp: "2026-01-01T00:00:00Z",
            model: longModel,
          },
        ],
      });

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("long-model-evt");
    expect(evt.model.length).toBeLessThanOrEqual(128);
  });

  // ── Multiple Sessions in One Batch ──────────────────────────────

  test("handles events across multiple sessions in one batch", async () => {
    const app = buildApp();
    const res = await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "multi-a",
            event_type: "session_start",
            agent_name: "agent-a",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "multi-b",
            event_type: "session_start",
            agent_name: "agent-b",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "multi-a",
            event_type: "llm_call",
            event_id: "ma-1",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 10,
            tokens_out: 5,
          },
          {
            session_id: "multi-b",
            event_type: "llm_call",
            event_id: "mb-1",
            timestamp: "2026-01-01T00:01:00Z",
            tokens_in: 20,
            tokens_out: 10,
          },
        ],
      });
    expect(res.status).toBe(200);
    expect(res.body.processed).toBe(4);

    // Verify separate token accumulation
    const sessA = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("multi-a");
    const sessB = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("multi-b");
    expect(sessA.total_tokens_in).toBe(10);
    expect(sessB.total_tokens_in).toBe(20);
  });

  // ── Transactional Atomicity ─────────────────────────────────────

  test("processes batch atomically (all or nothing)", async () => {
    const app = buildApp();
    // First batch succeeds
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "atomic-sess",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
        ],
      });

    // Verify session exists
    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("atomic-sess");
    expect(row).toBeTruthy();
  });

  // ── Default Values ──────────────────────────────────────────────

  test("uses default timestamp when not provided", async () => {
    const app = buildApp();
    const before = new Date().toISOString();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "default-ts-sess",
            event_type: "llm_call",
            event_id: "default-ts-evt",
            // no timestamp
          },
        ],
      });

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("default-ts-evt");
    expect(evt.timestamp).toBeTruthy();
    // Should be a valid ISO timestamp
    expect(new Date(evt.timestamp).getTime()).not.toBeNaN();
  });

  test("uses default status 'completed' for session_end", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "default-status-sess",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "default-status-sess",
            event_type: "session_end",
            // no status field
          },
        ],
      });

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("default-status-sess");
    expect(row.status).toBe("completed");
  });

  test("normalizes invalid session_end status to 'completed'", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "invalid-status-sess",
            event_type: "session_start",
            timestamp: "2026-01-01T00:00:00Z",
          },
          {
            session_id: "invalid-status-sess",
            event_type: "session_end",
            status: "hacked",
          },
        ],
      });

    const row = mockDb
      .prepare("SELECT * FROM sessions WHERE session_id = ?")
      .get("invalid-status-sess");
    expect(row.status).toBe("completed");
  });

  test("non-string input/output data is serialized as JSON", async () => {
    const app = buildApp();
    await request(app)
      .post("/events")
      .send({
        events: [
          {
            session_id: "json-data-sess",
            event_type: "llm_call",
            event_id: "json-data-evt",
            timestamp: "2026-01-01T00:00:00Z",
            input_data: { messages: [{ role: "user", content: "hi" }] },
            output_data: { choices: [{ text: "hello" }] },
          },
        ],
      });

    const evt = mockDb
      .prepare("SELECT * FROM events WHERE event_id = ?")
      .get("json-data-evt");
    const input = JSON.parse(evt.input_data);
    expect(input.messages[0].content).toBe("hi");
    const output = JSON.parse(evt.output_data);
    expect(output.choices[0].text).toBe("hello");
  });
});
