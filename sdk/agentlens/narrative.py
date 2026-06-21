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

The value types (:class:`Narrative`, :class:`NarrativeConfig`, ...) live in
``agentlens.narrative_types`` and the stateless prose-building engine
(classification, aggregation, section/summary/body composition) lives in
``agentlens.narrative_render``; this module keeps only the
:class:`NarrativeGenerator` orchestration that walks a session and assembles a
:class:`Narrative` from those parts.  All public names are re-exported here so
existing import paths stay unchanged.
"""

from __future__ import annotations

from agentlens import narrative_render as _render
from agentlens.models import Session
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

        # Classify events and aggregate stats via the prose-building engine.
        llm_events, tool_events, decision_events, error_events = (
            _render.classify_events(events)
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

        tool_map = _render.build_tool_summaries(tool_events)

        # Build sections
        sections: list[NarrativeSection] = []
        order = 0

        # Timeline section
        if cfg.include_timeline and events:
            order += 1
            timeline_lines = _render.build_timeline(events, cfg.style)
            sections.append(NarrativeSection(
                title="Timeline",
                content="\n".join(timeline_lines),
                order=order,
            ))

        # Decisions section
        if cfg.include_decisions and decision_events:
            order += 1
            dec_lines = _render.build_decisions(decision_events, cfg.style)
            sections.append(NarrativeSection(
                title="Key Decisions",
                content="\n".join(dec_lines),
                order=order,
            ))

        # Errors section
        if cfg.include_errors and error_events:
            order += 1
            err_lines = _render.build_errors(error_events, cfg.style)
            sections.append(NarrativeSection(
                title="Errors & Issues",
                content="\n".join(err_lines),
                order=order,
            ))

        # Models section
        model_agg = _render.aggregate_models(llm_events)
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
        summary = _render.build_summary(
            session, len(events), total_tokens, cost,
            len(error_events), len(decision_events),
            list(tool_map.keys()), duration_s, cfg.style,
        )
        body = _render.build_body(
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
