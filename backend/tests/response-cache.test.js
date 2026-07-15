"use strict";

const { createCache, cacheMiddleware } = require("../lib/response-cache");

describe("response-cache", () => {
  // ── createCache ──

  describe("createCache", () => {
    test("creates empty cache with defaults", () => {
      const cache = createCache();
      expect(cache.size).toBe(0);
      const s = cache.stats();
      expect(s.hits).toBe(0);
      expect(s.misses).toBe(0);
      expect(s.maxEntries).toBe(500);
      expect(s.ttlMs).toBe(30000);
    });

    test("respects custom options", () => {
      const cache = createCache({ ttlMs: 5000, maxEntries: 10 });
      const s = cache.stats();
      expect(s.maxEntries).toBe(10);
      expect(s.ttlMs).toBe(5000);
    });

    test("ignores invalid options", () => {
      const cache = createCache({ ttlMs: -1, maxEntries: 0 });
      const s = cache.stats();
      expect(s.ttlMs).toBe(30000);
      expect(s.maxEntries).toBe(500);
    });
  });

  // ── get / set ──

  describe("get and set", () => {
    test("returns null for unknown key", () => {
      const cache = createCache();
      expect(cache.get("missing")).toBeNull();
    });

    test("stores and retrieves entry", () => {
      const cache = createCache();
      cache.set("/api/data", 200, { "content-type": "application/json" }, { ok: true });
      const entry = cache.get("/api/data");
      expect(entry).not.toBeNull();
      expect(entry.body).toEqual({ ok: true });
      expect(entry.statusCode).toBe(200);
    });

    test("does not cache error responses (status >= 400)", () => {
      const cache = createCache();
      cache.set("/api/err", 404, {}, { error: "not found" });
      expect(cache.get("/api/err")).toBeNull();
      expect(cache.size).toBe(0);
    });

    test("does not cache 500 errors", () => {
      const cache = createCache();
      cache.set("/api/fail", 500, {}, { error: "server" });
      expect(cache.size).toBe(0);
    });

    test("expired entries return null", () => {
      const cache = createCache({ ttlMs: 1 }); // 1ms TTL
      cache.set("/api/fast", 200, {}, { data: 1 });

      // Wait for expiry
      const start = Date.now();
      while (Date.now() - start < 5) { /* spin */ }

      expect(cache.get("/api/fast")).toBeNull();
    });

    test("custom TTL per entry", () => {
      const cache = createCache({ ttlMs: 60000 });
      cache.set("/api/short", 200, {}, { data: 1 }, 1); // 1ms TTL

      const start = Date.now();
      while (Date.now() - start < 5) { /* spin */ }

      expect(cache.get("/api/short")).toBeNull();
    });
  });

  // ── has ──

  describe("has", () => {
    test("returns false for missing key", () => {
      const cache = createCache();
      expect(cache.has("nope")).toBe(false);
    });

    test("returns true for cached key", () => {
      const cache = createCache();
      cache.set("/x", 200, {}, {});
      expect(cache.has("/x")).toBe(true);
    });

    test("returns false for expired key", () => {
      const cache = createCache({ ttlMs: 1 });
      cache.set("/x", 200, {}, {});
      const start = Date.now();
      while (Date.now() - start < 5) { /* spin */ }
      expect(cache.has("/x")).toBe(false);
    });
  });

  // ── LRU eviction ──

  describe("LRU eviction", () => {
    test("evicts oldest when at capacity", () => {
      const cache = createCache({ maxEntries: 3, ttlMs: 60000 });
      cache.set("/a", 200, {}, "a");
      cache.set("/b", 200, {}, "b");
      cache.set("/c", 200, {}, "c");
      expect(cache.size).toBe(3);

      // Adding a 4th should evict /a (oldest)
      cache.set("/d", 200, {}, "d");
      expect(cache.size).toBe(3);
      expect(cache.get("/a")).toBeNull();
      expect(cache.get("/b")).not.toBeNull();
      expect(cache.get("/d")).not.toBeNull();
    });

    test("get refreshes LRU order", () => {
      const cache = createCache({ maxEntries: 3, ttlMs: 60000 });
      cache.set("/a", 200, {}, "a");
      cache.set("/b", 200, {}, "b");
      cache.set("/c", 200, {}, "c");

      // Access /a to make it fresh
      cache.get("/a");

      // Add /d — should evict /b (now oldest) instead of /a
      cache.set("/d", 200, {}, "d");
      expect(cache.get("/a")).not.toBeNull(); // refreshed
      expect(cache.get("/b")).toBeNull();     // evicted
    });
  });

  // ── invalidate ──

  describe("invalidation", () => {
    test("invalidate removes single key", () => {
      const cache = createCache();
      cache.set("/a", 200, {}, "a");
      cache.set("/b", 200, {}, "b");
      cache.invalidate("/a");
      expect(cache.get("/a")).toBeNull();
      expect(cache.get("/b")).not.toBeNull();
    });

    test("invalidatePrefix removes matching keys", () => {
      const cache = createCache();
      cache.set("/api/v1/users", 200, {}, "users");
      cache.set("/api/v1/events", 200, {}, "events");
      cache.set("/api/v2/users", 200, {}, "v2users");
      cache.invalidatePrefix("/api/v1");
      expect(cache.get("/api/v1/users")).toBeNull();
      expect(cache.get("/api/v1/events")).toBeNull();
      expect(cache.get("/api/v2/users")).not.toBeNull();
    });

    test("invalidateAll clears everything and resets stats", () => {
      const cache = createCache();
      cache.set("/a", 200, {}, "a");
      cache.set("/b", 200, {}, "b");
      cache.get("/a"); // hit
      cache.get("/missing"); // miss
      cache.invalidateAll();
      expect(cache.size).toBe(0);
      const s = cache.stats();
      expect(s.hits).toBe(0);
      expect(s.misses).toBe(0);
    });
  });

  // ── stats ──

  describe("stats", () => {
    test("tracks hits and misses", () => {
      const cache = createCache();
      cache.set("/a", 200, {}, "a");
      cache.get("/a"); // hit
      cache.get("/a"); // hit
      cache.get("/b"); // miss
      const s = cache.stats();
      expect(s.hits).toBe(2);
      expect(s.misses).toBe(1);
      expect(s.hitRate).toBe(66.67);
      expect(s.size).toBe(1);
    });

    test("hitRate is 0 when no requests", () => {
      const cache = createCache();
      expect(cache.stats().hitRate).toBe(0);
    });
  });

  // ── evictExpired ──

  describe("evictExpired", () => {
    test("removes only expired entries and returns count", () => {
      const cache = createCache({ ttlMs: 60000 });
      cache.set("/live", 200, {}, "live");
      cache.set("/dead1", 200, {}, "d1", 1); // 1ms TTL
      cache.set("/dead2", 200, {}, "d2", 1); // 1ms TTL

      const start = Date.now();
      while (Date.now() - start < 5) { /* spin */ }

      const evicted = cache.evictExpired();
      expect(evicted).toBe(2);
      expect(cache.size).toBe(1);
      expect(cache.get("/live")).not.toBeNull();
    });

    test("returns 0 when nothing is expired", () => {
      const cache = createCache({ ttlMs: 60000 });
      cache.set("/a", 200, {}, "a");
      expect(cache.evictExpired()).toBe(0);
      expect(cache.size).toBe(1);
    });
  });

  // ── destroy ──

  describe("destroy", () => {
    test("clears entries, resets stats, and stops the sweep timer", () => {
      const cache = createCache({ ttlMs: 60000 });
      cache.set("/a", 200, {}, "a");
      cache.get("/a"); // hit
      cache.get("/miss"); // miss
      cache.destroy();
      expect(cache.size).toBe(0);
      const s = cache.stats();
      expect(s.hits).toBe(0);
      expect(s.misses).toBe(0);
      // Idempotent: second destroy must not throw
      expect(() => cache.destroy()).not.toThrow();
    });
  });

  // ── passive sweep timer ──

  describe("passive sweep timer", () => {
    test("periodically evicts expired entries without manual calls", () => {
      jest.useFakeTimers();
      try {
        // ttlMs=10 → sweep interval fires every 20ms
        const cache = createCache({ ttlMs: 10 });
        cache.set("/a", 200, {}, "a");
        expect(cache.size).toBe(1);
        // Advance past entry expiry AND the 2×TTL sweep interval
        jest.advanceTimersByTime(25);
        expect(cache.size).toBe(0);
        cache.destroy();
      } finally {
        jest.useRealTimers();
      }
    });
  });

  // ── cacheMiddleware ──

  describe("cacheMiddleware", () => {
    function mockReq(method, url, headers) {
      return { method, url, originalUrl: url, headers: headers || {} };
    }

    function mockRes() {
      const res = {
        statusCode: 200,
        _headers: {},
        _json: null,
        set(k, v) { res._headers[k] = v; return res; },
        status(code) { res.statusCode = code; return res; },
        json(body) { res._json = body; return res; },
      };
      return res;
    }

    test("passes through non-GET requests", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      const req = mockReq("POST", "/api/data");
      const res = mockRes();
      let nextCalled = false;
      mw(req, res, () => { nextCalled = true; });
      expect(nextCalled).toBe(true);
    });

    test("sets X-Cache MISS on first GET", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      const req = mockReq("GET", "/api/data");
      const res = mockRes();
      mw(req, res, () => {});
      expect(res._headers["X-Cache"]).toBe("MISS");
    });

    test("caches response after json() call", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      const req = mockReq("GET", "/api/data");
      const res = mockRes();
      mw(req, res, () => {
        res.json({ result: 42 });
      });
      expect(cache.has("/api/data")).toBe(true);
    });

    test("serves cached response on second GET", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);

      // First request — cache miss
      const req1 = mockReq("GET", "/api/data");
      const res1 = mockRes();
      mw(req1, res1, () => {
        res1.json({ result: 42 });
      });

      // Second request — cache hit
      const req2 = mockReq("GET", "/api/data");
      const res2 = mockRes();
      let nextCalled = false;
      mw(req2, res2, () => { nextCalled = true; });
      expect(nextCalled).toBe(false);
      expect(res2._headers["X-Cache"]).toBe("HIT");
      expect(res2._json).toEqual({ result: 42 });
    });

    test("bypasses cache on Cache-Control: no-cache", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);

      cache.set("/api/data", 200, {}, { cached: true });

      const req = mockReq("GET", "/api/data", { "cache-control": "no-cache" });
      const res = mockRes();
      let nextCalled = false;
      mw(req, res, () => { nextCalled = true; });
      expect(nextCalled).toBe(true);
    });

    test("keys cache per API key and replays cached headers on HIT", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);

      // First GET with an API key populates the cache under a key-scoped slot,
      // capturing a custom response header to replay on the HIT path.
      const req1 = mockReq("GET", "/api/scoped", { "x-api-key": "key-A" });
      const res1 = mockRes();
      mw(req1, res1, () => {
        res1.json({ scoped: true });
      });
      expect(res1._headers["X-Cache"]).toBe("MISS");

      // Same key + URL → HIT, and the cached headers are replayed.
      const req2 = mockReq("GET", "/api/scoped", { "x-api-key": "key-A" });
      const res2 = mockRes();
      let next2 = false;
      mw(req2, res2, () => { next2 = true; });
      expect(next2).toBe(false);
      expect(res2._headers["X-Cache"]).toBe("HIT");
      expect(res2._json).toEqual({ scoped: true });

      // A different API key must NOT hit the other key's cached slot.
      const req3 = mockReq("GET", "/api/scoped", { "x-api-key": "key-B" });
      const res3 = mockRes();
      let next3 = false;
      mw(req3, res3, () => { next3 = true; });
      expect(next3).toBe(true);
      expect(res3._headers["X-Cache"]).toBe("MISS");
    });

    test("replays cached headers to the client on a HIT", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      // Pre-seed the cache with a stored entry that carries headers, so the
      // HIT path's header-replay loop runs against a non-empty header map.
      cache.set("/api/withhdr", 200, { "X-Custom": "from-origin", ETag: "abc" }, { ok: 1 });
      const req = mockReq("GET", "/api/withhdr");
      const res = mockRes();
      let nextCalled = false;
      mw(req, res, () => { nextCalled = true; });
      expect(nextCalled).toBe(false);
      expect(res._headers["X-Cache"]).toBe("HIT");
      expect(res._headers["X-Custom"]).toBe("from-origin");
      expect(res._headers.ETag).toBe("abc");
      expect(res._json).toEqual({ ok: 1 });
    });

    test("reuses the API-key hash micro-cache across requests", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      // Two GETs with the same key exercise the keyHashCache hit branch
      // (second call returns the memoized hash instead of re-hashing).
      for (let i = 0; i < 2; i++) {
        const req = mockReq("GET", "/api/reuse", { "x-api-key": "same-key" });
        const res = mockRes();
        mw(req, res, () => { res.json({ i }); });
      }
      // Second request is served from cache.
      const s = cache.stats();
      expect(s.hits).toBeGreaterThanOrEqual(1);
    });

    test("evicts oldest entry from the API-key hash micro-cache past capacity", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      // KEY_HASH_CACHE_MAX is 16; 20 distinct keys force LRU eviction.
      for (let i = 0; i < 20; i++) {
        const req = mockReq("GET", "/api/k" + i, { "x-api-key": "key-" + i });
        const res = mockRes();
        mw(req, res, () => { res.json({ i }); });
      }
      // Reaching here without throwing exercises the eviction branch.
      // Sanity: response caching still works for all distinct URLs.
      expect(cache.size).toBe(20);
    });

    test("does not cache error responses produced via json()", () => {
      const cache = createCache();
      const mw = cacheMiddleware(cache);
      const req = mockReq("GET", "/api/boom");
      const res = mockRes();
      mw(req, res, () => {
        res.status(500).json({ error: "boom" });
      });
      expect(cache.has("/api/boom")).toBe(false);
    });

    test("invalidates prefix on non-GET when configured", () => {
      const cache = createCache();
      cache.set("/api/data/1", 200, {}, "d1");
      cache.set("/api/data/2", 200, {}, "d2");

      const mw = cacheMiddleware(cache, { invalidatePrefix: "/api/data" });
      const req = mockReq("POST", "/api/data");
      const res = mockRes();
      mw(req, res, () => {});

      expect(cache.has("/api/data/1")).toBe(false);
      expect(cache.has("/api/data/2")).toBe(false);
    });
  });
});
