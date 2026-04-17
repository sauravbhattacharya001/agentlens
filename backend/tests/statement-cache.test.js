/* ── statement-cache — Unit Tests ────────────────────────────────────── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.exec("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)");
      mockDb.prepare("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta')").run();
    }
    return mockDb;
  },
}));

const { createStatementCache } = require("../lib/statement-cache");
const { getDb } = require("../db");

describe("createStatementCache", () => {
  test("returns a working prepared statement", () => {
    const cached = createStatementCache(getDb);
    const rows = cached("SELECT * FROM items ORDER BY id").all();
    expect(rows).toEqual([
      { id: 1, name: "alpha" },
      { id: 2, name: "beta" },
    ]);
  });

  test("caches repeated SQL — same statement object", () => {
    const cached = createStatementCache(getDb);
    const sql = "SELECT * FROM items WHERE id = ?";
    const s1 = cached(sql);
    const s2 = cached(sql);
    expect(s1).toBe(s2);
  });

  test("different SQL returns different statements", () => {
    const cached = createStatementCache(getDb);
    const s1 = cached("SELECT * FROM items WHERE id = 1");
    const s2 = cached("SELECT * FROM items WHERE id = 2");
    expect(s1).not.toBe(s2);
  });

  test("LRU eviction when cache exceeds maxSize", () => {
    const cached = createStatementCache(getDb, 3);
    const s1 = cached("SELECT 1");
    cached("SELECT 2");
    cached("SELECT 3");
    cached("SELECT 4");
    const s1b = cached("SELECT 1");
    expect(s1b).not.toBe(s1);
  });

  test("LRU refreshes accessed entries", () => {
    const cached = createStatementCache(getDb, 3);
    const s1 = cached("SELECT 1");
    cached("SELECT 2");
    cached("SELECT 3");
    cached("SELECT 1"); // refresh
    cached("SELECT 4"); // evicts SELECT 2
    const s1c = cached("SELECT 1");
    expect(s1c).toBe(s1);
  });

  test("defaults maxSize to 64 when invalid", () => {
    const cached = createStatementCache(getDb, 0);
    expect(cached("SELECT 1").get()).toBeDefined();
    const cached2 = createStatementCache(getDb, -5);
    expect(cached2("SELECT 1").get()).toBeDefined();
  });
});
