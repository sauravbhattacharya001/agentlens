"""Stateless multi-format rendering engine for the session timeline.

This module holds the pure rendering functions that
:class:`agentlens.timeline.TimelineRenderer` consumes: :func:`render_text`,
:func:`render_markdown`, and :func:`render_html`.  None of these functions read
any renderer state -- each one takes the already-computed event list, the
session dict, and the pre-aggregated summary (plus presentation options) and
returns a formatted string.  They live apart from ``timeline.py`` so the
orchestration class (offset computation, filtering, analysis, IO) stays thin
and the multi-format presentation vocabulary is readable in one place.

There is no event-traversal orchestration or state here.  The renderer class
on ``agentlens.timeline`` computes ``_offset_ms`` / the summary and delegates
to these functions; the per-event styling vocabulary itself lives in
``agentlens.timeline_format``.
"""

from __future__ import annotations

import html as _html
from typing import Any

from agentlens.timeline_format import (
    _HTML_COLORS,
    _format_duration,
    _format_timestamp_offset,
    _icon,
)


def _is_error_event(event: dict) -> bool:
    """True if an event represents an error (explicit type or failed tool)."""
    if event.get("event_type") == "error":
        return True
    tc = event.get("tool_call")
    if isinstance(tc, dict):
        out = tc.get("tool_output")
        if isinstance(out, dict) and out.get("error"):
            return True
    return False


def _error_message(event: dict) -> str:
    """Extract a human-readable error string from an event's output_data."""
    err = event.get("output_data", {})
    if isinstance(err, dict):
        err = err.get("error", err.get("message", ""))
    return str(err) if err else ""


def render_text(
    events: list[dict],
    session: dict,
    summary: dict[str, Any],
    *,
    show_metadata: bool = True,
    show_tokens: bool = True,
    show_duration: bool = True,
    max_width: int = 100,
) -> str:
    """Render a session timeline as a plain-text, box-drawing string.

    Args:
        events: Event dicts with ``_offset_ms`` already computed.
        session: Session metadata (``session_id``, ``agent_name``).
        summary: Pre-aggregated summary from ``TimelineRenderer.get_summary``.
        show_metadata: Include event metadata below each event.
        show_tokens: Show token in/out counts for events that have them.
        show_duration: Show duration_ms for events that have it.
        max_width: Maximum character width for the output (minimum 40).

    Returns:
        Multi-line string with the formatted timeline.
    """
    max_width = max(40, max_width)
    lines: list[str] = []

    # Header
    sid = session.get("session_id", "unknown")
    agent = session.get("agent_name", "")
    dur_str = _format_duration(summary["total_duration_ms"])

    header_line = "═" * max_width
    lines.append(header_line)
    lines.append(f" Session Timeline: {sid}")
    parts = []
    if agent:
        parts.append(f"Agent: {agent}")
    if dur_str:
        parts.append(f"Duration: {dur_str}")
    parts.append(f"Events: {summary['total_events']}")
    lines.append(" " + " | ".join(parts))
    lines.append(header_line)
    lines.append("")

    # Events
    for ev in events:
        offset = ev.get("_offset_ms", 0.0)
        ts_str = _format_timestamp_offset(offset)
        etype = ev.get("event_type", "generic")
        icon = _icon(etype)

        label = etype.upper()
        model = ev.get("model")
        tool_name = None
        tc = ev.get("tool_call")
        if isinstance(tc, dict):
            tool_name = tc.get("tool_name")

        suffix = ""
        if model:
            label += f" [{model}]"
        elif tool_name:
            label += f" [{tool_name}]"

        dur = ev.get("duration_ms")
        if show_duration and dur is not None:
            suffix = f" ──── {_format_duration(dur)}"

        lines.append(f" {ts_str} ┃ {icon} {label}{suffix}")

        # Metadata lines
        meta_lines: list[str] = []
        if show_metadata:
            if etype == "session_start" and agent:
                meta_lines.append(f"agent: {agent}")
            if etype == "error":
                err_msg = _error_message(ev)
                if err_msg:
                    meta_lines.append(err_msg)

        if show_tokens:
            tin = ev.get("tokens_in", 0) or 0
            tout = ev.get("tokens_out", 0) or 0
            if tin or tout:
                total = tin + tout
                meta_lines.append(f"tokens: {tin} → {tout} ({total} total)")

        if show_metadata:
            # Check for reasoning
            dt = ev.get("decision_trace")
            if isinstance(dt, dict) and dt.get("reasoning"):
                meta_lines.append("⚠ has_reasoning")

            # Session end total tokens
            if etype == "session_end":
                tt = summary["total_tokens"]
                if tt:
                    meta_lines.append(f"total_tokens: {tt}")

            # Tool status
            if tool_name and etype == "tool_call":
                tc_dict = ev.get("tool_call", {})
                if isinstance(tc_dict, dict):
                    out = tc_dict.get("tool_output")
                    if isinstance(out, dict) and out.get("error"):
                        meta_lines.append("status: error")
                    else:
                        meta_lines.append("status: success")

        pad = " " * len(ts_str)
        for ml in meta_lines:
            lines.append(f" {pad} ┃   {ml}")

    # Summary footer
    lines.append("")
    lines.append(f"─── Summary {'─' * (max_width - 14)}")
    llm_events = [e for e in events if e.get("event_type") == "llm_call"]
    tool_events = [e for e in events if e.get("event_type") == "tool_call"]
    error_events = [e for e in events if e.get("event_type") == "error"]

    line1_parts = [
        f"Total: {summary['total_events']} events",
    ]
    if dur_str:
        line1_parts.append(dur_str)
    line1_parts.append(f"{summary['total_tokens']} tokens")
    lines.append(f" {' | '.join(line1_parts)}")

    line2_parts = []
    if llm_events:
        avg_dur = sum(e.get("duration_ms", 0) or 0 for e in llm_events) / len(llm_events)
        line2_parts.append(f"LLM calls: {len(llm_events)} (avg {_format_duration(avg_dur)})")
    line2_parts.append(f"Tools: {len(tool_events)}")
    line2_parts.append(f"Errors: {len(error_events)}")
    lines.append(f" {' | '.join(line2_parts)}")

    return "\n".join(lines)


def render_markdown(
    events: list[dict],
    session: dict,
    summary: dict[str, Any],
    *,
    show_metadata: bool = True,
    show_tokens: bool = True,
    show_duration: bool = True,
    include_toc: bool = True,
) -> str:
    """Render a session timeline as a structured Markdown document.

    Args:
        events: Event dicts with ``_offset_ms`` already computed.
        session: Session metadata (``session_id``, ``agent_name``).
        summary: Pre-aggregated summary from ``TimelineRenderer.get_summary``.
        show_metadata: Include the metadata header line.
        show_tokens: Show token in/out counts for each event.
        show_duration: Show event duration in milliseconds.
        include_toc: Prepend a table of contents.

    Returns:
        Markdown-formatted string.
    """
    sid = session.get("session_id", "unknown")
    agent = session.get("agent_name", "")
    lines: list[str] = []

    lines.append(f"# Session Timeline: {sid}")
    lines.append("")
    if show_metadata:
        parts = []
        if agent:
            parts.append(f"**Agent:** {agent}")
        parts.append(f"**Duration:** {_format_duration(summary['total_duration_ms'])}")
        parts.append(f"**Events:** {summary['total_events']}")
        parts.append(f"**Tokens:** {summary['total_tokens']}")
        lines.append(" | ".join(parts))
        lines.append("")

    if include_toc:
        lines.append("## Table of Contents")
        lines.append("- [Events](#events)")
        lines.append("- [Summary](#summary)")
        lines.append("")

    lines.append("## Events")
    lines.append("")

    # Table header
    cols = ["Time", "Type", "Details"]
    if show_duration:
        cols.append("Duration")
    if show_tokens:
        cols.append("Tokens")
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")

    for ev in events:
        offset = ev.get("_offset_ms", 0.0)
        ts = _format_timestamp_offset(offset)
        etype = ev.get("event_type", "generic")
        icon = _icon(etype)
        detail_parts: list[str] = []
        model = ev.get("model")
        tc = ev.get("tool_call")
        tool_name = tc.get("tool_name") if isinstance(tc, dict) else None
        if model:
            detail_parts.append(f"model: {model}")
        if tool_name:
            detail_parts.append(f"tool: {tool_name}")
        if etype == "error":
            err = _error_message(ev)
            if err:
                detail_parts.append(err)
        detail = ", ".join(detail_parts) if detail_parts else "-"

        row = [ts, f"{icon} {etype.upper()}", detail]
        if show_duration:
            dur = ev.get("duration_ms")
            row.append(_format_duration(dur) if dur else "-")
        if show_tokens:
            tin = ev.get("tokens_in", 0) or 0
            tout = ev.get("tokens_out", 0) or 0
            row.append(f"{tin}→{tout}" if (tin or tout) else "-")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total events:** {summary['total_events']}")
    lines.append(f"- **Total duration:** {_format_duration(summary['total_duration_ms'])}")
    lines.append(f"- **Total tokens:** {summary['total_tokens']}")
    lines.append(f"- **Errors:** {summary['error_count']}")
    if summary["models_used"]:
        lines.append(f"- **Models:** {', '.join(summary['models_used'])}")

    return "\n".join(lines)


def render_html(
    events: list[dict],
    session: dict,
    summary: dict[str, Any],
    *,
    show_metadata: bool = True,
    show_tokens: bool = True,
    show_duration: bool = True,
    dark_mode: bool = False,
    title: str = "Session Timeline",
) -> str:
    """Render a session timeline as a self-contained HTML page.

    The output is a complete HTML document with inline CSS (no external
    dependencies) and can be saved to a file or embedded in a dashboard.

    Args:
        events: Event dicts with ``_offset_ms`` already computed.
        session: Session metadata (``session_id``, ``agent_name``).
        summary: Pre-aggregated summary from ``TimelineRenderer.get_summary``.
        show_metadata: Include the metadata header line.
        show_tokens: Show token in/out counts on event cards.
        show_duration: Show event duration on event cards.
        dark_mode: Use dark background with light text.
        title: HTML page title and main heading.

    Returns:
        Complete HTML document as a string.
    """
    sid = session.get("session_id", "unknown")
    agent = session.get("agent_name", "")

    bg = "#1a1a2e" if dark_mode else "#ffffff"
    fg = "#e0e0e0" if dark_mode else "#333333"
    card_bg = "#16213e" if dark_mode else "#f9fafb"
    border = "#0f3460" if dark_mode else "#e5e7eb"

    css = f"""
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: {bg}; color: {fg}; margin: 0; padding: 20px; }}
    .header {{ text-align: center; margin-bottom: 24px; }}
    .header h1 {{ margin: 0 0 8px 0; }}
    .meta {{ color: #888; font-size: 14px; }}
    .event-card {{ background: {card_bg}; border: 1px solid {border};
                   border-radius: 8px; padding: 12px 16px; margin: 8px 0;
                   border-left: 4px solid #6b7280; }}
    .event-time {{ font-family: monospace; font-size: 12px; color: #888; }}
    .event-type {{ font-weight: bold; margin-left: 8px; }}
    .event-duration {{ font-size: 12px; color: #888; margin-left: 8px; }}
    .token-badge {{ display: inline-block; background: #e0e7ff; color: #3730a3;
                    border-radius: 4px; padding: 2px 6px; font-size: 11px; margin-left: 8px; }}
    .error-details {{ color: #ef4444; margin-top: 4px; font-size: 13px; }}
    .summary {{ background: {card_bg}; border: 1px solid {border};
                border-radius: 8px; padding: 16px; margin-top: 24px; }}
    .duration-bar {{ display: inline-block; height: 6px; background: #3b82f6;
                     border-radius: 3px; margin-left: 8px; vertical-align: middle; }}
    """

    h = _html.escape
    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>{h(title)}</title>")
    parts.append(f"<style>{css}</style></head><body>")
    parts.append(f"<div class='header'><h1>{h(title)}</h1>")
    if show_metadata:
        meta_parts = []
        if agent:
            meta_parts.append(f"Agent: {h(agent)}")
        meta_parts.append(f"Session: {h(sid)}")
        meta_parts.append(f"Duration: {_format_duration(summary['total_duration_ms'])}")
        meta_parts.append(f"Events: {summary['total_events']}")
        parts.append(f"<div class='meta'>{' | '.join(meta_parts)}</div>")
    parts.append("</div>")

    # Find max duration for bar scaling
    max_dur = max((e.get("duration_ms") or 0 for e in events), default=1) or 1

    for ev in events:
        etype = ev.get("event_type", "generic")
        color = _HTML_COLORS.get(etype, _HTML_COLORS["generic"])
        offset = ev.get("_offset_ms", 0.0)
        ts = _format_timestamp_offset(offset)
        icon = _icon(etype)

        parts.append(f"<div class='event-card' style='border-left-color:{color}'>")
        parts.append(f"<span class='event-time'>{ts}</span>")
        parts.append(f"<span class='event-type'>{icon} {h(etype.upper())}</span>")

        model = ev.get("model")
        tc = ev.get("tool_call")
        tool_name = tc.get("tool_name") if isinstance(tc, dict) else None
        if model:
            parts.append(f"<span class='event-duration'>[{h(model)}]</span>")
        if tool_name:
            parts.append(f"<span class='event-duration'>[{h(tool_name)}]</span>")

        dur = ev.get("duration_ms")
        if show_duration and dur is not None:
            bar_w = max(2, int(80 * dur / max_dur))
            parts.append(f"<span class='event-duration'>{_format_duration(dur)}</span>")
            parts.append(f"<span class='duration-bar' style='width:{bar_w}px'></span>")

        if show_tokens:
            tin = ev.get("tokens_in", 0) or 0
            tout = ev.get("tokens_out", 0) or 0
            if tin or tout:
                parts.append(f"<span class='token-badge'>{tin}→{tout}</span>")

        if etype == "error":
            err = _error_message(ev)
            if err:
                parts.append(f"<div class='error-details'>{h(err)}</div>")

        parts.append("</div>")

    # Summary
    parts.append("<div class='summary'><h3>Summary</h3>")
    parts.append(f"<p>Total: {summary['total_events']} events | "
                  f"{_format_duration(summary['total_duration_ms'])} | "
                  f"{summary['total_tokens']} tokens | "
                  f"{summary['error_count']} errors</p>")
    if summary["models_used"]:
        parts.append(f"<p>Models: {', '.join(h(m) for m in summary['models_used'])}</p>")
    parts.append("</div></body></html>")

    return "".join(parts)
