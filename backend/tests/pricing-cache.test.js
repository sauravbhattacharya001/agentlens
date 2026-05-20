"use strict";

/**
 * Unit tests for lib/pricing — specifically the cache-invalidation
 * behavior added when fixing #189. Covers:
 *   - cache is rebuilt when model_pricing row count changes
 *   - cache is rebuilt when model_pricing.updated_at changes (upserts)
 *   - cache is rebuilt after the DB handle is swapped
 *   - findPricing prefix fallback survives the rebuild
 */

const Database = require("better-sqlite3");

let mockDb = null;
jest.mock("../db", () => ({ getDb: () => mockDb }));

function freshDb() {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE model_pricing (
      model TEXT PRIMARY KEY,
      input_cost_per_1m REAL NOT NULL,
      output_cost_per_1m REAL NOT NULL,
      currency TEXT NOT NULL DEFAULT 'USD',
      updated_at TEXT NOT NULL
    );
  `);
  return db;
}

function upsert(db, model, input, output, ts) {
  db.prepare(
    `INSERT OR REPLACE INTO model_pricing
     (model, input_cost_per_1m, output_cost_per_1m, currency, updated_at)
     VALUES (?, ?, ?, 'USD', ?)`
  ).run(model, input, output, ts);
}

describe("lib/pricing — cache invalidation", () => {
  let pricing;

  beforeEach(() => {
    jest.resetModules();
    mockDb = freshDb();
    pricing = require("../lib/pricing");
    pricing.invalidatePricingCache();
  });

  afterEach(() => {
    try {
      mockDb && mockDb.close();
    } catch (_) {}
    mockDb = null;
  });

  test("picks up newly inserted rows without an explicit invalidate", () => {
    const before = pricing.loadPricingMap();
    expect(before["new-llm-9"]).toBeUndefined();

    upsert(mockDb, "new-llm-9", 1.0, 2.0, "2026-01-01T00:00:00Z");

    const after = pricing.loadPricingMap();
    expect(after["new-llm-9"]).toEqual({
      input: 1.0,
      output: 2.0,
      currency: "USD",
    });
  });

  test("picks up updates to an existing row (updated_at changes)", () => {
    upsert(mockDb, "shared-llm", 1.0, 2.0, "2026-01-01T00:00:00Z");
    expect(pricing.loadPricingMap()["shared-llm"].input).toBe(1.0);

    upsert(mockDb, "shared-llm", 5.0, 9.0, "2026-01-02T00:00:00Z");
    expect(pricing.loadPricingMap()["shared-llm"]).toEqual({
      input: 5.0,
      output: 9.0,
      currency: "USD",
    });
  });

  test("rebuilds cache when the DB handle is swapped", () => {
    upsert(mockDb, "swap-llm", 7.0, 8.0, "2026-01-01T00:00:00Z");
    expect(pricing.loadPricingMap()["swap-llm"].input).toBe(7.0);

    // Simulate a test rebuilding the DB. The new DB has no rows.
    mockDb.close();
    mockDb = freshDb();

    const after = pricing.loadPricingMap();
    expect(after["swap-llm"]).toBeUndefined();
    // Defaults still present.
    expect(after["gpt-4o"]).toBeDefined();
  });

  test("returns the same map object on repeat calls when nothing changes", () => {
    upsert(mockDb, "stable-llm", 1.0, 2.0, "2026-01-01T00:00:00Z");
    const a = pricing.loadPricingMap();
    const b = pricing.loadPricingMap();
    expect(a).toBe(b);
  });

  test("computeCost reflects the most recent pricing", () => {
    upsert(mockDb, "compute-llm", 2.0, 4.0, "2026-01-01T00:00:00Z");
    let map = pricing.loadPricingMap();
    let cost = pricing.computeCost("compute-llm", 1_000_000, 500_000, map);
    // 1M in * $2/M + 0.5M out * $4/M = 2 + 2 = 4
    expect(cost.totalCost).toBeCloseTo(4.0, 6);

    upsert(mockDb, "compute-llm", 3.0, 6.0, "2026-01-02T00:00:00Z");
    map = pricing.loadPricingMap();
    cost = pricing.computeCost("compute-llm", 1_000_000, 500_000, map);
    // 1M * $3/M + 0.5M * $6/M = 3 + 3 = 6
    expect(cost.totalCost).toBeCloseTo(6.0, 6);
  });

  test("findPricing prefix fallback works after cache rebuild", () => {
    upsert(mockDb, "gpt-custom", 1.0, 2.0, "2026-01-01T00:00:00Z");
    let map = pricing.loadPricingMap();
    expect(pricing.findPricing("gpt-custom-2026-05-01", map)).toEqual({
      input: 1.0,
      output: 2.0,
      currency: "USD",
    });

    // Mutate -> cache rebuild + findPricing lookup cache clear.
    upsert(mockDb, "gpt-custom", 9.0, 18.0, "2026-02-01T00:00:00Z");
    map = pricing.loadPricingMap();
    expect(pricing.findPricing("gpt-custom-2026-05-01", map)).toEqual({
      input: 9.0,
      output: 18.0,
      currency: "USD",
    });
  });
});
