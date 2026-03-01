"""Session Timeline Renderer for AgentLens.

Transforms session event data into formatted timeline views (text, markdown,
HTML) for debugging, reporting, and sharing.  Pure SDK-side utility — no
backend changes needed.
"""

from __future__ import annotations

import html as _html
import os
from typing import Any


# ---------------------------------------------------------------------------
# Icon / label helpers
# ---------------------------------------------------------------------------

_ICONS: dict[str, str] = {
    "session_start": "▶",
    "session_end": "⏹",
    "llm_call": "🤖",
    "tool_call": "🔧",
    "error": "❌",
    "decision": "💡",
    "generic": "●",
}

_HTML_COLORS: dict[str, str] = {
    "session_start": "#22c55e",
    "session_end": "#6b7280",
    "llm_call": "#3b82f6",
    "tool_call": "#f59e0b",
    "error": "#ef4444",
    "decision": "#8b5cf6",
    "generic": "#6b7280",
}


def _icon(event_type: str) -> str:
    return _ICONS.get(event_type, _ICONS["generic"])


def _format_duration(ms: float | None) -> str:
    if ms is None:
        return ""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _format_timestamp_offset(ms: float) -> str:
    """Format millisecond offset as MM:SS.mmm."""
    total_s = ms / 1000.0
    minutes = int(total_s // 60)
    seconds = total_s - minutes * 60
    return f"{minutes:02d}:{seconds:06.3f}"


def _event_start_ms(event: dict) -> float | None:
    return event.get("_offset_ms")


# ---------------------------------------------------------------------------
# TimelineRenderer
# ---------------------------------------------------------------------------


class TimelineRenderer:
    """Render a list of event dicts as a formatted timeline."""

    def __init__(self, events: list[dict], session: dict | None = None) -> None:
        self.events = list(events)
        self.session = session or {}
        self._compute_offsets()

    # -- offset computation --------------------------------------------------

    def _compute_offsets(self) -> None:
        """Compute _offset_ms for each event relative to the first."""
        if not self.events:
            return

        # Try to find a base timestamp
        base: str | None = None
        for e in self.events:
            ts = e.get("timestamp")
            if ts:
                base = ts
                break

        if base is None:
            for i, e in enumerate(self.events):
                e["_offset_ms"] = 0.0
            return

        base_dt = self._parse_iso(base)
        for e in self.events:
            ts = e.get("timestamp")
            if ts:
                dt = self._parse_iso(ts)
                e["_offset_ms"] = max(0.0, (dt - base_dt).total_seconds() * 1000)
            else:
                e["_offset_ms"] = 0.0

    @staticmethod
    def _parse_iso(s: str) -> Any:
        from datetime import datetime, timezone
        s = s.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime(2000, 1, 1, tzinfo=timezone.utc)

    # -- Core rendering ------------------------------------------------------

    def render_text(
        self,
        *,
        show_metadata: bool = True,
        show_tokens: bool = True,
        show_duration: bool = True,
        max_width: int = 100,
    ) -> str:
        """Render the session timeline as a plain-text string.

        Produces a box-drawing formatted timeline suitable for terminal output,
        with event icons, timestamps, metadata, token counts, and durations.

        Args:
            show_metadata: Include event metadata (model, tags, etc.) below each event.
            show_tokens: Show token in/out counts for events that have them.
            show_duration: Show duration_ms for events that have it.
            max_width: Maximum character width for the output (minimum 40).

        Returns:
            Multi-line string with the formatted timeline.
        """
        max_width = max(40, max_width)
        lines: list[str] = []

        # Header
        sid = self.session.get("session_id", "unknown")
        agent = self.session.get("agent_name", "")
        summary = self.get_summary()
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
        for ev in self.events:
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
                    err_msg = ev.get("output_data", {})
                    if isinstance(err_msg, dict):
                        err_msg = err_msg.get("error", err_msg.get("message", ""))
                    if err_msg:
                        meta_lines.append(str(err_msg))

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
        llm_events = [e for e in self.events if e.get("event_type") == "llm_call"]
        tool_events = [e for e in self.events if e.get("event_type") == "tool_call"]
        error_events = [e for e in self.events if e.get("event_type") == "error"]

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
        self,
        *,
        show_metadata: bool = True,
        show_tokens: bool = True,
        show_duration: bool = True,
        include_toc: bool = True,
    ) -> str:
        """Render the session timeline as a Markdown document.

        Produces a structured Markdown document with headings, a table of
        contents, event details with badges, and an optional summary section.
        Suitable for embedding in reports, wikis, or issue comments.

        Args:
            show_metadata: Include event metadata as sub-items.
            show_tokens: Show token in/out counts for each event.
            show_duration: Show event duration in milliseconds.
            include_toc: Prepend a table of contents with event type breakdown.

        Returns:
            Markdown-formatted string.
        """
        summary = self.get_summary()
        sid = self.session.get("session_id", "unknown")
        agent = self.session.get("agent_name", "")
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

        for ev in self.events:
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
                err = ev.get("output_data", {})
                if isinstance(err, dict):
                    err = err.get("error", err.get("message", ""))
                if err:
                    detail_parts.append(str(err))
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
        self,
        *,
        show_metadata: bool = True,
        show_tokens: bool = True,
        show_duration: bool = True,
        dark_mode: bool = False,
        title: str = "Session Timeline",
    ) -> str:
        """Render the session timeline as a self-contained HTML page.

        Produces a complete HTML document with inline CSS, event cards, a
        summary header, and optional dark mode styling. The output is fully
        self-contained (no external dependencies) and can be saved to a file
        or embedded in a dashboard.

        Args:
            show_metadata: Include event metadata in each card.
            show_tokens: Show token in/out counts on event cards.
            show_duration: Show event duration on event cards.
            dark_mode: Use dark background with light text.
            title: HTML page title and main heading.

        Returns:
            Complete HTML document as a string.
        """
        summary = self.get_summary()
        sid = self.session.get("session_id", "unknown")
        agent = self.session.get("agent_name", "")

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
        parts.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'>")
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
        max_dur = max((e.get("duration_ms") or 0 for e in self.events), default=1) or 1

        for ev in self.events:
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
                err = ev.get("output_data", {})
                if isinstance(err, dict):
                    err = err.get("error", err.get("message", ""))
                if err:
                    parts.append(f"<div class='error-details'>{h(str(err))}</div>")

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

    # -- Filtering -----------------------------------------------------------

    def filter(
        self,
        *,
        event_types: list[str] | None = None,
        min_duration_ms: float | None = None,
        has_error: bool | None = None,
        model: str | None = None,
    ) -> "TimelineRenderer":
        """Return a new TimelineRenderer with filtered events."""
        filtered = list(self.events)

        if event_types is not None:
            types_lower = [t.lower() for t in event_types]
            filtered = [e for e in filtered if e.get("event_type", "").lower() in types_lower]

        if min_duration_ms is not None:
            filtered = [e for e in filtered if (e.get("duration_ms") or 0) >= min_duration_ms]

        if has_error is True:
            filtered = [e for e in filtered if self._is_error(e)]
        elif has_error is False:
            filtered = [e for e in filtered if not self._is_error(e)]

        if model is not None:
            model_lower = model.lower()
            filtered = [e for e in filtered if (e.get("model") or "").lower() == model_lower]

        return TimelineRenderer(filtered, self.session)

    # -- Analysis helpers ----------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return aggregate summary of events."""
        total_tokens = 0
        error_count = 0
        models: set[str] = set()

        for e in self.events:
            total_tokens += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
            if self._is_error(e):
                error_count += 1
            m = e.get("model")
            if m:
                models.add(m)

        # Duration: span from first to last event (using offsets + last event's duration)
        total_dur = 0.0
        if self.events:
            last = self.events[-1]
            total_dur = (last.get("_offset_ms") or 0.0) + (last.get("duration_ms") or 0.0)

        return {
            "total_events": len(self.events),
            "total_duration_ms": total_dur,
            "total_tokens": total_tokens,
            "error_count": error_count,
            "models_used": sorted(models),
        }

    def get_critical_path(self) -> list[dict]:
        """Return the longest sequential chain of events by duration."""
        if not self.events:
            return []
        # Simple approach: all events sorted by offset form the sequential chain.
        # Return events that have duration, sorted by offset.
        with_dur = [e for e in self.events if e.get("duration_ms") is not None]
        if not with_dur:
            return list(self.events)
        # Return all events sorted by offset (they are sequential)
        return sorted(with_dur, key=lambda e: e.get("_offset_ms", 0.0))

    def get_slowest_events(self, n: int = 5) -> list[dict]:
        """Return the n slowest events by duration_ms, descending."""
        with_dur = [e for e in self.events if e.get("duration_ms") is not None]
        with_dur.sort(key=lambda e: e.get("duration_ms", 0), reverse=True)
        return with_dur[:n]

    def get_error_events(self) -> list[dict]:
        """Return all error events."""
        return [e for e in self.events if self._is_error(e)]

    # -- Export --------------------------------------------------------------

    def save(self, path: str, format: str = "auto") -> None:
        """Save rendered timeline to a file.

        Args:
            path: File path to write.
            format: One of ``"text"``, ``"md"``, ``"html"``, or ``"auto"``
                (detect from extension).
        """
        if format == "auto":
            ext = os.path.splitext(path)[1].lower()
            fmt_map = {".html": "html", ".htm": "html", ".md": "md", ".txt": "text"}
            format = fmt_map.get(ext, "text")

        if format == "html":
            content = self.render_html()
        elif format == "md":
            content = self.render_markdown()
        else:
            content = self.render_text()

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _is_error(event: dict) -> bool:
        if event.get("event_type") == "error":
            return True
        tc = event.get("tool_call")
        if isinstance(tc, dict):
            out = tc.get("tool_output")
            if isinstance(out, dict) and out.get("error"):
                return True
        return False
