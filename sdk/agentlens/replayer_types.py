"""Value types for session replay -- the frames and stats a replay yields.

This module holds the pure data structures that
:class:`agentlens.replayer.SessionReplayer` produces, together with their
serialization helpers (:meth:`ReplayFrame.to_dict` / :meth:`ReplayFrame.to_text`
and :meth:`ReplayStats.to_dict` / :meth:`ReplayStats.summary`).  It is
intentionally separated from ``replayer.py`` so the value vocabulary and its
export formatting stay readable and are not interleaved with the replay
engine.

There is no replay/traversal logic here - only the two value types
(:class:`ReplayFrame`, :class:`ReplayStats`) and how a frame or a finished
replay renders to a dict / text.  These symbols are re-exported from
``agentlens.replayer`` (and ``agentlens``) so the public import paths are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentlens.models import AgentEvent


# ---------------------------------------------------------------------------
# Replay frame
# ---------------------------------------------------------------------------

@dataclass
class ReplayFrame:
    """A single frame emitted during replay."""

    index: int
    total: int
    event: AgentEvent
    wall_delay_ms: float  # delay since previous frame (speed-adjusted)
    elapsed_ms: float  # cumulative elapsed (original timeline)
    is_breakpoint: bool = False
    annotations: list[str] = field(default_factory=list)

    # Convenience ---------------------------------------------------------

    @property
    def progress(self) -> float:
        """0.0-1.0 progress through the session."""
        return (self.index + 1) / self.total if self.total else 0.0

    @property
    def progress_pct(self) -> float:
        return round(self.progress * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "total": self.total,
            "event_id": self.event.event_id,
            "event_type": self.event.event_type,
            "timestamp": self.event.timestamp.isoformat(),
            "wall_delay_ms": round(self.wall_delay_ms, 2),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "progress_pct": self.progress_pct,
            "is_breakpoint": self.is_breakpoint,
            "annotations": self.annotations,
            "model": self.event.model,
            "tokens_in": self.event.tokens_in,
            "tokens_out": self.event.tokens_out,
            "duration_ms": self.event.duration_ms,
            "tool_name": self.event.tool_call.tool_name if self.event.tool_call else None,
        }

    def to_text(self) -> str:
        parts = [
            f"[{self.index + 1}/{self.total}]",
            f"{self.event.event_type}",
        ]
        if self.event.model:
            parts.append(f"model={self.event.model}")
        if self.event.tool_call:
            parts.append(f"tool={self.event.tool_call.tool_name}")
        if self.event.duration_ms is not None:
            parts.append(f"dur={self.event.duration_ms:.0f}ms")
        if self.event.tokens_in or self.event.tokens_out:
            parts.append(f"tok={self.event.tokens_in}→{self.event.tokens_out}")
        if self.is_breakpoint:
            parts.append("⏸ BREAKPOINT")
        if self.annotations:
            parts.append(f"notes=[{', '.join(self.annotations)}]")
        parts.append(f"+{self.wall_delay_ms:.0f}ms")
        parts.append(f"({self.progress_pct}%)")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Replay statistics
# ---------------------------------------------------------------------------

@dataclass
class ReplayStats:
    """Aggregate statistics for a completed (or partial) replay."""

    total_events: int = 0
    played_events: int = 0
    filtered_events: int = 0
    breakpoints_hit: int = 0
    original_duration_ms: float = 0.0
    replay_duration_ms: float = 0.0
    speed: float = 1.0
    event_type_counts: dict[str, int] = field(default_factory=dict)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    models_used: set[str] = field(default_factory=set)
    tools_used: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "played_events": self.played_events,
            "filtered_events": self.filtered_events,
            "breakpoints_hit": self.breakpoints_hit,
            "original_duration_ms": round(self.original_duration_ms, 2),
            "replay_duration_ms": round(self.replay_duration_ms, 2),
            "speed": self.speed,
            "event_type_counts": dict(self.event_type_counts),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "models_used": sorted(self.models_used),
            "tools_used": sorted(self.tools_used),
        }

    def summary(self) -> str:
        lines = [
            "── Replay Summary ──",
            f"Events: {self.played_events}/{self.total_events} played"
            + (f" ({self.filtered_events} filtered)" if self.filtered_events else ""),
            f"Breakpoints hit: {self.breakpoints_hit}",
            f"Original duration: {self.original_duration_ms:.0f}ms",
            f"Replay duration: {self.replay_duration_ms:.0f}ms ({self.speed}x)",
            f"Tokens: {self.total_tokens_in} in / {self.total_tokens_out} out",
            f"Models: {', '.join(sorted(self.models_used)) or 'none'}",
            f"Tools: {', '.join(sorted(self.tools_used)) or 'none'}",
        ]
        if self.event_type_counts:
            lines.append("Event types:")
            for etype, count in sorted(
                self.event_type_counts.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  {etype}: {count}")
        return "\n".join(lines)
