/* ── sla.js route — regression tests for malformed JSON columns ───────
 *
 * Bug: GET /sla/history and GET /sla/summary called JSON.parse() directly
 * on the `metrics` and `violations` columns. A single corrupt row (truncated
 * write, manual DB edit, schema-default override with non-JSON) would crash
 * the entire route with a 500 and break the dashboard.
 *
 * Fix: route now uses safeJsonParse + parseJsonArray helpers that fall back
 * to {} / [] when the column is null, undefined, or unparseable, and also
 * coerce non-array `violations` to [] so the .length read in /summary is
 * always safe.
 *
 * These tests exercise the live router with a real (in-memory) SQLite DB
 * to guard against regressions.
 */

"use strict";

const path = require("path");
const express = require("express");
const request = require("supertest");

describe("sla route — malformed JSON column handling", () => {
  let app;
  let prevDbPath;
  let prevNodeEnv;

  beforeAll(() => {
    prevDbPath = process.env.DB_PATH;
    prevNodeEnv = process.env.NODE_ENV;
    // ":memory:" + reset modules so the DB singleton is fresh for this suite
    process.env.DB_PATH = ":memory:";
    process.env.NODE_ENV = "test";
    jest.resetModules();

    const db = require("../db").getDb();
    db.exec(`
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
        duration_ms REAL
      );
      CREATE TABLE IF NOT EXISTS sla_targets (
        agent_name TEXT NOT NULL,
        metric TEXT NOT NULL CHECK(metric IN (
          'p50_latency_ms','p95_latency_ms','p99_latency_ms',
          'error_rate_pct','avg_tokens_in','avg_tokens_out',
          'max_duration_ms','min_throughput'
        )),
        threshold REAL NOT NULL,
        comparison TEXT NOT NULL DEFAULT 'lte' CHECK(comparison IN ('lte','gte','lt','gt','eq')),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (agent_name, metric)
      );
      CREATE TABLE IF NOT EXISTS sla_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT NOT NULL,
        window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,
        metrics TEXT NOT NULL DEFAULT '{}',
        violations TEXT NOT NULL DEFAULT '[]',
        compliance_pct REAL NOT NULL DEFAULT 100,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_sla_snapshots_agent ON sla_snapshots(agent_name);
    `);

    const slaRouter = require("../routes/sla");
    app = express();
    app.use(express.json());
    app.use("/sla", slaRouter);
  });

  afterAll(() => {
    if (prevDbPath === undefined) delete process.env.DB_PATH; else process.env.DB_PATH = prevDbPath;
    if (prevNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prevNodeEnv;
  });

  function seedSnapshot(agent_name, metrics, violations) {
    const { getDb } = require("../db");
    const db = getDb();
    db.prepare(`
      INSERT INTO sla_snapshots
        (agent_name, window_start, window_end, metrics, violations, compliance_pct)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(agent_name, "2026-03-10T00:00:00Z", "2026-03-10T12:00:00Z",
           metrics, violations, 95.5);
  }

  function seedTarget(agent_name) {
    const { getDb } = require("../db");
    const db = getDb();
    db.prepare(`
      INSERT OR REPLACE INTO sla_targets (agent_name, metric, threshold, comparison, created_at, updated_at)
      VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
    `).run(agent_name, "p95_latency_ms", 5000, "lte");
  }

  // ── /history regression ───────────────────────────────────────────

  test("GET /sla/history returns 200 with valid JSON columns", async () => {
    seedSnapshot("agent-valid",
      JSON.stringify({ p95_latency_ms: 1234 }),
      JSON.stringify([{ metric: "p95_latency_ms", threshold: 5000, value: 1234 }]));

    const res = await request(app).get("/sla/history?agent_name=agent-valid");
    expect(res.status).toBe(200);
    expect(res.body.snapshots).toHaveLength(1);
    expect(res.body.snapshots[0].metrics).toEqual({ p95_latency_ms: 1234 });
    expect(res.body.snapshots[0].violations).toHaveLength(1);
  });

  test("GET /sla/history does NOT crash on malformed metrics/violations (regression)", async () => {
    // Before the fix: JSON.parse("not-json") throws -> wrapRoute -> 500.
    seedSnapshot("agent-bad", "not-json{{{", "also-broken]]]");

    const res = await request(app).get("/sla/history?agent_name=agent-bad");
    expect(res.status).toBe(200);
    expect(res.body.snapshots).toHaveLength(1);
    // Falls back to safe defaults instead of bubbling SyntaxError to the client.
    expect(res.body.snapshots[0].metrics).toEqual({});
    expect(res.body.snapshots[0].violations).toEqual([]);
  });

  test("GET /sla/history coerces non-array violations to []", async () => {
    // Storing `null` as a JSON value (not SQL NULL) used to set
    // violations: null at the API layer, which then exploded when /summary
    // dereferenced .length. Now it should normalise to [].
    seedSnapshot("agent-null-violations",
      JSON.stringify({ p95_latency_ms: 99 }),
      "null");

    const res = await request(app).get("/sla/history?agent_name=agent-null-violations");
    expect(res.status).toBe(200);
    expect(res.body.snapshots[0].violations).toEqual([]);
  });

  test("GET /sla/history tolerates an object stored in violations", async () => {
    seedSnapshot("agent-obj-violations",
      JSON.stringify({}),
      JSON.stringify({ surprise: "this should have been an array" }));

    const res = await request(app).get("/sla/history?agent_name=agent-obj-violations");
    expect(res.status).toBe(200);
    expect(res.body.snapshots[0].violations).toEqual([]);
  });

  // ── /summary regression ───────────────────────────────────────────

  test("GET /sla/summary does NOT crash when latest snapshot has malformed violations (regression)", async () => {
    seedTarget("agent-summary-bad");
    seedSnapshot("agent-summary-bad",
      JSON.stringify({ p95_latency_ms: 200 }),
      "definitely not json");

    const res = await request(app).get("/sla/summary");
    expect(res.status).toBe(200);
    const found = res.body.agents.find(a => a.agent_name === "agent-summary-bad");
    expect(found).toBeTruthy();
    expect(found.latest_check).toBeTruthy();
    // violation_count must be a number, not crash. Malformed -> 0.
    expect(found.latest_check.violation_count).toBe(0);
  });

  test("GET /sla/summary reports correct violation_count for valid snapshot", async () => {
    seedTarget("agent-summary-ok");
    seedSnapshot("agent-summary-ok",
      JSON.stringify({ p95_latency_ms: 9000 }),
      JSON.stringify([
        { metric: "p95_latency_ms", threshold: 5000, value: 9000 },
        { metric: "error_rate_pct", threshold: 1, value: 5 },
      ]));

    const res = await request(app).get("/sla/summary");
    expect(res.status).toBe(200);
    const found = res.body.agents.find(a => a.agent_name === "agent-summary-ok");
    expect(found).toBeTruthy();
    expect(found.latest_check.violation_count).toBe(2);
  });

  test("GET /sla/summary handles agent with no snapshots", async () => {
    seedTarget("agent-no-history");
    const res = await request(app).get("/sla/summary");
    expect(res.status).toBe(200);
    const found = res.body.agents.find(a => a.agent_name === "agent-no-history");
    expect(found).toBeTruthy();
    expect(found.latest_check).toBeNull();
  });
});
