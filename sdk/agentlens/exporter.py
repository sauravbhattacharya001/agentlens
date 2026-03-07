"""Session Exporter — offline export to JSON, CSV, and standalone HTML reports.

Unlike ``export_session()`` which requires the backend, this module works
directly with in-memory ``Session`` objects.  It's useful for:

- Sharing session reports with stakeholders who don't have dashboard access
- Archiving sessions as self-contained files
- CI/CD pipelines that need structured output without a running backend
- Offline analysis in spreadsheets or notebooks

Supported formats:
- **JSON** — full session data with events, suitable for programmatic use
- **CSV** — flat event table, great for spreadsheets and pandas
- **HTML** — self-contained report with timeline, stats, and event details

Example::

    from agentlens.exporter import SessionExporter

    exporter = SessionExporter(session)
    exporter.to_json("report.json")
    exporter.to_csv("events.csv")
    exporter.to_html("report.html")

    # Or get strings directly
    json_str = exporter.as_json()
    csv_str = exporter.as_csv()
    html_str = exporter.as_html()
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from agentlens.models import Session, AgentEvent


def _iso(dt: datetime | None) -> str | None:
    """Convert datetime to ISO string or None."""
    return dt.isoformat() if dt else None


def _duration_human(ms: float | None) -> str:
    """Format milliseconds as human-readable duration."""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    return f"{mins:.1f}m"


def _session_stats(session: Session) -> dict[str, Any]:
    """Compute summary statistics for a session."""
    events = session.events
    models: dict[str, int] = {}
    tool_calls: list[str] = []
    event_types: dict[str, int] = {}
    total_duration_ms = 0.0
    error_count = 0

    for ev in events:
        event_types[ev.event_type] = event_types.get(ev.event_type, 0) + 1
        if ev.model:
            models[ev.model] = models.get(ev.model, 0) + 1
        if ev.tool_call:
            tool_calls.append(ev.tool_call.tool_name)
        if ev.duration_ms:
            total_duration_ms += ev.duration_ms
        if ev.event_type == "error":
            error_count += 1

    session_duration_ms = None
    if session.ended_at and session.started_at:
        session_duration_ms = (session.ended_at - session.started_at).total_seconds() * 1000

    return {
        "event_count": len(events),
        "total_tokens_in": session.total_tokens_in,
        "total_tokens_out": session.total_tokens_out,
        "total_tokens": session.total_tokens_in + session.total_tokens_out,
        "models_used": models,
        "tool_calls": len(tool_calls),
        "unique_tools": list(set(tool_calls)),
        "event_types": event_types,
        "error_count": error_count,
        "total_event_duration_ms": round(total_duration_ms, 1),
        "session_duration_ms": round(session_duration_ms, 1) if session_duration_ms else None,
    }


def _event_to_row(ev: AgentEvent) -> dict[str, Any]:
    """Flatten an event into a dict suitable for CSV."""
    return {
        "event_id": ev.event_id,
        "session_id": ev.session_id,
        "event_type": ev.event_type,
        "timestamp": _iso(ev.timestamp),
        "model": ev.model or "",
        "tokens_in": ev.tokens_in,
        "tokens_out": ev.tokens_out,
        "duration_ms": ev.duration_ms if ev.duration_ms is not None else "",
        "tool_name": ev.tool_call.tool_name if ev.tool_call else "",
        "reasoning": ev.decision_trace.reasoning if ev.decision_trace else "",
        "confidence": ev.decision_trace.confidence if ev.decision_trace else "",
    }


_CSV_COLUMNS = [
    "event_id", "session_id", "event_type", "timestamp", "model",
    "tokens_in", "tokens_out", "duration_ms", "tool_name",
    "reasoning", "confidence",
]


class SessionExporter:
    """Export a session to JSON, CSV, or standalone HTML.

    Args:
        session: The Session object to export.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._stats = _session_stats(session)

    # ── JSON ────────────────────────────────────────────────────────

    def as_json(self, indent: int = 2) -> str:
        """Return the session as a JSON string."""
        payload = {
            "session": {
                "session_id": self.session.session_id,
                "agent_name": self.session.agent_name,
                "started_at": _iso(self.session.started_at),
                "ended_at": _iso(self.session.ended_at),
                "status": self.session.status,
                "metadata": self.session.metadata,
            },
            "stats": self._stats,
            "events": [ev.model_dump(mode="json", exclude_none=True) for ev in self.session.events],
        }
        return json.dumps(payload, indent=indent, default=str)

    def to_json(self, path: str) -> None:
        """Write session JSON to a file."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.as_json())

    # ── CSV ─────────────────────────────────────────────────────────

    def as_csv(self) -> str:
        """Return events as a CSV string."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for ev in self.session.events:
            writer.writerow(_event_to_row(ev))
        return buf.getvalue()

    def to_csv(self, path: str) -> None:
        """Write event CSV to a file."""
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(self.as_csv())

    # ── HTML ────────────────────────────────────────────────────────

    def as_html(self) -> str:
        """Return a self-contained HTML report."""
        s = self.session
        stats = self._stats
        events_html = self._render_events_table()
        models_html = self._render_models_table()
        tools_html = self._render_tools_list()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentLens Report — {_escape(s.agent_name)} ({s.session_id})</title>
<style>
  :root {{ --bg: #0f1117; --surface: #1a1d27; --border: #2a2d37; --text: #e1e4ea;
           --muted: #8b8fa3; --accent: #6366f1; --green: #22c55e; --red: #ef4444;
           --orange: #f97316; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
           padding: 1.25rem; }}
  .card .label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
                  letter-spacing: 0.05em; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 0.25rem; }}
  .card .value.green {{ color: var(--green); }}
  .card .value.red {{ color: var(--red); }}
  .card .value.accent {{ color: var(--accent); }}
  .card .value.orange {{ color: var(--orange); }}
  .section {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
              padding: 1.5rem; margin-bottom: 1.5rem; }}
  .section h2 {{ font-size: 1.1rem; margin-bottom: 1rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; color: var(--muted); font-weight: 500; padding: 0.5rem 0.75rem;
       border-bottom: 1px solid var(--border); }}
  td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: rgba(99, 102, 241, 0.05); }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px;
            font-size: 0.75rem; font-weight: 500; }}
  .badge-llm {{ background: rgba(99, 102, 241, 0.15); color: var(--accent); }}
  .badge-tool {{ background: rgba(34, 197, 94, 0.15); color: var(--green); }}
  .badge-decision {{ background: rgba(249, 115, 22, 0.15); color: var(--orange); }}
  .badge-error {{ background: rgba(239, 68, 68, 0.15); color: var(--red); }}
  .badge-generic {{ background: rgba(139, 143, 163, 0.15); color: var(--muted); }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 0.5rem; }}
  .pill {{ background: rgba(99, 102, 241, 0.1); color: var(--accent); padding: 0.25rem 0.75rem;
           border-radius: 20px; font-size: 0.8rem; }}
  .footer {{ text-align: center; color: var(--muted); font-size: 0.75rem; margin-top: 2rem; }}
  @media (max-width: 600px) {{ body {{ padding: 1rem; }} .cards {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <h1>🔎 {_escape(s.agent_name)}</h1>
  <div class="subtitle">
    Session <code>{s.session_id}</code> · {_iso(s.started_at) or '—'}
    {(' → ' + _iso(s.ended_at)) if s.ended_at else ''} · Status: {s.status}
  </div>

  <div class="cards">
    <div class="card"><div class="label">Events</div><div class="value accent">{stats['event_count']}</div></div>
    <div class="card"><div class="label">Tokens In</div><div class="value">{stats['total_tokens_in']:,}</div></div>
    <div class="card"><div class="label">Tokens Out</div><div class="value">{stats['total_tokens_out']:,}</div></div>
    <div class="card"><div class="label">Tool Calls</div><div class="value green">{stats['tool_calls']}</div></div>
    <div class="card"><div class="label">Errors</div><div class="value {'red' if stats['error_count'] else 'green'}">{stats['error_count']}</div></div>
    <div class="card"><div class="label">Duration</div><div class="value orange">{_duration_human(stats['session_duration_ms'])}</div></div>
  </div>

  {models_html}
  {tools_html}

  <div class="section">
    <h2>📋 Events</h2>
    {events_html}
  </div>

  <div class="footer">Generated by AgentLens SessionExporter · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
</div>
</body>
</html>"""

    def to_html(self, path: str) -> None:
        """Write HTML report to a file."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.as_html())

    # ── HTML helpers ────────────────────────────────────────────────

    def _render_events_table(self) -> str:
        if not self.session.events:
            return "<p style='color:var(--muted)'>No events recorded.</p>"
        rows = []
        for ev in self.session.events:
            badge_cls = {
                "llm_call": "badge-llm", "tool_call": "badge-tool",
                "decision": "badge-decision", "error": "badge-error",
            }.get(ev.event_type, "badge-generic")
            detail = ev.model or ""
            if ev.tool_call:
                detail = ev.tool_call.tool_name
            if ev.decision_trace:
                r = ev.decision_trace.reasoning
                detail = (r[:60] + "…") if len(r) > 60 else r
            rows.append(
                f"<tr>"
                f"<td><span class='badge {badge_cls}'>{_escape(ev.event_type)}</span></td>"
                f"<td>{_escape(detail)}</td>"
                f"<td>{ev.tokens_in + ev.tokens_out:,}</td>"
                f"<td>{_duration_human(ev.duration_ms)}</td>"
                f"<td style='color:var(--muted);font-size:0.75rem'>{_escape(ev.timestamp.strftime('%H:%M:%S'))}</td>"
                f"</tr>"
            )
        return (
            "<table><thead><tr><th>Type</th><th>Detail</th><th>Tokens</th>"
            "<th>Duration</th><th>Time</th></tr></thead><tbody>"
            + "\n".join(rows)
            + "</tbody></table>"
        )

    def _render_models_table(self) -> str:
        models = self._stats["models_used"]
        if not models:
            return ""
        rows = "".join(
            f"<tr><td>{_escape(m)}</td><td>{c}</td></tr>" for m, c in sorted(models.items(), key=lambda x: -x[1])
        )
        return (
            "<div class='section'><h2>🤖 Models</h2>"
            f"<table><thead><tr><th>Model</th><th>Calls</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )

    def _render_tools_list(self) -> str:
        tools = self._stats["unique_tools"]
        if not tools:
            return ""
        pills = "".join(f"<span class='pill'>{_escape(t)}</span>" for t in sorted(tools))
        return f"<div class='section'><h2>🔧 Tools Used</h2><div class='pills'>{pills}</div></div>"


def _escape(text: str) -> str:
    """HTML-escape a string."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
