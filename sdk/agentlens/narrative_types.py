"""Data model for generated session narratives.

This module holds the pure data structures that
:class:`agentlens.narrative.NarrativeGenerator` produces and the serialization
logic for them (:meth:`Narrative.to_markdown`, :meth:`Narrative.to_dict`).  It
is intentionally separated from ``narrative.py`` so the value types and their
export formatting stay readable and are not interleaved with the prose-building
engine.

There is no session-traversal logic here - only the narrative vocabulary
(:class:`NarrativeStyle`, :class:`NarrativeSection`, :class:`ToolSummary`,
:class:`NarrativeConfig`, :class:`Narrative`) and how a finished narrative
renders to Markdown / a dict.  These symbols are re-exported from
``agentlens.narrative`` (and ``agentlens``) so the public import paths are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from agentlens._utils import format_duration_seconds as _fmt_seconds
from agentlens._utils import utcnow as _utcnow


class NarrativeStyle(Enum):
    """Tone and detail level for generated narratives."""
    TECHNICAL = "technical"
    EXECUTIVE = "executive"
    CASUAL = "casual"


@dataclass
class NarrativeSection:
    """A named section of the narrative."""
    title: str
    content: str
    order: int = 0


@dataclass
class ToolSummary:
    """Summary of a tool's usage within a session."""
    tool_name: str
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0


@dataclass
class NarrativeConfig:
    """Configuration for narrative generation."""
    include_tools: bool = True
    include_decisions: bool = True
    include_costs: bool = True
    include_errors: bool = True
    include_timeline: bool = True
    max_steps: int = 100
    style: NarrativeStyle = NarrativeStyle.TECHNICAL
    cost_per_1k_input: float = 0.01
    cost_per_1k_output: float = 0.03

    def __post_init__(self):
        if isinstance(self.style, str):
            self.style = NarrativeStyle(self.style)


@dataclass
class Narrative:
    """A generated session narrative."""
    session_id: str
    agent_name: str
    summary: str
    body: str
    sections: list[NarrativeSection] = field(default_factory=list)
    tool_summaries: list[ToolSummary] = field(default_factory=list)
    total_events: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    error_count: int = 0
    decision_count: int = 0
    style: NarrativeStyle = NarrativeStyle.TECHNICAL
    generated_at: datetime = field(default_factory=_utcnow)

    def to_markdown(self) -> str:
        """Export narrative as markdown."""
        lines = [f"# Session Narrative: {self.session_id}", ""]
        lines.append(f"**Agent:** {self.agent_name}  ")
        lines.append(f"**Duration:** {self._fmt_duration()}  ")
        lines.append(f"**Events:** {self.total_events}  ")
        lines.append(f"**Tokens:** {self.total_tokens:,}  ")
        if self.total_cost_usd > 0:
            lines.append(f"**Estimated Cost:** ${self.total_cost_usd:.4f}  ")
        if self.error_count:
            lines.append(f"**Errors:** {self.error_count}  ")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(self.summary)
        lines.append("")
        for section in sorted(self.sections, key=lambda s: s.order):
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
        if self.tool_summaries:
            lines.append("## Tool Usage")
            lines.append("")
            lines.append("| Tool | Calls | Success | Failed | Avg Duration |")
            lines.append("|------|-------|---------|--------|-------------|")
            for ts in sorted(self.tool_summaries, key=lambda t: -t.call_count):
                lines.append(
                    f"| {ts.tool_name} | {ts.call_count} | {ts.success_count} | "
                    f"{ts.failure_count} | {ts.avg_duration_ms:.0f}ms |"
                )
            lines.append("")
        lines.append(f"*Generated at {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}*")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Export narrative as a dictionary."""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "summary": self.summary,
            "body": self.body,
            "sections": [{"title": s.title, "content": s.content, "order": s.order} for s in self.sections],
            "tool_summaries": [
                {
                    "tool_name": t.tool_name,
                    "call_count": t.call_count,
                    "success_count": t.success_count,
                    "failure_count": t.failure_count,
                    "total_duration_ms": t.total_duration_ms,
                    "avg_duration_ms": t.avg_duration_ms,
                }
                for t in self.tool_summaries
            ],
            "total_events": self.total_events,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "duration_seconds": self.duration_seconds,
            "error_count": self.error_count,
            "decision_count": self.decision_count,
            "style": self.style.value,
            "generated_at": self.generated_at.isoformat(),
        }

    def _fmt_duration(self) -> str:
        """Render :attr:`duration_seconds` as ``Ns`` / ``Nm Ns`` / ``Nh Nm``.

        Uses the shared :func:`agentlens._utils.format_duration_seconds`
        helper; a zero duration renders as ``"0s"`` (durations are always
        non-negative here, so no empty-string guard is applied).
        """
        return _fmt_seconds(self.duration_seconds)
