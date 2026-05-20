"use strict";

/**
 * Factory for lazy-initialized prepared statement caches.
 *
 * Eliminates the repeated pattern found across route files:
 *
 *   let _stmts = null;
 *   function getStatements() {
 *     if (_stmts) return _stmts;
 *     const db = getDb();
 *     _stmts = { ... };
 *     return _stmts;
 *   }
 *
 * Usage:
 *   const { createLazyStatements } = require("../lib/lazy-statements");
 *   const getStatements = createLazyStatements((db) => ({
 *     listAll: db.prepare("SELECT * FROM foo"),
 *     insert:  db.prepare("INSERT INTO foo VALUES (?, ?)"),
 *   }));
 *
 *   // In a route handler:
 *   const stmts = getStatements();
 *   const rows = stmts.listAll.all();
 *
 * The cache is keyed by the *identity* of the underlying database handle.
 * In production the handle is a long-lived singleton, so this behaves like
 * a plain `if (cached) return cached`. In tests, however, suites routinely
 * recreate the in-memory SQLite DB between tests (e.g. via `jest.resetModules()`
 * + `process.env.DB_PATH = ...`). When that happens the previously cached
 * prepared statements point at a closed/wrong database and surface as
 * `SQLITE_ERROR`, `Cannot read properties of undefined`, or a torn-down-Jest
 * `require()` failure (see issue #189). Re-checking the DB identity on every
 * call is one extra `===` per request — negligible — and makes the cache
 * self-invalidating without exposing a test-only reset hook.
 *
 * @param {(db: import("better-sqlite3").Database) => Object} factory
 *   Receives the database instance and returns an object of prepared statements.
 * @returns {() => Object} A getter that lazily initializes and caches the statements.
 */
function createLazyStatements(factory) {
  let cached = null;
  let cachedDb = null;
  return function getStatements() {
    const { getDb } = require("../db");
    const db = getDb();
    if (cached && cachedDb === db) return cached;
    cached = factory(db);
    cachedDb = db;
    return cached;
  };
}

module.exports = { createLazyStatements };
