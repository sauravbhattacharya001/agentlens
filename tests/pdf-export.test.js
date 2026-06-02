"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { buildPdfExport, PdfBuilder } = require("../backend/lib/pdf-export");

describe("pdf-export", () => {
  describe("PdfBuilder", () => {
    it("produces a valid PDF buffer with header and trailer", () => {
      const pdf = new PdfBuilder();
      pdf.title("Test");
      pdf.text("Hello world");
      const buf = pdf.build();
      assert.ok(Buffer.isBuffer(buf));
      const str = buf.toString("utf-8");
      assert.ok(str.startsWith("%PDF-1.4"));
      assert.ok(str.includes("%%EOF"));
      assert.ok(str.includes("/Type /Catalog"));
      assert.ok(str.includes("/Type /Page"));
    });

    it("creates multiple pages when content overflows", () => {
      const pdf = new PdfBuilder();
      for (let i = 0; i < 100; i++) {
        pdf.text(`Line ${i}`);
      }
      const buf = pdf.build();
      const str = buf.toString("utf-8");
      // Should have /Count > 1
      const match = str.match(/\/Count (\d+)/);
      assert.ok(match);
      assert.ok(parseInt(match[1]) > 1, "should have multiple pages");
    });
  });

  describe("buildPdfExport", () => {
    const mockSession = {
      session_id: "sess-abc123",
      agent_name: "test-agent",
      status: "completed",
      start_time: "2026-06-01T10:00:00Z",
      end_time: "2026-06-01T10:05:00Z",
      duration_ms: 300000,
      tags: "prod,critical",
    };

    const mockEvents = [
      {
        event_id: "e1",
        event_type: "llm_call",
        timestamp: "2026-06-01T10:00:01Z",
        model: "gpt-4o",
        tokens_in: 500,
        tokens_out: 200,
        duration_ms: 1200,
        decision_trace: "Chose retrieval over direct answer",
      },
      {
        event_id: "e2",
        event_type: "tool_call",
        timestamp: "2026-06-01T10:00:03Z",
        model: "",
        tokens_in: 0,
        tokens_out: 0,
        duration_ms: 450,
        tool_call: { name: "search", input: { q: "test" } },
      },
      {
        event_id: "e3",
        event_type: "llm_call",
        timestamp: "2026-06-01T10:00:05Z",
        model: "gpt-4o",
        tokens_in: 800,
        tokens_out: 350,
        duration_ms: 2000,
        decision_trace: null,
      },
    ];

    it("produces a PDF buffer from session + events", () => {
      const buf = buildPdfExport(mockSession, mockEvents);
      assert.ok(Buffer.isBuffer(buf));
      assert.ok(buf.length > 200);
      const str = buf.toString("utf-8");
      assert.ok(str.startsWith("%PDF-1.4"));
      assert.ok(str.includes("%%EOF"));
    });

    it("includes session metadata in output", () => {
      const buf = buildPdfExport(mockSession, mockEvents);
      const str = buf.toString("utf-8");
      assert.ok(str.includes("test-agent"));
      assert.ok(str.includes("sess-abc123"));
      assert.ok(str.includes("completed"));
    });

    it("includes token summary", () => {
      const buf = buildPdfExport(mockSession, mockEvents);
      const str = buf.toString("utf-8");
      // Total tokens in = 1300
      assert.ok(str.includes("1,300") || str.includes("1300"));
    });

    it("includes event type breakdown", () => {
      const buf = buildPdfExport(mockSession, mockEvents);
      const str = buf.toString("utf-8");
      assert.ok(str.includes("llm_call"));
      assert.ok(str.includes("tool_call"));
    });

    it("includes decision traces when present", () => {
      const buf = buildPdfExport(mockSession, mockEvents);
      const str = buf.toString("utf-8");
      assert.ok(str.includes("retrieval"));
    });

    it("handles empty events array", () => {
      const buf = buildPdfExport(mockSession, []);
      assert.ok(Buffer.isBuffer(buf));
      const str = buf.toString("utf-8");
      assert.ok(str.startsWith("%PDF-1.4"));
      assert.ok(str.includes("Total Events: 0"));
    });

    it("handles minimal session object", () => {
      const buf = buildPdfExport({ id: "x" }, []);
      assert.ok(Buffer.isBuffer(buf));
    });

    it("truncates timeline at 50 events", () => {
      const manyEvents = [];
      for (let i = 0; i < 75; i++) {
        manyEvents.push({
          event_id: `e${i}`,
          event_type: "llm_call",
          timestamp: `2026-06-01T10:${String(i).padStart(2, "0")}:00Z`,
          model: "gpt-4o",
          tokens_in: 10,
          tokens_out: 5,
          duration_ms: 100,
        });
      }
      const buf = buildPdfExport(mockSession, manyEvents);
      const str = buf.toString("utf-8");
      assert.ok(str.includes("25 more events"));
    });
  });
});
