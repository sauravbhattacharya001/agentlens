/* ── request-helpers.js — unit tests ─────────────────────────────────── */

const express = require("express");
const request = require("supertest");
const {
  parseLimit,
  parseOffset,
  parsePagination,
  parseDays,
  daysAgoCutoff,
  requireSessionId,
  wrapRoute,
} = require("../lib/request-helpers");

// ── parseLimit ──────────────────────────────────────────────────────

describe("parseLimit", () => {
  test("returns default when input is undefined", () => {
    expect(parseLimit(undefined)).toBe(50);
  });

  test("returns default when input is NaN", () => {
    expect(parseLimit("abc")).toBe(50);
  });

  test("returns custom default", () => {
    expect(parseLimit(undefined, 100)).toBe(100);
  });

  test("clamps to max", () => {
    expect(parseLimit("999", 50, 200)).toBe(200);
  });

  test("clamps to min", () => {
    expect(parseLimit("-5", 50, 200, 1)).toBe(1);
  });

  test("returns default for zero (falsy)", () => {
    expect(parseLimit("0", 50, 200, 1)).toBe(50);
  });

  test("returns valid value within range", () => {
    expect(parseLimit("25", 50, 200, 1)).toBe(25);
  });

  test("handles string numbers", () => {
    expect(parseLimit("100")).toBe(100);
  });
});

// ── parseOffset ─────────────────────────────────────────────────────

describe("parseOffset", () => {
  test("returns 0 when undefined", () => {
    expect(parseOffset(undefined)).toBe(0);
  });

  test("returns 0 for NaN", () => {
    expect(parseOffset("abc")).toBe(0);
  });

  test("returns 0 for negative", () => {
    expect(parseOffset("-5")).toBe(0);
  });

  test("returns 0 for zero", () => {
    expect(parseOffset("0")).toBe(0);
  });

  test("returns positive value", () => {
    expect(parseOffset("10")).toBe(10);
  });
});

// ── parsePagination ─────────────────────────────────────────────────

describe("parsePagination", () => {
  test("extracts limit and offset from query", () => {
    const result = parsePagination({ limit: "25", offset: "10" });
    expect(result).toEqual({ limit: 25, offset: 10 });
  });

  test("uses defaults when missing", () => {
    const result = parsePagination({});
    expect(result).toEqual({ limit: 50, offset: 0 });
  });

  test("respects custom options", () => {
    const result = parsePagination(
      { limit: "5000" },
      { defaultLimit: 100, maxLimit: 500, minLimit: 10 }
    );
    expect(result).toEqual({ limit: 500, offset: 0 });
  });
});

// ── requireSessionId ────────────────────────────────────────────────

describe("requireSessionId", () => {
  function makeApp() {
    const app = express();
    app.get("/:id", requireSessionId, (req, res) => {
      res.json({ ok: true, id: req.params.id });
    });
    return app;
  }

  test("passes valid session IDs", async () => {
    const app = makeApp();
    const res = await request(app).get("/session-123");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });

  test("rejects empty ID", async () => {
    // Express won't match /:id for empty path, so test with invalid chars
    const app = makeApp();
    const res = await request(app).get("/%00%01%02");
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Invalid session ID/);
  });

  test("rejects IDs with special characters", async () => {
    const app = makeApp();
    const res = await request(app).get("/id<script>");
    expect(res.status).toBe(400);
  });

  test("also accepts a `:sessionId` route param (regression for #189)", async () => {
    // Some routes (e.g. /pricing/costs/:sessionId) name the param
    // `sessionId` instead of `id`. The middleware should validate both.
    const app = express();
    app.get("/costs/:sessionId", requireSessionId, (req, res) => {
      res.json({ ok: true, id: req.params.sessionId });
    });

    const good = await request(app).get("/costs/session-abc");
    expect(good.status).toBe(200);
    expect(good.body.id).toBe("session-abc");

    const bad = await request(app).get("/costs/%00%01%02");
    expect(bad.status).toBe(400);
  });
});

// ── wrapRoute ───────────────────────────────────────────────────────

describe("wrapRoute", () => {
  test("passes through successful sync handlers", async () => {
    const app = express();
    app.get("/ok", wrapRoute("test ok", (req, res) => {
      res.json({ ok: true });
    }));

    const res = await request(app).get("/ok");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });

  test("catches sync throws and returns 500", async () => {
    const app = express();
    app.get("/fail", wrapRoute("test fail", () => {
      throw new Error("boom");
    }));

    const res = await request(app).get("/fail");
    expect(res.status).toBe(500);
    expect(res.body.error).toBe("Failed to test fail");
  });

  test("catches async rejections and returns 500", async () => {
    const app = express();
    app.get("/async-fail", wrapRoute("test async", async () => {
      throw new Error("async boom");
    }));

    const res = await request(app).get("/async-fail");
    expect(res.status).toBe(500);
    expect(res.body.error).toBe("Failed to test async");
  });

  test("preserves non-500 status codes from handler", async () => {
    const app = express();
    app.get("/not-found", wrapRoute("test 404", (req, res) => {
      res.status(404).json({ error: "Not found" });
    }));

    const res = await request(app).get("/not-found");
    expect(res.status).toBe(404);
  });

  // Covers the `!res.headersSent` FALSE branch: a sync handler that
  // responds and THEN throws must not attempt a second (500) send, which
  // would crash with ERR_HTTP_HEADERS_SENT.
  test("does not double-send when a sync handler throws after responding", async () => {
    const app = express();
    app.get("/late-sync-throw", wrapRoute("late sync", (req, res) => {
      res.status(200).json({ ok: true });
      throw new Error("thrown after response already sent");
    }));

    const res = await request(app).get("/late-sync-throw");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });

  // Same guard on the async-rejection path.
  test("does not double-send when an async handler rejects after responding", async () => {
    const app = express();
    app.get("/late-async-throw", wrapRoute("late async", async (req, res) => {
      res.status(200).json({ ok: true });
      throw new Error("rejected after response already sent");
    }));

    const res = await request(app).get("/late-async-throw");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });
});

// ── parseDays ───────────────────────────────────────────────────────

describe("parseDays", () => {
  test("returns default (30) when missing or NaN", () => {
    expect(parseDays(undefined)).toBe(30);
    expect(parseDays("abc")).toBe(30);
  });

  test("returns a custom default", () => {
    expect(parseDays(undefined, 7)).toBe(7);
  });

  test("clamps below 1 up to 1", () => {
    expect(parseDays("-5")).toBe(1);
    expect(parseDays("0")).toBe(30); // 0 is falsy -> falls back to default, then clamps
  });

  test("clamps above the max down to the max", () => {
    expect(parseDays("9999")).toBe(365);
    expect(parseDays("9999", 30, 90)).toBe(90);
  });

  test("returns a valid in-range value", () => {
    expect(parseDays("14")).toBe(14);
  });
});

// ── daysAgoCutoff ───────────────────────────────────────────────────

describe("daysAgoCutoff", () => {
  test("returns an ISO timestamp roughly `days` days in the past", () => {
    const before = Date.now() - 7 * 86400000;
    const iso = daysAgoCutoff(7);
    const parsed = Date.parse(iso);
    expect(Number.isNaN(parsed)).toBe(false);
    // Within a 2s window of the expected cutoff.
    expect(Math.abs(parsed - before)).toBeLessThan(2000);
  });

  test("0 days yields approximately now", () => {
    const iso = daysAgoCutoff(0);
    expect(Math.abs(Date.parse(iso) - Date.now())).toBeLessThan(2000);
  });
});
