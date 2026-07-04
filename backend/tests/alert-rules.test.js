/**
 * Tests for lib/alert-rules.js — the pure alerting primitives extracted from
 * routes/alerts.js: the metric/operator catalog, the threshold comparator
 * (`compareValue`), and the window/cooldown security clamps.
 *
 * Before extraction these lived inline in the route module and were only
 * reachable through a live HTTP + SQLite round trip: `compareValue` ran solely
 * inside POST /alerts/evaluate, the metric catalog + descriptions were split
 * across POST /rules and GET /metrics, and the clamp expressions were
 * duplicated across POST /rules and PUT /rules/:ruleId. The end-to-end suite
 * (alerts.test.js) still exercises the full path; these pin each primitive in
 * isolation — deterministic, no DB, no HTTP — so a regression in the
 * fire/no-fire decision or the SQL-injection-safety bounds is caught at the
 * unit level.
 */

const {
  METRIC_DESCRIPTIONS,
  VALID_METRICS,
  VALID_OPERATORS,
  MAX_WINDOW_MINUTES,
  MAX_COOLDOWN_MINUTES,
  MAX_NAME_LENGTH,
  MAX_AGENT_FILTER_LENGTH,
  MAX_ALERT_RULES,
  compareValue,
  clampWindowMinutes,
  clampCooldownMinutes,
} = require("../lib/alert-rules");

// ===================================================================
// Metric / operator catalog
// ===================================================================
describe("alert-rules catalog", () => {
  test("VALID_METRICS is exactly the eight supported metric names", () => {
    expect(VALID_METRICS).toEqual([
      "total_tokens",
      "avg_tokens_per_session",
      "error_rate",
      "avg_duration_ms",
      "max_duration_ms",
      "session_count",
      "event_count",
      "token_rate",
    ]);
  });

  test("VALID_METRICS is derived from METRIC_DESCRIPTIONS (they cannot drift)", () => {
    // The route surfaces VALID_METRICS.map(m => ({ name, description:
    // METRIC_DESCRIPTIONS[m] })), so every metric MUST have a description and
    // vice-versa or GET /alerts/metrics would emit an undefined description.
    expect(VALID_METRICS).toEqual(Object.keys(METRIC_DESCRIPTIONS));
    for (const m of VALID_METRICS) {
      expect(typeof METRIC_DESCRIPTIONS[m]).toBe("string");
      expect(METRIC_DESCRIPTIONS[m].length).toBeGreaterThan(0);
    }
  });

  test("VALID_OPERATORS is exactly the six comparison operators", () => {
    expect(VALID_OPERATORS).toEqual(["<", ">", "<=", ">=", "==", "!="]);
  });

  test("every VALID_OPERATOR is handled by compareValue (no operator falls through to fail-closed)", () => {
    // If an operator is in the catalog but not in the switch, compareValue
    // would silently return false for it — a rule that can never fire. Prove
    // each catalog operator produces a real true-branch for some input.
    const cases = {
      "<":  [1, 2],
      ">":  [2, 1],
      "<=": [2, 2],
      ">=": [2, 2],
      "==": [2, 2],
      "!=": [1, 2],
    };
    for (const op of VALID_OPERATORS) {
      const [value, threshold] = cases[op];
      expect(compareValue(value, op, threshold)).toBe(true);
    }
  });
});

// ===================================================================
// Security limit constants
// ===================================================================
describe("alert-rules security limits", () => {
  test("window and cooldown are both capped at 7 days (10080 minutes)", () => {
    expect(MAX_WINDOW_MINUTES).toBe(10080);
    expect(MAX_COOLDOWN_MINUTES).toBe(10080);
    expect(MAX_WINDOW_MINUTES).toBe(7 * 24 * 60);
  });

  test("name, agent-filter, and rule-count caps hold their documented values", () => {
    expect(MAX_NAME_LENGTH).toBe(128);
    expect(MAX_AGENT_FILTER_LENGTH).toBe(256);
    expect(MAX_ALERT_RULES).toBe(100);
  });
});

// ===================================================================
// compareValue — the fire / no-fire decision
// ===================================================================
describe("compareValue - operator semantics", () => {
  test("< fires only when value is strictly below threshold", () => {
    expect(compareValue(4, "<", 5)).toBe(true);
    expect(compareValue(5, "<", 5)).toBe(false);
    expect(compareValue(6, "<", 5)).toBe(false);
  });

  test("> fires only when value is strictly above threshold", () => {
    expect(compareValue(6, ">", 5)).toBe(true);
    expect(compareValue(5, ">", 5)).toBe(false);
    expect(compareValue(4, ">", 5)).toBe(false);
  });

  test("<= is inclusive at the boundary", () => {
    expect(compareValue(5, "<=", 5)).toBe(true);
    expect(compareValue(4.999, "<=", 5)).toBe(true);
    expect(compareValue(5.001, "<=", 5)).toBe(false);
  });

  test(">= is inclusive at the boundary", () => {
    expect(compareValue(5, ">=", 5)).toBe(true);
    expect(compareValue(5.001, ">=", 5)).toBe(true);
    expect(compareValue(4.999, ">=", 5)).toBe(false);
  });

  test("== matches only exact equality", () => {
    expect(compareValue(5, "==", 5)).toBe(true);
    expect(compareValue(5.0, "==", 5)).toBe(true);
    expect(compareValue(5.0001, "==", 5)).toBe(false);
  });

  test("!= matches only inequality", () => {
    expect(compareValue(4, "!=", 5)).toBe(true);
    expect(compareValue(5, "!=", 5)).toBe(false);
  });
});

describe("compareValue - fail-closed and edge inputs", () => {
  test("an unknown operator never fires (fail-closed, not a throw)", () => {
    // A malformed/legacy operator must not crash /evaluate and must not fire —
    // the switch default returns false so a bad rule stays silent.
    expect(compareValue(100, "=~", 5)).toBe(false);
    expect(compareValue(100, "", 5)).toBe(false);
    expect(compareValue(100, undefined, 5)).toBe(false);
    expect(compareValue(100, null, 5)).toBe(false);
  });

  test("uses strict === for == so 0 does not equal a nullish threshold", () => {
    // Guards against a loose-equality regression (0 == null is false in JS,
    // but 0 == '' is true — strict === keeps numeric semantics clean).
    expect(compareValue(0, "==", null)).toBe(false);
    expect(compareValue(0, "!=", null)).toBe(true);
  });

  test("negative values compare numerically", () => {
    expect(compareValue(-5, "<", 0)).toBe(true);
    expect(compareValue(-5, ">", -10)).toBe(true);
  });

  test("handles the zero threshold used by 'fire on any activity' rules", () => {
    expect(compareValue(1, ">", 0)).toBe(true);
    expect(compareValue(0, ">", 0)).toBe(false);
    expect(compareValue(0, "==", 0)).toBe(true);
  });
});

// ===================================================================
// clampWindowMinutes — [1, MAX_WINDOW_MINUTES]
// ===================================================================
describe("clampWindowMinutes", () => {
  test("passes through values already inside the safe range", () => {
    expect(clampWindowMinutes(60)).toBe(60);
    expect(clampWindowMinutes(1)).toBe(1);
    expect(clampWindowMinutes(10080)).toBe(10080);
  });

  test("floors at 1 (never a zero/negative window that would scan nothing or error)", () => {
    expect(clampWindowMinutes(0)).toBe(1);
    expect(clampWindowMinutes(-5)).toBe(1);
  });

  test("caps at MAX_WINDOW_MINUTES to prevent expensive full-table scans", () => {
    expect(clampWindowMinutes(10081)).toBe(MAX_WINDOW_MINUTES);
    expect(clampWindowMinutes(1_000_000)).toBe(MAX_WINDOW_MINUTES);
  });

  test("mirrors the POST default idiom: Number(x) || 60 then clamp", () => {
    // POST /rules computes clampWindowMinutes(Number(window_minutes) || 60);
    // reproduce that composition to pin the create-time default.
    const fromMissing = clampWindowMinutes(Number(undefined) || 60);
    expect(fromMissing).toBe(60);
    const fromString = clampWindowMinutes(Number("120") || 60);
    expect(fromString).toBe(120);
  });
});

// ===================================================================
// clampCooldownMinutes — [0, MAX_COOLDOWN_MINUTES]
// ===================================================================
describe("clampCooldownMinutes", () => {
  test("passes through values already inside the safe range", () => {
    expect(clampCooldownMinutes(15)).toBe(15);
    expect(clampCooldownMinutes(10080)).toBe(10080);
  });

  test("allows a zero cooldown (intentional: fire on every evaluation)", () => {
    // Unlike the window, the cooldown floor is 0 — a rule may opt out of the
    // debounce entirely. This asymmetry is the whole reason the two clamps
    // are separate functions.
    expect(clampCooldownMinutes(0)).toBe(0);
  });

  test("floors negatives at 0", () => {
    expect(clampCooldownMinutes(-1)).toBe(0);
    expect(clampCooldownMinutes(-100)).toBe(0);
  });

  test("caps at MAX_COOLDOWN_MINUTES", () => {
    expect(clampCooldownMinutes(10081)).toBe(MAX_COOLDOWN_MINUTES);
    expect(clampCooldownMinutes(999_999)).toBe(MAX_COOLDOWN_MINUTES);
  });

  test("mirrors the POST default idiom: Number(x) || 15 then clamp", () => {
    const fromMissing = clampCooldownMinutes(Number(undefined) || 15);
    expect(fromMissing).toBe(15);
    const explicitZeroFallsBackToDefault = clampCooldownMinutes(Number(0) || 15);
    // NOTE: this documents the existing || idiom — 0 is falsy so the create
    // path substitutes the 15-minute default. (The PUT path, which clamps
    // Number(x) without the || default, is what preserves an explicit 0.)
    expect(explicitZeroFallsBackToDefault).toBe(15);
  });

  test("mirrors the PUT idiom: Number(x) without a default preserves an explicit 0", () => {
    // PUT /rules/:ruleId computes clampCooldownMinutes(Number(cooldown_minutes));
    // an explicit 0 there must survive as 0 (no debounce), unlike the POST idiom.
    expect(clampCooldownMinutes(Number(0))).toBe(0);
  });
});
