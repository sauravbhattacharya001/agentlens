/* ── Webhook delivery-path tests ──────────────────────────────────── */

/**
 * Focused coverage for the delivery/failure branches of routes/webhooks.js
 * that the CRUD-oriented webhooks.test.js does not reach: the DNS-time SSRF
 * block, the retry loop over non-2xx responses (with exponential backoff),
 * fetch/timeout exceptions, oversized-response-body truncation, the
 * fireWebhooks() rule-scoping fan-out + its exported entry point, and the
 * delivery-history status filter.
 *
 * These are all driven through the public surface (POST /:id/test, the
 * exported fireWebhooks, GET /:id/deliveries) with a stubbed global.fetch,
 * so no real outbound HTTP is performed. Hosts are chosen so the real
 * delivery-time SSRF DNS check either passes (example.com → public) or
 * deterministically fails (a *.invalid host that cannot resolve).
 */

const assert = require("node:assert/strict");
const http = require("node:http");

// ── Stubbable fetch (per-test behaviour) ────────────────────────────
// `fetchImpl` is swapped by individual tests; default is a 200 OK.
let fetchCalls = [];
let fetchImpl = async () => ({ ok: true, status: 200, text: async () => "ok" });

global.fetch = async (url, opts) => {
  fetchCalls.push({ url, ...opts });
  return fetchImpl(url, opts);
};

process.env.DB_PATH = ":memory:";
const express = require("express");
const { getDb } = require("../db");
const webhooksRouter = require("../routes/webhooks");
const { fireWebhooks } = require("../routes/webhooks");

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

// Create a webhook directly in the DB so we can set fields (retry_count,
// rule_ids, enabled) that the create route clamps or the test needs precise.
function insertWebhook(fields) {
  const db = getDb();
  const now = new Date().toISOString();
  const w = {
    webhook_id: "wh-" + Math.random().toString(36).slice(2, 10),
    name: "delivery-test",
    url: "https://example.com/hook",
    secret: null,
    format: "json",
    rule_ids: null,
    enabled: 1,
    retry_count: 1,
    timeout_ms: 5000,
    ...fields,
  };
  db.prepare(`
    INSERT INTO webhooks (webhook_id, name, url, secret, format, rule_ids, enabled, retry_count, timeout_ms, created_at, updated_at)
    VALUES (@webhook_id, @name, @url, @secret, @format, @rule_ids, @enabled, @retry_count, @timeout_ms, @created_at, @updated_at)
  `).run({ ...w, created_at: now, updated_at: now });
  return w.webhook_id;
}

beforeAll(async () => {
  getDb();
  server = app.listen(0);
  // The webhooks table is created lazily by the route's ensureWebhooksTable
  // middleware on first request; warm it up before any direct insertWebhook.
  await request("GET", "/webhooks");
});

afterAll(() => {
  server.close();
});

beforeEach(() => {
  fetchCalls = [];
  fetchImpl = async () => ({ ok: true, status: 200, text: async () => "ok" });
});

describe("webhook delivery — SSRF DNS block at delivery time", () => {
  it("records a failed delivery (no fetch) when the host cannot resolve", async () => {
    // *.invalid is reserved (RFC 6761) and never resolves, so the delivery-time
    // validateResolvedIps() returns { safe: false } → recordFailedDelivery, and
    // fetch must never be called.
    const id = insertWebhook({ url: "https://nonexistent-host-abc123.invalid/hook" });
    const res = await request("POST", `/webhooks/${id}/test`);
    assert.equal(res.status, 200);
    assert.equal(res.body.status, "failed");
    assert.match(res.body.error, /SSRF/);
    assert.equal(fetchCalls.length, 0, "fetch must not fire when SSRF check blocks");

    const hist = await request("GET", `/webhooks/${id}/deliveries`);
    assert.equal(hist.body.deliveries[0].status, "failed");
    assert.equal(hist.body.deliveries[0].status_code, null);
  });
});

describe("webhook delivery — HTTP failure and retries", () => {
  it("retries on non-2xx and records HTTP <code> after exhausting attempts", async () => {
    let calls = 0;
    fetchImpl = async () => {
      calls += 1;
      return { ok: false, status: 503, text: async () => "unavailable" };
    };
    // retry_count=2 → one real 1s backoff between the two attempts.
    const id = insertWebhook({ retry_count: 2 });

    const res = await request("POST", `/webhooks/${id}/test`);

    assert.equal(res.body.status, "failed");
    assert.equal(res.body.error, "HTTP 503");
    assert.equal(res.body.attempts, 2);
    assert.equal(calls, 2, "should attempt exactly retry_count times");

    const hist = await request("GET", `/webhooks/${id}/deliveries?status=failed`);
    assert.equal(hist.body.deliveries[0].status_code, 503);
    assert.equal(hist.body.deliveries[0].response_body, "unavailable");
  }, 15000);

  it("succeeds on a later attempt after an initial failure", async () => {
    let calls = 0;
    fetchImpl = async () => {
      calls += 1;
      return calls === 1
        ? { ok: false, status: 500, text: async () => "err" }
        : { ok: true, status: 200, text: async () => "ok" };
    };
    const id = insertWebhook({ retry_count: 2 });

    const res = await request("POST", `/webhooks/${id}/test`);

    assert.equal(res.body.status, "success");
    assert.equal(res.body.attempts, 2);
    assert.equal(calls, 2);
  }, 15000);
});

describe("webhook delivery — fetch/timeout exceptions", () => {
  it("maps an AbortError to 'Timeout'", async () => {
    fetchImpl = async () => {
      const e = new Error("aborted");
      e.name = "AbortError";
      throw e;
    };
    const id = insertWebhook({ retry_count: 1 });
    const res = await request("POST", `/webhooks/${id}/test`);
    assert.equal(res.body.status, "failed");
    assert.equal(res.body.error, "Timeout");
  });

  it("records a generic fetch error message", async () => {
    fetchImpl = async () => {
      throw new Error("ECONNRESET");
    };
    const id = insertWebhook({ retry_count: 1 });
    const res = await request("POST", `/webhooks/${id}/test`);
    assert.equal(res.body.status, "failed");
    assert.equal(res.body.error, "ECONNRESET");
  });

  it("treats an unreadable response body as null but still succeeds on ok", async () => {
    fetchImpl = async () => ({
      ok: true,
      status: 200,
      text: async () => {
        throw new Error("stream error");
      },
    });
    const id = insertWebhook({ retry_count: 1 });
    const res = await request("POST", `/webhooks/${id}/test`);
    assert.equal(res.body.status, "success");

    const hist = await request("GET", `/webhooks/${id}/deliveries`);
    assert.equal(hist.body.deliveries[0].response_body, null);
  });

  it("truncates an oversized response body to 16 KB", async () => {
    const huge = "x".repeat(20000);
    fetchImpl = async () => ({ ok: true, status: 200, text: async () => huge });
    const id = insertWebhook({ retry_count: 1 });
    await request("POST", `/webhooks/${id}/test`);

    const hist = await request("GET", `/webhooks/${id}/deliveries`);
    const body = hist.body.deliveries[0].response_body;
    assert.ok(body.endsWith("...[truncated]"), "oversized body must be truncated");
    assert.ok(body.length <= 16384 + "...[truncated]".length);
  });
});

describe("webhook delivery — signed retry attaches HMAC headers", () => {
  it("signs each attempt when a secret is configured", async () => {
    fetchImpl = async () => ({ ok: true, status: 200, text: async () => "ok" });
    const id = insertWebhook({ secret: "s3cr3t", retry_count: 1 });
    await request("POST", `/webhooks/${id}/test`);
    assert.equal(fetchCalls.length, 1);
    const h = fetchCalls[0].headers;
    assert.ok(h["X-AgentLens-Signature"], "secret webhooks must be signed");
    assert.match(h["X-AgentLens-Signature"], /^t=\d+,v1=[0-9a-f]{64}$/);
    assert.ok(h["X-AgentLens-Delivery"], "delivery id header must be present");
  });
});

describe("fireWebhooks — rule scoping fan-out", () => {
  it("delivers to unscoped + matching webhooks and skips non-matching", async () => {
    fetchImpl = async () => ({ ok: true, status: 200, text: async () => "ok" });

    const unscoped = insertWebhook({ name: "all", rule_ids: null });
    const matching = insertWebhook({ name: "match", rule_ids: JSON.stringify(["rule-A"]) });
    const other = insertWebhook({ name: "other", rule_ids: JSON.stringify(["rule-B"]) });
    const empty = insertWebhook({ name: "empty-scope", rule_ids: JSON.stringify([]) });
    const disabled = insertWebhook({ name: "off", enabled: 0 });

    const results = await fireWebhooks({ alert_id: "a1", rule_id: "rule-A" });
    const firedIds = results.map((r) => r.webhook_id);

    assert.ok(firedIds.includes(unscoped), "unscoped webhook should fire");
    assert.ok(firedIds.includes(matching), "matching-rule webhook should fire");
    assert.ok(firedIds.includes(empty), "empty rule_ids array should fire (no scope)");
    assert.ok(!firedIds.includes(other), "non-matching-rule webhook must be skipped");
    assert.ok(!firedIds.includes(disabled), "disabled webhook must not fire");

    // Only assert delivery outcome for the webhooks this test created (the
    // shared in-memory DB may hold other enabled webhooks from prior tests).
    const mine = new Set([unscoped, matching, empty]);
    for (const r of results.filter((r) => mine.has(r.webhook_id))) {
      assert.equal(r.status, "success");
      assert.ok(r.name, "result carries the webhook name");
    }
  });
});

describe("GET /webhooks/:id/deliveries — status filter validation", () => {
  it("rejects an invalid status value", async () => {
    const id = insertWebhook({});
    const res = await request("GET", `/webhooks/${id}/deliveries?status=bogus`);
    assert.equal(res.status, 400);
    assert.match(res.body.error, /Invalid status/);
  });

  it("404s deliveries for an unknown webhook id", async () => {
    const res = await request("GET", "/webhooks/wh-doesnotexist/deliveries");
    assert.equal(res.status, 404);
  });

  it("honours the limit query param", async () => {
    fetchImpl = async () => ({ ok: true, status: 200, text: async () => "ok" });
    const id = insertWebhook({ retry_count: 1 });
    await request("POST", `/webhooks/${id}/test`);
    await request("POST", `/webhooks/${id}/test`);
    const res = await request("GET", `/webhooks/${id}/deliveries?limit=1`);
    assert.equal(res.body.deliveries.length, 1);
    assert.equal(res.body.count, 1);
  });
});
