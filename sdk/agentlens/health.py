"""Session Health Scoring for AgentLens.

Evaluates completed sessions holistically by scoring multiple quality and
health metrics, producing a weighted overall score, letter grade,
per-metric breakdown, and actionable recommendations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

# Default metric weights
_DEFAULT_WEIGHTS: dict[str, float] = {
    "error_rate": 0.25,
    "avg_latency": 0.20,
    "p95_latency": 0.15,
    "tool_success": 0.15,
    "token_efficiency": 0.15,
    "event_volume": 0.10,
}


class HealthScorer:
    """Scores session health based on event data."""

    def __init__(self, thresholds: HealthThresholds | None = None) -> None:
        self.thresholds = thresholds or HealthThresholds()

    # -- public API ----------------------------------------------------------

    def score(self, events: list[dict], session_id: str = "unknown") -> HealthReport:
        """Score a list of raw event dicts.

        Each dict is expected to have keys like *event_type*, *duration_ms*,
        *tokens_in*, *tokens_out*, *tool_call* (dict or ``None``).
        """
        metrics = [
            self._score_error_rate(events),
            self._score_latency(events),
            self._score_p95_latency(events),
            self._score_tool_success(events),
            self._score_token_efficiency(events),
            self._score_event_volume(events),
        ]

        total_weight = sum(m.weight for m in metrics)
        if total_weight > 0:
            overall = sum(m.score * m.weight for m in metrics) / total_weight
        else:
            overall = 0.0

        overall = max(0.0, min(100.0, overall))

        error_count = self._count_errors(events)
        total_tokens = sum(
            (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in events
        )
        total_duration = sum(e.get("duration_ms") or 0.0 for e in events)

        return HealthReport(
            session_id=session_id,
            overall_score=overall,
            grade=self._calculate_grade(overall),
            metrics=metrics,
            recommendations=self._generate_recommendations(metrics),
            event_count=len(events),
            error_count=error_count,
            total_tokens=total_tokens,
            total_duration_ms=total_duration,
        )

    def score_session(self, session: Any) -> HealthReport:
        """Score a *Session* model object directly.

        Extracts events from ``session.events`` and delegates to
        :meth:`score`.
        """
        raw_events: list[dict] = []
        for ev in session.events:
            d: dict[str, Any] = {}
            d["event_type"] = getattr(ev, "event_type", "generic")
            d["duration_ms"] = getattr(ev, "duration_ms", None)
            d["tokens_in"] = getattr(ev, "tokens_in", 0)
            d["tokens_out"] = getattr(ev, "tokens_out", 0)

            tc = getattr(ev, "tool_call", None)
            if tc is not None:
                if hasattr(tc, "model_dump"):
                    d["tool_call"] = tc.model_dump()
                elif isinstance(tc, dict):
                    d["tool_call"] = tc
                else:
                    d["tool_call"] = {
                        "tool_name": getattr(tc, "tool_name", ""),
                        "tool_output": getattr(tc, "tool_output", None),
                    }
            else:
                d["tool_call"] = None
            raw_events.append(d)

        return self.score(raw_events, session_id=getattr(session, "session_id", "unknown"))

    # -- individual metric scorers -------------------------------------------

    def _score_error_rate(self, events: list[dict]) -> MetricScore:
        total = len(events)
        errors = self._count_errors(events)
        rate = errors / total if total > 0 else 0.0
        threshold = self.thresholds.max_error_rate

        if total == 0:
            score = 100.0
        elif rate <= 0:
            score = 100.0
        else:
            score = max(0.0, 100.0 * (1.0 - rate / threshold))

        return MetricScore(
            name="error_rate",
            score=score,
            weight=_DEFAULT_WEIGHTS["error_rate"],
            value=rate,
            threshold=threshold,
            detail=f"{errors}/{total} events errored ({rate:.1%})",
        )

    def _score_latency(self, events: list[dict]) -> MetricScore:
        durations = [e["duration_ms"] for e in events if e.get("duration_ms") is not None]
        threshold = self.thresholds.max_avg_latency_ms

        if not durations:
            return MetricScore(
                name="avg_latency",
                score=100.0,
                weight=_DEFAULT_WEIGHTS["avg_latency"],
                value=0.0,
                threshold=threshold,
                detail="No duration data available",
            )

        avg = sum(durations) / len(durations)

        if avg <= 100.0:
            score = 100.0
        elif avg >= threshold:
            score = 0.0
        else:
            # Linear from 100 at 100ms to 0 at threshold
            score = max(0.0, 100.0 * (1.0 - (avg - 100.0) / (threshold - 100.0)))

        return MetricScore(
            name="avg_latency",
            score=score,
            weight=_DEFAULT_WEIGHTS["avg_latency"],
            value=avg,
            threshold=threshold,
            detail=f"Average latency {avg:.0f}ms",
        )

    def _score_p95_latency(self, events: list[dict]) -> MetricScore:
        durations = sorted(
            e["duration_ms"] for e in events if e.get("duration_ms") is not None
        )
        threshold = self.thresholds.max_p95_latency_ms

        if not durations:
            return MetricScore(
                name="p95_latency",
                score=100.0,
                weight=_DEFAULT_WEIGHTS["p95_latency"],
                value=0.0,
                threshold=threshold,
                detail="No duration data available",
            )

        # Percentile using linear interpolation (consistent with the
        # backend's analytics.js).  Previous formula used
        # floor(0.95 * len) which computed P100 (max) for small arrays
        # instead of P95.
        idx = 0.95 * (len(durations) - 1)
        lo = int(math.floor(idx))
        hi = min(lo + 1, len(durations) - 1)
        frac = idx - lo
        p95 = durations[lo] + (durations[hi] - durations[lo]) * frac

        if p95 <= 100.0:
            score = 100.0
        elif p95 >= threshold:
            score = 0.0
        else:
            score = max(0.0, 100.0 * (1.0 - (p95 - 100.0) / (threshold - 100.0)))

        return MetricScore(
            name="p95_latency",
            score=score,
            weight=_DEFAULT_WEIGHTS["p95_latency"],
            value=p95,
            threshold=threshold,
            detail=f"P95 latency {p95:.0f}ms",
        )

    def _score_tool_success(self, events: list[dict]) -> MetricScore:
        tool_events = [e for e in events if e.get("tool_call") is not None]
        threshold = self.thresholds.min_tool_success_rate

        if not tool_events:
            return MetricScore(
                name="tool_success",
                score=100.0,
                weight=_DEFAULT_WEIGHTS["tool_success"],
                value=1.0,
                threshold=threshold,
                detail="No tool calls to evaluate",
            )

        failures = sum(1 for e in tool_events if self._is_tool_error(e))
        success_rate = 1.0 - (failures / len(tool_events))

        if success_rate >= threshold:
            score = 100.0
        elif success_rate <= 0.0:
            score = 0.0
        else:
            score = max(0.0, 100.0 * (success_rate / threshold))

        return MetricScore(
            name="tool_success",
            score=score,
            weight=_DEFAULT_WEIGHTS["tool_success"],
            value=success_rate,
            threshold=threshold,
            detail=f"Tool success rate {success_rate:.1%} ({len(tool_events)} calls)",
        )

    def _score_token_efficiency(self, events: list[dict]) -> MetricScore:
        total = len(events)
        threshold = self.thresholds.max_tokens_per_event

        if total == 0:
            return MetricScore(
                name="token_efficiency",
                score=100.0,
                weight=_DEFAULT_WEIGHTS["token_efficiency"],
                value=0.0,
                threshold=float(threshold),
                detail="No events to evaluate",
            )

        total_tokens = sum(
            (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in events
        )
        avg_tokens = total_tokens / total
        half_threshold = threshold / 2.0

        if avg_tokens <= half_threshold:
            score = 100.0
        elif avg_tokens >= threshold:
            score = 0.0
        else:
            score = max(0.0, 100.0 * (1.0 - (avg_tokens - half_threshold) / half_threshold))

        return MetricScore(
            name="token_efficiency",
            score=score,
            weight=_DEFAULT_WEIGHTS["token_efficiency"],
            value=avg_tokens,
            threshold=float(threshold),
            detail=f"Average {avg_tokens:.0f} tokens/event",
        )

    def _score_event_volume(self, events: list[dict]) -> MetricScore:
        count = len(events)
        lo, hi = self.thresholds.ideal_events_range

        if lo <= count <= hi:
            score = 100.0
            detail = f"{count} events (within ideal range {lo}-{hi})"
        elif count < lo:
            # Penalise linearly: 0 events → 0 score
            score = max(0.0, 100.0 * (count / lo)) if lo > 0 else 100.0
            detail = f"{count} events (below ideal minimum {lo})"
        else:
            # Penalise linearly above hi — fully penalised at 2× hi
            overshoot = count - hi
            penalty_range = hi  # goes to 0 at 2× hi
            if penalty_range > 0:
                score = max(0.0, 100.0 * (1.0 - overshoot / penalty_range))
            else:
                score = 0.0
            detail = f"{count} events (above ideal maximum {hi})"

        return MetricScore(
            name="event_volume",
            score=score,
            weight=_DEFAULT_WEIGHTS["event_volume"],
            value=float(count),
            threshold=float(hi),
            detail=detail,
        )

    # -- recommendations -----------------------------------------------------

    def _generate_recommendations(self, metrics: list[MetricScore]) -> list[str]:
        recs: list[str] = []

        for m in metrics:
            if m.name == "error_rate" and m.value > 0.03:
                recs.append(
                    f"High error rate ({m.value:.1%}). "
                    "Review failing events for patterns."
                )
            elif m.name == "avg_latency" and m.value > 2000:
                recs.append(
                    f"Average latency of {m.value:.0f}ms exceeds recommended threshold. "
                    "Consider model optimization."
                )
            elif m.name == "p95_latency" and m.value > 5000:
                recs.append(
                    f"P95 latency of {m.value:.0f}ms is high. "
                    "Investigate slow outlier events."
                )
            elif m.name == "tool_success" and m.value < 0.95:
                pct = m.value * 100
                recs.append(
                    f"Tool success rate of {pct:.1f}% is below target. "
                    "Check tool configurations."
                )
            elif m.name == "token_efficiency" and m.value > self.thresholds.max_tokens_per_event / 2:
                recs.append(
                    f"Average token usage of {m.value:.0f} per event is high. "
                    "Consider prompt optimization."
                )
            elif m.name == "event_volume":
                lo, hi = self.thresholds.ideal_events_range
                if m.value < lo:
                    recs.append(
                        f"Only {int(m.value)} events recorded. "
                        "Session may be incomplete or under-instrumented."
                    )
                elif m.value > hi:
                    recs.append(
                        f"{int(m.value)} events recorded (ideal max {hi}). "
                        "Consider reducing verbosity or batching events."
                    )

        return recs

    # -- helpers -------------------------------------------------------------

    def _calculate_grade(self, score: float) -> HealthGrade:
        if score >= 90:
            return HealthGrade.EXCELLENT
        if score >= 80:
            return HealthGrade.GOOD
        if score >= 70:
            return HealthGrade.FAIR
        if score >= 60:
            return HealthGrade.POOR
        return HealthGrade.CRITICAL

    @staticmethod
    def _count_errors(events: list[dict]) -> int:
        count = 0
        for e in events:
            if e.get("event_type") == "error":
                count += 1
                continue
            tc = e.get("tool_call")
            if isinstance(tc, dict):
                out = tc.get("tool_output")
                if isinstance(out, dict) and out.get("error"):
                    count += 1
        return count

    @staticmethod
    def _is_tool_error(event: dict) -> bool:
        tc = event.get("tool_call")
        if not isinstance(tc, dict):
            return False
        out = tc.get("tool_output")
        if isinstance(out, dict) and out.get("error"):
            return True
        return False
