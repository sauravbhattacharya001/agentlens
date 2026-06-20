"""Value vocabulary and pure formatting helpers for the transcript exporter.

This module holds the stateless primitives that
:class:`agentlens.transcript.TranscriptExporter` consumes to render a
``transcript-contract@v1`` document: the status->outcome / status->exitStatus
maps and the contract-version constant (the *value vocabulary*), plus the small
formatting/normalization functions (:func:`_fmt_ts`, :func:`_fmt_duration`,
:func:`_summarize`, :func:`_as_event_dict`, :func:`_get_tool`,
:func:`_parse_iso`).  It is intentionally separated from ``transcript.py`` so
the contract vocabulary stays readable and is not interleaved with the
section-building engine.

There is no session-traversal or section-assembly logic here.  These symbols
are re-exported from ``agentlens.transcript`` so existing import paths are
unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agentlens.models import AgentEvent


# The contract version this exporter targets. Keep in sync with agent-eval's
# TRANSCRIPT_CONTRACT_V1.version.
TRANSCRIPT_CONTRACT_VERSION = "transcript-contract@v1"

# Map an AgentLens session status to a contract outcome token.
_STATUS_TO_OUTCOME: dict[str, str] = {
    "completed": "pass",
    "error": "fail",
    "failed": "fail",
    # "active" / anything unfinished -> IN-PROGRESS (handled explicitly below)
}

# Map an AgentLens session status to an agent-eval RunMetadata.exitStatus.
# This is the GROUND-TRUTH status the verification check grades the transcript
# against - distinct from the self-reported `## Outcome`.
_STATUS_TO_EXIT_STATUS: dict[str, str] = {
    "completed": "ok",
    "error": "error",
    "failed": "error",
    "timeout": "timeout",
    "killed": "killed",
    "active": "running",
}

_MAX_VALUE_LEN = 200


def _fmt_ts(ts: datetime | str | None) -> str:
    """Format a timestamp as a compact, human-readable UTC string."""
    if ts is None:
        return "unknown"
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_duration(start: datetime | None, end: datetime | None) -> str:
    """Render 'start -> end (N minutes)' from two timestamps."""
    if start is None:
        return "unknown"
    start_s = _fmt_ts(start)
    if end is None:
        return f"{start_s} -> (in progress)"
    end_s = _fmt_ts(end)
    secs = max(0.0, (end - start).total_seconds())
    if secs < 90:
        human = f"{secs:.0f} seconds"
    else:
        human = f"{secs / 60:.0f} minutes"
    return f"{start_s} -> {end_s} ({human})"


def _summarize(value: Any) -> str:
    """Render an input/output value compactly for a list item."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())  # collapse whitespace/newlines
    if len(text) > _MAX_VALUE_LEN:
        text = text[: _MAX_VALUE_LEN - 1].rstrip() + "\u2026"
    return text


def _as_event_dict(event: AgentEvent | dict[str, Any]) -> dict[str, Any]:
    """Normalize an event (model or backend dict) into a plain dict."""
    if isinstance(event, AgentEvent):
        return event.model_dump(mode="json", exclude_none=False)
    return event


def _get_tool(event: dict[str, Any]) -> dict[str, Any] | None:
    tc = event.get("tool_call")
    if isinstance(tc, dict):
        return tc
    # Some events carry tool fields inline.
    if event.get("tool_name"):
        return {
            "tool_name": event.get("tool_name"),
            "tool_input": event.get("tool_input"),
            "tool_output": event.get("tool_output"),
        }
    return None


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
