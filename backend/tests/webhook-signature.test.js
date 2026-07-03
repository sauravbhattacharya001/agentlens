/**
 * Tests for lib/webhook-signature.js - the HMAC-SHA256 request signer
 * extracted from routes/webhooks.js (deliverWebhook).
 *
 * Before extraction, signPayload() was module-private in the route file and
 * unexported, so the exact wire contract that receivers must reproduce had NO
 * direct coverage: the `${timestamp}.${rawBody}` canonical signing string, the
 * `t=<ts>,v1=<hex>` composite header shape, and the security-critical property
 * that the timestamp is bound into the MAC (issue #185, replay resistance).
 * The existing webhooks.test.js "signature scheme" block exercises the full
 * delivery path (create webhook -> POST /test -> inspect fetch headers); these
 * pin the signer in isolation - deterministic, no DB, no fetch, no clock - so a
 * regression in the format or the timestamp binding is caught here at the unit
 * level rather than only surfacing as an off-box verification failure.
 */

const crypto = require("node:crypto");
const { signPayload } = require("../lib/webhook-signature");

// Independent receiver-side verifier, matching the recipe documented in
// docs/webhooks.md and mirrored in webhooks.test.js. Deliberately NOT sharing
// signPayload's internals so the test proves an outside party can verify.
function verifySignature(rawBody, headerValue, secretKey) {
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

const SECRET = "test-secret-do-not-use-in-prod";

// ===================================================================
// Header format - the exact wire contract receivers parse
// ===================================================================
describe("signPayload - header format", () => {
  test("returns a Stripe-style t=<ts>,v1=<hex> composite header", () => {
    const sig = signPayload('{"hello":"world"}', 1700000000, SECRET);
    expect(sig).toMatch(/^t=\d+,v1=[0-9a-f]{64}$/);
  });

  test("v1 is a lowercase 64-char hex SHA-256 digest", () => {
    const sig = signPayload("body", 1700000000, SECRET);
    const v1 = /,v1=([0-9a-f]+)$/.exec(sig)[1];
    expect(v1).toHaveLength(64);           // SHA-256 = 32 bytes = 64 hex chars
    expect(v1).toBe(v1.toLowerCase());
    expect(v1).toMatch(/^[0-9a-f]{64}$/);
  });

  test("the t= field echoes the supplied timestamp verbatim", () => {
    const sig = signPayload("body", 1700000000, SECRET);
    expect(sig.startsWith("t=1700000000,")).toBe(true);
  });

  test("uses a literal '.' separator in the canonical signing string", () => {
    // v1 must equal HMAC over `${ts}.${body}` exactly - assert against a
    // hand-computed reference so the separator can't drift to ':' or '|'.
    const ts = 1700000000;
    const body = "payload-bytes";
    const expected = crypto.createHmac("sha256", SECRET).update(`${ts}.${body}`).digest("hex");
    const sig = signPayload(body, ts, SECRET);
    expect(sig).toBe(`t=${ts},v1=${expected}`);
  });
});

// ===================================================================
// Determinism & key/message sensitivity
// ===================================================================
describe("signPayload - determinism and sensitivity", () => {
  test("is deterministic: same inputs -> identical signature", () => {
    const a = signPayload("body", 1700000000, SECRET);
    const b = signPayload("body", 1700000000, SECRET);
    expect(a).toBe(b);
  });

  test("a different secret yields a different v1", () => {
    const v1a = /,v1=([0-9a-f]+)$/.exec(signPayload("body", 1700000000, "secret-a"))[1];
    const v1b = /,v1=([0-9a-f]+)$/.exec(signPayload("body", 1700000000, "secret-b"))[1];
    expect(v1a).not.toBe(v1b);
  });

  test("a one-byte body change yields a different v1 (integrity)", () => {
    const v1a = /,v1=([0-9a-f]+)$/.exec(signPayload("body", 1700000000, SECRET))[1];
    const v1b = /,v1=([0-9a-f]+)$/.exec(signPayload("body ", 1700000000, SECRET))[1];
    expect(v1a).not.toBe(v1b);
  });

  test("a different timestamp yields a different v1 (timestamp is bound into the MAC)", () => {
    // This is the core replay-resistance property (issue #185): the timestamp
    // is not just a prefix, it is part of the signed message.
    const v1a = /,v1=([0-9a-f]+)$/.exec(signPayload("body", 1700000000, SECRET))[1];
    const v1b = /,v1=([0-9a-f]+)$/.exec(signPayload("body", 1700003600, SECRET))[1];
    expect(v1a).not.toBe(v1b);
  });
});

// ===================================================================
// Round-trip: an independent receiver can verify what we sign
// ===================================================================
describe("signPayload - receiver round-trip", () => {
  test("a signature verifies against the exact raw body signed", () => {
    const body = JSON.stringify({ alert: "high", value: 15.5 });
    const ts = 1700000000;
    const sig = signPayload(body, ts, SECRET);
    const result = verifySignature(body, sig, SECRET);
    expect(result.ok).toBe(true);
    expect(result.t).toBe(ts);
  });

  test("verification fails if the body is tampered after signing", () => {
    const body = JSON.stringify({ alert: "high" });
    const sig = signPayload(body, 1700000000, SECRET);
    const result = verifySignature(body + " ", sig, SECRET);
    expect(result.ok).toBe(false);
  });

  test("verification fails if the timestamp is shifted (replay defence)", () => {
    const body = JSON.stringify({ alert: "high" });
    const sig = signPayload(body, 1700000000, SECRET);
    const m = /^t=(\d+),v1=([0-9a-f]+)$/.exec(sig);
    const shifted = `t=${Number(m[1]) + 3600},v1=${m[2]}`;
    const result = verifySignature(body, shifted, SECRET);
    expect(result.ok).toBe(false);
  });

  test("verification fails under the wrong secret", () => {
    const body = JSON.stringify({ alert: "high" });
    const sig = signPayload(body, 1700000000, SECRET);
    const result = verifySignature(body, sig, "wrong-secret");
    expect(result.ok).toBe(false);
  });
});

// ===================================================================
// Edge cases - inputs the delivery path can legitimately produce
// ===================================================================
describe("signPayload - edge cases", () => {
  test("signs an empty body (still a valid, verifiable signature)", () => {
    const sig = signPayload("", 1700000000, SECRET);
    expect(sig).toMatch(/^t=1700000000,v1=[0-9a-f]{64}$/);
    expect(verifySignature("", sig, SECRET).ok).toBe(true);
  });

  test("accepts a string timestamp identically to the numeric form", () => {
    // deliverWebhook passes a number, but the canonical string is built via
    // template interpolation, so `1700000000` and `"1700000000"` MUST agree -
    // otherwise the parallel X-AgentLens-Timestamp header (a String(...)) could
    // desync from the t= inside the signature.
    const numeric = signPayload("body", 1700000000, SECRET);
    const stringy = signPayload("body", "1700000000", SECRET);
    expect(stringy).toBe(numeric);
  });

  test("handles a large JSON body (16 KB) and stays verifiable", () => {
    const body = JSON.stringify({ blob: "x".repeat(16 * 1024) });
    const ts = 1700000000;
    const sig = signPayload(body, ts, SECRET);
    expect(sig).toMatch(/^t=\d+,v1=[0-9a-f]{64}$/);
    expect(verifySignature(body, sig, SECRET).ok).toBe(true);
  });

  test("bodies containing the '.' separator char do not confuse verification", () => {
    // The signing string is `${ts}.${body}`; a body that itself contains dots
    // (any JSON with floats) must still verify, since the receiver splits on
    // the FIRST dot only via the t= capture, not on every dot.
    const body = JSON.stringify({ a: 1.5, b: 2.75, note: "x.y.z" });
    const sig = signPayload(body, 1700000000, SECRET);
    expect(verifySignature(body, sig, SECRET).ok).toBe(true);
  });
});
