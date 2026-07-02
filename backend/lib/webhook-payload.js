/**
 * Alert-notification payload shaping for outbound webhooks.
 *
 * Extracted from routes/webhooks.js to separate the pure per-format payload
 * construction from the delivery machinery (DNS/SSRF validation, HMAC signing,
 * fetch with retries, and the deliveries table writes).  `deliverWebhook`
 * previously reached this logic only *after* a live network + SQLite round
 * trip, so the three payload shapes — a Slack Block Kit message, a Discord
 * embed, and the canonical `alert.fired` JSON body — had no direct test
 * coverage and could only be exercised end-to-end.  All of it is a pure
 * function of the requested `format` plus the alert fields, so isolating it
 * here makes the summary line, the conditional `agent_filter` field, and the
 * per-service field layout directly unit-testable.
 *
 * Behaviour is preserved byte-for-byte with the previous inline logic,
 * including stamping the current time into the Discord `timestamp` / JSON
 * `fired_at` fields at call time.
 *
 * @module lib/webhook-payload
 */

/**
 * Build the outbound request body for a fired alert in the given format.
 *
 * Unknown formats fall through to the `json` shape (the same default the
 * inline `switch` used), so an unexpected `format` value can never throw.
 *
 * @param {string} format - Delivery format: "slack", "discord", or "json"
 *   (anything else is treated as "json").
 * @param {Object} alertData - Alert fields.
 * @param {string} alertData.rule_name - Human-readable rule name.
 * @param {string} alertData.metric - Metric that tripped the rule.
 * @param {string} alertData.operator - Comparison operator (e.g. ">").
 * @param {number|string} alertData.threshold - Configured threshold.
 * @param {number|string} alertData.current_value - Observed value.
 * @param {number|string} alertData.window_minutes - Evaluation window in minutes.
 * @param {string|null} [alertData.agent_filter] - Agent scope; when falsy the
 *   Agent field is omitted from the Slack/Discord payloads.
 * @param {string} [alertData.alert_id] - Alert instance ID (JSON format only).
 * @param {string} [alertData.rule_id] - Rule ID (JSON format only).
 * @returns {Object} The service-specific payload object to serialize and POST.
 */
function formatPayload(format, alertData) {
  const { rule_name, metric, operator, threshold, current_value, window_minutes, agent_filter, alert_id, rule_id } = alertData;
  const summary = `🚨 Alert "${rule_name}": ${metric} ${operator} ${threshold} (current: ${current_value}) over ${window_minutes}m window`;

  switch (format) {
    case "slack":
      return {
        text: summary,
        blocks: [
          {
            type: "header",
            text: { type: "plain_text", text: `🚨 AgentLens Alert: ${rule_name}` },
          },
          {
            type: "section",
            fields: [
              { type: "mrkdwn", text: `*Metric:*\n${metric}` },
              { type: "mrkdwn", text: `*Condition:*\n${operator} ${threshold}` },
              { type: "mrkdwn", text: `*Current Value:*\n${current_value}` },
              { type: "mrkdwn", text: `*Window:*\n${window_minutes} minutes` },
              ...(agent_filter ? [{ type: "mrkdwn", text: `*Agent:*\n${agent_filter}` }] : []),
            ],
          },
        ],
      };

    case "discord":
      return {
        content: summary,
        embeds: [
          {
            title: `🚨 AgentLens Alert: ${rule_name}`,
            color: 0xff4444,
            fields: [
              { name: "Metric", value: metric, inline: true },
              { name: "Condition", value: `${operator} ${threshold}`, inline: true },
              { name: "Current Value", value: `${current_value}`, inline: true },
              { name: "Window", value: `${window_minutes} minutes`, inline: true },
              ...(agent_filter ? [{ name: "Agent", value: agent_filter, inline: true }] : []),
            ],
            timestamp: new Date().toISOString(),
          },
        ],
      };

    case "json":
    default:
      return {
        event: "alert.fired",
        alert_id,
        rule_id,
        rule_name,
        metric,
        operator,
        threshold,
        current_value,
        window_minutes,
        agent_filter,
        fired_at: new Date().toISOString(),
      };
  }
}

module.exports = {
  formatPayload,
};
