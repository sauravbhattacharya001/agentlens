const { csvEscape, eventsToCsv, buildJsonExport, ndjsonSessionLine, toExportEvent } = require("../lib/csv-export");

describe("csv-export", () => {
  const mockParseEventRow = (e) => ({
    ...e,
    input_data: e.input_data ? JSON.parse(e.input_data) : null,
    output_data: e.output_data ? JSON.parse(e.output_data) : null,
    tool_call: e.tool_call ? JSON.parse(e.tool_call) : null,
    decision_trace: e.decision_trace ? JSON.parse(e.decision_trace) : null,
  });

  describe("csvEscape", () => {
    test("null and undefined return empty string", () => {
      expect(csvEscape(null)).toBe("");
      expect(csvEscape(undefined)).toBe("");
    });

    test("plain strings pass through", () => {
      expect(csvEscape("hello")).toBe("hello");
    });

    test("strings with commas are quoted", () => {
      expect(csvEscape("a,b")).toBe('"a,b"');
    });

    test("strings with double quotes are escaped", () => {
      expect(csvEscape('say "hi"')).toBe('"say ""hi"""');
    });

    test("formula injection is prefixed", () => {
      expect(csvEscape("=SUM(A1)")).toBe("'=SUM(A1)");
      expect(csvEscape("+cmd|'/C calc'!A0")).toBe("'+cmd|'/C calc'!A0");
      expect(csvEscape("-1+1")).toBe("'-1+1");
      expect(csvEscape("@import")).toBe("'@import");
    });

    test("numeric strings skip formula prefix", () => {
      expect(csvEscape("-5")).toBe("-5");
      expect(csvEscape("+3.14")).toBe("+3.14");
      expect(csvEscape("42")).toBe("42");
    });

    test("objects are JSON stringified", () => {
      const result = csvEscape({ key: "val" });
      expect(result).toContain("key");
    });
  });

  describe("toExportEvent", () => {
    test("applies defaults for missing fields", () => {
      const raw = {
        event_id: "e1",
        event_type: "llm_call",
        timestamp: "2025-01-01T00:00:00Z",
        model: null,
        tokens_in: null,
        tokens_out: null,
        duration_ms: null,
        input_data: "null",
        output_data: "null",
        tool_call: "null",
        decision_trace: "null",
      };
      const result = toExportEvent(raw, mockParseEventRow);
      expect(result.model).toBe("");
      expect(result.tokens_in).toBe(0);
      expect(result.tokens_out).toBe(0);
      expect(result.duration_ms).toBe(0);
    });
  });

  describe("eventsToCsv", () => {
    test("produces header + data rows", () => {
      const events = [
        {
          event_id: "e1", event_type: "llm_call", timestamp: "2025-01-01",
          model: "gpt-4", tokens_in: 10, tokens_out: 20, duration_ms: 100,
          input_data: "hello", output_data: "world",
          tool_call: null, decision_trace: null,
        },
      ];
      const csv = eventsToCsv(events);
      const lines = csv.split("\n");
      expect(lines.length).toBe(2);
      expect(lines[0]).toContain("event_id");
      expect(lines[1]).toContain("e1");
    });
  });

  describe("buildJsonExport", () => {
    test("includes session and summary", () => {
      const session = {
        session_id: "s1", agent_name: "test", status: "completed",
        started_at: "2025-01-01", ended_at: "2025-01-02",
        total_tokens_in: 100, total_tokens_out: 200, metadata: "{}",
      };
      const events = [
        { event_id: "e1", event_type: "llm_call", model: "gpt-4", duration_ms: 50 },
      ];
      const result = buildJsonExport(session, events);
      expect(result.session.session_id).toBe("s1");
      expect(result.summary.total_events).toBe(1);
      expect(result.summary.total_tokens).toBe(300);
    });
  });

  describe("ndjsonSessionLine", () => {
    test("produces valid JSON with _type=session", () => {
      const session = {
        session_id: "s1", agent_name: "test", status: "active",
        started_at: "2025-01-01", ended_at: null, metadata: '{"foo":"bar"}',
      };
      const line = ndjsonSessionLine(session);
      const parsed = JSON.parse(line);
      expect(parsed._type).toBe("session");
      expect(parsed.session_id).toBe("s1");
      expect(parsed.metadata).toEqual({ foo: "bar" });
    });
  });
});
