# Webhook delivery & signature verification

AgentLens delivers outbound webhooks (alert fan-out, manual test
deliveries, etc.) over HTTPS as `POST` requests with a JSON body and
the following headers:

| Header | Example | Purpose |
| --- | --- | --- |
| `Content-Type` | `application/json` | The body is JSON. |
| `User-Agent` | `AgentLens-Webhook/1.0` | Identifies the sender. |
| `X-AgentLens-Delivery` | `lwf2k3o-9e4b1a` | Unique per delivery attempt, useful for de-duplication on the receiver. |
| `X-AgentLens-Timestamp` | `1747602840` | Unix timestamp **in seconds** at the moment the delivery was generated. |
| `X-AgentLens-Signature` | `t=1747602840,v1=2c4f...` | HMAC-SHA256 over `${timestamp}.${rawBody}`, keyed by the webhook secret. |

Signatures are only emitted when the webhook was created with a
`secret`. Webhooks without a secret are unsigned and **must not be
trusted** as authentic — only use them for non-sensitive notifications.

## Signing scheme

The signature header follows the
[Standard Webhooks](https://www.standardwebhooks.com/) and Stripe
conventions:

```
X-AgentLens-Signature: t=<unix-seconds>,v1=<hex-hmac-sha256>
```

Where `v1` is computed as:

```
v1 = HEX( HMAC-SHA256( secret, `${timestamp}.${rawBody}` ) )
```

- `timestamp` is the integer Unix seconds value also exposed in the
  `X-AgentLens-Timestamp` header.
- `rawBody` is the **exact bytes** of the request body, as shipped over
  the wire. Do not re-stringify a parsed JSON object before recomputing
  the signature — key ordering, whitespace, and Unicode escapes are not
  guaranteed to round-trip identically.

Binding the timestamp into the MAC is what makes the scheme
replay-resistant: a delivery captured from any source (compromised
endpoint, archived NGINX access log, TLS-stripping proxy) cannot be
re-fired after the receiver's tolerance window expires.

## Verification recipe

### Node.js

```js
const crypto = require("node:crypto");

function verifyAgentLensWebhook(rawBody, headers, secret, toleranceSec = 300) {
  const sigHeader = headers["x-agentlens-signature"] || "";
  const m = /^t=(\d+),v1=([0-9a-f]+)$/i.exec(sigHeader);
  if (!m) return { ok: false, reason: "malformed signature header" };

  const t = Number(m[1]);
  const v1 = m[2];

  // Reject deliveries outside the tolerance window.
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - t) > toleranceSec) {
    return { ok: false, reason: "timestamp outside tolerance window" };
  }

  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${t}.${rawBody}`)
    .digest("hex");

  const a = Buffer.from(expected, "hex");
  const b = Buffer.from(v1, "hex");
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return { ok: false, reason: "signature mismatch" };
  }
  return { ok: true };
}
```

The handler **must** read `rawBody` as a `Buffer` / string before any
JSON parsing — most frameworks (Express, Fastify, Koa) destroy the raw
bytes after body-parsing runs.

In Express, capture the raw body with `express.json({ verify })`:

```js
app.use(
  express.json({
    verify: (req, _res, buf) => {
      req.rawBody = buf.toString("utf8");
    },
  }),
);

app.post("/agentlens-webhook", (req, res) => {
  const result = verifyAgentLensWebhook(req.rawBody, req.headers, process.env.AL_SECRET);
  if (!result.ok) return res.status(401).json(result);
  // ... process req.body ...
  res.sendStatus(204);
});
```

### Python

```python
import hmac, hashlib, time

def verify_agentlens_webhook(raw_body: bytes, headers, secret: str, tolerance: int = 300):
    sig = headers.get("X-AgentLens-Signature", "")
    parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
    if "t" not in parts or "v1" not in parts:
        return False, "malformed signature header"

    t = int(parts["t"])
    if abs(int(time.time()) - t) > tolerance:
        return False, "timestamp outside tolerance window"

    signing_string = f"{t}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signing_string, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts["v1"]):
        return False, "signature mismatch"
    return True, "ok"
```

## Tolerance window

We recommend a tolerance of **5 minutes (300 seconds)** between the
timestamp embedded in the signature and the receiver's wall clock.
That tolerates normal clock skew while still aggressively rejecting
replays from archived deliveries.

Tighter (e.g. 60 s) is fine for receivers with strict NTP discipline.
Looser is discouraged.

## What gets signed

Only the request body is signed. URL, query string, and other headers
are **not** part of the MAC. This matches Stripe / GitHub / Slack and
keeps verification simple, but it means:

- The same `(body, signature, secret)` triple is valid against any
  receiver that shares the same secret. Use **unique secrets per
  webhook** if you fan out the same alerts to multiple subscribers.
- Receivers that proxy requests must forward the raw body verbatim;
  middleware that re-serializes JSON will break verification.

## Backward compatibility

Prior to issue #185, AgentLens emitted only the bare hex digest of
`JSON.stringify(payload)` in `X-AgentLens-Signature`, with no
`X-AgentLens-Timestamp` header. That format is no longer produced and
will not validate with the recipe above.

If you are running a verifier that supported the legacy format, switch
it over to the recipe in this document — there is no way to make the
new signature verify against the old recipe.
