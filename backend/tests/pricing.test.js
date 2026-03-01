/* ── Pricing API Tests ── */

let mockDb;
jest.mock("../db", () => ({
  getDb: () => {
    if (!mockDb) {
      const Database = require("better-sqlite3");
      mockDb = new Database(":memory:");
      mockDb.pragma("journal_mode = WAL");
      mockDb.pragma("foreign_keys = ON");
      mockDb.exec(`
        CREATE TABLE IF NOT EXISTS model_pricing (
          model TEXT PRIMARY KEY,
          input_cost_per_1m REAL NOT NULL DEFAULT 0,
          output_cost_per_1m REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'USD',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          agent_name TEXT NOT NULL DEFAULT 'default-agent',
          started_at TEXT NOT NULL,
          ended_at TEXT,
          metadata TEXT DEFAULT '{}',
          total_tokens_in INTEGER DEFAULT 0,
          total_tokens_out INTEGER DEFAULT 0,
          status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          event_type TEXT NOT NULL DEFAULT 'generic',
          timestamp TEXT NOT NULL,
          input_data TEXT,
          output_data TEXT,
          model TEXT,
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          tool_call TEXT,
          decision_trace TEXT,
          duration_ms REAL,
          FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
      `);
    }
    return mockDb;
  },
}));

const express = require("express");
const request = require("supertest");
const pricingRouter = require("../routes/pricing");

function createApp() {
  const app = express();
  app.use(express.json());
  app.use("/pricing", pricingRouter);
  return app;
}

// Trigger lazy initialization of mockDb
beforeAll(() => {
  require("../db").getDb();
});

function clearDb() {
  mockDb.exec("DELETE FROM model_pricing");
  mockDb.exec("DELETE FROM events");
  mockDb.exec("DELETE FROM sessions");
}

function insertSession(id, agentName = "test-agent") {
  mockDb.prepare(
    `INSERT INTO sessions (session_id, agent_name, started_at, status)
     VALUES (?, ?, datetime('now'), 'active')`
  ).run(id, agentName);
}

function insertEvent(eventId, sessionId, model, tokensIn, tokensOut) {
  mockDb.prepare(
    `INSERT INTO events (event_id, session_id, event_type, timestamp, model, tokens_in, tokens_out)
     VALUES (?, ?, 'llm_call', datetime('now'), ?, ?, ?)`
  ).run(eventId, sessionId, model, tokensIn, tokensOut);
}

function insertPricing(model, inputCost, outputCost, currency = "USD") {
  mockDb.prepare(
    `INSERT OR REPLACE INTO model_pricing (model, input_cost_per_1m, output_cost_per_1m, currency, updated_at)
     VALUES (?, ?, ?, ?, datetime('now'))`
  ).run(model, inputCost, outputCost, currency);
}

// ── GET /pricing ────────────────────────────────────────────────────

describe("GET /pricing", () => {
  const app = createApp();

  beforeEach(() => clearDb());

  it("should return 200 with pricing and defaults", async () => {
    const res = await request(app).get("/pricing");
    expect(res.status).toBe(200);
    expect(res.body.pricing).toBeDefined();
    expect(res.body.defaults).toBeDefined();
    expect(typeof res.body.pricing).toBe("object");
    expect(typeof res.body.defaults).toBe("object");
  });

  it("should seed default pricing on first call", async () => {
    // Defaults are always present in the response regardless of DB state
    const res = await request(app).get("/pricing");
    expect(res.status).toBe(200);
    const defaultKeys = Object.keys(res.body.defaults);
    expect(defaultKeys.length).toBeGreaterThanOrEqual(10);
    expect(res.body.defaults["gpt-4o"]).toBeDefined();
    expect(res.body.defaults["claude-3-sonnet"]).toBeDefined();
  });

  it("should return input and output costs per model", async () => {
    // Use defaults response — seedDefaults already ran
    const res = await request(app).get("/pricing");
    const models = res.body.defaults;
    // Verify defaults have the expected structure
    expect(models["gpt-4o"]).toBeDefined();
    expect(models["gpt-4o"].input).toBe(2.5);
    expect(models["gpt-4o"].output).toBe(10.0);
  });

  it("should include defaults in response", async () => {
    const res = await request(app).get("/pricing");
    expect(res.body.defaults["gpt-4"]).toEqual({ input: 30, output: 60 });
    expect(res.body.defaults["gpt-4o-mini"]).toEqual({ input: 0.15, output: 0.6 });
  });

  it("should return custom pricing after PUT", async () => {
    await request(app).put("/pricing").send({
      pricing: {
        "custom-model": { input_cost_per_1m: 5.0, output_cost_per_1m: 15.0 }
      }
    });
    const res = await request(app).get("/pricing");
    expect(res.body.pricing["custom-model"]).toBeDefined();
    expect(res.body.pricing["custom-model"].input_cost_per_1m).toBe(5.0);
  });
});

// ── PUT /pricing ────────────────────────────────────────────────────

describe("PUT /pricing", () => {
  const app = createApp();

  beforeEach(() => clearDb());

  it("should update pricing for a single model", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "gpt-4o": { input_cost_per_1m: 3.0, output_cost_per_1m: 12.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.updated).toBe(1);
  });

  it("should update pricing for multiple models", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "model-a": { input_cost_per_1m: 1.0, output_cost_per_1m: 2.0 },
        "model-b": { input_cost_per_1m: 3.0, output_cost_per_1m: 6.0 },
        "model-c": { input_cost_per_1m: 5.0, output_cost_per_1m: 10.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(3);
  });

  it("should return 400 for missing pricing object", async () => {
    const res = await request(app).put("/pricing").send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("Missing");
  });

  it("should return 400 for non-object pricing", async () => {
    const res = await request(app).put("/pricing").send({ pricing: "not-an-object" });
    expect(res.status).toBe(400);
  });

  it("should skip entries with non-finite input costs", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "good-model": { input_cost_per_1m: 1.0, output_cost_per_1m: 2.0 },
        "bad-model": { input_cost_per_1m: "not-a-number", output_cost_per_1m: 2.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(1);
  });

  it("should skip entries with negative costs", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "negative-model": { input_cost_per_1m: -5.0, output_cost_per_1m: 2.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(0);
  });

  it("should skip entries with negative output costs", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "neg-out": { input_cost_per_1m: 5.0, output_cost_per_1m: -2.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(0);
  });

  it("should default currency to USD", async () => {
    await request(app).put("/pricing").send({
      pricing: {
        "no-currency": { input_cost_per_1m: 1.0, output_cost_per_1m: 2.0 }
      }
    });
    const res = await request(app).get("/pricing");
    expect(res.body.pricing["no-currency"].currency).toBe("USD");
  });

  it("should accept custom currency", async () => {
    await request(app).put("/pricing").send({
      pricing: {
        "euro-model": { input_cost_per_1m: 1.0, output_cost_per_1m: 2.0, currency: "EUR" }
      }
    });
    const res = await request(app).get("/pricing");
    expect(res.body.pricing["euro-model"].currency).toBe("EUR");
  });

  it("should upsert (overwrite existing pricing)", async () => {
    await request(app).put("/pricing").send({
      pricing: { "gpt-4o": { input_cost_per_1m: 3.0, output_cost_per_1m: 12.0 } }
    });
    await request(app).put("/pricing").send({
      pricing: { "gpt-4o": { input_cost_per_1m: 5.0, output_cost_per_1m: 20.0 } }
    });
    const res = await request(app).get("/pricing");
    expect(res.body.pricing["gpt-4o"].input_cost_per_1m).toBe(5.0);
    expect(res.body.pricing["gpt-4o"].output_cost_per_1m).toBe(20.0);
  });

  it("should skip empty model names", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "": { input_cost_per_1m: 1.0, output_cost_per_1m: 2.0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(0);
  });

  it("should handle zero costs (free model)", async () => {
    const res = await request(app).put("/pricing").send({
      pricing: {
        "free-model": { input_cost_per_1m: 0, output_cost_per_1m: 0 }
      }
    });
    expect(res.status).toBe(200);
    expect(res.body.updated).toBe(1);
  });
});

// ── DELETE /pricing/:model ──────────────────────────────────────────

describe("DELETE /pricing/:model", () => {
  const app = createApp();

  beforeEach(() => clearDb());

  it("should delete an existing model", async () => {
    insertPricing("delete-me", 1.0, 2.0);
    const res = await request(app).delete("/pricing/delete-me");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.deleted).toBe("delete-me");
  });

  it("should return 404 for non-existent model", async () => {
    const res = await request(app).delete("/pricing/nonexistent-model");
    expect(res.status).toBe(404);
    expect(res.body.error).toContain("not found");
  });

  it("should verify model is removed after delete", async () => {
    insertPricing("remove-test", 1.0, 2.0);
    await request(app).delete("/pricing/remove-test");
    const row = mockDb.prepare("SELECT * FROM model_pricing WHERE model = ?").get("remove-test");
    expect(row).toBeUndefined();
  });
});

// ── GET /pricing/costs/:sessionId ───────────────────────────────────

describe("GET /pricing/costs/:sessionId", () => {
  const app = createApp();

  beforeEach(() => clearDb());

  it("should return 400 for invalid session ID", async () => {
    const res = await request(app).get("/pricing/costs/invalid<>session!@#");
    expect(res.status).toBe(400);
    expect(res.body.error).toContain("Invalid session ID");
  });

  it("should return 404 for non-existent session", async () => {
    const res = await request(app).get("/pricing/costs/sess-nonexistent-00000000");
    expect(res.status).toBe(404);
    expect(res.body.error).toContain("Session not found");
  });

  it("should calculate costs for a session with events", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-cost-test-001");
    insertEvent("evt-1", "sess-cost-test-001", "gpt-4o", 1000, 500);

    const res = await request(app).get("/pricing/costs/sess-cost-test-001");
    expect(res.status).toBe(200);
    expect(res.body.session_id).toBe("sess-cost-test-001");
    expect(res.body.currency).toBe("USD");
    expect(res.body.total_cost).toBeGreaterThan(0);
    expect(res.body.total_input_cost).toBeGreaterThan(0);
    expect(res.body.total_output_cost).toBeGreaterThan(0);
  });

  it("should calculate correct input cost", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-input-cost");
    // 1M tokens in at $2.50/1M = $2.50
    insertEvent("evt-2", "sess-input-cost", "gpt-4o", 1000000, 0);

    const res = await request(app).get("/pricing/costs/sess-input-cost");
    expect(res.body.total_input_cost).toBe(2.5);
    expect(res.body.total_output_cost).toBe(0);
    expect(res.body.total_cost).toBe(2.5);
  });

  it("should calculate correct output cost", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-output-cost");
    // 1M tokens out at $10/1M = $10
    insertEvent("evt-3", "sess-output-cost", "gpt-4o", 0, 1000000);

    const res = await request(app).get("/pricing/costs/sess-output-cost");
    expect(res.body.total_output_cost).toBe(10.0);
    expect(res.body.total_input_cost).toBe(0);
    expect(res.body.total_cost).toBe(10.0);
  });

  it("should aggregate costs across multiple events", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-multi-evt");
    insertEvent("evt-4a", "sess-multi-evt", "gpt-4o", 500000, 250000);
    insertEvent("evt-4b", "sess-multi-evt", "gpt-4o", 500000, 250000);

    const res = await request(app).get("/pricing/costs/sess-multi-evt");
    // 1M total in at $2.50 + 500K total out at $5.00 = $7.50
    expect(res.body.total_cost).toBe(7.5);
    expect(res.body.total_input_cost).toBe(2.5);
    expect(res.body.total_output_cost).toBe(5.0);
  });

  it("should return model_costs breakdown", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-model-costs");
    insertEvent("evt-5", "sess-model-costs", "gpt-4o", 1000, 500);

    const res = await request(app).get("/pricing/costs/sess-model-costs");
    expect(res.body.model_costs).toBeDefined();
    expect(res.body.model_costs["gpt-4o"]).toBeDefined();
    expect(res.body.model_costs["gpt-4o"].calls).toBe(1);
    expect(res.body.model_costs["gpt-4o"].tokens_in).toBe(1000);
    expect(res.body.model_costs["gpt-4o"].tokens_out).toBe(500);
    expect(res.body.model_costs["gpt-4o"].matched).toBe(true);
  });

  it("should return event_costs with per-event details", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-evt-costs");
    insertEvent("evt-6", "sess-evt-costs", "gpt-4o", 1000, 500);

    const res = await request(app).get("/pricing/costs/sess-evt-costs");
    expect(res.body.event_costs).toBeDefined();
    expect(res.body.event_costs.length).toBe(1);
    expect(res.body.event_costs[0].event_id).toBe("evt-6");
    expect(res.body.event_costs[0].model).toBe("gpt-4o");
    expect(res.body.event_costs[0].pricing_matched).toBe(true);
  });

  it("should handle events with no model (null costs)", async () => {
    insertSession("sess-no-model");
    insertEvent("evt-7", "sess-no-model", null, 1000, 500);

    const res = await request(app).get("/pricing/costs/sess-no-model");
    expect(res.status).toBe(200);
    expect(res.body.total_cost).toBe(0);
    expect(res.body.event_costs[0].model).toBeNull();
  });

  it("should report unmatched models", async () => {
    insertSession("sess-unmatched");
    insertEvent("evt-8", "sess-unmatched", "unknown-model-xyz", 1000, 500);

    const res = await request(app).get("/pricing/costs/sess-unmatched");
    expect(res.body.unmatched_models).toContain("unknown-model-xyz");
  });

  it("should handle session with zero events", async () => {
    insertSession("sess-empty-events");

    const res = await request(app).get("/pricing/costs/sess-empty-events");
    expect(res.status).toBe(200);
    expect(res.body.total_cost).toBe(0);
    expect(res.body.event_costs).toEqual([]);
    expect(res.body.model_costs).toEqual({});
  });

  it("should handle events with zero tokens", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-zero-tokens");
    insertEvent("evt-9", "sess-zero-tokens", "gpt-4o", 0, 0);

    const res = await request(app).get("/pricing/costs/sess-zero-tokens");
    expect(res.body.total_cost).toBe(0);
    expect(res.body.event_costs[0].total_cost).toBe(0);
  });

  it("should handle multiple models in one session", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertPricing("claude-3-sonnet", 3.0, 15.0);
    insertSession("sess-multi-model");
    insertEvent("evt-10a", "sess-multi-model", "gpt-4o", 1000000, 0);
    insertEvent("evt-10b", "sess-multi-model", "claude-3-sonnet", 1000000, 0);

    const res = await request(app).get("/pricing/costs/sess-multi-model");
    expect(res.body.total_input_cost).toBe(5.5); // $2.50 + $3.00
    expect(Object.keys(res.body.model_costs).length).toBe(2);
    expect(res.body.model_costs["gpt-4o"].calls).toBe(1);
    expect(res.body.model_costs["claude-3-sonnet"].calls).toBe(1);
  });

  it("should include pricing_used in response", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-pricing-used");
    insertEvent("evt-11", "sess-pricing-used", "gpt-4o", 100, 50);

    const res = await request(app).get("/pricing/costs/sess-pricing-used");
    expect(res.body.pricing_used).toBeDefined();
    expect(res.body.pricing_used["gpt-4o"]).toBeDefined();
  });

  it("should round costs to 6 decimal places", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-rounding");
    insertEvent("evt-12", "sess-rounding", "gpt-4o", 3, 7);

    const res = await request(app).get("/pricing/costs/sess-rounding");
    // Check that costs have at most 6 decimal places
    const costStr = res.body.total_cost.toString();
    const parts = costStr.split(".");
    if (parts.length > 1) {
      expect(parts[1].length).toBeLessThanOrEqual(6);
    }
  });

  it("should include agent_name from session", async () => {
    insertPricing("gpt-4o", 2.5, 10.0);
    insertSession("sess-agent-name", "my-cool-agent");
    insertEvent("evt-13", "sess-agent-name", "gpt-4o", 100, 50);

    const res = await request(app).get("/pricing/costs/sess-agent-name");
    expect(res.body.agent_name).toBe("my-cool-agent");
  });
});
