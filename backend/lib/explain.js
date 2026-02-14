/**
 * Human-readable explanation generator for agent sessions.
 *
 * Extracted from `routes/sessions.js` so the explanation logic can be
 * reused (e.g. in a CLI tool, export pipeline, or future LLM-powered
 * explainer) without pulling in Express dependencies.
 */

const { safeJsonParse } = require("./validation");

/**
 * Generate a markdown explanation from a session row and its events.
 *
 * @param {{ agent_name: string, started_at: string, ended_at: string|null, total_tokens_in: number, total_tokens_out: number, status: string, metadata: string }} session
 * @param {Array<{ event_type: string, model: string|null, input_data: string|null, output_data: string|null, tool_call: string|null, decision_trace: string|null, duration_ms: number|null, tokens_in: number, tokens_out: number }>} events
 * @returns {string}
 */
function generateExplanation(session, events) {
  const lines = [];

  lines.push(`## Agent Session: ${session.agent_name}`);
  lines.push(`**Duration:** ${formatDuration(session.started_at, session.ended_at)}`);
  lines.push(
    `**Total tokens used:** ${session.total_tokens_in + session.total_tokens_out}` +
      ` (${session.total_tokens_in} input, ${session.total_tokens_out} output)`
  );
  lines.push("");
  lines.push("### What happened:");
  lines.push("");

  let stepNum = 1;
  for (const event of events) {
    const trace = safeJsonParse(event.decision_trace);
    const toolCall = safeJsonParse(event.tool_call);
    const input = safeJsonParse(event.input_data);
    const output = safeJsonParse(event.output_data);

    if (event.event_type === "llm_call") {
      const prompt = input?.prompt || "unknown prompt";
      const response = output?.response || "unknown response";
      lines.push(
        `**Step ${stepNum}:** The agent made an LLM call` +
          (event.model ? ` using ${event.model}` : "") +
          "."
      );
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

// ── Internal helpers ────────────────────────────────────────────────

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

module.exports = { generateExplanation, truncate, formatDuration };
