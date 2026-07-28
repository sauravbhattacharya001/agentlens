const { csvEscape, eventsToCsv, buildJsonExport, ndjsonSessionLine, toExportEvent, eventToCsvRow } = require("../lib/csv-export");

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

    test("formula injection with leading whitespace is still escaped", () => {
      // Some spreadsheet importers (LibreOffice with trim-whitespace,
      // Google Sheets paste-as-plain) strip leading spaces/tabs before
      // formula evaluation, so we must catch the trigger char after
      // any leading whitespace, not just at position 0.
      expect(csvEscape(" =SUM(1)")).toBe("'=SUM(1)");
      expect(csvEscape("\t=SUM(1)")).toBe("'=SUM(1)");
      expect(csvEscape("  +cmd|/c calc")).toBe("'+cmd|/c calc");
      expect(csvEscape("\r\n@hi")).toBe("'@hi");
      expect(csvEscape("   -1+1")).toBe("'-1+1");
    });

    test("plain text with leading whitespace is not treated as a formula", () => {
      // Don’t over-escape: a sentence that begins with a space is
      // legitimate user content and must not get an apostrophe.
      expect(csvEscape(" hello")).toBe(" hello");
      expect(csvEscape("  some note")).toBe("  some note");
    });

    test("numeric value containing a delimiter is quote-wrapped, not prefixed", () => {
      // Exercises the numeric branch's CSV-quoting path: isFinite(Number(str))
      // is true (JS parses these as numbers) yet the raw text carries a comma
      // or newline, so it must be double-quote wrapped without a formula prefix.
      expect(csvEscape("1,000")).toBe('"1,000"');
      expect(csvEscape("1e3\n")).toBe('"1e3\n"');
    });

    test("leading tab/CR with no formula char is still control-escaped", () => {
      // Exercises the non-formula control-char arm: a value beginning with a
      // bare tab or CR (and NO =/+/-/@ trigger) is prefixed with an apostrophe
      // because some importers treat leading control input specially.
      expect(csvEscape("\tvalue")).toBe("'\tvalue");
      expect(csvEscape("\rvalue")).toBe("'\rvalue");
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

  describe("eventToCsvRow", () => {
    test("omits optional tool_call/decision_trace fields safely", () => {
      // No tool_call or decision_trace: the optional-chaining arms yield
      // undefined, which csvEscape maps to empty trailing cells.
      const row = eventToCsvRow({
        event_id: "e1",
        event_type: "model_call",
        timestamp: 1,
        model: "gpt-4",
        tokens_in: 1,
        tokens_out: 2,
        duration_ms: 3,
        input_data: "in",
        output_data: "out",
      });
      expect(row).toBe("e1,model_call,1,gpt-4,1,2,3,in,out,,,,");
    });

    test("includes nested tool_call and decision_trace fields when present", () => {
      const row = eventToCsvRow({
        event_id: "e2",
        event_type: "tool_call",
        timestamp: 2,
        model: "",
        tokens_in: 0,
        tokens_out: 0,
        duration_ms: 0,
        input_data: "",
        output_data: "",
        tool_call: { tool_name: "search", tool_input: "q", tool_output: "r" },
        decision_trace: { reasoning: "why" },
      });
      expect(row).toBe("e2,tool_call,2,,0,0,0,,,search,q,r,why");
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
