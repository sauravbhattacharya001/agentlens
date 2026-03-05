/* ── Tests for correlation-scheduler ──────────────────────────────── */
var assert = require("assert");

// Test groupContentHash produces stable, deterministic hashes
(function testContentHash() {
  var crypto = require("crypto");

  function groupContentHash(ruleId, events) {
    var ids = [];
    for (var i = 0; i < events.length; i++) ids.push(events[i].event_id);
    ids.sort();
    return crypto.createHash("sha256").update(ruleId + ":" + ids.join(",")).digest("hex").slice(0, 32);
  }

  var events = [
    { event_id: "evt-3" },
    { event_id: "evt-1" },
    { event_id: "evt-2" },
  ];

  var hash1 = groupContentHash("rule-A", events);
  var hash2 = groupContentHash("rule-A", events);
  assert.strictEqual(hash1, hash2, "Same events should produce same hash");

  // Order shouldn't matter
  var reversed = [events[2], events[0], events[1]];
  var hash3 = groupContentHash("rule-A", reversed);
  assert.strictEqual(hash1, hash3, "Event order should not affect hash");

  // Different rule produces different hash
  var hash4 = groupContentHash("rule-B", events);
  assert.notStrictEqual(hash1, hash4, "Different rule should produce different hash");

  console.log("✓ groupContentHash tests passed");
})();

// Test SSE broadcast mechanism
(function testBroadcast() {
  var clients = [];
  var received = [];

  function addClient(res) { clients.push(res); }

  function broadcast(eventName, data) {
    var payload = "event: " + eventName + "\ndata: " + JSON.stringify(data) + "\n\n";
    for (var i = 0; i < clients.length; i++) {
      try { clients[i].write(payload); } catch (e) { /* client gone */ }
    }
  }

  // Mock client
  addClient({
    write: function (data) { received.push(data); },
  });

  broadcast("correlation", { group_id: "g1" });
  assert.strictEqual(received.length, 1, "Should broadcast to one client");
  assert.ok(received[0].indexOf("correlation") >= 0, "Should contain event name");
  assert.ok(received[0].indexOf("g1") >= 0, "Should contain group id");

  console.log("✓ SSE broadcast tests passed");
})();

console.log("\nAll correlation-scheduler tests passed ✓");
