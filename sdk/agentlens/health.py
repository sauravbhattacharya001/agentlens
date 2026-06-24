"""Session Health Scoring for AgentLens.

Evaluates completed sessions holistically by scoring multiple quality and
health metrics, producing a weighted overall score, letter grade,
per-metric breakdown, and actionable recommendations.

The value types and report formatting (:class:`HealthGrade`,
:class:`HealthThresholds`, :class:`MetricScore`, :class:`HealthReport`) live
in :mod:`agentlens.health_types` and are re-exported here so the public import
paths (``agentlens.health`` and ``agentlens``) are unchanged.
"""

from __future__ import annotations

import math
from typing import Any

from agentlens.health_types import (
    _DEFAULT_WEIGHTS,
    HealthGrade,
    HealthReport,
    HealthThresholds,
    MetricScore,
)

__all__ = [
    "HealthScorer",
    "HealthReport",
    "HealthGrade",
    "HealthThresholds",
    "MetricScore",
]


class HealthScorer:
    """Scores session health based on event data."""

    def __init__(self, thresholds: HealthThresholds | None = None) -> None:
        self.thresholds = thresholds or HealthThresholds()

    # -- public API ----------------------------------------------------------

    @staticmethod
    def _aggregate(events: list[dict]) -> dict:
        """Single-pass pre-aggregation of all per-event stats.

        Eliminates redundant iterations (previously 9+ passes over the
        events list) by collecting every metric in one loop.
        """
        total = len(events)
        error_count = 0
        total_tokens = 0
        total_duration = 0.0
        durations: list[float] = []
        tool_count = 0
        tool_failures = 0

        for e in events:
            # Error counting
            if e.get("event_type") == "error":
                error_count += 1
            else:
                tc = e.get("tool_call")
                if isinstance(tc, dict):
                    out = tc.get("tool_output")
                    if isinstance(out, dict) and out.get("error"):
                        error_count += 1

            # Duration
            dur = e.get("duration_ms")
            if dur is not None:
                durations.append(dur)
                total_duration += dur

            # Tokens
            total_tokens += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)

            # Tool calls
            tc = e.get("tool_call")
            if isinstance(tc, dict):
                tool_count += 1
                out = tc.get("tool_output")
                if isinstance(out, dict) and out.get("error"):
                    tool_failures += 1

        return {
            "total": total,
            "error_count": error_count,
            "total_tokens": total_tokens,
            "total_duration": total_duration,
            "durations": durations,
            "tool_count": tool_count,
            "tool_failures": tool_failures,
        }

    def score(self, events: list[dict], session_id: str = "unknown") -> HealthReport:
        """Score a list of raw event dicts.

        Each dict is expected to have keys like *event_type*, *duration_ms*,
        *tokens_in*, *tokens_out*, *tool_call* (dict or ``None``).

        Uses single-pass aggregation to avoid redundant iterations.
        """
        agg = self._aggregate(events)

        metrics = [
            self._score_error_rate(agg),
            self._score_latency(agg),
            self._score_p95_latency(agg),
            self._score_tool_success(agg),
            self._score_token_efficiency(agg),
            self._score_event_volume(agg),
        ]

        total_weight = sum(m.weight for m in metrics)
        if total_weight > 0:
            overall = sum(m.score * m.weight for m in metrics) / total_weight
        else:
            overall = 0.0

        overall = max(0.0, min(100.0, overall))

        return HealthReport(
            session_id=session_id,
            overall_score=overall,
            grade=self._calculate_grade(overall),
            metrics=metrics,
            recommendations=self._generate_recommendations(metrics),
            event_count=agg["total"],
            error_count=agg["error_count"],
            total_tokens=agg["total_tokens"],
            total_duration_ms=agg["total_duration"],
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

    def _ensure_agg(self, data: dict | list) -> dict:
        """Accept either a pre-aggregated dict or a raw event list.

        Allows scorer methods to be called directly with event lists
        (backward-compatible) or with pre-aggregated data from ``score()``.
        """
        if isinstance(data, list):
            return self._aggregate(data)
        return data

    def _score_error_rate(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        total = agg["total"]
        errors = agg["error_count"]
        rate = errors / total if total > 0 else 0.0
        threshold = self.thresholds.max_error_rate

        if total == 0 or rate <= 0:
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

    def _score_latency(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        durations = agg["durations"]
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

    def _score_p95_latency(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        durations = sorted(agg["durations"])
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
        # backend's analytics.js).
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

    def _score_tool_success(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        tool_count = agg["tool_count"]
        tool_failures = agg["tool_failures"]
        threshold = self.thresholds.min_tool_success_rate

        if tool_count == 0:
            return MetricScore(
                name="tool_success",
                score=100.0,
                weight=_DEFAULT_WEIGHTS["tool_success"],
                value=1.0,
                threshold=threshold,
                detail="No tool calls to evaluate",
            )

        success_rate = 1.0 - (tool_failures / tool_count)

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
            detail=f"Tool success rate {success_rate:.1%} ({tool_count} calls)",
        )

    def _score_token_efficiency(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        total = agg["total"]
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

        avg_tokens = agg["total_tokens"] / total
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

    def _score_event_volume(self, data: dict | list) -> MetricScore:
        agg = self._ensure_agg(data)
        count = agg["total"]
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
