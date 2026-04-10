const express = require("express");
const { isValidSessionId } = require("../lib/validation");
const { wrapRoute } = require("../lib/request-helpers");
const { createLazyStatements } = require("../lib/lazy-statements");

const router = express.Router();

const getStatements = createLazyStatements((db) => ({
  sessionEvents: db.prepare(`
    SELECT event_id, session_id, event_type, timestamp, duration_ms,
           model, tokens_in, tokens_out, input_data, output_data, tool_call
    FROM events
    WHERE session_id = ?
    ORDER BY timestamp ASC
  `),
  recentErrorSessions: db.prepare(`
    SELECT DISTINCT e.session_id, s.agent_name, s.started_at,
           COUNT(*) as error_count
    FROM events e
    JOIN sessions s ON e.session_id = s.session_id
    WHERE e.event_type IN ('error', 'tool_error', 'agent_error', 'timeout', 'rate_limit')
    GROUP BY e.session_id
    HAVING error_count >= ?
    ORDER BY s.started_at DESC
    LIMIT ?
  `),
}));

// Default cost rates (per 1K tokens)
const DEFAULT_COST_INPUT = 0.003;
const DEFAULT_COST_OUTPUT = 0.015;

const ERROR_TYPES = new Set([
  "error", "tool_error", "agent_error", "timeout", "rate_limit",
]);

function parseEvent(row) {
  const event = { ...row };
  if (typeof event.input_data === "string") {
    try { event.input_data = JSON.parse(event.input_data); } catch { event.input_data = null; }
  }
  if (typeof event.output_data === "string") {
    try { event.output_data = JSON.parse(event.output_data); } catch { event.output_data = null; }
  }
  if (typeof event.tool_call === "string") {
    try { event.tool_call = JSON.parse(event.tool_call); } catch { event.tool_call = null; }
  }
  return event;
}

// Severity thresholds are fixed to prevent manipulation via query parameters.
// Previously, callers could override thresholds (e.g. ?sev1Rate=0.001) to
// inflate all incidents to SEV-1 (causing alert fatigue or triggering
// automated responses) or suppress genuine critical incidents.
const SEVERITY_THRESHOLDS = Object.freeze({
  sev1: 0.50,
  sev2: 0.25,
  sev3: 0.10,
});

function classifySeverity(errorRate) {
  if (errorRate >= SEVERITY_THRESHOLDS.sev1) return "SEV-1";
  if (errorRate >= SEVERITY_THRESHOLDS.sev2) return "SEV-2";
  if (errorRate >= SEVERITY_THRESHOLDS.sev3) return "SEV-3";
  return "SEV-4";
}

function buildTimeline(events, errors) {
  const errorIds = new Set(errors.map((e) => e.event_id));
  const firstTs = events[0] ? new Date(events[0].timestamp).getTime() : 0;
  const slowMs = 10000;
  const timeline = [];

  for (let i = 0; i < events.length; i++) {
    const event = events[i];
    const ts = new Date(event.timestamp).getTime();
    const elapsed = ts - firstTs;
    const isError = errorIds.has(event.event_id);
    const isSlow = (event.duration_ms || 0) > slowMs;
    const isFirst = i === 0;
    const isLast = i === events.length - 1;

    if (isError || isSlow || isFirst || isLast) {
      let description = event.event_type;
      if (isError) {
        const msg = event.error_message ||
          (event.output_data && event.output_data.error) || "";
        description = msg ? `Error: ${String(msg).slice(0, 120)}` : "Error occurred";
      } else if (event.event_type === "tool_call" && event.tool_call) {
        description = `Tool call: ${event.tool_call.tool_name || "unknown"}`;
      } else if (event.event_type === "llm_call") {
        description = `LLM call: ${event.model || "unknown"}`;
      }

      timeline.push({
        timestamp: event.timestamp,
        elapsed_ms: elapsed,
        event_type: event.event_type,
        description,
        severity: isError ? "error" : isSlow ? "warning" : "info",
        event_id: event.event_id,
      });
    }
  }
  return timeline;
}

function identifyRootCauses(errors) {
  const causes = [];
  const toolCounts = {};
  const modelCounts = {};
  const msgCounts = {};

  for (const e of errors) {
    if (e.tool_call && e.tool_call.tool_name) {
      const t = e.tool_call.tool_name;
      toolCounts[t] = (toolCounts[t] || 0) + 1;
    }
    if (e.model) {
      modelCounts[e.model] = (modelCounts[e.model] || 0) + 1;
    }
    const msg = e.error_message ||
      (e.output_data && e.output_data.error ? String(e.output_data.error).slice(0, 80) : "");
    if (msg) {
      const key = msg.slice(0, 80);
      msgCounts[key] = (msgCounts[key] || 0) + 1;
    }
  }

  // Tool failures
  const topTool = Object.entries(toolCounts).sort((a, b) => b[1] - a[1])[0];
  if (topTool) {
    causes.push({
      description: `Tool '${topTool[0]}' failures`,
      confidence: Math.min(1.0, topTool[1] / errors.length + 0.1),
      category: "tool_failure",
      affected_events: topTool[1],
      evidence: [
        `Tool '${topTool[0]}' failed ${topTool[1]} times`,
        `Represents ${Math.round((topTool[1] / errors.length) * 100)}% of all errors`,
      ],
    });
  }

  // Model errors
  const topModel = Object.entries(modelCounts).sort((a, b) => b[1] - a[1])[0];
  if (topModel) {
    causes.push({
      description: `Model '${topModel[0]}' errors`,
      confidence: Math.min(1.0, topModel[1] / errors.length + 0.05),
      category: "model_error",
      affected_events: topModel[1],
      evidence: [
        `Model '${topModel[0]}' produced ${topModel[1]} errors`,
      ],
    });
  }

  // Repeated errors
  const topMsg = Object.entries(msgCounts).sort((a, b) => b[1] - a[1])[0];
  if (topMsg && topMsg[1] >= 2) {
    causes.push({
      description: `Repeated error: ${topMsg[0].slice(0, 60)}`,
      confidence: Math.min(1.0, topMsg[1] / errors.length),
      category: "repeated_error",
      affected_events: topMsg[1],
      evidence: [`Same error occurred ${topMsg[1]} times`],
    });
  }

  // Rate limits
  const rateLimits = errors.filter((e) => e.event_type === "rate_limit");
  if (rateLimits.length) {
    causes.push({
      description: `Rate limited ${rateLimits.length} time(s)`,
      confidence: 0.9,
      category: "rate_limit",
      affected_events: rateLimits.length,
      evidence: [`${rateLimits.length} rate limit events`],
    });
  }

  // Timeouts
  const timeouts = errors.filter((e) => e.event_type === "timeout");
  if (timeouts.length) {
    const avgDur = timeouts.reduce((s, e) => s + (e.duration_ms || 0), 0) / timeouts.length;
    causes.push({
      description: `${timeouts.length} timeout(s) detected`,
      confidence: 0.8,
      category: "timeout",
      affected_events: timeouts.length,
      evidence: [`Avg duration: ${Math.round(avgDur)}ms`],
    });
  }

  causes.sort((a, b) => b.confidence - a.confidence);
  return causes;
}

// POST /api/postmortem/:sessionId — generate postmortem for a session
router.post(
  "/:sessionId",
  wrapRoute("generate postmortem", async (req, res) => {
    const { sessionId } = req.params;

    if (!isValidSessionId(sessionId)) {
      return res.status(400).json({ error: "Invalid session ID format" });
    }

    const stmts = getStatements();
    const rows = stmts.sessionEvents.all(sessionId);

    if (!rows.length) {
      return res.status(404).json({ error: "Session not found or has no events" });
    }

    const events = rows.map(parseEvent);
    const errors = events.filter((e) => ERROR_TYPES.has(e.event_type));

    if (!errors.length) {
      return res.json({
        incident_id: "INC-NONE",
        title: "No incident detected",
        severity: "SEV-4",
        summary: "No errors were found in the session events.",
        event_count: events.length,
        session_id: sessionId,
      });
    }

    const errorRate = errors.length / events.length;
    const severity = classifySeverity(errorRate);
    const timeline = buildTimeline(events, errors);
    const rootCauses = identifyRootCauses(errors);

    // Impact
    const affectedTools = [...new Set(
      errors.filter((e) => e.tool_call && e.tool_call.tool_name)
        .map((e) => e.tool_call.tool_name)
    )].sort();
    const affectedModels = [...new Set(
      errors.filter((e) => e.model).map((e) => e.model)
    )].sort();
    const downtime = errors.reduce((s, e) => s + (e.duration_ms || 0), 0);
    const tokensInWasted = errors.reduce((s, e) => s + (e.tokens_in || 0), 0);
    const tokensOutWasted = errors.reduce((s, e) => s + (e.tokens_out || 0), 0);
    const tokensWasted = tokensInWasted + tokensOutWasted;
    const costImpact =
      (tokensInWasted / 1000) * DEFAULT_COST_INPUT +
      (tokensOutWasted / 1000) * DEFAULT_COST_OUTPUT;

    const firstTs = new Date(events[0].timestamp).getTime();
    const lastTs = new Date(events[events.length - 1].timestamp).getTime();

    res.json({
      incident_id: `INC-${sessionId.slice(0, 8).toUpperCase()}`,
      title: `${severity}: ${rootCauses[0] ? rootCauses[0].description : "Unknown error"}`,
      severity,
      summary: `A ${severity} incident occurred with ${errors.length} error(s) out of ${events.length} events (${Math.round(errorRate * 100)}% error rate).`,
      duration_ms: lastTs - firstTs,
      session_id: sessionId,
      event_count: events.length,
      generated_at: new Date().toISOString(),
      timeline,
      root_causes: rootCauses,
      impact: {
        severity,
        error_count: errors.length,
        total_events: events.length,
        error_rate: Math.round(errorRate * 10000) / 10000,
        affected_tools: affectedTools,
        affected_models: affectedModels,
        downtime_ms: Math.round(downtime * 10) / 10,
        tokens_wasted: tokensWasted,
        estimated_cost_impact: Math.round(costImpact * 10000) / 10000,
        user_facing: errors.some((e) =>
          ["tool_error", "agent_error", "error"].includes(e.event_type)
        ),
      },
    });
  })
);

// GET /api/postmortem/candidates — list sessions with enough errors for postmortem
router.get(
  "/candidates",
  wrapRoute("list postmortem candidates", async (req, res) => {
    const minErrors = Math.max(1, Math.min(100, parseInt(req.query.min_errors) || 2));
    const limit = Math.min(parseInt(req.query.limit) || 20, 100);
    const stmts = getStatements();
    const rows = stmts.recentErrorSessions.all(minErrors, limit);
    res.json({
      candidates: rows,
      total: rows.length,
    });
  })
);

module.exports = router;
