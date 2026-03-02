/* ── Webhooks — notify external services when alerts fire ────────── */

const express = require("express");
const crypto = require("crypto");
const router = express.Router();
const { getDb } = require("../db");

// ── Schema initialisation ───────────────────────────────────────────

function ensureWebhooksTable() {
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
}

function generateId() {
  return `${Date.now().toString(36)}-${crypto.randomBytes(6).toString("hex")}`;
}

// ── Format payload for different services ───────────────────────────

function formatPayload(format, alertData) {
  const { rule_name, metric, operator, threshold, current_value, window_minutes, agent_filter, alert_id, rule_id } = alertData;
  const summary = `🚨 Alert "${rule_name}": ${metric} ${operator} ${threshold} (current: ${current_value}) over ${window_minutes}m window`;

  switch (format) {
    case "slack":
      return {
        text: summary,
        blocks: [
          {
            type: "header",
            text: { type: "plain_text", text: `🚨 AgentLens Alert: ${rule_name}` },
          },
          {
            type: "section",
            fields: [
              { type: "mrkdwn", text: `*Metric:*\n${metric}` },
              { type: "mrkdwn", text: `*Condition:*\n${operator} ${threshold}` },
              { type: "mrkdwn", text: `*Current Value:*\n${current_value}` },
              { type: "mrkdwn", text: `*Window:*\n${window_minutes} minutes` },
              ...(agent_filter ? [{ type: "mrkdwn", text: `*Agent:*\n${agent_filter}` }] : []),
            ],
          },
        ],
      };

    case "discord":
      return {
        content: summary,
        embeds: [
          {
            title: `🚨 AgentLens Alert: ${rule_name}`,
            color: 0xff4444,
            fields: [
              { name: "Metric", value: metric, inline: true },
              { name: "Condition", value: `${operator} ${threshold}`, inline: true },
              { name: "Current Value", value: `${current_value}`, inline: true },
              { name: "Window", value: `${window_minutes} minutes`, inline: true },
              ...(agent_filter ? [{ name: "Agent", value: agent_filter, inline: true }] : []),
            ],
            timestamp: new Date().toISOString(),
          },
        ],
      };

    case "json":
    default:
      return {
        event: "alert.fired",
        alert_id,
        rule_id,
        rule_name,
        metric,
        operator,
        threshold,
        current_value,
        window_minutes,
        agent_filter,
        fired_at: new Date().toISOString(),
      };
  }
}

// ── Helper: sign payload with HMAC-SHA256 ───────────────────────────

function signPayload(payload, secret) {
  const body = JSON.stringify(payload);
  return crypto.createHmac("sha256", secret).update(body).digest("hex");
}

// ── Helper: deliver webhook (with retries) ──────────────────────────

async function deliverWebhook(webhook, alertData) {
  const db = getDb();
  const payload = formatPayload(webhook.format, alertData);
  const body = JSON.stringify(payload);
  const deliveryId = generateId();

  let lastError = null;
  let statusCode = null;
  let responseBody = null;

  for (let attempt = 1; attempt <= webhook.retry_count; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), webhook.timeout_ms);

      const headers = { "Content-Type": "application/json", "User-Agent": "AgentLens-Webhook/1.0" };
      if (webhook.secret) {
        headers["X-AgentLens-Signature"] = signPayload(payload, webhook.secret);
      }
      headers["X-AgentLens-Delivery"] = deliveryId;

      const resp = await fetch(webhook.url, {
        method: "POST",
        headers,
        body,
        signal: controller.signal,
      });
      clearTimeout(timer);

      statusCode = resp.status;
      try { responseBody = await resp.text(); } catch { responseBody = null; }

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
  db.prepare(`
    INSERT INTO webhook_deliveries (delivery_id, webhook_id, alert_id, status, status_code, request_body, response_body, error, attempts, delivered_at)
    VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?)
  `).run(deliveryId, webhook.webhook_id, alertData.alert_id || null, statusCode, body, responseBody, lastError, webhook.retry_count, new Date().toISOString());

  return { delivery_id: deliveryId, status: "failed", error: lastError, attempts: webhook.retry_count };
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
      const ruleIds = JSON.parse(wh.rule_ids);
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

router.get("/", (req, res) => {
  try {
    ensureWebhooksTable();
    const db = getDb();
    const webhooks = db.prepare("SELECT * FROM webhooks ORDER BY created_at DESC").all();
    res.json({
      webhooks: webhooks.map((w) => ({
        ...w,
        enabled: !!w.enabled,
        rule_ids: w.rule_ids ? JSON.parse(w.rule_ids) : null,
        secret: w.secret ? "••••••" : null, // Don't expose secrets
      })),
    });
  } catch (err) {
    console.error("Error listing webhooks:", err);
    res.status(500).json({ error: "Failed to list webhooks" });
  }
});

// ── POST /webhooks — create a webhook ───────────────────────────────

router.post("/", (req, res) => {
  try {
    ensureWebhooksTable();
    const db = getDb();
    const { name, url, secret, format, rule_ids, retry_count, timeout_ms } = req.body;

    if (!name || typeof name !== "string" || name.trim().length === 0) {
      return res.status(400).json({ error: "name is required" });
    }
    if (!url || typeof url !== "string") {
      return res.status(400).json({ error: "url is required" });
    }
    try { new URL(url); } catch { return res.status(400).json({ error: "url must be a valid URL" }); }

    const validFormats = ["json", "slack", "discord"];
    if (format && !validFormats.includes(format)) {
      return res.status(400).json({ error: `format must be one of: ${validFormats.join(", ")}` });
    }
    if (rule_ids && !Array.isArray(rule_ids)) {
      return res.status(400).json({ error: "rule_ids must be an array of rule IDs" });
    }

    const webhookId = generateId();
    const now = new Date().toISOString();

    db.prepare(`
      INSERT INTO webhooks (webhook_id, name, url, secret, format, rule_ids, enabled, retry_count, timeout_ms, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
    `).run(
      webhookId, name.trim(), url, secret || null, format || "json",
      rule_ids ? JSON.stringify(rule_ids) : null,
      Number(retry_count) || 3, Number(timeout_ms) || 5000, now, now
    );

    const webhook = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
    res.status(201).json({
      webhook: {
        ...webhook,
        enabled: !!webhook.enabled,
        rule_ids: webhook.rule_ids ? JSON.parse(webhook.rule_ids) : null,
        secret: webhook.secret ? "••••••" : null,
      },
    });
  } catch (err) {
    console.error("Error creating webhook:", err);
    res.status(500).json({ error: "Failed to create webhook" });
  }
});

// ── PUT /webhooks/:webhookId — update a webhook ─────────────────────

router.put("/:webhookId", (req, res) => {
  try {
    ensureWebhooksTable();
    const db = getDb();
    const { webhookId } = req.params;

    const existing = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
    if (!existing) return res.status(404).json({ error: "Webhook not found" });

    const { name, url, secret, format, rule_ids, enabled, retry_count, timeout_ms } = req.body;
    const updates = {};

    if (name !== undefined) updates.name = name.trim();
    if (url !== undefined) {
      try { new URL(url); } catch { return res.status(400).json({ error: "url must be a valid URL" }); }
      updates.url = url;
    }
    if (secret !== undefined) updates.secret = secret || null;
    if (format !== undefined) updates.format = format;
    if (rule_ids !== undefined) updates.rule_ids = rule_ids ? JSON.stringify(rule_ids) : null;
    if (enabled !== undefined) updates.enabled = enabled ? 1 : 0;
    if (retry_count !== undefined) updates.retry_count = Number(retry_count);
    if (timeout_ms !== undefined) updates.timeout_ms = Number(timeout_ms);

    const setClauses = Object.keys(updates).map((k) => `${k} = ?`);
    setClauses.push("updated_at = ?");
    const values = [...Object.values(updates), new Date().toISOString(), webhookId];

    db.prepare(`UPDATE webhooks SET ${setClauses.join(", ")} WHERE webhook_id = ?`).run(...values);

    const webhook = db.prepare("SELECT * FROM webhooks WHERE webhook_id = ?").get(webhookId);
    res.json({
      webhook: {
        ...webhook,
        enabled: !!webhook.enabled,
        rule_ids: webhook.rule_ids ? JSON.parse(webhook.rule_ids) : null,
        secret: webhook.secret ? "••••••" : null,
      },
    });
  } catch (err) {
    console.error("Error updating webhook:", err);
    res.status(500).json({ error: "Failed to update webhook" });
  }
});

// ── DELETE /webhooks/:webhookId — delete a webhook ──────────────────

router.delete("/:webhookId", (req, res) => {
  try {
    ensureWebhooksTable();
    const db = getDb();
    const { webhookId } = req.params;

    const result = db.prepare("DELETE FROM webhooks WHERE webhook_id = ?").run(webhookId);
    if (result.changes === 0) return res.status(404).json({ error: "Webhook not found" });

    res.json({ deleted: true, webhook_id: webhookId });
  } catch (err) {
    console.error("Error deleting webhook:", err);
    res.status(500).json({ error: "Failed to delete webhook" });
  }
});

// ── POST /webhooks/:webhookId/test — send a test payload ────────────

router.post("/:webhookId/test", async (req, res) => {
  try {
    ensureWebhooksTable();
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
  } catch (err) {
    console.error("Error testing webhook:", err);
    res.status(500).json({ error: "Failed to test webhook" });
  }
});

// ── GET /webhooks/:webhookId/deliveries — delivery history ──────────

router.get("/:webhookId/deliveries", (req, res) => {
  try {
    ensureWebhooksTable();
    const db = getDb();
    const { webhookId } = req.params;

    const webhook = db.prepare("SELECT webhook_id FROM webhooks WHERE webhook_id = ?").get(webhookId);
    if (!webhook) return res.status(404).json({ error: "Webhook not found" });

    const { status, limit: limitStr } = req.query;
    const limit = Math.min(Number(limitStr) || 50, 200);

    let sql = "SELECT * FROM webhook_deliveries WHERE webhook_id = ?";
    const params = [webhookId];
    if (status) { sql += " AND status = ?"; params.push(status); }
    sql += " ORDER BY delivered_at DESC LIMIT ?";
    params.push(limit);

    const deliveries = db.prepare(sql).all(...params);
    res.json({ deliveries, count: deliveries.length });
  } catch (err) {
    console.error("Error listing deliveries:", err);
    res.status(500).json({ error: "Failed to list deliveries" });
  }
});

module.exports = router;
module.exports.fireWebhooks = fireWebhooks;
