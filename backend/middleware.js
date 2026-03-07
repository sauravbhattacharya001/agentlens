/**
 * Express middleware configuration.
 *
 * Keeps security headers, CORS, rate-limiting, and API-key auth in one
 * place so that `server.js` stays focused on application wiring.
 */

const helmet = require("helmet");
const cors = require("cors");
const rateLimit = require("express-rate-limit");
const crypto = require("crypto");

// ── Helmet (security headers) ───────────────────────────────────────

function createHelmetMiddleware() {
  return helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'", "'unsafe-inline'"],
        styleSrc: ["'self'", "'unsafe-inline'"],
        imgSrc: ["'self'", "data:"],
        connectSrc: ["'self'"],
        fontSrc: ["'self'"],
        objectSrc: ["'none'"],
        frameAncestors: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
        upgradeInsecureRequests: [],
      },
    },
    crossOriginEmbedderPolicy: false,
    referrerPolicy: { policy: "strict-origin-when-cross-origin" },
    permissionsPolicy: {
      features: {
        camera: [],
        microphone: [],
        geolocation: [],
        payment: [],
        usb: [],
        magnetometer: [],
        gyroscope: [],
        accelerometer: [],
      },
    },
  });
}

// ── CORS ─────────────────────────────────────────────────────────────

function createCorsMiddleware() {
  const ALLOWED_ORIGINS = process.env.CORS_ORIGINS
    ? process.env.CORS_ORIGINS.split(",").map((o) => o.trim())
    : null;

  return cors({
    origin: ALLOWED_ORIGINS || true,
    methods: ["GET", "POST", "PUT", "DELETE"],
    allowedHeaders: ["Content-Type", "X-API-Key"],
    maxAge: 86400,
  });
}

// ── Rate limiters ───────────────────────────────────────────────────

function createApiLimiter() {
  return rateLimit({
    windowMs: 60 * 1000,
    max: 120,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many requests, please try again later" },
  });
}

function createIngestLimiter() {
  return rateLimit({
    windowMs: 60 * 1000,
    max: 60,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many ingestion requests, please try again later" },
  });
}

// ── API key authentication ──────────────────────────────────────────

function createApiKeyAuth() {
  const API_KEY = process.env.AGENTLENS_API_KEY || null;
  const API_KEY_BUF = API_KEY ? Buffer.from(API_KEY) : null;

  function authenticateApiKey(req, res, next) {
    if (!API_KEY_BUF) return next(); // dev mode
    const providedKey = req.headers["x-api-key"];
    if (!providedKey) {
      return res.status(401).json({ error: "Unauthorized: invalid or missing API key" });
    }
    const providedBuf = Buffer.from(String(providedKey));
    // Constant-time comparison to prevent timing attacks.
    // timingSafeEqual requires equal-length buffers. To avoid leaking
    // the expected key length through response time, we hash both
    // values with SHA-256 (producing fixed-length digests) before
    // comparing. This ensures the comparison always operates on
    // 32-byte buffers regardless of input lengths.
    const expectedHash = crypto.createHash("sha256").update(API_KEY_BUF).digest();
    const providedHash = crypto.createHash("sha256").update(providedBuf).digest();
    if (!crypto.timingSafeEqual(providedHash, expectedHash)) {
      return res.status(401).json({ error: "Unauthorized: invalid or missing API key" });
    }
    next();
  }

  return { authenticateApiKey, hasApiKey: !!API_KEY };
}

module.exports = {
  createHelmetMiddleware,
  createCorsMiddleware,
  createApiLimiter,
  createIngestLimiter,
  createApiKeyAuth,
};
