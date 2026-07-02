/**
 * Tests for lib/cost-rollup.js - the pure per-model / per-day cost aggregation
 * extracted from GET /analytics/costs.
 *
 * The endpoint previously had no direct coverage of the aggregation math: the
 * pricing match, per-model rounding + percent-share fill, daily bucketing, the
 * average-daily / 30-day projection, and the unmatched-model handling were
 * only reachable through a live HTTP + SQLite round trip. These exercise
 * rollUpCosts() directly as a pure unit.
 *
 * A local pricing map (model -> {input, output} price per 1M tokens) is used
 * so the expected costs are exact and independent of DB state / built-in
 * defaults. computeCost() memoizes findPricing() by lowercased model name, so
 * the pricing cache is invalidated before each test to avoid cross-test bleed.
 */

const { rollUpCosts } = require("../lib/cost-rollup");
const { invalidatePricingCache } = require("../lib/pricing");

beforeEach(() => invalidatePricingCache());

// Prices are per 1,000,000 tokens (matching lib/pricing semantics).
const PRICING = {
  "model-a": { input: 10, output: 20, currency: "USD" },   // cheap
  "model-b": { input: 100, output: 200, currency: "USD" }, // 10x
};

// Row builders mirroring the SQL GROUP BY shapes.
const modelRow = (model, call_count, tin, tout) => ({
  model,
  call_count,
  total_tokens_in: tin,
  total_tokens_out: tout,
});
const dailyRow = (day, model, tin, tout) => ({ day, model, tokens_in: tin, tokens_out: tout });

describe("rollUpCosts - shape and empty input", () => {
  test("empty rows yield a fully zeroed report", () => {
    const out = rollUpCosts([], [], PRICING, { days: 30 });
    expect(out).toEqual({
      period_days: 30,
      total_cost: 0,
      total_input_cost: 0,
      total_output_cost: 0,
      avg_daily_cost: 0,
      projected_monthly_cost: 0,
      currency: "USD",
      by_model: [],
      daily_trend: [],
      unmatched_models: [],
    });
  });

  test("echoes days into period_days; undefined when opts omitted", () => {
    expect(rollUpCosts([], [], PRICING, { days: 7 }).period_days).toBe(7);
    expect(rollUpCosts([], [], PRICING).period_days).toBeUndefined();
  });

  test("currency is always USD", () => {
    expect(rollUpCosts([], [], PRICING).currency).toBe("USD");
  });
});

describe("rollUpCosts - single matched model", () => {
  test("computes per-model input/output/total cost and 100% share", () => {
    // model-a: 1,000,000 in @ $10/M = $10 ; 500,000 out @ $20/M = $10 ; total $20
    const out = rollUpCosts(
      [modelRow("model-a", 3, 1_000_000, 500_000)],
      [],
      PRICING,
      { days: 30 }
    );
    expect(out.by_model).toHaveLength(1);
    const mc = out.by_model[0];
    expect(mc.model).toBe("model-a");
    expect(mc.call_count).toBe(3);
    expect(mc.tokens_in).toBe(1_000_000);
    expect(mc.tokens_out).toBe(500_000);
    expect(mc.input_cost).toBe(10);
    expect(mc.output_cost).toBe(10);
    expect(mc.total_cost).toBe(20);
    expect(mc.percent).toBe(100);

    expect(out.total_cost).toBe(20);
    expect(out.total_input_cost).toBe(10);
    expect(out.total_output_cost).toBe(10);
    expect(out.unmatched_models).toEqual([]);
  });
});

describe("rollUpCosts - multiple models and percent share", () => {
  test("percent shares reflect each model's fraction of matched total", () => {
    // model-a total $20 ; model-b: 2,000,000 in @ $100/M = $200 ; 1,000,000 out @ $200/M = $200 ; total $400
    // grand total = $420 -> a = 20/420 = 4.7619...% -> 4.76 ; b = 400/420 = 95.238...% -> 95.24
    const out = rollUpCosts(
      [
        modelRow("model-a", 1, 1_000_000, 500_000),
        modelRow("model-b", 1, 2_000_000, 1_000_000),
      ],
      [],
      PRICING,
      { days: 30 }
    );
    expect(out.total_cost).toBe(420);
    const byName = Object.fromEntries(out.by_model.map((m) => [m.model, m]));
    expect(byName["model-a"].total_cost).toBe(20);
    expect(byName["model-b"].total_cost).toBe(400);
    expect(byName["model-a"].percent).toBe(4.76);
    expect(byName["model-b"].percent).toBe(95.24);
  });
});

describe("rollUpCosts - unmatched models", () => {
  test("a model with no pricing is excluded from cost and listed in unmatched_models", () => {
    const out = rollUpCosts(
      [
        modelRow("model-a", 1, 1_000_000, 500_000),
        modelRow("mystery-model", 5, 9_000_000, 9_000_000),
      ],
      [],
      PRICING,
      { days: 30 }
    );
    expect(out.by_model.map((m) => m.model)).toEqual(["model-a"]);
    expect(out.unmatched_models).toEqual(["mystery-model"]);
    expect(out.total_cost).toBe(20); // mystery-model contributes nothing
  });

  test("all-unmatched input yields zero cost but records every model", () => {
    const out = rollUpCosts(
      [modelRow("ghost-1", 1, 1000, 1000), modelRow("ghost-2", 1, 1000, 1000)],
      [dailyRow("2026-06-01", "ghost-1", 1000, 1000)],
      PRICING,
      { days: 30 }
    );
    expect(out.by_model).toEqual([]);
    expect(out.total_cost).toBe(0);
    expect(out.unmatched_models).toEqual(["ghost-1", "ghost-2"]);
    expect(out.daily_trend).toEqual([]); // unmatched daily rows are skipped
    expect(out.projected_monthly_cost).toBe(0);
  });

  test("percent is 0 for every model when the matched total is 0", () => {
    // Zero tokens -> computeCost returns 0 costs (still a match), total 0 -> percent 0, no divide-by-zero.
    const out = rollUpCosts(
      [modelRow("model-a", 1, 0, 0)],
      [],
      PRICING,
      { days: 30 }
    );
    expect(out.by_model[0].total_cost).toBe(0);
    expect(out.by_model[0].percent).toBe(0);
    expect(out.total_cost).toBe(0);
  });
});

describe("rollUpCosts - daily trend bucketing", () => {
  test("rows for the same day accumulate across models; one entry per day", () => {
    const out = rollUpCosts(
      [],
      [
        dailyRow("2026-06-01", "model-a", 1_000_000, 500_000), // $20
        dailyRow("2026-06-01", "model-b", 1_000_000, 0),        // input $100, output $0 -> $100
        dailyRow("2026-06-02", "model-a", 500_000, 0),          // input $5, output $0 -> $5
      ],
      PRICING,
      { days: 30 }
    );
    expect(out.daily_trend).toHaveLength(2);
    const byDay = Object.fromEntries(out.daily_trend.map((d) => [d.day, d]));
    expect(byDay["2026-06-01"].cost).toBe(120);       // 20 + 100
    expect(byDay["2026-06-01"].input_cost).toBe(110);  // 10 + 100
    expect(byDay["2026-06-01"].output_cost).toBe(10);  // 10 + 0
    expect(byDay["2026-06-02"].cost).toBe(5);
  });

  test("daily trend preserves input row order (day ordering echoed as-is)", () => {
    const out = rollUpCosts(
      [],
      [
        dailyRow("2026-06-01", "model-a", 1_000_000, 0),
        dailyRow("2026-06-03", "model-a", 1_000_000, 0),
        dailyRow("2026-06-02", "model-a", 1_000_000, 0),
      ],
      PRICING
    );
    expect(out.daily_trend.map((d) => d.day)).toEqual(["2026-06-01", "2026-06-03", "2026-06-02"]);
  });
});

describe("rollUpCosts - average daily cost and projection", () => {
  test("avg_daily_cost divides total by number of days with matched cost", () => {
    // Two days: $10 and $30 -> total $40 across the model rows, avg = 40/2 = $20/day.
    const out = rollUpCosts(
      [modelRow("model-a", 2, 2_000_000, 500_000)], // total: input $20 + output $10 = $30... see note
      [
        dailyRow("2026-06-01", "model-a", 1_000_000, 0), // $10
        dailyRow("2026-06-02", "model-a", 2_000_000, 0), // $20
      ],
      PRICING,
      { days: 30 }
    );
    // total_cost comes from the per-MODEL rows (input 2M @ $10 = $20, output 500k @ $20 = $10) => $30.
    expect(out.total_cost).toBe(30);
    // daily_trend has 2 entries -> avg = 30 / 2 = 15
    expect(out.daily_trend).toHaveLength(2);
    expect(out.avg_daily_cost).toBe(15);
    // projected monthly = avg * 30 = 450
    expect(out.projected_monthly_cost).toBe(450);
  });

  test("projection is always a fixed 30x of avg, independent of the days echo", () => {
    const out = rollUpCosts(
      [modelRow("model-a", 1, 1_000_000, 0)], // $10 total
      [dailyRow("2026-06-01", "model-a", 1_000_000, 0)], // one day
      PRICING,
      { days: 7 } // even though days=7, projection uses 30
    );
    expect(out.total_cost).toBe(10);
    expect(out.avg_daily_cost).toBe(10); // 10 / 1 day
    expect(out.projected_monthly_cost).toBe(300); // 10 * 30
    expect(out.period_days).toBe(7);
  });

  test("avg and projection are 0 when there is no daily data", () => {
    const out = rollUpCosts([modelRow("model-a", 1, 1_000_000, 0)], [], PRICING, { days: 30 });
    expect(out.total_cost).toBe(10);   // per-model total still computed
    expect(out.avg_daily_cost).toBe(0); // no daily rows -> divisor 0 guarded to 0
    expect(out.projected_monthly_cost).toBe(0);
  });
});

describe("rollUpCosts - rounding scales", () => {
  test("costs round to 4 decimals; projection rounds to cents (2 decimals)", () => {
    // Craft a fractional cost: input price $3/M, 1 token in -> 3 / 1e6 = 0.000003 -> round4 -> 0.
    // Use a price that lands on 4-decimal and 2-decimal boundaries distinctly.
    const pricing = { "frac-model": { input: 33333, output: 0, currency: "USD" } };
    // 100 tokens in @ $33333/M = 100/1e6 * 33333 = 3.3333 -> round4 = 3.3333
    const out = rollUpCosts(
      [modelRow("frac-model", 1, 100, 0)],
      [dailyRow("2026-06-01", "frac-model", 100, 0)],
      pricing,
      { days: 30 }
    );
    expect(out.by_model[0].input_cost).toBe(3.3333); // 4-decimal precision retained
    expect(out.total_cost).toBe(3.3333);
    // avg = 3.3333 / 1 day = 3.3333 ; projection = 3.3333 * 30 = 99.999 -> round to cents = 100
    expect(out.avg_daily_cost).toBe(3.3333);
    expect(out.projected_monthly_cost).toBe(100);
  });
});
