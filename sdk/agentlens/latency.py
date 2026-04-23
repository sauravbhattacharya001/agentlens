"""Latency Profiler for AI agent pipelines.

Tracks per-step execution latency across agent sessions, computes
percentile distributions, detects slow steps, identifies bottlenecks,
and generates profiling reports.

Example::

    from agentlens.latency import LatencyProfiler

    profiler = LatencyProfiler()

    # Start a profiling session
    session = profiler.start_session("pipeline-run-42")

    # Time individual steps
    with session.step("retrieve_context"):
        docs = retriever.search(query)

    with session.step("llm_call"):
        response = llm.generate(prompt)

    with session.step("post_process"):
        result = parse(response)

    # Analyze
    report = profiler.report("pipeline-run-42")
    print(report.summary)
    # [pipeline-run-42] 3 steps | total 2.34s | bottleneck: llm_call (1.89s, 80.8%)

    # Detect slow steps vs historical baselines
    slow = profiler.detect_slow_steps("pipeline-run-42", threshold_factor=2.0)
"""

from __future__ import annotations

import statistics
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generator

from agentlens._utils import utcnow as _utcnow


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class StepStatus(str, Enum):
    """Status of a profiled step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepRecord:
    """A single timed step within a profiling session."""
    name: str
    step_id: str = field(default_factory=_new_id)
    status: StepStatus = StepStatus.PENDING
    start_time: float | None = None
    end_time: float | None = None
    wall_start: datetime | None = None
    wall_end: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        """Duration in seconds, or None if not completed."""
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return None

    @property
    def duration_ms(self) -> float | None:
        """Duration in milliseconds, or None if not completed."""
        d = self.duration_s
        return d * 1000.0 if d is not None else None


@dataclass
class ProfilingSession:
    """A collection of timed steps for a single pipeline run."""
    session_id: str
    label: str = ""
    steps: list[StepRecord] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    _step_index: dict[str, int] = field(default_factory=dict, repr=False)

    @contextmanager
    def step(self, name: str, **metadata: Any) -> Generator[StepRecord, None, None]:
        """Context manager to time a named step.

        Args:
            name: Human-readable step name.
            **metadata: Arbitrary key-value pairs attached to the step.

        Yields:
            The StepRecord (can be used to attach extra metadata).
        """
        record = StepRecord(name=name, metadata=metadata)
        idx = len(self.steps)
        self.steps.append(record)
        self._step_index[name] = idx

        record.status = StepStatus.RUNNING
        record.wall_start = _utcnow()
        record.start_time = time.perf_counter()
        try:
            yield record
            record.status = StepStatus.COMPLETED
        except Exception as exc:
            record.status = StepStatus.FAILED
            record.error = str(exc)
            raise
        finally:
            record.end_time = time.perf_counter()
            record.wall_end = _utcnow()

    def record_step(self, name: str, duration_s: float, **metadata: Any) -> StepRecord:
        """Manually record a step with a known duration (no context manager).

        Args:
            name: Step name.
            duration_s: Duration in seconds.
            **metadata: Extra metadata.

        Returns:
            The created StepRecord.
        """
        now = time.perf_counter()
        record = StepRecord(
            name=name,
            status=StepStatus.COMPLETED,
            start_time=now - duration_s,
            end_time=now,
            wall_start=_utcnow(),
            wall_end=_utcnow(),
            metadata=metadata,
        )
        idx = len(self.steps)
        self.steps.append(record)
        self._step_index[name] = idx
        return record

    @property
    def total_duration_s(self) -> float:
        """Total duration of all completed steps in seconds."""
        return sum(
            s.duration_s for s in self.steps
            if s.duration_s is not None
        )

    @property
    def completed_steps(self) -> list[StepRecord]:
        """Steps that finished successfully."""
        return [s for s in self.steps if s.status == StepStatus.COMPLETED]

    @property
    def failed_steps(self) -> list[StepRecord]:
        """Steps that failed."""
        return [s for s in self.steps if s.status == StepStatus.FAILED]

    @property
    def bottleneck(self) -> StepRecord | None:
        """The slowest completed step, or None if no steps completed."""
        completed = self.completed_steps
        if not completed:
            return None
        return max(completed, key=lambda s: s.duration_s or 0)

    def get_step(self, name: str) -> StepRecord | None:
        """Look up a step by name (returns most recent if duplicates)."""
        idx = self._step_index.get(name)
        if idx is not None and idx < len(self.steps):
            return self.steps[idx]
        return None


@dataclass
class PercentileStats:
    """Percentile distribution for a set of latency values."""
    count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    stdev_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "median_ms": round(self.median_ms, 3),
            "p90_ms": round(self.p90_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "stdev_ms": round(self.stdev_ms, 3),
        }


def compute_percentiles(values_ms: list[float]) -> PercentileStats | None:
    """Compute percentile statistics from a list of millisecond values.

    Returns None if the list is empty.
    """
    if not values_ms:
        return None
    sorted_v = sorted(values_ms)
    n = len(sorted_v)

    def _percentile(data: list[float], pct: float) -> float:
        k = (n - 1) * pct / 100.0
        f = int(k)
        c = f + 1 if f + 1 < n else f
        return data[f] + (k - f) * (data[c] - data[f])

    return PercentileStats(
        count=n,
        min_ms=sorted_v[0],
        max_ms=sorted_v[-1],
        mean_ms=statistics.mean(sorted_v),
        median_ms=statistics.median(sorted_v),
        p90_ms=_percentile(sorted_v, 90),
        p95_ms=_percentile(sorted_v, 95),
        p99_ms=_percentile(sorted_v, 99),
        stdev_ms=statistics.stdev(sorted_v) if n >= 2 else 0.0,
    )


@dataclass
class SlowStepAlert:
    """Alert for a step that exceeded the expected latency."""
    step_name: str
    session_id: str
    actual_ms: float
    baseline_mean_ms: float
    threshold_factor: float
    threshold_ms: float
    ratio: float  # actual / baseline mean

    @property
    def severity(self) -> str:
        """Severity based on how far above baseline."""
        if self.ratio >= 5.0:
            return "critical"
        if self.ratio >= 3.0:
            return "high"
        if self.ratio >= 2.0:
            return "medium"
        return "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "session_id": self.session_id,
            "actual_ms": round(self.actual_ms, 3),
            "baseline_mean_ms": round(self.baseline_mean_ms, 3),
            "threshold_factor": self.threshold_factor,
            "threshold_ms": round(self.threshold_ms, 3),
            "ratio": round(self.ratio, 2),
            "severity": self.severity,
        }


@dataclass
class SessionReport:
    """Profiling report for a single session."""
    session_id: str
    label: str
    total_duration_s: float
    step_count: int
    completed_count: int
    failed_count: int
    bottleneck_name: str | None
    bottleneck_duration_ms: float | None
    bottleneck_pct: float | None
    steps: list[dict[str, Any]]
    created_at: datetime

    @property
    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = [f"[{self.session_id}]"]
        parts.append(f"{self.step_count} steps")
        parts.append(f"total {self.total_duration_s:.2f}s")
        if self.bottleneck_name and self.bottleneck_duration_ms is not None:
            pct = self.bottleneck_pct or 0
            parts.append(
                f"bottleneck: {self.bottleneck_name} "
                f"({self.bottleneck_duration_ms:.0f}ms, {pct:.1f}%)"
            )
        if self.failed_count > 0:
            parts.append(f"{self.failed_count} failed")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "label": self.label,
            "total_duration_s": round(self.total_duration_s, 4),
            "step_count": self.step_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "bottleneck_name": self.bottleneck_name,
            "bottleneck_duration_ms": round(self.bottleneck_duration_ms, 3) if self.bottleneck_duration_ms else None,
            "bottleneck_pct": round(self.bottleneck_pct, 2) if self.bottleneck_pct else None,
            "steps": self.steps,
            "created_at": self.created_at.isoformat(),
        }


class LatencyProfiler:
    """Central profiler that manages sessions, computes baselines, and detects anomalies.

    Tracks latency across multiple pipeline runs, builds per-step
    baselines from historical data, and alerts on slow steps.
    """

    def __init__(self, baseline_window: int = 50) -> None:
        """Initialize profiler.

        Args:
            baseline_window: Number of recent sessions to consider
                for baseline computation (default 50).
        """
        self._sessions: dict[str, ProfilingSession] = {}
        self._session_order: list[str] = []  # insertion order
        self.baseline_window = baseline_window
        self._baseline_cache: dict[str, PercentileStats] | None = None
        self._baseline_generation: int = 0  # bumped on session add/remove

    def start_session(self, session_id: str | None = None, label: str = "") -> ProfilingSession:
        """Create and register a new profiling session.

        Args:
            session_id: Unique ID (auto-generated if None).
            label: Optional human-readable label.

        Returns:
            A ProfilingSession to time steps with.
        """
        sid = session_id or _new_id()
        session = ProfilingSession(session_id=sid, label=label)
        self._sessions[sid] = session
        self._session_order.append(sid)
        self._baseline_cache = None  # invalidate
        self._baseline_generation += 1
        return session

    def get_session(self, session_id: str) -> ProfilingSession | None:
        """Look up a session by ID."""
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            # Rebuild order list only when removing; this trades O(n)
            # remove for a clean list without tombstones.
            self._session_order = [
                sid for sid in self._session_order if sid != session_id
            ]
            self._baseline_cache = None  # invalidate
            self._baseline_generation += 1
            return True
        return False

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def report(self, session_id: str) -> SessionReport:
        """Generate a profiling report for a session.

        Args:
            session_id: The session to report on.

        Returns:
            SessionReport with step breakdown and bottleneck analysis.

        Raises:
            KeyError: If session not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")

        total = session.total_duration_s
        bottleneck = session.bottleneck
        bn_name = bottleneck.name if bottleneck else None
        bn_ms = bottleneck.duration_ms if bottleneck else None
        bn_pct = (
            ((bottleneck.duration_s or 0) / total * 100.0)
            if bottleneck and total > 0
            else None
        )

        steps_data = []
        for s in session.steps:
            d: dict[str, Any] = {
                "name": s.name,
                "status": s.status.value,
                "duration_ms": round(s.duration_ms, 3) if s.duration_ms is not None else None,
            }
            if total > 0 and s.duration_s is not None:
                d["pct_of_total"] = round(s.duration_s / total * 100, 2)
            if s.error:
                d["error"] = s.error
            if s.metadata:
                d["metadata"] = s.metadata
            steps_data.append(d)

        return SessionReport(
            session_id=session_id,
            label=session.label,
            total_duration_s=total,
            step_count=len(session.steps),
            completed_count=len(session.completed_steps),
            failed_count=len(session.failed_steps),
            bottleneck_name=bn_name,
            bottleneck_duration_ms=bn_ms,
            bottleneck_pct=bn_pct,
            steps=steps_data,
            created_at=session.created_at,
        )

    def step_baselines(self) -> dict[str, PercentileStats]:
        """Compute per-step latency baselines from recent sessions.

        Uses the last ``baseline_window`` sessions to build percentile
        distributions for each step name.  Results are cached and
        invalidated automatically when sessions are added or removed,
        so repeated calls (e.g. from ``detect_slow_steps`` followed by
        ``fleet_summary``) are O(1) after the first computation.

        Returns:
            Dict mapping step name to PercentileStats.
        """
        if self._baseline_cache is not None:
            return self._baseline_cache

        recent_ids = self._session_order[-self.baseline_window:]
        step_values: dict[str, list[float]] = {}

        for sid in recent_ids:
            session = self._sessions.get(sid)
            if session is None:
                continue
            for step in session.completed_steps:
                if step.duration_ms is not None:
                    step_values.setdefault(step.name, []).append(step.duration_ms)

        baselines: dict[str, PercentileStats] = {}
        for name, values in step_values.items():
            stats = compute_percentiles(values)
            if stats is not None:
                baselines[name] = stats

        self._baseline_cache = baselines
        return baselines

    def detect_slow_steps(
        self,
        session_id: str,
        threshold_factor: float = 2.0,
    ) -> list[SlowStepAlert]:
        """Detect steps in a session that are slower than historical baselines.

        A step is flagged as slow if its duration exceeds
        `baseline_mean * threshold_factor`.

        Args:
            session_id: Session to analyze.
            threshold_factor: Multiplier on the baseline mean (default 2.0).

        Returns:
            List of SlowStepAlert for steps exceeding the threshold.

        Raises:
            KeyError: If session not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")

        baselines = self.step_baselines()
        alerts: list[SlowStepAlert] = []

        for step in session.completed_steps:
            if step.duration_ms is None:
                continue
            baseline = baselines.get(step.name)
            if baseline is None:
                continue
            threshold_ms = baseline.mean_ms * threshold_factor
            if step.duration_ms > threshold_ms:
                alerts.append(SlowStepAlert(
                    step_name=step.name,
                    session_id=session_id,
                    actual_ms=step.duration_ms,
                    baseline_mean_ms=baseline.mean_ms,
                    threshold_factor=threshold_factor,
                    threshold_ms=threshold_ms,
                    ratio=step.duration_ms / baseline.mean_ms if baseline.mean_ms > 0 else 0,
                ))

        alerts.sort(key=lambda a: a.ratio, reverse=True)
        return alerts

    def compare_sessions(
        self,
        session_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Compare step latencies across multiple sessions.

        Returns a dict mapping step name to a list of
        {session_id, duration_ms} entries, sorted by duration.

        Args:
            session_ids: Sessions to compare.

        Returns:
            Dict of step_name -> [{session_id, duration_ms}].
        """
        comparison: dict[str, list[dict[str, Any]]] = {}
        for sid in session_ids:
            session = self._sessions.get(sid)
            if session is None:
                continue
            for step in session.completed_steps:
                if step.duration_ms is None:
                    continue
                comparison.setdefault(step.name, []).append({
                    "session_id": sid,
                    "duration_ms": round(step.duration_ms, 3),
                })

        for name in comparison:
            comparison[name].sort(key=lambda e: e["duration_ms"])

        return comparison

    def fleet_summary(self) -> dict[str, Any]:
        """Aggregate statistics across all sessions.

        Returns:
            Dict with total_sessions, total_steps, overall_duration,
            step_baselines, slowest_sessions, and failure_rate.
        """
        total_steps = 0
        total_completed = 0
        total_failed = 0
        total_duration = 0.0
        session_durations: list[tuple[str, float]] = []

        for sid in self._session_order:
            session = self._sessions.get(sid)
            if session is None:
                continue
            total_steps += len(session.steps)
            total_completed += len(session.completed_steps)
            total_failed += len(session.failed_steps)
            dur = session.total_duration_s
            total_duration += dur
            session_durations.append((sid, dur))

        session_durations.sort(key=lambda x: x[1], reverse=True)

        baselines = self.step_baselines()
        baselines_dict = {name: stats.to_dict() for name, stats in baselines.items()}

        return {
            "total_sessions": len(self._sessions),
            "total_steps": total_steps,
            "total_completed": total_completed,
            "total_failed": total_failed,
            "failure_rate": round(total_failed / total_steps, 4) if total_steps > 0 else 0.0,
            "total_duration_s": round(total_duration, 4),
            "avg_session_duration_s": round(total_duration / len(self._sessions), 4) if self._sessions else 0.0,
            "slowest_sessions": [
                {"session_id": sid, "duration_s": round(dur, 4)}
                for sid, dur in session_durations[:5]
            ],
            "step_baselines": baselines_dict,
        }
