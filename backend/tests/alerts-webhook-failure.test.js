/* ── Alert evaluate — webhook delivery failure path ─────────────────── */

// This suite isolates the try/catch around fireWebhooks in POST
// /alerts/evaluate: when webhook delivery throws, the alert must still
// fire and persist, with result.webhooks defaulting to [] (never a 500).

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
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          duration_ms REAL
        );
      `);
    }
    return mockDb;
  },
}));

// Force webhook delivery to reject so the catch branch executes.
jest.mock("../routes/webhooks", () => {
  const express = require("express");
  const router = express.Router();
  return {
    __esModule: false,
    // routes/alerts destructures { fireWebhooks } from this module.
    fireWebhooks: jest.fn().mockRejectedValue(new Error("boom: collector down")),
    // server.js mounts the router export; keep a stub so requiring is safe.
    router,
  };
});

const express = require("express");
const request = require("supertest");
const alertsRouter = require("../routes/alerts");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/alerts", alertsRouter);
  return app;
}

describe("POST /alerts/evaluate — webhook delivery failure", () => {
  let app;
  let consoleErrorSpy;

  beforeAll(() => {
    app = createApp();
  });

  beforeEach(async () => {
    // A GET runs ensureAlertsTable() so the alert_* tables exist before we clear them.
    await request(app).get("/alerts/rules");
    const { getDb } = require("../db");
    const db = getDb();
    db.exec("DELETE FROM alert_events; DELETE FROM alert_rules; DELETE FROM sessions;");
    // Seed a session so total_tokens > 0 and the rule triggers.
    db.prepare(
      `INSERT INTO sessions (session_id, agent_name, started_at, total_tokens_in, total_tokens_out)
       VALUES ('s1', 'a', ?, 500, 500)`
    ).run(new Date().toISOString());
    consoleErrorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it("still fires the alert and returns webhooks:[] when delivery throws", async () => {
    await request(app).post("/alerts/rules").send({
      name: "TokenGuard", metric: "total_tokens", operator: ">", threshold: 1, window_minutes: 120,
    });

    const res = await request(app).post("/alerts/evaluate");
    expect(res.status).toBe(200);
    expect(res.body.fired).toBe(1);

    const fired = res.body.results.find(r => r.status === "fired");
    expect(fired).toBeDefined();
    expect(fired.alert_id).toBeTruthy();
    expect(fired.webhooks).toEqual([]);

    // Failure was logged, not swallowed silently, and the alert persisted.
    expect(consoleErrorSpy).toHaveBeenCalledWith("Webhook delivery error:", expect.any(Error));

    const { getDb } = require("../db");
    const stored = getDb().prepare("SELECT COUNT(*) AS c FROM alert_events").get().c;
    expect(stored).toBe(1);
  });
});
