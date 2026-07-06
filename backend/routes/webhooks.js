/* ── Webhooks — notify external services when alerts fire ────────── */

const express = require("express");
const rateLimit = require("express-rate-limit");
const router = express.Router();
const { getDb } = require("../db");
const { validateWebhookUrl, safeJsonParse } = require("../lib/validation");
const { parseLimit, wrapRoute } = require("../lib/request-helpers");
const { formatPayload } = require("../lib/webhook-payload");
const { validateResolvedIps } = require("../lib/ssrf-guard");
const { signPayload } = require("../lib/webhook-signature");
const { makeId } = require("../lib/id-generator");

// ── Stricter rate limit for outbound webhook requests ───────────────
// The /test and fire endpoints trigger outbound HTTP requests to
// user-supplied URLs. A tighter limit (10 req/min) prevents abuse
// of the server as an HTTP request proxy, even within SSRF guards.
const webhookOutboundLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many webhook test requests, please try again later" },
});

// ── Security limits ─────────────────────────────────────────────────
// Prevent resource exhaustion via unbounded user-controlled values.
const MAX_RETRY_COUNT = 10;       // max delivery retries per webhook
const MAX_TIMEOUT_MS = 30000;     // max 30s per delivery attempt
const MAX_SECRET_LENGTH = 256;    // max HMAC secret length
const MAX_NAME_LENGTH = 128;      // max webhook name length
const MAX_RULE_IDS = 50;          // max alert rule bindings per webhook

// ── Schema initialisation ───────────────────────────────────────────

let _tableReady = false;

function ensureWebhooksTable() {
  if (_tableReady) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS webhooks (
      webhook_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      url TEXT NOT NULL,
      secret TEXT DEFAULT NULL,
      format TEXT NOT NULL DEFAULT 'json' CHECK(format IN ('json', 'slack', 'discord')),
      rule_ids TEXT DEFAULT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      retry_count INTEGER NOT NULL DEFAULT 3,
      timeout_ms INTEGER NOT NULL DEFAULT 5000,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS webhook_deliveries (
      delivery_id TEXT PRIMARY KEY,
      webhook_id TEXT NOT NULL,
      alert_id TEXT DEFAULT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','success','failed')),
      status_code INTEGER DEFAULT NULL,
      request_body TEXT NOT NULL,
      response_body TEXT DEFAULT NULL,
      error TEXT DEFAULT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      delivered_at TEXT NOT NULL,
      FOREIGN KEY (webhook_id) REFERENCES webhooks(webhook_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook ON webhook_deliveries(webhook_id);
    CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries(status);
    CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_delivered ON webhook_deliveries(delivered_at);
  `);
  _tableReady = true;
}

function generateId() {
  return makeId();
}

// Validate webhookId: alphanumeric + hyphens, max 64 chars
const WEBHOOK_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$/;

function validateWebhookId(id) {
  return typeof id === "string" && WEBHOOK_ID_RE.test(id);
}

// ── Shared response formatter ───────────────────────────────────────
// Consolidates 3 identical inline mapping blocks that mask secrets
// and parse rule_ids JSON for the API response.

function formatWebhookResponse(w) {
  return {
    ...w,
    enabled: !!w.enabled,
    rule_ids: w.rule_ids ? safeJsonParse(w.rule_ids, null) : null,
    secret: w.secret ? "••••••" : null,
  };
}

// Middleware: reject invalid webhook IDs early
router.param("webhookId", (req, res, next, val) => {
  if (!validateWebhookId(val)) {
    return res.status(400).json({ error: "Invalid webhook ID format" });
  }
  next();
});

// Middleware: ensure table exists (once per process, not per request)
router.use((req, res, next) => {
  ensureWebhooksTable();
  next();
});

// Per-format alert payload shaping (Slack / Discord / JSON) lives in
// lib/webhook-payload.js so the three payload shapes are unit-testable
// without the DNS/HMAC/fetch/DB delivery path; formatPayload is imported above.

// ── Helper: sign payload with HMAC-SHA256 ───────────────────────────
// The replay-resistant HMAC signing (canonical `${timestamp}.${rawBody}`
// string → `t=<ts>,v1=<hex>` header, issue #185) lives in
// lib/webhook-signature.js so the exact wire contract receivers must
// reproduce is unit-testable without the DNS/fetch/DB delivery path;
// signPayload is imported above.

// ── Helper: deliver webhook (with retries) ──────────────────────────

/**
 * Record a failed webhook delivery and return the failure result object.
 * Consolidates 3 identical INSERT blocks into a single reusable helper.
 *
 * @param {Object}      db          - Database connection
 * @param {string}      deliveryId  - Unique delivery ID
 * @param {Object}      webhook     - Webhook record
 * @param {string|null} alertId     - Alert ID (nullable)
 * @param {string}      body        - Serialized request body
 * @param {string}      errorMsg    - Human-readable error description
 * @param {number}      [statusCode=null] - HTTP status code (null for non-HTTP failures)
 * @param {string|null} [responseBody=null] - Response body if available
 * @param {number}      [attempts=0] - Number of delivery attempts made
 * @returns {{ delivery_id: string, status: string, error: string, attempts: number }}
 */
function recordFailedDelivery(db, deliveryId, webhook, alertId, body, errorMsg, statusCode, responseBody, attempts) {
  db.prepare(`
    INSERT INTO webhook_deliveries (delivery_id, webhook_id, alert_id, status, status_code, request_body, response_body, error, attempts, delivered_at)
    VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?)
  `).run(deliveryId, webhook.webhook_id, alertId, statusCode != null ? statusCode : null, body, responseBody != null ? responseBody : null, errorMsg, attempts || 0, new Date().toISOString());
  return { delivery_id: deliveryId, status: "failed", error: errorMsg, attempts: attempts || 0 };
}

async function deliverWebhook(webhook, alertData) {
  const db = getDb();
  const payload = formatPayload(webhook.format, alertData);
  const body = JSON.stringify(payload);
  const deliveryId = generateId();
  const alertId = alertData.alert_id || null;

  // DNS rebinding protection: resolve the hostname and validate IPs
  // at delivery time, not just at URL registration time.
  // IMPORTANT: if DNS resolution fails for ANY reason, block the delivery
  // rather than proceeding without the SSRF check — a permissive catch
  // here would allow DNS rebinding attacks to bypass the protection.
  try {
    const parsed = new URL(webhook.url);
    const dnsCheck = await validateResolvedIps(parsed.hostname);
    if (!dnsCheck.safe) {
      return recordFailedDelivery(db, deliveryId, webhook, alertId, body, `SSRF blocked: ${dnsCheck.error}`);
    }
  } catch (dnsErr) {
    // Block delivery when DNS validation itself fails — proceeding without
    // the check would allow SSRF via DNS rebinding or transient resolution errors.
    return recordFailedDelivery(db, deliveryId, webhook, alertId, body, `SSRF check failed: ${dnsErr.message || "DNS validation error"}`);
  }

  let lastError = null;
  let statusCode = null;
  let responseBody = null;

  for (let attempt = 1; attempt <= webhook.retry_count; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), webhook.timeout_ms);

      const headers = { "Content-Type": "application/json", "User-Agent": "AgentLens-Webhook/1.0" };
      if (webhook.secret) {
        // Bind a Unix-seconds timestamp into the MAC so receivers can
        // reject replays (issue #185). The X-AgentLens-Timestamp header
        // is mirrored from `t=...` inside the signature so receivers can
        // recompute the canonical signing string without parsing the
        // composite signature header first.
        const ts = Math.floor(Date.now() / 1000);
        headers["X-AgentLens-Timestamp"] = String(ts);
        headers["X-AgentLens-Signature"] = signPayload(body, ts, webhook.secret);
      }
      headers["X-AgentLens-Delivery"] = deliveryId;

      const resp = await fetch(webhook.url, {
        method: "POST",
        headers,
        body,
        signal: controller.signal,
        redirect: "error",  // Block redirects to prevent SSRF bypass via open redirect
      });
      clearTimeout(timer);

      statusCode = resp.status;
      // Limit response body to 16 KB to prevent memory exhaustion from
      // malicious webhook endpoints returning huge payloads.
      try {
        const rawBody = await resp.text();
        responseBody = rawBody.length > 16384 ? rawBody.slice(0, 16384) + "...[truncated]" : rawBody;
      } catch { responseBody = null; }

      if (resp.ok) {
        db.prepare(`
          INSERT INTO webhook_deliveries (delivery_id, webhook_id, alert_id, status, status_code, request_body, response_body, attempts, delivered_at)
          VALUES (?, ?, ?, 'success', ?, ?, ?, ?, ?)
        `).run(deliveryId, webhook.webhook_id, alertData.alert_id || null, statusCode, body, responseBody, attempt, new Date().toISOString());
        return { delivery_id: deliveryId, status: "success", status_code: statusCode, attempts: attempt };
      }

      lastError = `HTTP ${statusCode}`;
    } catch (err) {
      lastError = err.name === "AbortError" ? "Timeout" : err.message;
    }

    // Wait before retry (exponential backoff: 1s, 2s, 4s...)
    if (attempt < webhook.retry_count) {
      await new Promise((r) => setTimeout(r, 1000 * Math.pow(2, attempt - 1)));
    }
  }

  // All retries exhausted
  return recordFailedDelivery(db, deliveryId, webhook, alertId, body, lastError, statusCode, responseBody, webhook.retry_count);
}

// ── Fire webhooks for a triggered alert ─────────────────────────────

async function fireWebhooks(alertData) {
  ensureWebhooksTable();
  const db = getDb();

  const webhooks = db.prepare("SELECT * FROM webhooks WHERE enabled = 1").all();
  const results = [];

  for (const wh of webhooks) {
    // Check if webhook is scoped to specific rules
    if (wh.rule_ids) {
      const ruleIds = safeJsonParse(wh.rule_ids, null);
      if (Array.isArray(ruleIds) && ruleIds.length > 0 && !ruleIds.includes(alertData.rule_id)) {
        continue; // Skip — this webhook doesn't watch this rule
      }
    }

    const result = await deliverWebhook(wh, alertData);
    results.push({ webhook_id: wh.webhook_id, name: wh.name, ...result });
  }

  return results;
}

// ── GET /webhooks — list all webhooks ───────────────────────────────

router.get("/", wrapRoute("list webhooks", (req, res) => {
  const db = getDb();
  const webhooks = db.prepare("SELECT * FROM webhooks ORDER BY created_at DESC").all();
  res.json({ webhooks: webhooks.map(formatWebhookResponse) });
}));

// ── POST /webhooks — create a webhook ───────────────────────────────

router.post("/", wrapRoute("create webhook", (req, res) => {
  const db = getDb();
  const { name, url, secret, format, rule_ids, retry_count, timeout_ms } = req.body;

  if (!name || typeof name !== "string" || name.trim().length === 0) {
    return res.status(400).json({ error: "name is required" });
  }
  if (!url || typeof url !== "string") {
    return res.status(400).json({ error: "url is required" });
  }
  const urlCheck = validateWebhookUrl(url);
  if (!urlCheck.valid) {
    return res.status(400).json({ error: urlCheck.error });
  }

  const validFormats = ["json", "slack", "discord"];
  if (format && !validFormats.includes(format)) {
    return res.status(400).json({ error: `format must be one of: ${validFormats.join(", ")}` });
  }
  if (rule_ids && !Array.isArray(rule_ids)) {
    return res.status(400).json({ error: "rule_ids must be an array of rule IDs" });
  }

  // Bound retry_count and timeout_ms to prevent resource exhaustion
  const safeRetryCount = Math.min(Math.max(0, Number(retry_count) || 3), MAX_RETRY_COUNT);
  const safeTimeoutMs = Math.min(Math.max(500, Number(timeout_ms) || 5000), MAX_TIMEOUT_MS);

  // Limit rule_ids array size
  if (rule_ids && rule_ids.length > MAX_RULE_IDS) {
    return res.status(400).json({ error: `rule_ids cannot exceed ${MAX_RULE_IDS} entries` });
  }

  // Limit secret length
  if (secret && typeof secret === "string" && secret.length > MAX_SECRET_LENGTH) {
    return res.status(400).json({ error: `secret cannot exceed ${MAX_SECRET_LENGTH} characters` });
  }

  const webhookId = generateId();
  const now = new Date().toISOString();

  db.prepare(`
    INSERT INTO webhooks (webhook_id, name, url, secret, format, rule_ids, enabled, retry_count, timeout_ms, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
  `).run(
    webhookId, name.trim().slice(0, MAX_NAME_LENGTH), url, secret || null, format || "json",
    rule_ids ? JSON.stringify(rule_ids) : null,
    safeRetryCount, safeTimeoutMs, now, now
  );

  const webhook = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
  res.status(201).json({ webhook: formatWebhookResponse(webhook) });
}));

// ── PUT /webhooks/:webhookId — update a webhook ─────────────────────

router.put("/:webhookId", wrapRoute("update webhook", (req, res) => {
  const db = getDb();
  const { webhookId } = req.params;

  const existing = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
  if (!existing) return res.status(404).json({ error: "Webhook not found" });

  const { name, url, secret, format, rule_ids, enabled, retry_count, timeout_ms } = req.body;
  const updates = {};

  if (name !== undefined) {
    if (typeof name !== "string" || !name.trim()) {
      return res.status(400).json({ error: "name must be a non-empty string" });
    }
    updates.name = name.trim().slice(0, MAX_NAME_LENGTH);
  }
  if (url !== undefined) {
    const urlCheck = validateWebhookUrl(url);
    if (!urlCheck.valid) {
      return res.status(400).json({ error: urlCheck.error });
    }
    updates.url = url;
  }
  if (secret !== undefined) {
    if (secret && typeof secret === "string" && secret.length > MAX_SECRET_LENGTH) {
      return res.status(400).json({ error: `secret cannot exceed ${MAX_SECRET_LENGTH} characters` });
    }
    updates.secret = secret || null;
  }
  if (format !== undefined) {
    const validFormats = ["json", "slack", "discord"];
    if (!validFormats.includes(format)) {
      return res.status(400).json({ error: `format must be one of: ${validFormats.join(", ")}` });
    }
    updates.format = format;
  }
  if (rule_ids !== undefined) {
    if (rule_ids && Array.isArray(rule_ids) && rule_ids.length > MAX_RULE_IDS) {
      return res.status(400).json({ error: `rule_ids cannot exceed ${MAX_RULE_IDS} entries` });
    }
    updates.rule_ids = rule_ids ? JSON.stringify(rule_ids) : null;
  }
  if (enabled !== undefined) updates.enabled = enabled ? 1 : 0;
  if (retry_count !== undefined) updates.retry_count = Math.min(Math.max(0, Number(retry_count)), MAX_RETRY_COUNT);
  if (timeout_ms !== undefined) updates.timeout_ms = Math.min(Math.max(500, Number(timeout_ms)), MAX_TIMEOUT_MS);

  const setClauses = Object.keys(updates).map((k) => `${k} = ?`);
  setClauses.push("updated_at = ?");
  const values = [...Object.values(updates), new Date().toISOString(), webhookId];

  db.prepare(`UPDATE webhooks SET ${setClauses.join(", ")} WHERE webhook_id = ?`).run(...values);

  const webhook = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
  res.json({ webhook: formatWebhookResponse(webhook) });
}));

// ── DELETE /webhooks/:webhookId — delete a webhook ──────────────────

router.delete("/:webhookId", wrapRoute("delete webhook", (req, res) => {
  const db = getDb();
  const { webhookId } = req.params;

  const result = db.prepare("DELETE FROM webhooks WHERE webhook_id = ?").run(webhookId);
  if (result.changes === 0) return res.status(404).json({ error: "Webhook not found" });

  res.json({ deleted: true, webhook_id: webhookId });
}));

// ── POST /webhooks/:webhookId/test — send a test payload ────────────

router.post("/:webhookId/test", webhookOutboundLimiter, wrapRoute("test webhook", async (req, res) => {
  const db = getDb();
  const { webhookId } = req.params;

  const webhook = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
  if (!webhook) return res.status(404).json({ error: "Webhook not found" });

  const testData = {
    rule_name: "Test Alert",
    metric: "error_rate",
    operator: ">",
    threshold: 10,
    current_value: 15.5,
    window_minutes: 60,
    agent_filter: null,
    alert_id: "test-" + generateId(),
    rule_id: "test-rule",
  };

  const result = await deliverWebhook(webhook, testData);
  res.json({ test: true, ...result });
}));

// ── GET /webhooks/:webhookId/deliveries — delivery history ──────────

router.get("/:webhookId/deliveries", wrapRoute("list deliveries", (req, res) => {
  const db = getDb();
  const { webhookId } = req.params;

  const webhook = db.prepare("SELECT webhook_id FROM webhooks WHERE webhook_id = ?").get(webhookId);
  if (!webhook) return res.status(404).json({ error: "Webhook not found" });

  const { status } = req.query;
  const limit = parseLimit(req.query.limit, 50, 200);

  // Validate status filter to prevent unexpected SQL values
  const VALID_DELIVERY_STATUSES = ["pending", "success", "failed"];
  if (status && !VALID_DELIVERY_STATUSES.includes(status)) {
    return res.status(400).json({ error: `Invalid status. Valid: ${VALID_DELIVERY_STATUSES.join(", ")}` });
  }

  let sql = "SELECT * FROM webhook_deliveries WHERE webhook_id = ?";
  const params = [webhookId];
  if (status) { sql += " AND status = ?"; params.push(status); }
  sql += " ORDER BY delivered_at DESC LIMIT ?";
  params.push(limit);

  const deliveries = db.prepare(sql).all(...params);
  res.json({ deliveries, count: deliveries.length });
}));

module.exports = router;
module.exports.fireWebhooks = fireWebhooks;
