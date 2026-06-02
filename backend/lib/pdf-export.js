/**
 * Minimal PDF export for AgentLens sessions.
 *
 * Generates a valid PDF 1.4 document without external dependencies.
 * Produces a human-readable session report with metadata, event summary,
 * cost breakdown, and timeline.
 *
 * @module lib/pdf-export
 */

"use strict";

// ── Minimal PDF builder ─────────────────────────────────────────────────────

class PdfBuilder {
  constructor() {
    this.objects = [];
    this.pages = [];
    this.currentPageContent = [];
    this.y = 750; // cursor
    this.pageHeight = 792;
    this.pageWidth = 612;
    this.margin = 50;
    this.lineHeight = 14;
    this.fontSize = 10;
  }

  _escPdf(str) {
    if (str == null) return "";
    return String(str)
      .replace(/\\/g, "\\\\")
      .replace(/\(/g, "\\(")
      .replace(/\)/g, "\\)")
      .replace(/[\r\n]/g, " ");
  }

  _addLine(text, opts = {}) {
    const size = opts.size || this.fontSize;
    const bold = opts.bold ? "/F2" : "/F1";
    if (this.y < this.margin + 20) {
      this._newPage();
    }
    const escaped = this._escPdf(text);
    this.currentPageContent.push(
      `BT ${bold} ${size} Tf ${this.margin} ${this.y} Td (${escaped}) Tj ET`
    );
    this.y -= (opts.spacing || this.lineHeight);
  }

  _newPage() {
    if (this.currentPageContent.length > 0) {
      this.pages.push(this.currentPageContent.join("\n"));
    }
    this.currentPageContent = [];
    this.y = 750;
  }

  _blankLine() {
    this.y -= this.lineHeight * 0.7;
  }

  title(text) { this._addLine(text, { size: 16, bold: true, spacing: 22 }); }
  heading(text) { this._blankLine(); this._addLine(text, { size: 12, bold: true, spacing: 18 }); }
  text(text) { this._addLine(text, { size: 10, spacing: 14 }); }
  smallText(text) { this._addLine(text, { size: 8, spacing: 11 }); }

  build() {
    // Flush last page
    if (this.currentPageContent.length > 0) {
      this.pages.push(this.currentPageContent.join("\n"));
    }

    // Build PDF structure
    const objs = [];
    let objNum = 0;

    function addObj(content) {
      objNum++;
      objs.push({ num: objNum, content });
      return objNum;
    }

    // 1. Catalog
    const catalogNum = addObj("<< /Type /Catalog /Pages 2 0 R >>");

    // 2. Pages (placeholder - will be updated)
    const pagesNum = addObj(""); // placeholder

    // 3-4. Fonts
    const f1Num = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
    const f2Num = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>");

    // Build page objects
    const pageObjNums = [];
    for (const pageContent of this.pages) {
      const streamBytes = Buffer.from(pageContent, "utf-8");
      const contentNum = addObj(
        `<< /Length ${streamBytes.length} >>\nstream\n${pageContent}\nendstream`
      );
      const pageNum = addObj(
        `<< /Type /Page /Parent ${pagesNum} 0 R ` +
        `/MediaBox [0 0 ${this.pageWidth} ${this.pageHeight}] ` +
        `/Contents ${contentNum} 0 R ` +
        `/Resources << /Font << /F1 ${f1Num} 0 R /F2 ${f2Num} 0 R >> >> >>`
      );
      pageObjNums.push(pageNum);
    }

    // Update pages object
    const kids = pageObjNums.map(n => `${n} 0 R`).join(" ");
    objs[pagesNum - 1].content = `<< /Type /Pages /Kids [${kids}] /Count ${pageObjNums.length} >>`;

    // Serialize
    let output = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
    const offsets = [];

    for (const obj of objs) {
      offsets.push(Buffer.byteLength(output, "utf-8"));
      output += `${obj.num} 0 obj\n${obj.content}\nendobj\n`;
    }

    const xrefOffset = Buffer.byteLength(output, "utf-8");
    output += "xref\n";
    output += `0 ${objs.length + 1}\n`;
    output += "0000000000 65535 f \n";
    for (const off of offsets) {
      output += `${String(off).padStart(10, "0")} 00000 n \n`;
    }

    output += "trailer\n";
    output += `<< /Size ${objs.length + 1} /Root ${catalogNum} 0 R >>\n`;
    output += "startxref\n";
    output += `${xrefOffset}\n`;
    output += "%%EOF\n";

    return Buffer.from(output, "utf-8");
  }
}

// ── Export formatter ────────────────────────────────────────────────────────

/**
 * Generate a PDF buffer from session + events data.
 *
 * @param {Object} session - Session row from DB.
 * @param {Object[]} events - Parsed event objects (from toExportEvent).
 * @returns {Buffer} PDF file content.
 */
function buildPdfExport(session, events) {
  const pdf = new PdfBuilder();

  // Title
  pdf.title("AgentLens Session Report");
  pdf._blankLine();

  // Metadata
  pdf.heading("Session Metadata");
  pdf.text(`Session ID: ${session.session_id || session.id || "unknown"}`);
  pdf.text(`Agent: ${session.agent_name || "unnamed"}`);
  pdf.text(`Status: ${session.status || "unknown"}`);
  pdf.text(`Started: ${session.start_time || session.created_at || "N/A"}`);
  if (session.end_time) pdf.text(`Ended: ${session.end_time}`);
  if (session.duration_ms != null) pdf.text(`Duration: ${session.duration_ms}ms`);
  if (session.tags) pdf.text(`Tags: ${session.tags}`);

  // Cost summary
  pdf.heading("Cost & Token Summary");
  let totalTokensIn = 0, totalTokensOut = 0, totalDuration = 0;
  const modelUsage = {};
  for (const e of events) {
    totalTokensIn += e.tokens_in || 0;
    totalTokensOut += e.tokens_out || 0;
    totalDuration += e.duration_ms || 0;
    if (e.model) {
      if (!modelUsage[e.model]) modelUsage[e.model] = { calls: 0, tokensIn: 0, tokensOut: 0 };
      modelUsage[e.model].calls++;
      modelUsage[e.model].tokensIn += e.tokens_in || 0;
      modelUsage[e.model].tokensOut += e.tokens_out || 0;
    }
  }
  pdf.text(`Total Events: ${events.length}`);
  pdf.text(`Total Tokens In: ${totalTokensIn.toLocaleString()}`);
  pdf.text(`Total Tokens Out: ${totalTokensOut.toLocaleString()}`);
  pdf.text(`Total Duration: ${totalDuration}ms`);

  if (Object.keys(modelUsage).length > 0) {
    pdf._blankLine();
    pdf.text("Model breakdown:");
    for (const [model, usage] of Object.entries(modelUsage)) {
      pdf.smallText(`  ${model}: ${usage.calls} calls, ${usage.tokensIn} in / ${usage.tokensOut} out`);
    }
  }

  // Event type breakdown
  pdf.heading("Event Type Breakdown");
  const typeCounts = {};
  for (const e of events) {
    const t = e.event_type || "unknown";
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  }
  for (const [type, count] of Object.entries(typeCounts).sort((a, b) => b[1] - a[1])) {
    pdf.text(`${type}: ${count}`);
  }

  // Timeline (first 50 events)
  pdf.heading("Event Timeline");
  const timelineEvents = events.slice(0, 50);
  for (let i = 0; i < timelineEvents.length; i++) {
    const e = timelineEvents[i];
    const ts = e.timestamp ? new Date(e.timestamp).toISOString().slice(11, 23) : "??:??:??.???";
    const model = e.model ? ` [${e.model}]` : "";
    const tokens = (e.tokens_in || e.tokens_out) ? ` (${e.tokens_in}/${e.tokens_out} tok)` : "";
    pdf.smallText(`${String(i + 1).padStart(3)}. ${ts} ${e.event_type || "event"}${model}${tokens}`);
  }
  if (events.length > 50) {
    pdf._blankLine();
    pdf.text(`... and ${events.length - 50} more events (truncated)`);
  }

  // Decision traces (if any)
  const decisions = events.filter(e => e.decision_trace);
  if (decisions.length > 0) {
    pdf.heading("Decision Traces");
    for (const d of decisions.slice(0, 20)) {
      const trace = typeof d.decision_trace === "string" ? d.decision_trace : JSON.stringify(d.decision_trace);
      const truncated = trace.length > 120 ? trace.slice(0, 117) + "..." : trace;
      pdf.smallText(`[${d.event_type}] ${truncated}`);
    }
    if (decisions.length > 20) {
      pdf.text(`... ${decisions.length - 20} more decision traces omitted`);
    }
  }

  // Footer
  pdf._blankLine();
  pdf._blankLine();
  pdf.smallText(`Generated by AgentLens on ${new Date().toISOString()}`);

  return pdf.build();
}

module.exports = { buildPdfExport, PdfBuilder };
