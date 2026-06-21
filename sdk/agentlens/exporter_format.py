"""Pure value-formatting helpers for the offline session exporter.

This module holds the stateless primitives that
:class:`agentlens.exporter.SessionExporter` (and the flamegraph/timeline
HTML exporters) consume to render a session report: the output-path safety
guard (:func:`_validate_output_path`), the summary-statistics computation
(:func:`_session_stats`), the CSV row/column shaping (:func:`_event_to_row`,
:data:`_CSV_COLUMNS`), the ISO timestamp helper (:func:`_iso`), the
human-duration alias (:func:`_duration_human`), and the OWASP HTML escaper
(:func:`_escape`).  It is intentionally separated from ``exporter.py`` so the
reusable, side-effect-light helpers stay readable and are not interleaved with
the report-assembly engine.

There is no report-assembly logic here.  These symbols are re-exported from
``agentlens.exporter`` so existing import paths (e.g.
``agentlens.exporter._session_stats``, ``agentlens.exporter._validate_output_path``)
are unchanged.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agentlens._utils import format_duration as _duration_human
from agentlens.models import Session, AgentEvent


__all__ = [
    "_CSV_COLUMNS",
    "_duration_human",
    "_escape",
    "_event_to_row",
    "_iso",
    "_session_stats",
    "_validate_output_path",
]


def _validate_output_path(path: str) -> Path:
    """Validate that an output path is safe to write to.

    Resolves the path to its canonical form and rejects any path that
    escapes the current working directory or the system temp directory.
    This prevents directory-traversal attacks (CWE-22) when callers
    pass user-controlled file names.

    Raises:
        ValueError: if the resolved path is outside allowed directories.
    """
    resolved = Path(path).resolve()
    cwd = Path.cwd().resolve()
    tmp = Path(tempfile.gettempdir()).resolve()

    if resolved == cwd or resolved == tmp:
        raise ValueError(
            f"Export path must be a file, not a directory: {resolved}"
        )

    for allowed in (cwd, tmp):
        try:
            resolved.relative_to(allowed)
            return resolved
        except ValueError:
            continue

    raise ValueError(
        f"Export path must be within the working directory ({cwd}) "
        f"or temp directory ({tmp}). Resolved path: {resolved}"
    )


def _iso(dt: datetime | None) -> str | None:
    """Convert datetime to ISO string or None."""
    return dt.isoformat() if dt else None


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


def _escape(text: str) -> str:
    """HTML-escape a string for safe embedding in HTML content and attributes.

    Escapes all five characters recommended by OWASP for XSS prevention:
    ``& < > " '``
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
