/**
 * Model Pricing API — GET/PUT model pricing for cost estimation.
 *
 * Prices are stored per 1M tokens (industry standard).
 * Built-in defaults cover popular models; users can override via PUT.
 */

const express = require("express");
const { getDb } = require("../db");
const { sanitizeString, isValidSessionId } = require("../lib/validation");

const router = express.Router();

// ── Default model pricing (per 1M tokens, USD) ─────────────────────
// Source: approximate public pricing as of early 2025.
const DEFAULT_PRICING = {
  "gpt-4":           { input: 30.00, output: 60.00 },
  "gpt-4-turbo":     { input: 10.00, output: 30.00 },
  "gpt-4o":          { input:  2.50, output: 10.00 },
  "gpt-4o-mini":     { input:  0.15, output:  0.60 },
  "gpt-3.5-turbo":   { input:  0.50, output:  1.50 },
  "claude-3-opus":   { input: 15.00, output: 75.00 },
  "claude-3-sonnet": { input:  3.00, output: 15.00 },
  "claude-3-haiku":  { input:  0.25, output:  1.25 },
  "claude-3.5-sonnet": { input: 3.00, output: 15.00 },
  "claude-4-opus":   { input: 15.00, output: 75.00 },
  "claude-4-sonnet": { input:  3.00, output: 15.00 },
  "gemini-pro":      { input:  0.50, output:  1.50 },
  "gemini-1.5-pro":  { input:  1.25, output:  5.00 },
  "gemini-1.5-flash":{ input:  0.075, output: 0.30 },
};

// ── Cached prepared statements ──────────────────────────────────────
let _pricingStmts = null;

function getPricingStatements() {
  if (_pricingStmts) return _pricingStmts;
  const db = getDb();

  _pricingStmts = {
    getAll: db.prepare("SELECT * FROM model_pricing ORDER BY model ASC"),
    getByModel: db.prepare("SELECT * FROM model_pricing WHERE model = ?"),
    upsert: db.prepare(`
      INSERT INTO model_pricing (model, input_cost_per_1m, output_cost_per_1m, currency, updated_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(model) DO UPDATE SET
        input_cost_per_1m = excluded.input_cost_per_1m,
        output_cost_per_1m = excluded.output_cost_per_1m,
        currency = excluded.currency,
        updated_at = excluded.updated_at
    `),
    deleteModel: db.prepare("DELETE FROM model_pricing WHERE model = ?"),
    getSession: db.prepare("SELECT * FROM sessions WHERE session_id = ?"),
    getSessionEvents: db.prepare(
      "SELECT event_id, event_type, model, tokens_in, tokens_out, duration_ms, timestamp FROM events WHERE session_id = ? ORDER BY timestamp ASC"
    ),
  };

  return _pricingStmts;
}

/**
 * Ensure default pricing entries exist in the DB.
 * Called once on first request.
 */
let _seeded = false;
function seedDefaults() {
  if (_seeded) return;
  const db = getDb();
  const stmts = getPricingStatements();

  const existing = stmts.getAll.all();
  if (existing.length === 0) {
    const now = new Date().toISOString();
    const insertMany = db.transaction(() => {
      for (const [model, prices] of Object.entries(DEFAULT_PRICING)) {
        stmts.upsert.run(model, prices.input, prices.output, "USD", now);
      }
    });
    insertMany();
  }
  _seeded = true;
}

// GET /pricing — List all model pricing
router.get("/", (req, res) => {
  try {
    seedDefaults();
    const stmts = getPricingStatements();
    const rows = stmts.getAll.all();

    const pricing = {};
    for (const row of rows) {
      pricing[row.model] = {
        input_cost_per_1m: row.input_cost_per_1m,
        output_cost_per_1m: row.output_cost_per_1m,
        currency: row.currency,
        updated_at: row.updated_at,
      };
    }

    res.json({
      pricing,
      defaults: DEFAULT_PRICING,
    });
  } catch (err) {
    console.error("Error fetching pricing:", err);
    res.status(500).json({ error: "Failed to fetch pricing" });
  }
});

// PUT /pricing — Update pricing for one or more models
router.put("/", (req, res) => {
  const { pricing } = req.body;

  if (!pricing || typeof pricing !== "object") {
    return res.status(400).json({ error: "Missing 'pricing' object in request body" });
  }

  try {
    seedDefaults();
    const db = getDb();
    const stmts = getPricingStatements();
    const now = new Date().toISOString();
    let updated = 0;

    const updateAll = db.transaction(() => {
      for (const [model, prices] of Object.entries(pricing)) {
        const modelName = sanitizeString(model, 128);
        if (!modelName) continue;

        const inputCost = Number(prices.input_cost_per_1m);
        const outputCost = Number(prices.output_cost_per_1m);
        const currency = sanitizeString(prices.currency || "USD", 8);

        if (!Number.isFinite(inputCost) || !Number.isFinite(outputCost)) continue;
        if (inputCost < 0 || outputCost < 0) continue;

        stmts.upsert.run(modelName, inputCost, outputCost, currency, now);
        updated++;
      }
    });
    updateAll();

    res.json({ status: "ok", updated });
  } catch (err) {
    console.error("Error updating pricing:", err);
    res.status(500).json({ error: "Failed to update pricing" });
  }
});

// DELETE /pricing/:model — Remove custom pricing for a model
router.delete("/:model", (req, res) => {
  const model = sanitizeString(req.params.model, 128);
  if (!model) {
    return res.status(400).json({ error: "Invalid model name" });
  }

  try {
    const stmts = getPricingStatements();
    const result = stmts.deleteModel.run(model);

    if (result.changes === 0) {
      return res.status(404).json({ error: `Pricing for '${model}' not found` });
    }

    res.json({ status: "ok", deleted: model });
  } catch (err) {
    console.error("Error deleting pricing:", err);
    res.status(500).json({ error: "Failed to delete pricing" });
  }
});

// GET /pricing/costs/:sessionId — Calculate costs for a session
router.get("/costs/:sessionId", (req, res) => {
  const { sessionId } = req.params;

  if (!isValidSessionId(sessionId)) {
    return res.status(400).json({ error: "Invalid session ID format" });
  }

  try {
    seedDefaults();
    const stmts = getPricingStatements();

    // Get all pricing
    const pricingRows = stmts.getAll.all();
    const pricingMap = {};
    for (const row of pricingRows) {
      pricingMap[row.model] = {
        input: row.input_cost_per_1m,
        output: row.output_cost_per_1m,
        currency: row.currency,
      };
    }

    // Also include defaults for models not in DB
    for (const [model, prices] of Object.entries(DEFAULT_PRICING)) {
      if (!pricingMap[model]) {
        pricingMap[model] = { input: prices.input, output: prices.output, currency: "USD" };
      }
    }

    // Get session events
    const session = stmts.getSession.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = stmts.getSessionEvents.all(sessionId);

    // Calculate per-event costs
    let totalCost = 0;
    let totalInputCost = 0;
    let totalOutputCost = 0;
    const modelCosts = {};
    const eventCosts = [];
    let unmatchedModels = new Set();

    for (const event of events) {
      const model = event.model;
      const tokensIn = event.tokens_in || 0;
      const tokensOut = event.tokens_out || 0;

      let inputCost = 0;
      let outputCost = 0;
      let matched = false;

      if (model && pricingMap[model]) {
        inputCost = (tokensIn / 1_000_000) * pricingMap[model].input;
        outputCost = (tokensOut / 1_000_000) * pricingMap[model].output;
        matched = true;
      } else if (model) {
        // Try fuzzy match (case-insensitive, partial)
        const lowerModel = model.toLowerCase();
        for (const [key, prices] of Object.entries(pricingMap)) {
          if (lowerModel.includes(key.toLowerCase()) || key.toLowerCase().includes(lowerModel)) {
            inputCost = (tokensIn / 1_000_000) * prices.input;
            outputCost = (tokensOut / 1_000_000) * prices.output;
            matched = true;
            break;
          }
        }
        if (!matched && (tokensIn > 0 || tokensOut > 0)) {
          unmatchedModels.add(model);
        }
      }

      const eventCost = inputCost + outputCost;
      totalCost += eventCost;
      totalInputCost += inputCost;
      totalOutputCost += outputCost;

      if (model) {
        if (!modelCosts[model]) {
          modelCosts[model] = { calls: 0, tokens_in: 0, tokens_out: 0, input_cost: 0, output_cost: 0, total_cost: 0, matched };
        }
        modelCosts[model].calls++;
        modelCosts[model].tokens_in += tokensIn;
        modelCosts[model].tokens_out += tokensOut;
        modelCosts[model].input_cost += inputCost;
        modelCosts[model].output_cost += outputCost;
        modelCosts[model].total_cost += eventCost;
      }

      eventCosts.push({
        event_id: event.event_id,
        event_type: event.event_type,
        model: model || null,
        tokens_in: tokensIn,
        tokens_out: tokensOut,
        input_cost: Math.round(inputCost * 1_000_000) / 1_000_000,
        output_cost: Math.round(outputCost * 1_000_000) / 1_000_000,
        total_cost: Math.round(eventCost * 1_000_000) / 1_000_000,
        timestamp: event.timestamp,
        pricing_matched: matched,
      });
    }

    // Round model costs
    for (const model of Object.keys(modelCosts)) {
      const mc = modelCosts[model];
      mc.input_cost = Math.round(mc.input_cost * 1_000_000) / 1_000_000;
      mc.output_cost = Math.round(mc.output_cost * 1_000_000) / 1_000_000;
      mc.total_cost = Math.round(mc.total_cost * 1_000_000) / 1_000_000;
    }

    res.json({
      session_id: sessionId,
      agent_name: session.agent_name,
      currency: "USD",
      total_cost: Math.round(totalCost * 1_000_000) / 1_000_000,
      total_input_cost: Math.round(totalInputCost * 1_000_000) / 1_000_000,
      total_output_cost: Math.round(totalOutputCost * 1_000_000) / 1_000_000,
      model_costs: modelCosts,
      event_costs: eventCosts,
      unmatched_models: [...unmatchedModels],
      pricing_used: pricingMap,
    });
  } catch (err) {
    console.error("Error calculating costs:", err);
    res.status(500).json({ error: "Failed to calculate costs" });
  }
});

module.exports = router;
