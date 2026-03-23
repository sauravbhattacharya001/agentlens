/**
 * Shared pagination and request-handling helpers.
 *
 * Eliminates duplicated Math.min(Math.max(...)) pagination parsing,
 * session-ID validation guards, and try/catch error response blocks
 * that were copy-pasted across every route file.
 */

const { isValidSessionId } = require("./validation");

// ── Pagination ──────────────────────────────────────────────────────

/**
 * Parse and clamp a pagination `limit` query parameter.
 *
 * @param {string|number} raw   – the raw query value (req.query.limit)
 * @param {number}        [def=50]  – default when missing/NaN
 * @param {number}        [max=200] – hard upper bound
 * @param {number}        [min=1]   – hard lower bound
 * @returns {number} clamped integer
 */
function parseLimit(raw, def, max, min) {
  if (def === undefined) def = 50;
  if (max === undefined) max = 200;
  if (min === undefined) min = 1;
  var n = parseInt(raw);
  if (!Number.isFinite(n) || n === 0) return def;
  return Math.min(Math.max(n, min), max);
}

/**
 * Parse and clamp a pagination `offset` query parameter.
 *
 * @param {string|number} raw – the raw query value (req.query.offset)
 * @returns {number} non-negative integer
 */
function parseOffset(raw) {
  var n = parseInt(raw);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * Extract { limit, offset } from a request's query string in one call.
 * Accepts optional defaults and caps for limit.
 *
 * @param {Object} query           – express req.query
 * @param {{ defaultLimit?: number, maxLimit?: number, minLimit?: number }} [opts]
 * @returns {{ limit: number, offset: number }}
 */
function parsePagination(query, opts) {
  opts = opts || {};
  return {
    limit: parseLimit(query.limit, opts.defaultLimit, opts.maxLimit, opts.minLimit),
    offset: parseOffset(query.offset),
  };
}

// ── Request guards ──────────────────────────────────────────────────

/**
 * Express middleware that validates `req.params.id` as a session ID.
 * Sends a 400 response and short-circuits if invalid.
 *
 * Usage:
 *   router.get("/:id", requireSessionId, (req, res) => { ... });
 */
function requireSessionId(req, res, next) {
  if (!isValidSessionId(req.params.id)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }
  next();
}

// ── Async route wrapper ─────────────────────────────────────────────

/**
 * Wrap an Express route handler so any thrown/rejected error is caught
 * and returned as a 500 JSON response.  Eliminates per-route try/catch.
 *
 * @param {string} label – human-readable action for the log line
 * @param {Function} fn  – (req, res) => any | Promise
 * @returns {Function} Express-compatible handler
 *
 * Usage:
 *   router.get("/foo", wrapRoute("fetch foo", (req, res) => { ... }));
 */
function wrapRoute(label, fn) {
  return function (req, res, next) {
    try {
      var result = fn(req, res, next);
      // Handle both sync and async handlers
      if (result && typeof result.catch === "function") {
        result.catch(function (err) {
          console.error("Error " + label + ":", err);
          if (!res.headersSent) {
            res.status(500).json({ error: "Failed to " + label });
          }
        });
      }
    } catch (err) {
      console.error("Error " + label + ":", err);
      if (!res.headersSent) {
        res.status(500).json({ error: "Failed to " + label });
      }
    }
  };
}

// ── Date helpers ────────────────────────────────────────────────────

/**
 * Parse and clamp a `days` query parameter (1–365, default 30).
 *
 * @param {string|number} raw – the raw query value (req.query.days)
 * @param {number}        [def=30]  – default when missing/NaN
 * @param {number}        [max=365] – hard upper bound
 * @returns {number} clamped integer
 */
function parseDays(raw, def, max) {
  if (def === undefined) def = 30;
  if (max === undefined) max = 365;
  return Math.min(Math.max(1, parseInt(raw) || def), max);
}

/**
 * Return an ISO-8601 timestamp representing `days` days ago from now.
 * Convenience for the common `new Date(Date.now() - days * 86400000).toISOString()` pattern.
 *
 * @param {number} days – number of days to look back
 * @returns {string} ISO timestamp
 */
function daysAgoCutoff(days) {
  return new Date(Date.now() - days * 86400000).toISOString();
}

module.exports = {
  parseLimit,
  parseOffset,
  parsePagination,
  parseDays,
  daysAgoCutoff,
  requireSessionId,
  wrapRoute,
};
