"use strict";

/**
 * Jest global setup.
 *
 * Creates a per-run temporary directory under the OS temp dir so that
 * each `jest` invocation has its own isolated database root, and stores
 * the path in process.env so that `setupFiles` (which run in each
 * worker) can pick it up.
 *
 * Without this, multiple Jest workers race to open the same
 * `backend/agentlens.db` file and produce intermittent SQLITE_BUSY /
 * "database is locked" / stale-row failures.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = function jestGlobalSetup() {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "agentlens-jest-")
  );
  process.env.AGENTLENS_TEST_DB_ROOT = root;
};
