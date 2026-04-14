"use strict";

/**
 * LRU cache for dynamically-built prepared statements.
 *
 * Route files that construct SQL at request time (e.g. session search,
 * event search) previously called `db.prepare()` on every request,
 * re-compiling the same query even when the SQL string was identical.
 * This module caches the last N compiled statements keyed by their SQL
 * text, saving ~0.1-0.5 ms per repeated query.
 *
 * Usage:
 *   const { createStatementCache } = require("../lib/statement-cache");
 *   const cachedPrepare = createStatementCache(getDb);
 *   // In a route handler:
 *   const rows = cachedPrepare("SELECT * FROM foo WHERE bar = ?").all(val);
 *
 * @param {() => import("better-sqlite3").Database} getDbFn
 *   Factory that returns the current database handle.
 * @param {number} [maxSize=64]
 *   Maximum number of cached statements (LRU eviction).
 * @returns {(sql: string) => import("better-sqlite3").Statement}
 */
function createStatementCache(getDbFn, maxSize) {
  if (!maxSize || maxSize < 1) maxSize = 64;

  /** @type {Map<string, import("better-sqlite3").Statement>} */
  var cache = new Map();

  return function cachedPrepare(sql) {
    var stmt = cache.get(sql);
    if (stmt) {
      // Move to end for LRU freshness
      cache.delete(sql);
      cache.set(sql, stmt);
      return stmt;
    }
    stmt = getDbFn().prepare(sql);
    if (cache.size >= maxSize) {
      var oldest = cache.keys().next().value;
      cache.delete(oldest);
    }
    cache.set(sql, stmt);
    return stmt;
  };
}

module.exports = { createStatementCache };
