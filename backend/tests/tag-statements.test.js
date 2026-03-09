const { getDb } = require("../db");
const { getTagStatements } = require("../lib/tag-statements");

let db;
let stmts;

beforeAll(() => {
  process.env.DB_PATH = ":memory:";
  db = getDb();

  // Seed a couple of sessions for tag operations
  const insertSession = db.prepare(
    "INSERT INTO sessions (session_id, agent_name, started_at) VALUES (?, ?, ?)"
  );
  insertSession.run("sess-1", "agent-a", "2025-01-01T00:00:00Z");
  insertSession.run("sess-2", "agent-b", "2025-01-02T00:00:00Z");
  insertSession.run("sess-3", "agent-a", "2025-01-03T00:00:00Z");

  stmts = getTagStatements();
});

afterAll(() => {
  db.close();
});

describe("getTagStatements", () => {
  test("returns the same cached object on repeated calls", () => {
    const a = getTagStatements();
    const b = getTagStatements();
    expect(a).toBe(b);
  });

  test("returns an object with all expected statement keys", () => {
    const keys = [
      "getTagsForSession",
      "addTag",
      "removeTag",
      "removeAllTags",
      "countTags",
      "sessionsByTag",
      "sessionsByTagCount",
      "allTags",
    ];
    for (const k of keys) {
      expect(stmts).toHaveProperty(k);
    }
  });
});

describe("addTag / getTagsForSession", () => {
  test("adds a tag and retrieves it", () => {
    stmts.addTag.run("sess-1", "production", "2025-01-01T00:00:00Z");
    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(1);
    expect(tags[0].tag).toBe("production");
  });

  test("INSERT OR IGNORE prevents duplicate tags", () => {
    stmts.addTag.run("sess-1", "production", "2025-01-01T01:00:00Z");
    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(1); // still 1
  });

  test("adds multiple tags to the same session", () => {
    stmts.addTag.run("sess-1", "debug", "2025-01-01T02:00:00Z");
    stmts.addTag.run("sess-1", "high-cost", "2025-01-01T03:00:00Z");
    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags).toHaveLength(3);
    expect(tags.map((t) => t.tag)).toEqual(
      expect.arrayContaining(["production", "debug", "high-cost"])
    );
  });

  test("returns tags in created_at ASC order", () => {
    const tags = stmts.getTagsForSession.all("sess-1");
    const dates = tags.map((t) => t.created_at);
    const sorted = [...dates].sort();
    expect(dates).toEqual(sorted);
  });
});

describe("countTags", () => {
  test("returns correct count for a tagged session", () => {
    const row = stmts.countTags.get("sess-1");
    expect(row.count).toBe(3);
  });

  test("returns 0 for a session with no tags", () => {
    const row = stmts.countTags.get("sess-2");
    expect(row.count).toBe(0);
  });
});

describe("removeTag", () => {
  test("removes a specific tag", () => {
    stmts.removeTag.run("sess-1", "debug");
    const tags = stmts.getTagsForSession.all("sess-1");
    expect(tags.map((t) => t.tag)).not.toContain("debug");
  });

  test("removing a non-existent tag is a no-op", () => {
    const before = stmts.countTags.get("sess-1").count;
    stmts.removeTag.run("sess-1", "nonexistent");
    const after = stmts.countTags.get("sess-1").count;
    expect(after).toBe(before);
  });
});

describe("removeAllTags", () => {
  test("removes all tags from a session", () => {
    stmts.addTag.run("sess-2", "test-tag", "2025-01-02T00:00:00Z");
    stmts.addTag.run("sess-2", "another", "2025-01-02T01:00:00Z");
    expect(stmts.countTags.get("sess-2").count).toBe(2);

    stmts.removeAllTags.run("sess-2");
    expect(stmts.countTags.get("sess-2").count).toBe(0);
  });
});

describe("sessionsByTag / sessionsByTagCount", () => {
  beforeAll(() => {
    // Tag sess-1 and sess-3 with "shared"
    stmts.addTag.run("sess-1", "shared", "2025-01-01T04:00:00Z");
    stmts.addTag.run("sess-3", "shared", "2025-01-03T00:00:00Z");
  });

  test("finds sessions by tag", () => {
    const rows = stmts.sessionsByTag.all("shared", 10, 0);
    const ids = rows.map((r) => r.session_id);
    expect(ids).toContain("sess-1");
    expect(ids).toContain("sess-3");
    expect(ids).not.toContain("sess-2");
  });

  test("returns sessions ordered by started_at DESC", () => {
    const rows = stmts.sessionsByTag.all("shared", 10, 0);
    expect(rows[0].session_id).toBe("sess-3"); // 2025-01-03 > 2025-01-01
  });

  test("respects LIMIT and OFFSET", () => {
    const page1 = stmts.sessionsByTag.all("shared", 1, 0);
    expect(page1).toHaveLength(1);
    const page2 = stmts.sessionsByTag.all("shared", 1, 1);
    expect(page2).toHaveLength(1);
    expect(page1[0].session_id).not.toBe(page2[0].session_id);
  });

  test("sessionsByTagCount returns correct count", () => {
    const row = stmts.sessionsByTagCount.get("shared");
    expect(row.count).toBe(2);
  });

  test("sessionsByTagCount returns 0 for unknown tag", () => {
    const row = stmts.sessionsByTagCount.get("does-not-exist");
    expect(row.count).toBe(0);
  });
});

describe("allTags", () => {
  test("lists all tags with session counts", () => {
    const tags = stmts.allTags.all();
    expect(tags.length).toBeGreaterThanOrEqual(1);
    const shared = tags.find((t) => t.tag === "shared");
    expect(shared).toBeDefined();
    expect(shared.session_count).toBe(2);
  });

  test("tags are ordered by session_count DESC then tag ASC", () => {
    const tags = stmts.allTags.all();
    for (let i = 1; i < tags.length; i++) {
      if (tags[i].session_count === tags[i - 1].session_count) {
        expect(tags[i].tag >= tags[i - 1].tag).toBe(true);
      } else {
        expect(tags[i].session_count <= tags[i - 1].session_count).toBe(true);
      }
    }
  });
});
