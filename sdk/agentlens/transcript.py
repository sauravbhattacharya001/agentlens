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

from datetime import datetime
from typing import Any

from agentlens.models import Session

# The value vocabulary (status maps, contract version) and the pure
# formatting/normalization helpers live in transcript_format.py; re-imported
# here so they resolve in the engine body and the public import paths (e.g.
# ``agentlens.transcript._summarize``, ``agentlens.transcript.TRANSCRIPT_CONTRACT_VERSION``)
# stay unchanged.
from agentlens.transcript_format import (
    TRANSCRIPT_CONTRACT_VERSION,
    _STATUS_TO_EXIT_STATUS,
    _STATUS_TO_OUTCOME,
    _as_event_dict,
    _fmt_duration,
    _fmt_ts,
    _get_tool,
    _parse_iso,
    _summarize,
)

# Public surface of this module (also the re-exported names above).  Declared
# explicitly so the re-imported value-vocabulary constant is recognised as a
# re-export and the ``agentlens.transcript`` API stays intentional.
__all__ = [
    "TRANSCRIPT_CONTRACT_VERSION",
    "TranscriptExporter",
    "export_run_metadata",
    "export_transcript",
]


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

    def to_run_metadata(self, session: Session | dict[str, Any]) -> dict[str, Any]:
        """Extract agent-eval ``RunMetadata`` from the session's trusted fields.

        This is the GROUND-TRUTH side-channel for agent-eval's ``verification``
        check. Where the exported transcript carries the agent's self-report,
        this carries what AgentLens actually recorded: the session status
        (mapped to ``exitStatus``) and the wall-clock start/end/duration. The
        verification check cross-checks one against the other.

        Returns a plain dict shaped like agent-eval's ``RunMetadata``
        (``exitStatus``, ``startedAt``, ``endedAt``, ``durationMs``), suitable
        for JSON serialization. Keys with no data are omitted.
        """
        if isinstance(session, Session):
            sess_dict = session.to_api_dict()
        else:
            sess_dict = dict(session)

        meta: dict[str, Any] = {}

        status = (sess_dict.get("status") or "").lower()
        if status in _STATUS_TO_EXIT_STATUS:
            meta["exitStatus"] = _STATUS_TO_EXIT_STATUS[status]

        started = sess_dict.get("started_at")
        ended = sess_dict.get("ended_at")
        if started:
            meta["startedAt"] = started if isinstance(started, str) else _parse_iso(started)
            if isinstance(meta["startedAt"], datetime):
                meta["startedAt"] = meta["startedAt"].isoformat()
        if ended:
            meta["endedAt"] = ended if isinstance(ended, str) else _parse_iso(ended)
            if isinstance(meta["endedAt"], datetime):
                meta["endedAt"] = meta["endedAt"].isoformat()

        # Prefer an explicit duration_ms; otherwise derive from start/end.
        dur = sess_dict.get("duration_ms")
        start_dt = _parse_iso(started)
        end_dt = _parse_iso(ended)
        if dur is not None:
            meta["durationMs"] = float(dur)
        elif start_dt is not None and end_dt is not None:
            meta["durationMs"] = max(0.0, (end_dt - start_dt).total_seconds() * 1000.0)

        return meta


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


def export_run_metadata(session: Session | dict[str, Any]) -> dict[str, Any]:
    """Extract agent-eval ``RunMetadata`` from an AgentLens session.

    Pairs with :func:`export_transcript`: the transcript is the agent's
    *claim*, this metadata is the *ground truth* (recorded status + wall-clock)
    that agent-eval's ``verification`` check grades the claim against. Together
    they make the AgentLens -> agent-eval path self-verifying.

    Args:
        session: An AgentLens :class:`~agentlens.models.Session`, or a
            session-shaped dict (e.g. the output of ``export_session``).

    Returns:
        A dict shaped like agent-eval's ``RunMetadata`` (``exitStatus``,
        ``startedAt``, ``endedAt``, ``durationMs``); keys with no data omitted.
    """
    return TranscriptExporter().to_run_metadata(session)
