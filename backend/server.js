const express = require("express");
const cors = require("cors");
const path = require("path");
const { getDb } = require("./db");
const eventsRouter = require("./routes/events");
const sessionsRouter = require("./routes/sessions");

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json({ limit: "10mb" }));

// Serve dashboard static files
app.use(express.static(path.join(__dirname, "..", "dashboard")));

// API routes
app.use("/events", eventsRouter);
app.use("/sessions", sessionsRouter);

// Health check
app.get("/health", (req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// Dashboard catch-all (SPA-style)
app.get("/", (req, res) => {
  res.sendFile(path.join(__dirname, "..", "dashboard", "index.html"));
});

// Initialize DB on startup
getDb();

app.listen(PORT, () => {
  console.log(`🔍 AgentOps backend running on http://localhost:${PORT}`);
  console.log(`📊 Dashboard available at http://localhost:${PORT}`);
});
