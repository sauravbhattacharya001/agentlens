"""Session Diff — structured comparison of two agent sessions.

Compares two ``Session`` objects and produces a rich diff report
covering event-level changes, token/cost deltas, timing shifts,
tool-call differences, and model usage divergence.  Useful for:

- Debugging regressions ("why did run #42 use 3× more tokens?")
- A/B testing prompt or model changes
- Auditing behavioural differences across agent versions
- CI assertions on session shape (expected vs actual events)

The diff is purely SDK-side — no backend needed.

Example::

    from agentlens.session_diff import SessionDiff

    diff = SessionDiff(baseline, candidate)
    report = diff.compare()
    print(report.summary())
    print(report.render_text())

    # Programmatic access
    print(report.token_delta)          # +1 240 tokens
    print(report.added_tool_calls)     # ['web_search']
    print(report.removed_tool_calls)   # ['calculator']
    print(report.event_alignment)      # list of (baseline_evt, candidate_evt, status)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from agentlens.models import AgentEvent, Session


class AlignmentStatus(str, Enum):
    """How a pair of events relate in the diff."""
    MATCHED = "matched"
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass
class EventPair:
    """A single aligned pair (or one-sided entry) in the event diff."""
    baseline: AgentEvent | None
    candidate: AgentEvent | None
    status: AlignmentStatus
    changes: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        evt = self.baseline or self.candidate
        assert evt is not None
        if evt.tool_call:
            return f"{evt.event_type}({evt.tool_call.tool_name})"
        return evt.event_type


@dataclass
class ToolCallDelta:
    """Summary of tool-call differences between sessions."""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    common: list[str] = field(default_factory=list)
    baseline_counts: dict[str, int] = field(default_factory=dict)
    candidate_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class DiffReport:
    """Full diff report between two sessions."""
    baseline_id: str
    candidate_id: str
    baseline_agent: str
    candidate_agent: str

    # Token deltas
    tokens_in_delta: int = 0
    tokens_out_delta: int = 0
    token_delta: int = 0

    # Timing
    baseline_duration_ms: float | None = None
    candidate_duration_ms: float | None = None
    duration_delta_ms: float | None = None

    # Event counts
    baseline_event_count: int = 0
    candidate_event_count: int = 0

    # Event alignment
    event_alignment: list[EventPair] = field(default_factory=list)

    # Tool calls
    tool_delta: ToolCallDelta = field(default_factory=ToolCallDelta)

    # Model usage
    baseline_models: dict[str, int] = field(default_factory=dict)
    candidate_models: dict[str, int] = field(default_factory=dict)

    # Added / removed event types
    added_event_types: list[str] = field(default_factory=list)
    removed_event_types: list[str] = field(default_factory=list)

    # Scores
    similarity_score: float = 0.0  # 0-1, how similar the sessions are

    def summary(self) -> str:
        """One-line summary of the diff."""
        parts: list[str] = []
        parts.append(f"Diff: {self.baseline_id[:8]} → {self.candidate_id[:8]}")
        if self.token_delta:
            sign = "+" if self.token_delta > 0 else ""
            parts.append(f"tokens {sign}{self.token_delta}")
        parts.append(f"events {self.baseline_event_count}→{self.candidate_event_count}")
        if self.tool_delta.added:
            parts.append(f"+tools: {','.join(self.tool_delta.added)}")
        if self.tool_delta.removed:
            parts.append(f"-tools: {','.join(self.tool_delta.removed)}")
        parts.append(f"similarity={self.similarity_score:.0%}")
        return " | ".join(parts)

    def render_text(self) -> str:
        """Human-readable multi-line diff report."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("SESSION DIFF REPORT")
        lines.append("=" * 60)
        lines.append(f"Baseline : {self.baseline_id} ({self.baseline_agent})")
        lines.append(f"Candidate: {self.candidate_id} ({self.candidate_agent})")
        lines.append("")

        # Tokens
        lines.append("── Tokens ──")
        sign_in = "+" if self.tokens_in_delta > 0 else ""
        sign_out = "+" if self.tokens_out_delta > 0 else ""
        lines.append(f"  Input:  {sign_in}{self.tokens_in_delta}")
        lines.append(f"  Output: {sign_out}{self.tokens_out_delta}")
        lines.append(f"  Total:  {'+'if self.token_delta>0 else ''}{self.token_delta}")
        lines.append("")

        # Timing
        if self.duration_delta_ms is not None:
            lines.append("── Timing ──")
            lines.append(f"  Baseline:  {self.baseline_duration_ms:.0f}ms")
            lines.append(f"  Candidate: {self.candidate_duration_ms:.0f}ms")
            sign_d = "+" if self.duration_delta_ms > 0 else ""
            lines.append(f"  Delta:     {sign_d}{self.duration_delta_ms:.0f}ms")
            lines.append("")

        # Tool calls
        if self.tool_delta.added or self.tool_delta.removed or self.tool_delta.common:
            lines.append("── Tool Calls ──")
            for t in self.tool_delta.added:
                cnt = self.tool_delta.candidate_counts.get(t, 0)
                lines.append(f"  + {t} (×{cnt})")
            for t in self.tool_delta.removed:
                cnt = self.tool_delta.baseline_counts.get(t, 0)
                lines.append(f"  - {t} (×{cnt})")
            for t in self.tool_delta.common:
                bc = self.tool_delta.baseline_counts.get(t, 0)
                cc = self.tool_delta.candidate_counts.get(t, 0)
                marker = "" if bc == cc else f" ({bc}→{cc})"
                lines.append(f"  = {t}{marker}")
            lines.append("")

        # Models
        all_models = set(self.baseline_models) | set(self.candidate_models)
        if all_models:
            lines.append("── Models ──")
            for m in sorted(all_models):
                bc = self.baseline_models.get(m, 0)
                cc = self.candidate_models.get(m, 0)
                if bc == 0:
                    lines.append(f"  + {m} (×{cc})")
                elif cc == 0:
                    lines.append(f"  - {m} (×{bc})")
                else:
                    lines.append(f"  = {m} ({bc}→{cc})")
            lines.append("")

        # Event alignment
        lines.append("── Event Alignment ──")
        for pair in self.event_alignment:
            icon = {"matched": "=", "added": "+", "removed": "-", "modified": "~"}[pair.status.value]
            detail = ""
            if pair.changes:
                parts = [f"{k}: {v}" for k, v in pair.changes.items()]
                detail = f"  [{', '.join(parts)}]"
            lines.append(f"  {icon} {pair.label}{detail}")
        lines.append("")

        lines.append(f"Similarity: {self.similarity_score:.0%}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialisable dictionary."""
        return {
            "baseline_id": self.baseline_id,
            "candidate_id": self.candidate_id,
            "baseline_agent": self.baseline_agent,
            "candidate_agent": self.candidate_agent,
            "tokens_in_delta": self.tokens_in_delta,
            "tokens_out_delta": self.tokens_out_delta,
            "token_delta": self.token_delta,
            "baseline_duration_ms": self.baseline_duration_ms,
            "candidate_duration_ms": self.candidate_duration_ms,
            "duration_delta_ms": self.duration_delta_ms,
            "baseline_event_count": self.baseline_event_count,
            "candidate_event_count": self.candidate_event_count,
            "tool_delta": {
                "added": self.tool_delta.added,
                "removed": self.tool_delta.removed,
                "common": self.tool_delta.common,
            },
            "baseline_models": self.baseline_models,
            "candidate_models": self.candidate_models,
            "added_event_types": self.added_event_types,
            "removed_event_types": self.removed_event_types,
            "similarity_score": self.similarity_score,
            "event_count": len(self.event_alignment),
            "events": [
                {
                    "label": p.label,
                    "status": p.status.value,
                    "changes": p.changes,
                }
                for p in self.event_alignment
            ],
        }

    def to_json(self, path: str) -> None:
        """Write report to a JSON file.

        Raises ValueError if the path escapes the working/temp directory.
        """
        from agentlens.exporter import _validate_output_path
        safe = _validate_output_path(path)
        with open(safe, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


def _tool_counts(events: Sequence[AgentEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        if e.tool_call:
            name = e.tool_call.tool_name
            counts[name] = counts.get(name, 0) + 1
    return counts


def _model_counts(events: Sequence[AgentEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        if e.model:
            counts[e.model] = counts.get(e.model, 0) + 1
    return counts


def _session_duration_ms(s: Session) -> float | None:
    if s.ended_at and s.started_at:
        return (s.ended_at - s.started_at).total_seconds() * 1000
    # Fallback: sum event durations
    total = sum(e.duration_ms for e in s.events if e.duration_ms)
    return total if total else None


def _align_events(
    baseline: list[AgentEvent],
    candidate: list[AgentEvent],
) -> list[EventPair]:
    """Align events using longest-common-subsequence on event_type.

    Events are matched by event_type (and tool name if present) in order.
    Unmatched events are marked as added/removed.
    """

    def _key(e: AgentEvent) -> str:
        if e.tool_call:
            return f"{e.event_type}:{e.tool_call.tool_name}"
        return e.event_type

    # LCS via DP
    n, m = len(baseline), len(candidate)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if _key(baseline[i]) == _key(candidate[j]):
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    pairs: list[EventPair] = []
    i, j = 0, 0
    while i < n and j < m:
        if _key(baseline[i]) == _key(candidate[j]):
            changes: dict[str, Any] = {}
            if baseline[i].tokens_in != candidate[j].tokens_in:
                changes["tokens_in"] = f"{baseline[i].tokens_in}→{candidate[j].tokens_in}"
            if baseline[i].tokens_out != candidate[j].tokens_out:
                changes["tokens_out"] = f"{baseline[i].tokens_out}→{candidate[j].tokens_out}"
            if baseline[i].model != candidate[j].model:
                changes["model"] = f"{baseline[i].model}→{candidate[j].model}"
            if baseline[i].duration_ms and candidate[j].duration_ms:
                if abs(baseline[i].duration_ms - candidate[j].duration_ms) > 10:
                    changes["duration_ms"] = f"{baseline[i].duration_ms:.0f}→{candidate[j].duration_ms:.0f}"
            status = AlignmentStatus.MODIFIED if changes else AlignmentStatus.MATCHED
            pairs.append(EventPair(baseline=baseline[i], candidate=candidate[j],
                                   status=status, changes=changes))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            pairs.append(EventPair(baseline=baseline[i], candidate=None,
                                   status=AlignmentStatus.REMOVED))
            i += 1
        else:
            pairs.append(EventPair(baseline=None, candidate=candidate[j],
                                   status=AlignmentStatus.ADDED))
            j += 1

    while i < n:
        pairs.append(EventPair(baseline=baseline[i], candidate=None,
                               status=AlignmentStatus.REMOVED))
        i += 1
    while j < m:
        pairs.append(EventPair(baseline=None, candidate=candidate[j],
                               status=AlignmentStatus.ADDED))
        j += 1

    return pairs


class SessionDiff:
    """Compare two sessions and produce a ``DiffReport``.

    Args:
        baseline: The reference/expected session.
        candidate: The new/actual session to compare against baseline.
    """

    def __init__(self, baseline: Session, candidate: Session) -> None:
        self.baseline = baseline
        self.candidate = candidate

    def compare(self) -> DiffReport:
        """Run the diff and return a ``DiffReport``."""
        b, c = self.baseline, self.candidate

        # Token deltas
        tin_d = c.total_tokens_in - b.total_tokens_in
        tout_d = c.total_tokens_out - b.total_tokens_out

        # Duration
        b_dur = _session_duration_ms(b)
        c_dur = _session_duration_ms(c)
        dur_d = (c_dur - b_dur) if (b_dur is not None and c_dur is not None) else None

        # Tool call delta
        b_tools = _tool_counts(b.events)
        c_tools = _tool_counts(c.events)
        all_tools = set(b_tools) | set(c_tools)
        td = ToolCallDelta(
            added=[t for t in sorted(all_tools) if t not in b_tools],
            removed=[t for t in sorted(all_tools) if t not in c_tools],
            common=[t for t in sorted(all_tools) if t in b_tools and t in c_tools],
            baseline_counts=b_tools,
            candidate_counts=c_tools,
        )

        # Model usage
        b_models = _model_counts(b.events)
        c_models = _model_counts(c.events)

        # Event types
        b_types = set(e.event_type for e in b.events)
        c_types = set(e.event_type for e in c.events)

        # Event alignment
        alignment = _align_events(b.events, c.events)

        # Similarity score
        matched = sum(1 for p in alignment if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.MODIFIED))
        total = len(alignment) if alignment else 1
        similarity = matched / total

        return DiffReport(
            baseline_id=b.session_id,
            candidate_id=c.session_id,
            baseline_agent=b.agent_name,
            candidate_agent=c.agent_name,
            tokens_in_delta=tin_d,
            tokens_out_delta=tout_d,
            token_delta=tin_d + tout_d,
            baseline_duration_ms=b_dur,
            candidate_duration_ms=c_dur,
            duration_delta_ms=dur_d,
            baseline_event_count=len(b.events),
            candidate_event_count=len(c.events),
            event_alignment=alignment,
            tool_delta=td,
            baseline_models=b_models,
            candidate_models=c_models,
            added_event_types=sorted(c_types - b_types),
            removed_event_types=sorted(b_types - c_types),
            similarity_score=similarity,
        )
