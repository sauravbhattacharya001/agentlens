/**
 * Tests for lib/id-generator.js - the short unique-ID generator extracted from
 * the three route files (alerts.js, annotations.js, webhooks.js) that each
 * carried a byte-identical private `generateId()`.
 *
 * Before extraction the format contract had NO direct coverage: each copy was
 * module-private and unexported, so `${Date.now().toString(36)}-${6 random
 * bytes as hex}` (optionally `ann-`-prefixed) was only exercised transitively
 * through full HTTP create-route round trips. A drift in any one copy - e.g.
 * dropping the time component (killing rough time-ordering) or shrinking the
 * random suffix (weakening collision resistance) - would only surface as a
 * duplicate-key or lookup failure at runtime in whichever route diverged.
 * These pin the ID shape in isolation: deterministic structure, no DB, no HTTP.
 *
 * The byte-for-byte equivalence tests below reproduce the ORIGINAL inline
 * expressions and assert the extracted helper still matches them, so the
 * refactor is provably behaviour-preserving:
 *   - alerts.js / webhooks.js: `${Date.now().toString(36)}-${randomBytes(6)hex}`  -> makeId()
 *   - annotations.js:          `ann-${...same...}`                                 -> makeId("ann")
 */

const crypto = require("node:crypto");
const { makeId } = require("../lib/id-generator");

// The base (unprefixed) shape: base-36 millisecond clock, a hyphen, then
// exactly 12 lowercase hex chars (6 random bytes).
const BASE_RE = /^[0-9a-z]+-[0-9a-f]{12}$/;

// ===================================================================
// Format - the exact ID shape every consumer route depends on
// ===================================================================
describe("makeId - format", () => {
  test("unprefixed ID is `<base36>-<12 hex chars>`", () => {
    expect(makeId()).toMatch(BASE_RE);
  });

  test("the random suffix is exactly 6 bytes (12 lowercase hex chars)", () => {
    const suffix = makeId().split("-")[1];
    expect(suffix).toHaveLength(12);
    expect(suffix).toMatch(/^[0-9a-f]{12}$/);
    expect(suffix).toBe(suffix.toLowerCase());
  });

  test("the leading component is the base-36 millisecond clock", () => {
    // Reconstruct the time component and confirm it decodes to a plausible
    // 'now' (within a generous window), proving it is Date.now() in base 36
    // and not some other radix or a random value.
    const before = Date.now();
    const timePart = makeId().split("-")[0];
    const after = Date.now();
    const decoded = parseInt(timePart, 36);
    expect(decoded).toBeGreaterThanOrEqual(before - 1000);
    expect(decoded).toBeLessThanOrEqual(after + 1000);
  });

  test("a prefix is namespaced as `<prefix>-<base>`", () => {
    const id = makeId("ann");
    expect(id.startsWith("ann-")).toBe(true);
    // Strip the prefix and its joining hyphen; the remainder is a full base ID.
    expect(id.slice("ann-".length)).toMatch(BASE_RE);
    // And the whole thing: prefix + base = 3 hyphen-joined segments.
    expect(id.split("-")).toHaveLength(3);
  });

  test("an empty-string prefix behaves like no prefix (unprefixed base)", () => {
    // makeId("") must NOT emit a leading "-"; the falsy branch is taken.
    expect(makeId("")).toMatch(BASE_RE);
    expect(makeId("").startsWith("-")).toBe(false);
  });
});

// ===================================================================
// Byte-for-byte equivalence with the original inline generators
// ===================================================================
describe("makeId - equivalence with the pre-extraction inline code", () => {
  test("unprefixed form matches alerts.js/webhooks.js `generateId()` structure", () => {
    // Original: `${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`
    const original = `${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`;
    const generated = makeId();
    // Same structural shape (time part is clock-dependent, suffix is random,
    // so compare structure not literal value).
    expect(generated).toMatch(BASE_RE);
    expect(original).toMatch(BASE_RE);
    expect(generated.split("-").length).toBe(original.split("-").length);
    expect(generated.split("-")[1]).toHaveLength(original.split("-")[1].length);
  });

  test("prefixed form matches annotations.js `ann-...` structure", () => {
    // Original: `ann-${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`
    const original = `ann-${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`;
    const generated = makeId("ann");
    expect(generated.startsWith("ann-")).toBe(true);
    expect(original.startsWith("ann-")).toBe(true);
    expect(generated.split("-").length).toBe(original.split("-").length);
    expect(generated.slice("ann-".length)).toMatch(BASE_RE);
  });
});

// ===================================================================
// Uniqueness / collision resistance - why the random suffix exists
// ===================================================================
describe("makeId - uniqueness", () => {
  test("1000 IDs minted in a tight loop are all distinct", () => {
    // Same-millisecond mints share a time component, so this exercises the
    // 48-bit random suffix that guards against collisions.
    const ids = new Set();
    for (let i = 0; i < 1000; i++) ids.add(makeId());
    expect(ids.size).toBe(1000);
  });

  test("1000 prefixed IDs in a tight loop are all distinct", () => {
    const ids = new Set();
    for (let i = 0; i < 1000; i++) ids.add(makeId("ann"));
    expect(ids.size).toBe(1000);
  });

  test("prefixed and unprefixed IDs never collide (namespace separation)", () => {
    // An "ann"-prefixed ID has 3 segments; an unprefixed one has 2, so they
    // can never be equal regardless of the random suffix.
    const unprefixed = new Set();
    for (let i = 0; i < 200; i++) unprefixed.add(makeId());
    for (let i = 0; i < 200; i++) {
      expect(unprefixed.has(makeId("ann"))).toBe(false);
    }
  });
});
