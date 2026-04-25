"""CapacityPlanner — fleet capacity planning for AI agent deployments.

Provides workload projection, resource sizing, scaling recommendations,
and bottleneck detection based on historical session/usage data.

Usage:
    from agentlens import CapacityPlanner, WorkloadSample

    planner = CapacityPlanner()

    # Feed historical workload samples
    planner.add_sample(WorkloadSample(
        timestamp=datetime.now() - timedelta(hours=2),
        active_sessions=50, requests_per_minute=120,
        avg_latency_ms=450, token_throughput=8000,
        error_rate=0.02, cpu_utilization=0.65, memory_utilization=0.55
    ))
    # ... add more samples ...

    # Project future workload
    projection = planner.project_workload(horizon_hours=72)

    # Get resource sizing for a target workload
    sizing = planner.size_resources(target_rpm=500, target_latency_ms=300)

    # Detect bottlenecks
    bottlenecks = planner.detect_bottlenecks()

    # Full capacity report
    report = planner.report()
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ResourceKind(Enum):
    """Type of infrastructure resource."""
    COMPUTE = "compute"
    MEMORY = "memory"
    TOKEN_BUDGET = "token_budget"
    API_RATE = "api_rate"
    CONCURRENCY = "concurrency"


class ScalingAction(Enum):
    """Recommended scaling action."""
    NONE = "none"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    SCALE_OUT = "scale_out"
    OPTIMIZE = "optimize"
    URGENT = "urgent"


class BottleneckSeverity(Enum):
    """How critical a bottleneck is."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TrendDirection(Enum):
    """Direction of a metric trend."""
    RISING = "rising"
    STABLE = "stable"
    FALLING = "falling"


@dataclass
class WorkloadSample:
    """A snapshot of workload metrics at a point in time."""
    timestamp: datetime
    active_sessions: int = 0
    requests_per_minute: float = 0.0
    avg_latency_ms: float = 0.0
    token_throughput: float = 0.0
    error_rate: float = 0.0
    cpu_utilization: float = 0.0
    memory_utilization: float = 0.0

    def __post_init__(self) -> None:
        if self.active_sessions < 0:
            raise ValueError("active_sessions must be >= 0")
        if self.requests_per_minute < 0:
            raise ValueError("requests_per_minute must be >= 0")
        if self.avg_latency_ms < 0:
            raise ValueError("avg_latency_ms must be >= 0")
        if not (0.0 <= self.error_rate <= 1.0):
            raise ValueError("error_rate must be between 0 and 1")
        if not (0.0 <= self.cpu_utilization <= 1.0):
            raise ValueError("cpu_utilization must be between 0 and 1")
        if not (0.0 <= self.memory_utilization <= 1.0):
            raise ValueError("memory_utilization must be between 0 and 1")


@dataclass
class WorkloadProjection:
    """Projected workload at a future time point."""
    timestamp: datetime
    projected_rpm: float
    projected_sessions: float
    projected_tokens: float
    confidence: float  # 0-1
    trend: TrendDirection


@dataclass
class Bottleneck:
    """A detected capacity bottleneck."""
    resource: ResourceKind
    severity: BottleneckSeverity
    current_utilization: float
    projected_saturation_hours: Optional[float]
    description: str
    recommendation: str


@dataclass
class ResourceSizing:
    """Recommended resource allocation for a target workload."""
    target_rpm: float
    target_latency_ms: float
    recommended_instances: int
    estimated_cpu_per_instance: float
    estimated_memory_per_instance: float
    estimated_monthly_tokens: float
    headroom_factor: float
    notes: List[str] = field(default_factory=list)


@dataclass
class ScalingRecommendation:
    """A scaling recommendation with rationale."""
    action: ScalingAction
    resource: ResourceKind
    urgency_hours: Optional[float]
    rationale: str
    estimated_impact: str


@dataclass
class CapacityReport:
    """Full capacity planning report."""
    generated_at: datetime
    sample_count: int
    observation_window_hours: float
    current_utilization: Dict[str, float]
    peak_utilization: Dict[str, float]
    trends: Dict[str, TrendDirection]
    projections: List[WorkloadProjection]
    bottlenecks: List[Bottleneck]
    scaling_recommendations: List[ScalingRecommendation]
    headroom_score: float  # 0-100, higher = more headroom
    summary: str

    def to_dict(self) -> dict:
        """Serialize to a plain dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "sample_count": self.sample_count,
            "observation_window_hours": round(self.observation_window_hours, 1),
            "current_utilization": {k: round(v, 3) for k, v in self.current_utilization.items()},
            "peak_utilization": {k: round(v, 3) for k, v in self.peak_utilization.items()},
            "trends": {k: v.value for k, v in self.trends.items()},
            "projections_count": len(self.projections),
            "bottleneck_count": len(self.bottlenecks),
            "bottlenecks": [
                {"resource": b.resource.value, "severity": b.severity.value, "description": b.description}
                for b in self.bottlenecks
            ],
            "scaling_recommendations": [
                {"action": r.action.value, "resource": r.resource.value, "rationale": r.rationale}
                for r in self.scaling_recommendations
            ],
            "headroom_score": round(self.headroom_score, 1),
            "summary": self.summary,
        }


class CapacityPlanner:
    """Fleet capacity planning engine.

    Analyzes historical workload samples to project future demand,
    detect bottlenecks, and recommend scaling actions.

    Args:
        max_cpu_threshold: CPU utilization threshold for warnings (default 0.80).
        max_memory_threshold: Memory utilization threshold (default 0.85).
        max_error_threshold: Error rate threshold (default 0.05).
        headroom_factor: Safety margin multiplier for sizing (default 1.3).
        max_samples: Maximum samples to retain (default 10000).
    """

    def __init__(
        self,
        max_cpu_threshold: float = 0.80,
        max_memory_threshold: float = 0.85,
        max_error_threshold: float = 0.05,
        headroom_factor: float = 1.3,
        max_samples: int = 10000,
    ) -> None:
        if max_error_threshold <= 0:
            raise ValueError(
                "max_error_threshold must be positive, got %s" % max_error_threshold
            )
        if max_cpu_threshold <= 0:
            raise ValueError(
                "max_cpu_threshold must be positive, got %s" % max_cpu_threshold
            )
        if max_memory_threshold <= 0:
            raise ValueError(
                "max_memory_threshold must be positive, got %s" % max_memory_threshold
            )
        if max_samples < 1:
            raise ValueError(
                "max_samples must be at least 1, got %s" % max_samples
            )
        self._samples: List[WorkloadSample] = []
        self.max_cpu_threshold = max_cpu_threshold
        self.max_memory_threshold = max_memory_threshold
        self.max_error_threshold = max_error_threshold
        self.headroom_factor = headroom_factor
        self.max_samples = max_samples

    @property
    def sample_count(self) -> int:
        """Number of stored samples."""
        return len(self._samples)

    def add_sample(self, sample: WorkloadSample) -> None:
        """Record a workload sample."""
        self._samples.append(sample)
        if len(self._samples) > self.max_samples:
            self._samples = self._samples[-self.max_samples:]

    def add_samples(self, samples: List[WorkloadSample]) -> None:
        """Record multiple workload samples."""
        for s in samples:
            self.add_sample(s)

    def clear(self) -> None:
        """Remove all samples."""
        self._samples.clear()
        self._sorted_cache = None
        self._sorted_cache_key = -1
        self._ts_cache = None
        self._ts_cache_key = -1

    def _sorted_samples(self) -> List[WorkloadSample]:
        """Return samples sorted by timestamp, with caching.

        The cache is invalidated whenever the sample list changes length
        (i.e. new samples are added or clear() is called).
        """
        cache_key = len(self._samples)
        if (
            hasattr(self, "_sorted_cache")
            and self._sorted_cache_key == cache_key
            and self._sorted_cache is not None
        ):
            return self._sorted_cache
        result = sorted(self._samples, key=lambda s: s.timestamp)
        self._sorted_cache = result
        self._sorted_cache_key = cache_key
        return result

    def _observation_hours(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        ss = self._sorted_samples()
        return (ss[-1].timestamp - ss[0].timestamp).total_seconds() / 3600

    def _compute_trend(self, values: List[float]) -> Tuple[TrendDirection, float]:
        """Single-pass linear regression slope and trend direction.

        Since x = 0..n-1 (evenly spaced integers), x_mean = (n-1)/2 and
        Σ(x - x_mean)² = n(n²-1)/12 (closed form).  This lets us compute
        slope in one pass over values instead of three.
        """
        n = len(values)
        if n < 2:
            return TrendDirection.STABLE, 0.0
        x_mean = (n - 1) * 0.5
        # Single pass: accumulate y_sum and Σ(x - x_mean)(y - y_mean)
        # Note: Σ(x - x_mean)(y - y_mean) = Σ x·y - n·x_mean·y_mean
        #       = Σ i·values[i] - n·x_mean·y_mean
        y_sum = 0.0
        xy_sum = 0.0
        for i in range(n):
            v = values[i]
            y_sum += v
            xy_sum += i * v
        y_mean = y_sum / n
        num = xy_sum - n * x_mean * y_mean
        den = n * (n * n - 1) / 12.0  # closed-form Σ(x - x_mean)²
        if den == 0:
            return TrendDirection.STABLE, 0.0
        slope = num / den
        # Normalize slope relative to mean
        rel_slope = slope / max(abs(y_mean), 1e-9)
        if rel_slope > 0.01:
            return TrendDirection.RISING, slope
        elif rel_slope < -0.01:
            return TrendDirection.FALLING, slope
        return TrendDirection.STABLE, slope

    def _recent_samples(self, hours: float = 1.0) -> List[WorkloadSample]:
        """Get samples from the most recent N hours.

        Uses binary search (bisect) on the sorted timestamp list to avoid
        scanning all samples — O(log n) lookup instead of O(n).
        """
        if not self._samples:
            return []
        ss = self._sorted_samples()
        cutoff = ss[-1].timestamp - timedelta(hours=hours)
        # Build a list of timestamps for bisect (only when cache changes)
        timestamps = self._sorted_timestamps()
        idx = bisect.bisect_left(timestamps, cutoff)
        return ss[idx:]

    def _sorted_timestamps(self) -> List[datetime]:
        """Return cached list of timestamps aligned with _sorted_samples().

        Invalidated alongside the sorted-samples cache so the two stay
        in sync.
        """
        cache_key = len(self._samples)
        if (
            hasattr(self, "_ts_cache")
            and self._ts_cache_key == cache_key
            and self._ts_cache is not None
        ):
            return self._ts_cache
        ss = self._sorted_samples()
        self._ts_cache = [s.timestamp for s in ss]
        self._ts_cache_key = cache_key
        return self._ts_cache

    def current_utilization(self) -> Dict[str, float]:
        """Average utilization from the most recent samples."""
        recent = self._recent_samples(1.0) or self._samples[-5:]
        if not recent:
            return {"cpu": 0, "memory": 0, "error_rate": 0, "rpm": 0, "sessions": 0}
        return {
            "cpu": statistics.mean(s.cpu_utilization for s in recent),
            "memory": statistics.mean(s.memory_utilization for s in recent),
            "error_rate": statistics.mean(s.error_rate for s in recent),
            "rpm": statistics.mean(s.requests_per_minute for s in recent),
            "sessions": statistics.mean(s.active_sessions for s in recent),
        }

    def peak_utilization(self) -> Dict[str, float]:
        """Peak utilization across all samples.

        Single-pass scan instead of 5 separate generator expressions,
        reducing from O(5n) to O(n).
        """
        if not self._samples:
            return {"cpu": 0, "memory": 0, "error_rate": 0, "rpm": 0, "sessions": 0}
        cpu = mem = err = rpm = ses = 0.0
        for s in self._samples:
            if s.cpu_utilization > cpu:
                cpu = s.cpu_utilization
            if s.memory_utilization > mem:
                mem = s.memory_utilization
            if s.error_rate > err:
                err = s.error_rate
            if s.requests_per_minute > rpm:
                rpm = s.requests_per_minute
            if s.active_sessions > ses:
                ses = s.active_sessions
        return {
            "cpu": cpu,
            "memory": mem,
            "error_rate": err,
            "rpm": rpm,
            "sessions": ses,
        }

    def _compute_all_trends(
        self,
    ) -> Dict[str, Tuple[TrendDirection, float, List[float]]]:
        """Single-pass trend computation for all metrics.

        Iterates over sorted samples once to extract all metric vectors,
        then runs linear regression on each. Returns a dict mapping metric
        name to (direction, slope, values). The values list is cached for
        callers that need the raw data (e.g. project_workload).

        This replaces 6+ separate list comprehensions that each scanned
        the full sample list independently.
        """
        _ALL_KEYS = ("cpu", "memory", "rpm", "sessions", "error_rate", "latency")
        ss = self._sorted_samples()
        if len(ss) < 2:
            return {
                k: (TrendDirection.STABLE, 0.0, [])
                for k in _ALL_KEYS
            }
        # Single pass: extract all metric vectors simultaneously
        cpu_v: List[float] = []
        mem_v: List[float] = []
        rpm_v: List[float] = []
        ses_v: List[float] = []
        err_v: List[float] = []
        lat_v: List[float] = []
        for s in ss:
            cpu_v.append(s.cpu_utilization)
            mem_v.append(s.memory_utilization)
            rpm_v.append(s.requests_per_minute)
            ses_v.append(float(s.active_sessions))
            err_v.append(s.error_rate)
            lat_v.append(s.avg_latency_ms)
        vectors = {
            "cpu": cpu_v, "memory": mem_v, "rpm": rpm_v,
            "sessions": ses_v, "error_rate": err_v, "latency": lat_v,
        }
        result: Dict[str, Tuple[TrendDirection, float, List[float]]] = {}
        for key in _ALL_KEYS:
            vals = vectors[key]
            direction, slope = self._compute_trend(vals)
            result[key] = (direction, slope, vals)
        return result

    def compute_trends(self) -> Dict[str, TrendDirection]:
        """Compute trends for key metrics."""
        all_trends = self._compute_all_trends()
        return {k: v[0] for k, v in all_trends.items()}

    def project_workload(
        self,
        horizon_hours: float = 24,
        steps: int = 6,
        _all_trends: Optional[Dict[str, tuple]] = None,
    ) -> List[WorkloadProjection]:
        """Project workload metrics into the future.

        Uses linear trend extrapolation with decaying confidence.
        Reuses pre-computed trend data from _compute_all_trends() to
        avoid redundant list extraction and regression calls.

        Args:
            horizon_hours: How far ahead to project.
            steps: Number of projection points.
            _all_trends: Pre-computed output of _compute_all_trends().
                When None the method computes it internally.  Passing
                it in from :meth:`report` avoids a redundant O(n) scan.
        """
        ss = self._sorted_samples()
        if len(ss) < 2:
            return []

        now = ss[-1].timestamp
        obs_hours = self._observation_hours()

        # Reuse trend data; rpm/sessions are already computed.
        all_trends = _all_trends or self._compute_all_trends()
        rpm_vals = all_trends["rpm"][2]
        ses_vals = all_trends["sessions"][2]
        rpm_slope = all_trends["rpm"][1]
        ses_slope = all_trends["sessions"][1]
        rpm_trend = all_trends["rpm"][0]

        # Token throughput is not in the standard trend set; extract once.
        tok_vals = [s.token_throughput for s in ss]
        _, tok_slope = self._compute_trend(tok_vals)

        cur_rpm = statistics.mean(rpm_vals[-3:]) if len(rpm_vals) >= 3 else rpm_vals[-1]
        cur_ses = statistics.mean(ses_vals[-3:]) if len(ses_vals) >= 3 else ses_vals[-1]
        cur_tok = statistics.mean(tok_vals[-3:]) if len(tok_vals) >= 3 else tok_vals[-1]

        samples_per_hour = len(ss) / max(obs_hours, 1)

        projections: List[WorkloadProjection] = []
        for i in range(1, steps + 1):
            t = horizon_hours * i / steps
            rpm_proj = max(0, cur_rpm + rpm_slope * samples_per_hour * t)
            ses_proj = max(0, cur_ses + ses_slope * samples_per_hour * t)
            tok_proj = max(0, cur_tok + tok_slope * samples_per_hour * t)
            confidence = max(0.1, 1.0 - (t / horizon_hours) * 0.7)

            projections.append(WorkloadProjection(
                timestamp=now + timedelta(hours=t),
                projected_rpm=round(rpm_proj, 1),
                projected_sessions=round(ses_proj, 1),
                projected_tokens=round(tok_proj, 1),
                confidence=round(confidence, 2),
                trend=rpm_trend,
            ))
        return projections

    def detect_bottlenecks(self) -> List[Bottleneck]:
        """Identify current and emerging capacity bottlenecks."""
        if not self._samples:
            return []
        cur = self.current_utilization()
        all_trends = self._compute_all_trends()
        trends = {k: v[0] for k, v in all_trends.items()}
        return self._detect_bottlenecks_with(cur, trends, _all_trends=all_trends)

    def size_resources(
        self,
        target_rpm: float,
        target_latency_ms: float = 500,
        max_rpm_per_instance: float = 100,
    ) -> ResourceSizing:
        """Calculate resource requirements for a target workload.

        Estimates instance count, CPU, memory, and token budget needed.
        """
        # Base instance count from RPM
        raw_instances = target_rpm / max(max_rpm_per_instance, 1)
        recommended = max(1, math.ceil(raw_instances * self.headroom_factor))

        # Estimate CPU per instance from current data
        cur = self.current_utilization() if self._samples else {}
        cur_rpm = cur.get("rpm", target_rpm)
        cur_cpu = cur.get("cpu", 0.5)
        # Linear estimate: cpu_per_rpm * target_rpm_per_instance
        if cur_rpm > 0:
            cpu_per_rpm = cur_cpu / cur_rpm
            est_cpu = min(1.0, cpu_per_rpm * (target_rpm / recommended))
        else:
            est_cpu = 0.5

        cur_mem = cur.get("memory", 0.5)
        est_mem = min(0.95, cur_mem * self.headroom_factor)

        # Token throughput estimate
        if self._samples:
            avg_tokens_per_request = statistics.mean(
                s.token_throughput / max(s.requests_per_minute, 1) for s in self._samples
            )
        else:
            avg_tokens_per_request = 50.0

        monthly_tokens = target_rpm * 60 * 24 * 30 * avg_tokens_per_request

        notes: List[str] = []
        if recommended > 5:
            notes.append("Consider auto-scaling groups for this instance count")
        if est_cpu > 0.7:
            notes.append("Per-instance CPU is high; consider larger instance types")
        if target_latency_ms < 200:
            notes.append("Low latency target may require GPU acceleration")

        return ResourceSizing(
            target_rpm=target_rpm,
            target_latency_ms=target_latency_ms,
            recommended_instances=recommended,
            estimated_cpu_per_instance=round(est_cpu, 3),
            estimated_memory_per_instance=round(est_mem, 3),
            estimated_monthly_tokens=round(monthly_tokens),
            headroom_factor=self.headroom_factor,
            notes=notes,
        )

    def scaling_recommendations(self) -> List[ScalingRecommendation]:
        """Generate scaling recommendations based on current state."""
        if not self._samples:
            return []
        cur = self.current_utilization()
        trends = self.compute_trends()
        bottlenecks = self._detect_bottlenecks_with(cur, trends)
        return self._scaling_recommendations_with(cur, trends, bottlenecks)

    def headroom_score(self) -> float:
        """Calculate overall headroom score (0-100).

        Higher = more capacity headroom available.
        """
        if not self._samples:
            return 100.0
        return self._headroom_score_with(self.current_utilization())

    def report(self) -> CapacityReport:
        """Generate a comprehensive capacity planning report.

        Computes _compute_all_trends() once and threads the result
        through projections and bottleneck detection, eliminating
        three redundant O(n) sample scans.
        """
        now = datetime.now(timezone.utc)
        cur = self.current_utilization()
        peak = self.peak_utilization()
        all_trends = self._compute_all_trends()
        trends = {k: v[0] for k, v in all_trends.items()}
        projections = self.project_workload(_all_trends=all_trends)

        bottlenecks = self._detect_bottlenecks_with(cur, trends, _all_trends=all_trends)
        recs = self._scaling_recommendations_with(cur, trends, bottlenecks)
        score = self._headroom_score_with(cur)

        # Generate summary
        if score >= 80:
            summary = f"Healthy capacity with {score:.0f}% headroom. No immediate scaling needed."
        elif score >= 50:
            summary = f"Moderate capacity at {score:.0f}% headroom. Monitor trends and plan scaling."
        elif score >= 20:
            summary = f"Limited capacity at {score:.0f}% headroom. Scaling recommended soon."
        else:
            summary = f"Critical capacity at {score:.0f}% headroom. Immediate scaling action required."

        if bottlenecks:
            critical = [b for b in bottlenecks if b.severity in (BottleneckSeverity.HIGH, BottleneckSeverity.CRITICAL)]
            if critical:
                summary += f" {len(critical)} critical bottleneck(s) detected."

        return CapacityReport(
            generated_at=now,
            sample_count=len(self._samples),
            observation_window_hours=self._observation_hours(),
            current_utilization=cur,
            peak_utilization=peak,
            trends=trends,
            projections=projections,
            bottlenecks=bottlenecks,
            scaling_recommendations=recs,
            headroom_score=score,
            summary=summary,
        )

    # ── Internal helpers that accept pre-computed values ──────────

    def _detect_bottlenecks_with(
        self,
        cur: Dict[str, float],
        trends: Dict[str, TrendDirection],
        *,
        _all_trends: Optional[Dict[str, tuple]] = None,
    ) -> List[Bottleneck]:
        """Like detect_bottlenecks() but reuses pre-computed utilization/trends.

        Args:
            _all_trends: Pre-computed output of _compute_all_trends().
                When supplied the CPU slope is read directly instead of
                re-scanning all samples with _compute_trend().
        """
        bottlenecks: List[Bottleneck] = []
        if not self._samples:
            return bottlenecks

        ss = self._sorted_samples()

        cpu = cur["cpu"]
        if cpu >= self.max_cpu_threshold:
            severity = BottleneckSeverity.CRITICAL if cpu >= 0.95 else BottleneckSeverity.HIGH
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.COMPUTE, severity=severity,
                current_utilization=cpu, projected_saturation_hours=None,
                description=f"CPU at {cpu:.0%} (threshold {self.max_cpu_threshold:.0%})",
                recommendation="Scale out compute or optimize hot paths",
            ))
        elif trends.get("cpu") == TrendDirection.RISING and cpu > 0.5:
            # Reuse pre-computed CPU slope when available
            if _all_trends and "cpu" in _all_trends:
                slope = _all_trends["cpu"][1]
            else:
                cpu_vals = [s.cpu_utilization for s in ss]
                _, slope = self._compute_trend(cpu_vals)
            obs_hours = self._observation_hours()
            samples_per_hour = len(ss) / max(obs_hours, 1) if obs_hours > 0 else 1
            if slope > 0:
                remaining = (self.max_cpu_threshold - cpu) / (slope * samples_per_hour)
                bottlenecks.append(Bottleneck(
                    resource=ResourceKind.COMPUTE, severity=BottleneckSeverity.MEDIUM,
                    current_utilization=cpu, projected_saturation_hours=round(remaining, 1),
                    description=f"CPU trending up at {cpu:.0%}, projected to hit {self.max_cpu_threshold:.0%} in {remaining:.0f}h",
                    recommendation="Plan compute scaling within projection window",
                ))

        mem = cur["memory"]
        if mem >= self.max_memory_threshold:
            severity = BottleneckSeverity.CRITICAL if mem >= 0.95 else BottleneckSeverity.HIGH
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.MEMORY, severity=severity,
                current_utilization=mem, projected_saturation_hours=None,
                description=f"Memory at {mem:.0%} (threshold {self.max_memory_threshold:.0%})",
                recommendation="Increase memory or reduce session cache sizes",
            ))

        err = cur["error_rate"]
        if err >= self.max_error_threshold:
            severity = BottleneckSeverity.HIGH if err >= 0.10 else BottleneckSeverity.MEDIUM
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.API_RATE, severity=severity,
                current_utilization=err, projected_saturation_hours=None,
                description=f"Error rate at {err:.1%} (threshold {self.max_error_threshold:.0%})",
                recommendation="Check rate limits, add retry logic, or reduce request volume",
            ))

        peak = self.peak_utilization()
        if peak["sessions"] > 0:
            session_ratio = cur["sessions"] / peak["sessions"]
            if session_ratio > 0.9 and trends.get("sessions") == TrendDirection.RISING:
                bottlenecks.append(Bottleneck(
                    resource=ResourceKind.CONCURRENCY, severity=BottleneckSeverity.MEDIUM,
                    current_utilization=session_ratio, projected_saturation_hours=None,
                    description=f"Active sessions at {session_ratio:.0%} of historical peak ({cur['sessions']:.0f}/{peak['sessions']:.0f})",
                    recommendation="Prepare for session scaling or implement queue-based admission",
                ))

        return bottlenecks

    def _scaling_recommendations_with(
        self,
        cur: Dict[str, float],
        trends: Dict[str, TrendDirection],
        bottlenecks: List[Bottleneck],
    ) -> List[ScalingRecommendation]:
        """Like scaling_recommendations() but reuses pre-computed values."""
        recs: List[ScalingRecommendation] = []
        if not self._samples:
            return recs

        for b in bottlenecks:
            if b.severity == BottleneckSeverity.CRITICAL:
                recs.append(ScalingRecommendation(
                    action=ScalingAction.URGENT, resource=b.resource,
                    urgency_hours=1, rationale=b.description,
                    estimated_impact="Prevent service degradation or outage",
                ))

        if trends.get("cpu") == TrendDirection.RISING and cur["cpu"] > 0.6:
            recs.append(ScalingRecommendation(
                action=ScalingAction.SCALE_OUT, resource=ResourceKind.COMPUTE,
                urgency_hours=24 if cur["cpu"] > 0.7 else 72,
                rationale=f"CPU at {cur['cpu']:.0%} and rising",
                estimated_impact="Maintain response times as load increases",
            ))

        if (trends.get("cpu") == TrendDirection.FALLING
                and trends.get("rpm") == TrendDirection.FALLING
                and cur["cpu"] < 0.3):
            recs.append(ScalingRecommendation(
                action=ScalingAction.SCALE_DOWN, resource=ResourceKind.COMPUTE,
                urgency_hours=None, rationale=f"CPU at {cur['cpu']:.0%} with falling demand",
                estimated_impact="Reduce infrastructure costs",
            ))

        if cur["error_rate"] > self.max_error_threshold:
            recs.append(ScalingRecommendation(
                action=ScalingAction.OPTIMIZE, resource=ResourceKind.API_RATE,
                urgency_hours=12, rationale=f"Error rate at {cur['error_rate']:.1%}",
                estimated_impact="Improve reliability and reduce wasted tokens",
            ))

        if not recs:
            recs.append(ScalingRecommendation(
                action=ScalingAction.NONE, resource=ResourceKind.COMPUTE,
                urgency_hours=None, rationale="All metrics within acceptable ranges",
                estimated_impact="No action needed",
            ))

        return recs

    def _headroom_score_with(self, cur: Dict[str, float]) -> float:
        """Like headroom_score() but reuses pre-computed utilization."""
        if not self._samples:
            return 100.0
        cpu_headroom = 1.0 - cur["cpu"]
        mem_headroom = 1.0 - cur["memory"]
        err_headroom = (
            1.0 - (cur["error_rate"] / self.max_error_threshold)
            if self.max_error_threshold > 0
            else 0.0
        )
        score = cpu_headroom * 40 + mem_headroom * 30 + max(0, err_headroom) * 30
        return round(max(0, min(100, score)), 1)
