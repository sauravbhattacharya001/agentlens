/**
 * Tests for lib/ssrf-guard.js - the runtime SSRF guard extracted from
 * routes/webhooks.js (deliverWebhook).
 *
 * Before extraction, isBlockedIp() and validateResolvedIps() were module-
 * private in the route file and unexported, so the private-range table, the
 * IPv6 prefix rules, and the "block on any resolution failure" DNS-rebinding
 * policy had NO direct coverage - a regression in a single range (e.g. adding
 * a CGN carve-out or dropping the multicast guard) would ship silently. These
 * exercise both functions directly; validateResolvedIps takes injectable
 * resolvers so DNS-rebinding paths are deterministic without live DNS.
 */

const { isBlockedIp, validateResolvedIps } = require("../lib/ssrf-guard");

// ===================================================================
// isBlockedIp - IPv4 internal / reserved ranges (must all be blocked)
// ===================================================================
describe("isBlockedIp - blocked IPv4 ranges", () => {
  test.each([
    ["loopback 127.0.0.1", "127.0.0.1"],
    ["loopback high 127.255.255.255", "127.255.255.255"],
    ["this-network 0.0.0.0", "0.0.0.0"],
    ["this-network 0.1.2.3", "0.1.2.3"],
    ["private 10.0.0.0/8 low", "10.0.0.0"],
    ["private 10.0.0.0/8 high", "10.255.255.255"],
    ["private 172.16.0.0/12 low", "172.16.0.1"],
    ["private 172.16.0.0/12 high", "172.31.255.255"],
    ["private 192.168.0.0/16", "192.168.1.1"],
    ["link-local 169.254.0.0/16", "169.254.1.1"],
    ["cloud metadata 169.254.169.254", "169.254.169.254"],
    ["CGN 100.64.0.0/10 low", "100.64.0.0"],
    ["CGN 100.64.0.0/10 high", "100.127.255.255"],
    ["multicast 224.0.0.1", "224.0.0.1"],
    ["reserved 240.0.0.1", "240.0.0.1"],
    ["broadcast 255.255.255.255", "255.255.255.255"],
  ])("blocks %s", (_label, ip) => {
    expect(isBlockedIp(ip)).toBe(true);
  });
});

// ===================================================================
// isBlockedIp - IPv6 internal forms (must all be blocked)
// ===================================================================
describe("isBlockedIp - blocked IPv6 forms", () => {
  test.each([
    ["loopback ::1", "::1"],
    ["unspecified ::", "::"],
    ["link-local fe80::", "fe80::1"],
    ["link-local uppercase FE80", "FE80::abcd"],
    ["unique-local fc00::", "fc00::1"],
    ["unique-local fd00::", "fd12:3456:789a::1"],
    ["IPv4-mapped ::ffff:127.0.0.1", "::ffff:127.0.0.1"],
    ["IPv4-mapped ::ffff:10.0.0.1", "::ffff:10.0.0.1"],
  ])("blocks %s", (_label, ip) => {
    expect(isBlockedIp(ip)).toBe(true);
  });
});

// ===================================================================
// isBlockedIp - public / routable addresses (must be allowed)
// ===================================================================
describe("isBlockedIp - allowed public addresses", () => {
  test.each([
    ["public 8.8.8.8", "8.8.8.8"],
    ["public 1.1.1.1", "1.1.1.1"],
    ["public 93.184.216.34 (example.com)", "93.184.216.34"],
    ["public 172.15.x (just below private)", "172.15.255.255"],
    ["public 172.32.x (just above private)", "172.32.0.1"],
    ["public 11.0.0.1 (just above 10/8)", "11.0.0.1"],
    ["public 192.169.0.1 (just above 192.168/16)", "192.169.0.1"],
    ["public 100.63.255.255 (just below CGN)", "100.63.255.255"],
    ["public 100.128.0.0 (just above CGN)", "100.128.0.0"],
    ["public 223.255.255.255 (just below multicast)", "223.255.255.255"],
    ["public IPv6 2606:4700::1111 (Cloudflare)", "2606:4700:4700::1111"],
    ["public IPv6 2001:4860::8888 (Google)", "2001:4860:4860::8888"],
  ])("allows %s", (_label, ip) => {
    expect(isBlockedIp(ip)).toBe(false);
  });

  test("empty / clearly-non-IP input is not classified as blocked", () => {
    expect(isBlockedIp("")).toBe(false);
    expect(isBlockedIp("not-an-ip")).toBe(false);
    expect(isBlockedIp("example.com")).toBe(false);
  });
});

// ===================================================================
// isBlockedIp - raw-string caveat (documents a real, deliberate property)
// ===================================================================
// isBlockedIp is a fail-safe classifier fed ONLY resolved IP literals by
// validateResolvedIps (and, at registration time, hostnames are screened by
// validateWebhookUrl in lib/validation.js, which has the DNS-name carve-outs
// for fcc.gov / fdic.gov etc.). So isBlockedIp errs on the side of blocking
// for ambiguous strings - these tests pin that documented behaviour so a
// future refactor cannot loosen it by accident.
describe("isBlockedIp - fail-safe on ambiguous raw strings", () => {
  test("treats any string beginning fc/fd as unique-local (IPv6 ULA prefix match)", () => {
    // Only reachable with a raw hostname; in the real path these are IP
    // literals or already screened. Blocking here is the safe default.
    expect(isBlockedIp("fd00::1")).toBe(true);
    expect(isBlockedIp("fdic.example")).toBe(true);
    expect(isBlockedIp("fcc")).toBe(true);
  });

  test("an out-of-range dotted quad still matches the IPv4 shape and is blocked as reserved", () => {
    // 999.999.999.999 matches /^(\d{1,3}\.){3}\d{1,3}$/ with a=999 >= 224,
    // so it is treated as reserved/multicast and blocked - a safe default for
    // a value that is not a valid routable address anyway.
    expect(isBlockedIp("999.999.999.999")).toBe(true);
  });
});

// ===================================================================
// validateResolvedIps - direct IP literals (no DNS needed)
// ===================================================================
describe("validateResolvedIps - direct IP literals", () => {
  test("blocks a directly-supplied internal IPv4 literal", async () => {
    const r = await validateResolvedIps("10.0.0.5");
    expect(r).toEqual({ safe: false, error: "Resolved IP is a blocked address" });
  });

  test("blocks a directly-supplied internal IPv6 literal", async () => {
    const r = await validateResolvedIps("::1");
    expect(r).toEqual({ safe: false, error: "Resolved IP is a blocked address" });
  });

  test("allows a directly-supplied public IPv4 literal (no resolver call)", async () => {
    let called = false;
    const r = await validateResolvedIps("8.8.8.8", {
      resolve4: async () => { called = true; return []; },
      resolve6: async () => { called = true; return []; },
    });
    expect(r).toEqual({ safe: true });
    expect(called).toBe(false); // literal path short-circuits before DNS
  });

  test("allows a directly-supplied public IPv6 literal", async () => {
    const r = await validateResolvedIps("2606:4700:4700::1111");
    expect(r).toEqual({ safe: true });
  });
});

// ===================================================================
// validateResolvedIps - DNS rebinding via injected resolvers
// ===================================================================
describe("validateResolvedIps - DNS rebinding defence", () => {
  test("allows a hostname that resolves only to public A records", async () => {
    const r = await validateResolvedIps("example.com", {
      resolve4: async () => ["93.184.216.34"],
      resolve6: async () => { throw new Error("no AAAA"); },
    });
    expect(r).toEqual({ safe: true });
  });

  test("blocks a hostname that resolves to an internal A record (rebinding)", async () => {
    const r = await validateResolvedIps("rebind.evil.test", {
      resolve4: async () => ["169.254.169.254"],
      resolve6: async () => { throw new Error("no AAAA"); },
    });
    expect(r).toEqual({ safe: false, error: "DNS resolved to blocked IP: 169.254.169.254" });
  });

  test("blocks when ANY resolved address is internal, even if others are public", async () => {
    const r = await validateResolvedIps("mixed.test", {
      resolve4: async () => ["93.184.216.34", "10.1.2.3"],
      resolve6: async () => { throw new Error("no AAAA"); },
    });
    expect(r).toEqual({ safe: false, error: "DNS resolved to blocked IP: 10.1.2.3" });
  });

  test("blocks a hostname that resolves to an internal AAAA record", async () => {
    const r = await validateResolvedIps("v6rebind.test", {
      resolve4: async () => { throw new Error("no A"); },
      resolve6: async () => ["fd00::1"],
    });
    expect(r).toEqual({ safe: false, error: "DNS resolved to blocked IP: fd00::1" });
  });

  test("allows a hostname resolving to public A and public AAAA records", async () => {
    const r = await validateResolvedIps("dual.test", {
      resolve4: async () => ["93.184.216.34"],
      resolve6: async () => ["2606:4700:4700::1111"],
    });
    expect(r).toEqual({ safe: true });
  });

  test("blocks (does not silently allow) when NO records resolve", async () => {
    // The critical fail-closed policy: a permissive fallthrough would let a
    // rebinding or transient-resolution attack bypass the guard entirely.
    const r = await validateResolvedIps("nxdomain.test", {
      resolve4: async () => { throw new Error("ENOTFOUND"); },
      resolve6: async () => { throw new Error("ENOTFOUND"); },
    });
    expect(r).toEqual({ safe: false, error: "DNS resolution failed \u2014 no records found" });
  });

  test("blocks when resolvers return empty arrays (no throw, no records)", async () => {
    const r = await validateResolvedIps("empty.test", {
      resolve4: async () => [],
      resolve6: async () => [],
    });
    expect(r).toEqual({ safe: false, error: "DNS resolution failed \u2014 no records found" });
  });
});
