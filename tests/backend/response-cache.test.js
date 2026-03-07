"use strict";

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { createCache, cacheMiddleware } = require("../../backend/lib/response-cache");

// ── createCache ─────────────────────────────────────────────────────

describe("createCache", () => {
  let cache;

  beforeEach(() => {
    cache = createCache({ ttlMs: 1000, maxEntries: 5 });
  });

  it("should start empty", () => {
    assert.strictEqual(cache.size, 0);
    assert.strictEqual(cache.stats().hits, 0);
    assert.strictEqual(cache.stats().misses, 0);
  });

  it("should store and retrieve entries", () => {
    cache.set("/test", 200, {}, { data: "hello" });
    const entry = cache.get("/test");
    assert.ok(entry);
    assert.deepStrictEqual(entry.body, { data: "hello" });
    assert.strictEqual(entry.statusCode, 200);
  });

  it("should return null for missing keys", () => {
    assert.strictEqual(cache.get("/missing"), null);
  });

  it("should track hits and misses", () => {
    cache.set("/a", 200, {}, { x: 1 });
    cache.get("/a");  // hit
    cache.get("/b");  // miss
    const s = cache.stats();
    assert.strictEqual(s.hits, 1);
    assert.strictEqual(s.misses, 1);
    assert.strictEqual(s.hitRate, 50);
  });

  it("should not cache error responses (status >= 400)", () => {
    cache.set("/err", 500, {}, { error: "fail" });
    assert.strictEqual(cache.size, 0);
    assert.strictEqual(cache.get("/err"), null);
  });

  it("should expire entries after TTL", async () => {
    cache = createCache({ ttlMs: 50, maxEntries: 10 });
    cache.set("/ttl", 200, {}, { data: 1 });
    assert.ok(cache.get("/ttl"));
    
    await new Promise(r => setTimeout(r, 80));
    assert.strictEqual(cache.get("/ttl"), null);
  });

  it("should evict LRU entries at capacity", () => {
    for (let i = 0; i < 5; i++) {
      cache.set(`/item${i}`, 200, {}, { i });
    }
    assert.strictEqual(cache.size, 5);

    // Adding one more should evict the first
    cache.set("/item5", 200, {}, { i: 5 });
    assert.strictEqual(cache.size, 5);
    assert.strictEqual(cache.get("/item0"), null); // evicted
    assert.ok(cache.get("/item5")); // newest
  });

  it("LRU should refresh on access", () => {
    for (let i = 0; i < 5; i++) {
      cache.set(`/item${i}`, 200, {}, { i });
    }
    // Access item0 to refresh it
    cache.get("/item0");

    // Now add a new item — item1 should be evicted (oldest untouched)
    cache.set("/item5", 200, {}, { i: 5 });
    assert.ok(cache.get("/item0")); // refreshed, still present
    assert.strictEqual(cache.get("/item1"), null); // evicted
  });

  it("invalidate should remove specific key", () => {
    cache.set("/a", 200, {}, {});
    cache.set("/b", 200, {}, {});
    cache.invalidate("/a");
    assert.strictEqual(cache.get("/a"), null);
    assert.ok(cache.get("/b"));
  });

  it("invalidatePrefix should remove matching keys", () => {
    cache.set("/api/v1/a", 200, {}, {});
    cache.set("/api/v1/b", 200, {}, {});
    cache.set("/other", 200, {}, {});
    cache.invalidatePrefix("/api/v1");
    assert.strictEqual(cache.size, 1);
    assert.ok(cache.get("/other"));
  });

  it("invalidateAll should clear everything", () => {
    cache.set("/a", 200, {}, {});
    cache.set("/b", 200, {}, {});
    cache.invalidateAll();
    assert.strictEqual(cache.size, 0);
    assert.strictEqual(cache.stats().hits, 0);
  });

  it("has should check non-expired existence", () => {
    cache.set("/x", 200, {}, {});
    assert.ok(cache.has("/x"));
    assert.ok(!cache.has("/y"));
  });

  it("should use default TTL and maxEntries", () => {
    const c = createCache();
    const s = c.stats();
    assert.strictEqual(s.ttlMs, 30000);
    assert.strictEqual(s.maxEntries, 500);
  });

  it("should support custom TTL per entry", async () => {
    cache = createCache({ ttlMs: 5000, maxEntries: 10 });
    cache.set("/short", 200, {}, {}, 50); // 50ms TTL override
    assert.ok(cache.get("/short"));
    
    await new Promise(r => setTimeout(r, 80));
    assert.strictEqual(cache.get("/short"), null);
  });

  it("hitRate should be 0 with no requests", () => {
    assert.strictEqual(cache.stats().hitRate, 0);
  });
});

// ── cacheMiddleware ─────────────────────────────────────────────────

describe("cacheMiddleware", () => {
  function mockReq(method, url) {
    return { method, originalUrl: url, url };
  }

  function mockRes() {
    const res = {
      statusCode: 200,
      _headers: {},
      _body: null,
      set(key, val) { this._headers[key] = val; return this; },
      status(code) { this.statusCode = code; return this; },
      json(body) { this._body = body; return this; },
    };
    return res;
  }

  it("should pass through non-GET requests", (t, done) => {
    const cache = createCache();
    const mw = cacheMiddleware(cache);
    const req = mockReq("POST", "/api/test");
    const res = mockRes();
    
    mw(req, res, () => {
      done();
    });
  });

  it("should serve cached response on hit", (t, done) => {
    const cache = createCache({ ttlMs: 5000 });
    cache.set("/api/test", 200, {}, { cached: true });
    
    const mw = cacheMiddleware(cache);
    const req = mockReq("GET", "/api/test");
    const res = mockRes();
    
    // Middleware should serve from cache and not call next()
    mw(req, res, () => {
      assert.fail("next() should not be called for cached response");
    });

    assert.deepStrictEqual(res._body, { cached: true });
    assert.strictEqual(res._headers["X-Cache"], "HIT");
    done();
  });

  it("should set X-Cache MISS on cache miss", (t, done) => {
    const cache = createCache();
    const mw = cacheMiddleware(cache);
    const req = mockReq("GET", "/api/test");
    const res = mockRes();

    mw(req, res, () => {
      assert.strictEqual(res._headers["X-Cache"], "MISS");
      done();
    });
  });

  it("should cache response body on miss", (t, done) => {
    const cache = createCache();
    const mw = cacheMiddleware(cache);
    const req = mockReq("GET", "/api/test");
    const res = mockRes();

    mw(req, res, () => {
      // Simulate handler sending response
      res.json({ result: 42 });
      
      // Should now be cached
      assert.ok(cache.has("/api/test"));
      const entry = cache.get("/api/test");
      assert.deepStrictEqual(entry.body, { result: 42 });
      done();
    });
  });

  it("should not cache error responses", (t, done) => {
    const cache = createCache();
    const mw = cacheMiddleware(cache);
    const req = mockReq("GET", "/api/fail");
    const res = mockRes();

    mw(req, res, () => {
      res.status(500).json({ error: "fail" });
      assert.ok(!cache.has("/api/fail"));
      done();
    });
  });

  it("should invalidate prefix on non-GET", (t, done) => {
    const cache = createCache();
    cache.set("/api/data", 200, {}, { x: 1 });
    
    const mw = cacheMiddleware(cache, { invalidatePrefix: "/api" });
    const req = mockReq("POST", "/api/data");
    const res = mockRes();

    mw(req, res, () => {
      assert.ok(!cache.has("/api/data"));
      done();
    });
  });

  it("should use route-level TTL override", (t, done) => {
    const cache = createCache({ ttlMs: 60000 });
    const mw = cacheMiddleware(cache, { ttlMs: 100 });
    const req = mockReq("GET", "/api/short");
    const res = mockRes();

    mw(req, res, () => {
      res.json({ data: 1 });
      const entry = cache.get("/api/short");
      // Check that TTL is short (entry expires soon)
      assert.ok(entry.expiresAt <= Date.now() + 200);
      done();
    });
  });
});
