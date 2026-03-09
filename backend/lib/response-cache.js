"use strict";

/**
 * In-memory response cache for read-heavy endpoints.
 *
 * Provides a short-lived LRU cache that stores full JSON response bodies,
 * keyed by the request URL (path + querystring). Cache entries expire
 * after a configurable TTL.
 *
 * Design choices:
 *  - Uses Map for insertion-order LRU eviction (O(1) delete + re-insert)
 *  - TTL per-entry, checked lazily on read (no timer overhead)
 *  - Optionally invalidated by POST/PUT/DELETE to related paths
 *  - Does not cache error responses (status >= 400)
 *  - Zero external dependencies
 *
 * Usage:
 *   const { createCache, cacheMiddleware } = require("../lib/response-cache");
 *   const cache = createCache({ ttlMs: 30000, maxEntries: 200 });
 *   router.get("/", cacheMiddleware(cache), (req, res) => { ... });
 *   // Invalidate after writes:
 *   router.post("/", (req, res) => { cache.invalidatePrefix("/analytics"); ... });
 */

/**
 * Create a new cache instance.
 *
 * @param {Object} [options]
 * @param {number} [options.ttlMs=30000] - Time-to-live in milliseconds
 * @param {number} [options.maxEntries=500] - Maximum cached entries (LRU eviction)
 * @returns {{ get, set, has, invalidate, invalidatePrefix, invalidateAll, stats, size }}
 */
function createCache(options) {
  options = options || {};
  var ttlMs = typeof options.ttlMs === "number" && options.ttlMs > 0 ? options.ttlMs : 30000;
  var maxEntries = typeof options.maxEntries === "number" && options.maxEntries > 0 ? options.maxEntries : 500;

  /** @type {Map<string, {body: *, statusCode: number, headers: Object, expiresAt: number}>} */
  var store = new Map();
  var hits = 0;
  var misses = 0;

  /**
   * Get a cached entry if it exists and hasn't expired.
   * @param {string} key
   * @returns {Object|null}
   */
  function get(key) {
    var entry = store.get(key);
    if (!entry) {
      misses++;
      return null;
    }
    if (Date.now() > entry.expiresAt) {
      store.delete(key);
      misses++;
      return null;
    }
    // Move to end for LRU freshness
    store.delete(key);
    store.set(key, entry);
    hits++;
    return entry;
  }

  /**
   * Store a response in the cache.
   * @param {string} key
   * @param {number} statusCode
   * @param {Object} headers - Response headers to cache
   * @param {*} body - JSON-serializable response body
   * @param {number} [customTtlMs] - Override default TTL for this entry
   */
  function set(key, statusCode, headers, body, customTtlMs) {
    // Don't cache error responses
    if (statusCode >= 400) return;

    // Evict oldest if at capacity
    if (store.size >= maxEntries && !store.has(key)) {
      var oldest = store.keys().next().value;
      store.delete(oldest);
    }

    var entryTtl = typeof customTtlMs === "number" && customTtlMs > 0 ? customTtlMs : ttlMs;
    store.set(key, {
      body: body,
      statusCode: statusCode,
      headers: headers || {},
      expiresAt: Date.now() + entryTtl,
    });
  }

  /**
   * Check if a non-expired entry exists.
   * @param {string} key
   * @returns {boolean}
   */
  function has(key) {
    var entry = store.get(key);
    if (!entry) return false;
    if (Date.now() > entry.expiresAt) {
      store.delete(key);
      return false;
    }
    return true;
  }

  /**
   * Remove a specific key.
   * @param {string} key
   */
  function invalidate(key) {
    store.delete(key);
  }

  /**
   * Remove all keys that start with a given prefix.
   * Useful for invalidating all analytics or session queries after a write.
   * @param {string} prefix
   */
  function invalidatePrefix(prefix) {
    // Collect keys first to avoid deleting during iteration,
    // which can cause skipped entries in some Map implementations.
    var toDelete = [];
    for (var key of store.keys()) {
      if (key.indexOf(prefix) === 0) {
        toDelete.push(key);
      }
    }
    for (var i = 0; i < toDelete.length; i++) {
      store.delete(toDelete[i]);
    }
  }

  /**
   * Clear the entire cache.
   */
  function invalidateAll() {
    store.clear();
    hits = 0;
    misses = 0;
  }

  /**
   * Get cache statistics.
   * @returns {{ size: number, hits: number, misses: number, hitRate: number, maxEntries: number, ttlMs: number }}
   */
  function stats() {
    var total = hits + misses;
    return {
      size: store.size,
      hits: hits,
      misses: misses,
      hitRate: total > 0 ? Math.round((hits / total) * 10000) / 100 : 0,
      maxEntries: maxEntries,
      ttlMs: ttlMs,
    };
  }

  return {
    get: get,
    set: set,
    has: has,
    invalidate: invalidate,
    invalidatePrefix: invalidatePrefix,
    invalidateAll: invalidateAll,
    stats: stats,
    get size() { return store.size; },
  };
}

/**
 * Express middleware that serves cached responses for GET requests.
 * Non-GET requests pass through and optionally invalidate.
 *
 * @param {Object} cache - Cache instance from createCache()
 * @param {Object} [options]
 * @param {number} [options.ttlMs] - Override cache TTL for this route
 * @param {string} [options.invalidatePrefix] - Prefix to invalidate on non-GET
 * @returns {Function} Express middleware
 */
function cacheMiddleware(cache, options) {
  options = options || {};
  var routeTtl = options.ttlMs;
  var invPrefix = options.invalidatePrefix;

  return function (req, res, next) {
    // Only cache GET requests
    if (req.method !== "GET") {
      // Invalidate cache on writes
      if (invPrefix) {
        cache.invalidatePrefix(invPrefix);
      }
      return next();
    }

    // Respect Cache-Control: no-cache (useful for tests and debugging)
    var cc = req.headers && req.headers["cache-control"];
    if (cc && cc.indexOf("no-cache") !== -1) {
      return next();
    }

    var key = req.originalUrl || req.url;
    var cached = cache.get(key);

    if (cached) {
      // Serve from cache
      res.set("X-Cache", "HIT");
      for (var h in cached.headers) {
        res.set(h, cached.headers[h]);
      }
      return res.status(cached.statusCode).json(cached.body);
    }

    // Cache miss — intercept res.json to capture the response
    res.set("X-Cache", "MISS");
    var originalJson = res.json.bind(res);

    res.json = function (body) {
      // Only cache successful responses
      if (res.statusCode < 400) {
        cache.set(key, res.statusCode, {}, body, routeTtl);
      }
      return originalJson(body);
    };

    next();
  };
}

module.exports = { createCache, cacheMiddleware };
