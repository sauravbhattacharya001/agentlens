const express = require("express");
const path = require("path");
const { getDb, closeDb } = require("./db");
const {
  createHelmetMiddleware,
  createCorsMiddleware,
  createApiLimiter,
  createIngestLimiter,
  createApiKeyAuth,
} = require("./middleware");

const app = express();
const PORT = process.env.PORT || 3000;

// ── Security middleware ─────────────────────────────────────────────
app.use(createHelmetMiddleware());
app.use(createCorsMiddleware());

// ── Rate limiting & authentication ──────────────────────────────────
const apiLimiter = createApiLimiter();
const ingestLimiter = createIngestLimiter();
const { authenticateApiKey, hasApiKey } = createApiKeyAuth();

// ── Route definitions ───────────────────────────────────────────────
// Each entry: [mountPath, routerModule, options?]
//   options.limiter  — override the default apiLimiter (e.g. ingestLimiter)
//   options.noAuth   — skip API-key auth for this mount
//   options.mount    — override the Express mount path (when different from URL prefix)
const routeDefs = [
  ["/events",       "./routes/events",                { limiter: ingestLimiter }],
  // Tag routes must be mounted before sessions to avoid /:id catching "tags" / "by-tag"
  ["/sessions",     "./routes/tags"],
  ["/sessions",     "./routes/sessions"],
  ["/analytics",    "./routes/analytics"],
  ["/pricing",      "./routes/pricing"],
  ["/alerts",       "./routes/alerts"],
  ["/annotations",  "./routes/annotations"],
  ["/retention",    "./routes/retention"],
  ["/leaderboard",  "./routes/leaderboard"],
  ["/errors",       "./routes/errors"],
  ["/webhooks",     "./routes/webhooks"],
  ["/dependencies", "./routes/dependencies"],
  ["/correlations", "./routes/correlations"],
  ["/correlations", "./routes/correlation-scheduler"],
  ["/postmortem",   "./routes/postmortem"],
  ["/bookmarks",    "./routes/bookmarks"],
  ["/baselines",    "./routes/baselines"],
  ["/budgets",      "./routes/budgets"],
  ["/sla",          "./routes/sla"],
  ["/anomalies",    "./routes/anomalies"],
  ["/replay",       "./routes/replay"],
  ["/forecast",     "./routes/forecast"],
  ["/scorecards",   "./routes/scorecards"],
  ["/diff",         "./routes/diff"],
  // Session-scoped annotation routes
  ["/sessions",     "./routes/annotations"],
];

// Deduplicate mount paths for middleware registration (rate-limit + auth
// only need to be applied once per path prefix).
const registeredPaths = new Set();

for (const [mountPath, modulePath, opts = {}] of routeDefs) {
  if (!registeredPaths.has(mountPath)) {
    app.use(mountPath, opts.limiter || apiLimiter);
    if (!opts.noAuth) {
      app.use(mountPath, authenticateApiKey);
    }
    registeredPaths.add(mountPath);
  }
}

// Prevent browsers and proxies from caching sensitive API responses.
// The in-memory cache handles server-side caching; downstream layers
// must never store token counts, costs, or session data.
app.use((req, res, next) => {
  res.set("Cache-Control", "no-store, no-cache, must-revalidate, private");
  res.set("Pragma", "no-cache");
  res.set("Expires", "0");
  next();
});

// Body parser with size limit (after rate-limit/auth, before route handlers)
app.use(express.json({ limit: "10mb" }));

// Serve dashboard static files
app.use(express.static(path.join(__dirname, "..", "dashboard")));

// Mount all route handlers
for (const [mountPath, modulePath] of routeDefs) {
  app.use(mountPath, require(modulePath));
}

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

const server = app.listen(PORT, () => {
  console.log(`🔍 AgentLens backend running on http://localhost:${PORT}`);
  console.log(`📊 Dashboard available at http://localhost:${PORT}`);
  if (hasApiKey) {
    console.log(`🔑 API key authentication enabled`);
  } else {
    console.log(`⚠️  No AGENTLENS_API_KEY set — running without auth (dev mode)`);
  }
});

// ── Graceful shutdown ───────────────────────────────────────────────
// Ensures in-flight requests complete and the SQLite WAL journal is
// checkpointed before the process exits. Without this, a SIGTERM from
// Docker/systemd/PM2 could leave the database in an unclean state.

function shutdown(signal) {
  console.log(`\n🛑 Received ${signal}, shutting down gracefully...`);
  server.close(() => {
    console.log("✅ HTTP server closed");
    closeDb();
    console.log("✅ Database closed");
    process.exit(0);
  });

  // Force exit after 10 seconds if connections don't drain
  setTimeout(() => {
    console.error("⚠️  Forcing shutdown after timeout");
    closeDb();
    process.exit(1);
  }, 10000).unref();
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
