const express = require("express");
const { getDb } = require("../db");

const router = express.Router();

// GET /sessions — List all sessions
router.get("/", (req, res) => {
  const db = getDb();
  const limit = Math.min(parseInt(req.query.limit) || 50, 200);
  const offset = parseInt(req.query.offset) || 0;
  const status = req.query.status;

  let query = "SELECT * FROM sessions";
  const params = [];

  if (status) {
    query += " WHERE status = ?";
    params.push(status);
  }

  query += " ORDER BY started_at DESC LIMIT ? OFFSET ?";
  params.push(limit, offset);

  try {
    const sessions = db.prepare(query).all(...params);
    const total = db
      .prepare(`SELECT COUNT(*) as count FROM sessions${status ? " WHERE status = ?" : ""}`)
      .get(...(status ? [status] : []));

    // Parse JSON metadata
    const parsed = sessions.map((s) => ({
      ...s,
      metadata: JSON.parse(s.metadata || "{}"),
    }));

    res.json({ sessions: parsed, total: total.count });
  } catch (err) {
    console.error("Error listing sessions:", err);
    res.status(500).json({ error: "Failed to list sessions" });
  }
});

// GET /sessions/:id — Session detail with full event trace
router.get("/:id", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  try {
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(id);

    // Parse JSON fields
    const parsedEvents = events.map((e) => ({
      ...e,
      input_data: e.input_data ? JSON.parse(e.input_data) : null,
      output_data: e.output_data ? JSON.parse(e.output_data) : null,
      tool_call: e.tool_call ? JSON.parse(e.tool_call) : null,
      decision_trace: e.decision_trace ? JSON.parse(e.decision_trace) : null,
    }));

    res.json({
      ...session,
      metadata: JSON.parse(session.metadata || "{}"),
      events: parsedEvents,
    });
  } catch (err) {
    console.error("Error fetching session:", err);
    res.status(500).json({ error: "Failed to fetch session" });
  }
});

// GET /sessions/:id/explain — Human-readable explanation
router.get("/:id/explain", (req, res) => {
  const db = getDb();
  const { id } = req.params;

  try {
    const session = db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(id);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    const events = db
      .prepare("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp ASC")
      .all(id);

    // Generate explanation (MVP: rule-based; production: LLM-powered)
    const explanation = generateExplanation(session, events);
    res.json({ session_id: id, explanation });
  } catch (err) {
    console.error("Error generating explanation:", err);
    res.status(500).json({ error: "Failed to generate explanation" });
  }
});

function generateExplanation(session, events) {
  const meta = JSON.parse(session.metadata || "{}");
  const lines = [];

  lines.push(`## Agent Session: ${session.agent_name}`);
  lines.push(`**Duration:** ${formatDuration(session.started_at, session.ended_at)}`);
  lines.push(`**Total tokens used:** ${session.total_tokens_in + session.total_tokens_out} (${session.total_tokens_in} input, ${session.total_tokens_out} output)`);
  lines.push("");
  lines.push("### What happened:");
  lines.push("");

  let stepNum = 1;
  for (const event of events) {
    const trace = event.decision_trace ? JSON.parse(event.decision_trace) : null;
    const toolCall = event.tool_call ? JSON.parse(event.tool_call) : null;
    const input = event.input_data ? JSON.parse(event.input_data) : null;
    const output = event.output_data ? JSON.parse(event.output_data) : null;

    if (event.event_type === "llm_call") {
      const prompt = input?.prompt || "unknown prompt";
      const response = output?.response || "unknown response";
      lines.push(`**Step ${stepNum}:** The agent made an LLM call${event.model ? ` using ${event.model}` : ""}.`);
      lines.push(`- *Input:* "${truncate(prompt, 120)}"`);
      lines.push(`- *Output:* "${truncate(response, 120)}"`);
      if (event.tokens_in || event.tokens_out) {
        lines.push(`- *Tokens:* ${event.tokens_in} in / ${event.tokens_out} out`);
      }
      if (trace?.reasoning) {
        lines.push(`- 💡 *Reasoning:* ${trace.reasoning}`);
      }
      lines.push("");
      stepNum++;
    } else if (event.event_type === "tool_call") {
      const toolName = toolCall?.tool_name || "unknown tool";
      lines.push(`**Step ${stepNum}:** The agent called the **${toolName}** tool.`);
      if (toolCall?.tool_input) {
        lines.push(`- *Input:* ${truncate(JSON.stringify(toolCall.tool_input), 120)}`);
      }
      if (toolCall?.tool_output) {
        lines.push(`- *Output:* ${truncate(JSON.stringify(toolCall.tool_output), 120)}`);
      }
      if (event.duration_ms) {
        lines.push(`- *Duration:* ${event.duration_ms.toFixed(1)}ms`);
      }
      lines.push("");
      stepNum++;
    } else if (event.event_type === "agent_call") {
      lines.push(`**Step ${stepNum}:** Agent function executed.`);
      if (trace?.reasoning) {
        lines.push(`- 💡 *Reasoning:* ${trace.reasoning}`);
      }
      if (event.duration_ms) {
        lines.push(`- *Duration:* ${event.duration_ms.toFixed(1)}ms`);
      }
      lines.push("");
      stepNum++;
    }
  }

  // Summary
  const llmCalls = events.filter((e) => e.event_type === "llm_call").length;
  const toolCalls = events.filter((e) => e.event_type === "tool_call").length;
  const errors = events.filter((e) => e.event_type.includes("error")).length;

  lines.push("### Summary");
  lines.push(`- ${llmCalls} LLM call(s), ${toolCalls} tool call(s), ${errors} error(s)`);
  lines.push(`- Total tokens: ${session.total_tokens_in + session.total_tokens_out}`);
  lines.push(`- Session status: ${session.status}`);

  return lines.join("\n");
}

function truncate(str, maxLen) {
  if (!str) return "";
  return str.length > maxLen ? str.slice(0, maxLen) + "…" : str;
}

function formatDuration(start, end) {
  if (!start) return "unknown";
  if (!end) return "ongoing";
  const ms = new Date(end) - new Date(start);
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

module.exports = router;
