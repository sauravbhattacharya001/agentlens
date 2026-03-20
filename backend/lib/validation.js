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
const MAX_TAG_LENGTH = 64;
const MAX_TAGS_PER_SESSION = 20;
const TAG_RE = /^[a-zA-Z0-9_\-.:/ ]+$/;
const SESSION_ID_RE = /^[a-zA-Z0-9_\-.:]+$/;

const VALID_EVENT_TYPES = new Set([
  "session_start",
  "session_end",
  "llm_call",
  "tool_call",
  "tool_error",
  "agent_call",
  "agent_error",
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

/**
 * Validate a tag string: alphanumeric + `_-.:/ `, max 64 chars, trimmed.
 *
 * @param {*} tag
 * @returns {string|null} – cleaned tag or null if invalid
 */
function validateTag(tag) {
  if (!tag || typeof tag !== "string") return null;
  const trimmed = tag.trim().slice(0, MAX_TAG_LENGTH);
  if (trimmed.length === 0) return null;
  return TAG_RE.test(trimmed) ? trimmed : null;
}

/**
 * Validate an array of tags. Returns an array of valid tags (deduped)
 * or null if the input is invalid.
 *
 * @param {*} tags
 * @returns {string[]|null}
 */
function validateTags(tags) {
  if (!Array.isArray(tags)) return null;
  const valid = [];
  const seen = new Set();
  for (const t of tags) {
    const cleaned = validateTag(t);
    if (cleaned && !seen.has(cleaned)) {
      seen.add(cleaned);
      valid.push(cleaned);
    }
  }
  if (valid.length > MAX_TAGS_PER_SESSION) return null;
  return valid.length > 0 ? valid : null;
}

/**
 * Validate a webhook URL: must be a valid https (or http) URL pointing
 * to a public, non-internal host.  Blocks SSRF attacks against cloud
 * metadata endpoints, loopback addresses, and private RFC-1918 ranges.
 *
 * @param {string} url
 * @returns {{ valid: boolean, error?: string }}
 */
function validateWebhookUrl(url) {
  if (!url || typeof url !== "string") {
    return { valid: false, error: "url is required" };
  }

  // Reject URLs with embedded credentials (user:pass@host)
  if (url.match(/:\/\/[^/]*@/)) {
    return { valid: false, error: "url must not contain embedded credentials" };
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { valid: false, error: "url must be a valid URL" };
  }

  // Enforce HTTPS in production to prevent leaking webhook secrets
  // and HMAC signatures over plaintext HTTP connections.
  // HTTP is allowed only in development (NODE_ENV !== "production").
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { valid: false, error: "url must use http or https protocol" };
  }
  if (parsed.protocol === "http:" && process.env.NODE_ENV === "production") {
    return { valid: false, error: "url must use https in production (webhook secrets and HMAC signatures must not be sent over plaintext)" };
  }

  const hostname = parsed.hostname.toLowerCase();

  // Block loopback
  if (
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "::1" ||
    hostname === "[::1]" ||
    hostname === "0.0.0.0"
  ) {
    return { valid: false, error: "url must not point to a loopback address" };
  }

  // Block IPv6-mapped IPv4 addresses (::ffff:127.0.0.1, ::ffff:10.x.x.x, etc.)
  // These bypass IPv4-only checks while resolving to the same destinations.
  const bare = hostname.replace(/^\[|\]$/g, "");
  if (bare.startsWith("::ffff:")) {
    return { valid: false, error: "url must not use IPv6-mapped IPv4 addresses" };
  }

  // Block non-standard IP representations that bypass regex checks:
  // decimal (2130706433 = 127.0.0.1), octal (0177.0.0.1), hex (0x7f.0.0.1)
  if (/^(0x[0-9a-f]+|0[0-7]+|\d{5,})$/i.test(hostname)) {
    return { valid: false, error: "url must use standard dotted-decimal IP notation" };
  }
  // Octal octets (e.g., 0177.0.0.01)
  const octets = hostname.split(".");
  if (octets.length === 4 && octets.every(o => /^\d+$/.test(o) || /^0[0-7]+$/.test(o) || /^0x[0-9a-f]+$/i.test(o))) {
    if (octets.some(o => /^0\d/.test(o) || /^0x/i.test(o))) {
      return { valid: false, error: "url must use standard dotted-decimal IP notation" };
    }
  }

  // Block link-local / metadata (169.254.x.x — AWS/GCP/Azure metadata)
  if (hostname === "169.254.169.254" || hostname.startsWith("169.254.")) {
    return { valid: false, error: "url must not point to a cloud metadata endpoint" };
  }

  // Block private RFC-1918 ranges
  const ipv4Match = hostname.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (ipv4Match) {
    const [, a, b] = ipv4Match.map(Number);
    if (
      a === 10 ||                           // 10.0.0.0/8
      (a === 172 && b >= 16 && b <= 31) ||  // 172.16.0.0/12
      (a === 192 && b === 168)              // 192.168.0.0/16
    ) {
      return { valid: false, error: "url must not point to a private network address" };
    }
  }

  // Block common internal service hostnames
  const blockedHostnames = [
    "metadata.google.internal",
    "metadata.google",
    "kubernetes.default",
    "kubernetes.default.svc",
  ];
  if (blockedHostnames.includes(hostname)) {
    return { valid: false, error: "url must not point to an internal service" };
  }

  return { valid: true };
}

/**
 * Escape SQL LIKE wildcard characters in a user-provided search term.
 * Prevents `%` and `_` in user input from acting as wildcards, which
 * can cause unexpected full-table matches and information disclosure.
 *
 * @param {string} term – raw search term
 * @returns {string} escaped term safe for LIKE patterns
 */
function escapeLikeWildcards(term) {
  if (typeof term !== "string") return "";
  return term.replace(/[%_\\]/g, "\\$&");
}

module.exports = {
  // Constants
  MAX_BATCH_SIZE,
  MAX_STRING_LENGTH,
  MAX_DATA_LENGTH,
  MAX_TAG_LENGTH,
  MAX_TAGS_PER_SESSION,
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
  validateTag,
  validateTags,
  validateWebhookUrl,
  escapeLikeWildcards,
};
