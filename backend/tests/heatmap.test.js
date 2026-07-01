/**
 * Tests for lib/heatmap.js - the pure day-of-week x hour-of-day matrix
 * construction extracted from GET /analytics/heatmap.
 *
 * The endpoint previously had no direct coverage: the matrix bucketing,
 * intensity normalization, per-day / per-hour roll-ups, and peak detection
 * were only reachable through a live HTTP + SQLite round trip. These exercise
 * buildHeatmap() directly as a pure unit.
 */

const { buildHeatmap, DAY_NAMES } = require("../lib/heatmap");

// Helper: a grouped bucket row as produced by the SQL GROUP BY dow, hour.
const cell = (dow, hour, value) => ({ dow, hour, value });

describe("buildHeatmap - shape and echoes", () => {
  test("empty rows yield a zeroed 7x24 matrix and empty cells", () => {
    const out = buildHeatmap([], { days: 30, metric: "events" });
    expect(out.matrix).toHaveLength(7);
    for (const row of out.matrix) {
      expect(row).toHaveLength(24);
      expect(row.every((v) => v === 0)).toBe(true);
    }
    expect(out.cells).toEqual([]);
    expect(out.max_value).toBe(0);
  });

  test("echoes days and metric verbatim without using them in computation", () => {
    const out = buildHeatmap([], { days: 7, metric: "tokens" });
    expect(out.period_days).toBe(7);
    expect(out.metric).toBe("tokens");
  });

  test("missing opts default period_days/metric to undefined (no crash)", () => {
    const out = buildHeatmap([cell(0, 0, 1)]);
    expect(out.period_days).toBeUndefined();
    expect(out.metric).toBeUndefined();
    expect(out.max_value).toBe(1);
  });

  test("day_totals and hour_totals always have full 7 / 24 length", () => {
    const out = buildHeatmap([cell(2, 5, 3)], { days: 1, metric: "events" });
    expect(out.day_totals).toHaveLength(7);
    expect(out.hour_totals).toHaveLength(24);
    expect(out.day_totals.map((d) => d.day_name)).toEqual(DAY_NAMES);
  });
});

describe("buildHeatmap - matrix placement", () => {
  test("places a value at the correct [dow][hour] slot", () => {
    const out = buildHeatmap([cell(3, 14, 42)], { days: 30, metric: "events" });
    expect(out.matrix[3][14]).toBe(42);
    // everything else stays zero
    expect(out.matrix[3][13]).toBe(0);
    expect(out.matrix[2][14]).toBe(0);
  });

  test("Sunday=0 and Saturday=6 map to the expected day names", () => {
    const out = buildHeatmap([cell(0, 0, 1), cell(6, 23, 2)], {
      days: 30,
      metric: "events",
    });
    expect(out.day_totals[0].day_name).toBe("Sunday");
    expect(out.day_totals[6].day_name).toBe("Saturday");
    expect(out.matrix[0][0]).toBe(1);
    expect(out.matrix[6][23]).toBe(2);
  });

  test("ignores out-of-range dow/hour defensively (no throw, no placement)", () => {
    const out = buildHeatmap(
      [cell(7, 0, 99), cell(0, 24, 99), cell(-1, -1, 99), cell(1, 1, 5)],
      { days: 30, metric: "events" }
    );
    // only the valid (1,1) row lands; the bogus ones are skipped
    expect(out.max_value).toBe(5);
    expect(out.matrix[1][1]).toBe(5);
    const nonZero = out.matrix.flat().filter((v) => v !== 0);
    expect(nonZero).toEqual([5]);
  });
});

describe("buildHeatmap - cells and intensity", () => {
  test("emits only non-zero cells, sorted by day then hour", () => {
    const out = buildHeatmap(
      [cell(1, 5, 10), cell(0, 9, 4), cell(1, 2, 8)],
      { days: 30, metric: "events" }
    );
    expect(out.cells.map((c) => [c.day, c.hour])).toEqual([
      [0, 9],
      [1, 2],
      [1, 5],
    ]);
  });

  test("intensity is value/max rounded to 2 decimals", () => {
    // max = 8 -> 3/8 = 0.375 -> rounds to 0.38; 8/8 = 1
    const out = buildHeatmap([cell(0, 0, 3), cell(0, 1, 8)], {
      days: 30,
      metric: "events",
    });
    const byHour = Object.fromEntries(out.cells.map((c) => [c.hour, c]));
    expect(byHour[0].intensity).toBe(0.38);
    expect(byHour[1].intensity).toBe(1);
  });

  test("each cell carries day_name matching its day index", () => {
    const out = buildHeatmap([cell(4, 12, 7)], { days: 30, metric: "events" });
    expect(out.cells[0].day_name).toBe("Thursday");
    expect(out.cells[0].value).toBe(7);
  });
});

describe("buildHeatmap - totals", () => {
  test("day_totals sum each day's row", () => {
    const out = buildHeatmap(
      [cell(2, 1, 3), cell(2, 5, 4), cell(2, 10, 5)],
      { days: 30, metric: "events" }
    );
    expect(out.day_totals[2].total).toBe(12);
    expect(out.day_totals[0].total).toBe(0);
  });

  test("hour_totals sum each hour column across days", () => {
    const out = buildHeatmap(
      [cell(0, 9, 2), cell(3, 9, 5), cell(6, 9, 1)],
      { days: 30, metric: "events" }
    );
    expect(out.hour_totals[9].total).toBe(8);
    expect(out.hour_totals[8].total).toBe(0);
  });
});

describe("buildHeatmap - peak detection", () => {
  test("selects the max cell as peak with correct coordinates and name", () => {
    const out = buildHeatmap(
      [cell(1, 3, 5), cell(4, 20, 99), cell(6, 6, 12)],
      { days: 30, metric: "events" }
    );
    expect(out.peak).toEqual({
      day: 4,
      day_name: "Thursday",
      hour: 20,
      value: 99,
    });
    expect(out.max_value).toBe(99);
  });

  test("ties resolve to the first-scanned cell (lowest day, then hour)", () => {
    // Two cells share the max value 10; scan order is day-outer, hour-inner,
    // and only a strictly-greater value replaces the peak -> (1,2) wins.
    const out = buildHeatmap(
      [cell(5, 8, 10), cell(1, 2, 10)],
      { days: 30, metric: "events" }
    );
    expect(out.peak.day).toBe(1);
    expect(out.peak.hour).toBe(2);
    expect(out.peak.value).toBe(10);
  });

  test("all-empty input yields a zero-valued peak at the origin", () => {
    const out = buildHeatmap([], { days: 30, metric: "events" });
    expect(out.peak).toEqual({
      day: 0,
      day_name: "Sunday",
      hour: 0,
      value: 0,
    });
  });
});
