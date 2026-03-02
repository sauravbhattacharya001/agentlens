/* ── Webhook route tests ──────────────────────────────────────────── */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");

// Stub fetch for testing webhook delivery
let fetchCalls = [];
let fetchResponse = { ok: true, status: 200, text: async () => "ok" };

global.fetch = async (url, opts) => {
  fetchCalls.push({ url, ...opts });
  return { ...fetchResponse, text: async () => fetchResponse.textBody || "ok" };
};

// ── Bootstrap Express app without listening ─────────────────────────

process.env.DB_PATH = ":memory:";
const express = require("express");
const { getDb } = require("../db");
const webhooksRouter = require("../routes/webhooks");

const app = express();
app.use(express.json());
app.use("/webhooks", webhooksRouter);

let server;

function request(method, path, body) {
  return new Promise((resolve, reject) => {
    const opts = {
      hostname: "127.0.0.1",
      port: server.address().port,
      path,
      method,
      headers: { "Content-Type": "application/json" },
    };
    const req = http.request(opts, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, body: data });
        }
      });
    });
    req.on("error", reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

before(() => {
  getDb(); // init schema
  server = app.listen(0);
});

after(() => {
  server.close();
});

describe("Webhooks API", () => {
  let webhookId;

  it("POST /webhooks — creates a webhook", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Test Slack",
      url: "https://hooks.slack.com/test",
      format: "slack",
    });
    assert.equal(res.status, 201);
    assert.ok(res.body.webhook);
    assert.equal(res.body.webhook.name, "Test Slack");
    assert.equal(res.body.webhook.format, "slack");
    webhookId = res.body.webhook.webhook_id;
  });

  it("POST /webhooks — rejects invalid URL", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Bad",
      url: "not-a-url",
    });
    assert.equal(res.status, 400);
  });

  it("POST /webhooks — rejects missing name", async () => {
    const res = await request("POST", "/webhooks", {
      url: "https://example.com",
    });
    assert.equal(res.status, 400);
  });

  it("GET /webhooks — lists webhooks", async () => {
    const res = await request("GET", "/webhooks");
    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.webhooks));
    assert.ok(res.body.webhooks.length >= 1);
    // Secret should be masked
    const wh = res.body.webhooks.find((w) => w.webhook_id === webhookId);
    assert.ok(wh);
  });

  it("PUT /webhooks/:id — updates a webhook", async () => {
    const res = await request("PUT", `/webhooks/${webhookId}`, {
      name: "Updated Slack",
      format: "discord",
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.webhook.name, "Updated Slack");
    assert.equal(res.body.webhook.format, "discord");
  });

  it("POST /webhooks/:id/test — sends test delivery", async () => {
    fetchCalls = [];
    const res = await request("POST", `/webhooks/${webhookId}/test`);
    assert.equal(res.status, 200);
    assert.equal(res.body.test, true);
    assert.equal(res.body.status, "success");
    assert.ok(fetchCalls.length >= 1);
  });

  it("GET /webhooks/:id/deliveries — shows delivery history", async () => {
    const res = await request("GET", `/webhooks/${webhookId}/deliveries`);
    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.deliveries));
    assert.ok(res.body.deliveries.length >= 1);
  });

  it("DELETE /webhooks/:id — deletes a webhook", async () => {
    const res = await request("DELETE", `/webhooks/${webhookId}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.deleted, true);
  });

  it("DELETE /webhooks/:id — 404 for missing webhook", async () => {
    const res = await request("DELETE", "/webhooks/nonexistent");
    assert.equal(res.status, 404);
  });
});
