const express = require("express");
const request = require("supertest");
const { getDb } = require("../db");

process.env.DB_PATH = ":memory:";

const postmortemRouter = require("../routes/postmortem");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/postmortem", postmortemRouter);
  return app;
}

function seedData() {
  const db = getDb();

  const insertSession = db.prepare(
    `INSERT OR IGNORE INTO sessions (session_id, agent_name, started_at, ended_at, total_tokens_in, total_tokens_out, status)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  const insertEvent = db.prepare(
    `INSERT OR IGNORE INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out, duration_ms, input_data, output_data, tool_call)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  );

  // Session with multiple errors (should produce SEV-1/2/3 postmortem)
  insertSession.run("pm-err-1", "agent-alpha", "2026-03-01T10:00:00Z", "2026-03-01T10:30:00Z", 500, 1000, "error");

  // Normal event
  insertEvent.run("pm-ev1", "pm-err-1", "llm_call", "2026-03-01T10:01:00Z", "gpt-4", 200, 500, 1200, null, null, null);
  // Error events
  insertEvent.run("pm-ev2", "pm-err-1", "error", "2026-03-01T10:05:00Z", "gpt-4", 50, 20, 100, null, JSON.stringify({ error: "Rate limit exceeded" }), null);
  insertEvent.run("pm-ev3", "pm-err-1", "tool_error", "2026-03-01T10:10:00Z", "gpt-4", 30, 10, 50, null, JSON.stringify({ error: "Connection refused" }), JSON.stringify({ tool_name: "web_search" }));
  insertEvent.run("pm-ev4", "pm-err-1", "error", "2026-03-01T10:15:00Z", "gpt-4", 40, 15, 200, null, JSON.stringify({ error: "Rate limit exceeded" }), null);
  insertEvent.run("pm-ev5", "pm-err-1", "timeout", "2026-03-01T10:20:00Z", "gpt-4", 0, 0, 15000, null, null, null);
  // Final normal event
  insertEvent.run("pm-ev6", "pm-err-1", "llm_call", "2026-03-01T10:25:00Z", "gpt-4", 180, 400, 900, null, null, null);

  // Clean session (no errors)
  insertSession.run("pm-clean-1", "agent-beta", "2026-03-01T11:00:00Z", "2026-03-01T11:30:00Z", 300, 600, "completed");
  insertEvent.run("pm-cev1", "pm-clean-1", "llm_call", "2026-03-01T11:05:00Z", "claude-3", 100, 200, 500, null, null, null);
  insertEvent.run("pm-cev2", "pm-clean-1", "tool_call", "2026-03-01T11:10:00Z", "claude-3", 50, 100, 300, null, null, JSON.stringify({ tool_name: "calculator" }));

  // Session with rate_limit events only
  insertSession.run("pm-rl-1", "agent-gamma", "2026-03-01T12:00:00Z", "2026-03-01T12:10:00Z", 100, 200, "error");
  insertEvent.run("pm-rlev1", "pm-rl-1", "rate_limit", "2026-03-01T12:02:00Z", "gpt-4", 0, 0, 100, null, null, null);
  insertEvent.run("pm-rlev2", "pm-rl-1", "rate_limit", "2026-03-01T12:04:00Z", "gpt-4", 0, 0, 80, null, null, null);
  insertEvent.run("pm-rlev3", "pm-rl-1", "llm_call", "2026-03-01T12:06:00Z", "gpt-4", 100, 200, 600, null, null, null);
}

beforeAll(() => {
  seedData();
});

afterAll(() => {
  const db = getDb();
  db.close();
});

describe("POST /postmortem/:sessionId", () => {
  test("generates postmortem for session with errors", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.status).toBe(200);
    expect(res.body.incident_id).toMatch(/^INC-/);
    expect(res.body.severity).toMatch(/^SEV-[1-4]$/);
    expect(res.body.session_id).toBe("pm-err-1");
    expect(res.body.event_count).toBe(6);
    expect(typeof res.body.summary).toBe("string");
    expect(typeof res.body.duration_ms).toBe("number");
    expect(res.body.generated_at).toBeTruthy();
  });

  test("returns no-incident for clean session", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-clean-1");

    expect(res.status).toBe(200);
    expect(res.body.incident_id).toBe("INC-NONE");
    expect(res.body.severity).toBe("SEV-4");
    expect(res.body.summary).toContain("No errors");
  });

  test("returns 404 for nonexistent session", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/nonexistent-id");

    expect(res.status).toBe(404);
    expect(res.body.error).toBeTruthy();
  });

  test("includes root cause analysis", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.root_causes)).toBe(true);
    expect(res.body.root_causes.length).toBeGreaterThan(0);

    const cause = res.body.root_causes[0];
    expect(typeof cause.description).toBe("string");
    expect(typeof cause.confidence).toBe("number");
    expect(cause.confidence).toBeGreaterThan(0);
    expect(cause.confidence).toBeLessThanOrEqual(1);
    expect(typeof cause.category).toBe("string");
    expect(Array.isArray(cause.evidence)).toBe(true);
  });

  test("includes impact assessment", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.status).toBe(200);
    const impact = res.body.impact;
    expect(impact).toBeTruthy();
    expect(impact.error_count).toBe(4);
    expect(impact.total_events).toBe(6);
    expect(typeof impact.error_rate).toBe("number");
    expect(impact.error_rate).toBeGreaterThan(0);
    expect(Array.isArray(impact.affected_models)).toBe(true);
    expect(impact.affected_models).toContain("gpt-4");
    expect(typeof impact.downtime_ms).toBe("number");
    expect(typeof impact.tokens_wasted).toBe("number");
    expect(typeof impact.estimated_cost_impact).toBe("number");
    expect(typeof impact.user_facing).toBe("boolean");
  });

  test("includes incident timeline", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.timeline)).toBe(true);
    expect(res.body.timeline.length).toBeGreaterThan(0);

    const entry = res.body.timeline[0];
    expect(typeof entry.timestamp).toBe("string");
    expect(typeof entry.elapsed_ms).toBe("number");
    expect(typeof entry.event_type).toBe("string");
    expect(typeof entry.description).toBe("string");
    expect(["error", "warning", "info"]).toContain(entry.severity);
  });

  test("identifies tool failures in root causes", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    const toolCause = res.body.root_causes.find(c => c.category === "tool_failure");
    expect(toolCause).toBeTruthy();
    expect(toolCause.description).toContain("web_search");
  });

  test("identifies repeated errors in root causes", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    const repeatedCause = res.body.root_causes.find(c => c.category === "repeated_error");
    expect(repeatedCause).toBeTruthy();
    expect(repeatedCause.affected_events).toBeGreaterThanOrEqual(2);
  });

  test("identifies timeout events in root causes", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    const timeoutCause = res.body.root_causes.find(c => c.category === "timeout");
    expect(timeoutCause).toBeTruthy();
    expect(timeoutCause.affected_events).toBe(1);
  });

  test("affected tools list includes web_search", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.body.impact.affected_tools).toContain("web_search");
  });

  test("user_facing is true when session has tool_error and error events", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    expect(res.body.impact.user_facing).toBe(true);
  });

  test("generates postmortem for rate-limit-only session", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-rl-1");

    expect(res.status).toBe(200);
    expect(res.body.severity).toMatch(/^SEV-[1-4]$/);
    expect(res.body.impact.error_count).toBe(2);

    const rlCause = res.body.root_causes.find(c => c.category === "rate_limit");
    expect(rlCause).toBeTruthy();
    expect(rlCause.affected_events).toBe(2);
  });

  test("root causes are sorted by confidence descending", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    const causes = res.body.root_causes;
    for (let i = 1; i < causes.length; i++) {
      expect(causes[i - 1].confidence).toBeGreaterThanOrEqual(causes[i].confidence);
    }
  });

  test("timeline includes first and last events", async () => {
    const app = createApp();
    const res = await request(app).post("/postmortem/pm-err-1");

    const timestamps = res.body.timeline.map(t => t.timestamp);
    expect(timestamps).toContain("2026-03-01T10:01:00Z");
    expect(timestamps).toContain("2026-03-01T10:25:00Z");
  });

  test("severity scales with error rate", async () => {
    const app = createApp();
    // pm-err-1 has 4 errors out of 6 events (67%) — should be SEV-1
    const res = await request(app).post("/postmortem/pm-err-1");
    expect(res.body.severity).toBe("SEV-1");

    // pm-rl-1 has 2 errors out of 3 events (67%) — should also be SEV-1
    const res2 = await request(app).post("/postmortem/pm-rl-1");
    expect(res2.body.severity).toBe("SEV-1");
  });
});

describe("GET /postmortem/candidates", () => {
  test("returns candidate sessions with errors", async () => {
    const app = createApp();
    const res = await request(app).get("/postmortem/candidates");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.candidates)).toBe(true);
    expect(typeof res.body.total).toBe("number");
  });

  test("filters by min_errors", async () => {
    const app = createApp();
    // pm-err-1 has 4 errors, pm-rl-1 has 2
    const res = await request(app).get("/postmortem/candidates?min_errors=3");

    expect(res.status).toBe(200);
    const ids = res.body.candidates.map(c => c.session_id);
    expect(ids).toContain("pm-err-1");
    expect(ids).not.toContain("pm-rl-1");
  });

  test("respects limit parameter", async () => {
    const app = createApp();
    const res = await request(app).get("/postmortem/candidates?limit=1");

    expect(res.status).toBe(200);
    expect(res.body.candidates.length).toBeLessThanOrEqual(1);
  });

  test("candidate rows include agent_name and error_count", async () => {
    const app = createApp();
    const res = await request(app).get("/postmortem/candidates?min_errors=2");

    expect(res.status).toBe(200);
    if (res.body.candidates.length > 0) {
      const c = res.body.candidates[0];
      expect(typeof c.session_id).toBe("string");
      expect(typeof c.error_count).toBe("number");
    }
  });
});
