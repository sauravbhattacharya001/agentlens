/**
 * Activity-heatmap shaping for GET /analytics/heatmap.
 *
 * Extracted from routes/analytics.js to separate the pure day-of-week ×
 * hour-of-day matrix construction from HTTP routing and SQLite access.  The
 * route handler previously inlined ~60 lines that turned the grouped
 * `(dow, hour, value)` rows returned by SQL into a 7×24 matrix, a flattened
 * non-zero `cells` list with normalized intensity, per-day / per-hour totals,
 * and peak detection.  All of that is a pure function of the query rows plus
 * the requested `days`/`metric` echoes, so isolating it here makes the
 * bucketing, intensity normalization, total roll-ups, and peak selection
 * directly unit-testable instead of only reachable through a live HTTP +
 * SQLite round trip (the endpoint previously had no direct test coverage).
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic.
 *
 * @module lib/heatmap
 */

// Sun=0 .. Sat=6, matching SQLite strftime('%w', ...).
const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

/**
 * Shape grouped activity rows into the /analytics/heatmap response body.
 *
 * Each input row is one populated `(day-of-week, hour-of-day)` bucket; buckets
 * with no activity are simply absent from `rows` and default to 0 in the
 * matrix.  `day` values are expected in 0-6 (Sun-Sat) and `hour` in 0-23, as
 * produced by `strftime('%w'|'%H', ...)`; out-of-range indices are ignored
 * defensively so a malformed row can never throw.
 *
 * @param {Array<{ dow: number, hour: number, value: number }>} rows
 *   Grouped activity buckets from SQL (already filtered to the time window).
 * @param {Object} [opts={}]
 * @param {number} [opts.days] - Period echoed back into the response as
 *   `period_days` (not used in any computation).
 * @param {string} [opts.metric] - Metric name echoed back as `metric`.
 * @returns {{
 *   period_days: number|undefined,
 *   metric: string|undefined,
 *   max_value: number,
 *   peak: { day: number, day_name: string, hour: number, value: number },
 *   matrix: number[][],
 *   cells: Array<{ day: number, day_name: string, hour: number, value: number, intensity: number }>,
 *   day_totals: Array<{ day: number, day_name: string, total: number }>,
 *   hour_totals: Array<{ hour: number, total: number }>
 * }} The heatmap response body.
 */
function buildHeatmap(rows, opts = {}) {
  const { days, metric } = opts;

  // Build 7×24 matrix (Sun=0 .. Sat=6, hours 0-23)
  const matrix = Array.from({ length: 7 }, () => Array(24).fill(0));
  let maxValue = 0;

  for (const row of rows) {
    // Defensive bounds check: strftime always yields 0-6 / 0-23, but guard
    // so an unexpected row can never index outside the fixed-size matrix.
    if (row.dow < 0 || row.dow > 6 || row.hour < 0 || row.hour > 23) continue;
    matrix[row.dow][row.hour] = row.value;
    if (row.value > maxValue) maxValue = row.value;
  }

  // Flatten for easy consumption (non-zero cells only)
  const cells = [];
  for (let d = 0; d < 7; d++) {
    for (let h = 0; h < 24; h++) {
      if (matrix[d][h] > 0) {
        cells.push({
          day: d,
          day_name: DAY_NAMES[d],
          hour: h,
          value: matrix[d][h],
          intensity: maxValue > 0 ? Math.round((matrix[d][h] / maxValue) * 100) / 100 : 0,
        });
      }
    }
  }

  // Day and hour totals
  const dayTotals = DAY_NAMES.map((name, i) => ({
    day: i,
    day_name: name,
    total: matrix[i].reduce((a, b) => a + b, 0),
  }));

  const hourTotals = Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    total: matrix.reduce((sum, row) => sum + row[h], 0),
  }));

  // Peak detection
  let peakDay = 0, peakHour = 0, peakValue = 0;
  for (let d = 0; d < 7; d++) {
    for (let h = 0; h < 24; h++) {
      if (matrix[d][h] > peakValue) {
        peakValue = matrix[d][h];
        peakDay = d;
        peakHour = h;
      }
    }
  }

  return {
    period_days: days,
    metric,
    max_value: maxValue,
    peak: {
      day: peakDay,
      day_name: DAY_NAMES[peakDay],
      hour: peakHour,
      value: peakValue,
    },
    matrix,
    cells,
    day_totals: dayTotals,
    hour_totals: hourTotals,
  };
}

module.exports = {
  DAY_NAMES,
  buildHeatmap,
};
