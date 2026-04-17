/* ── lazy-statements — Unit Tests ────────────────────────────────────── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)");
      mockDb.prepare("INSERT INTO t VALUES (1, 'hello')").run();
    }
    return mockDb;
  },
}));

const { createLazyStatements } = require("../lib/lazy-statements");

describe("createLazyStatements", () => {
  test("lazily initializes statements on first call", () => {
    const factory = jest.fn((db) => ({
      getAll: db.prepare("SELECT * FROM t"),
    }));
    const getStatements = createLazyStatements(factory);
    expect(factory).not.toHaveBeenCalled();
    const stmts = getStatements();
    expect(factory).toHaveBeenCalledTimes(1);
    expect(stmts.getAll).toBeDefined();
  });

  test("caches result — factory only called once", () => {
    const factory = jest.fn((db) => ({
      getAll: db.prepare("SELECT * FROM t"),
    }));
    const getStatements = createLazyStatements(factory);
    getStatements();
    getStatements();
    getStatements();
    expect(factory).toHaveBeenCalledTimes(1);
  });

  test("prepared statements actually work", () => {
    const getStatements = createLazyStatements((db) => ({
      getAll: db.prepare("SELECT * FROM t"),
      getById: db.prepare("SELECT * FROM t WHERE id = ?"),
    }));
    const stmts = getStatements();
    const rows = stmts.getAll.all();
    expect(rows).toEqual([{ id: 1, val: "hello" }]);
    const row = stmts.getById.get(1);
    expect(row).toEqual({ id: 1, val: "hello" });
    expect(stmts.getById.get(999)).toBeUndefined();
  });
});
