"""Transcript exporter - render an AgentLens session as a contract-compliant
transcript for agent-eval.

This is the AgentLens -> agent-eval bridge. Where a hand-written transcript is a
*self-report* (prose the agent wrote about itself), a transcript exported from an
AgentLens session is *evidence-backed*: every section is derived from captured
trace data - real tool calls, real timing, the recorded session status - not the
agent's word for it.

The output conforms to ``transcript-contract@v1`` (see the agent-eval CONTRACT.md),
so it can be parsed, validated (``agent-eval validate``), and scored directly.

Mapping
-------
======================  =================================================
Contract section        AgentLens source (captured evidence)
======================  =================================================
``# <title>``           ``agent_name`` + ``started_at``
``## Task``             session metadata ``task`` / first event input
``## Actions Taken``    ``tool_call`` events (name + summarized input)
``## Key Outputs``      final event output + tool outputs
``## Outcome``          ``session.status`` (completed->pass, error->fail,
                        active->IN-PROGRESS) - trusted, not self-reported
``## Errors & Retries`` events with ``event_type == "error"``
``## Duration``         ``ended_at - started_at`` - a trusted clock
======================  =================================================
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agentlens.models import AgentEvent, Session

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


class TranscriptExporter:
    """Render an AgentLens :class:`Session` (or session-shaped dict) as a
    ``transcript-contract@v1`` markdown document.

    Parameters
    ----------
    title_prefix:
        Optional label used in the ``#`` title. Defaults to the session's
        ``agent_name``.
    timezone_label:
        Cosmetic timezone label appended to title/duration times. Times are
        formatted in UTC; this only changes the printed label. Defaults to
        ``"UTC"``.
    """

    def __init__(self, *, timezone_label: str = "UTC") -> None:
        self.timezone_label = timezone_label

    # ----- section builders -------------------------------------------------

    def _title(self, session: dict[str, Any]) -> str:
        name = session.get("agent_name") or "Agent"
        started = session.get("started_at")
        when = _fmt_ts(started)
        return f"# {name} Run - {when}"

    def _task(self, session: dict[str, Any], events: list[dict[str, Any]]) -> str:
        meta = session.get("metadata") or {}
        # Prefer an explicit task in metadata.
        for key in ("task", "prompt", "goal", "description"):
            if meta.get(key):
                return str(meta[key]).strip()
        # Fall back to the first event's input.
        for ev in events:
            summary = _summarize(ev.get("input_data"))
            if summary:
                return summary
        return "(no task recorded)"

    def _actions(self, events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        n = 0
        for ev in events:
            tool = _get_tool(ev)
            if tool and tool.get("tool_name"):
                n += 1
                inp = _summarize(tool.get("tool_input"))
                item = f"{n}. `{tool['tool_name']}`"
                if inp:
                    item += f" - {inp}"
                lines.append(item)
            elif ev.get("event_type") == "decision":
                dt = ev.get("decision_trace") or {}
                reasoning = _summarize(dt.get("reasoning") or ev.get("reasoning"))
                if reasoning:
                    n += 1
                    lines.append(f"{n}. (decision) {reasoning}")
        if not lines:
            return "(no actions recorded)"
        return "\n".join(lines)

    def _outputs(self, events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        # Tool outputs, in order.
        for ev in events:
            tool = _get_tool(ev)
            if tool and tool.get("tool_output") is not None:
                out = _summarize(tool.get("tool_output"))
                if out:
                    lines.append(f"- `{tool.get('tool_name', 'tool')}` -> {out}")
        # Final event's output_data, if any (the agent's terminal result).
        for ev in reversed(events):
            out = _summarize(ev.get("output_data"))
            if out:
                lines.append(f"- Final output: {out}")
                break
        if not lines:
            return "(no outputs recorded)"
        return "\n".join(lines)

    def _outcome(self, session: dict[str, Any]) -> str:
        status = (session.get("status") or "").lower()
        if status in _STATUS_TO_OUTCOME:
            token = _STATUS_TO_OUTCOME[status]
            reason = "session completed" if token == "pass" else f"session status: {status}"
            return f"{token} - {reason}"
        # active / unknown -> not finished
        return "IN-PROGRESS"

    def _errors(self, events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for ev in events:
            if ev.get("event_type") == "error":
                detail = _summarize(ev.get("output_data") or ev.get("input_data"))
                lines.append(f"- {detail or 'error event recorded'}")
        if not lines:
            return "(none)"
        return "\n".join(lines)

    def _duration(self, session: dict[str, Any]) -> str:
        started = session.get("started_at")
        ended = session.get("ended_at")
        start_dt = started if isinstance(started, datetime) else _parse_iso(started)
        end_dt = ended if isinstance(ended, datetime) else _parse_iso(ended)
        text = _fmt_duration(start_dt, end_dt)
        if self.timezone_label and self.timezone_label != "UTC":
            text += f"  [{self.timezone_label}]"
        return text

    # ----- public API -------------------------------------------------------

    def render(self, session: Session | dict[str, Any]) -> str:
        """Render the session as a contract-compliant markdown transcript."""
        if isinstance(session, Session):
            sess_dict = session.to_api_dict()
            events = [_as_event_dict(e) for e in session.events]
        else:
            sess_dict = dict(session)
            events = [_as_event_dict(e) for e in (session.get("events") or [])]

        parts = [
            self._title(sess_dict),
            "",
            "## Task",
            self._task(sess_dict, events),
            "",
            "## Actions Taken",
            self._actions(events),
            "",
            "## Key Outputs",
            self._outputs(events),
            "",
            "## Outcome",
            self._outcome(sess_dict),
            "",
            "## Errors & Retries",
            self._errors(events),
            "",
            "## Duration",
            self._duration(sess_dict),
            "",
        ]
        return "\n".join(parts)


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def export_transcript(
    session: Session | dict[str, Any],
    *,
    timezone_label: str = "UTC",
) -> str:
    """Render an AgentLens session as a ``transcript-contract@v1`` transcript.

    This is the evidence-backed producer for agent-eval: validate the result
    with ``agent-eval validate`` and score it with agent-eval's monitor.

    Args:
        session: An AgentLens :class:`~agentlens.models.Session`, or a
            session-shaped dict (e.g. the output of ``export_session``) that
            includes an ``events`` list.
        timezone_label: Cosmetic timezone label for printed times (times are
            formatted in UTC).

    Returns:
        A markdown string conforming to the agent-eval transcript contract.
    """
    return TranscriptExporter(timezone_label=timezone_label).render(session)
