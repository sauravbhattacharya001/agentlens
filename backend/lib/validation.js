/**
 * Shared input validation and sanitization utilities.
 *
 * Centralizes all user-input validation so that every route uses the same
 * rules.  Adding a new endpoint?  Import from here instead of duplicating
 * validation logic.
 */

// ── Constants ───────────────────────────────────────────────────────
const MAX_BATCH_SIZE = 500;
const MAX_STRING_LENGTH = 1024;
const MAX_DATA_LENGTH = 1024 * 256; // 256 KB
const SESSION_ID_RE = /^[a-zA-Z0-9_\-.:]+$/;

const VALID_EVENT_TYPES = new Set([
  "session_start",
  "session_end",
  "llm_call",
  "tool_call",
  "agent_call",
  "error",
  "generic",
]);

const VALID_SESSION_STATUSES = new Set([
  "active",
  "completed",
  "error",
  "timeout",
]);

// ── Helpers ─────────────────────────────────────────────────────────

/**
 * Strip control characters and enforce a maximum length.
 *
 * @param {*}      val    – value to sanitize (non-strings return null)
 * @param {number} maxLen – maximum allowed length (default 1024)
 * @returns {string|null}
 */
function sanitizeString(val, maxLen = MAX_STRING_LENGTH) {
  if (typeof val !== "string") return null;
  const cleaned = val.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
  return cleaned.slice(0, maxLen);
}

/**
 * Validate and sanitize a session ID (alphanumeric + `_-.:`, max 128 chars).
 *
 * @param {*} id
 * @returns {string|null}
 */
function validateSessionId(id) {
  if (!id || typeof id !== "string") return null;
  const trimmed = id.slice(0, 128);
  return SESSION_ID_RE.test(trimmed) ? trimmed : null;
}

/**
 * Check whether a session ID string is structurally valid.
 *
 * @param {*} id
 * @returns {boolean}
 */
function isValidSessionId(id) {
  return typeof id === "string" && id.length <= 128 && SESSION_ID_RE.test(id);
}

/**
 * Safely JSON-stringify a value, returning a truncation stub when the
 * serialized size exceeds `maxLen`.
 *
 * @param {*}      data
 * @param {number} maxLen – hard cap in characters (default 256 KB)
 * @returns {string|null}
 */
function safeJsonStringify(data, maxLen = MAX_DATA_LENGTH) {
  if (data == null) return null;
  try {
    const str = JSON.stringify(data);
    if (str.length > maxLen) {
      return JSON.stringify({ _truncated: true, _original_size: str.length });
    }
    return str;
  } catch {
    return null;
  }
}

/**
 * Safely parse a JSON string, returning `fallback` on failure.
 *
 * @param {*}   str
 * @param {*}   fallback – returned when parsing fails (default `{}`)
 * @returns {*}
 */
function safeJsonParse(str, fallback = {}) {
  if (str == null) return fallback;
  if (typeof str !== "string") return str; // already parsed
  try {
    return JSON.parse(str);
  } catch {
    return fallback;
  }
}

/**
 * Clamp a value that should be a finite non-negative integer.
 * Returns 0 for non-numeric / negative input.
 *
 * @param {*} val
 * @returns {number}
 */
function clampNonNegInt(val) {
  return Number.isFinite(val) ? Math.max(0, Math.floor(val)) : 0;
}

/**
 * Clamp a value that should be a finite non-negative float.
 * Returns `null` for non-numeric input.
 *
 * @param {*} val
 * @returns {number|null}
 */
function clampNonNegFloat(val) {
  return Number.isFinite(val) ? Math.max(0, val) : null;
}

/**
 * Validate that a status string belongs to the known set.
 *
 * @param {string} status
 * @returns {boolean}
 */
function isValidStatus(status) {
  return VALID_SESSION_STATUSES.has(status);
}

/**
 * Validate that an event type string belongs to the known set.
 *
 * @param {string} type
 * @returns {boolean}
 */
function isValidEventType(type) {
  return VALID_EVENT_TYPES.has(type);
}

module.exports = {
  // Constants
  MAX_BATCH_SIZE,
  MAX_STRING_LENGTH,
  MAX_DATA_LENGTH,
  VALID_EVENT_TYPES,
  VALID_SESSION_STATUSES,
  // Helpers
  sanitizeString,
  validateSessionId,
  isValidSessionId,
  safeJsonStringify,
  safeJsonParse,
  clampNonNegInt,
  clampNonNegFloat,
  isValidStatus,
  isValidEventType,
};
