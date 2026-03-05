/* ── request-helpers.js — unit tests ─────────────────────────────────── */

const express = require("express");
const request = require("supertest");
const {
  parseLimit,
  parseOffset,
  parsePagination,
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
});
