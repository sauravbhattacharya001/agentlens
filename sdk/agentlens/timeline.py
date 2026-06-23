"""Session Timeline Renderer for AgentLens.

Transforms session event data into formatted timeline views (text, markdown,
HTML) for debugging, reporting, and sharing.  Pure SDK-side utility — no
backend changes needed.

:class:`TimelineRenderer` owns the stateful concerns (offset computation,
filtering, analysis, IO); the stateless multi-format rendering functions it
delegates to live in ``agentlens.timeline_render`` and the per-event styling
vocabulary lives in ``agentlens.timeline_format``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from agentlens._utils import parse_iso
# The duration / timestamp formatters live in timeline_format.py; they are
# re-exported here (see ``__all__``) so the historical public import paths
# ``agentlens.timeline._format_duration`` / ``_format_timestamp_offset`` keep
# working for callers that imported them directly.
from agentlens.timeline_format import (
    _format_duration,
    _format_timestamp_offset,
)
# Stateless multi-format render engine lives in timeline_render.py; each
# render_* method below is a thin delegator over the already-computed events
# and summary.
from agentlens.timeline_render import (
    _is_error_event,
    render_html as _render_html,
    render_markdown as _render_markdown,
    render_text as _render_text,
)

__all__ = [
    "TimelineRenderer",
    # Re-exported for backwards-compatible ``agentlens.timeline.*`` access.
    "_format_duration",
    "_format_timestamp_offset",
]


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
            for _i, e in enumerate(self.events):
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
        return parse_iso(s) or datetime(2000, 1, 1, tzinfo=timezone.utc)

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
        return _render_text(
            self.events,
            self.session,
            self.get_summary(),
            show_metadata=show_metadata,
            show_tokens=show_tokens,
            show_duration=show_duration,
            max_width=max_width,
        )

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
        return _render_markdown(
            self.events,
            self.session,
            self.get_summary(),
            show_metadata=show_metadata,
            show_tokens=show_tokens,
            show_duration=show_duration,
            include_toc=include_toc,
        )

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
        return _render_html(
            self.events,
            self.session,
            self.get_summary(),
            show_metadata=show_metadata,
            show_tokens=show_tokens,
            show_duration=show_duration,
            dark_mode=dark_mode,
            title=title,
        )

    # -- Filtering -----------------------------------------------------------

    def filter(
        self,
        *,
        event_types: list[str] | None = None,
        min_duration_ms: float | None = None,
        has_error: bool | None = None,
        model: str | None = None,
    ) -> TimelineRenderer:
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

        Raises:
            ValueError: if the path escapes the working/temp directory.
        """
        from agentlens.exporter import _validate_output_path
        safe = _validate_output_path(path)

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

        with open(safe, "w", encoding="utf-8") as f:
            f.write(content)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _is_error(event: dict) -> bool:
        return _is_error_event(event)
