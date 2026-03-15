"""SessionReplayer -- step-by-step session replay for debugging agent runs.

Reconstructs timing from recorded events, supports speed control, event
filtering, breakpoints, callbacks, and multiple export formats (text,
JSON, Markdown).  Useful for post-hoc debugging, demos, and team
code-reviews of agent behaviour.

Usage:
    from agentlens.replayer import SessionReplayer

    replayer = SessionReplayer(session)
    replayer.set_speed(2.0)
    replayer.add_filter("llm_call", "tool_call")
    replayer.add_breakpoint(lambda e: e.event_type == "error")
    for frame in replayer.play():
        print(frame)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from agentlens.models import AgentEvent, Session


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


# ---------------------------------------------------------------------------
# Session replayer
# ---------------------------------------------------------------------------

BreakpointFn = Callable[[AgentEvent], bool]
CallbackFn = Callable[["ReplayFrame"], None]


class SessionReplayer:
    """Replay an agent session event-by-event with speed control and filters.

    Parameters
    ----------
    session : Session
        The recorded session to replay.
    speed : float
        Playback speed multiplier (default 1.0).  2.0 = 2× faster.
    """

    def __init__(self, session: Session, *, speed: float = 1.0) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._session = session
        self._speed = speed
        self._filters: set[str] = set()
        self._exclude_filters: set[str] = set()
        self._breakpoints: list[BreakpointFn] = []
        self._callbacks: list[CallbackFn] = []
        self._annotations: dict[str, list[str]] = {}  # event_id → notes
        self._stats = ReplayStats()
        self._paused = False
        self._position = 0  # for step-through

    # -- Configuration -----------------------------------------------------

    def set_speed(self, speed: float) -> "SessionReplayer":
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._speed = speed
        return self

    def add_filter(self, *event_types: str) -> "SessionReplayer":
        """Include only these event types (allowlist)."""
        self._filters.update(event_types)
        return self

    def remove_filter(self, *event_types: str) -> "SessionReplayer":
        self._filters.discard(*event_types) if len(event_types) == 1 else [
            self._filters.discard(t) for t in event_types
        ]
        return self

    def clear_filters(self) -> "SessionReplayer":
        self._filters.clear()
        self._exclude_filters.clear()
        return self

    def exclude(self, *event_types: str) -> "SessionReplayer":
        """Exclude these event types (blocklist)."""
        self._exclude_filters.update(event_types)
        return self

    def add_breakpoint(self, predicate: BreakpointFn) -> "SessionReplayer":
        self._breakpoints.append(predicate)
        return self

    def clear_breakpoints(self) -> "SessionReplayer":
        self._breakpoints.clear()
        return self

    def on_frame(self, callback: CallbackFn) -> "SessionReplayer":
        self._callbacks.append(callback)
        return self

    def annotate(self, event_id: str, note: str) -> "SessionReplayer":
        self._annotations.setdefault(event_id, []).append(note)
        return self

    # -- Filtering helpers -------------------------------------------------

    def _should_include(self, event: AgentEvent) -> bool:
        if self._exclude_filters and event.event_type in self._exclude_filters:
            return False
        if self._filters and event.event_type not in self._filters:
            return False
        return True

    def _is_breakpoint(self, event: AgentEvent) -> bool:
        return any(bp(event) for bp in self._breakpoints)

    # -- Core replay -------------------------------------------------------

    @property
    def events(self) -> list[AgentEvent]:
        """Session events sorted by timestamp."""
        return sorted(self._session.events, key=lambda e: e.timestamp)

    @property
    def filtered_events(self) -> list[AgentEvent]:
        return [e for e in self.events if self._should_include(e)]

    def play(self) -> Iterator[ReplayFrame]:
        """Yield replay frames in chronological order.

        Computes wall delays between events adjusted by speed.  Breakpoints
        set ``is_breakpoint=True`` on the frame (caller decides whether to
        actually pause).
        """
        filtered = self.filtered_events
        total = len(filtered)
        all_events = self.events

        self._stats = ReplayStats(
            total_events=len(all_events),
            filtered_events=len(all_events) - total,
            speed=self._speed,
        )

        if not filtered:
            return

        # Compute original timeline span
        first_ts = filtered[0].timestamp
        last_ts = filtered[-1].timestamp
        self._stats.original_duration_ms = (
            (last_ts - first_ts).total_seconds() * 1000
        )

        cumulative_ms = 0.0
        prev_ts = first_ts

        for idx, event in enumerate(filtered):
            # Timing
            gap_ms = max(
                0.0, (event.timestamp - prev_ts).total_seconds() * 1000
            )
            wall_delay = gap_ms / self._speed
            cumulative_ms += gap_ms

            hit = self._is_breakpoint(event)
            if hit:
                self._stats.breakpoints_hit += 1

            annotations = self._annotations.get(event.event_id, [])

            frame = ReplayFrame(
                index=idx,
                total=total,
                event=event,
                wall_delay_ms=wall_delay,
                elapsed_ms=cumulative_ms,
                is_breakpoint=hit,
                annotations=list(annotations),
            )

            # Stats bookkeeping
            self._stats.played_events += 1
            self._stats.event_type_counts[event.event_type] = (
                self._stats.event_type_counts.get(event.event_type, 0) + 1
            )
            self._stats.total_tokens_in += event.tokens_in
            self._stats.total_tokens_out += event.tokens_out
            if event.model:
                self._stats.models_used.add(event.model)
            if event.tool_call:
                self._stats.tools_used.add(event.tool_call.tool_name)

            self._stats.replay_duration_ms += wall_delay

            # Fire callbacks
            for cb in self._callbacks:
                cb(frame)

            prev_ts = event.timestamp
            yield frame

    def play_range(
        self, start: int = 0, end: int | None = None
    ) -> Iterator[ReplayFrame]:
        """Yield frames for a slice of the filtered events."""
        for frame in self.play():
            if frame.index < start:
                continue
            if end is not None and frame.index >= end:
                break
            yield frame

    def step(self) -> ReplayFrame | None:
        """Step through one frame at a time (stateful)."""
        filtered = self.filtered_events
        if self._position >= len(filtered):
            return None
        frames = list(self.play_range(self._position, self._position + 1))
        self._position += 1
        return frames[0] if frames else None

    def reset(self) -> "SessionReplayer":
        """Reset step position."""
        self._position = 0
        self._stats = ReplayStats()
        return self

    def seek(self, position: int) -> "SessionReplayer":
        """Set step position."""
        self._position = max(0, position)
        return self

    # -- Statistics --------------------------------------------------------

    @property
    def stats(self) -> ReplayStats:
        return self._stats

    # -- Export ------------------------------------------------------------

    def to_json(self, *, indent: int = 2) -> str:
        frames = [f.to_dict() for f in self.play()]
        return json.dumps(
            {
                "session_id": self._session.session_id,
                "agent_name": self._session.agent_name,
                "speed": self._speed,
                "frames": frames,
                "stats": self._stats.to_dict(),
            },
            indent=indent,
            default=str,
        )

    def to_text(self) -> str:
        lines = [
            f"Replay: session={self._session.session_id}"
            f" agent={self._session.agent_name} speed={self._speed}x",
            "",
        ]
        for frame in self.play():
            lines.append(frame.to_text())
        lines.append("")
        lines.append(self._stats.summary())
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines = [
            f"# Session Replay: `{self._session.session_id}`",
            "",
            f"**Agent:** {self._session.agent_name}  ",
            f"**Speed:** {self._speed}x  ",
            f"**Events:** {len(self.filtered_events)}  ",
            "",
            "## Timeline",
            "",
            "| # | Type | Model | Tool | Tokens | Duration | Delay | Notes |",
            "|---|------|-------|------|--------|----------|-------|-------|",
        ]
        for frame in self.play():
            e = frame.event
            tool = e.tool_call.tool_name if e.tool_call else ""
            tokens = (
                f"{e.tokens_in}→{e.tokens_out}"
                if e.tokens_in or e.tokens_out
                else ""
            )
            dur = f"{e.duration_ms:.0f}ms" if e.duration_ms is not None else ""
            notes = "; ".join(frame.annotations)
            if frame.is_breakpoint:
                notes = ("⏸ " + notes) if notes else "⏸"
            lines.append(
                f"| {frame.index + 1} | {e.event_type} | {e.model or ''} "
                f"| {tool} | {tokens} | {dur} | +{frame.wall_delay_ms:.0f}ms "
                f"| {notes} |"
            )
        lines.append("")
        lines.append("## Stats")
        lines.append("")
        lines.append(f"```\n{self._stats.summary()}\n```")
        return "\n".join(lines)

    # -- Comparison --------------------------------------------------------

    @staticmethod
    def diff(
        session_a: Session, session_b: Session
    ) -> dict[str, Any]:
        """Compare two session replays for structural differences.

        Returns a dict with event count diffs, timing diffs, and
        event-type distribution comparison.
        """
        events_a = sorted(session_a.events, key=lambda e: e.timestamp)
        events_b = sorted(session_b.events, key=lambda e: e.timestamp)

        def _type_counts(events: list[AgentEvent]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for e in events:
                counts[e.event_type] = counts.get(e.event_type, 0) + 1
            return counts

        def _duration(events: list[AgentEvent]) -> float:
            if len(events) < 2:
                return 0.0
            return (
                (events[-1].timestamp - events[0].timestamp).total_seconds()
                * 1000
            )

        def _total_tokens(events: list[AgentEvent]) -> tuple[int, int]:
            tin = sum(e.tokens_in for e in events)
            tout = sum(e.tokens_out for e in events)
            return tin, tout

        tc_a, tc_b = _type_counts(events_a), _type_counts(events_b)
        all_types = sorted(set(tc_a) | set(tc_b))
        tok_a, tok_b = _total_tokens(events_a), _total_tokens(events_b)

        return {
            "session_a": session_a.session_id,
            "session_b": session_b.session_id,
            "event_count": {"a": len(events_a), "b": len(events_b)},
            "duration_ms": {
                "a": round(_duration(events_a), 2),
                "b": round(_duration(events_b), 2),
            },
            "tokens": {
                "a": {"in": tok_a[0], "out": tok_a[1]},
                "b": {"in": tok_b[0], "out": tok_b[1]},
            },
            "event_types": {
                t: {"a": tc_a.get(t, 0), "b": tc_b.get(t, 0)}
                for t in all_types
            },
        }
