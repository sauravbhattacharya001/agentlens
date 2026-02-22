/* ── Session Annotations — Backend Tests ────────────────────────────── */

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
const annotationsRouter = require("../routes/annotations");

let app;
const SESSION_ID = "test-session-001";
const SESSION_ID_2 = "test-session-002";
const EVENT_ID = "test-event-001";

function seedData() {
  const db = require("../db").getDb();
  db.exec("DELETE FROM sessions");
  // Drop annotations if it exists (clean slate)
  try { db.exec("DROP TABLE IF EXISTS annotations"); } catch (_) {}

  db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)"
  ).run(SESSION_ID, "test-agent", "2025-01-01T00:00:00Z", "active");
  db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at, status) VALUES (?, ?, ?, ?)"
  ).run(SESSION_ID_2, "agent-2", "2025-01-02T00:00:00Z", "active");
  db.prepare(
    "INSERT INTO events (event_id, session_id, event_type, timestamp) VALUES (?, ?, ?, ?)"
  ).run(EVENT_ID, SESSION_ID, "llm_call", "2025-01-01T00:01:00Z");
}

beforeAll(() => {
  app = express();
  app.use(express.json());
  // Session-scoped routes
  app.use("/sessions", annotationsRouter);
  // Global routes
  app.use("/annotations", annotationsRouter);
  seedData();
});

afterAll(() => {
  if (mockDb) mockDb.close();
});

// ── Create Annotation ───────────────────────────────────────────────

describe("POST /sessions/:id/annotations", () => {
  it("creates a basic annotation", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "This is a test note" });
    expect(res.status).toBe(201);
    expect(res.body.annotation_id).toBeTruthy();
    expect(res.body.session_id).toBe(SESSION_ID);
    expect(res.body.text).toBe("This is a test note");
    expect(res.body.author).toBe("system");
    expect(res.body.type).toBe("note");
    expect(res.body.created_at).toBeTruthy();
    expect(res.body.updated_at).toBeTruthy();
  });

  it("creates annotation with all fields", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({
        text: "Bug found: model hallucinated",
        author: "alice",
        type: "bug",
        event_id: EVENT_ID,
      });
    expect(res.status).toBe(201);
    expect(res.body.text).toBe("Bug found: model hallucinated");
    expect(res.body.author).toBe("alice");
    expect(res.body.type).toBe("bug");
    expect(res.body.event_id).toBe(EVENT_ID);
  });

  it("creates annotation with each valid type", async () => {
    const types = ["note", "bug", "insight", "warning", "milestone"];
    for (const type of types) {
      const res = await request(app)
        .post(`/sessions/${SESSION_ID}/annotations`)
        .send({ text: `Testing ${type}`, type });
      expect(res.status).toBe(201);
      expect(res.body.type).toBe(type);
    }
  });

  it("returns 400 for missing text", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Validation failed");
  });

  it("returns 400 for empty text", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "   " });
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid type", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "test", type: "invalid_type" });
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid event_id", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "test", event_id: "nonexistent-event" });
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("event_id not found");
  });

  it("returns 404 for nonexistent session", async () => {
    const res = await request(app)
      .post("/sessions/nonexistent/annotations")
      .send({ text: "test" });
    expect(res.status).toBe(404);
  });

  it("returns 400 for text exceeding max length", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "x".repeat(4001) });
    expect(res.status).toBe(400);
  });

  it("trims whitespace from text", async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "  trimmed  " });
    expect(res.status).toBe(201);
    expect(res.body.text).toBe("trimmed");
  });
});

// ── List Annotations ────────────────────────────────────────────────

describe("GET /sessions/:id/annotations", () => {
  it("lists all annotations for a session", async () => {
    const res = await request(app).get(`/sessions/${SESSION_ID}/annotations`);
    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe(SESSION_ID);
    expect(res.body.total).toBeGreaterThanOrEqual(1);
    expect(res.body.annotations).toBeInstanceOf(Array);
    expect(res.body.type_breakdown).toBeDefined();
  });

  it("returns annotations in chronological order", async () => {
    const res = await request(app).get(`/sessions/${SESSION_ID}/annotations`);
    const times = res.body.annotations.map((a) => a.created_at);
    for (let i = 1; i < times.length; i++) {
      expect(times[i] >= times[i - 1]).toBe(true);
    }
  });

  it("filters by type", async () => {
    const res = await request(app)
      .get(`/sessions/${SESSION_ID}/annotations?type=bug`);
    expect(res.status).toBe(200);
    res.body.annotations.forEach((a) => {
      expect(a.type).toBe("bug");
    });
  });

  it("filters by author", async () => {
    const res = await request(app)
      .get(`/sessions/${SESSION_ID}/annotations?author=alice`);
    expect(res.status).toBe(200);
    res.body.annotations.forEach((a) => {
      expect(a.author).toBe("alice");
    });
  });

  it("supports pagination", async () => {
    const res = await request(app)
      .get(`/sessions/${SESSION_ID}/annotations?limit=2&offset=0`);
    expect(res.status).toBe(200);
    expect(res.body.returned).toBeLessThanOrEqual(2);
    expect(res.body.limit).toBe(2);
    expect(res.body.offset).toBe(0);
  });

  it("returns 404 for nonexistent session", async () => {
    const res = await request(app).get("/sessions/nonexistent/annotations");
    expect(res.status).toBe(404);
  });

  it("includes type breakdown", async () => {
    const res = await request(app).get(`/sessions/${SESSION_ID}/annotations`);
    expect(res.body.type_breakdown).toBeDefined();
    expect(typeof res.body.type_breakdown).toBe("object");
  });
});

// ── Update Annotation ───────────────────────────────────────────────

describe("PUT /sessions/:id/annotations/:annId", () => {
  let annotationId;

  beforeAll(async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "To be updated", author: "bob", type: "note" });
    annotationId = res.body.annotation_id;
  });

  it("updates text", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ text: "Updated text" });
    expect(res.status).toBe(200);
    expect(res.body.text).toBe("Updated text");
  });

  it("updates type", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ type: "insight" });
    expect(res.status).toBe(200);
    expect(res.body.type).toBe("insight");
  });

  it("updates author", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ author: "charlie" });
    expect(res.status).toBe(200);
    expect(res.body.author).toBe("charlie");
  });

  it("updates updated_at timestamp", async () => {
    const before = await request(app)
      .get(`/sessions/${SESSION_ID}/annotations`);
    const ann = before.body.annotations.find((a) => a.annotation_id === annotationId);
    const oldUpdated = ann.updated_at;

    // Small delay to ensure timestamp differs
    await new Promise((r) => setTimeout(r, 10));

    await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ text: "Timestamp check" });

    const after = await request(app).get(`/sessions/${SESSION_ID}/annotations`);
    const updated = after.body.annotations.find((a) => a.annotation_id === annotationId);
    expect(updated.updated_at >= oldUpdated).toBe(true);
  });

  it("returns 400 for no valid fields", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({});
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid type", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ type: "invalid" });
    expect(res.status).toBe(400);
  });

  it("returns 400 for empty text", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/${annotationId}`)
      .send({ text: "" });
    expect(res.status).toBe(400);
  });

  it("returns 404 for nonexistent annotation", async () => {
    const res = await request(app)
      .put(`/sessions/${SESSION_ID}/annotations/nonexistent`)
      .send({ text: "test" });
    expect(res.status).toBe(404);
  });
});

// ── Delete Annotation ───────────────────────────────────────────────

describe("DELETE /sessions/:id/annotations/:annId", () => {
  let annotationId;

  beforeAll(async () => {
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text: "To be deleted" });
    annotationId = res.body.annotation_id;
  });

  it("deletes an annotation", async () => {
    const res = await request(app)
      .delete(`/sessions/${SESSION_ID}/annotations/${annotationId}`);
    expect(res.status).toBe(200);
    expect(res.body.deleted).toBe(true);
    expect(res.body.annotation_id).toBe(annotationId);
  });

  it("returns 404 after deletion", async () => {
    const res = await request(app)
      .delete(`/sessions/${SESSION_ID}/annotations/${annotationId}`);
    expect(res.status).toBe(404);
  });

  it("returns 404 for nonexistent annotation", async () => {
    const res = await request(app)
      .delete(`/sessions/${SESSION_ID}/annotations/nonexistent`);
    expect(res.status).toBe(404);
  });
});

// ── Recent Annotations ──────────────────────────────────────────────

describe("GET /annotations (recent across all sessions)", () => {
  beforeAll(async () => {
    await request(app)
      .post(`/sessions/${SESSION_ID_2}/annotations`)
      .send({ text: "Note on session 2", type: "insight" });
  });

  it("lists recent annotations across sessions", async () => {
    const res = await request(app).get("/annotations");
    expect(res.status).toBe(200);
    expect(res.body.total).toBeGreaterThanOrEqual(1);
    expect(res.body.annotations).toBeInstanceOf(Array);
  });

  it("includes agent_name in results", async () => {
    const res = await request(app).get("/annotations");
    res.body.annotations.forEach((a) => {
      expect(a.agent_name).toBeDefined();
    });
  });

  it("filters by type", async () => {
    const res = await request(app).get("/annotations?type=insight");
    expect(res.status).toBe(200);
    res.body.annotations.forEach((a) => {
      expect(a.type).toBe("insight");
    });
  });

  it("supports limit", async () => {
    const res = await request(app).get("/annotations?limit=2");
    expect(res.status).toBe(200);
    expect(res.body.annotations.length).toBeLessThanOrEqual(2);
  });

  it("returns annotations in reverse chronological order", async () => {
    const res = await request(app).get("/annotations");
    const times = res.body.annotations.map((a) => a.created_at);
    for (let i = 1; i < times.length; i++) {
      expect(times[i] <= times[i - 1]).toBe(true);
    }
  });
});

// ── Edge Cases ──────────────────────────────────────────────────────

describe("Edge cases", () => {
  it("can create multiple annotations on same session", async () => {
    for (let i = 0; i < 3; i++) {
      const res = await request(app)
        .post(`/sessions/${SESSION_ID_2}/annotations`)
        .send({ text: `Annotation ${i}` });
      expect(res.status).toBe(201);
    }
    const list = await request(app).get(`/sessions/${SESSION_ID_2}/annotations`);
    expect(list.body.total).toBeGreaterThanOrEqual(3);
  });

  it("annotation_id is unique", async () => {
    const ids = new Set();
    for (let i = 0; i < 5; i++) {
      const res = await request(app)
        .post(`/sessions/${SESSION_ID}/annotations`)
        .send({ text: `Unique ${i}` });
      expect(ids.has(res.body.annotation_id)).toBe(false);
      ids.add(res.body.annotation_id);
    }
  });

  it("handles special characters in text", async () => {
    const text = 'Test <script>alert("xss")</script> & "quotes" \' slashes';
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text });
    expect(res.status).toBe(201);
    expect(res.body.text).toBe(text);
  });

  it("handles unicode in text", async () => {
    const text = "Testing 🎯 unicode 日本語 العربية";
    const res = await request(app)
      .post(`/sessions/${SESSION_ID}/annotations`)
      .send({ text });
    expect(res.status).toBe(201);
    expect(res.body.text).toBe(text);
  });

  it("multi-type filter works", async () => {
    const res = await request(app)
      .get(`/sessions/${SESSION_ID}/annotations?type=note,bug`);
    expect(res.status).toBe(200);
    res.body.annotations.forEach((a) => {
      expect(["note", "bug"]).toContain(a.type);
    });
  });
});
