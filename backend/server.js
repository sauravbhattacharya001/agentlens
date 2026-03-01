const express = require("express");
const path = require("path");
const { getDb } = require("./db");
const {
  createHelmetMiddleware,
  createCorsMiddleware,
  createApiLimiter,
  createIngestLimiter,
  createApiKeyAuth,
} = require("./middleware");
const eventsRouter = require("./routes/events");
const sessionsRouter = require("./routes/sessions");
const analyticsRouter = require("./routes/analytics");
const pricingRouter = require("./routes/pricing");
const alertsRouter = require("./routes/alerts");
const annotationsRouter = require("./routes/annotations");
const retentionRouter = require("./routes/retention");
const leaderboardRouter = require("./routes/leaderboard");
const errorsRouter = require("./routes/errors");

const app = express();
const PORT = process.env.PORT || 3000;

// ── Security middleware ─────────────────────────────────────────────
app.use(createHelmetMiddleware());
app.use(createCorsMiddleware());

// ── Rate limiting ───────────────────────────────────────────────────
app.use("/sessions", createApiLimiter());
app.use("/events", createIngestLimiter());
app.use("/analytics", createApiLimiter());
app.use("/pricing", createApiLimiter());
app.use("/alerts", createApiLimiter());
app.use("/annotations", createApiLimiter());
app.use("/retention", createApiLimiter());
app.use("/leaderboard", createApiLimiter());
app.use("/errors", createApiLimiter());

// ── API key authentication ──────────────────────────────────────────
const { authenticateApiKey, hasApiKey } = createApiKeyAuth();
app.use("/events", authenticateApiKey);
app.use("/sessions", authenticateApiKey);
app.use("/analytics", authenticateApiKey);
app.use("/pricing", authenticateApiKey);
app.use("/alerts", authenticateApiKey);
app.use("/annotations", authenticateApiKey);
app.use("/retention", authenticateApiKey);
app.use("/leaderboard", authenticateApiKey);
app.use("/errors", authenticateApiKey);

// Body parser with size limit
app.use(express.json({ limit: "10mb" }));

// Serve dashboard static files
app.use(express.static(path.join(__dirname, "..", "dashboard")));

// API routes
app.use("/events", eventsRouter);
app.use("/sessions", sessionsRouter);
app.use("/analytics", analyticsRouter);
app.use("/pricing", pricingRouter);
app.use("/alerts", alertsRouter);
app.use("/annotations", annotationsRouter);
app.use("/retention", retentionRouter);
app.use("/leaderboard", leaderboardRouter);
app.use("/errors", errorsRouter);
// Mount session-scoped annotation routes on /sessions
app.use("/sessions", annotationsRouter);

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
  if (hasApiKey) {
    console.log(`🔑 API key authentication enabled`);
  } else {
    console.log(`⚠️  No AGENTLENS_API_KEY set — running without auth (dev mode)`);
  }
});
