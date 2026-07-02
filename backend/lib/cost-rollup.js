/**
 * Cost-rollup shaping for GET /analytics/costs.
 *
 * Extracted from routes/analytics.js to separate the pure model/day cost
 * aggregation from HTTP routing and SQLite access.  The route handler
 * previously inlined ~75 lines that turned the grouped per-model and per-day
 * token rows (from SQL) plus the merged pricing map into a full cost report:
 * per-model cost breakdown with percentage share, a daily cost trend, overall
 * input/output/total roll-ups, average daily cost, a 30-day projection, and a
 * list of models with no pricing match.  All of that is a pure function of the
 * two query-row sets plus the pricing map and the requested `days` echo, so
 * isolating it here makes the per-model rounding, percent-share fill, daily
 * bucketing, and monthly projection directly unit-testable instead of only
 * reachable through a live HTTP + SQLite round trip (the endpoint previously
 * had no direct test coverage of the aggregation math).
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic,
 * including the two distinct rounding scales: costs are rounded to 4 decimals
 * (1e4) everywhere except `projected_monthly_cost`, which is rounded to cents
 * (1e2), exactly as the route emitted them.
 *
 * @module lib/cost-rollup
 */

const { computeCost } = require("./pricing");

// Costs are surfaced to 4 decimal places (fractions of a cent) to keep small
// per-model figures meaningful; the 30-day projection is coarser (cents).
const COST_SCALE = 10000;
const CENTS_SCALE = 100;

/** Round to 4 decimal places (fractions of a cent). */
function round4(n) {
  return Math.round(n * COST_SCALE) / COST_SCALE;
}

/**
 * Shape grouped per-model and per-day token rows into the /analytics/costs
 * response body.
 *
 * `modelRows` are one row per model over the whole window; `dailyRows` are one
 * row per `(day, model)` pair.  Rows whose model has no pricing match are
 * dropped from the cost math: the model is recorded in `unmatched_models`
 * (per-model rows only) and simply skipped for the daily trend.  A model that
 * matches pricing contributes to totals, its per-model entry, and the day
 * bucket.  `percent` is each model's share of the matched total cost (0 when
 * the total is 0).  `avg_daily_cost` divides the total by the number of days
 * that had any matched cost; `projected_monthly_cost` extrapolates that
 * average across 30 days.
 *
 * @param {Array<{ model: string, call_count: number, total_tokens_in: number,
 *   total_tokens_out: number }>} modelRows - Per-model token aggregates.
 * @param {Array<{ day: string, model: string, tokens_in: number,
 *   tokens_out: number }>} dailyRows - Per-(day, model) token aggregates,
 *   expected in ascending day order (echoed through as-is).
 * @param {Object} pricingMap - Merged pricing map (DB overrides + built-in
 *   defaults) as returned by loadPricingMap(); passed straight to computeCost.
 * @param {Object} [opts={}]
 * @param {number} [opts.days] - Period echoed back as `period_days` (also the
 *   projection base is a fixed 30 days, independent of this value).
 * @returns {{ period_days: (number|undefined), total_cost: number,
 *   total_input_cost: number, total_output_cost: number, avg_daily_cost: number,
 *   projected_monthly_cost: number, currency: string,
 *   by_model: Array<Object>, daily_trend: Array<Object>,
 *   unmatched_models: Array<string> }}
 */
function rollUpCosts(modelRows, dailyRows, pricingMap, opts = {}) {
  const { days } = opts;

  let totalCost = 0;
  let totalInputCost = 0;
  let totalOutputCost = 0;
  const modelCosts = [];
  const unmatchedModels = [];

  // Aggregate by model
  for (const row of modelRows) {
    const cost = computeCost(row.model, row.total_tokens_in, row.total_tokens_out, pricingMap);
    if (cost) {
      totalCost += cost.totalCost;
      totalInputCost += cost.inputCost;
      totalOutputCost += cost.outputCost;
      modelCosts.push({
        model: row.model,
        call_count: row.call_count,
        tokens_in: row.total_tokens_in,
        tokens_out: row.total_tokens_out,
        input_cost: round4(cost.inputCost),
        output_cost: round4(cost.outputCost),
        total_cost: round4(cost.totalCost),
        percent: 0,  // filled below
      });
    } else {
      unmatchedModels.push(row.model);
    }
  }

  // Fill percent (share of the matched total cost)
  for (const mc of modelCosts) {
    mc.percent = totalCost > 0
      ? Math.round((mc.total_cost / totalCost) * COST_SCALE) / CENTS_SCALE
      : 0;
  }

  // Daily cost trend
  const dailyCosts = {};
  for (const row of dailyRows) {
    const cost = computeCost(row.model, row.tokens_in, row.tokens_out, pricingMap);
    if (!cost) continue;
    if (!dailyCosts[row.day]) {
      dailyCosts[row.day] = { day: row.day, cost: 0, input_cost: 0, output_cost: 0 };
    }
    dailyCosts[row.day].cost += cost.totalCost;
    dailyCosts[row.day].input_cost += cost.inputCost;
    dailyCosts[row.day].output_cost += cost.outputCost;
  }

  const dailyTrend = Object.values(dailyCosts).map(d => ({
    day: d.day,
    cost: round4(d.cost),
    input_cost: round4(d.input_cost),
    output_cost: round4(d.output_cost),
  }));

  // Average daily cost (over days that had any matched cost)
  const avgDailyCost = dailyTrend.length > 0
    ? totalCost / dailyTrend.length
    : 0;

  // Projected monthly cost (30-day extrapolation)
  const projectedMonthlyCost = avgDailyCost * 30;

  return {
    period_days: days,
    total_cost: round4(totalCost),
    total_input_cost: round4(totalInputCost),
    total_output_cost: round4(totalOutputCost),
    avg_daily_cost: round4(avgDailyCost),
    projected_monthly_cost: Math.round(projectedMonthlyCost * CENTS_SCALE) / CENTS_SCALE,
    currency: "USD",
    by_model: modelCosts,
    daily_trend: dailyTrend,
    unmatched_models: unmatchedModels,
  };
}

module.exports = {
  rollUpCosts,
};
