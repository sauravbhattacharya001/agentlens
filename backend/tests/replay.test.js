/* ── Session Replay Route Tests ── */

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
const replayRouter = require("../routes/replay");
const { buildFrames, replaySummary, msBetween, classifyEvent, safeJsonParse } =
  require("../routes/replay")._internals;

let app;

function seedData(db) {
  const insertSession = db.prepare(
    `INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  const insertEvent = db.prepare(
    `INSERT OR IGNORE INTO events (event_id, session_id, event_type, timestamp, input_data, output_data, model, tokens_in, tokens_out, tool_call, decision_trace, duration_ms)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  );

  insertSession.run("replay-s1", "agent-alpha", "2026-03-10T10:00:00Z", "2026-03-10T10:01:00Z", 500, 250, "completed");
  insertEvent.run("re1", "replay-s1", "llm_call", "2026-03-10T10:00:00Z",
    '{"prompt":"hello"}', '{"text":"hi there"}', "gpt-4", 100, 50, null, null, 1200);
  insertEvent.run("re2", "replay-s1", "tool_use", "2026-03-10T10:00:10Z",
    '{"tool":"search"}', '{"results":3}', null, 0, 0, '{"name":"search","args":"query"}', null, 800);
  insertEvent.run("re3", "replay-s1", "llm_call", "2026-03-10T10:00:20Z",
    '{"prompt":"summarize"}', '{"text":"summary"}', "gpt-4", 200, 100, null, null, 2000);
  insertEvent.run("re4", "replay-s1", "error", "2026-03-10T10:00:30Z",
    '{"prompt":"fail"}', null, "gpt-4", 50, 0, null, null, 100);
  insertEvent.run("re5", "replay-s1", "decision", "2026-03-10T10:00:40Z",
    null, '{"action":"retry"}', null, 0, 0, null, '{"reason":"error recovery"}', 50);

  insertSession.run("replay-empty", "agent-beta", "2026-03-10T11:00:00Z", null, 0, 0, "active");

  insertSession.run("replay-gaps", "agent-gamma", "2026-03-10T12:00:00Z", "2026-03-10T14:00:00Z", 100, 50, "completed");
  insertEvent.run("rg1", "replay-gaps", "llm_call", "2026-03-10T12:00:00Z", null, null, "gpt-4", 50, 25, null, null, 500);
  insertEvent.run("rg2", "replay-gaps", "llm_call", "2026-03-10T13:00:00Z", null, null, "gpt-4", 50, 25, null, null, 500);

  insertSession.run("replay-single", "agent-delta", "2026-03-10T15:00:00Z", null, 10, 5, "completed");
  insertEvent.run("rs1", "replay-single", "generic", "2026-03-10T15:00:00Z", null, null, null, 10, 5, null, null, null);
}

beforeAll(() => {
  const { getDb } = require("../db");
  const db = getDb();
  seedData(db);
  app = express();
  app.use(express.json());
  app.use("/replay", replayRouter);
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

describe("msBetween", () => {
  test("computes milliseconds between two timestamps", () => {
    expect(msBetween("2026-03-10T10:00:00Z", "2026-03-10T10:00:10Z")).toBe(10000);
  });
  test("returns 0 for same timestamps", () => {
    expect(msBetween("2026-03-10T10:00:00Z", "2026-03-10T10:00:00Z")).toBe(0);
  });
  test("returns 0 for invalid timestamps", () => {
    expect(msBetween("bad", "2026-03-10T10:00:00Z")).toBe(0);
    expect(msBetween("2026-03-10T10:00:00Z", "bad")).toBe(0);
  });
  test("clamps negative differences to 0", () => {
    expect(msBetween("2026-03-10T10:00:10Z", "2026-03-10T10:00:00Z")).toBe(0);
  });
});

describe("classifyEvent", () => {
  test("classifies llm events", () => {
    expect(classifyEvent({ event_type: "llm_call" })).toBe("llm_call");
    expect(classifyEvent({ event_type: "llm_completion" })).toBe("llm_call");
  });
  test("classifies tool events", () => {
    expect(classifyEvent({ event_type: "tool_use" })).toBe("tool_use");
  });
  test("classifies error events", () => {
    expect(classifyEvent({ event_type: "error" })).toBe("error");
    expect(classifyEvent({ event_type: "failure" })).toBe("error");
  });
  test("classifies decision events", () => {
    expect(classifyEvent({ event_type: "decision" })).toBe("decision");
    expect(classifyEvent({ event_type: "planning" })).toBe("decision");
  });
  test("returns type for unknown types", () => {
    expect(classifyEvent({ event_type: "custom" })).toBe("custom");
  });
  test("handles missing event_type", () => {
    expect(classifyEvent({})).toBe("generic");
  });
});

describe("safeJsonParse", () => {
  test("parses valid JSON", () => {
    expect(safeJsonParse('{"a":1}')).toEqual({ a: 1 });
  });
  test("returns fallback for invalid JSON", () => {
    expect(safeJsonParse("not json")).toBeNull();
    expect(safeJsonParse("not json", "default")).toBe("default");
  });
  test("returns fallback for null/undefined", () => {
    expect(safeJsonParse(null)).toBeNull();
    expect(safeJsonParse(undefined, [])).toEqual([]);
  });
});

describe("buildFrames", () => {
  test("returns empty for empty input", () => {
    expect(buildFrames([])).toEqual([]);
    expect(buildFrames(null)).toEqual([]);
  });

  test("builds frames with delays", () => {
    const events = [
      { event_id: "e1", event_type: "llm_call", timestamp: "2026-01-01T00:00:00Z", tokens_in: 10, tokens_out: 5 },
      { event_id: "e2", event_type: "tool_use", timestamp: "2026-01-01T00:00:05Z", tokens_in: 0, tokens_out: 0 },
      { event_id: "e3", event_type: "llm_call", timestamp: "2026-01-01T00:00:15Z", tokens_in: 20, tokens_out: 10 },
    ];
    const frames = buildFrames(events);
    expect(frames).toHaveLength(3);
    expect(frames[0].delay_ms).toBe(0);
    expect(frames[1].delay_ms).toBe(5000);
    expect(frames[1].elapsed_ms).toBe(5000);
    expect(frames[2].delay_ms).toBe(10000);
    expect(frames[2].elapsed_ms).toBe(15000);
  });

  test("applies speed multiplier", () => {
    const events = [
      { event_id: "e1", event_type: "a", timestamp: "2026-01-01T00:00:00Z" },
      { event_id: "e2", event_type: "b", timestamp: "2026-01-01T00:00:10Z" },
    ];
    const frames = buildFrames(events, { speedMultiplier: 2 });
    expect(frames[1].delay_ms).toBe(5000);
  });

  test("caps delay at maxDelayMs", () => {
    const events = [
      { event_id: "e1", event_type: "a", timestamp: "2026-01-01T00:00:00Z" },
      { event_id: "e2", event_type: "b", timestamp: "2026-01-01T01:00:00Z" },
    ];
    const frames = buildFrames(events, { maxDelayMs: 5000 });
    expect(frames[1].delay_ms).toBe(5000);
  });

  test("parses JSON fields", () => {
    const events = [{
      event_id: "e1", event_type: "llm_call", timestamp: "2026-01-01T00:00:00Z",
      input_data: '{"prompt":"hi"}', output_data: '{"text":"hello"}',
      tool_call: '{"name":"search"}', decision_trace: '{"reason":"test"}',
    }];
    const frames = buildFrames(events);
    expect(frames[0].input_data).toEqual({ prompt: "hi" });
    expect(frames[0].output_data).toEqual({ text: "hello" });
  });

  test("single event has zero delay", () => {
    const events = [{ event_id: "e1", event_type: "a", timestamp: "2026-01-01T00:00:00Z" }];
    const frames = buildFrames(events);
    expect(frames).toHaveLength(1);
    expect(frames[0].delay_ms).toBe(0);
  });

  test("classifies events correctly", () => {
    const events = [
      { event_id: "e1", event_type: "llm_call", timestamp: "2026-01-01T00:00:00Z" },
      { event_id: "e2", event_type: "tool_use", timestamp: "2026-01-01T00:00:01Z" },
      { event_id: "e3", event_type: "error", timestamp: "2026-01-01T00:00:02Z" },
    ];
    const frames = buildFrames(events);
    expect(frames[0].category).toBe("llm_call");
    expect(frames[1].category).toBe("tool_use");
    expect(frames[2].category).toBe("error");
  });
});

describe("replaySummary", () => {
  test("returns zeros for empty frames", () => {
    const summary = replaySummary([], null);
    expect(summary.total_frames).toBe(0);
    expect(summary.total_duration_ms).toBe(0);
    expect(summary.models_used).toEqual([]);
  });

  test("computes summary from frames", () => {
    const frames = [
      { event_type: "llm_call", category: "llm_call", model: "gpt-4", tokens_in: 100, tokens_out: 50, delay_ms: 0, elapsed_ms: 0 },
      { event_type: "tool_use", category: "tool_use", model: null, tokens_in: 0, tokens_out: 0, delay_ms: 5000, elapsed_ms: 5000 },
      { event_type: "llm_call", category: "llm_call", model: "gpt-4", tokens_in: 200, tokens_out: 100, delay_ms: 10000, elapsed_ms: 15000 },
    ];
    const session = { session_id: "s1", agent_name: "agent-a", status: "completed" };
    const summary = replaySummary(frames, session);
    expect(summary.total_frames).toBe(3);
    expect(summary.total_duration_ms).toBe(15000);
    expect(summary.event_types.llm_call).toBe(2);
    expect(summary.models_used).toEqual(["gpt-4"]);
    expect(summary.total_tokens_in).toBe(300);
    expect(summary.session_id).toBe("s1");
  });

  test("recommends speed based on duration", () => {
    const short = [
      { event_type: "a", category: "a", model: null, tokens_in: 0, tokens_out: 0, delay_ms: 0, elapsed_ms: 0 },
      { event_type: "b", category: "b", model: null, tokens_in: 0, tokens_out: 0, delay_ms: 1000, elapsed_ms: 1000 },
    ];
    expect(replaySummary(short, null).speed_recommendation).toBe("0.5x");

    const long = [
      { event_type: "a", category: "a", model: null, tokens_in: 0, tokens_out: 0, delay_ms: 0, elapsed_ms: 0 },
      { event_type: "b", category: "b", model: null, tokens_in: 0, tokens_out: 0, delay_ms: 120000, elapsed_ms: 120000 },
    ];
    expect(replaySummary(long, null).speed_recommendation).toBe("5x");
  });
});

describe("GET /replay/:sessionId", () => {
  test("returns full replay for valid session", async () => {
    const res = await request(app).get("/replay/replay-s1");
    expect(res.status).toBe(200);
    expect(res.body.session.session_id).toBe("replay-s1");
    expect(res.body.replay.frames).toHaveLength(5);
    expect(res.body.replay.total_frames).toBe(5);
    expect(res.body.replay.speed).toBe(1);
    expect(res.body.replay.frames[0].delay_ms).toBe(0);
    expect(res.body.replay.frames[1].delay_ms).toBe(10000);
  });

  test("returns 404 for non-existent session", async () => {
    const res = await request(app).get("/replay/nonexistent");
    expect(res.status).toBe(404);
  });

  test("returns 400 for invalid session ID", async () => {
    const res = await request(app).get("/replay/" + "x".repeat(200));
    expect(res.status).toBe(400);
  });

  test("applies speed multiplier", async () => {
    const res = await request(app).get("/replay/replay-s1?speed=2");
    expect(res.status).toBe(200);
    expect(res.body.replay.speed).toBe(2);
    expect(res.body.replay.frames[1].delay_ms).toBe(5000);
  });

  test("clamps speed to valid range", async () => {
    const res = await request(app).get("/replay/replay-s1?speed=0.01");
    expect(res.body.replay.speed).toBe(0.1);
    const res2 = await request(app).get("/replay/replay-s1?speed=999");
    expect(res2.body.replay.speed).toBe(100);
  });

  test("applies maxDelay cap", async () => {
    const res = await request(app).get("/replay/replay-gaps?maxDelay=5000");
    expect(res.body.replay.frames[1].delay_ms).toBe(5000);
  });

  test("supports range slicing", async () => {
    const res = await request(app).get("/replay/replay-s1?from=1&to=3");
    expect(res.body.replay.frames).toHaveLength(2);
    expect(res.body.replay.frames[0].index).toBe(1);
  });

  test("handles empty session", async () => {
    const res = await request(app).get("/replay/replay-empty");
    expect(res.body.replay.frames).toHaveLength(0);
    expect(res.body.replay.total_frames).toBe(0);
  });

  test("handles single-event session", async () => {
    const res = await request(app).get("/replay/replay-single");
    expect(res.body.replay.frames).toHaveLength(1);
    expect(res.body.replay.frames[0].delay_ms).toBe(0);
  });

  test("parses JSON fields in frames", async () => {
    const res = await request(app).get("/replay/replay-s1");
    expect(res.body.replay.frames[0].input_data).toEqual({ prompt: "hello" });
    expect(res.body.replay.frames[1].tool_call).toEqual({ name: "search", args: "query" });
  });

  test("includes event categories", async () => {
    const res = await request(app).get("/replay/replay-s1");
    const cats = res.body.replay.frames.map(f => f.category);
    expect(cats).toEqual(["llm_call", "tool_use", "llm_call", "error", "decision"]);
  });
});

describe("GET /replay/:sessionId/frame/:index", () => {
  test("returns single frame", async () => {
    const res = await request(app).get("/replay/replay-s1/frame/2");
    expect(res.status).toBe(200);
    expect(res.body.frame.index).toBe(2);
    expect(res.body.total_frames).toBe(5);
    expect(res.body.has_next).toBe(true);
    expect(res.body.has_previous).toBe(true);
  });

  test("first frame has no previous", async () => {
    const res = await request(app).get("/replay/replay-s1/frame/0");
    expect(res.body.has_previous).toBe(false);
    expect(res.body.has_next).toBe(true);
  });

  test("last frame has no next", async () => {
    const res = await request(app).get("/replay/replay-s1/frame/4");
    expect(res.body.has_next).toBe(false);
    expect(res.body.has_previous).toBe(true);
  });

  test("returns 404 for out-of-range index", async () => {
    const res = await request(app).get("/replay/replay-s1/frame/99");
    expect(res.status).toBe(404);
    expect(res.body.total_frames).toBe(5);
  });

  test("returns 400 for negative index", async () => {
    const res = await request(app).get("/replay/replay-s1/frame/-1");
    expect(res.status).toBe(400);
  });

  test("returns 404 for non-existent session", async () => {
    const res = await request(app).get("/replay/nonexistent/frame/0");
    expect(res.status).toBe(404);
  });
});

describe("GET /replay/:sessionId/summary", () => {
  test("returns summary for valid session", async () => {
    const res = await request(app).get("/replay/replay-s1/summary");
    expect(res.status).toBe(200);
    expect(res.body.total_frames).toBe(5);
    expect(res.body.session_id).toBe("replay-s1");
    expect(res.body.models_used).toContain("gpt-4");
    expect(res.body.total_tokens_in).toBe(350);
    expect(res.body.speed_recommendation).toBeDefined();
  });

  test("returns 404 for non-existent session", async () => {
    const res = await request(app).get("/replay/nonexistent/summary");
    expect(res.status).toBe(404);
  });

  test("handles empty session", async () => {
    const res = await request(app).get("/replay/replay-empty/summary");
    expect(res.body.total_frames).toBe(0);
    expect(res.body.total_duration_ms).toBe(0);
  });

  test("includes category breakdown", async () => {
    const res = await request(app).get("/replay/replay-s1/summary");
    expect(res.body.categories.llm_call).toBe(2);
    expect(res.body.categories.tool_use).toBe(1);
    expect(res.body.categories.error).toBe(1);
    expect(res.body.categories.decision).toBe(1);
  });

  test("computes average and max delay", async () => {
    const res = await request(app).get("/replay/replay-s1/summary");
    expect(res.body.avg_delay_ms).toBe(10000);
    expect(res.body.max_delay_ms).toBe(10000);
  });
});
