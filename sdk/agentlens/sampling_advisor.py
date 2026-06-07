"""Agentic sampling advisor for AgentLens.

The :mod:`agentlens.sampling` module gives you the sampling *primitives*
(probabilistic, rate-limited, priority, tail, composite).  In production
you still have to *decide* what knobs to set: what fallback rate keeps
your storage budget happy?  What latency counts as "slow" for *your*
workload?  Are errors so rare you can keep all of them, or so common
they'd blow the budget on their own?

:class:`SamplingAdvisor` answers those questions.  Feed it a window of
historical events (or :class:`~agentlens.models.AgentEvent` objects from
a :class:`~agentlens.tracker.AgentTracker`) plus an optional volume
target and it will:

1. Profile the workload -- volume per minute, error rate, latency
   distribution, token spend.
2. Decide a recommended :class:`~agentlens.sampling.PrioritySampler`
   configuration:

   * ``error_always_keep`` (toggled off automatically if errors are so
     common they'd swallow the whole budget).
   * ``slow_threshold_ms`` set to the empirical p95 of duration.
   * ``important_threshold`` mapped to the event "priority" tag p90.
   * ``fallback_rate`` solved so total expected kept traces match the
     target volume.

3. Emit a report explaining *why* it chose each value (audit trail) and
   the expected volume reduction.
4. Optionally build a ready-to-use :class:`PrioritySampler` instance.

The advisor is the next step on AgentLens' agency ladder: instead of
asking the operator to hand-tune sampler knobs, the system observes
itself and proposes a configuration with its reasoning.

Example
-------
::

    from agentlens.sampling_advisor import SamplingAdvisor

    advisor = SamplingAdvisor()
    advisor.observe(tracker.list_events(limit=5000))
    advice = advisor.recommend(target_volume_per_minute=20)

    print(advice.summary())
    sampler = advice.build_sampler()
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from collections.abc import Iterable, Sequence

from agentlens._utils import parse_iso_or_epoch as _parse_ts_raw, percentile as _percentile_impl


def _to_dt(value: Any) -> datetime | None:
    """Parse a timestamp value into a timezone-aware datetime.

    Delegates to :func:`agentlens._utils.parse_iso_or_epoch` but ensures
    naive ``datetime`` objects are stamped UTC to match this module's
    historical contract.
    """
    dt = _parse_ts_raw(value)
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorkloadProfile:
    """Empirical view of a window of traces."""

    sample_count: int = 0
    window_seconds: float = 0.0
    events_per_minute: float = 0.0
    error_rate: float = 0.0
    error_count: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_max_ms: float = 0.0
    avg_tokens: float = 0.0
    total_tokens: int = 0
    priority_p90: float = 0.0
    distinct_event_types: int = 0
    distinct_models: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SamplingAdvice:
    """Recommended sampler configuration plus reasoning."""

    fallback_rate: float
    error_always_keep: bool
    slow_threshold_ms: float
    important_threshold: int
    target_events_per_minute: float | None
    expected_keep_rate: float
    expected_kept_per_minute: float
    expected_volume_reduction_pct: float
    reasoning: list[str] = field(default_factory=list)
    profile: WorkloadProfile | None = None
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["generated_at"] = self.generated_at.isoformat()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def summary(self) -> str:
        """Compact human-readable summary."""
        lines = [
            "SamplingAdvisor recommendation",
            f"  fallback_rate       = {self.fallback_rate:.3f}",
            f"  error_always_keep   = {self.error_always_keep}",
            f"  slow_threshold_ms   = {self.slow_threshold_ms:.0f}",
            f"  important_threshold = {self.important_threshold}",
            f"  expected keep rate  = {self.expected_keep_rate:.1%}",
            f"  expected kept/min   = {self.expected_kept_per_minute:.1f}",
            f"  volume reduction    = {self.expected_volume_reduction_pct:.1f}%",
        ]
        if self.target_events_per_minute is not None:
            lines.append(
                f"  target/min          = {self.target_events_per_minute:.1f}"
            )
        if self.reasoning:
            lines.append("  reasoning:")
            lines.extend(f"    - {r}" for r in self.reasoning)
        return "\n".join(lines)

    def to_markdown(self) -> str:
        head = "# Sampling Advisor\n"
        body = (
            "| Setting | Value |\n"
            "|---|---|\n"
            f"| fallback_rate | {self.fallback_rate:.3f} |\n"
            f"| error_always_keep | {self.error_always_keep} |\n"
            f"| slow_threshold_ms | {self.slow_threshold_ms:.0f} |\n"
            f"| important_threshold | {self.important_threshold} |\n"
            f"| expected keep rate | {self.expected_keep_rate:.1%} |\n"
            f"| expected kept/min | {self.expected_kept_per_minute:.1f} |\n"
            f"| volume reduction | {self.expected_volume_reduction_pct:.1f}% |\n"
        )
        if self.target_events_per_minute is not None:
            body += f"| target/min | {self.target_events_per_minute:.1f} |\n"
        reasoning = ""
        if self.reasoning:
            reasoning = "\n## Reasoning\n\n" + "\n".join(
                f"- {r}" for r in self.reasoning
            )
        return head + "\n" + body + reasoning + "\n"

    def format(self, fmt: str = "text") -> str:
        fmt = (fmt or "text").lower()
        if fmt in ("text", "txt", "summary"):
            return self.summary()
        if fmt in ("md", "markdown"):
            return self.to_markdown()
        if fmt == "json":
            return self.to_json()
        raise ValueError(f"unsupported format: {fmt}")

    def build_sampler(self):  # pragma: no cover - thin wrapper
        """Return a configured :class:`PrioritySampler`."""
        from agentlens.sampling import PrioritySampler

        return PrioritySampler(
            error_always_keep=self.error_always_keep,
            slow_threshold_ms=self.slow_threshold_ms,
            priority_threshold=self.important_threshold,
            fallback_rate=self.fallback_rate,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Duck-typed accessor: works for dataclasses, Pydantic and dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _percentile(values: Sequence[float], pct: float) -> float:
    """Compute a percentile of unsorted values (fractional 0..1 scale).

    Thin wrapper around :func:`agentlens._utils.percentile` that preserves
    the 0..1 calling convention used by this module while delegating the
    actual interpolation to the SDK-wide helper.
    """
    if not values:
        return 0.0
    return float(_percentile_impl(sorted(values), pct * 100.0))



# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------


class SamplingAdvisor:
    """Profile a workload and recommend a sampler configuration."""

    #: Default minimum allowed fallback rate when solving for a budget.
    DEFAULT_MIN_FALLBACK = 0.001
    #: Default maximum allowed fallback rate.
    DEFAULT_MAX_FALLBACK = 1.0

    def __init__(
        self,
        *,
        min_fallback: float | None = None,
        max_fallback: float | None = None,
        min_slow_threshold_ms: float = 250.0,
    ) -> None:
        self._events: list[Any] = []
        self._min_fallback = (
            self.DEFAULT_MIN_FALLBACK if min_fallback is None else float(min_fallback)
        )
        self._max_fallback = (
            self.DEFAULT_MAX_FALLBACK if max_fallback is None else float(max_fallback)
        )
        if not 0.0 <= self._min_fallback <= self._max_fallback <= 1.0:
            raise ValueError("min_fallback must be <= max_fallback within [0, 1]")
        self._min_slow_threshold_ms = float(min_slow_threshold_ms)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe(self, events: Iterable[Any]) -> SamplingAdvisor:
        """Add observed events to the rolling window."""
        for ev in events:
            self._events.append(ev)
        return self

    def reset(self) -> None:
        self._events.clear()

    @property
    def event_count(self) -> int:
        return len(self._events)

    # ------------------------------------------------------------------
    # Profiling
    # ------------------------------------------------------------------

    def analyze(self) -> WorkloadProfile:
        """Compute a :class:`WorkloadProfile` over observed events."""
        if not self._events:
            return WorkloadProfile(notes=["no events observed"])

        durations: list[float] = []
        tokens: list[int] = []
        priorities: list[float] = []
        timestamps: list[datetime] = []
        models: set[str] = set()
        types: set[str] = set()
        error_count = 0
        total_tokens = 0

        for ev in self._events:
            d = _get(ev, "duration_ms")
            if d is None:
                tc = _get(ev, "tool_call")
                if tc is not None:
                    d = _get(tc, "duration_ms")
            if d is not None:
                try:
                    durations.append(float(d))
                except (TypeError, ValueError):
                    pass

            t_in = _get(ev, "tokens_in", 0) or 0
            t_out = _get(ev, "tokens_out", 0) or 0
            try:
                t_total = int(t_in) + int(t_out)
            except (TypeError, ValueError):
                t_total = 0
            if t_total:
                tokens.append(t_total)
                total_tokens += t_total

            etype = _get(ev, "event_type") or "generic"
            types.add(str(etype))
            if str(etype).lower() in ("error", "failure", "exception"):
                error_count += 1
            # Also count events whose output_data carries an error marker
            elif (out := _get(ev, "output_data")) and isinstance(out, dict):
                if out.get("error") or out.get("status") in ("error", "failed"):
                    error_count += 1

            model = _get(ev, "model")
            if model:
                models.add(str(model))

            prio = _get(ev, "priority")
            if prio is None:
                meta = _get(ev, "metadata")
                if isinstance(meta, dict):
                    prio = meta.get("priority")
            if prio is not None:
                try:
                    priorities.append(float(prio))
                except (TypeError, ValueError):
                    pass

            ts = _to_dt(_get(ev, "timestamp"))
            if ts is not None:
                timestamps.append(ts)

        sample_count = len(self._events)
        window_seconds = 0.0
        if len(timestamps) >= 2:
            window_seconds = max(
                (max(timestamps) - min(timestamps)).total_seconds(), 0.0
            )
        events_per_minute = (
            sample_count / (window_seconds / 60.0)
            if window_seconds > 0
            else float(sample_count)  # treat as "per minute" when no time info
        )

        avg_tokens = statistics.fmean(tokens) if tokens else 0.0

        profile = WorkloadProfile(
            sample_count=sample_count,
            window_seconds=window_seconds,
            events_per_minute=events_per_minute,
            error_rate=(error_count / sample_count) if sample_count else 0.0,
            error_count=error_count,
            latency_p50_ms=_percentile(durations, 0.50),
            latency_p95_ms=_percentile(durations, 0.95),
            latency_p99_ms=_percentile(durations, 0.99),
            latency_max_ms=max(durations) if durations else 0.0,
            avg_tokens=avg_tokens,
            total_tokens=total_tokens,
            priority_p90=_percentile(priorities, 0.90) if priorities else 0.0,
            distinct_event_types=len(types),
            distinct_models=len(models),
        )

        if window_seconds <= 0:
            profile.notes.append(
                "no usable timestamps - events_per_minute treated as raw count"
            )
        if not durations:
            profile.notes.append("no duration_ms observed - slow detection disabled")
        if not priorities:
            profile.notes.append(
                "no priority field on events - important_threshold default applied"
            )

        return profile

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    def recommend(
        self,
        *,
        target_events_per_minute: float | None = None,
        target_keep_pct: float | None = None,
        max_error_keep_rate: float = 0.30,
    ) -> SamplingAdvice:
        """Build a :class:`SamplingAdvice`.

        Parameters
        ----------
        target_events_per_minute
            How many kept traces per minute you can afford.  If None,
            falls back to ``target_keep_pct``.
        target_keep_pct
            Desired fraction of all incoming traces to keep (0-1).  Used
            when ``target_events_per_minute`` is None.
        max_error_keep_rate
            If the observed error rate exceeds this fraction we turn
            off ``error_always_keep`` (otherwise errors alone would blow
            the budget).
        """
        profile = self.analyze()
        reasoning: list[str] = []

        # --- slow_threshold_ms ------------------------------------------------
        slow_threshold = profile.latency_p95_ms
        if slow_threshold <= 0:
            slow_threshold = self._min_slow_threshold_ms
            reasoning.append(
                f"no latency data; defaulting slow_threshold_ms to {slow_threshold:.0f}"
            )
        elif slow_threshold < self._min_slow_threshold_ms:
            reasoning.append(
                f"p95 latency {slow_threshold:.0f}ms below floor; "
                f"raising slow_threshold_ms to {self._min_slow_threshold_ms:.0f}"
            )
            slow_threshold = self._min_slow_threshold_ms
        else:
            reasoning.append(
                f"slow_threshold_ms set to p95 latency ({slow_threshold:.0f}ms)"
            )

        # --- important_threshold ---------------------------------------------
        if profile.priority_p90 > 0:
            important_threshold = int(max(1, math.ceil(profile.priority_p90)))
            reasoning.append(
                f"important_threshold set to priority p90 ({important_threshold})"
            )
        else:
            important_threshold = 5
            reasoning.append(
                "no priority data; important_threshold defaulted to 5"
            )

        # --- error_always_keep -----------------------------------------------
        error_always_keep = True
        if profile.error_rate > max_error_keep_rate:
            error_always_keep = False
            reasoning.append(
                f"error_rate {profile.error_rate:.1%} exceeds "
                f"{max_error_keep_rate:.0%} cap; disabling error_always_keep"
            )
        else:
            reasoning.append(
                f"errors are rare ({profile.error_rate:.1%}); keeping all of them"
            )

        # --- target volume ---------------------------------------------------
        epm = profile.events_per_minute
        target_kept_per_min: float | None = None
        if target_events_per_minute is not None:
            target_kept_per_min = max(0.0, float(target_events_per_minute))
            reasoning.append(
                f"solving for target of {target_kept_per_min:.1f} kept events/min"
            )
        elif target_keep_pct is not None:
            pct = max(0.0, min(1.0, float(target_keep_pct)))
            target_kept_per_min = epm * pct
            reasoning.append(
                f"target_keep_pct {pct:.1%} -> {target_kept_per_min:.1f} kept/min"
            )

        # Mandatory keeps come from errors + slow tail.  We approximate
        # the slow tail as ~5% (since slow_threshold = p95) plus a small
        # buffer; if we set the threshold to the p95 we keep p95-and-up
        # which is at least 5% of traces.
        slow_keep_frac = 0.05 if profile.latency_p95_ms > 0 else 0.0
        error_keep_frac = profile.error_rate if error_always_keep else 0.0
        # crude union estimate, capped at 1.0
        mandatory_frac = min(1.0, slow_keep_frac + error_keep_frac)

        # --- fallback_rate ---------------------------------------------------
        if target_kept_per_min is None or epm <= 0:
            fallback_rate = 0.2  # production-friendly default
            reasoning.append(
                "no target supplied; defaulting fallback_rate to 0.20"
            )
        else:
            mandatory_per_min = epm * mandatory_frac
            remaining = target_kept_per_min - mandatory_per_min
            other_per_min = max(epm * (1.0 - mandatory_frac), 1e-9)
            if remaining <= 0:
                fallback_rate = self._min_fallback
                reasoning.append(
                    "mandatory keeps already meet/exceed target; "
                    f"fallback_rate clamped to floor {fallback_rate:.3f}"
                )
            else:
                fallback_rate = remaining / other_per_min
                fallback_rate = max(
                    self._min_fallback, min(self._max_fallback, fallback_rate)
                )
                reasoning.append(
                    f"fallback_rate solved to {fallback_rate:.3f} "
                    f"({remaining:.1f}/{other_per_min:.1f} traces/min via probabilistic)"
                )

        # --- expected keep rate ----------------------------------------------
        expected_keep_rate = min(
            1.0, mandatory_frac + (1.0 - mandatory_frac) * fallback_rate
        )
        expected_kept_per_minute = epm * expected_keep_rate
        expected_reduction_pct = (1.0 - expected_keep_rate) * 100.0

        advice = SamplingAdvice(
            fallback_rate=round(fallback_rate, 4),
            error_always_keep=error_always_keep,
            slow_threshold_ms=round(slow_threshold, 1),
            important_threshold=important_threshold,
            target_events_per_minute=target_kept_per_min,
            expected_keep_rate=round(expected_keep_rate, 4),
            expected_kept_per_minute=round(expected_kept_per_minute, 2),
            expected_volume_reduction_pct=round(expected_reduction_pct, 2),
            reasoning=reasoning,
            profile=profile,
        )
        return advice


__all__ = [
    "WorkloadProfile",
    "SamplingAdvice",
    "SamplingAdvisor",
]
