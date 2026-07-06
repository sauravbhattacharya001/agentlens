/**
 * Short, sortable-ish unique identifier generation.
 *
 * Extracted from the three route files that each carried a byte-identical
 * private `generateId()` — routes/alerts.js, routes/annotations.js, and
 * routes/webhooks.js — so the exact ID shape used for alert IDs, annotation
 * IDs, webhook IDs, and webhook delivery IDs has ONE home and direct unit
 * coverage.  Previously each copy was module-private and unexported, so the
 * format contract (a base-36 millisecond clock component joined to 6 random
 * bytes of hex) had no test of its own — a drift in one copy (e.g. dropping
 * the time component, or shrinking the random suffix and weakening collision
 * resistance) would only surface as a duplicate-key or lookup failure at
 * runtime, in whichever route diverged.
 *
 * The ID is `${base36-millis}-${12 hex chars}`, optionally namespaced with a
 * caller-supplied prefix (`${prefix}-${base}`).  The leading time component
 * makes IDs roughly monotonic within a process — handy for `ORDER BY` and for
 * eyeballing recency — while the 48 random bits guard against collisions when
 * two IDs are minted in the same millisecond.  This is a deliberately small,
 * dependency-light scheme (Node's `crypto` only), NOT a UUID: it is used for
 * internal record keys, not for anything requiring RFC-4122 guarantees.
 *
 * This module is a pure function of its argument plus the two ambient sources
 * every prior copy already used (the wall clock and the CSPRNG), so runtime
 * behaviour is byte-for-byte identical to the previous inline implementations:
 *   - alerts.js / webhooks.js called `generateId()`  → `makeId()`
 *   - annotations.js called the `ann-`-prefixed form → `makeId("ann")`
 *
 * @module lib/id-generator
 */

const crypto = require("crypto");

/**
 * Generate a short unique identifier.
 *
 * The returned value is `${Date.now().toString(36)}-${hex}` where `hex` is 6
 * random bytes (12 lowercase hex chars).  When `prefix` is a non-empty string,
 * the value is namespaced as `${prefix}-${base}` — e.g. `makeId("ann")` yields
 * `ann-<base36-millis>-<hex>`, matching the annotation-ID format exactly.
 *
 * The leading base-36 millisecond timestamp keeps IDs roughly time-ordered
 * within a process; the 6 random bytes (48 bits) make same-millisecond
 * collisions vanishingly unlikely.  This is intentionally not a UUID — it is a
 * compact internal record key, not an RFC-4122 identifier.
 *
 * @param {string} [prefix] - Optional namespace prefix. Falsy values (including
 *   the default) produce an unprefixed ID identical to the original
 *   `generateId()` used by alerts.js and webhooks.js.
 * @returns {string} A unique identifier, e.g. `lqf3k2p9-a1b2c3d4e5f6` or, with
 *   a prefix, `ann-lqf3k2p9-a1b2c3d4e5f6`.
 */
function makeId(prefix) {
  const base = `${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`;
  return prefix ? `${prefix}-${base}` : base;
}

module.exports = {
  makeId,
};
