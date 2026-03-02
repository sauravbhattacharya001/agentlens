"""Behavioral Drift Detection for AgentLens.

Detects behavioral changes in AI agents over time by comparing metrics
between a baseline window and a current window.  Answers the question:
"Is my agent behaving differently than before?"

Works with session objects — no external dependencies beyond stdlib.

Usage::

    from agentlens.drift import DriftDetector

    detector = DriftDetector()

    # Add sessions from two time periods
    for s in historical_sessions:
        detector.add_baseline(s)
    for s in recent_sessions:
        detector.add_current(s)

    # Detect drift
    report = detector.detect()
    print(report.format_report())
    print(f"Drift score: {report.drift_score}/100")
    print(f"Status: {report.status.value}")

    # Or compare two session lists directly
    report = DriftDetector.compare(baseline_sessions, current_sessions)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────

class DriftStatus(Enum):
    """Overall drift classification."""
    STABLE = "stable"
    MINOR_DRIFT = "minor_drift"
    SIGNIFICANT_DRIFT = "significant_drift"
    DEGRADED = "degraded"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class DriftDirection(Enum):
    """Direction of metric change."""
    INCREASED = "increased"
    DECREASED = "decreased"
    STABLE = "stable"


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class MetricDrift:
    """Drift analysis for a single metric.

    Attributes
    ----------
    name : str
        Metric name (e.g. ``avg_latency_ms``, ``error_rate``).
    baseline_mean : float
        Mean value in the baseline window.
    current_mean : float
        Mean value in the current window.
    baseline_std : float
        Standard deviation in the baseline window.
    current_std : float
        Standard deviation in the current window.
    relative_change : float
        Relative change as a fraction: ``(current - baseline) / baseline``.
    effect_size : float
        Cohen's d effect size (standardized difference).
    direction : DriftDirection
        Whether the metric increased, decreased, or stayed stable.
    is_drifting : bool
        True if the change is statistically meaningful.
    """
    name: str = ""
    baseline_mean: float = 0.0
    current_mean: float = 0.0
    baseline_std: float = 0.0
    current_std: float = 0.0
    relative_change: float = 0.0
    effect_size: float = 0.0
    direction: DriftDirection = DriftDirection.STABLE
    is_drifting: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "baseline_mean": round(self.baseline_mean, 4),
            "current_mean": round(self.current_mean, 4),
            "baseline_std": round(self.baseline_std, 4),
            "current_std": round(self.current_std, 4),
            "relative_change": round(self.relative_change, 4),
            "effect_size": round(self.effect_size, 4),
            "direction": self.direction.value,
            "is_drifting": self.is_drifting,
        }


@dataclass
class ToolUsageDrift:
    """Drift in tool usage patterns.

    Attributes
    ----------
    tool_name : str
        Name of the tool.
    baseline_rate : float
        Proportion of events using this tool in baseline.
    current_rate : float
        Proportion of events using this tool in current.
    change : float
        Absolute change in usage rate.
    is_new : bool
        Tool appeared only in the current window.
    is_dropped : bool
        Tool appeared only in the baseline window.
    """
    tool_name: str = ""
    baseline_rate: float = 0.0
    current_rate: float = 0.0
    change: float = 0.0
    is_new: bool = False
    is_dropped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "baseline_rate": round(self.baseline_rate, 4),
            "current_rate": round(self.current_rate, 4),
            "change": round(self.change, 4),
            "is_new": self.is_new,
            "is_dropped": self.is_dropped,
        }


@dataclass
class DriftReport:
    """Complete drift analysis report.

    Attributes
    ----------
    drift_score : int
        Overall drift score (0-100). 0 = identical, 100 = completely different.
    status : DriftStatus
        Classification: stable / minor_drift / significant_drift / degraded.
    baseline_sessions : int
        Number of sessions in the baseline window.
    current_sessions : int
        Number of sessions in the current window.
    metric_drifts : list of MetricDrift
        Per-metric drift analysis.
    tool_drifts : list of ToolUsageDrift
        Per-tool usage changes.
    drifting_metrics : list of str
        Names of metrics that are significantly drifting.
    summary : str
        Human-readable summary sentence.
    """
    drift_score: int = 0
    status: DriftStatus = DriftStatus.STABLE
    baseline_sessions: int = 0
    current_sessions: int = 0
    metric_drifts: list = field(default_factory=list)
    tool_drifts: list = field(default_factory=list)
    drifting_metrics: list = field(default_factory=list)
    summary: str = ""

    def format_report(self) -> str:
        """Return a human-readable text report."""
        lines = [
            "=" * 60,
            "  Behavioral Drift Report",
            "=" * 60,
            "",
            f"  Drift Score:        {self.drift_score}/100",
            f"  Status:             {self.status.label}",
            f"  Baseline Sessions:  {self.baseline_sessions}",
            f"  Current Sessions:   {self.current_sessions}",
            "",
        ]

        if self.drifting_metrics:
            lines.append(f"  Drifting Metrics:   {', '.join(self.drifting_metrics)}")
            lines.append("")

        # Metric details
        lines.append("  ── Metric Analysis ──")
        lines.append(
            f"  {'Metric':<22} {'Baseline':>10} {'Current':>10} "
            f"{'Change':>8} {'Effect':>8} {'Status':>10}"
        )
        lines.append(f"  {'─' * 22} {'─' * 10} {'─' * 10} {'─' * 8} {'─' * 8} {'─' * 10}")
        for m in self.metric_drifts:
            status = "DRIFT" if m.is_drifting else "stable"
            lines.append(
                f"  {m.name:<22} {m.baseline_mean:>10.2f} {m.current_mean:>10.2f} "
                f"{m.relative_change:>+7.1%} {m.effect_size:>8.2f} {status:>10}"
            )
        lines.append("")

        # Tool usage changes
        if self.tool_drifts:
            lines.append("  ── Tool Usage Changes ──")
            for t in self.tool_drifts:
                if t.is_new:
                    lines.append(f"  + {t.tool_name} (NEW — {t.current_rate:.1%} of events)")
                elif t.is_dropped:
                    lines.append(f"  - {t.tool_name} (DROPPED — was {t.baseline_rate:.1%})")
                elif abs(t.change) > 0.05:
                    lines.append(
                        f"  ~ {t.tool_name}: {t.baseline_rate:.1%} → {t.current_rate:.1%} "
                        f"({t.change:+.1%})"
                    )
            lines.append("")

        lines.append(f"  Summary: {self.summary}")
        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_score": self.drift_score,
            "status": self.status.value,
            "baseline_sessions": self.baseline_sessions,
            "current_sessions": self.current_sessions,
            "metric_drifts": [m.to_dict() for m in self.metric_drifts],
            "tool_drifts": [t.to_dict() for t in self.tool_drifts],
            "drifting_metrics": self.drifting_metrics,
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# ── Helpers ─────────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    """Arithmetic mean, 0.0 for empty."""
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _cohens_d(mean1: float, std1: float, n1: int,
              mean2: float, std2: float, n2: int) -> float:
    """Cohen's d effect size (pooled standard deviation).

    Uses the pooled SD formula: sqrt(((n1-1)*s1² + (n2-1)*s2²) / (n1+n2-2)).

    When both groups have zero variance but different means, the difference
    is infinitely significant — we return a large sentinel value (10.0)
    capped to avoid overflow.  Returns 0.0 when means are equal or when
    there are too few observations.
    """
    if n1 + n2 < 3:
        return 0.0
    diff = mean2 - mean1
    if diff == 0:
        return 0.0
    pooled_var = ((max(n1 - 1, 0) * std1 ** 2) + (max(n2 - 1, 0) * std2 ** 2)) / max(n1 + n2 - 2, 1)
    pooled_sd = math.sqrt(pooled_var)
    if pooled_sd == 0:
        # Both groups have zero variance but different means — maximally
        # significant.  Use the absolute mean as a denominator fallback
        # so effect size scales with the magnitude of change.
        fallback = max(abs(mean1), abs(mean2), 1e-9)
        return min(abs(diff) / fallback * 5.0, 10.0)
    return diff / pooled_sd


def _extract_session_metrics(session: Any) -> dict[str, float]:
    """Extract drift-relevant metrics from a session."""
    events = getattr(session, "events", []) or []
    event_count = len(events)

    metrics: dict[str, float] = {"event_count": float(event_count)}

    if event_count == 0:
        for k in ("avg_latency_ms", "error_rate", "total_tokens",
                   "tokens_per_event", "tool_call_rate"):
            metrics[k] = 0.0
        return metrics

    # Latency
    durations = [
        e.duration_ms for e in events
        if hasattr(e, "duration_ms") and e.duration_ms is not None
    ]
    metrics["avg_latency_ms"] = _mean(durations) if durations else 0.0

    # Tokens
    total_tokens = sum(
        (getattr(e, "tokens_in", 0) or 0) + (getattr(e, "tokens_out", 0) or 0)
        for e in events
    )
    metrics["total_tokens"] = float(total_tokens)
    metrics["tokens_per_event"] = total_tokens / event_count

    # Error rate
    error_count = sum(
        1 for e in events
        if getattr(e, "event_type", "") == "error"
    )
    metrics["error_rate"] = error_count / event_count

    # Tool call rate
    tool_count = sum(
        1 for e in events
        if getattr(e, "tool_call", None) is not None
    )
    metrics["tool_call_rate"] = tool_count / event_count

    return metrics


def _extract_tool_usage(sessions: list) -> dict[str, int]:
    """Count tool invocations across sessions."""
    counts: dict[str, int] = {}
    for session in sessions:
        for event in getattr(session, "events", []) or []:
            tc = getattr(event, "tool_call", None)
            if tc is not None:
                name = getattr(tc, "tool_name", "unknown")
                counts[name] = counts.get(name, 0) + 1
    return counts


def _total_events(sessions: list) -> int:
    """Total event count across sessions."""
    return sum(len(getattr(s, "events", []) or []) for s in sessions)


# ── Main detector ───────────────────────────────────────────────────

# Drift effect-size thresholds (Cohen's d)
_DRIFT_THRESHOLD = 0.5       # Medium effect = drifting
_DEGRADED_THRESHOLD = 1.0    # Large effect = degraded

# Metrics where an increase is bad (higher = worse agent behavior)
_NEGATIVE_METRICS = {"error_rate", "avg_latency_ms"}


class DriftDetector:
    """Detects behavioral drift between baseline and current session windows.

    Add sessions to the ``baseline`` and ``current`` windows, then call
    :meth:`detect` to produce a :class:`DriftReport`.

    Parameters
    ----------
    drift_threshold : float
        Cohen's d threshold for flagging a metric as drifting (default 0.5).
    """

    def __init__(self, drift_threshold: float = _DRIFT_THRESHOLD) -> None:
        if drift_threshold <= 0:
            raise ValueError("drift_threshold must be positive")
        self._baseline: list = []
        self._current: list = []
        self._threshold = drift_threshold

    @property
    def baseline_count(self) -> int:
        """Number of baseline sessions."""
        return len(self._baseline)

    @property
    def current_count(self) -> int:
        """Number of current sessions."""
        return len(self._current)

    def add_baseline(self, session: Any) -> None:
        """Add a session to the baseline window."""
        self._baseline.append(session)

    def add_current(self, session: Any) -> None:
        """Add a session to the current window."""
        self._current.append(session)

    def clear(self) -> None:
        """Reset both windows."""
        self._baseline.clear()
        self._current.clear()

    def detect(self) -> DriftReport:
        """Compare baseline and current windows, returning a DriftReport.

        Raises
        ------
        ValueError
            If either window is empty.
        """
        if not self._baseline:
            raise ValueError("Baseline window is empty — add sessions with add_baseline()")
        if not self._current:
            raise ValueError("Current window is empty — add sessions with add_current()")

        return self._compare(self._baseline, self._current)

    @classmethod
    def compare(cls, baseline: list, current: list,
                drift_threshold: float = _DRIFT_THRESHOLD) -> DriftReport:
        """One-shot comparison of two session lists.

        Parameters
        ----------
        baseline : list
            Baseline sessions.
        current : list
            Current sessions.
        drift_threshold : float
            Cohen's d threshold.

        Returns
        -------
        DriftReport
        """
        det = cls(drift_threshold=drift_threshold)
        for s in baseline:
            det.add_baseline(s)
        for s in current:
            det.add_current(s)
        return det.detect()

    def _compare(self, baseline: list, current: list) -> DriftReport:
        """Internal comparison engine."""
        # Extract per-session metrics
        b_metrics = [_extract_session_metrics(s) for s in baseline]
        c_metrics = [_extract_session_metrics(s) for s in current]

        # Determine all metric names
        metric_names = sorted(set().union(
            *(m.keys() for m in b_metrics),
            *(m.keys() for m in c_metrics),
        ))

        # Analyze each metric
        metric_drifts: list[MetricDrift] = []
        drifting_names: list[str] = []
        drift_scores: list[float] = []

        for name in metric_names:
            b_vals = [m.get(name, 0.0) for m in b_metrics]
            c_vals = [m.get(name, 0.0) for m in c_metrics]

            b_mean = _mean(b_vals)
            c_mean = _mean(c_vals)
            b_std = _std(b_vals)
            c_std = _std(c_vals)

            # Relative change
            if b_mean != 0:
                rel_change = (c_mean - b_mean) / abs(b_mean)
            elif c_mean != 0:
                rel_change = 1.0  # went from 0 to something
            else:
                rel_change = 0.0

            # Effect size
            d = _cohens_d(b_mean, b_std, len(b_vals), c_mean, c_std, len(c_vals))

            # Direction
            if abs(d) < 0.2:
                direction = DriftDirection.STABLE
            elif c_mean > b_mean:
                direction = DriftDirection.INCREASED
            else:
                direction = DriftDirection.DECREASED

            is_drifting = abs(d) >= self._threshold

            md = MetricDrift(
                name=name,
                baseline_mean=b_mean,
                current_mean=c_mean,
                baseline_std=b_std,
                current_std=c_std,
                relative_change=rel_change,
                effect_size=d,
                direction=direction,
                is_drifting=is_drifting,
            )
            metric_drifts.append(md)

            if is_drifting:
                drifting_names.append(name)

            # Contribute to overall drift score based on effect size
            drift_scores.append(min(abs(d) / 2.0, 1.0))  # cap at 1.0

        # Tool usage drift
        b_tools = _extract_tool_usage(baseline)
        c_tools = _extract_tool_usage(current)
        b_total = max(_total_events(baseline), 1)
        c_total = max(_total_events(current), 1)
        all_tools = sorted(set(b_tools.keys()) | set(c_tools.keys()))

        tool_drifts: list[ToolUsageDrift] = []
        tool_drift_count = 0
        for tool in all_tools:
            b_rate = b_tools.get(tool, 0) / b_total
            c_rate = c_tools.get(tool, 0) / c_total
            is_new = tool not in b_tools
            is_dropped = tool not in c_tools
            change = c_rate - b_rate

            td = ToolUsageDrift(
                tool_name=tool,
                baseline_rate=b_rate,
                current_rate=c_rate,
                change=change,
                is_new=is_new,
                is_dropped=is_dropped,
            )
            tool_drifts.append(td)

            if is_new or is_dropped or abs(change) > 0.1:
                tool_drift_count += 1

        # Overall drift score (0-100)
        if drift_scores:
            avg_drift = _mean(drift_scores)
        else:
            avg_drift = 0.0

        # Boost score if tools are changing
        tool_factor = min(tool_drift_count * 0.1, 0.3)
        raw_score = avg_drift + tool_factor

        drift_score = min(int(raw_score * 100), 100)

        # Classify
        has_degradation = any(
            md.is_drifting and md.name in _NEGATIVE_METRICS
            and md.direction == DriftDirection.INCREASED
            for md in metric_drifts
        )

        if has_degradation and drift_score >= 40:
            status = DriftStatus.DEGRADED
        elif drift_score >= 30:
            status = DriftStatus.SIGNIFICANT_DRIFT
        elif drift_score >= 15:
            status = DriftStatus.MINOR_DRIFT
        else:
            status = DriftStatus.STABLE

        # Summary
        if status == DriftStatus.STABLE:
            summary = "Agent behavior is consistent with the baseline."
        elif status == DriftStatus.MINOR_DRIFT:
            summary = (
                f"Minor drift detected in {len(drifting_names)} metric(s): "
                f"{', '.join(drifting_names)}."
            )
        elif status == DriftStatus.DEGRADED:
            bad = [n for n in drifting_names if n in _NEGATIVE_METRICS]
            summary = (
                f"Agent performance is degrading — {', '.join(bad)} "
                f"increased significantly compared to baseline."
            )
        else:
            summary = (
                f"Significant behavioral drift in {len(drifting_names)} metric(s). "
                f"Review agent configuration and inputs."
            )

        return DriftReport(
            drift_score=drift_score,
            status=status,
            baseline_sessions=len(baseline),
            current_sessions=len(current),
            metric_drifts=metric_drifts,
            tool_drifts=tool_drifts,
            drifting_metrics=drifting_names,
            summary=summary,
        )
