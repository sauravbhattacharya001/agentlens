/**
 * ChallengeReplayGuard — prevents CAPTCHA challenge replay attacks.
 *
 * Issues cryptographic tokens for each challenge serve and enforces
 * single-use consumption with configurable TTL. Tokens are HMAC-signed
 * to prevent forgery, and nonces prevent reuse.
 *
 * Features:
 *   - HMAC-SHA256 signed challenge tokens (server-side secret)
 *   - Configurable TTL with automatic expiry
 *   - One-time-use enforcement via nonce registry
 *   - LRU-bounded nonce storage (no unbounded memory growth)
 *   - Token introspection (decode without consuming)
 *   - Bulk token issuance for session flows
 *   - Statistics and monitoring
 *   - State export/import for persistence
 *
 * @module challenge-replay-guard
 */

"use strict";

var crypto = require("crypto");

/**
 * Create a ChallengeReplayGuard instance.
 *
 * @param {Object} [options]
 * @param {string} [options.secret] - HMAC signing secret (auto-generated if omitted)
 * @param {number} [options.ttlMs=120000] - Token time-to-live in ms (default 2 min)
 * @param {number} [options.maxNonces=50000] - Maximum stored nonces before LRU eviction
 * @param {string} [options.algorithm="sha256"] - HMAC algorithm
 * @param {number} [options.nonceBytes=16] - Random bytes for nonce generation
 * @param {boolean} [options.strictTiming=true] - Reject tokens with future issuedAt
 * @returns {Object} ReplayGuard instance
 */
function createChallengeReplayGuard(options) {
  options = options || {};

  var secret = options.secret || crypto.randomBytes(32).toString("hex");
  var ttlMs = (typeof options.ttlMs === "number" && options.ttlMs > 0) ? options.ttlMs : 120000;
  var maxNonces = (typeof options.maxNonces === "number" && options.maxNonces > 0)
    ? Math.floor(options.maxNonces) : 50000;
  var algorithm = options.algorithm || "sha256";
  var nonceBytes = (typeof options.nonceBytes === "number" && options.nonceBytes >= 8)
    ? Math.floor(options.nonceBytes) : 16;
  var strictTiming = options.strictTiming !== false;

  // Nonce registry: tracks consumed nonces to prevent reuse.
  // Uses a Map for O(1) operations + insertion-order iteration for LRU eviction.
  var _usedNonces = new Map();

  // Statistics
  var _stats = {
    tokensIssued: 0,
    tokensConsumed: 0,
    tokensRejected: 0,
    rejectionReasons: {
      invalid_format: 0,
      invalid_signature: 0,
      expired: 0,
      already_used: 0,
      future_timestamp: 0,
      challenge_mismatch: 0,
    },
  };

  /**
   * Generate a cryptographic nonce.
   * @returns {string} Hex-encoded nonce
   */
  function _generateNonce() {
    return crypto.randomBytes(nonceBytes).toString("hex");
  }

  /**
   * Compute HMAC signature for token payload.
   * @param {string} payload - Data to sign
   * @returns {string} Hex-encoded HMAC
   */
  function _sign(payload) {
    return crypto.createHmac(algorithm, secret).update(payload).digest("hex");
  }

  /**
   * Constant-time comparison to prevent timing attacks.
   * @param {string} a
   * @param {string} b
   * @returns {boolean}
   */
  function _safeEqual(a, b) {
    if (typeof a !== "string" || typeof b !== "string") return false;
    var bufA = Buffer.from(a, "utf8");
    var bufB = Buffer.from(b, "utf8");
    if (bufA.length !== bufB.length) return false;
    return crypto.timingSafeEqual(bufA, bufB);
  }

  /**
   * Evict oldest nonces when exceeding capacity.
   */
  function _evictNonces() {
    while (_usedNonces.size > maxNonces) {
      var oldest = _usedNonces.keys().next().value;
      _usedNonces.delete(oldest);
    }
  }

  /**
   * Purge expired nonces to reclaim memory.
   * Called periodically or on-demand.
   * @returns {number} Number of nonces purged
   */
  function purgeExpired() {
    var now = Date.now();
    var purged = 0;
    // Nonces older than 2x TTL are safe to remove
    // (even if token was issued at the nonce time, it would be expired)
    var cutoff = now - (ttlMs * 2);
    _usedNonces.forEach(function (timestamp, nonce) {
      if (timestamp < cutoff) {
        _usedNonces.delete(nonce);
        purged++;
      }
    });
    return purged;
  }

  /**
   * Issue a signed challenge token.
   *
   * @param {string} challengeId - The challenge this token authorizes
   * @param {Object} [meta] - Optional metadata to embed (userId, ip, etc.)
   * @returns {{ token: string, nonce: string, issuedAt: number, expiresAt: number }}
   */
  function issueToken(challengeId, meta) {
    if (!challengeId || typeof challengeId !== "string") {
      throw new Error("challengeId must be a non-empty string");
    }

    var nonce = _generateNonce();
    var issuedAt = Date.now();
    var expiresAt = issuedAt + ttlMs;

    // Payload: JSON-encoded to prevent delimiter injection (CVE-safe).
    // Previously used pipe-delimited format which was vulnerable to
    // challengeId containing pipes, causing field misalignment during parsing.
    var metaObj = meta || null;
    var payloadObj = {
      c: challengeId,
      n: nonce,
      i: issuedAt,
      e: expiresAt,
      m: metaObj
    };
    var payload = JSON.stringify(payloadObj);
    var signature = _sign(payload);

    // Token format: base64(payload).signature
    var encodedPayload = Buffer.from(payload, "utf8").toString("base64");
    var token = encodedPayload + "." + signature;

    _stats.tokensIssued++;

    return {
      token: token,
      nonce: nonce,
      issuedAt: issuedAt,
      expiresAt: expiresAt,
    };
  }

  /**
   * Issue tokens for multiple challenges at once (for session flows).
   *
   * @param {string[]} challengeIds - Array of challenge IDs
   * @param {Object} [meta] - Shared metadata for all tokens
   * @returns {Array<{ challengeId: string, token: string, nonce: string, issuedAt: number, expiresAt: number }>}
   */
  function issueBatch(challengeIds, meta) {
    if (!Array.isArray(challengeIds) || challengeIds.length === 0) {
      throw new Error("challengeIds must be a non-empty array");
    }
    return challengeIds.map(function (id) {
      var result = issueToken(id, meta);
      result.challengeId = id;
      return result;
    });
  }

  /**
   * Decode a token without consuming it (introspection).
   *
   * @param {string} token - The token string
   * @returns {{ valid: boolean, challengeId?: string, nonce?: string, issuedAt?: number, expiresAt?: number, meta?: Object, expired?: boolean, consumed?: boolean, error?: string }}
   */
  function introspect(token) {
    if (!token || typeof token !== "string") {
      return { valid: false, error: "invalid_format" };
    }

    var dotIndex = token.lastIndexOf(".");
    if (dotIndex === -1) {
      return { valid: false, error: "invalid_format" };
    }

    var encodedPayload = token.substring(0, dotIndex);
    var signature = token.substring(dotIndex + 1);

    var payload;
    try {
      payload = Buffer.from(encodedPayload, "base64").toString("utf8");
    } catch (e) {
      return { valid: false, error: "invalid_format" };
    }

    // Verify signature
    var expectedSig = _sign(payload);
    if (!_safeEqual(signature, expectedSig)) {
      return { valid: false, error: "invalid_signature" };
    }

    var payloadObj;
    try {
      payloadObj = JSON.parse(payload);
    } catch (e) {
      return { valid: false, error: "invalid_format" };
    }

    if (!payloadObj || !payloadObj.c || !payloadObj.n || !payloadObj.i || !payloadObj.e) {
      return { valid: false, error: "invalid_format" };
    }

    var challengeId = payloadObj.c;
    var nonce = payloadObj.n;
    var issuedAt = payloadObj.i;
    var expiresAt = payloadObj.e;
    var meta = payloadObj.m || null;

    var now = Date.now();
    var expired = now > expiresAt;
    var consumed = _usedNonces.has(nonce);

    return {
      valid: !expired && !consumed,
      challengeId: challengeId,
      nonce: nonce,
      issuedAt: issuedAt,
      expiresAt: expiresAt,
      meta: meta,
      expired: expired,
      consumed: consumed,
    };
  }

  /**
   * Consume (validate and mark as used) a challenge token.
   * Returns the validation result. On success, the nonce is registered
   * and cannot be reused.
   *
   * @param {string} token - The token to consume
   * @param {string} [expectedChallengeId] - If provided, must match the token's challengeId
   * @returns {{ valid: boolean, challengeId?: string, nonce?: string, meta?: Object, error?: string }}
   */
  function consume(token, expectedChallengeId) {
    if (!token || typeof token !== "string") {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_format++;
      return { valid: false, error: "invalid_format" };
    }

    var dotIndex = token.lastIndexOf(".");
    if (dotIndex === -1) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_format++;
      return { valid: false, error: "invalid_format" };
    }

    var encodedPayload = token.substring(0, dotIndex);
    var signature = token.substring(dotIndex + 1);

    var payload;
    try {
      payload = Buffer.from(encodedPayload, "base64").toString("utf8");
    } catch (e) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_format++;
      return { valid: false, error: "invalid_format" };
    }

    // Verify HMAC signature (constant-time)
    var expectedSig = _sign(payload);
    if (!_safeEqual(signature, expectedSig)) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_signature++;
      return { valid: false, error: "invalid_signature" };
    }

    var payloadObj;
    try {
      payloadObj = JSON.parse(payload);
    } catch (e) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_format++;
      return { valid: false, error: "invalid_format" };
    }

    if (!payloadObj || !payloadObj.c || !payloadObj.n || !payloadObj.i || !payloadObj.e) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.invalid_format++;
      return { valid: false, error: "invalid_format" };
    }

    var challengeId = payloadObj.c;
    var nonce = payloadObj.n;
    var issuedAt = payloadObj.i;
    var expiresAt = payloadObj.e;
    var meta = payloadObj.m || null;

    var now = Date.now();

    // Check future timestamp
    if (strictTiming && issuedAt > now + 5000) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.future_timestamp++;
      return { valid: false, error: "future_timestamp", challengeId: challengeId };
    }

    // Check expiry
    if (now > expiresAt) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.expired++;
      return { valid: false, error: "expired", challengeId: challengeId };
    }

    // Check challenge ID match
    if (expectedChallengeId && challengeId !== expectedChallengeId) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.challenge_mismatch++;
      return { valid: false, error: "challenge_mismatch", challengeId: challengeId };
    }

    // Check nonce reuse
    if (_usedNonces.has(nonce)) {
      _stats.tokensRejected++;
      _stats.rejectionReasons.already_used++;
      return { valid: false, error: "already_used", challengeId: challengeId };
    }

    // Mark nonce as used
    _usedNonces.set(nonce, now);
    _evictNonces();
    _stats.tokensConsumed++;

    return {
      valid: true,
      challengeId: challengeId,
      nonce: nonce,
      issuedAt: issuedAt,
      expiresAt: expiresAt,
      meta: meta,
    };
  }

  /**
   * Check if a nonce has been consumed.
   *
   * @param {string} nonce
   * @returns {boolean}
   */
  function isConsumed(nonce) {
    return _usedNonces.has(nonce);
  }

  /**
   * Get guard statistics.
   *
   * @returns {Object} Stats snapshot
   */
  function getStats() {
    return {
      tokensIssued: _stats.tokensIssued,
      tokensConsumed: _stats.tokensConsumed,
      tokensRejected: _stats.tokensRejected,
      activeNonces: _usedNonces.size,
      maxNonces: maxNonces,
      rejectionReasons: Object.assign({}, _stats.rejectionReasons),
      rejectRate: _stats.tokensIssued > 0
        ? Math.round((_stats.tokensRejected / _stats.tokensIssued) * 1000) / 1000
        : 0,
      consumeRate: _stats.tokensIssued > 0
        ? Math.round((_stats.tokensConsumed / _stats.tokensIssued) * 1000) / 1000
        : 0,
    };
  }

  /**
   * Get guard configuration.
   * @returns {Object}
   */
  function getConfig() {
    return {
      ttlMs: ttlMs,
      maxNonces: maxNonces,
      algorithm: algorithm,
      nonceBytes: nonceBytes,
      strictTiming: strictTiming,
    };
  }

  /**
   * Export state for persistence (nonce registry + stats).
   * @returns {Object} Serializable state
   */
  function exportState() {
    var nonces = [];
    _usedNonces.forEach(function (timestamp, nonce) {
      nonces.push({ nonce: nonce, consumedAt: timestamp });
    });
    return {
      nonces: nonces,
      stats: {
        tokensIssued: _stats.tokensIssued,
        tokensConsumed: _stats.tokensConsumed,
        tokensRejected: _stats.tokensRejected,
        rejectionReasons: Object.assign({}, _stats.rejectionReasons),
      },
      exportedAt: Date.now(),
    };
  }

  /**
   * Import previously exported state.
   * @param {Object} state
   * @returns {number} Number of nonces restored
   */
  function importState(state) {
    if (!state || typeof state !== "object") {
      throw new Error("state must be an object");
    }
    var restored = 0;
    if (Array.isArray(state.nonces)) {
      var now = Date.now();
      var cutoff = now - (ttlMs * 2);
      state.nonces.forEach(function (entry) {
        // Only import nonces that haven't expired
        if (entry.consumedAt >= cutoff) {
          _usedNonces.set(entry.nonce, entry.consumedAt);
          restored++;
        }
      });
      _evictNonces();
    }
    if (state.stats) {
      _stats.tokensIssued += (state.stats.tokensIssued || 0);
      _stats.tokensConsumed += (state.stats.tokensConsumed || 0);
      _stats.tokensRejected += (state.stats.tokensRejected || 0);
      if (state.stats.rejectionReasons) {
        Object.keys(state.stats.rejectionReasons).forEach(function (k) {
          _stats.rejectionReasons[k] = (_stats.rejectionReasons[k] || 0) + state.stats.rejectionReasons[k];
        });
      }
    }
    return restored;
  }

  /**
   * Reset all state (nonces + stats).
   */
  function reset() {
    _usedNonces.clear();
    _stats.tokensIssued = 0;
    _stats.tokensConsumed = 0;
    _stats.tokensRejected = 0;
    Object.keys(_stats.rejectionReasons).forEach(function (k) {
      _stats.rejectionReasons[k] = 0;
    });
  }

  /**
   * Generate a summary report.
   * @returns {Object}
   */
  function generateReport() {
    var stats = getStats();
    var issues = [];
    if (stats.rejectRate > 0.5) {
      issues.push({
        severity: "warning",
        message: "High rejection rate (" + (stats.rejectRate * 100).toFixed(1) + "%) — possible attack or misconfiguration",
      });
    }
    if (stats.rejectionReasons.already_used > stats.tokensConsumed * 0.1) {
      issues.push({
        severity: "warning",
        message: "Significant replay attempts detected (" + stats.rejectionReasons.already_used + " blocked)",
      });
    }
    if (stats.rejectionReasons.invalid_signature > 0) {
      issues.push({
        severity: "error",
        message: stats.rejectionReasons.invalid_signature + " forged token attempt(s) detected",
      });
    }
    if (stats.activeNonces > maxNonces * 0.9) {
      issues.push({
        severity: "info",
        message: "Nonce registry near capacity (" + stats.activeNonces + "/" + maxNonces + ")",
      });
    }

    return {
      stats: stats,
      config: getConfig(),
      issues: issues,
      health: issues.some(function (i) { return i.severity === "error"; }) ? "degraded"
        : issues.some(function (i) { return i.severity === "warning"; }) ? "caution"
        : "healthy",
    };
  }

  return {
    issueToken: issueToken,
    issueBatch: issueBatch,
    consume: consume,
    introspect: introspect,
    isConsumed: isConsumed,
    purgeExpired: purgeExpired,
    getStats: getStats,
    getConfig: getConfig,
    generateReport: generateReport,
    exportState: exportState,
    importState: importState,
    reset: reset,
  };
}

module.exports = { createChallengeReplayGuard: createChallengeReplayGuard };
