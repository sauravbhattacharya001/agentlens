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
 * @param {(db: import("better-sqlite3").Database) => Object} factory
 *   Receives the database instance and returns an object of prepared statements.
 * @returns {() => Object} A getter that lazily initializes and caches the statements.
 */
function createLazyStatements(factory) {
  let cached = null;
  return function getStatements() {
    if (cached) return cached;
    const { getDb } = require("../db");
    cached = factory(getDb());
    return cached;
  };
}

module.exports = { createLazyStatements };
