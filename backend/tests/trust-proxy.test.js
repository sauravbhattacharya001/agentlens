/**
 * Tests for AGENTLENS_TRUST_PROXY handling.
 *
 * Issue #184: When the backend runs behind a reverse proxy (nginx,
 * Traefik, ALB, Cloud Run, ...), `req.ip` defaults to the proxy's
 * address. That collapses all per-IP rate limits into a single global
 * bucket and makes audit IPs useless.
 *
 * AGENTLENS_TRUST_PROXY opts in to trusting X-Forwarded-For for N hops
 * (or a CIDR list / Express named preset). These tests verify:
 *   1. Unset  \u2192 no `trust proxy` is configured, req.ip is the socket addr.
 *   2. Number \u2192 numeric hop count, req.ip honors X-Forwarded-For.
 *   3. CIDR   \u2192 string passes through to Express unchanged.
 *   4. Empty / whitespace \u2192 treated as unset (no spoofing window).
 *   5. createApiLimiter still returns a function regardless of value.
 */

"use strict";

const express = require("express");
const request = require("supertest");
const { createApiLimiter, createIngestLimiter } = require("../middleware");

// Re-load server.js trust-proxy block in isolation by replicating it.
// We import server.js's logic indirectly: spinning up a minimal app
// that mirrors the production wiring keeps this test fast (no DB).
function makeApp({ trustProxyEnv } = {}) {
  const prev = process.env.AGENTLENS_TRUST_PROXY;
  if (trustProxyEnv === undefined) delete process.env.AGENTLENS_TRUST_PROXY;
  else process.env.AGENTLENS_TRUST_PROXY = trustProxyEnv;

  // Reset module registry so middleware.js re-reads env on require.
  jest.resetModules();
  const freshMiddleware = require("../middleware");

  const app = express();

  // Mirror the server.js trust-proxy block exactly.
  const raw = process.env.AGENTLENS_TRUST_PROXY;
  if (raw && raw.trim() !== "") {
    const trimmed = raw.trim();
    const asNum = Number(trimmed);
    app.set(
      "trust proxy",
      Number.isFinite(asNum) && /^-?\d+$/.test(trimmed) ? asNum : trimmed,
    );
  }

  app.get("/whoami", (req, res) => res.json({ ip: req.ip }));

  // Attach a limiter so we exercise the express-rate-limit code path
  // that previously emitted ERR_ERL_PERMISSIVE_TRUST_PROXY.
  app.get("/limited", freshMiddleware.createApiLimiter(), (req, res) =>
    res.json({ ok: true, ip: req.ip }),
  );

  // Restore env for the next test.
  if (prev === undefined) delete process.env.AGENTLENS_TRUST_PROXY;
  else process.env.AGENTLENS_TRUST_PROXY = prev;

  return app;
}

describe("AGENTLENS_TRUST_PROXY", () => {
  // Silence the express-rate-limit warnings that surface on stderr in
  // CI; the validators themselves are exercised inside the limiter.
  let warnSpy;
  beforeEach(() => {
    warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
    jest.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    warnSpy.mockRestore();
    jest.restoreAllMocks();
  });

  test("unset \u2192 X-Forwarded-For is ignored (no spoofing)", async () => {
    const app = makeApp({ trustProxyEnv: undefined });
    const res = await request(app)
      .get("/whoami")
      .set("X-Forwarded-For", "1.2.3.4");
    expect(res.status).toBe(200);
    // req.ip falls back to the socket address (loopback in tests),
    // NOT the spoofed header value.
    expect(res.body.ip).not.toBe("1.2.3.4");
  });

  test("empty string \u2192 treated as unset", async () => {
    const app = makeApp({ trustProxyEnv: "   " });
    const res = await request(app)
      .get("/whoami")
      .set("X-Forwarded-For", "1.2.3.4");
    expect(res.status).toBe(200);
    expect(res.body.ip).not.toBe("1.2.3.4");
  });

  test("AGENTLENS_TRUST_PROXY=1 \u2192 honors X-Forwarded-For", async () => {
    const app = makeApp({ trustProxyEnv: "1" });
    expect(app.get("trust proxy")).toBe(1);
    const res = await request(app)
      .get("/whoami")
      .set("X-Forwarded-For", "1.2.3.4");
    expect(res.status).toBe(200);
    expect(res.body.ip).toBe("1.2.3.4");
  });

  test("AGENTLENS_TRUST_PROXY=2 \u2192 walks two hops back", async () => {
    const app = makeApp({ trustProxyEnv: "2" });
    expect(app.get("trust proxy")).toBe(2);
    const res = await request(app)
      .get("/whoami")
      // Express walks right-to-left, trusting the last `n` entries.
      // With n=2 and three hops, req.ip should be 5.5.5.5.
      .set("X-Forwarded-For", "9.9.9.9, 5.5.5.5, 7.7.7.7");
    expect(res.status).toBe(200);
    expect(res.body.ip).toBe("5.5.5.5");
  });

  test("CIDR string passes through to Express unchanged", () => {
    const app = makeApp({ trustProxyEnv: "10.0.0.0/8" });
    expect(app.get("trust proxy")).toBe("10.0.0.0/8");
  });

  test("named preset passes through to Express unchanged", () => {
    const app = makeApp({ trustProxyEnv: "loopback" });
    expect(app.get("trust proxy")).toBe("loopback");
  });

  test("rate limiter still constructs cleanly with trust proxy set", async () => {
    const app = makeApp({ trustProxyEnv: "1" });
    const res = await request(app)
      .get("/limited")
      .set("X-Forwarded-For", "1.2.3.4");
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.ip).toBe("1.2.3.4");
  });

  test("rate limiter still constructs cleanly with no trust proxy", async () => {
    const app = makeApp({ trustProxyEnv: undefined });
    const limiter = createApiLimiter();
    const ingest = createIngestLimiter();
    expect(typeof limiter).toBe("function");
    expect(typeof ingest).toBe("function");
  });
});
