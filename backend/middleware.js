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

  // In production without explicit CORS_ORIGINS, deny all cross-origin
  // requests instead of reflecting every origin.  Reflecting all origins
  // lets any website make authenticated API calls using a stolen or
  // guessed API key, exfiltrating observability data cross-origin.
  // In development (no NODE_ENV or NODE_ENV !== "production"), allow
  // localhost origins for convenience.
  let origin;
  if (ALLOWED_ORIGINS) {
    origin = ALLOWED_ORIGINS;
  } else if (process.env.NODE_ENV === "production") {
    origin = false; // deny all cross-origin requests
  } else {
    // Dev mode: allow common local origins
    origin = [
      "http://localhost:3000",
      "http://localhost:5173",
      "http://127.0.0.1:3000",
      "http://127.0.0.1:5173",
    ];
  }

  return cors({
    origin,
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
  // Pre-compute the SHA-256 hash of the expected key once at init time.
  // Previously this was recomputed on every request, adding ~0.01-0.05ms
  // of unnecessary crypto work per authenticated call. On high-throughput
  // ingest paths handling hundreds of requests/second, this adds up.
  const EXPECTED_HASH = API_KEY
    ? crypto.createHash("sha256").update(Buffer.from(API_KEY)).digest()
    : null;

  function authenticateApiKey(req, res, next) {
    if (!EXPECTED_HASH) return next(); // dev mode
    const providedKey = req.headers["x-api-key"];
    if (!providedKey) {
      return res.status(401).json({ error: "Unauthorized: invalid or missing API key" });
    }
    // Constant-time comparison to prevent timing attacks.
    // timingSafeEqual requires equal-length buffers. We hash the
    // provided key with SHA-256 (producing a fixed 32-byte digest)
    // and compare against the pre-computed expected hash. This avoids
    // leaking the expected key length through response time.
    const providedHash = crypto.createHash("sha256").update(Buffer.from(String(providedKey))).digest();
    if (!crypto.timingSafeEqual(providedHash, EXPECTED_HASH)) {
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
