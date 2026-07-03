/**
 * HMAC-SHA256 request signing for outbound webhook delivery.
 *
 * Extracted from routes/webhooks.js so the canonical signing string and the
 * emitted signature header format can be unit-tested directly instead of only
 * through a full HTTP delivery round trip.  Previously `signPayload` was
 * module-private in the route file and unexported, so the exact wire contract
 * that receivers must reproduce — the `${timestamp}.${rawBody}` canonical
 * string and the `t=<ts>,v1=<hex>` header shape (issue #185) — had *zero*
 * direct coverage.  A regression in either (e.g. dropping the timestamp from
 * the MAC, or changing the header separators) would only surface as a
 * signature-verification failure on the *receiver's* side, off-box and hard to
 * attribute — so it could ship silently.
 *
 * The signing scheme is replay-resistant and modelled on Stripe /
 * Standard-Webhooks: the Unix-seconds timestamp is bound into the MAC so a
 * receiver can reject replays of captured deliveries.  The delivery path in
 * routes/webhooks.js mirrors the same `t=...` value into a parallel
 * `X-AgentLens-Timestamp` header so receivers can recompute the canonical
 * string without first parsing the composite signature header.
 *
 * This module is a pure function of its arguments: no DNS, no IO, no clock —
 * the caller supplies the timestamp — so production behaviour is byte-for-byte
 * identical to the previous inline implementation.
 *
 * @module lib/webhook-signature
 */

const crypto = require("crypto");

/**
 * Sign a webhook payload with a replay-resistant HMAC-SHA256 signature.
 *
 * The canonical signing string is `${timestamp}.${rawBody}` — the timestamp is
 * bound into the MAC so receivers can reject replays of captured deliveries
 * (see issue #185).  `rawBody` MUST be the exact bytes shipped over the wire:
 * do not re-stringify the payload after signing, or verification on the
 * receiver will fail because the bytes will differ.
 *
 * The returned value is the `X-AgentLens-Signature` header content in a
 * Stripe-style composite form: `t=<timestamp>,v1=<hex>`, where `v1` is the
 * lowercase hex HMAC-SHA256 digest of the canonical string keyed by `secret`.
 *
 * @param {string} rawBody - The exact request-body bytes being sent on the wire.
 * @param {number|string} timestamp - Unix-seconds timestamp bound into the MAC;
 *   the caller also mirrors this into the `X-AgentLens-Timestamp` header.
 * @param {string} secret - The webhook's shared HMAC secret.
 * @returns {string} The signature header value, e.g. `t=1700000000,v1=<64 hex chars>`.
 */
function signPayload(rawBody, timestamp, secret) {
  // Stripe / Standard-Webhooks-style canonical signing string:
  //   `${timestamp}.${rawBody}`
  // Binding the timestamp into the MAC lets receivers reject replays of
  // captured deliveries (see issue #185). `rawBody` MUST be the exact
  // bytes shipped over the wire — do not re-stringify the payload after
  // signing, or verification on the receiver will fail.
  const signingString = `${timestamp}.${rawBody}`;
  const v1 = crypto.createHmac("sha256", secret).update(signingString).digest("hex");
  return `t=${timestamp},v1=${v1}`;
}

module.exports = {
  signPayload,
};
