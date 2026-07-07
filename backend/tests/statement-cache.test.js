"use strict";

/**
 * Tests for lib/statement-cache - the LRU cache of dynamically-built
 * prepared statements used by the SQL-at-request-time routes (session
 * list + session search in routes/sessions.js).
 *
 * Why this file exists: `createStatementCache` sits on a hot path (every
 * session-search request re-uses it) but had ZERO direct coverage - it was
 * only exercised transitively through the full session routes in
 * sessions.test.js, which never asserts the cache's OWN behaviour (hit vs
 * miss, LRU eviction order, move-to-end freshness, the maxSize guard). A
 * regression in any of those would silently degrade performance - the query
 * results would still be correct, so no route test would fail - while the
 * cache quietly re-compiled every statement or evicted the wrong entry.
 * These tests pin that behaviour directly.
 *
 * The helper takes its DB factory as an argument (not via require("../db")),
 * so unlike the sibling lazy-statements suite we can inject a plain fake
 * getDb and count prepare() calls precisely. A final case uses a real
 * better-sqlite3 :memory: DB to prove the cached statements still execute.
 */

const Database = require("better-sqlite3");

const { createStatementCache } = require("../lib/statement-cache");

/**
 * Build a fake DB whose prepare() returns a distinct tagged sentinel per
 * call and records every SQL string it was asked to compile. Lets us assert
 * on identity (same object served on a hit) and on how many times the
 * underlying prepare() actually ran.
 */
function fakeDb() {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      calls.push(sql);
      // Distinct object per prepare() call, tagged with its SQL + ordinal
      // so tests can distinguish "served from cache" from "recompiled".
      return { __sql: sql, __ordinal: calls.length };
    },
  };
}

describe("createStatementCache", () => {
  test("compiles on a miss and serves the same statement on a hit", () => {
    const db = fakeDb();
    const cachedPrepare = createStatementCache(() => db);

    const first = cachedPrepare("SELECT 1");
    const second = cachedPrepare("SELECT 1");

    // Second call is a cache hit: same object, no second prepare().
    expect(second).toBe(first);
    expect(db.calls).toEqual(["SELECT 1"]);
  });

  test("keys strictly by SQL text - whitespace-different SQL is a separate entry", () => {
    const db = fakeDb();
    const cachedPrepare = createStatementCache(() => db);

    const a = cachedPrepare("SELECT * FROM foo WHERE bar = ?");
    const b = cachedPrepare("SELECT  * FROM foo WHERE bar = ?"); // extra space

    expect(b).not.toBe(a);
    expect(db.calls).toHaveLength(2);
  });

  test("re-reads the DB handle on every miss (via the factory)", () => {
    // The factory is what lets a test swap the DB between requests; a miss
    // must call it again rather than caching the handle itself.
    let handle = fakeDb();
    const getDb = jest.fn(() => handle);
    const cachedPrepare = createStatementCache(getDb);

    cachedPrepare("SELECT 1"); // miss -> getDb()
    cachedPrepare("SELECT 1"); // hit  -> no getDb()
    cachedPrepare("SELECT 2"); // miss -> getDb()

    expect(getDb).toHaveBeenCalledTimes(2);
  });

  test("evicts the least-recently-used entry once maxSize is exceeded", () => {
    const db = fakeDb();
    const cachedPrepare = createStatementCache(() => db, 2);

    cachedPrepare("A"); // miss -> [A]
    cachedPrepare("B"); // miss -> [A, B]
    cachedPrepare("C"); // full -> evict LRU (A) -> [B, C]

    // A was evicted, so re-fetching it recompiles AND evicts the next LRU
    // (B), leaving [C, A].
    cachedPrepare("A");
    expect(db.calls).toEqual(["A", "B", "C", "A"]);

    // C survived both evictions: re-fetching it is a hit (no recompile).
    const before = db.calls.length;
    cachedPrepare("C");
    expect(db.calls.length).toBe(before);

    // B was the second eviction, so it recompiles.
    cachedPrepare("B");
    expect(db.calls).toEqual(["A", "B", "C", "A", "B"]);
  });

  test("a cache hit moves the entry to most-recently-used (LRU freshness)", () => {
    const db = fakeDb();
    const cachedPrepare = createStatementCache(() => db, 2);

    cachedPrepare("A"); // [A]
    cachedPrepare("B"); // [A, B]
    cachedPrepare("A"); // hit -> refresh A -> [B, A]
    cachedPrepare("C"); // evict LRU (B, not A) -> [A, C]

    const before = db.calls.length;
    cachedPrepare("A"); // still cached -> no recompile
    expect(db.calls.length).toBe(before);

    cachedPrepare("B"); // was evicted -> recompile
    expect(db.calls[db.calls.length - 1]).toBe("B");
  });

  test("defaults to a maxSize of 64 when it is omitted", () => {
    const db = fakeDb();
    const cachedPrepare = createStatementCache(() => db);

    // Fill 64 distinct entries, then touch the first again - it must still
    // be cached (proving the default cap is >= 64, i.e. nothing evicted yet).
    for (let i = 0; i < 64; i++) cachedPrepare("Q" + i);
    expect(db.calls).toHaveLength(64);

    const before = db.calls.length;
    cachedPrepare("Q0");
    expect(db.calls.length).toBe(before); // hit, not recompiled

    // The 65th distinct entry triggers the first eviction (of Q1, since Q0
    // was just refreshed to MRU).
    cachedPrepare("Q64");
    cachedPrepare("Q1");
    expect(db.calls[db.calls.length - 1]).toBe("Q1"); // Q1 was evicted
  });

  test("treats a zero or negative maxSize as the default (never a 0-slot cache)", () => {
    for (const bad of [0, -5, undefined, null]) {
      const db = fakeDb();
      const cachedPrepare = createStatementCache(() => db, bad);
      const first = cachedPrepare("SELECT 1");
      const second = cachedPrepare("SELECT 1");
      // A 0-slot cache would evict immediately and recompile every time;
      // the guard must keep at least the default capacity.
      expect(second).toBe(first);
      expect(db.calls).toEqual(["SELECT 1"]);
    }
  });

  test("each cache instance is isolated (separate backing store)", () => {
    const dbA = fakeDb();
    const dbB = fakeDb();
    const cacheA = createStatementCache(() => dbA);
    const cacheB = createStatementCache(() => dbB);

    cacheA("SELECT 1");
    cacheA("SELECT 1");
    cacheB("SELECT 1");

    expect(dbA.calls).toEqual(["SELECT 1"]); // one compile in A
    expect(dbB.calls).toEqual(["SELECT 1"]); // one compile in B (not shared)
  });

  test("cached statements remain executable against a real better-sqlite3 DB", () => {
    const db = new Database(":memory:");
    try {
      db.exec("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)");
      const cachedPrepare = createStatementCache(() => db);

      const insert = cachedPrepare("INSERT INTO items (name) VALUES (?)");
      insert.run("first");
      // Second lookup of the same SQL must be the identical prepared
      // statement and still run correctly.
      const insertAgain = cachedPrepare("INSERT INTO items (name) VALUES (?)");
      expect(insertAgain).toBe(insert);
      insertAgain.run("second");

      const rows = cachedPrepare("SELECT name FROM items ORDER BY id").all();
      expect(rows).toEqual([{ name: "first" }, { name: "second" }]);
    } finally {
      db.close();
    }
  });
});
