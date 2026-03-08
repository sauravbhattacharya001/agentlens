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

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

    def _sorted_samples(self) -> List[WorkloadSample]:
        return sorted(self._samples, key=lambda s: s.timestamp)

    def _observation_hours(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        ss = self._sorted_samples()
        return (ss[-1].timestamp - ss[0].timestamp).total_seconds() / 3600

    def _compute_trend(self, values: List[float]) -> Tuple[TrendDirection, float]:
        """Simple linear regression slope and trend direction."""
        n = len(values)
        if n < 2:
            return TrendDirection.STABLE, 0.0
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(values) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
        den = sum((x - x_mean) ** 2 for x in xs)
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
        """Get samples from the most recent N hours."""
        if not self._samples:
            return []
        ss = self._sorted_samples()
        cutoff = ss[-1].timestamp - timedelta(hours=hours)
        return [s for s in ss if s.timestamp >= cutoff]

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
        """Peak utilization across all samples."""
        if not self._samples:
            return {"cpu": 0, "memory": 0, "error_rate": 0, "rpm": 0, "sessions": 0}
        return {
            "cpu": max(s.cpu_utilization for s in self._samples),
            "memory": max(s.memory_utilization for s in self._samples),
            "error_rate": max(s.error_rate for s in self._samples),
            "rpm": max(s.requests_per_minute for s in self._samples),
            "sessions": max(s.active_sessions for s in self._samples),
        }

    def compute_trends(self) -> Dict[str, TrendDirection]:
        """Compute trends for key metrics."""
        ss = self._sorted_samples()
        if len(ss) < 2:
            return {k: TrendDirection.STABLE for k in ["cpu", "memory", "rpm", "sessions", "error_rate", "latency"]}
        return {
            "cpu": self._compute_trend([s.cpu_utilization for s in ss])[0],
            "memory": self._compute_trend([s.memory_utilization for s in ss])[0],
            "rpm": self._compute_trend([s.requests_per_minute for s in ss])[0],
            "sessions": self._compute_trend([s.active_sessions for s in ss])[0],
            "error_rate": self._compute_trend([s.error_rate for s in ss])[0],
            "latency": self._compute_trend([s.avg_latency_ms for s in ss])[0],
        }

    def project_workload(self, horizon_hours: float = 24, steps: int = 6) -> List[WorkloadProjection]:
        """Project workload metrics into the future.

        Uses linear trend extrapolation with decaying confidence.
        """
        ss = self._sorted_samples()
        if len(ss) < 2:
            return []

        now = ss[-1].timestamp
        obs_hours = self._observation_hours()

        rpm_vals = [s.requests_per_minute for s in ss]
        ses_vals = [float(s.active_sessions) for s in ss]
        tok_vals = [s.token_throughput for s in ss]

        _, rpm_slope = self._compute_trend(rpm_vals)
        _, ses_slope = self._compute_trend(ses_vals)
        _, tok_slope = self._compute_trend(tok_vals)

        cur_rpm = statistics.mean(rpm_vals[-3:]) if len(rpm_vals) >= 3 else rpm_vals[-1]
        cur_ses = statistics.mean(ses_vals[-3:]) if len(ses_vals) >= 3 else ses_vals[-1]
        cur_tok = statistics.mean(tok_vals[-3:]) if len(tok_vals) >= 3 else tok_vals[-1]

        projections: List[WorkloadProjection] = []
        for i in range(1, steps + 1):
            t = horizon_hours * i / steps
            # Scale slope from per-sample to per-hour
            samples_per_hour = len(ss) / max(obs_hours, 1)
            rpm_proj = max(0, cur_rpm + rpm_slope * samples_per_hour * t)
            ses_proj = max(0, cur_ses + ses_slope * samples_per_hour * t)
            tok_proj = max(0, cur_tok + tok_slope * samples_per_hour * t)
            # Confidence decays with projection distance
            confidence = max(0.1, 1.0 - (t / horizon_hours) * 0.7)

            overall_trend = self._compute_trend(rpm_vals)[0]

            projections.append(WorkloadProjection(
                timestamp=now + timedelta(hours=t),
                projected_rpm=round(rpm_proj, 1),
                projected_sessions=round(ses_proj, 1),
                projected_tokens=round(tok_proj, 1),
                confidence=round(confidence, 2),
                trend=overall_trend,
            ))
        return projections

    def detect_bottlenecks(self) -> List[Bottleneck]:
        """Identify current and emerging capacity bottlenecks."""
        bottlenecks: List[Bottleneck] = []
        if not self._samples:
            return bottlenecks

        cur = self.current_utilization()
        trends = self.compute_trends()
        ss = self._sorted_samples()

        # CPU bottleneck
        cpu = cur["cpu"]
        if cpu >= self.max_cpu_threshold:
            severity = BottleneckSeverity.CRITICAL if cpu >= 0.95 else BottleneckSeverity.HIGH
            sat_hours = None
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.COMPUTE,
                severity=severity,
                current_utilization=cpu,
                projected_saturation_hours=sat_hours,
                description=f"CPU at {cpu:.0%} (threshold {self.max_cpu_threshold:.0%})",
                recommendation="Scale out compute or optimize hot paths",
            ))
        elif trends.get("cpu") == TrendDirection.RISING and cpu > 0.5:
            # Project when CPU hits threshold
            cpu_vals = [s.cpu_utilization for s in ss]
            _, slope = self._compute_trend(cpu_vals)
            obs_hours = self._observation_hours()
            samples_per_hour = len(ss) / max(obs_hours, 1) if obs_hours > 0 else 1
            if slope > 0:
                remaining = (self.max_cpu_threshold - cpu) / (slope * samples_per_hour)
                bottlenecks.append(Bottleneck(
                    resource=ResourceKind.COMPUTE,
                    severity=BottleneckSeverity.MEDIUM,
                    current_utilization=cpu,
                    projected_saturation_hours=round(remaining, 1),
                    description=f"CPU trending up at {cpu:.0%}, projected to hit {self.max_cpu_threshold:.0%} in {remaining:.0f}h",
                    recommendation="Plan compute scaling within projection window",
                ))

        # Memory bottleneck
        mem = cur["memory"]
        if mem >= self.max_memory_threshold:
            severity = BottleneckSeverity.CRITICAL if mem >= 0.95 else BottleneckSeverity.HIGH
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.MEMORY,
                severity=severity,
                current_utilization=mem,
                projected_saturation_hours=None,
                description=f"Memory at {mem:.0%} (threshold {self.max_memory_threshold:.0%})",
                recommendation="Increase memory or reduce session cache sizes",
            ))

        # Error rate bottleneck
        err = cur["error_rate"]
        if err >= self.max_error_threshold:
            severity = BottleneckSeverity.HIGH if err >= 0.10 else BottleneckSeverity.MEDIUM
            bottlenecks.append(Bottleneck(
                resource=ResourceKind.API_RATE,
                severity=severity,
                current_utilization=err,
                projected_saturation_hours=None,
                description=f"Error rate at {err:.1%} (threshold {self.max_error_threshold:.0%})",
                recommendation="Check rate limits, add retry logic, or reduce request volume",
            ))

        # Concurrency / session pressure
        peak = self.peak_utilization()
        if peak["sessions"] > 0:
            session_ratio = cur["sessions"] / peak["sessions"]
            if session_ratio > 0.9 and trends.get("sessions") == TrendDirection.RISING:
                bottlenecks.append(Bottleneck(
                    resource=ResourceKind.CONCURRENCY,
                    severity=BottleneckSeverity.MEDIUM,
                    current_utilization=session_ratio,
                    projected_saturation_hours=None,
                    description=f"Active sessions at {session_ratio:.0%} of historical peak ({cur['sessions']:.0f}/{peak['sessions']:.0f})",
                    recommendation="Prepare for session scaling or implement queue-based admission",
                ))

        return bottlenecks

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
        recs: List[ScalingRecommendation] = []
        if not self._samples:
            return recs

        cur = self.current_utilization()
        trends = self.compute_trends()
        bottlenecks = self.detect_bottlenecks()

        # Critical bottleneck → urgent action
        for b in bottlenecks:
            if b.severity == BottleneckSeverity.CRITICAL:
                recs.append(ScalingRecommendation(
                    action=ScalingAction.URGENT,
                    resource=b.resource,
                    urgency_hours=1,
                    rationale=b.description,
                    estimated_impact="Prevent service degradation or outage",
                ))

        # Rising CPU trend
        if trends.get("cpu") == TrendDirection.RISING and cur["cpu"] > 0.6:
            recs.append(ScalingRecommendation(
                action=ScalingAction.SCALE_OUT,
                resource=ResourceKind.COMPUTE,
                urgency_hours=24 if cur["cpu"] > 0.7 else 72,
                rationale=f"CPU at {cur['cpu']:.0%} and rising",
                estimated_impact="Maintain response times as load increases",
            ))

        # Falling utilization → scale down
        if (trends.get("cpu") == TrendDirection.FALLING
                and trends.get("rpm") == TrendDirection.FALLING
                and cur["cpu"] < 0.3):
            recs.append(ScalingRecommendation(
                action=ScalingAction.SCALE_DOWN,
                resource=ResourceKind.COMPUTE,
                urgency_hours=None,
                rationale=f"CPU at {cur['cpu']:.0%} with falling demand",
                estimated_impact="Reduce infrastructure costs",
            ))

        # High error rate → optimize
        if cur["error_rate"] > self.max_error_threshold:
            recs.append(ScalingRecommendation(
                action=ScalingAction.OPTIMIZE,
                resource=ResourceKind.API_RATE,
                urgency_hours=12,
                rationale=f"Error rate at {cur['error_rate']:.1%}",
                estimated_impact="Improve reliability and reduce wasted tokens",
            ))

        if not recs:
            recs.append(ScalingRecommendation(
                action=ScalingAction.NONE,
                resource=ResourceKind.COMPUTE,
                urgency_hours=None,
                rationale="All metrics within acceptable ranges",
                estimated_impact="No action needed",
            ))

        return recs

    def headroom_score(self) -> float:
        """Calculate overall headroom score (0-100).

        Higher = more capacity headroom available.
        """
        if not self._samples:
            return 100.0

        cur = self.current_utilization()
        cpu_headroom = 1.0 - cur["cpu"]
        mem_headroom = 1.0 - cur["memory"]
        err_headroom = 1.0 - (cur["error_rate"] / self.max_error_threshold)

        # Weighted average
        score = (
            cpu_headroom * 40
            + mem_headroom * 30
            + max(0, err_headroom) * 30
        )
        return round(max(0, min(100, score)), 1)

    def report(self) -> CapacityReport:
        """Generate a comprehensive capacity planning report."""
        now = datetime.now()
        cur = self.current_utilization()
        peak = self.peak_utilization()
        trends = self.compute_trends()
        projections = self.project_workload()
        bottlenecks = self.detect_bottlenecks()
        recs = self.scaling_recommendations()
        score = self.headroom_score()

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
