/**
 * Contract tests for lib/pdf-export.
 *
 * These pin the *structure and section text* of the generated PDF, NOT the exact
 * bytes.  A PDF is a container format, so byte-for-byte assertions would be brittle
 * (any spacing/offset tweak would break them without indicating a real regression).
 * Instead we decode the buffer as latin1 text and assert on the invariants a reader
 * actually depends on: a valid PDF envelope (header/xref/trailer/%%EOF), the section
 * headings, the aggregated cost/token/event numbers, the truncation summaries, and
 * the PDF-string escaping that keeps generated content from corrupting the document.
 */

const { buildPdfExport, PdfBuilder } = require("../lib/pdf-export");

// The content of a PDF `(...)` string literal for the given raw text, i.e. the text
// after pdf-export's _escPdf has run.  We assert against this so tests document the
// escaping that is applied to every drawn line.
function escPdf(str) {
  if (str == null) return "";
  return String(str)
    .replace(/\\/g, "\\\\")
    .replace(/\(/g, "\\(")
    .replace(/\)/g, "\\)")
    .replace(/[\r\n]/g, " ");
}

// Decode a built PDF buffer to a searchable string.  latin1 is a total, 1:1 byte
// mapping so the binary header bytes never throw and text stays intact.
function decode(buf) {
  return buf.toString("latin1");
}

describe("pdf-export", () => {
  describe("PdfBuilder._escPdf", () => {
    const b = new PdfBuilder();

    test("null and undefined become the empty string", () => {
      expect(b._escPdf(null)).toBe("");
      expect(b._escPdf(undefined)).toBe("");
    });

    test("backslashes are doubled", () => {
      expect(b._escPdf("a\\b")).toBe("a\\\\b");
    });

    test("parentheses are escaped (they delimit PDF string literals)", () => {
      expect(b._escPdf("f(x)")).toBe("f\\(x\\)");
    });

    test("CR and LF are flattened to spaces (a literal newline would break the line)", () => {
      expect(b._escPdf("a\nb")).toBe("a b");
      expect(b._escPdf("a\r\nb")).toBe("a  b"); // CR->space + LF->space
    });

    test("non-string values are coerced before escaping", () => {
      expect(b._escPdf(42)).toBe("42");
    });

    test("escaping is combined in one pass", () => {
      expect(b._escPdf("x(\\)\n")).toBe("x\\(\\\\\\) ");
    });
  });

  describe("buildPdfExport - PDF envelope", () => {
    const buf = buildPdfExport(
      { session_id: "s1", agent_name: "a", status: "ok" },
      [{ event_type: "llm_call", timestamp: "2026-01-01T00:00:00Z" }]
    );
    const s = decode(buf);

    test("returns a Buffer", () => {
      expect(Buffer.isBuffer(buf)).toBe(true);
    });

    test("starts with a PDF 1.4 header", () => {
      expect(s.startsWith("%PDF-1.4\n")).toBe(true);
    });

    test("ends with the %%EOF marker", () => {
      expect(s.trimEnd().endsWith("%%EOF")).toBe(true);
    });

    test("contains the cross-reference table and trailer", () => {
      expect(s).toContain("\nxref\n");
      expect(s).toContain("\ntrailer\n");
      expect(s).toContain("\nstartxref\n");
    });

    test("declares a document catalog and a page tree", () => {
      expect(s).toContain("/Type /Catalog");
      expect(s).toContain("/Type /Pages");
      expect(s).toContain("/Type /Page ");
    });

    test("embeds the Helvetica fonts it references", () => {
      expect(s).toContain("/BaseFont /Helvetica >>");
      expect(s).toContain("/BaseFont /Helvetica-Bold >>");
    });

    test("trailer /Size equals object count + 1 and the xref lists that many entries", () => {
      const size = Number(/\/Size (\d+) \/Root/.exec(s)[1]);
      // Free-list head + one entry per real object (matches /Size).
      const nEntries = Number(/xref\n0 (\d+)\n/.exec(s)[1]);
      expect(nEntries).toBe(size);
      // The page tree's /Count is >= 1 for any non-empty report.
      const count = Number(/\/Count (\d+) >>/.exec(s)[1]);
      expect(count).toBeGreaterThanOrEqual(1);
    });
  });

  describe("buildPdfExport - metadata section", () => {
    test("renders the title and all metadata section headings", () => {
      const s = decode(buildPdfExport({ id: "x" }, []));
      expect(s).toContain("(AgentLens Session Report)");
      expect(s).toContain("(Session Metadata)");
      expect(s).toContain("(Cost & Token Summary)");
      expect(s).toContain("(Event Type Breakdown)");
      expect(s).toContain("(Event Timeline)");
    });

    test("prefers session_id, then id, then 'unknown'", () => {
      expect(decode(buildPdfExport({ session_id: "sid" }, []))).toContain("(Session ID: sid)");
      expect(decode(buildPdfExport({ id: "iid" }, []))).toContain("(Session ID: iid)");
      expect(decode(buildPdfExport({}, []))).toContain("(Session ID: unknown)");
    });

    test("falls back to placeholder metadata when fields are absent", () => {
      const s = decode(buildPdfExport({ id: "x" }, []));
      expect(s).toContain("(Agent: unnamed)");
      expect(s).toContain("(Status: unknown)");
      expect(s).toContain("(Started: N/A)");
    });

    test("start time falls back to created_at when start_time is missing", () => {
      const s = decode(buildPdfExport({ id: "x", created_at: "2026-02-02T00:00:00Z" }, []));
      expect(s).toContain("(Started: 2026-02-02T00:00:00Z)");
    });

    test("optional metadata rows only appear when present", () => {
      const bare = decode(buildPdfExport({ id: "x" }, []));
      expect(bare).not.toContain("(Ended:");
      expect(bare).not.toContain("(Duration:");
      expect(bare).not.toContain("(Tags:");

      const full = decode(
        buildPdfExport(
          { id: "x", end_time: "2026-01-01T01:00:00Z", duration_ms: 3600000, tags: "prod,ci" },
          []
        )
      );
      expect(full).toContain("(Ended: 2026-01-01T01:00:00Z)");
      expect(full).toContain("(Duration: 3600000ms)");
      expect(full).toContain("(Tags: prod,ci)");
    });

    test("duration_ms of 0 is still rendered (null-check, not falsy-check)", () => {
      const s = decode(buildPdfExport({ id: "x", duration_ms: 0 }, []));
      expect(s).toContain("(Duration: 0ms)");
    });
  });

  describe("buildPdfExport - cost & token aggregation", () => {
    const events = [
      { event_type: "llm_call", model: "gpt-4", tokens_in: 1000, tokens_out: 500, duration_ms: 200 },
      { event_type: "llm_call", model: "gpt-4", tokens_in: 200, tokens_out: 100, duration_ms: 50 },
      { event_type: "tool_call", tokens_in: 0, tokens_out: 0, duration_ms: 5 },
    ];
    const s = decode(buildPdfExport({ id: "x" }, events));

    test("counts total events", () => {
      expect(s).toContain("(Total Events: 3)");
    });

    test("sums tokens with locale grouping and duration", () => {
      expect(s).toContain("(Total Tokens In: 1,200)");
      expect(s).toContain("(Total Tokens Out: 600)");
      expect(s).toContain("(Total Duration: 255ms)");
    });

    test("rolls up per-model usage across calls", () => {
      expect(s).toContain("(Model breakdown:)");
      expect(s).toContain("gpt-4: 2 calls, 1200 in / 600 out");
    });

    test("omits the model breakdown entirely when no event has a model", () => {
      const noModel = decode(buildPdfExport({ id: "x" }, [{ event_type: "tool_call" }]));
      expect(noModel).not.toContain("(Model breakdown:)");
    });

    test("treats missing token/duration fields as zero", () => {
      const s2 = decode(buildPdfExport({ id: "x" }, [{ event_type: "e" }]));
      expect(s2).toContain("(Total Tokens In: 0)");
      expect(s2).toContain("(Total Tokens Out: 0)");
      expect(s2).toContain("(Total Duration: 0ms)");
    });
  });

  describe("buildPdfExport - event type breakdown", () => {
    test("counts each event type and orders by descending frequency", () => {
      const events = [
        { event_type: "tool_call" },
        { event_type: "tool_call" },
        { event_type: "tool_call" },
        { event_type: "llm_call" },
        { event_type: "llm_call" },
        { event_type: "error" },
      ];
      const s = decode(buildPdfExport({ id: "x" }, events));
      expect(s).toContain("(tool_call: 3)");
      expect(s).toContain("(llm_call: 2)");
      expect(s).toContain("(error: 1)");
      // Highest count is drawn first.
      expect(s.indexOf("(tool_call: 3)")).toBeLessThan(s.indexOf("(llm_call: 2)"));
      expect(s.indexOf("(llm_call: 2)")).toBeLessThan(s.indexOf("(error: 1)"));
    });

    test("events without a type are bucketed as 'unknown'", () => {
      const s = decode(buildPdfExport({ id: "x" }, [{}, {}]));
      expect(s).toContain("(unknown: 2)");
    });
  });

  describe("buildPdfExport - timeline", () => {
    test("numbers timeline rows and formats the timestamp clock component", () => {
      const s = decode(
        buildPdfExport({ id: "x" }, [
          { event_type: "llm_call", model: "gpt-4", tokens_in: 10, tokens_out: 5, timestamp: "2026-01-01T12:34:56.789Z" },
        ])
      );
      // Drawn as "  1. 12:34:56.789 llm_call [gpt-4] (10/5 tok)"; the token-count
      // parens are escaped inside the PDF string literal.
      expect(s).toContain(`12:34:56.789 llm_call [gpt-4] ${escPdf("(10/5 tok)")}`);
      expect(s).toMatch(/\(\s*1\. 12:34:56\.789 llm_call/);
    });

    test("uses a placeholder clock when a timestamp is missing", () => {
      const s = decode(buildPdfExport({ id: "x" }, [{ event_type: "e" }]));
      expect(s).toContain("??:??:??.???");
    });

    test("shows at most 50 timeline rows and summarises the remainder (parens escaped)", () => {
      const many = Array.from({ length: 63 }, (_, i) => ({
        event_type: "e" + i,
        timestamp: "2026-01-01T00:00:00.000Z",
      }));
      const s = decode(buildPdfExport({ id: "x" }, many));
      expect(s).toContain("50. 00:00:00.000 e49");
      expect(s).not.toContain(" e50 "); // 51st event is not in the timeline
      // The literal parens of "(truncated)" are escaped in the drawn string.
      expect(s).toContain(`(... and 13 more events ${escPdf("(truncated)")})`);
    });

    test("no truncation summary when exactly at the 50-event cap", () => {
      const fifty = Array.from({ length: 50 }, () => ({ event_type: "e", timestamp: "2026-01-01T00:00:00Z" }));
      const s = decode(buildPdfExport({ id: "x" }, fifty));
      expect(s).not.toContain("more events");
    });
  });

  describe("buildPdfExport - decision traces", () => {
    test("the section is omitted when no event carries a decision_trace", () => {
      const s = decode(buildPdfExport({ id: "x" }, [{ event_type: "e" }]));
      expect(s).not.toContain("(Decision Traces)");
    });

    test("renders traces with the owning event type", () => {
      const s = decode(
        buildPdfExport({ id: "x" }, [{ event_type: "plan", decision_trace: "chose path A" }])
      );
      expect(s).toContain("(Decision Traces)");
      expect(s).toContain("([plan] chose path A)");
    });

    test("object traces are JSON-stringified", () => {
      const s = decode(
        buildPdfExport({ id: "x" }, [{ event_type: "plan", decision_trace: { choice: "A" } }])
      );
      expect(s).toContain(escPdf(`[plan] ${JSON.stringify({ choice: "A" })}`));
    });

    test("long traces are truncated to 117 chars + ellipsis", () => {
      const long = "D".repeat(200);
      const s = decode(buildPdfExport({ id: "x" }, [{ event_type: "plan", decision_trace: long }]));
      expect(s).toContain(`[plan] ${"D".repeat(117)}...`);
      expect(s).not.toContain("D".repeat(121));
    });

    test("shows at most 20 traces and reports how many were omitted", () => {
      const events = Array.from({ length: 26 }, (_, i) => ({
        event_type: "plan",
        decision_trace: "trace-" + i,
      }));
      const s = decode(buildPdfExport({ id: "x" }, events));
      expect(s).toContain("([plan] trace-19)");
      expect(s).not.toContain("([plan] trace-20)");
      expect(s).toContain("(... 6 more decision traces omitted)");
    });
  });

  describe("buildPdfExport - escaping of session content", () => {
    test("session fields containing PDF delimiters are escaped in the drawn stream", () => {
      const s = decode(
        buildPdfExport({ session_id: "id(1)", agent_name: "team\\ops", status: "a\nb" }, [])
      );
      expect(s).toContain(`(Session ID: ${escPdf("id(1)")})`);
      expect(s).toContain(`(Agent: ${escPdf("team\\ops")})`);
      expect(s).toContain(`(Status: ${escPdf("a\nb")})`);
      // The raw, unescaped forms must NOT leak into the document body.
      expect(s).not.toContain("(Session ID: id(1))");
    });

    test("always stamps a generated-on footer", () => {
      const s = decode(buildPdfExport({ id: "x" }, []));
      expect(s).toContain("Generated by AgentLens on ");
    });
  });

  describe("PdfBuilder - pagination", () => {
    test("overflowing content spills onto a second page (Count == 2)", () => {
      const b = new PdfBuilder();
      // ~55 lines at lineHeight 14 exceeds the ~700pt usable height of one page.
      for (let i = 0; i < 80; i++) b.text("line " + i);
      const s = decode(b.build());
      expect(s).toContain("/Count 2 ");
      // Both the first and a later line survive across the page break.
      expect(s).toContain("(line 0)");
      expect(s).toContain("(line 79)");
    });

    test("an empty builder still produces a structurally valid one-page PDF", () => {
      const s = decode(new PdfBuilder().build());
      expect(s.startsWith("%PDF-1.4")).toBe(true);
      expect(s.trimEnd().endsWith("%%EOF")).toBe(true);
      expect(s).toContain("/Count 0 "); // no pages were flushed
    });
  });
});
