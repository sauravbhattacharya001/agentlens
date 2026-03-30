"""Anomaly Detection for AgentLens.

Statistical anomaly detector that identifies unusual patterns in agent
session behavior.  Works in two phases:

1. **Baseline** – feed historical sessions / metrics to compute statistical
   baselines (mean, standard deviation).
2. **Detect** – check new sessions against baselines to flag anomalies
   using Z-score analysis.

Pure Python, stdlib only (math, dataclasses, enum).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AnomalyKind(Enum):
    """Type of detected anomaly."""
    LATENCY_SPIKE = "latency_spike"
    TOKEN_SURGE = "token_surge"
    ERROR_BURST = "error_burst"
    EVENT_FLOOD = "event_flood"
    EVENT_DROUGHT = "event_drought"
    TOOL_FAILURE_SPIKE = "tool_failure_spike"


class AnomalySeverity(Enum):
    """Severity of an anomaly based on how many standard deviations from the mean."""
    WARNING = "warning"    # 2-3 sigma
    CRITICAL = "critical"  # 3+ sigma

    @property
    def label(self) -> str:
        """Human-readable severity label (e.g. 'Warning', 'Critical')."""
        return self.value.capitalize()


@dataclass
class Anomaly:
    """A single detected anomaly."""
    kind: AnomalyKind
    severity: AnomalySeverity
    metric_name: str       # e.g., "avg_latency_ms", "error_rate"
    observed: float        # the actual value
    expected: float        # the baseline mean
    std_dev: float         # baseline standard deviation
    z_score: float         # how many std devs from mean
    description: str       # human-readable description

    def to_dict(self) -> dict[str, Any]:
        """Serialize the anomaly to a JSON-friendly dictionary.

        Returns:
            Dict with keys: kind, severity, metric_name, observed, expected,
            std_dev, z_score, description. Numeric values are rounded for
            clean serialization.
        """
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "metric_name": self.metric_name,
            "observed": round(self.observed, 4),
            "expected": round(self.expected, 4),
            "std_dev": round(self.std_dev, 4),
            "z_score": round(self.z_score, 2),
            "description": self.description,
        }


@dataclass
class MetricBaseline:
    """Statistical baseline for a single metric."""
    name: str
    mean: float
    std_dev: float
    min_val: float
    max_val: float
    sample_count: int

    @property
    def coefficient_of_variation(self) -> float:
        """Coefficient of variation (CV = std_dev / |mean|).

        Higher values indicate more variable data. Returns 0.0 if mean is zero.
        """
        if self.mean == 0:
            return 0.0
        return self.std_dev / abs(self.mean)

    def z_score(self, value: float) -> float:
        """Calculate how many standard deviations ``value`` is from the mean.

        Args:
            value: The observed metric value.

        Returns:
            Z-score as a float. Positive means above mean, negative means below.
            Returns 0.0 if value equals mean with zero std_dev, or inf otherwise.
        """
        if self.std_dev == 0:
            return 0.0 if value == self.mean else float('inf')
        return (value - self.mean) / self.std_dev

    def to_dict(self) -> dict[str, Any]:
        """Serialize the baseline to a JSON-friendly dictionary.

        Returns:
            Dict with keys: name, mean, std_dev, min, max, sample_count, cv.
        """
        return {
            "name": self.name,
            "mean": round(self.mean, 4),
            "std_dev": round(self.std_dev, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
            "sample_count": self.sample_count,
            "cv": round(self.coefficient_of_variation, 4),
        }


@dataclass
class AnomalyReport:
    """Results of anomaly detection on a session."""
    session_id: str
    anomalies: list[Anomaly] = field(default_factory=list)
    baselines_used: dict[str, MetricBaseline] = field(default_factory=dict)

    @property
    def anomaly_count(self) -> int:
        """Total number of anomalies detected."""
        return len(self.anomalies)

    @property
    def has_anomalies(self) -> bool:
        """Whether any anomalies were detected."""
        return len(self.anomalies) > 0

    @property
    def max_severity(self) -> AnomalySeverity | None:
        """Highest severity among detected anomalies, or None if clean."""
        if not self.anomalies:
            return None
        if any(a.severity == AnomalySeverity.CRITICAL for a in self.anomalies):
            return AnomalySeverity.CRITICAL
        return AnomalySeverity.WARNING

    @property
    def by_kind(self) -> dict[AnomalyKind, list[Anomaly]]:
        """Group anomalies by their kind (e.g. LATENCY_SPIKE, TOKEN_SURGE)."""
        result: dict[AnomalyKind, list[Anomaly]] = {}
        for a in self.anomalies:
            result.setdefault(a.kind, []).append(a)
        return result

    @property
    def by_severity(self) -> dict[AnomalySeverity, list[Anomaly]]:
        """Group anomalies by severity level (WARNING or CRITICAL)."""
        result: dict[AnomalySeverity, list[Anomaly]] = {}
        for a in self.anomalies:
            result.setdefault(a.severity, []).append(a)
        return result

    @property
    def critical_count(self) -> int:
        """Number of CRITICAL-severity anomalies."""
        return sum(1 for a in self.anomalies if a.severity == AnomalySeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        """Number of WARNING-severity anomalies."""
        return sum(1 for a in self.anomalies if a.severity == AnomalySeverity.WARNING)

    @property
    def summary(self) -> str:
        """One-line human-readable summary of the anomaly report.

        Examples:
            'Session abc123: no anomalies detected.'
            'Session abc123: 2 anomalie(s) detected 1 critical 1 warning — error_burst, latency_spike.'
        """
        if not self.anomalies:
            return f"Session {self.session_id}: no anomalies detected."
        parts = [f"Session {self.session_id}: {self.anomaly_count} anomalie(s) detected"]
        if self.critical_count:
            parts.append(f"{self.critical_count} critical")
        if self.warning_count:
            parts.append(f"{self.warning_count} warning")
        parts.append("—")
        kinds = [a.kind.value for a in self.anomalies]
        parts.append(", ".join(sorted(set(kinds))))
        return " ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full report to a JSON-friendly dictionary.

        Returns:
            Dict with session_id, anomaly_count, has_anomalies, max_severity,
            critical_count, warning_count, anomalies list, and summary.
        """
        return {
            "session_id": self.session_id,
            "anomaly_count": self.anomaly_count,
            "has_anomalies": self.has_anomalies,
            "max_severity": self.max_severity.value if self.max_severity else None,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "summary": self.summary,
        }


@dataclass
class AnomalyDetectorConfig:
    """Configuration for the anomaly detector.

    Attributes:
        warning_threshold: Z-score threshold for WARNING severity (default: 2.0σ).
        critical_threshold: Z-score threshold for CRITICAL severity (default: 3.0σ).
        min_samples: Minimum number of baseline samples required before analysis
            can run (default: 3).
        check_latency: Enable latency spike detection.
        check_tokens: Enable token surge detection.
        check_errors: Enable error burst detection.
        check_event_count: Enable event flood/drought detection.
        check_tool_failures: Enable tool failure spike detection.
    """
    warning_threshold: float = 2.0    # Z-score for warning
    critical_threshold: float = 3.0   # Z-score for critical
    min_samples: int = 3              # Minimum baseline samples needed
    check_latency: bool = True
    check_tokens: bool = True
    check_errors: bool = True
    check_event_count: bool = True
    check_tool_failures: bool = True


class AnomalyDetector:
    """Statistical anomaly detector for agent sessions.

    Build a baseline from historical session metrics, then check new sessions
    for statistically significant deviations.

    Usage::

        detector = AnomalyDetector()

        # Feed historical data
        for session in historical_sessions:
            detector.add_sample(detector.extract_metrics(session))

        # Or feed raw metric dicts
        detector.add_sample({"avg_latency_ms": 150, "error_rate": 0.02})

        # Check a new session
        report = detector.analyze(new_session)
        if report.has_anomalies:
            print(report.summary)
    """

    # Metric names used for anomaly detection
    METRIC_AVG_LATENCY = "avg_latency_ms"
    METRIC_P95_LATENCY = "p95_latency_ms"
    METRIC_TOTAL_TOKENS = "total_tokens"
    METRIC_TOKENS_PER_EVENT = "tokens_per_event"
    METRIC_ERROR_RATE = "error_rate"
    METRIC_EVENT_COUNT = "event_count"
    METRIC_TOOL_FAILURE_RATE = "tool_failure_rate"

    def __init__(self, config: AnomalyDetectorConfig | None = None):
        self.config = config or AnomalyDetectorConfig()
        self._samples: dict[str, list[float]] = {}  # metric_name -> list of values
        self._baseline_cache: dict[str, MetricBaseline] = {}
        self._sample_lengths: dict[str, int] = {}  # track lengths for cache invalidation

    @property
    def sample_count(self) -> int:
        """Number of samples added."""
        if not self._samples:
            return 0
        return max(len(v) for v in self._samples.values())

    @property
    def has_baseline(self) -> bool:
        """Whether enough samples exist for analysis."""
        return self.sample_count >= self.config.min_samples

    @property
    def metric_names(self) -> list[str]:
        """Names of metrics being tracked."""
        return sorted(self._samples.keys())

    def add_sample(self, metrics: dict[str, float]) -> None:
        """Add a session's metrics to the baseline.

        Args:
            metrics: dict mapping metric names to float values.
                     Unknown metrics are accepted (extensible).

        Invalidates cached baselines for any metric that receives new data.
        """
        for name, value in metrics.items():
            if not isinstance(value, (int, float)):
                continue
            self._samples.setdefault(name, []).append(float(value))
            # Invalidate cache for this metric (length changed)
            self._baseline_cache.pop(name, None)

    def add_session(self, session) -> None:
        """Extract metrics from a Session object and add to baseline.

        Args:
            session: An agentlens.models.Session object.
        """
        metrics = self.extract_metrics(session)
        self.add_sample(metrics)

    def get_baseline(self, metric_name: str) -> MetricBaseline | None:
        """Get the statistical baseline for a specific metric.

        Uses a cache keyed by metric name, invalidated when new samples are
        added via ``add_sample``. This avoids recomputing mean/std_dev on
        every call — significant when analyzing many sessions against the
        same baseline.
        """
        values = self._samples.get(metric_name)
        if not values or len(values) < self.config.min_samples:
            return None
        cached = self._baseline_cache.get(metric_name)
        if cached is not None:
            return cached
        baseline = self._compute_baseline(metric_name, values)
        self._baseline_cache[metric_name] = baseline
        return baseline

    def get_all_baselines(self) -> dict[str, MetricBaseline]:
        """Get baselines for all tracked metrics."""
        result = {}
        for name in self._samples:
            baseline = self.get_baseline(name)
            if baseline is not None:
                result[name] = baseline
        return result

    def analyze(self, session) -> AnomalyReport:
        """Analyze a session for anomalies against the baseline.

        Args:
            session: An agentlens.models.Session object.

        Returns:
            AnomalyReport with any detected anomalies.

        Raises:
            ValueError: If insufficient baseline data.
        """
        if not self.has_baseline:
            raise ValueError(
                f"Need at least {self.config.min_samples} samples for baseline, "
                f"have {self.sample_count}"
            )
        metrics = self.extract_metrics(session)
        return self.analyze_metrics(metrics, session_id=session.session_id)

    def analyze_metrics(
        self,
        metrics: dict[str, float],
        session_id: str = "unknown",
    ) -> AnomalyReport:
        """Analyze raw metrics against the baseline.

        Args:
            metrics: dict of metric name -> value
            session_id: identifier for the report

        Returns:
            AnomalyReport with detected anomalies.
        """
        if not self.has_baseline:
            raise ValueError(
                f"Need at least {self.config.min_samples} samples for baseline, "
                f"have {self.sample_count}"
            )

        anomalies: list[Anomaly] = []
        baselines_used: dict[str, MetricBaseline] = {}

        for metric_name, value in metrics.items():
            baseline = self.get_baseline(metric_name)
            if baseline is None:
                continue
            baselines_used[metric_name] = baseline

            z = baseline.z_score(value)
            abs_z = abs(z)

            if abs_z < self.config.warning_threshold:
                continue

            severity = (
                AnomalySeverity.CRITICAL
                if abs_z >= self.config.critical_threshold
                else AnomalySeverity.WARNING
            )

            kind = self._classify_anomaly(metric_name, z)
            if kind is None:
                continue

            # Skip if this kind of check is disabled
            if not self._is_check_enabled(kind):
                continue

            description = self._describe_anomaly(
                kind, severity, metric_name, value, baseline.mean, z,
            )

            anomalies.append(Anomaly(
                kind=kind,
                severity=severity,
                metric_name=metric_name,
                observed=value,
                expected=baseline.mean,
                std_dev=baseline.std_dev,
                z_score=z,
                description=description,
            ))

        return AnomalyReport(
            session_id=session_id,
            anomalies=anomalies,
            baselines_used=baselines_used,
        )

    def reset(self) -> None:
        """Clear all baseline data."""
        self._samples.clear()
        self._baseline_cache.clear()

    @staticmethod
    def extract_metrics(session) -> dict[str, float]:
        """Extract anomaly-detection metrics from a Session object.

        Metrics extracted:
        - avg_latency_ms: average event duration
        - p95_latency_ms: 95th percentile event duration
        - total_tokens: total tokens (in + out)
        - tokens_per_event: average tokens per event
        - error_rate: fraction of error events
        - event_count: number of events
        - tool_failure_rate: fraction of tool events that are errors
        """
        events = session.events if hasattr(session, "events") else []
        event_count = len(events)

        metrics: dict[str, float] = {
            "event_count": float(event_count),
        }

        if event_count == 0:
            metrics["avg_latency_ms"] = 0.0
            metrics["p95_latency_ms"] = 0.0
            metrics["total_tokens"] = 0.0
            metrics["tokens_per_event"] = 0.0
            metrics["error_rate"] = 0.0
            metrics["tool_failure_rate"] = 0.0
            return metrics

        # Latency
        durations = [
            e.duration_ms
            for e in events
            if hasattr(e, "duration_ms") and e.duration_ms is not None
        ]
        if durations:
            metrics["avg_latency_ms"] = sum(durations) / len(durations)
            sorted_d = sorted(durations)
            p95_idx = min(int(len(sorted_d) * 0.95), len(sorted_d) - 1)
            metrics["p95_latency_ms"] = sorted_d[p95_idx]
        else:
            metrics["avg_latency_ms"] = 0.0
            metrics["p95_latency_ms"] = 0.0

        # Tokens
        total_tokens = sum(
            (getattr(e, "tokens_in", 0) or 0) + (getattr(e, "tokens_out", 0) or 0)
            for e in events
        )
        metrics["total_tokens"] = float(total_tokens)
        metrics["tokens_per_event"] = total_tokens / event_count

        # Errors
        error_count = sum(
            1
            for e in events
            if hasattr(e, "event_type")
            and "error" in (e.event_type or "").lower()
        )
        metrics["error_rate"] = error_count / event_count

        # Tool failures
        tool_events = [
            e
            for e in events
            if hasattr(e, "event_type")
            and "tool" in (e.event_type or "").lower()
        ]
        if tool_events:
            tool_errors = sum(
                1 for e in tool_events if "error" in (e.event_type or "").lower()
            )
            metrics["tool_failure_rate"] = tool_errors / len(tool_events)
        else:
            metrics["tool_failure_rate"] = 0.0

        return metrics

    # ── Private helpers ──

    @staticmethod
    def _compute_baseline(name: str, values: list[float]) -> MetricBaseline:
        """Compute statistical baseline from a list of values."""
        n = len(values)
        mean = sum(values) / n
        # Sample variance (Bessel's correction) — the observations are a sample
        # from the ongoing stream of sessions, not the complete population.
        # Using n instead of n-1 underestimates the true std dev, inflating
        # z-scores and causing false-positive anomaly detections.
        variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
        std_dev = math.sqrt(variance)
        return MetricBaseline(
            name=name,
            mean=mean,
            std_dev=std_dev,
            min_val=min(values),
            max_val=max(values),
            sample_count=n,
        )

    @staticmethod
    def _classify_anomaly(metric_name: str, z_score: float) -> AnomalyKind | None:
        """Map metric + direction to an anomaly kind."""
        mapping = {
            "avg_latency_ms": (AnomalyKind.LATENCY_SPIKE, None),
            "p95_latency_ms": (AnomalyKind.LATENCY_SPIKE, None),
            "total_tokens": (AnomalyKind.TOKEN_SURGE, None),
            "tokens_per_event": (AnomalyKind.TOKEN_SURGE, None),
            "error_rate": (AnomalyKind.ERROR_BURST, None),
            "event_count": (AnomalyKind.EVENT_FLOOD, AnomalyKind.EVENT_DROUGHT),
            "tool_failure_rate": (AnomalyKind.TOOL_FAILURE_SPIKE, None),
        }
        entry = mapping.get(metric_name)
        if entry is None:
            return None
        high_kind, low_kind = entry
        if z_score > 0:
            return high_kind
        elif low_kind is not None:
            return low_kind
        return None  # negative z-score for metrics where low isn't anomalous

    def _is_check_enabled(self, kind: AnomalyKind) -> bool:
        """Check if this anomaly kind's check is enabled in config."""
        check_map = {
            AnomalyKind.LATENCY_SPIKE: self.config.check_latency,
            AnomalyKind.TOKEN_SURGE: self.config.check_tokens,
            AnomalyKind.ERROR_BURST: self.config.check_errors,
            AnomalyKind.EVENT_FLOOD: self.config.check_event_count,
            AnomalyKind.EVENT_DROUGHT: self.config.check_event_count,
            AnomalyKind.TOOL_FAILURE_SPIKE: self.config.check_tool_failures,
        }
        return check_map.get(kind, True)

    @staticmethod
    def _describe_anomaly(
        kind: AnomalyKind,
        severity: AnomalySeverity,
        metric_name: str,
        observed: float,
        expected: float,
        z_score: float,
    ) -> str:
        """Generate human-readable anomaly description."""
        direction = "above" if z_score > 0 else "below"
        descriptions = {
            AnomalyKind.LATENCY_SPIKE: (
                f"Latency spike: {metric_name} is {observed:.1f}ms "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.1f}ms)"
            ),
            AnomalyKind.TOKEN_SURGE: (
                f"Token surge: {metric_name} is {observed:.0f} "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.0f})"
            ),
            AnomalyKind.ERROR_BURST: (
                f"Error burst: error rate is {observed:.1%} "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.1%})"
            ),
            AnomalyKind.EVENT_FLOOD: (
                f"Event flood: {int(observed)} events "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.0f})"
            ),
            AnomalyKind.EVENT_DROUGHT: (
                f"Event drought: only {int(observed)} events "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.0f})"
            ),
            AnomalyKind.TOOL_FAILURE_SPIKE: (
                f"Tool failure spike: failure rate is {observed:.1%} "
                f"({abs(z_score):.1f}\u03c3 {direction} baseline mean of {expected:.1%})"
            ),
        }
        return descriptions.get(
            kind,
            f"Anomaly in {metric_name}: {observed} "
            f"({abs(z_score):.1f}\u03c3 {direction} mean {expected})",
        )
