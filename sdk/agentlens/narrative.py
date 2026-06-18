"""Session Narrative Generator — auto-generate human-readable session summaries.

Transform raw agent session data into structured, readable narratives that
describe what happened, key decisions made, tools used, errors encountered,
and overall outcomes — useful for logs, reports, and stakeholder updates.

Usage::

    from agentlens.narrative import NarrativeGenerator, NarrativeConfig

    gen = NarrativeGenerator()

    # Generate from a session
    narrative = gen.generate(session)
    print(narrative.summary)       # One-line summary
    print(narrative.body)          # Full narrative text
    print(narrative.sections)      # Structured sections

    # With custom config
    config = NarrativeConfig(
        include_tools=True,
        include_decisions=True,
        include_costs=True,
        max_steps=50,
        style="technical",       # or "executive" or "casual"
    )
    narrative = gen.generate(session, config=config)

    # Export
    print(narrative.to_markdown())
    data = narrative.to_dict()
"""

from __future__ import annotations

from agentlens.models import AgentEvent, Session
from agentlens.narrative_types import (
    Narrative,
    NarrativeConfig,
    NarrativeSection,
    NarrativeStyle,
    ToolSummary,
)

__all__ = [
    "Narrative",
    "NarrativeConfig",
    "NarrativeGenerator",
    "NarrativeSection",
    "NarrativeStyle",
    "ToolSummary",
]


class NarrativeGenerator:
    """Generates human-readable narratives from agent sessions."""

    @staticmethod
    def _classify_events(
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

    @staticmethod
    def _build_tool_summaries(
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

    @staticmethod
    def _aggregate_models(llm_events: list[AgentEvent]) -> dict[str, list[int]]:
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

    def generate(self, session: Session, config: NarrativeConfig | None = None) -> Narrative:
        """Generate a narrative for the given session."""
        cfg = config or NarrativeConfig()
        events = session.events[:cfg.max_steps]

        # Compute duration
        duration_s = 0.0
        if session.ended_at and session.started_at:
            duration_s = (session.ended_at - session.started_at).total_seconds()
        elif events:
            duration_s = (events[-1].timestamp - events[0].timestamp).total_seconds()

        # Classify events and aggregate stats via dedicated helpers.
        llm_events, tool_events, decision_events, error_events = (
            self._classify_events(events)
        )

        # Token totals
        total_in = sum(e.tokens_in for e in events)
        total_out = sum(e.tokens_out for e in events)
        total_tokens = total_in + total_out

        # Cost estimate
        cost = 0.0
        if cfg.include_costs:
            cost = (
                (total_in / 1000 * cfg.cost_per_1k_input)
                + (total_out / 1000 * cfg.cost_per_1k_output)
            )

        tool_map = self._build_tool_summaries(tool_events)

        # Build sections
        sections: list[NarrativeSection] = []
        order = 0

        # Timeline section
        if cfg.include_timeline and events:
            order += 1
            timeline_lines = self._build_timeline(events, cfg.style)
            sections.append(NarrativeSection(
                title="Timeline",
                content="\n".join(timeline_lines),
                order=order,
            ))

        # Decisions section
        if cfg.include_decisions and decision_events:
            order += 1
            dec_lines = self._build_decisions(decision_events, cfg.style)
            sections.append(NarrativeSection(
                title="Key Decisions",
                content="\n".join(dec_lines),
                order=order,
            ))

        # Errors section
        if cfg.include_errors and error_events:
            order += 1
            err_lines = self._build_errors(error_events, cfg.style)
            sections.append(NarrativeSection(
                title="Errors & Issues",
                content="\n".join(err_lines),
                order=order,
            ))

        # Models section
        model_agg = self._aggregate_models(llm_events)
        if model_agg:
            order += 1
            model_lines = [
                f"- **{m}**: {model_agg[m][0]} calls, {model_agg[m][1]:,} tokens"
                for m in sorted(model_agg)
            ]
            sections.append(NarrativeSection(
                title="Models Used",
                content="\n".join(model_lines),
                order=order,
            ))

        # Build summary and body
        summary = self._build_summary(
            session, len(events), total_tokens, cost,
            len(error_events), len(decision_events),
            list(tool_map.keys()), duration_s, cfg.style,
        )
        body = self._build_body(
            session, events, llm_events, tool_events,
            decision_events, error_events, tool_map,
            total_tokens, cost, duration_s, cfg.style,
        )

        return Narrative(
            session_id=session.session_id,
            agent_name=session.agent_name,
            summary=summary,
            body=body,
            sections=sections,
            tool_summaries=list(tool_map.values()),
            total_events=len(events),
            total_tokens=total_tokens,
            total_cost_usd=round(cost, 6),
            duration_seconds=duration_s,
            error_count=len(error_events),
            decision_count=len(decision_events),
            style=cfg.style,
        )

    def generate_batch(self, sessions: list[Session], config: NarrativeConfig | None = None) -> list[Narrative]:
        """Generate narratives for multiple sessions."""
        return [self.generate(s, config) for s in sessions]

    def compare(self, session_a: Session, session_b: Session, config: NarrativeConfig | None = None) -> str:
        """Generate a comparative narrative between two sessions."""
        na = self.generate(session_a, config)
        nb = self.generate(session_b, config)

        lines = [
            "# Session Comparison",
            "",
            "| Metric | Session A | Session B |",
            "|--------|-----------|-----------|",
            f"| ID | {na.session_id} | {nb.session_id} |",
            f"| Agent | {na.agent_name} | {nb.agent_name} |",
            f"| Events | {na.total_events} | {nb.total_events} |",
            f"| Tokens | {na.total_tokens:,} | {nb.total_tokens:,} |",
            f"| Cost | ${na.total_cost_usd:.4f} | ${nb.total_cost_usd:.4f} |",
            f"| Errors | {na.error_count} | {nb.error_count} |",
            f"| Decisions | {na.decision_count} | {nb.decision_count} |",
        ]

        # Tool comparison
        all_tools = set(t.tool_name for t in na.tool_summaries) | set(t.tool_name for t in nb.tool_summaries)
        if all_tools:
            lines.extend(["", "## Tool Usage Comparison", ""])
            lines.append("| Tool | A Calls | B Calls | A Failures | B Failures |")
            lines.append("|------|---------|---------|------------|------------|")
            a_map = {t.tool_name: t for t in na.tool_summaries}
            b_map = {t.tool_name: t for t in nb.tool_summaries}
            for tool in sorted(all_tools):
                at = a_map.get(tool)
                bt = b_map.get(tool)
                lines.append(
                    f"| {tool} | {at.call_count if at else 0} | {bt.call_count if bt else 0} | "
                    f"{at.failure_count if at else 0} | {bt.failure_count if bt else 0} |"
                )

        return "\n".join(lines)

    def _build_summary(
        self, session: Session, event_count: int, total_tokens: int,
        cost: float, error_count: int, decision_count: int,
        tools: list[str], duration_s: float, style: NarrativeStyle,
    ) -> str:
        status_word = "completed" if session.status == "completed" else session.status
        dur = self._fmt_dur(duration_s)

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

    def _build_body(
        self, session: Session, events: list[AgentEvent],
        llm_events: list[AgentEvent], tool_events: list[AgentEvent],
        decision_events: list[AgentEvent], error_events: list[AgentEvent],
        tool_map: dict[str, ToolSummary], total_tokens: int,
        cost: float, duration_s: float, style: NarrativeStyle,
    ) -> str:
        paragraphs: list[str] = []

        # Opening
        dur = self._fmt_dur(duration_s)
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

    def _build_timeline(self, events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
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

    def _build_decisions(self, events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
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

    def _build_errors(self, events: list[AgentEvent], style: NarrativeStyle) -> list[str]:
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

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        if seconds <= 0:
            return ""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        elif s < 3600:
            return f"{s // 60}m {s % 60}s"
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"
