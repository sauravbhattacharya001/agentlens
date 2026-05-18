/* ── Webhook route tests ──────────────────────────────────────────── */

// Jest provides describe/it/beforeAll/afterAll globals
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

beforeAll(() => {
  getDb(); // init schema
  server = app.listen(0);
});

afterAll(() => {
  server.close();
});

describe("Webhooks API", () => {
  let webhookId;

  it("POST /webhooks - creates a webhook", async () => {
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

  it("POST /webhooks - rejects invalid URL", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Bad",
      url: "not-a-url",
    });
    assert.equal(res.status, 400);
  });

  it("POST /webhooks - rejects missing name", async () => {
    const res = await request("POST", "/webhooks", {
      url: "https://example.com",
    });
    assert.equal(res.status, 400);
  });

  it("GET /webhooks - lists webhooks", async () => {
    const res = await request("GET", "/webhooks");
    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.webhooks));
    assert.ok(res.body.webhooks.length >= 1);
    // Secret should be masked
    const wh = res.body.webhooks.find((w) => w.webhook_id === webhookId);
    assert.ok(wh);
  });

  it("PUT /webhooks/:id - updates a webhook", async () => {
    const res = await request("PUT", `/webhooks/${webhookId}`, {
      name: "Updated Slack",
      format: "discord",
    });
    assert.equal(res.status, 200);
    assert.equal(res.body.webhook.name, "Updated Slack");
    assert.equal(res.body.webhook.format, "discord");
  });

  it("POST /webhooks/:id/test - sends test delivery", async () => {
    fetchCalls = [];
    const res = await request("POST", `/webhooks/${webhookId}/test`);
    assert.equal(res.status, 200);
    assert.equal(res.body.test, true);
    assert.equal(res.body.status, "success");
    assert.ok(fetchCalls.length >= 1);
  });

  it("GET /webhooks/:id/deliveries - shows delivery history", async () => {
    const res = await request("GET", `/webhooks/${webhookId}/deliveries`);
    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.deliveries));
    assert.ok(res.body.deliveries.length >= 1);
  });

  it("DELETE /webhooks/:id - deletes a webhook", async () => {
    const res = await request("DELETE", `/webhooks/${webhookId}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.deleted, true);
  });

  it("DELETE /webhooks/:id - 404 for missing webhook", async () => {
    const res = await request("DELETE", "/webhooks/nonexistent");
    assert.equal(res.status, 404);
  });

  // ── Security: input bounds ─────────────────────────────────────────

  it("POST /webhooks - clamps retry_count to maximum 10", async () => {
    const res = await request("POST", "/webhooks", {
      name: "High Retry",
      url: "https://example.com/hook",
      retry_count: 999999,
    });
    assert.equal(res.status, 201);
    assert.ok(res.body.webhook.retry_count <= 10,
      `retry_count should be clamped to 10, got ${res.body.webhook.retry_count}`);
  });

  it("POST /webhooks - clamps timeout_ms to maximum 30000", async () => {
    const res = await request("POST", "/webhooks", {
      name: "High Timeout",
      url: "https://example.com/hook",
      timeout_ms: 9999999,
    });
    assert.equal(res.status, 201);
    assert.ok(res.body.webhook.timeout_ms <= 30000,
      `timeout_ms should be clamped to 30000, got ${res.body.webhook.timeout_ms}`);
  });

  it("POST /webhooks - clamps timeout_ms minimum to 500", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Low Timeout",
      url: "https://example.com/hook",
      timeout_ms: 1,
    });
    assert.equal(res.status, 201);
    assert.ok(res.body.webhook.timeout_ms >= 500,
      `timeout_ms should be at least 500, got ${res.body.webhook.timeout_ms}`);
  });

  it("POST /webhooks - rejects secret exceeding 256 characters", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Long Secret",
      url: "https://example.com/hook",
      secret: "x".repeat(300),
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("secret"));
  });

  it("POST /webhooks - rejects rule_ids exceeding 50 entries", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Many Rules",
      url: "https://example.com/hook",
      rule_ids: Array.from({ length: 51 }, (_, i) => `rule-${i}`),
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("rule_ids"));
  });

  it("POST /webhooks - truncates name to 128 characters", async () => {
    const longName = "A".repeat(200);
    const res = await request("POST", "/webhooks", {
      name: longName,
      url: "https://example.com/hook",
    });
    assert.equal(res.status, 201);
    assert.ok(res.body.webhook.name.length <= 128,
      `name should be truncated to 128, got ${res.body.webhook.name.length}`);
  });

  it("PUT /webhooks/:id - clamps retry_count on update", async () => {
    // Create a webhook first
    const create = await request("POST", "/webhooks", {
      name: "Update Test",
      url: "https://example.com/hook",
    });
    const id = create.body.webhook.webhook_id;

    const res = await request("PUT", `/webhooks/${id}`, {
      retry_count: 50,
    });
    assert.equal(res.status, 200);
    assert.ok(res.body.webhook.retry_count <= 10,
      `retry_count should be clamped to 10, got ${res.body.webhook.retry_count}`);
  });

  it("PUT /webhooks/:id - clamps timeout_ms on update", async () => {
    const create = await request("POST", "/webhooks", {
      name: "Timeout Update Test",
      url: "https://example.com/hook",
    });
    const id = create.body.webhook.webhook_id;

    const res = await request("PUT", `/webhooks/${id}`, {
      timeout_ms: 100000,
    });
    assert.equal(res.status, 200);
    assert.ok(res.body.webhook.timeout_ms <= 30000,
      `timeout_ms should be clamped to 30000, got ${res.body.webhook.timeout_ms}`);
  });

  it("PUT /webhooks/:id - rejects invalid format on update", async () => {
    const create = await request("POST", "/webhooks", {
      name: "Format Test",
      url: "https://example.com/hook",
    });
    const id = create.body.webhook.webhook_id;

    const res = await request("PUT", `/webhooks/${id}`, {
      format: "xml",
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("format"));
  });

  it("PUT /webhooks/:id - rejects oversized secret on update", async () => {
    const create = await request("POST", "/webhooks", {
      name: "Secret Update Test",
      url: "https://example.com/hook",
    });
    const id = create.body.webhook.webhook_id;

    const res = await request("PUT", `/webhooks/${id}`, {
      secret: "s".repeat(300),
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("secret"));
  });

  it("POST /webhooks - rejects loopback URL", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Loopback",
      url: "http://127.0.0.1:8080/hook",
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("loopback"));
  });

  it("POST /webhooks - rejects private network URL", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Private",
      url: "http://192.168.1.1/hook",
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("private"));
  });

  it("POST /webhooks - rejects metadata endpoint URL", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Metadata",
      url: "http://169.254.169.254/latest/meta-data/",
    });
    assert.equal(res.status, 400);
    assert.ok(res.body.error.includes("metadata"));
  });
});

/* ── Signature scheme tests (issue #185) ─────────────────────── */

// Replay-resistant signing: HMAC-SHA256 over `${timestamp}.${rawBody}`
// emitted as X-AgentLens-Signature: t=<ts>,v1=<hex> with a parallel
// X-AgentLens-Timestamp header. See backend/routes/webhooks.js.

describe("Webhook signature scheme (issue #185)", () => {
  const crypto = require("node:crypto");
  let webhookId;
  const secret = "test-secret-do-not-use-in-prod";

  function verifySignature(rawBody, headerValue, secretKey) {
    // Receiver-side recipe documented in docs/webhooks.md.
    const m = /^t=(\d+),v1=([0-9a-f]+)$/.exec(headerValue || "");
    if (!m) return { ok: false, reason: "malformed signature header" };
    const t = m[1];
    const v1 = m[2];
    const expected = crypto
      .createHmac("sha256", secretKey)
      .update(`${t}.${rawBody}`)
      .digest("hex");
    if (expected.length !== v1.length) return { ok: false, reason: "length mismatch" };
    const ok = crypto.timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(v1, "hex"));
    return { ok, t: Number(t), v1 };
  }

  it("creates a webhook with a secret", async () => {
    const res = await request("POST", "/webhooks", {
      name: "Signed",
      url: "https://example.com/signed-hook",
      format: "json",
      secret,
    });
    assert.equal(res.status, 201);
    webhookId = res.body.webhook.webhook_id;
  });

  it("emits X-AgentLens-Timestamp and t=...,v1=... signature", async () => {
    fetchCalls = [];
    const res = await request("POST", `/webhooks/${webhookId}/test`);
    assert.equal(res.status, 200);
    assert.equal(res.body.status, "success");
    assert.equal(fetchCalls.length, 1);

    const headers = fetchCalls[0].headers;
    const ts = headers["X-AgentLens-Timestamp"];
    const sig = headers["X-AgentLens-Signature"];

    assert.ok(ts, "X-AgentLens-Timestamp must be present");
    assert.match(ts, /^\d+$/, "timestamp must be Unix seconds");
    assert.ok(sig, "X-AgentLens-Signature must be present");
    assert.match(
      sig,
      /^t=\d+,v1=[0-9a-f]{64}$/,
      `expected Stripe-style t=...,v1=... signature, got ${sig}`,
    );

    // The timestamp inside the signature must match the parallel header.
    const inner = /^t=(\d+),/.exec(sig)[1];
    assert.equal(inner, ts, "t= in signature must match X-AgentLens-Timestamp header");

    // And it must be close to now (within 60s).
    const now = Math.floor(Date.now() / 1000);
    assert.ok(Math.abs(now - Number(ts)) < 60, `timestamp drifted: ${ts} vs ${now}`);
  });

  it("signature verifies against the raw body sent on the wire", async () => {
    fetchCalls = [];
    await request("POST", `/webhooks/${webhookId}/test`);
    const call = fetchCalls[0];
    const headers = call.headers;
    const rawBody = call.body; // exact bytes passed to fetch

    const result = verifySignature(rawBody, headers["X-AgentLens-Signature"], secret);
    assert.ok(result.ok, `signature did not verify against rawBody: ${result.reason || ""}`);
  });

  it("tampering with the body invalidates v1", async () => {
    fetchCalls = [];
    await request("POST", `/webhooks/${webhookId}/test`);
    const call = fetchCalls[0];
    const tampered = call.body + " "; // any byte change must break v1
    const result = verifySignature(tampered, call.headers["X-AgentLens-Signature"], secret);
    assert.equal(result.ok, false, "tampered body must not verify");
  });

  it("tampering with the timestamp invalidates v1", async () => {
    fetchCalls = [];
    await request("POST", `/webhooks/${webhookId}/test`);
    const call = fetchCalls[0];
    const original = call.headers["X-AgentLens-Signature"];
    const m = /^t=(\d+),v1=([0-9a-f]+)$/.exec(original);
    const shifted = `t=${Number(m[1]) + 3600},v1=${m[2]}`;
    const result = verifySignature(call.body, shifted, secret);
    assert.equal(result.ok, false, "shifted timestamp must not verify");
  });

  it("wrong secret invalidates v1", async () => {
    fetchCalls = [];
    await request("POST", `/webhooks/${webhookId}/test`);
    const call = fetchCalls[0];
    const result = verifySignature(call.body, call.headers["X-AgentLens-Signature"], "wrong-secret");
    assert.equal(result.ok, false, "wrong secret must not verify");
  });

  it("signature is NOT the legacy bare-hex format", async () => {
    fetchCalls = [];
    await request("POST", `/webhooks/${webhookId}/test`);
    const sig = fetchCalls[0].headers["X-AgentLens-Signature"];
    assert.ok(
      !/^[0-9a-f]{64}$/.test(sig),
      `expected timestamped signature, got legacy bare-hex: ${sig}`,
    );
  });
});
