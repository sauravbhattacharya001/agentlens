/**
 * Tests for lib/webhook-payload.js - the pure per-format alert payload
 * builder extracted from routes/webhooks.js (deliverWebhook).
 *
 * Before extraction these three payload shapes (Slack Block Kit, Discord
 * embed, canonical `alert.fired` JSON) were only reachable through the full
 * DNS/SSRF -> HMAC -> fetch -> deliveries-table path, so they had no direct
 * coverage: the webhooks route tests only asserted that a `format` value was
 * stored and echoed, never that the emitted body was correct. These exercise
 * formatPayload() directly as a pure unit.
 */

const { formatPayload } = require("../lib/webhook-payload");

// A representative fired-alert record (matches the shape deliverWebhook passes).
const ALERT = {
  rule_name: "High error rate",
  metric: "error_rate",
  operator: ">",
  threshold: 10,
  current_value: 15.5,
  window_minutes: 60,
  agent_filter: "billing-agent",
  alert_id: "al-123",
  rule_id: "rule-abc",
};

describe("formatPayload - json (default) shape", () => {
  test("emits the canonical alert.fired envelope with all fields", () => {
    const out = formatPayload("json", ALERT);
    expect(out).toMatchObject({
      event: "alert.fired",
      alert_id: "al-123",
      rule_id: "rule-abc",
      rule_name: "High error rate",
      metric: "error_rate",
      operator: ">",
      threshold: 10,
      current_value: 15.5,
      window_minutes: 60,
      agent_filter: "billing-agent",
    });
    expect(typeof out.fired_at).toBe("string");
    expect(Number.isNaN(Date.parse(out.fired_at))).toBe(false);
  });

  test("unknown/omitted format falls through to the json shape", () => {
    const unknown = formatPayload("teams", ALERT);
    const missing = formatPayload(undefined, ALERT);
    expect(unknown.event).toBe("alert.fired");
    expect(missing.event).toBe("alert.fired");
    // no Slack/Discord-only keys leak into the fallthrough body
    expect(unknown.blocks).toBeUndefined();
    expect(unknown.embeds).toBeUndefined();
  });

  test("json shape carries a null agent_filter through verbatim", () => {
    const out = formatPayload("json", { ...ALERT, agent_filter: null });
    expect(out.agent_filter).toBeNull();
  });
});

describe("formatPayload - slack shape", () => {
  test("builds a Block Kit message: header block + section fields", () => {
    const out = formatPayload("slack", ALERT);
    expect(out.text).toContain('Alert "High error rate"');
    expect(Array.isArray(out.blocks)).toBe(true);
    expect(out.blocks[0]).toMatchObject({
      type: "header",
      text: { type: "plain_text", text: expect.stringContaining("High error rate") },
    });
    const section = out.blocks[1];
    expect(section.type).toBe("section");
    const fieldTexts = section.fields.map((f) => f.text);
    expect(fieldTexts).toEqual([
      "*Metric:*\nerror_rate",
      "*Condition:*\n> 10",
      "*Current Value:*\n15.5",
      "*Window:*\n60 minutes",
      "*Agent:*\nbilling-agent",
    ]);
    expect(section.fields.every((f) => f.type === "mrkdwn")).toBe(true);
  });

  test("omits the Agent field when agent_filter is falsy", () => {
    const out = formatPayload("slack", { ...ALERT, agent_filter: null });
    const fieldTexts = out.blocks[1].fields.map((f) => f.text);
    expect(fieldTexts).toHaveLength(4);
    expect(fieldTexts.some((t) => t.startsWith("*Agent:*"))).toBe(false);
  });

  test("slack payload has no JSON-envelope keys", () => {
    const out = formatPayload("slack", ALERT);
    expect(out.event).toBeUndefined();
    expect(out.embeds).toBeUndefined();
  });
});

describe("formatPayload - discord shape", () => {
  test("builds an embed with color, inline fields, and a timestamp", () => {
    const out = formatPayload("discord", ALERT);
    expect(out.content).toContain('Alert "High error rate"');
    expect(Array.isArray(out.embeds)).toBe(true);
    const embed = out.embeds[0];
    expect(embed.title).toContain("High error rate");
    expect(embed.color).toBe(0xff4444);
    expect(embed.fields).toEqual([
      { name: "Metric", value: "error_rate", inline: true },
      { name: "Condition", value: "> 10", inline: true },
      { name: "Current Value", value: "15.5", inline: true },
      { name: "Window", value: "60 minutes", inline: true },
      { name: "Agent", value: "billing-agent", inline: true },
    ]);
    expect(typeof embed.timestamp).toBe("string");
    expect(Number.isNaN(Date.parse(embed.timestamp))).toBe(false);
  });

  test("omits the Agent field when agent_filter is falsy", () => {
    const out = formatPayload("discord", { ...ALERT, agent_filter: "" });
    const names = out.embeds[0].fields.map((f) => f.name);
    expect(names).toEqual(["Metric", "Condition", "Current Value", "Window"]);
  });

  test("coerces numeric current_value into the field string", () => {
    const out = formatPayload("discord", { ...ALERT, current_value: 0 });
    const cv = out.embeds[0].fields.find((f) => f.name === "Current Value");
    expect(cv.value).toBe("0");
  });
});

describe("formatPayload - shared summary line", () => {
  test("the summary text is identical across slack (text) and discord (content)", () => {
    const slack = formatPayload("slack", ALERT);
    const discord = formatPayload("discord", ALERT);
    expect(slack.text).toBe(discord.content);
    expect(slack.text).toBe(
      '\uD83D\uDEA8 Alert "High error rate": error_rate > 10 (current: 15.5) over 60m window'
    );
  });
});
