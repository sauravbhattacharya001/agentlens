"use strict";

/**
 * Per-worker Jest setup.
 *
 * Assigns a unique SQLite file per Jest worker so concurrent suites
 * that fall through to the real `db.js` cannot stomp on each other.
 *
 * Tests that mock `../db` (most of them) are unaffected.
 */
const path = require("path");

if (!process.env.DB_PATH) {
  const root = process.env.AGENTLENS_TEST_DB_ROOT || require("os").tmpdir();
  const workerId = process.env.JEST_WORKER_ID || "1";
  process.env.DB_PATH = path.join(root, `agentlens-worker-${workerId}.db`);
}
