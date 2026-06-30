"""Stateless prose-building engine for the session narrative.

This module holds the pure rendering primitives that
:class:`agentlens.narrative.NarrativeGenerator` consumes: the single-pass
event classifier, the per-tool / per-model aggregators, the per-section line
builders (timeline / decisions / errors), the summary + body composers, and the
duration formatter.  None of these functions read any generator state -- they
take events/config and return strings or aggregates -- so they live apart from
``narrative.py`` to keep the orchestration class thin and the prose vocabulary
readable.

There is no session-traversal orchestration here (that stays on
``NarrativeGenerator.generate``).  These symbols are imported by
``agentlens.narrative``; the data model itself lives in
``agentlens.narrative_types``.
"""

from __future__ import annotations

from agentlens.models import AgentEvent, Session
from agentlens._utils import format_duration_seconds as _fmt_seconds
from agentlens.narrative_types import (
    NarrativeStyle,
    ToolSummary,
)


def classify_events(
    events: list[AgentEvent],
) -> tuple[
    list[AgentEvent], list[AgentEvent], list[AgentEvent], list[AgentEvent]
]:
    """Bucket events by event_type in a single pass.

    Returns ``(llm_events, tool_events, decision_events, error_events)``.
    Single-pass classification keeps the cost at O(E) regardless of
    how many categories the caller cares about.
    """
    llm_events: list[AgentEvent] = []
    tool_events: list[AgentEvent] = []
    decision_events: list[AgentEvent] = []
    error_events: list[AgentEvent] = []
    for evt in events:
        et = evt.event_type
        if et == "llm_call":
            llm_events.append(evt)
        elif et == "tool_call":
            tool_events.append(evt)
        elif et == "decision":
            decision_events.append(evt)
        elif et == "error":
            error_events.append(evt)
    return llm_events, tool_events, decision_events, error_events


def build_tool_summaries(
    tool_events: list[AgentEvent],
) -> dict[str, ToolSummary]:
    """Aggregate per-tool call/failure/duration stats from tool events."""
    tool_map: dict[str, ToolSummary] = {}
    for e in tool_events:
        tc = e.tool_call
        if not tc:
            continue
        name = tc.tool_name
        ts = tool_map.get(name)
        if ts is None:
            ts = ToolSummary(tool_name=name)
            tool_map[name] = ts
        ts.call_count += 1
        if e.output_data and e.output_data.get("error"):
            ts.failure_count += 1
        else:
            ts.success_count += 1
        if tc.duration_ms:
            ts.total_duration_ms += tc.duration_ms
    for ts in tool_map.values():
        if ts.call_count:
            ts.avg_duration_ms = ts.total_duration_ms / ts.call_count
    return tool_map


def aggregate_models(llm_events: list[AgentEvent]) -> dict[str, list[int]]:
    """Aggregate (call_count, token_total) per model from LLM events.

    Returns a dict ``model -> [calls, tokens]``.  Empty when no LLM
    events carry a ``model`` value.
    """
    model_agg: dict[str, list[int]] = {}
    for e in llm_events:
        if not e.model:
            continue
        bucket = model_agg.get(e.model)
        if bucket is None:
            bucket = [0, 0]
            model_agg[e.model] = bucket
        bucket[0] += 1
        bucket[1] += e.tokens_in + e.tokens_out
    return model_agg


def build_summary(
    session: Session, event_count: int, total_tokens: int,
    cost: float, error_count: int, decision_count: int,
    tools: list[str], duration_s: float, style: NarrativeStyle,
) -> str:
    status_word = "completed" if session.status == "completed" else session.status
    dur = fmt_dur(duration_s)

    if style == NarrativeStyle.EXECUTIVE:
        parts = [f"Agent '{session.agent_name}' {status_word}"]
        if dur:
            parts[0] += f" in {dur}"
        parts.append(f"processing {event_count} events with {total_tokens:,} tokens")
        if cost > 0:
            parts.append(f"at an estimated cost of ${cost:.4f}")
        if error_count:
            parts.append(f"encountering {error_count} error(s)")
        return ", ".join(parts) + "."

    elif style == NarrativeStyle.CASUAL:
        msg = f"Session with '{session.agent_name}'"
        if dur:
            msg += f" ran for {dur}"
        msg += f" — {event_count} events, {total_tokens:,} tokens"
        if tools:
            msg += f", used {len(tools)} tool(s)"
        if error_count:
            msg += f", hit {error_count} error(s)"
        return msg + "."

    else:  # TECHNICAL
        parts = [
            f"session_id={session.session_id}",
            f"agent={session.agent_name}",
            f"status={status_word}",
            f"events={event_count}",
            f"tokens={total_tokens:,}",
        ]
        if dur:
            parts.append(f"duration={dur}")
        if cost > 0:
            parts.append(f"cost=${cost:.4f}")
        if error_count:
            parts.append(f"errors={error_count}")
        if decision_count:
            parts.append(f"decisions={decision_count}")
        if tools:
            parts.append(f"tools=[{','.join(tools)}]")
        return " | ".join(parts)


def build_body(
    session: Session, events: list[AgentEvent],
    llm_events: list[AgentEvent], tool_events: list[AgentEvent],
    decision_events: list[AgentEvent], error_events: list[AgentEvent],
    tool_map: dict[str, ToolSummary], total_tokens: int,
    cost: float, duration_s: float, style: NarrativeStyle,
) -> str:
    paragraphs: list[str] = []

    # Opening
    dur = fmt_dur(duration_s)
    if style == NarrativeStyle.EXECUTIVE:
        opening = (
            f"The agent '{session.agent_name}' executed a session "
            f"consisting of {len(events)} events"
        )
        if dur:
            opening += f" over {dur}"
        opening += "."
    elif style == NarrativeStyle.CASUAL:
        opening = f"Here's what happened in this '{session.agent_name}' session"
        if dur:
            opening += f" ({dur})"
        opening += ":"
    else:
        opening = (
            f"Session {session.session_id} ({session.agent_name}) processed "
            f"{len(events)} events"
        )
        if dur:
            opening += f" in {dur}"
        opening += f". Total tokens: {total_tokens:,}."
    paragraphs.append(opening)

    # LLM usage
    if llm_events:
        llm_tokens = sum(e.tokens_in + e.tokens_out for e in llm_events)
        avg_tok = llm_tokens // len(llm_events) if llm_events else 0
        if style == NarrativeStyle.CASUAL:
            paragraphs.append(
                f"Made {len(llm_events)} LLM call(s) using {llm_tokens:,} tokens "
                f"(~{avg_tok:,} per call)."
            )
        else:
            paragraphs.append(
                f"LLM interactions: {len(llm_events)} call(s), {llm_tokens:,} total tokens, "
                f"avg {avg_tok:,} tokens/call."
            )

    # Tools
    if tool_map:
        tool_names = sorted(tool_map.keys())
        total_calls = sum(t.call_count for t in tool_map.values())
        total_failures = sum(t.failure_count for t in tool_map.values())
        if style == NarrativeStyle.CASUAL:
            paragraphs.append(
                f"Used {len(tool_names)} tool(s) ({', '.join(tool_names)}) "
                f"a total of {total_calls} time(s)"
                + (f", with {total_failures} failure(s)" if total_failures else "")
                + "."
            )
        else:
            paragraphs.append(
                f"Tool usage: {total_calls} call(s) across {len(tool_names)} tool(s) "
                f"[{', '.join(tool_names)}]"
                + (f". Failures: {total_failures}." if total_failures else ".")
            )

    # Decisions
    if decision_events:
        if style == NarrativeStyle.CASUAL:
            paragraphs.append(f"The agent made {len(decision_events)} notable decision(s).")
        else:
            paragraphs.append(f"Decision points: {len(decision_events)}.")

    # Errors
    if error_events:
        if style == NarrativeStyle.CASUAL:
            paragraphs.append(f"Ran into {len(error_events)} error(s) during the session.")
        else:
            paragraphs.append(f"Errors encountered: {len(error_events)}.")

    # Cost
    if cost > 0:
        paragraphs.append(f"Estimated cost: ${cost:.4f}.")

    # Status
    if session.status != "active":
        if style == NarrativeStyle.CASUAL:
            paragraphs.append(f"Session ended with status: {session.status}.")
        else:
            paragraphs.append(f"Final status: {session.status}.")

    return "\n\n".join(paragraphs)


def build_timeline(events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
    lines: list[str] = []
    for _i, e in enumerate(events):
        ts = e.timestamp.strftime("%H:%M:%S")
        if e.event_type == "llm_call":
            model_info = f" ({e.model})" if e.model else ""
            tok = e.tokens_in + e.tokens_out
            if style == NarrativeStyle.CASUAL:
                lines.append(f"- {ts} — LLM call{model_info}, {tok:,} tokens")
            else:
                lines.append(f"- `{ts}` **LLM call**{model_info}: {tok:,} tokens")
        elif e.event_type == "tool_call":
            name = e.tool_call.tool_name if e.tool_call else "unknown"
            dur = f" ({e.tool_call.duration_ms:.0f}ms)" if e.tool_call and e.tool_call.duration_ms else ""
            lines.append(f"- `{ts}` **Tool: {name}**{dur}")
        elif e.event_type == "decision":
            reason = ""
            if e.decision_trace and e.decision_trace.reasoning:
                reason = f": {e.decision_trace.reasoning[:80]}"
            lines.append(f"- `{ts}` **Decision**{reason}")
        elif e.event_type == "error":
            msg = ""
            if e.output_data and e.output_data.get("error"):
                msg = f": {str(e.output_data['error'])[:80]}"
            lines.append(f"- `{ts}` ⚠️ **Error**{msg}")
        else:
            lines.append(f"- `{ts}` {e.event_type}")
    return lines if lines else ["No events recorded."]


def build_decisions(events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
    lines: list[str] = []
    for i, e in enumerate(events, 1):
        dt = e.decision_trace
        if not dt:
            lines.append(f"{i}. Decision at step {dt.step if dt else '?'}")
            continue
        conf = f" (confidence: {dt.confidence:.0%})" if dt.confidence is not None else ""
        lines.append(f"{i}. **{dt.reasoning[:120]}**{conf}")
        if dt.alternatives_considered:
            alts = ", ".join(dt.alternatives_considered[:5])
            lines.append(f"   Alternatives considered: {alts}")
    return lines


def build_errors(events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
    lines: list[str] = []
    for e in events:
        ts = e.timestamp.strftime("%H:%M:%S")
        msg = "Unknown error"
        if e.output_data and e.output_data.get("error"):
            msg = str(e.output_data["error"])[:200]
        elif e.output_data and e.output_data.get("message"):
            msg = str(e.output_data["message"])[:200]
        lines.append(f"- `{ts}` — {msg}")
    return lines


def fmt_dur(seconds: float) -> str:
    """Format a duration in seconds, returning ``""`` for non-positive input.

    Delegates the ``Ns`` / ``Nm Ns`` / ``Nh Nm`` formatting to the shared
    :func:`agentlens._utils.format_duration_seconds` helper; the only extra
    behaviour here is suppressing zero/negative durations to an empty string
    so summaries omit the duration clause entirely.
    """
    if seconds <= 0:
        return ""
    return _fmt_seconds(seconds)
