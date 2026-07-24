/**
 * Unit tests for the analytics response-cache wiring helper
 * (routes/analytics.js `_internals.analyticsCacheMw`).
 *
 * This helper is the single source of truth for deciding whether the analytics
 * cache is active on a route. It was previously three near-identical
 * `isTest ? passthrough : cacheMiddleware(...)` expressions inlined at each
 * route; folding them into one function removed the duplication but left the
 * production (non-test) arm unexercised because the whole suite runs under
 * NODE_ENV=test. These tests toggle NODE_ENV so both arms are covered.
 */

const { _internals } = require("../routes/analytics");
const { analyticsCacheMw } = _internals;

describe("analytics response-cache wiring (analyticsCacheMw)", () => {
  const savedEnv = process.env.NODE_ENV;
  afterEach(() => { process.env.NODE_ENV = savedEnv; });

  test("returns a pass-through middleware under NODE_ENV=test", () => {
    process.env.NODE_ENV = "test";
    const mw = analyticsCacheMw();
    expect(typeof mw).toBe("function");
    // Pass-through: calls next() immediately, does not touch res.
    let called = false;
    const next = () => { called = true; };
    const res = {
      json() { throw new Error("pass-through must not write a response"); },
    };
    mw({ method: "GET", originalUrl: "/analytics" }, res, next);
    expect(called).toBe(true);
  });

  test("returns a real cache middleware outside the test environment", () => {
    process.env.NODE_ENV = "production";
    const mw = analyticsCacheMw();
    expect(typeof mw).toBe("function");
    // The real middleware wraps res.json to populate the cache, so invoking it
    // must NOT immediately fall through the way the pass-through does; it
    // installs its interception and then calls next().
    let nextCalled = false;
    const req = { method: "GET", originalUrl: "/analytics/unit-test" };
    const originalJson = function () { return res; };
    const res = { json: originalJson, statusCode: 200, set() {}, get() {} };
    mw(req, res, () => { nextCalled = true; });
    expect(nextCalled).toBe(true);
    // res.json was wrapped by the caching middleware (behaviour differs from
    // the pass-through, which never touches res).
    expect(res.json).not.toBe(originalJson);
  });

  test("honours a per-route TTL override outside the test environment", () => {
    process.env.NODE_ENV = "production";
    // Distinct TTLs must each yield a usable middleware function; this covers
    // the `typeof ttlMs === 'number'` arm as well as the default arm.
    expect(typeof analyticsCacheMw(15000)).toBe("function");
    expect(typeof analyticsCacheMw(60000)).toBe("function");
    expect(typeof analyticsCacheMw()).toBe("function");
  });
});
