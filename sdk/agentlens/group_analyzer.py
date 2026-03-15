"""Session Group Analyzer — group and compare agent sessions.

Group sessions by agent name, model, status, metadata fields, or custom
time windows, then compute aggregate statistics for each group to spot
trends and compare performance across cohorts.

Usage::

    from agentlens.group_analyzer import SessionGroupAnalyzer

    analyzer = SessionGroupAnalyzer(sessions)
    groups = analyzer.group_by_agent()
    report = analyzer.compare(groups)
    print(analyzer.text_report(report))
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

from agentlens.models import Session


# ---------------------------------------------------------------------------
# Group descriptor
# ---------------------------------------------------------------------------

class GroupStats:
    """Aggregate statistics for a group of sessions."""

    __slots__ = (
        "name", "count", "total_tokens_in", "total_tokens_out",
        "total_events", "avg_tokens_in", "avg_tokens_out",
        "avg_events", "avg_duration_ms", "median_duration_ms",
        "p95_duration_ms", "min_duration_ms", "max_duration_ms",
        "completed_count", "error_count", "active_count",
        "completion_rate", "error_rate",
        "models_used", "durations",
    )

    def __init__(self, name: str, sessions: Sequence[Session]) -> None:
        self.name = name
        self.count = len(sessions)

        self.total_tokens_in = sum(s.total_tokens_in for s in sessions)
        self.total_tokens_out = sum(s.total_tokens_out for s in sessions)
        self.total_events = sum(len(s.events) for s in sessions)

        self.avg_tokens_in = self.total_tokens_in / self.count if self.count else 0.0
        self.avg_tokens_out = self.total_tokens_out / self.count if self.count else 0.0
        self.avg_events = self.total_events / self.count if self.count else 0.0

        # Duration stats (only for sessions that have ended)
        self.durations: list[float] = []
        for s in sessions:
            if s.ended_at and s.started_at:
                d = (s.ended_at - s.started_at).total_seconds() * 1000
                self.durations.append(d)

        if self.durations:
            self.avg_duration_ms = statistics.mean(self.durations)
            self.median_duration_ms = statistics.median(self.durations)
            sorted_d = sorted(self.durations)
            idx = min(int(math.ceil(0.95 * len(sorted_d))) - 1, len(sorted_d) - 1)
            self.p95_duration_ms = sorted_d[max(idx, 0)]
            self.min_duration_ms = sorted_d[0]
            self.max_duration_ms = sorted_d[-1]
        else:
            self.avg_duration_ms = 0.0
            self.median_duration_ms = 0.0
            self.p95_duration_ms = 0.0
            self.min_duration_ms = 0.0
            self.max_duration_ms = 0.0

        self.completed_count = sum(1 for s in sessions if s.status == "completed")
        self.error_count = sum(1 for s in sessions if s.status == "error")
        self.active_count = sum(1 for s in sessions if s.status == "active")
        self.completion_rate = self.completed_count / self.count if self.count else 0.0
        self.error_rate = self.error_count / self.count if self.count else 0.0

        # Distinct models
        models: set[str] = set()
        for s in sessions:
            for e in s.events:
                if e.model:
                    models.add(e.model)
        self.models_used = sorted(models)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_events": self.total_events,
            "avg_tokens_in": round(self.avg_tokens_in, 1),
            "avg_tokens_out": round(self.avg_tokens_out, 1),
            "avg_events": round(self.avg_events, 1),
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "median_duration_ms": round(self.median_duration_ms, 1),
            "p95_duration_ms": round(self.p95_duration_ms, 1),
            "min_duration_ms": round(self.min_duration_ms, 1),
            "max_duration_ms": round(self.max_duration_ms, 1),
            "completed_count": self.completed_count,
            "error_count": self.error_count,
            "active_count": self.active_count,
            "completion_rate": round(self.completion_rate, 4),
            "error_rate": round(self.error_rate, 4),
            "models_used": self.models_used,
        }


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

class ComparisonReport:
    """Result of comparing multiple groups."""

    def __init__(self, groups: dict[str, GroupStats]) -> None:
        self.groups = groups
        self.best_completion_rate: str | None = None
        self.lowest_error_rate: str | None = None
        self.fastest_median: str | None = None
        self.most_efficient: str | None = None  # lowest avg tokens per event
        self._compute()

    def _compute(self) -> None:
        if not self.groups:
            return
        gs = list(self.groups.values())

        # Best completion rate
        self.best_completion_rate = max(gs, key=lambda g: g.completion_rate).name

        # Lowest error rate
        self.lowest_error_rate = min(gs, key=lambda g: g.error_rate).name

        # Fastest median duration (only groups with durations)
        with_dur = [g for g in gs if g.durations]
        if with_dur:
            self.fastest_median = min(with_dur, key=lambda g: g.median_duration_ms).name

        # Most efficient (lowest total tokens per event)
        with_events = [g for g in gs if g.total_events > 0]
        if with_events:
            self.most_efficient = min(
                with_events,
                key=lambda g: (g.total_tokens_in + g.total_tokens_out) / g.total_events,
            ).name

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": {k: v.to_dict() for k, v in self.groups.items()},
            "highlights": {
                "best_completion_rate": self.best_completion_rate,
                "lowest_error_rate": self.lowest_error_rate,
                "fastest_median": self.fastest_median,
                "most_efficient": self.most_efficient,
            },
        }


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class SessionGroupAnalyzer:
    """Group sessions and compute comparative statistics."""

    def __init__(self, sessions: Sequence[Session] | None = None) -> None:
        self._sessions: list[Session] = list(sessions or [])

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions)

    def add_session(self, session: Session) -> None:
        self._sessions.append(session)

    def add_sessions(self, sessions: Sequence[Session]) -> None:
        self._sessions.extend(sessions)

    # -- grouping helpers --------------------------------------------------

    def group_by_agent(self) -> dict[str, list[Session]]:
        """Group sessions by agent_name."""
        return self._group_by(lambda s: s.agent_name)

    def group_by_status(self) -> dict[str, list[Session]]:
        """Group sessions by status."""
        return self._group_by(lambda s: s.status)

    def group_by_model(self) -> dict[str, list[Session]]:
        """Group sessions by primary model (most frequent model in events)."""
        groups: dict[str, list[Session]] = defaultdict(list)
        for s in self._sessions:
            model = self._primary_model(s) or "(no model)"
            groups[model].append(s)
        return dict(groups)

    def group_by_metadata(self, key: str) -> dict[str, list[Session]]:
        """Group sessions by a metadata field value."""
        groups: dict[str, list[Session]] = defaultdict(list)
        for s in self._sessions:
            val = str(s.metadata.get(key, "(missing)"))
            groups[val].append(s)
        return dict(groups)

    def group_by_time_window(
        self,
        window: timedelta,
        *,
        origin: datetime | None = None,
    ) -> dict[str, list[Session]]:
        """Group sessions into fixed-width time windows.

        Args:
            window: Width of each time bucket.
            origin: Start of the first bucket (defaults to earliest session).
        """
        if not self._sessions:
            return {}

        if origin is None:
            origin = min(s.started_at for s in self._sessions)

        groups: dict[str, list[Session]] = defaultdict(list)
        window_secs = window.total_seconds()
        for s in self._sessions:
            offset = (s.started_at - origin).total_seconds()
            bucket_idx = int(offset // window_secs) if window_secs > 0 else 0
            bucket_start = origin + timedelta(seconds=bucket_idx * window_secs)
            bucket_end = bucket_start + window
            label = f"{bucket_start.isoformat()} — {bucket_end.isoformat()}"
            groups[label].append(s)
        return dict(groups)

    def group_by_custom(self, key_fn: Callable[[Session], str]) -> dict[str, list[Session]]:
        """Group sessions by an arbitrary key function."""
        return self._group_by(key_fn)

    # -- analysis -----------------------------------------------------------

    def compute_stats(self, groups: dict[str, list[Session]]) -> dict[str, GroupStats]:
        """Compute aggregate stats for each group."""
        return {name: GroupStats(name, sessions) for name, sessions in groups.items()}

    def compare(self, groups: dict[str, list[Session]]) -> ComparisonReport:
        """Compare groups and produce a report with highlights."""
        stats = self.compute_stats(groups)
        return ComparisonReport(stats)

    # -- output -------------------------------------------------------------

    @staticmethod
    def text_report(report: ComparisonReport) -> str:
        """Render a human-readable text report."""
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("SESSION GROUP COMPARISON REPORT")
        lines.append("=" * 72)

        for name, gs in report.groups.items():
            lines.append("")
            lines.append(f"  Group: {name}")
            lines.append(f"    Sessions: {gs.count}  (completed={gs.completed_count}, error={gs.error_count}, active={gs.active_count})")
            lines.append(f"    Completion rate: {gs.completion_rate:.1%}  |  Error rate: {gs.error_rate:.1%}")
            lines.append(f"    Tokens in:  total={gs.total_tokens_in:,}  avg={gs.avg_tokens_in:,.1f}")
            lines.append(f"    Tokens out: total={gs.total_tokens_out:,}  avg={gs.avg_tokens_out:,.1f}")
            lines.append(f"    Events: total={gs.total_events}  avg={gs.avg_events:.1f}")
            if gs.durations:
                lines.append(f"    Duration (ms): avg={gs.avg_duration_ms:,.1f}  median={gs.median_duration_ms:,.1f}  p95={gs.p95_duration_ms:,.1f}")
                lines.append(f"                   min={gs.min_duration_ms:,.1f}  max={gs.max_duration_ms:,.1f}")
            if gs.models_used:
                lines.append(f"    Models: {', '.join(gs.models_used)}")

        lines.append("")
        lines.append("-" * 72)
        lines.append("HIGHLIGHTS")
        lines.append(f"  Best completion rate : {report.best_completion_rate}")
        lines.append(f"  Lowest error rate    : {report.lowest_error_rate}")
        lines.append(f"  Fastest (median)     : {report.fastest_median}")
        lines.append(f"  Most token-efficient : {report.most_efficient}")
        lines.append("=" * 72)
        return "\n".join(lines)

    @staticmethod
    def json_export(report: ComparisonReport) -> str:
        """Export report as JSON string."""
        return json.dumps(report.to_dict(), indent=2)

    # -- internals ----------------------------------------------------------

    def _group_by(self, key_fn: Callable[[Session], str]) -> dict[str, list[Session]]:
        groups: dict[str, list[Session]] = defaultdict(list)
        for s in self._sessions:
            groups[key_fn(s)].append(s)
        return dict(groups)

    @staticmethod
    def _primary_model(session: Session) -> str | None:
        """Return the most-used model in a session's events."""
        counts: dict[str, int] = defaultdict(int)
        for e in session.events:
            if e.model:
                counts[e.model] += 1
        if not counts:
            return None
        return max(counts, key=counts.__getitem__)
