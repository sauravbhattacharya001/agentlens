/**
 * Tests for backend/lib/validation.js — input validation and sanitization.
 *
 * Covers all exported functions: sanitizeString, validateSessionId,
 * isValidSessionId, safeJsonStringify, safeJsonParse, clampNonNegInt,
 * clampNonNegFloat, isValidStatus, isValidEventType, and constants.
 */

const {
  MAX_BATCH_SIZE,
  MAX_STRING_LENGTH,
  MAX_DATA_LENGTH,
  VALID_EVENT_TYPES,
  VALID_SESSION_STATUSES,
  sanitizeString,
  validateSessionId,
  isValidSessionId,
  safeJsonStringify,
  safeJsonParse,
  clampNonNegInt,
  clampNonNegFloat,
  isValidStatus,
  isValidEventType,
} = require("../lib/validation");

/* ================================================================
 * Constants
 * ================================================================ */
describe("constants", () => {
  test("MAX_BATCH_SIZE is 500", () => {
    expect(MAX_BATCH_SIZE).toBe(500);
  });

  test("MAX_STRING_LENGTH is 1024", () => {
    expect(MAX_STRING_LENGTH).toBe(1024);
  });

  test("MAX_DATA_LENGTH is 256 KB", () => {
    expect(MAX_DATA_LENGTH).toBe(1024 * 256);
  });

  test("VALID_EVENT_TYPES contains expected types", () => {
    const expected = [
      "session_start", "session_end", "llm_call", "tool_call",
      "tool_error", "agent_call", "agent_error", "error", "generic",
    ];
    for (const type of expected) {
      expect(VALID_EVENT_TYPES.has(type)).toBe(true);
    }
    expect(VALID_EVENT_TYPES.size).toBe(expected.length);
  });

  test("VALID_SESSION_STATUSES contains expected statuses", () => {
    const expected = ["active", "completed", "error", "timeout"];
    for (const status of expected) {
      expect(VALID_SESSION_STATUSES.has(status)).toBe(true);
    }
    expect(VALID_SESSION_STATUSES.size).toBe(expected.length);
  });
});

/* ================================================================
 * sanitizeString
 * ================================================================ */
describe("sanitizeString", () => {
  test("returns null for non-string input", () => {
    expect(sanitizeString(42)).toBeNull();
    expect(sanitizeString(null)).toBeNull();
    expect(sanitizeString(undefined)).toBeNull();
    expect(sanitizeString({})).toBeNull();
    expect(sanitizeString([])).toBeNull();
    expect(sanitizeString(true)).toBeNull();
  });

  test("returns empty string unchanged", () => {
    expect(sanitizeString("")).toBe("");
  });

  test("passes through normal strings", () => {
    expect(sanitizeString("hello world")).toBe("hello world");
    expect(sanitizeString("gpt-4o")).toBe("gpt-4o");
  });

  test("strips control characters", () => {
    expect(sanitizeString("hello\x00world")).toBe("helloworld");
    expect(sanitizeString("test\x07bell")).toBe("testbell");
    expect(sanitizeString("null\x00byte")).toBe("nullbyte");
    expect(sanitizeString("del\x7Fchar")).toBe("delchar");
  });

  test("preserves newlines and tabs (not control chars in the stripped set)", () => {
    // \x09 (tab), \x0A (LF), \x0D (CR) are NOT in the stripped range
    expect(sanitizeString("line1\nline2")).toBe("line1\nline2");
    expect(sanitizeString("col1\tcol2")).toBe("col1\tcol2");
  });

  test("truncates to default max length (1024)", () => {
    const long = "a".repeat(2000);
    const result = sanitizeString(long);
    expect(result.length).toBe(1024);
  });

  test("truncates to custom max length", () => {
    const long = "b".repeat(100);
    const result = sanitizeString(long, 50);
    expect(result.length).toBe(50);
  });

  test("does not truncate strings under the limit", () => {
    const short = "x".repeat(100);
    expect(sanitizeString(short, 200)).toBe(short);
  });

  test("handles combined strip + truncate", () => {
    const input = "\x00" + "a".repeat(2000) + "\x01";
    const result = sanitizeString(input, 100);
    expect(result.length).toBe(100);
    expect(result).not.toContain("\x00");
    expect(result).not.toContain("\x01");
  });
});

/* ================================================================
 * validateSessionId
 * ================================================================ */
describe("validateSessionId", () => {
  test("returns null for empty/missing input", () => {
    expect(validateSessionId("")).toBeNull();
    expect(validateSessionId(null)).toBeNull();
    expect(validateSessionId(undefined)).toBeNull();
    expect(validateSessionId(42)).toBeNull();
  });

  test("accepts valid session IDs", () => {
    expect(validateSessionId("abc123")).toBe("abc123");
    expect(validateSessionId("session_1")).toBe("session_1");
    expect(validateSessionId("sess-2024-01-01")).toBe("sess-2024-01-01");
    expect(validateSessionId("a.b.c")).toBe("a.b.c");
    expect(validateSessionId("ns:scope:id")).toBe("ns:scope:id");
    expect(validateSessionId("MixedCase_123")).toBe("MixedCase_123");
  });

  test("rejects IDs with invalid characters", () => {
    expect(validateSessionId("has space")).toBeNull();
    expect(validateSessionId("has/slash")).toBeNull();
    expect(validateSessionId("has@sign")).toBeNull();
    expect(validateSessionId("has#hash")).toBeNull();
    expect(validateSessionId("has$dollar")).toBeNull();
    expect(validateSessionId("has%percent")).toBeNull();
  });

  test("truncates to 128 characters before validation", () => {
    const long = "a".repeat(200);
    const result = validateSessionId(long);
    expect(result).toBe("a".repeat(128));
  });

  test("rejects after truncation if invalid chars appear early", () => {
    // Put an invalid char within first 128 chars
    const input = "a".repeat(50) + "!" + "b".repeat(100);
    expect(validateSessionId(input)).toBeNull();
  });
});

/* ================================================================
 * isValidSessionId
 * ================================================================ */
describe("isValidSessionId", () => {
  test("returns true for valid IDs", () => {
    expect(isValidSessionId("abc")).toBe(true);
    expect(isValidSessionId("a_b-c.d:e")).toBe(true);
  });

  test("returns false for non-string input", () => {
    expect(isValidSessionId(123)).toBe(false);
    expect(isValidSessionId(null)).toBe(false);
    expect(isValidSessionId(undefined)).toBe(false);
  });

  test("returns false for strings exceeding 128 chars", () => {
    expect(isValidSessionId("a".repeat(129))).toBe(false);
    expect(isValidSessionId("a".repeat(128))).toBe(true);
  });

  test("returns false for invalid characters", () => {
    expect(isValidSessionId("space here")).toBe(false);
    expect(isValidSessionId("excl!")).toBe(false);
  });
});

/* ================================================================
 * safeJsonStringify
 * ================================================================ */
describe("safeJsonStringify", () => {
  test("returns null for null/undefined", () => {
    expect(safeJsonStringify(null)).toBeNull();
    expect(safeJsonStringify(undefined)).toBeNull();
  });

  test("stringifies normal objects", () => {
    const obj = { key: "value", num: 42 };
    expect(safeJsonStringify(obj)).toBe(JSON.stringify(obj));
  });

  test("stringifies arrays", () => {
    expect(safeJsonStringify([1, 2, 3])).toBe("[1,2,3]");
  });

  test("stringifies strings", () => {
    expect(safeJsonStringify("hello")).toBe('"hello"');
  });

  test("stringifies numbers", () => {
    expect(safeJsonStringify(42)).toBe("42");
  });

  test("truncates oversized data", () => {
    const big = { data: "x".repeat(MAX_DATA_LENGTH + 100) };
    const result = safeJsonStringify(big);
    const parsed = JSON.parse(result);
    expect(parsed._truncated).toBe(true);
    expect(parsed._original_size).toBeGreaterThan(MAX_DATA_LENGTH);
  });

  test("respects custom maxLen", () => {
    const obj = { data: "x".repeat(200) };
    const result = safeJsonStringify(obj, 100);
    const parsed = JSON.parse(result);
    expect(parsed._truncated).toBe(true);
  });

  test("handles circular references gracefully", () => {
    const circular = {};
    circular.self = circular;
    expect(safeJsonStringify(circular)).toBeNull();
  });

  test("does not truncate data within limit", () => {
    const small = { ok: true };
    const result = safeJsonStringify(small);
    expect(JSON.parse(result)).toEqual(small);
  });
});

/* ================================================================
 * safeJsonParse
 * ================================================================ */
describe("safeJsonParse", () => {
  test("parses valid JSON", () => {
    expect(safeJsonParse('{"a":1}')).toEqual({ a: 1 });
    expect(safeJsonParse("[1,2]")).toEqual([1, 2]);
    expect(safeJsonParse('"hello"')).toBe("hello");
  });

  test("returns fallback for null/undefined", () => {
    expect(safeJsonParse(null)).toEqual({});
    expect(safeJsonParse(undefined)).toEqual({});
  });

  test("returns custom fallback on failure", () => {
    expect(safeJsonParse("invalid", [])).toEqual([]);
    expect(safeJsonParse("invalid", null)).toBeNull();
    expect(safeJsonParse("invalid", "default")).toBe("default");
  });

  test("returns default fallback for invalid JSON", () => {
    expect(safeJsonParse("not json")).toEqual({});
    expect(safeJsonParse("{broken")).toEqual({});
  });

  test("returns already-parsed objects unchanged", () => {
    const obj = { already: "parsed" };
    expect(safeJsonParse(obj)).toBe(obj); // same reference
  });

  test("returns already-parsed arrays unchanged", () => {
    const arr = [1, 2, 3];
    expect(safeJsonParse(arr)).toBe(arr);
  });

  test("returns already-parsed numbers unchanged", () => {
    expect(safeJsonParse(42)).toBe(42);
  });
});

/* ================================================================
 * clampNonNegInt
 * ================================================================ */
describe("clampNonNegInt", () => {
  test("passes through valid non-negative integers", () => {
    expect(clampNonNegInt(0)).toBe(0);
    expect(clampNonNegInt(42)).toBe(42);
    expect(clampNonNegInt(1000000)).toBe(1000000);
  });

  test("floors floating point numbers", () => {
    expect(clampNonNegInt(3.7)).toBe(3);
    expect(clampNonNegInt(0.99)).toBe(0);
    expect(clampNonNegInt(100.001)).toBe(100);
  });

  test("clamps negative numbers to 0", () => {
    expect(clampNonNegInt(-1)).toBe(0);
    expect(clampNonNegInt(-100)).toBe(0);
    expect(clampNonNegInt(-0.5)).toBe(0);
  });

  test("returns 0 for non-numeric input", () => {
    expect(clampNonNegInt("hello")).toBe(0);
    expect(clampNonNegInt(null)).toBe(0);
    expect(clampNonNegInt(undefined)).toBe(0);
    expect(clampNonNegInt(NaN)).toBe(0);
    expect(clampNonNegInt(Infinity)).toBe(0);
    expect(clampNonNegInt(-Infinity)).toBe(0);
    expect(clampNonNegInt({})).toBe(0);
  });
});

/* ================================================================
 * clampNonNegFloat
 * ================================================================ */
describe("clampNonNegFloat", () => {
  test("passes through valid non-negative floats", () => {
    expect(clampNonNegFloat(0)).toBe(0);
    expect(clampNonNegFloat(3.14)).toBe(3.14);
    expect(clampNonNegFloat(0.001)).toBe(0.001);
  });

  test("clamps negative numbers to 0", () => {
    expect(clampNonNegFloat(-1.5)).toBe(0);
    expect(clampNonNegFloat(-0.001)).toBe(0);
  });

  test("returns null for non-numeric input", () => {
    expect(clampNonNegFloat("hello")).toBeNull();
    expect(clampNonNegFloat(null)).toBeNull();
    expect(clampNonNegFloat(undefined)).toBeNull();
    expect(clampNonNegFloat(NaN)).toBeNull();
    expect(clampNonNegFloat(Infinity)).toBeNull();
    expect(clampNonNegFloat(-Infinity)).toBeNull();
    expect(clampNonNegFloat({})).toBeNull();
  });
});

/* ================================================================
 * isValidStatus
 * ================================================================ */
describe("isValidStatus", () => {
  test("returns true for valid statuses", () => {
    expect(isValidStatus("active")).toBe(true);
    expect(isValidStatus("completed")).toBe(true);
    expect(isValidStatus("error")).toBe(true);
    expect(isValidStatus("timeout")).toBe(true);
  });

  test("returns false for invalid statuses", () => {
    expect(isValidStatus("running")).toBe(false);
    expect(isValidStatus("Active")).toBe(false); // case-sensitive
    expect(isValidStatus("")).toBe(false);
    expect(isValidStatus("pending")).toBe(false);
  });
});

/* ================================================================
 * isValidEventType
 * ================================================================ */
describe("isValidEventType", () => {
  test("returns true for valid event types", () => {
    expect(isValidEventType("llm_call")).toBe(true);
    expect(isValidEventType("tool_call")).toBe(true);
    expect(isValidEventType("session_start")).toBe(true);
    expect(isValidEventType("session_end")).toBe(true);
    expect(isValidEventType("agent_call")).toBe(true);
    expect(isValidEventType("error")).toBe(true);
    expect(isValidEventType("generic")).toBe(true);
    expect(isValidEventType("tool_error")).toBe(true);
    expect(isValidEventType("agent_error")).toBe(true);
  });

  test("returns false for invalid event types", () => {
    expect(isValidEventType("unknown")).toBe(false);
    expect(isValidEventType("LLM_CALL")).toBe(false); // case-sensitive
    expect(isValidEventType("")).toBe(false);
    expect(isValidEventType("custom_event")).toBe(false);
  });
});
