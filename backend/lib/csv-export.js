/**
 * CSV and NDJSON export utilities.
 *
 * Extracted from routes/sessions.js to separate data-formatting concerns
 * from HTTP routing, and to make the helpers reusable by future export
 * endpoints (e.g. analytics CSV export, bulk session export).
 *
 * @module lib/csv-export
 */

const { safeJsonParse } = require("./validation");

// ── Event field selection ───────────────────────────────────────────

/**
 * Parse a raw event row and select only the fields relevant for export,
 * with safe defaults for nulls.  Calls parseEventRow (which mutates
 * in-place) then extracts a minimal export object — this avoids a
 * redundant full-row copy while still isolating the export shape from
 * internal DB column additions.
 *
 * @param {Object} e - Raw event row from the events table.
 * @param {Function} parseEventRow - Row parser that expands JSON text columns in-place.
 * @returns {Object} Flattened export-ready event object.
 */
function toExportEvent(e, parseEventRow) {
  parseEventRow(e);
  return {
    event_id: e.event_id,
    event_type: e.event_type,
    timestamp: e.timestamp,
    model: e.model || "",
    tokens_in: e.tokens_in || 0,
    tokens_out: e.tokens_out || 0,
    duration_ms: e.duration_ms || 0,
    input_data: e.input_data,
    output_data: e.output_data,
    tool_call: e.tool_call,
    decision_trace: e.decision_trace,
  };
}

// ── CSV helpers ─────────────────────────────────────────────────────

const CSV_HEADERS = [
  "event_id", "event_type", "timestamp", "model",
  "tokens_in", "tokens_out", "duration_ms",
  "input_data", "output_data", "tool_name", "tool_input",
  "tool_output", "reasoning",
];

/**
 * Escape a value for safe inclusion in a CSV cell.
 *
 * Handles:
 * - Null/undefined → empty string
 * - Objects → JSON stringified
 * - Formula injection defense (OWASP): prefixes formula-trigger chars
 *   (=, +, -, @, tab, CR) with a single quote so spreadsheet apps
 *   don't execute them as DDE / HYPERLINK injections
 * - Numeric values are passed through without the formula prefix
 * - Values containing commas, quotes, or newlines are double-quote wrapped
 *
 * @param {*} val - Value to escape.
 * @returns {string} CSV-safe string.
 */
function csvEscape(val) {
  if (val == null) return "";
  let str = typeof val === "object" ? JSON.stringify(val) : String(val);

  // Numeric values are safe data — skip formula-trigger prefix
  if (str.length > 0 && isFinite(Number(str))) {
    if (str.includes(",") || str.includes('"') || str.includes("\n")) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  }

  // Formula injection defense
  const first = str.charAt(0);
  if (first === "=" || first === "+" || first === "-" || first === "@" ||
      first === "\t" || first === "\r") {
    str = "'" + str;
  }

  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Convert an array of export-ready events into a CSV string.
 *
 * @param {Object[]} events - Array of objects from toExportEvent().
 * @returns {string} Complete CSV content with header row.
 */
function eventsToCsv(events) {
  const rows = [CSV_HEADERS.join(",")];
  for (const e of events) {
    rows.push([
      csvEscape(e.event_id),
      csvEscape(e.event_type),
      csvEscape(e.timestamp),
      csvEscape(e.model),
      csvEscape(e.tokens_in),
      csvEscape(e.tokens_out),
      csvEscape(e.duration_ms),
      csvEscape(e.input_data),
      csvEscape(e.output_data),
      csvEscape(e.tool_call?.tool_name),
      csvEscape(e.tool_call?.tool_input),
      csvEscape(e.tool_call?.tool_output),
      csvEscape(e.decision_trace?.reasoning),
    ].join(","));
  }
  return rows.join("\n");
}

/**
 * Build the JSON export envelope for a session + its events.
 * Computes summary stats in a single pass over events instead of
 * creating intermediate arrays via filter+map+Set.
 *
 * @param {Object} session - Session row (with raw metadata).
 * @param {Object[]} parsedEvents - Array of export-ready event objects.
 * @returns {Object} Full export payload with session, events, and summary.
 */
function buildJsonExport(session, parsedEvents) {
  const modelsUsed = new Set();
  const eventTypesUsed = new Set();
  let totalDuration = 0;
  for (let i = 0; i < parsedEvents.length; i++) {
    const e = parsedEvents[i];
    if (e.model) modelsUsed.add(e.model);
    eventTypesUsed.add(e.event_type);
    totalDuration += e.duration_ms || 0;
  }

  return {
    exported_at: new Date().toISOString(),
    session: {
      session_id: session.session_id,
      agent_name: session.agent_name,
      status: session.status,
      started_at: session.started_at,
      ended_at: session.ended_at,
      total_tokens_in: session.total_tokens_in,
      total_tokens_out: session.total_tokens_out,
      metadata: safeJsonParse(session.metadata),
    },
    events: parsedEvents,
    summary: {
      total_events: parsedEvents.length,
      total_tokens: session.total_tokens_in + session.total_tokens_out,
      models_used: [...modelsUsed],
      event_types: [...eventTypesUsed],
      total_duration_ms: totalDuration,
    },
  };
}

/**
 * Build the NDJSON session header line.
 *
 * @param {Object} session - Session row.
 * @returns {string} JSON string (without trailing newline).
 */
function ndjsonSessionLine(session) {
  return JSON.stringify({
    _type: "session",
    session_id: session.session_id,
    agent_name: session.agent_name,
    status: session.status,
    started_at: session.started_at,
    ended_at: session.ended_at,
    metadata: safeJsonParse(session.metadata),
  });
}

/**
 * Convert a single export-ready event to a CSV row string.
 * Used for streaming CSV export to avoid buffering all rows in memory.
 *
 * @param {Object} e - Export-ready event object from toExportEvent().
 * @returns {string} Single CSV row (no trailing newline).
 */
function eventToCsvRow(e) {
  return [
    csvEscape(e.event_id),
    csvEscape(e.event_type),
    csvEscape(e.timestamp),
    csvEscape(e.model),
    csvEscape(e.tokens_in),
    csvEscape(e.tokens_out),
    csvEscape(e.duration_ms),
    csvEscape(e.input_data),
    csvEscape(e.output_data),
    csvEscape(e.tool_call?.tool_name),
    csvEscape(e.tool_call?.tool_input),
    csvEscape(e.tool_call?.tool_output),
    csvEscape(e.decision_trace?.reasoning),
  ].join(",");
}

module.exports = {
  toExportEvent,
  csvEscape,
  eventsToCsv,
  eventToCsvRow,
  buildJsonExport,
  ndjsonSessionLine,
  CSV_HEADERS,
};
