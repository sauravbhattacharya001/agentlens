const express = require("express");
const cors = require("cors");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");
const path = require("path");
const { getDb } = require("./db");
const eventsRouter = require("./routes/events");
const sessionsRouter = require("./routes/sessions");

const app = express();
const PORT = process.env.PORT || 3000;

// ── Security: Helmet sets standard security headers ─────────────────
app.use(
  helmet({
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
  })
);

// ── Security: CORS — restrict to known origins in production ────────
const ALLOWED_ORIGINS = process.env.CORS_ORIGINS
  ? process.env.CORS_ORIGINS.split(",").map((o) => o.trim())
  : null; // null = allow all in dev; set CORS_ORIGINS in production

app.use(
  cors({
    origin: ALLOWED_ORIGINS || true,
    methods: ["GET", "POST"],
    allowedHeaders: ["Content-Type", "X-API-Key"],
    maxAge: 86400,
  })
);

// ── Security: Rate limiting ─────────────────────────────────────────
const apiLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 120, // 120 requests per minute per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests, please try again later" },
});

const ingestLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 60, // 60 event ingestion requests per minute per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many ingestion requests, please try again later" },
});

app.use("/sessions", apiLimiter);
app.use("/events", ingestLimiter);

// ── Security: API key authentication middleware ─────────────────────
const API_KEY = process.env.AGENTLENS_API_KEY || null;

function authenticateApiKey(req, res, next) {
  // If no API key is configured, skip auth (dev mode)
  if (!API_KEY) return next();

  const providedKey = req.headers["x-api-key"];
  if (!providedKey || providedKey !== API_KEY) {
    return res.status(401).json({ error: "Unauthorized: invalid or missing API key" });
  }
  next();
}

// Apply auth to API routes (not to dashboard static files)
app.use("/events", authenticateApiKey);
app.use("/sessions", authenticateApiKey);

// Body parser with size limit
app.use(express.json({ limit: "10mb" }));

// Serve dashboard static files
app.use(express.static(path.join(__dirname, "..", "dashboard")));

// API routes
app.use("/events", eventsRouter);
app.use("/sessions", sessionsRouter);

// Health check (no auth required)
app.get("/health", (req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// Dashboard catch-all (SPA-style)
app.get("/", (req, res) => {
  res.sendFile(path.join(__dirname, "..", "dashboard", "index.html"));
});

// ── Global error handler — never leak internals ─────────────────────
app.use((err, _req, res, _next) => {
  console.error("Unhandled error:", err);
  res.status(500).json({ error: "Internal server error" });
});

// Initialize DB on startup
getDb();

app.listen(PORT, () => {
  console.log(`🔍 AgentLens backend running on http://localhost:${PORT}`);
  console.log(`📊 Dashboard available at http://localhost:${PORT}`);
  if (API_KEY) {
    console.log(`🔑 API key authentication enabled`);
  } else {
    console.log(`⚠️  No AGENTLENS_API_KEY set — running without auth (dev mode)`);
  }
});
