"""Presentation vocabulary and formatting helpers for the session timeline.

This module holds the pure, stateless rendering primitives that
:class:`agentlens.timeline.TimelineRenderer` consumes: the per-event icon /
HTML-colour vocabulary (:data:`_ICONS`, :data:`_HTML_COLORS`) and the small
formatting functions (:func:`_icon`, :func:`_format_duration`,
:func:`_format_timestamp_offset`).  It is intentionally separated from
``timeline.py`` so the event-styling vocabulary stays readable and is not
interleaved with the multi-format renderer engine.

There is no event-traversal or rendering logic here.  These symbols are
re-exported from ``agentlens.timeline`` so existing import paths are
unchanged.
"""

from __future__ import annotations

import math

from agentlens._utils import format_duration as _format_duration_impl


_ICONS: dict[str, str] = {
    "session_start": "▶",
    "session_end": "⏹",
    "llm_call": "🧠",
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
    return _format_duration_impl(ms)


def _format_timestamp_offset(ms: float) -> str:
    """Format a millisecond offset as ``MM:SS.mmm``.

    Offsets come from ``event_ts - session_start`` timestamp math, so a missing
    or malformed timestamp can legitimately yield a non-finite value; guard it
    rather than raising ``ValueError`` deep inside a pure formatter. A negative
    offset (event before the session start, e.g. clock skew) is rendered with a
    leading sign and zero-padded, magnitude-based ``MM:SS.mmm`` instead of the
    previous ``int`` floor artefact (``-1:59.999`` for ``-1``).
    """
    if not math.isfinite(ms):
        return "--:--.---"
    sign = "-" if ms < 0 else ""
    total_s = abs(ms) / 1000.0
    minutes = int(total_s // 60)
    seconds = total_s - minutes * 60
    return f"{sign}{minutes:02d}:{seconds:06.3f}"
