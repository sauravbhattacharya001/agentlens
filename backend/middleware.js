/**
 * Express middleware configuration.
 *
 * Keeps security headers, CORS, rate-limiting, and API-key auth in one
 * place so that `server.js` stays focused on application wiring.
 */

const helmet = require("helmet");
const cors = require("cors");
const rateLimit = require("express-rate-limit");

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
      },
    },
    crossOriginEmbedderPolicy: false,
  });
}

// ── CORS ─────────────────────────────────────────────────────────────

function createCorsMiddleware() {
  const ALLOWED_ORIGINS = process.env.CORS_ORIGINS
    ? process.env.CORS_ORIGINS.split(",").map((o) => o.trim())
    : null;

  return cors({
    origin: ALLOWED_ORIGINS || true,
    methods: ["GET", "POST"],
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

  function authenticateApiKey(req, res, next) {
    if (!API_KEY) return next(); // dev mode
    const providedKey = req.headers["x-api-key"];
    if (!providedKey || providedKey !== API_KEY) {
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
