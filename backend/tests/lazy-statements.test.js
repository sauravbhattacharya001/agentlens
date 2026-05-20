"use strict";

/**
 * Tests for lib/lazy-statements — the helper that caches prepared
 * statements by DB-handle identity (regression suite for #189).
 */

const Database = require("better-sqlite3");
const path = require("path");

// We need to control what require("../db").getDb() returns from within
// createLazyStatements. Jest's module mocking is the cleanest way.
let mockDb = null;
jest.mock("../db", () => ({
  getDb: () => mockDb,
}));

const { createLazyStatements } = require("../lib/lazy-statements");

function freshDb() {
  const db = new Database(":memory:");
  db.exec(
    "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
  );
  return db;
}

describe("createLazyStatements", () => {
  beforeEach(() => {
    mockDb = freshDb();
  });

  afterEach(() => {
    try {
      if (mockDb) mockDb.close();
    } catch (_) {
      /* already closed */
    }
    mockDb = null;
  });

  test("returns the same object across calls with the same DB", () => {
    const factory = jest.fn((db) => ({
      list: db.prepare("SELECT * FROM items"),
    }));
    const get = createLazyStatements(factory);

    const a = get();
    const b = get();

    expect(a).toBe(b);
    expect(factory).toHaveBeenCalledTimes(1);
  });

  test("lazy — factory is not invoked until first get()", () => {
    const factory = jest.fn((db) => ({
      list: db.prepare("SELECT * FROM items"),
    }));
    createLazyStatements(factory);

    expect(factory).not.toHaveBeenCalled();
  });

  test("re-runs factory and returns fresh statements after DB swap", () => {
    const factory = jest.fn((db) => ({
      list: db.prepare("SELECT * FROM items"),
    }));
    const get = createLazyStatements(factory);

    const first = get();
    expect(factory).toHaveBeenCalledTimes(1);

    // Simulate a test rebuilding the DB between cases.
    mockDb.close();
    mockDb = freshDb();

    const second = get();
    expect(factory).toHaveBeenCalledTimes(2);
    expect(second).not.toBe(first);
  });

  test("statements returned are bound to the current DB, not a stale handle", () => {
    const get = createLazyStatements((db) => ({
      insert: db.prepare("INSERT INTO items (name) VALUES (?)"),
      list: db.prepare("SELECT name FROM items ORDER BY id"),
    }));

    // First DB: insert a row.
    get().insert.run("first-db-row");

    // Replace the DB. The stale prepared statements (bound to the
    // closed handle) would throw SQLITE_ERROR if the cache survived.
    mockDb.close();
    mockDb = freshDb();

    // Statements bound to the new DB.
    const stmts = get();
    expect(() => stmts.insert.run("second-db-row")).not.toThrow();
    expect(stmts.list.all()).toEqual([{ name: "second-db-row" }]);
  });

  test("each createLazyStatements() instance has its own cache", () => {
    const fA = jest.fn((db) => ({ a: db.prepare("SELECT 1 AS a") }));
    const fB = jest.fn((db) => ({ b: db.prepare("SELECT 2 AS b") }));
    const getA = createLazyStatements(fA);
    const getB = createLazyStatements(fB);

    getA();
    getA();
    getB();

    expect(fA).toHaveBeenCalledTimes(1);
    expect(fB).toHaveBeenCalledTimes(1);
    expect(getA().a.get()).toEqual({ a: 1 });
    expect(getB().b.get()).toEqual({ b: 2 });
  });

  test("factory exceptions propagate and do not poison the cache", () => {
    let attempts = 0;
    const factory = (db) => {
      attempts++;
      if (attempts === 1) throw new Error("boom");
      return { list: db.prepare("SELECT * FROM items") };
    };
    const get = createLazyStatements(factory);

    expect(() => get()).toThrow("boom");
    // A retry on the same DB should rebuild rather than serve a half-baked cache.
    expect(() => get()).not.toThrow();
    expect(attempts).toBe(2);
  });
});
