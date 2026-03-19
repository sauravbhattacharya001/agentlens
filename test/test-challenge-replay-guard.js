/**
 * Tests for ChallengeReplayGuard
 */

"use strict";

var assert = require("assert");
var crypto = require("crypto");
var mod = require("../src/challenge-replay-guard");
var createChallengeReplayGuard = mod.createChallengeReplayGuard;

var passCount = 0;
var failCount = 0;
var totalTests = 0;

function test(name, fn) {
  totalTests++;
  try {
    fn();
    passCount++;
    console.log("  \x1b[32m✓\x1b[0m " + name);
  } catch (e) {
    failCount++;
    console.log("  \x1b[31m✗\x1b[0m " + name);
    console.log("    " + e.message);
  }
}

function assertThrows(fn, msgMatch) {
  var threw = false;
  try { fn(); } catch (e) {
    threw = true;
    if (msgMatch && e.message.indexOf(msgMatch) === -1) {
      throw new Error("Expected error containing '" + msgMatch + "', got: " + e.message);
    }
  }
  if (!threw) throw new Error("Expected function to throw");
}

console.log("\n=== ChallengeReplayGuard Tests ===\n");

// ── Construction ──

console.log("Construction:");

test("creates with defaults", function () {
  var guard = createChallengeReplayGuard();
  var config = guard.getConfig();
  assert.strictEqual(config.ttlMs, 120000);
  assert.strictEqual(config.algorithm, "sha256");
  assert.strictEqual(config.nonceBytes, 16);
  assert.strictEqual(config.strictTiming, true);
});

test("creates with custom options", function () {
  var guard = createChallengeReplayGuard({
    ttlMs: 60000,
    maxNonces: 100,
    nonceBytes: 32,
    strictTiming: false,
  });
  var config = guard.getConfig();
  assert.strictEqual(config.ttlMs, 60000);
  assert.strictEqual(config.maxNonces, 100);
  assert.strictEqual(config.nonceBytes, 32);
  assert.strictEqual(config.strictTiming, false);
});

test("creates with custom secret", function () {
  var guard = createChallengeReplayGuard({ secret: "my-secret-key" });
  var result = guard.issueToken("ch1");
  assert.ok(result.token);
});

// ── Token Issuance ──

console.log("\nToken Issuance:");

test("issues valid token", function () {
  var guard = createChallengeReplayGuard();
  var result = guard.issueToken("challenge-1");
  assert.ok(result.token);
  assert.ok(result.nonce);
  assert.ok(result.issuedAt > 0);
  assert.ok(result.expiresAt > result.issuedAt);
});

test("token contains dot separator", function () {
  var guard = createChallengeReplayGuard();
  var result = guard.issueToken("ch1");
  assert.ok(result.token.indexOf(".") > 0);
});

test("each token has unique nonce", function () {
  var guard = createChallengeReplayGuard();
  var t1 = guard.issueToken("ch1");
  var t2 = guard.issueToken("ch1");
  assert.notStrictEqual(t1.nonce, t2.nonce);
  assert.notStrictEqual(t1.token, t2.token);
});

test("rejects empty challengeId", function () {
  var guard = createChallengeReplayGuard();
  assertThrows(function () { guard.issueToken(""); }, "non-empty string");
  assertThrows(function () { guard.issueToken(null); }, "non-empty string");
});

test("token expiresAt matches TTL", function () {
  var guard = createChallengeReplayGuard({ ttlMs: 5000 });
  var result = guard.issueToken("ch1");
  var diff = result.expiresAt - result.issuedAt;
  assert.strictEqual(diff, 5000);
});

test("issues token with metadata", function () {
  var guard = createChallengeReplayGuard();
  var result = guard.issueToken("ch1", { userId: "u1", ip: "1.2.3.4" });
  assert.ok(result.token);
});

// ── Batch Issuance ──

console.log("\nBatch Issuance:");

test("issues batch tokens", function () {
  var guard = createChallengeReplayGuard();
  var batch = guard.issueBatch(["ch1", "ch2", "ch3"]);
  assert.strictEqual(batch.length, 3);
  assert.strictEqual(batch[0].challengeId, "ch1");
  assert.strictEqual(batch[1].challengeId, "ch2");
  assert.strictEqual(batch[2].challengeId, "ch3");
});

test("batch tokens all unique", function () {
  var guard = createChallengeReplayGuard();
  var batch = guard.issueBatch(["ch1", "ch2", "ch3"]);
  var nonces = batch.map(function (b) { return b.nonce; });
  var unique = new Set(nonces);
  assert.strictEqual(unique.size, 3);
});

test("rejects empty batch", function () {
  var guard = createChallengeReplayGuard();
  assertThrows(function () { guard.issueBatch([]); }, "non-empty array");
});

// ── Token Consumption ──

console.log("\nToken Consumption:");

test("consumes valid token", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  var result = guard.consume(issued.token);
  assert.strictEqual(result.valid, true);
  assert.strictEqual(result.challengeId, "ch1");
  assert.strictEqual(result.nonce, issued.nonce);
});

test("rejects already consumed token", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  guard.consume(issued.token);
  var result = guard.consume(issued.token);
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "already_used");
});

test("rejects tampered token", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  var tampered = issued.token.slice(0, -5) + "xxxxx";
  var result = guard.consume(tampered);
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "invalid_signature");
});

test("rejects null/empty token", function () {
  var guard = createChallengeReplayGuard();
  assert.strictEqual(guard.consume(null).error, "invalid_format");
  assert.strictEqual(guard.consume("").error, "invalid_format");
  assert.strictEqual(guard.consume("no-dot").error, "invalid_format");
});

test("rejects token with wrong secret", function () {
  var g1 = createChallengeReplayGuard({ secret: "secret-1" });
  var g2 = createChallengeReplayGuard({ secret: "secret-2" });
  var issued = g1.issueToken("ch1");
  var result = g2.consume(issued.token);
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "invalid_signature");
});

test("consumes with expected challengeId match", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  var result = guard.consume(issued.token, "ch1");
  assert.strictEqual(result.valid, true);
});

test("rejects challenge mismatch", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  var result = guard.consume(issued.token, "ch2");
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "challenge_mismatch");
});

test("preserves metadata on consume", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1", { userId: "u42" });
  var result = guard.consume(issued.token);
  assert.strictEqual(result.valid, true);
  assert.deepStrictEqual(result.meta, { userId: "u42" });
});

// ── Expiry ──

console.log("\nExpiry:");

test("rejects expired token", function () {
  var guard = createChallengeReplayGuard({ ttlMs: 1 });
  var issued = guard.issueToken("ch1");
  // Token expires after 1ms - wait a bit
  var start = Date.now();
  while (Date.now() - start < 10) { /* spin */ }
  var result = guard.consume(issued.token);
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "expired");
});

// ── Introspection ──

console.log("\nIntrospection:");

test("introspects valid token without consuming", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1", { foo: "bar" });
  var info = guard.introspect(issued.token);
  assert.strictEqual(info.valid, true);
  assert.strictEqual(info.challengeId, "ch1");
  assert.strictEqual(info.consumed, false);
  assert.strictEqual(info.expired, false);
  assert.deepStrictEqual(info.meta, { foo: "bar" });
  // Should still be consumable
  var result = guard.consume(issued.token);
  assert.strictEqual(result.valid, true);
});

test("introspects consumed token", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  guard.consume(issued.token);
  var info = guard.introspect(issued.token);
  assert.strictEqual(info.valid, false);
  assert.strictEqual(info.consumed, true);
});

test("introspects invalid token", function () {
  var guard = createChallengeReplayGuard();
  var info = guard.introspect("garbage");
  assert.strictEqual(info.valid, false);
});

// ── isConsumed ──

console.log("\nisConsumed:");

test("tracks nonce consumption", function () {
  var guard = createChallengeReplayGuard();
  var issued = guard.issueToken("ch1");
  assert.strictEqual(guard.isConsumed(issued.nonce), false);
  guard.consume(issued.token);
  assert.strictEqual(guard.isConsumed(issued.nonce), true);
});

// ── Nonce Eviction ──

console.log("\nNonce Eviction:");

test("evicts oldest nonces when exceeding max", function () {
  var guard = createChallengeReplayGuard({ maxNonces: 5 });
  var tokens = [];
  for (var i = 0; i < 7; i++) {
    tokens.push(guard.issueToken("ch" + i));
  }
  // Consume all 7
  for (var i = 0; i < 7; i++) {
    guard.consume(tokens[i].token);
  }
  // First 2 should have been evicted, so their nonces aren't tracked
  assert.strictEqual(guard.isConsumed(tokens[0].nonce), false);
  assert.strictEqual(guard.isConsumed(tokens[1].nonce), false);
  // Recent ones should still be tracked
  assert.strictEqual(guard.isConsumed(tokens[6].nonce), true);
});

// ── purgeExpired ──

console.log("\npurgeExpired:");

test("purges expired nonces", function () {
  var guard = createChallengeReplayGuard({ ttlMs: 1 });
  var issued = guard.issueToken("ch1");
  guard.consume(issued.token);
  // Wait for expiry
  var start = Date.now();
  while (Date.now() - start < 10) { /* spin */ }
  var purged = guard.purgeExpired();
  assert.ok(purged >= 1);
});

// ── Statistics ──

console.log("\nStatistics:");

test("tracks issuance stats", function () {
  var guard = createChallengeReplayGuard();
  guard.issueToken("ch1");
  guard.issueToken("ch2");
  var stats = guard.getStats();
  assert.strictEqual(stats.tokensIssued, 2);
  assert.strictEqual(stats.tokensConsumed, 0);
  assert.strictEqual(stats.tokensRejected, 0);
});

test("tracks consumption and rejection stats", function () {
  var guard = createChallengeReplayGuard();
  var t1 = guard.issueToken("ch1");
  guard.consume(t1.token); // success
  guard.consume(t1.token); // replay → rejected
  guard.consume("bad.token"); // invalid sig
  var stats = guard.getStats();
  assert.strictEqual(stats.tokensConsumed, 1);
  assert.strictEqual(stats.tokensRejected, 2);
  assert.strictEqual(stats.rejectionReasons.already_used, 1);
  assert.strictEqual(stats.rejectionReasons.invalid_signature, 1);
});

test("computes consume and reject rates", function () {
  var guard = createChallengeReplayGuard();
  var t1 = guard.issueToken("ch1");
  var t2 = guard.issueToken("ch2");
  guard.consume(t1.token);
  guard.consume(t1.token); // replay
  var stats = guard.getStats();
  assert.strictEqual(stats.consumeRate, 0.5);
  assert.strictEqual(stats.rejectRate, 0.5);
});

// ── Report ──

console.log("\nReport:");

test("generates health report", function () {
  var guard = createChallengeReplayGuard();
  var t1 = guard.issueToken("ch1");
  guard.consume(t1.token);
  var report = guard.generateReport();
  assert.strictEqual(report.health, "healthy");
  assert.ok(report.stats);
  assert.ok(report.config);
  assert.ok(Array.isArray(report.issues));
});

test("reports forged token attempts", function () {
  var guard = createChallengeReplayGuard();
  guard.issueToken("ch1");
  guard.consume("forged.token");
  var report = guard.generateReport();
  assert.ok(report.issues.some(function (i) { return i.severity === "error"; }));
  assert.strictEqual(report.health, "degraded");
});

test("reports high replay attempts", function () {
  var guard = createChallengeReplayGuard();
  var t = guard.issueToken("ch1");
  guard.consume(t.token);
  // Replay many times
  for (var i = 0; i < 5; i++) guard.consume(t.token);
  var report = guard.generateReport();
  assert.ok(report.issues.some(function (i) {
    return i.message.indexOf("replay") !== -1;
  }));
});

// ── State Export/Import ──

console.log("\nState Export/Import:");

test("exports and imports state", function () {
  var guard = createChallengeReplayGuard({ secret: "test-key" });
  var t1 = guard.issueToken("ch1");
  var t2 = guard.issueToken("ch2");
  guard.consume(t1.token);
  var exported = guard.exportState();
  assert.ok(exported.nonces.length >= 1);
  assert.ok(exported.stats.tokensIssued === 2);

  // New guard with same secret, import state
  var guard2 = createChallengeReplayGuard({ secret: "test-key" });
  var restored = guard2.importState(exported);
  assert.ok(restored >= 1);
  // Replay should be blocked in new guard
  var result = guard2.consume(t1.token);
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.error, "already_used");
  // Unused token should still work
  var result2 = guard2.consume(t2.token);
  assert.strictEqual(result2.valid, true);
});

test("import rejects invalid state", function () {
  var guard = createChallengeReplayGuard();
  assertThrows(function () { guard.importState(null); }, "state must be an object");
});

// ── Reset ──

console.log("\nReset:");

test("reset clears all state", function () {
  var guard = createChallengeReplayGuard();
  var t = guard.issueToken("ch1");
  guard.consume(t.token);
  guard.reset();
  var stats = guard.getStats();
  assert.strictEqual(stats.tokensIssued, 0);
  assert.strictEqual(stats.tokensConsumed, 0);
  assert.strictEqual(stats.activeNonces, 0);
});

// ── Edge Cases ──

console.log("\nEdge Cases:");

test("handles special characters in challengeId", function () {
  var guard = createChallengeReplayGuard();
  var t = guard.issueToken("ch|with|pipes");
  var result = guard.consume(t.token);
  // JSON-encoded payload preserves the full challengeId including pipes
  assert.strictEqual(result.valid, true);
  assert.strictEqual(result.challengeId, "ch|with|pipes");
});

test("handles challengeId with JSON-special characters", function () {
  var guard = createChallengeReplayGuard();
  var t = guard.issueToken('ch"with\\quotes');
  var result = guard.consume(t.token);
  assert.strictEqual(result.valid, true);
  assert.strictEqual(result.challengeId, 'ch"with\\quotes');
});

test("high-volume issuance", function () {
  var guard = createChallengeReplayGuard({ maxNonces: 100 });
  for (var i = 0; i < 200; i++) {
    var t = guard.issueToken("ch" + i);
    guard.consume(t.token);
  }
  var stats = guard.getStats();
  assert.strictEqual(stats.tokensIssued, 200);
  assert.strictEqual(stats.tokensConsumed, 200);
  assert.ok(stats.activeNonces <= 100);
});

test("concurrent token issuance for same challenge", function () {
  var guard = createChallengeReplayGuard();
  var t1 = guard.issueToken("ch1");
  var t2 = guard.issueToken("ch1");
  // Both should be independently consumable
  assert.strictEqual(guard.consume(t1.token).valid, true);
  assert.strictEqual(guard.consume(t2.token).valid, true);
  // But not twice
  assert.strictEqual(guard.consume(t1.token).valid, false);
  assert.strictEqual(guard.consume(t2.token).valid, false);
});

// ── Summary ──

console.log("\n" + "─".repeat(45));
console.log(
  passCount + " passing, " + failCount + " failing (" + totalTests + " total)"
);
if (failCount > 0) process.exit(1);
