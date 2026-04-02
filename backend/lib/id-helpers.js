/**
 * Shared ID generation and validation helpers.
 *
 * Consolidates the identical generateId() and RESOURCE_ID_RE patterns
 * that were duplicated across alerts.js, webhooks.js, and annotations.js.
 */

const crypto = require("crypto");

// IDs are generated via `Date.now().toString(36)-<12 hex chars>`, so
// they only contain alphanumeric characters and hyphens.  Reject
// anything else early to prevent SQL injection or parameter confusion.
const RESOURCE_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$/;

/**
 * Generate a unique ID: base-36 timestamp + 6 random bytes (hex).
 *
 * @param {string} [prefix] – optional prefix (e.g. "ann") prepended with a hyphen
 * @returns {string}
 */
function generateId(prefix) {
  const id = `${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`;
  return prefix ? `${prefix}-${id}` : id;
}

/**
 * Validate a resource ID (alert, webhook, annotation, etc.).
 *
 * @param {string} id
 * @returns {boolean}
 */
function isValidResourceId(id) {
  return typeof id === "string" && RESOURCE_ID_RE.test(id);
}

/**
 * Express middleware factory: validate a named path parameter as a resource ID.
 * Returns 400 if invalid, otherwise calls next().
 *
 * @param {string} paramName – the path parameter name to validate
 * @returns {Function} Express middleware
 */
function validateIdParam(paramName) {
  return (req, res, next) => {
    const val = req.params[paramName];
    if (!isValidResourceId(val)) {
      return res.status(400).json({ error: `Invalid ${paramName} format` });
    }
    next();
  };
}

module.exports = { generateId, isValidResourceId, validateIdParam, RESOURCE_ID_RE };
