/**
 * Minimal jest-style compatibility shim for `node --test`.
 *
 * Background: the dashboard test files were originally written in a
 * jest-style flavour (`describe`/`test`/`expect`). The CI workflow,
 * however, executes them with `node --test`, which only provides
 * `describe`/`it`/`test` from `node:test` and no global `expect`. As a
 * result `tests/errors-dashboard.test.js` and `tests/sla-dashboard.test.js`
 * blew up with `ReferenceError: describe is not defined` before any
 * assertion ran.
 *
 * Rewriting both files (and any future ones) into the node:test +
 * `node:assert` idiom is fine but invasive. Instead, this shim provides:
 *   - `describe` / `test` re-exported from `node:test`.
 *   - A tiny `expect()` wrapper that covers exactly the matchers the
 *     existing dashboard tests use: `.toContain`, `.toBe`,
 *     `.toBeTruthy`, `.toBeFalsy`, `.toEqual`, `.toMatch`,
 *     `.toBeDefined`, `.toBeNull`, plus a `.not` modifier.
 *
 * If a test needs a matcher that isn't here, add it — but keep the shim
 * intentionally small so it's obvious what's supported.
 */

"use strict";

const { describe, test, it } = require("node:test");
const assert = require("node:assert/strict");

function deepEqual(a, b) {
  try {
    assert.deepStrictEqual(a, b);
    return true;
  } catch (_) {
    return false;
  }
}

function buildMatchers(actual, negated) {
  const check = (cond, message) => {
    if (negated ? cond : !cond) {
      throw new assert.AssertionError({
        message: (negated ? "[not] " : "") + message,
        actual,
        operator: "expect",
      });
    }
  };

  const matchers = {
    toContain(expected) {
      if (typeof actual === "string") {
        check(actual.includes(expected), `expected string to contain ${JSON.stringify(expected)}`);
      } else if (Array.isArray(actual)) {
        check(actual.includes(expected), `expected array to contain ${JSON.stringify(expected)}`);
      } else {
        throw new TypeError(`expect(...).toContain only supports strings/arrays, got ${typeof actual}`);
      }
    },
    toBe(expected) {
      check(Object.is(actual, expected), `expected ${JSON.stringify(actual)} to be ${JSON.stringify(expected)}`);
    },
    toEqual(expected) {
      check(deepEqual(actual, expected), `expected values to be deeply equal`);
    },
    toBeTruthy() {
      check(Boolean(actual), `expected ${JSON.stringify(actual)} to be truthy`);
    },
    toBeFalsy() {
      check(!actual, `expected ${JSON.stringify(actual)} to be falsy`);
    },
    toBeDefined() {
      check(actual !== undefined, `expected value to be defined`);
    },
    toBeUndefined() {
      check(actual === undefined, `expected value to be undefined`);
    },
    toBeNull() {
      check(actual === null, `expected value to be null`);
    },
    toMatch(pattern) {
      const re = pattern instanceof RegExp ? pattern : new RegExp(pattern);
      check(typeof actual === "string" && re.test(actual), `expected ${JSON.stringify(actual)} to match ${re}`);
    },
  };

  return matchers;
}

function expect(actual) {
  const positive = buildMatchers(actual, false);
  Object.defineProperty(positive, "not", {
    get: () => buildMatchers(actual, true),
    enumerable: false,
  });
  return positive;
}

module.exports = { describe, test, it, expect };
