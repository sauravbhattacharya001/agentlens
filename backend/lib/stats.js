/**
 * Statistical utility functions for analytics and performance routes.
 *
 * Extracted from analytics.js /performance endpoint to enable reuse
 * across analytics, alerts, and any future route that needs percentile
 * calculations or grouped duration breakdowns.
 */

"use strict";

/**
 * Compute the p-th percentile from a **pre-sorted ascending** array
 * using linear interpolation.
 *
 * @param {number[]} sorted — Values sorted in ascending order.
 * @param {number}   p      — Percentile (0–100).
 * @returns {number}  Interpolated value at the given percentile, or 0 for empty input.
 */
function percentile(sorted, p) {
  if (sorted.length === 0) return 0;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

/**
 * Compute a full latency statistics object from sorted durations.
 *
 * @param {number[]} sortedDurations — Duration values sorted ascending.
 * @param {number}   [precomputedSum] — If the caller already has the sum
 *   (e.g. from a SQL SUM()), pass it here to skip the O(n) reduce.
 * @returns {{ p50: number, p75: number, p90: number, p95: number, p99: number,
 *             avg: number, min: number, max: number } | null}
 *   Rounded to 2 decimal places. Returns null if the array is empty.
 */
function latencyStats(sortedDurations, precomputedSum) {
  if (sortedDurations.length === 0) return null;
  const total = typeof precomputedSum === "number" ? precomputedSum
    : sortedDurations.reduce((a, b) => a + b, 0);
  return {
    p50: round2(percentile(sortedDurations, 50)),
    p75: round2(percentile(sortedDurations, 75)),
    p90: round2(percentile(sortedDurations, 90)),
    p95: round2(percentile(sortedDurations, 95)),
    p99: round2(percentile(sortedDurations, 99)),
    avg: round2(total / sortedDurations.length),
    min: sortedDurations[0],
    max: sortedDurations[sortedDurations.length - 1],
  };
}

/**
 * Group an array of objects by a key function, accumulating durations
 * and token counts per group.
 *
 * Returns a plain object mapping each group key to its aggregated stats.
 * Duration arrays within each group are already sorted ascending.
 *
 * @param {Array<{ duration_ms: number, tokens_in?: number, tokens_out?: number }>} events
 * @param {(event: object) => string} keyFn — Extracts the grouping key from each event.
 * @returns {Object<string, { count: number, durations: number[], tokens_in: number, tokens_out: number }>}
 */
function groupEventStats(events, keyFn) {
  const groups = Object.create(null);
  for (const e of events) {
    const k = keyFn(e);
    if (!groups[k]) groups[k] = { durations: [], tokens_in: 0, tokens_out: 0, count: 0 };
    groups[k].durations.push(e.duration_ms);
    groups[k].tokens_in += e.tokens_in || 0;
    groups[k].tokens_out += e.tokens_out || 0;
    groups[k].count++;
  }
  // Sort durations within each group for percentile calculation
  for (const g of Object.values(groups)) {
    g.durations.sort((a, b) => a - b);
  }
  return groups;
}

/**
 * Build a per-group performance breakdown from grouped event stats.
 *
 * Each group entry includes latency percentiles, token totals, and
 * tokens-per-second throughput.
 *
 * @param {ReturnType<typeof groupEventStats>} groups — Output of groupEventStats().
 * @returns {Object<string, object>}
 */
function buildGroupPerf(groups) {
  const result = Object.create(null);
  for (const [key, data] of Object.entries(groups)) {
    const totalTokens = data.tokens_in + data.tokens_out;
    const totalDur = data.durations.reduce((a, b) => a + b, 0);
    result[key] = {
      count: data.count,
      latency: latencyStats(data.durations),
      tokens: {
        total_in: data.tokens_in,
        total_out: data.tokens_out,
        total: totalTokens,
        avg_per_call: Math.round(totalTokens / data.count),
      },
      tokens_per_second: totalDur > 0
        ? round2(totalTokens / (totalDur / 1000))
        : 0,
    };
  }
  return result;
}

/** Round to 2 decimal places. */
function round2(n) {
  return Math.round(n * 100) / 100;
}

module.exports = {
  percentile,
  latencyStats,
  groupEventStats,
  buildGroupPerf,
  round2,
};
