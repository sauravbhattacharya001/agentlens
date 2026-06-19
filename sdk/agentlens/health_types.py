"""Value types and report formatting for AgentLens session health scoring.

This module holds the pure data structures that
:class:`agentlens.health.HealthScorer` produces and the configuration /
weighting vocabulary it consumes, kept separate from ``health.py`` so the
value types and their export formatting stay readable and are not interleaved
with the scoring engine.

There is no event-traversal or scoring logic here - only the health
vocabulary (:class:`HealthGrade`, :class:`HealthThresholds`,
:class:`MetricScore`, :class:`HealthReport`, and the default metric weights)
and how a finished :class:`HealthReport` renders to text / a dict.  These
symbols are re-exported from ``agentlens.health`` (and ``agentlens``) so the
public import paths are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


__all__ = [
    "HealthGrade",
    "HealthThresholds",
    "MetricScore",
    "HealthReport",
    "_DEFAULT_WEIGHTS",
]


class HealthGrade(Enum):
    """Letter-grade for session health."""
    EXCELLENT = "A"   # 90-100
    GOOD = "B"        # 80-89
    FAIR = "C"        # 70-79
    POOR = "D"        # 60-69
    CRITICAL = "F"    # 0-59


@dataclass
class HealthThresholds:
    """Configurable thresholds for health scoring."""
    max_error_rate: float = 0.05          # 5% errors → full penalty
    max_avg_latency_ms: float = 5000.0    # 5 s avg → full penalty
    max_p95_latency_ms: float = 10000.0   # 10 s p95 → full penalty
    min_tool_success_rate: float = 0.90   # Below 90% → penalised
    max_tokens_per_event: int = 8000      # Above → token-waste penalty
    max_retries_per_event: float = 0.3    # 30% retry rate → full penalty
    ideal_events_range: tuple[int, int] = (2, 100)  # Too few / too many


@dataclass
class MetricScore:
    """Score for a single health metric."""
    name: str
    score: float        # 0-100
    weight: float       # relative weight (0-1)
    value: float        # actual measured value
    threshold: float    # threshold used
    detail: str         # human-readable explanation


@dataclass
class HealthReport:
    """Complete health assessment of a session."""
    session_id: str
    overall_score: float         # 0-100 weighted average
    grade: HealthGrade
    metrics: list[MetricScore]
    recommendations: list[str]
    event_count: int
    error_count: int
    total_tokens: int
    total_duration_ms: float

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict representation."""
        return {
            "session_id": self.session_id,
            "overall_score": round(self.overall_score, 2),
            "grade": self.grade.value,
            "metrics": [
                {
                    "name": m.name,
                    "score": round(m.score, 2),
                    "weight": m.weight,
                    "value": round(m.value, 4) if isinstance(m.value, float) else m.value,
                    "threshold": m.threshold,
                    "detail": m.detail,
                }
                for m in self.metrics
            ],
            "recommendations": self.recommendations,
            "event_count": self.event_count,
            "error_count": self.error_count,
            "total_tokens": self.total_tokens,
            "total_duration_ms": round(self.total_duration_ms, 2),
        }

    def render(self) -> str:
        """Return a human-readable text summary."""
        lines: list[str] = []
        lines.append(f"Session Health Report: {self.session_id}")
        lines.append("=" * 50)
        lines.append(f"Grade: {self.grade.value}  |  Score: {self.overall_score:.1f}/100")
        lines.append(f"Events: {self.event_count}  |  Errors: {self.error_count}")
        lines.append(f"Tokens: {self.total_tokens}  |  Duration: {self.total_duration_ms:.0f}ms")
        lines.append("")
        lines.append("Metrics:")
        lines.append("-" * 50)
        for m in self.metrics:
            lines.append(
                f"  {m.name:<22} {m.score:6.1f}/100  (weight {m.weight:.2f})  {m.detail}"
            )
        if self.recommendations:
            lines.append("")
            lines.append("Recommendations:")
            lines.append("-" * 50)
            for r in self.recommendations:
                lines.append(f"  • {r}")
        return "\n".join(lines)


# Default metric weights
_DEFAULT_WEIGHTS: dict[str, float] = {
    "error_rate": 0.25,
    "avg_latency": 0.20,
    "p95_latency": 0.15,
    "tool_success": 0.15,
    "token_efficiency": 0.15,
    "event_volume": 0.10,
}
