/**
 * SSRF (Server-Side Request Forgery) runtime guard for outbound webhook
 * delivery.
 *
 * Extracted from routes/webhooks.js so the IP-classification and DNS-rebinding
 * defence can be unit-tested directly instead of only through a full HTTP
 * delivery round trip.  Previously `isBlockedIp` and `validateResolvedIps`
 * were module-private in the route file and unexported, so the private-range
 * table, the IPv6 prefix rules, and the "block on any resolution failure"
 * policy had *zero* direct coverage — a regression in any single range could
 * ship silently.
 *
 * Two complementary layers guard outbound requests:
 *   1. `validateWebhookUrl` (lib/validation.js) rejects unsafe URLs at
 *      registration / parse time, working purely on the URL *string*.
 *   2. This module re-checks the destination at *delivery* time by resolving
 *      the hostname's live DNS records and blocking if any resolved address
 *      is internal.  This closes the DNS-rebinding window where a name is
 *      public when the webhook is saved but points at an internal / cloud
 *      metadata address when the request actually fires.
 *
 * `validateResolvedIps` accepts injectable resolvers so the rebinding logic
 * can be exercised with deterministic stub records; when omitted it uses the
 * real Node `dns.resolve4` / `dns.resolve6`, so production behaviour is
 * byte-for-byte identical to the previous inline implementation.
 *
 * @module lib/ssrf-guard
 */

const dns = require("dns");
const { promisify } = require("util");

const defaultResolve4 = promisify(dns.resolve4);
const defaultResolve6 = promisify(dns.resolve6);

/**
 * Classify a raw IP literal as internal / unsafe for outbound requests.
 *
 * Covers the loopback, private (RFC-1918), link-local / cloud-metadata,
 * Carrier-Grade NAT, multicast, and reserved IPv4 ranges, plus the IPv6
 * loopback, unspecified, link-local, unique-local, and IPv4-mapped forms.
 * Any input that is not one of those recognised internal forms — including a
 * routable public address or a malformed string — returns `false`.
 *
 * This is a pure function of its argument: no DNS, no IO.
 *
 * @param {string} ip - An IPv4 or IPv6 address literal.
 * @returns {boolean} `true` when the address is internal / must be blocked.
 */
function isBlockedIp(ip) {
  // IPv4 checks
  const v4 = ip.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [, a, b] = v4.map(Number);
    if (a === 127) return true;                                // loopback
    if (a === 0) return true;                                  // 0.0.0.0/8
    if (a === 10) return true;                                 // 10.0.0.0/8
    if (a === 172 && b >= 16 && b <= 31) return true;          // 172.16.0.0/12
    if (a === 192 && b === 168) return true;                   // 192.168.0.0/16
    if (a === 169 && b === 254) return true;                   // link-local / cloud metadata
    if (a === 100 && b >= 64 && b <= 127) return true;         // CGN 100.64.0.0/10
    if (a >= 224) return true;                                 // multicast + reserved
  }

  // IPv6 checks
  const lower = ip.toLowerCase();
  if (lower === "::1") return true;                            // loopback
  if (lower === "::") return true;                             // unspecified
  if (lower.startsWith("fe80:")) return true;                  // link-local
  if (lower.startsWith("fc") || lower.startsWith("fd")) return true;  // unique local
  if (lower.startsWith("::ffff:")) return true;                // IPv4-mapped

  return false;
}

/**
 * Validate that a hostname does not resolve to any internal address, to
 * defend against DNS rebinding at delivery time.
 *
 * When `hostname` is already an IP literal it is checked directly.  Otherwise
 * the hostname is resolved via both A and AAAA records and *every* returned
 * address must pass `isBlockedIp`.  If neither record type resolves — for any
 * reason — the destination is treated as unsafe: a permissive fallthrough
 * would let a rebinding or transient-failure attack bypass the guard.
 *
 * @param {string} hostname - Hostname or IP literal to validate.
 * @param {Object} [options] - Optional resolver overrides (for testing).
 * @param {(name: string) => Promise<string[]>} [options.resolve4] - A-record
 *   resolver; defaults to the real `dns.resolve4`.
 * @param {(name: string) => Promise<string[]>} [options.resolve6] - AAAA-record
 *   resolver; defaults to the real `dns.resolve6`.
 * @returns {Promise<{ safe: boolean, error?: string }>} `{ safe: true }` when
 *   every resolved address is public, otherwise `{ safe: false, error }`.
 */
async function validateResolvedIps(hostname, options = {}) {
  const resolve4 = options.resolve4 || defaultResolve4;
  const resolve6 = options.resolve6 || defaultResolve6;

  // If hostname is already an IP, check it directly
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(hostname) || hostname.includes(":")) {
    if (isBlockedIp(hostname)) {
      return { safe: false, error: "Resolved IP is a blocked address" };
    }
    return { safe: true };
  }

  // Resolve DNS and check all returned IPs
  const ips = [];
  try {
    const v4 = await resolve4(hostname);
    ips.push(...v4);
  } catch { /* no A records */ }
  try {
    const v6 = await resolve6(hostname);
    ips.push(...v6);
  } catch { /* no AAAA records */ }

  if (ips.length === 0) {
    return { safe: false, error: "DNS resolution failed — no records found" };
  }

  for (const ip of ips) {
    if (isBlockedIp(ip)) {
      return { safe: false, error: `DNS resolved to blocked IP: ${ip}` };
    }
  }
  return { safe: true };
}

module.exports = {
  isBlockedIp,
  validateResolvedIps,
};
