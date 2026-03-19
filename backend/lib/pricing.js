"use strict";

/**
 * Model pricing utilities for cost estimation.
 *
 * Extracted from routes/analytics.js to enable reuse across cost-related
 * endpoints (analytics, budgets, SLA, export) without duplicating the
 * pricing lookup logic and default model pricing table.
 *
 * Pricing resolution order:
 *  1. Exact match in model_pricing DB table (operator-configured)
 *  2. Exact match in built-in defaults
 *  3. Longest-prefix match (e.g. "gpt-4o-2024-05-13" → "gpt-4o")
 *  4. null (caller decides fallback behavior)
 */

const { getDb } = require("../db");

// ── Built-in default pricing (per 1M tokens, USD) ──────────────────
// These cover common models so AgentLens works out-of-the-box without
// requiring the operator to populate model_pricing first.
const DEFAULT_PRICING = {
  "gpt-4":              { input: 30.00,  output: 60.00  },
  "gpt-4-turbo":        { input: 10.00,  output: 30.00  },
  "gpt-4o":             { input:  2.50,  output: 10.00  },
  "gpt-4o-mini":        { input:  0.15,  output:  0.60  },
  "gpt-3.5-turbo":      { input:  0.50,  output:  1.50  },
  "claude-3-opus":      { input: 15.00,  output: 75.00  },
  "claude-3-sonnet":    { input:  3.00,  output: 15.00  },
  "claude-3-haiku":     { input:  0.25,  output:  1.25  },
  "claude-3.5-sonnet":  { input:  3.00,  output: 15.00  },
  "claude-4-opus":      { input: 15.00,  output: 75.00  },
  "claude-4-sonnet":    { input:  3.00,  output: 15.00  },
  "gemini-pro":         { input:  0.50,  output:  1.50  },
  "gemini-1.5-pro":     { input:  1.25,  output:  5.00  },
  "gemini-1.5-flash":   { input:  0.075, output:  0.30  },
};

/**
 * Build a merged pricing map from DB rows + built-in defaults.
 * DB entries take precedence over defaults.
 *
 * @returns {Object.<string, {input: number, output: number, currency: string}>}
 */
function loadPricingMap() {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM model_pricing ORDER BY model ASC").all();

  const map = Object.create(null);

  // Layer 1: built-in defaults
  for (const [model, prices] of Object.entries(DEFAULT_PRICING)) {
    map[model] = { input: prices.input, output: prices.output, currency: "USD" };
  }

  // Layer 2: DB overrides (wins)
  for (const row of rows) {
    map[row.model.toLowerCase()] = {
      input:    row.input_cost_per_1m,
      output:   row.output_cost_per_1m,
      currency: row.currency || "USD",
    };
  }

  return map;
}

// Delimiter set for prefix matching boundary check
const _delimiters = new Set(["-", "_", ".", "/", " "]);

/**
 * Find pricing for a model name, with fuzzy prefix fallback.
 *
 * @param {string} model        - Model name (e.g. "gpt-4o-2024-05-13")
 * @param {Object} pricingMap   - Map from loadPricingMap()
 * @returns {{input: number, output: number, currency: string}|null}
 */
function findPricing(model, pricingMap) {
  if (!model) return null;
  const lower = model.toLowerCase();

  // Exact match
  if (pricingMap[lower]) return pricingMap[lower];

  // Longest-prefix match at a word boundary
  var bestKey = null;
  var bestLen = 0;
  for (var key in pricingMap) {
    if (lower.startsWith(key) && key.length > bestLen) {
      if (key.length === lower.length || _delimiters.has(lower[key.length])) {
        bestKey = key;
        bestLen = key.length;
      }
    }
  }
  return bestKey ? pricingMap[bestKey] : null;
}

/**
 * Compute cost for a single event/row given token counts.
 *
 * @param {string} model      - Model name
 * @param {number} tokensIn   - Input token count
 * @param {number} tokensOut  - Output token count
 * @param {Object} pricingMap - Map from loadPricingMap()
 * @returns {{inputCost: number, outputCost: number, totalCost: number}|null}
 */
function computeCost(model, tokensIn, tokensOut, pricingMap) {
  var pricing = findPricing(model, pricingMap);
  if (!pricing) return null;

  var inputCost  = (tokensIn  / 1_000_000) * pricing.input;
  var outputCost = (tokensOut / 1_000_000) * pricing.output;
  return {
    inputCost:  inputCost,
    outputCost: outputCost,
    totalCost:  inputCost + outputCost,
  };
}

module.exports = {
  DEFAULT_PRICING,
  loadPricingMap,
  findPricing,
  computeCost,
};
