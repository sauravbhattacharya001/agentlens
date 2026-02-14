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

const app = express();
const PORT = process.env.PORT || 3000;

// ── Security middleware ─────────────────────────────────────────────
app.use(createHelmetMiddleware());
app.use(createCorsMiddleware());

// ── Rate limiting ───────────────────────────────────────────────────
app.use("/sessions", createApiLimiter());
app.use("/events", createIngestLimiter());

// ── API key authentication ──────────────────────────────────────────
const { authenticateApiKey, hasApiKey } = createApiKeyAuth();
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
  if (hasApiKey) {
    console.log(`🔑 API key authentication enabled`);
  } else {
    console.log(`⚠️  No AGENTLENS_API_KEY set — running without auth (dev mode)`);
  }
});
