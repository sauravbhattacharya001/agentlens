"""Agent Memory Leak Detector for AgentLens.

Autonomously identifies growing context/memory accumulation patterns in agent
sessions — detecting when agents accumulate context without releasing it,
leading to token bloat, latency creep, and eventual context window exhaustion.

Answers: "Is my agent leaking memory? Where is it accumulating? When will it
blow the context window?"

Detects 7 leak categories:
  1. Token Growth — monotonic increase in tokens_in per call
  2. Context Snowball — accelerating context accumulation rate
  3. Tool Output Hoarding — tool outputs growing without summarization
  4. Repetition Bloat — repeated content inflating context
  5. Dead Reference Retention — references to completed/failed operations kept
  6. Unbounded History — linear or super-linear history growth
  7. Payload Inflation — growing input/output payload sizes

Usage::

    from agentlens.memory_leak import MemoryLeakDetector

    detector = MemoryLeakDetector()
    report = detector.analyze(session)
    print(report.format_report())
    print(f"Leak severity: {report.severity.value}")
    print(f"Projected exhaustion at event #{report.exhaustion_event_index}")

Pure Python, stdlib only (math, statistics, dataclasses, enum).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────


class LeakSeverity(Enum):
    """Overall leak severity classification."""
    NONE = "none"                      # No leaks detected
    LOW = "low"                        # Minor accumulation, manageable
    MODERATE = "moderate"              # Growing concern, intervention advised
    HIGH = "high"                      # Active leaks, nearing limits
    CRITICAL = "critical"             # Imminent context exhaustion


class LeakCategory(Enum):
    """Types of memory leak patterns."""
    TOKEN_GROWTH = "token_growth"
    CONTEXT_SNOWBALL = "context_snowball"
    TOOL_OUTPUT_HOARDING = "tool_output_hoarding"
    REPETITION_BLOAT = "repetition_bloat"
    DEAD_REFERENCE_RETENTION = "dead_reference_retention"
    UNBOUNDED_HISTORY = "unbounded_history"
    PAYLOAD_INFLATION = "payload_inflation"


class TrendDirection(Enum):
    """Direction of a metric trend."""
    STABLE = "stable"
    GROWING = "growing"
    ACCELERATING = "accelerating"
    DECELERATING = "decelerating"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class LeakSignal:
    """A single detected leak signal."""
    category: LeakCategory
    severity: LeakSeverity
    confidence: float          # 0.0 - 1.0
    description: str
    first_seen_index: int      # Event index where leak started
    growth_rate: float         # Units per event (tokens, bytes, etc.)
    trend: TrendDirection
    evidence: list[str] = field(default_factory=list)
    recommended_action: str = ""


@dataclass
class GrowthSegment:
    """A segment of monotonic growth in a time series."""
    start_index: int
    end_index: int
    start_value: float
    end_value: float
    slope: float               # Growth per event
    r_squared: float           # Fit quality


@dataclass
class ExhaustionForecast:
    """Prediction of when context window will be exhausted."""
    current_usage: int         # Current total tokens
    projected_limit: int       # Assumed context window size
    events_until_exhaustion: int | None  # None if no exhaustion predicted
    exhaustion_probability: float  # 0.0 - 1.0
    growth_model: str          # "linear", "quadratic", "exponential"
    confidence: float


@dataclass
class AccumulationProfile:
    """Profile of how context accumulates across event types."""
    event_type: str
    total_contribution: int    # Total tokens contributed
    avg_contribution: float    # Avg tokens per event of this type
    growth_trend: TrendDirection
    count: int


@dataclass
class MemoryLeakReport:
    """Complete memory leak analysis report."""
    session_id: str
    total_events: int
    leak_signals: list[LeakSignal]
    severity: LeakSeverity
    leak_score: float          # 0 - 100 (0 = no leaks, 100 = critical)
    exhaustion_forecast: ExhaustionForecast | None
    accumulation_profiles: list[AccumulationProfile]
    growth_segments: list[GrowthSegment]
    peak_token_usage: int
    total_token_growth: int    # tokens at end - tokens at start
    growth_rate_per_event: float
    recommendations: list[str]
    json_data: dict[str, Any] = field(default_factory=dict)

    def format_report(self) -> str:
        """Format as human-readable report."""
        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║          AGENT MEMORY LEAK DETECTOR — REPORT               ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            f"  Session:          {self.session_id}",
            f"  Events analyzed:  {self.total_events}",
            f"  Leak Score:       {self.leak_score:.1f}/100",
            f"  Severity:         {self.severity.value.upper()}",
            f"  Peak tokens:      {self.peak_token_usage:,}",
            f"  Total growth:     {self.total_token_growth:,} tokens",
            f"  Growth rate:      {self.growth_rate_per_event:.1f} tokens/event",
            "",
        ]

        if self.exhaustion_forecast:
            ef = self.exhaustion_forecast
            lines.append("  ┌─── Exhaustion Forecast ───────────────────────────────┐")
            lines.append(f"  │ Current usage:   {ef.current_usage:,} tokens")
            lines.append(f"  │ Window limit:    {ef.projected_limit:,} tokens")
            if ef.events_until_exhaustion is not None:
                lines.append(f"  │ Events until exhaustion: ~{ef.events_until_exhaustion}")
            lines.append(f"  │ Exhaustion probability: {ef.exhaustion_probability:.1%}")
            lines.append(f"  │ Growth model:    {ef.growth_model}")
            lines.append("  └─────────────────────────────────────────────────────────┘")
            lines.append("")

        if self.leak_signals:
            lines.append("  ┌─── Detected Leaks ────────────────────────────────────┐")
            for sig in sorted(self.leak_signals, key=lambda s: s.confidence, reverse=True):
                sev_icon = {"none": "○", "low": "◔", "moderate": "◑", "high": "◕", "critical": "●"}
                icon = sev_icon.get(sig.severity.value, "?")
                lines.append(f"  │ {icon} [{sig.category.value}]")
                lines.append(f"  │   {sig.description}")
                lines.append(f"  │   Severity: {sig.severity.value} | Confidence: {sig.confidence:.0%}")
                lines.append(f"  │   Growth: {sig.growth_rate:.1f}/event | Trend: {sig.trend.value}")
                if sig.recommended_action:
                    lines.append(f"  │   → {sig.recommended_action}")
                lines.append("  │")
            lines.append("  └─────────────────────────────────────────────────────────┘")
            lines.append("")

        if self.accumulation_profiles:
            lines.append("  ┌─── Accumulation by Event Type ────────────────────────┐")
            for prof in sorted(self.accumulation_profiles, key=lambda p: p.total_contribution, reverse=True)[:5]:
                lines.append(f"  │ {prof.event_type:20s} {prof.total_contribution:>8,} tokens ({prof.count} events, ~{prof.avg_contribution:.0f}/ea)")
            lines.append("  └─────────────────────────────────────────────────────────┘")
            lines.append("")

        if self.recommendations:
            lines.append("  ┌─── Recommendations ───────────────────────────────────┐")
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"  │ {i}. {rec}")
            lines.append("  └─────────────────────────────────────────────────────────┘")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "severity": self.severity.value,
            "leak_score": self.leak_score,
            "peak_token_usage": self.peak_token_usage,
            "total_token_growth": self.total_token_growth,
            "growth_rate_per_event": self.growth_rate_per_event,
            "leak_signals": [
                {
                    "category": s.category.value,
                    "severity": s.severity.value,
                    "confidence": s.confidence,
                    "description": s.description,
                    "first_seen_index": s.first_seen_index,
                    "growth_rate": s.growth_rate,
                    "trend": s.trend.value,
                    "evidence": s.evidence,
                    "recommended_action": s.recommended_action,
                }
                for s in self.leak_signals
            ],
            "exhaustion_forecast": {
                "current_usage": self.exhaustion_forecast.current_usage,
                "projected_limit": self.exhaustion_forecast.projected_limit,
                "events_until_exhaustion": self.exhaustion_forecast.events_until_exhaustion,
                "exhaustion_probability": self.exhaustion_forecast.exhaustion_probability,
                "growth_model": self.exhaustion_forecast.growth_model,
                "confidence": self.exhaustion_forecast.confidence,
            } if self.exhaustion_forecast else None,
            "accumulation_profiles": [
                {
                    "event_type": p.event_type,
                    "total_contribution": p.total_contribution,
                    "avg_contribution": p.avg_contribution,
                    "growth_trend": p.growth_trend.value,
                    "count": p.count,
                }
                for p in self.accumulation_profiles
            ],
            "recommendations": self.recommendations,
        }

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class LeakDetectorConfig:
    """Configuration for the memory leak detector."""
    # Context window assumptions
    context_window_tokens: int = 128_000  # Default to GPT-4 128k
    warning_threshold: float = 0.75       # Warn at 75% usage

    # Detection sensitivity
    min_events_for_analysis: int = 5      # Need at least this many events
    growth_significance_threshold: float = 0.6  # R² for trend significance
    monotonic_run_threshold: int = 4      # Consecutive increases to flag

    # Snowball detection
    acceleration_threshold: float = 1.5   # Growth rate must increase 1.5x

    # Repetition detection
    repetition_similarity_threshold: float = 0.8  # Jaccard similarity

    # Payload inflation
    payload_growth_factor: float = 2.0    # 2x growth triggers flag


# ── Detector Engine ─────────────────────────────────────────────────


class MemoryLeakDetector:
    """Autonomous agent memory leak detection engine.

    Analyzes session events to detect patterns of unbounded context/memory
    growth that will eventually cause context window exhaustion, latency
    issues, or cost overruns.
    """

    def __init__(self, config: LeakDetectorConfig | None = None):
        self.config = config or LeakDetectorConfig()

    def analyze(self, session: Any) -> MemoryLeakReport:
        """Analyze a session for memory leak patterns.

        Args:
            session: An AgentLens Session object with .events list.

        Returns:
            MemoryLeakReport with detected leaks and recommendations.
        """
        events = session.events if hasattr(session, "events") else []
        session_id = session.session_id if hasattr(session, "session_id") else "unknown"

        if len(events) < self.config.min_events_for_analysis:
            return self._empty_report(session_id, len(events))

        # Extract time series
        tokens_in_series = [e.tokens_in for e in events]
        tokens_out_series = [e.tokens_out for e in events]
        cumulative_tokens = self._cumulative(tokens_in_series)
        durations = [e.duration_ms or 0 for e in events]

        # Run all detectors
        signals: list[LeakSignal] = []
        signals.extend(self._detect_token_growth(tokens_in_series))
        signals.extend(self._detect_context_snowball(cumulative_tokens))
        signals.extend(self._detect_tool_output_hoarding(events))
        signals.extend(self._detect_repetition_bloat(events))
        signals.extend(self._detect_dead_references(events))
        signals.extend(self._detect_unbounded_history(tokens_in_series, cumulative_tokens))
        signals.extend(self._detect_payload_inflation(events))

        # Compute growth segments
        growth_segments = self._find_growth_segments(tokens_in_series)

        # Compute accumulation profiles
        profiles = self._compute_accumulation_profiles(events)

        # Compute exhaustion forecast
        forecast = self._forecast_exhaustion(cumulative_tokens, tokens_in_series)

        # Score and classify
        leak_score = self._compute_leak_score(signals)
        severity = self._classify_severity(leak_score)

        # Generate recommendations
        recommendations = self._generate_recommendations(signals, forecast)

        peak_usage = max(cumulative_tokens) if cumulative_tokens else 0
        total_growth = tokens_in_series[-1] - tokens_in_series[0] if len(tokens_in_series) > 1 else 0
        growth_rate = total_growth / max(len(tokens_in_series) - 1, 1)

        return MemoryLeakReport(
            session_id=session_id,
            total_events=len(events),
            leak_signals=signals,
            severity=severity,
            leak_score=leak_score,
            exhaustion_forecast=forecast,
            accumulation_profiles=profiles,
            growth_segments=growth_segments,
            peak_token_usage=peak_usage,
            total_token_growth=total_growth,
            growth_rate_per_event=growth_rate,
            recommendations=recommendations,
        )

    # ── Detector: Token Growth ──────────────────────────────────────

    def _detect_token_growth(self, series: list[int]) -> list[LeakSignal]:
        """Detect monotonically increasing token consumption per call."""
        if len(series) < self.config.min_events_for_analysis:
            return []

        signals = []

        # Find longest monotonic run
        max_run_start = 0
        max_run_len = 1
        current_start = 0
        current_len = 1

        for i in range(1, len(series)):
            if series[i] >= series[i - 1]:
                current_len += 1
            else:
                if current_len > max_run_len:
                    max_run_len = current_len
                    max_run_start = current_start
                current_start = i
                current_len = 1
        if current_len > max_run_len:
            max_run_len = current_len
            max_run_start = current_start

        if max_run_len >= self.config.monotonic_run_threshold:
            run_end = max_run_start + max_run_len - 1
            growth = series[run_end] - series[max_run_start]
            rate = growth / max(max_run_len - 1, 1)

            # Linear regression on the run
            r_sq = self._r_squared(series[max_run_start:run_end + 1])
            confidence = min(1.0, r_sq * (max_run_len / len(series)))

            if confidence > 0.3:
                severity = self._rate_to_severity(rate, series)
                signals.append(LeakSignal(
                    category=LeakCategory.TOKEN_GROWTH,
                    severity=severity,
                    confidence=confidence,
                    description=f"Token consumption growing monotonically for {max_run_len} consecutive events (+{growth:,} tokens)",
                    first_seen_index=max_run_start,
                    growth_rate=rate,
                    trend=TrendDirection.GROWING,
                    evidence=[
                        f"Run: events {max_run_start}-{run_end}",
                        f"Start: {series[max_run_start]:,} → End: {series[run_end]:,}",
                        f"R²: {r_sq:.3f}",
                    ],
                    recommended_action="Implement context summarization to compress older history",
                ))

        return signals

    # ── Detector: Context Snowball ──────────────────────────────────

    def _detect_context_snowball(self, cumulative: list[int]) -> list[LeakSignal]:
        """Detect accelerating cumulative context growth (super-linear)."""
        if len(cumulative) < self.config.min_events_for_analysis:
            return []

        signals = []

        # Compare growth rate in first half vs second half
        mid = len(cumulative) // 2
        if mid < 2:
            return []

        first_half_rate = (cumulative[mid] - cumulative[0]) / mid
        second_half_rate = (cumulative[-1] - cumulative[mid]) / (len(cumulative) - mid)

        if first_half_rate > 0 and second_half_rate / first_half_rate >= self.config.acceleration_threshold:
            acceleration_ratio = second_half_rate / first_half_rate
            confidence = min(1.0, (acceleration_ratio - 1.0) / 2.0)

            signals.append(LeakSignal(
                category=LeakCategory.CONTEXT_SNOWBALL,
                severity=self._acceleration_to_severity(acceleration_ratio),
                confidence=confidence,
                description=f"Context accumulation accelerating: {acceleration_ratio:.1f}x faster in second half",
                first_seen_index=mid,
                growth_rate=second_half_rate,
                trend=TrendDirection.ACCELERATING,
                evidence=[
                    f"First half rate: {first_half_rate:.1f} tokens/event",
                    f"Second half rate: {second_half_rate:.1f} tokens/event",
                    f"Acceleration: {acceleration_ratio:.2f}x",
                ],
                recommended_action="Add progressive summarization — compress earlier context as session grows",
            ))

        return signals

    # ── Detector: Tool Output Hoarding ──────────────────────────────

    def _detect_tool_output_hoarding(self, events: list[Any]) -> list[LeakSignal]:
        """Detect growing tool output sizes without compression."""
        tool_events = [e for e in events if e.event_type == "tool_call" and e.tool_call]
        if len(tool_events) < 3:
            return []

        signals = []

        # Track output sizes
        output_sizes: list[int] = []
        for e in tool_events:
            if e.tool_call and e.tool_call.tool_output:
                size = len(json.dumps(e.tool_call.tool_output, default=str))
                output_sizes.append(size)
            else:
                output_sizes.append(0)

        if len(output_sizes) < 3:
            return []

        # Check for growing trend
        slope, r_sq = self._linear_fit(output_sizes)
        if slope > 0 and r_sq > self.config.growth_significance_threshold:
            avg_size = statistics.mean(output_sizes)
            growth_pct = (slope * len(output_sizes)) / max(avg_size, 1) * 100

            if growth_pct > 50:  # More than 50% growth over session
                confidence = min(1.0, r_sq * min(growth_pct / 100, 1.0))
                signals.append(LeakSignal(
                    category=LeakCategory.TOOL_OUTPUT_HOARDING,
                    severity=self._pct_to_severity(growth_pct),
                    confidence=confidence,
                    description=f"Tool outputs growing {growth_pct:.0f}% across session — no summarization detected",
                    first_seen_index=next((i for i, e in enumerate(events) if e.event_type == "tool_call"), 0),
                    growth_rate=slope,
                    trend=TrendDirection.GROWING,
                    evidence=[
                        f"Avg output size: {avg_size:.0f} bytes",
                        f"Growth: {slope:.1f} bytes/call",
                        f"Total tool calls: {len(tool_events)}",
                    ],
                    recommended_action="Summarize or truncate tool outputs before adding to context",
                ))

        return signals

    # ── Detector: Repetition Bloat ──────────────────────────────────

    def _detect_repetition_bloat(self, events: list[Any]) -> list[LeakSignal]:
        """Detect repeated content inflating context."""
        if len(events) < 4:
            return []

        signals = []

        # Extract text fingerprints from input_data
        fingerprints: list[set[str]] = []
        for e in events:
            if e.input_data:
                text = json.dumps(e.input_data, default=str)
                # Simple word-level shingling
                words = text.lower().split()
                shingles = set()
                for i in range(len(words) - 2):
                    shingles.add(f"{words[i]}_{words[i+1]}_{words[i+2]}")
                fingerprints.append(shingles)
            else:
                fingerprints.append(set())

        # Check for high similarity between consecutive events
        high_sim_count = 0
        first_repetition = -1
        for i in range(1, len(fingerprints)):
            if fingerprints[i] and fingerprints[i - 1]:
                sim = self._jaccard(fingerprints[i], fingerprints[i - 1])
                if sim > self.config.repetition_similarity_threshold:
                    high_sim_count += 1
                    if first_repetition == -1:
                        first_repetition = i

        if high_sim_count >= 3:
            repetition_rate = high_sim_count / (len(events) - 1)
            confidence = min(1.0, repetition_rate * 1.5)

            signals.append(LeakSignal(
                category=LeakCategory.REPETITION_BLOAT,
                severity=self._rate_fraction_to_severity(repetition_rate),
                confidence=confidence,
                description=f"{high_sim_count} event pairs show >80% content overlap — repeated context bloat",
                first_seen_index=first_repetition if first_repetition >= 0 else 0,
                growth_rate=high_sim_count,
                trend=TrendDirection.STABLE,
                evidence=[
                    f"Repetition rate: {repetition_rate:.1%} of transitions",
                    f"Similarity threshold: {self.config.repetition_similarity_threshold}",
                ],
                recommended_action="Deduplicate context — keep only latest version of repeated information",
            ))

        return signals

    # ── Detector: Dead Reference Retention ──────────────────────────

    def _detect_dead_references(self, events: list[Any]) -> list[LeakSignal]:
        """Detect references to failed/completed operations kept in context."""
        if len(events) < 4:
            return []

        signals = []

        # Track error events and see if subsequent events still reference them
        error_indices: list[int] = []
        for i, e in enumerate(events):
            if e.event_type == "error" or (e.output_data and e.output_data.get("error")):
                error_indices.append(i)

        if not error_indices:
            return []

        # Check if post-error events still carry growing context
        dead_ref_count = 0
        for err_idx in error_indices:
            # Look at events after the error
            post_error = events[err_idx + 1:err_idx + 5]
            for pe in post_error:
                if pe.tokens_in > 0 and pe.input_data:
                    input_str = json.dumps(pe.input_data, default=str).lower()
                    # Check for error-related keywords still in context
                    if any(kw in input_str for kw in ["error", "failed", "exception", "traceback"]):
                        dead_ref_count += 1

        if dead_ref_count >= 2:
            confidence = min(1.0, dead_ref_count / len(error_indices))
            signals.append(LeakSignal(
                category=LeakCategory.DEAD_REFERENCE_RETENTION,
                severity=LeakSeverity.MODERATE if dead_ref_count < 5 else LeakSeverity.HIGH,
                confidence=confidence,
                description=f"Error context retained in {dead_ref_count} subsequent events — dead references inflating context",
                first_seen_index=error_indices[0],
                growth_rate=float(dead_ref_count),
                trend=TrendDirection.STABLE,
                evidence=[
                    f"Error events: {len(error_indices)}",
                    f"Post-error references: {dead_ref_count}",
                ],
                recommended_action="Clear error context after handling — retain only actionable summaries",
            ))

        return signals

    # ── Detector: Unbounded History ─────────────────────────────────

    def _detect_unbounded_history(self, per_event: list[int], cumulative: list[int]) -> list[LeakSignal]:
        """Detect linear or super-linear history growth without windowing."""
        if len(per_event) < self.config.min_events_for_analysis:
            return []

        signals = []

        # Check if cumulative growth is super-linear (quadratic or exponential)
        n = len(cumulative)
        indices = list(range(n))

        # Fit linear
        linear_slope, linear_r2 = self._linear_fit(cumulative)

        # Fit quadratic (check if residuals have systematic pattern)
        if n > 6:
            # Simple quadratic check: compare first-third, mid-third, last-third slopes
            third = n // 3
            slope1 = (cumulative[third] - cumulative[0]) / third
            slope2 = (cumulative[2 * third] - cumulative[third]) / third
            slope3 = (cumulative[-1] - cumulative[2 * third]) / (n - 2 * third)

            if slope1 > 0 and slope2 > slope1 * 1.2 and slope3 > slope2 * 1.2:
                confidence = min(1.0, (slope3 / slope1 - 1.0) / 3.0)
                signals.append(LeakSignal(
                    category=LeakCategory.UNBOUNDED_HISTORY,
                    severity=LeakSeverity.HIGH,
                    confidence=confidence,
                    description=f"Super-linear context growth: rate increasing from {slope1:.0f} to {slope3:.0f} tokens/event",
                    first_seen_index=0,
                    growth_rate=slope3,
                    trend=TrendDirection.ACCELERATING,
                    evidence=[
                        f"Early rate: {slope1:.1f} tokens/event",
                        f"Mid rate: {slope2:.1f} tokens/event",
                        f"Late rate: {slope3:.1f} tokens/event",
                    ],
                    recommended_action="Implement sliding window or progressive summarization for history",
                ))
            elif linear_r2 > 0.9 and linear_slope > 100:
                # Strong linear growth — still unbounded
                signals.append(LeakSignal(
                    category=LeakCategory.UNBOUNDED_HISTORY,
                    severity=LeakSeverity.MODERATE,
                    confidence=linear_r2 * 0.7,
                    description=f"Linear unbounded history growth at {linear_slope:.0f} tokens/event",
                    first_seen_index=0,
                    growth_rate=linear_slope,
                    trend=TrendDirection.GROWING,
                    evidence=[
                        f"Linear fit R²: {linear_r2:.3f}",
                        f"Slope: {linear_slope:.1f} tokens/event",
                        f"Projected at 100 events: {linear_slope * 100:,.0f} cumulative tokens",
                    ],
                    recommended_action="Add context windowing — keep only last N events in full detail",
                ))

        return signals

    # ── Detector: Payload Inflation ─────────────────────────────────

    def _detect_payload_inflation(self, events: list[Any]) -> list[LeakSignal]:
        """Detect growing input/output data payload sizes."""
        if len(events) < self.config.min_events_for_analysis:
            return []

        signals = []

        # Measure payload sizes
        input_sizes: list[int] = []
        for e in events:
            if e.input_data:
                input_sizes.append(len(json.dumps(e.input_data, default=str)))
            else:
                input_sizes.append(0)

        non_zero = [s for s in input_sizes if s > 0]
        if len(non_zero) < 3:
            return []

        # Check for significant growth
        early_avg = statistics.mean(non_zero[:len(non_zero) // 3]) if len(non_zero) >= 3 else non_zero[0]
        late_avg = statistics.mean(non_zero[-(len(non_zero) // 3):]) if len(non_zero) >= 3 else non_zero[-1]

        if early_avg > 0 and late_avg / early_avg >= self.config.payload_growth_factor:
            growth_factor = late_avg / early_avg
            confidence = min(1.0, (growth_factor - 1.0) / 3.0)

            signals.append(LeakSignal(
                category=LeakCategory.PAYLOAD_INFLATION,
                severity=self._factor_to_severity(growth_factor),
                confidence=confidence,
                description=f"Input payloads grew {growth_factor:.1f}x from session start to end",
                first_seen_index=0,
                growth_rate=late_avg - early_avg,
                trend=TrendDirection.GROWING,
                evidence=[
                    f"Early avg payload: {early_avg:,.0f} bytes",
                    f"Late avg payload: {late_avg:,.0f} bytes",
                    f"Growth factor: {growth_factor:.2f}x",
                ],
                recommended_action="Trim or compress input payloads — consider extracting only relevant fields",
            ))

        return signals

    # ── Forecasting ─────────────────────────────────────────────────

    def _forecast_exhaustion(self, cumulative: list[int], per_event: list[int]) -> ExhaustionForecast | None:
        """Forecast when context window will be exhausted."""
        if len(cumulative) < 3:
            return None

        current_usage = cumulative[-1]
        limit = self.config.context_window_tokens
        remaining = limit - current_usage

        if remaining <= 0:
            return ExhaustionForecast(
                current_usage=current_usage,
                projected_limit=limit,
                events_until_exhaustion=0,
                exhaustion_probability=1.0,
                growth_model="already_exceeded",
                confidence=1.0,
            )

        # Estimate growth rate from recent events
        recent = per_event[-min(10, len(per_event)):]
        avg_rate = statistics.mean(recent) if recent else 0

        if avg_rate <= 0:
            return ExhaustionForecast(
                current_usage=current_usage,
                projected_limit=limit,
                events_until_exhaustion=None,
                exhaustion_probability=0.0,
                growth_model="stable",
                confidence=0.8,
            )

        # Linear projection
        events_left = int(remaining / avg_rate)

        # Check if growth is accelerating
        if len(per_event) > 6:
            mid = len(per_event) // 2
            early_rate = statistics.mean(per_event[:mid])
            late_rate = statistics.mean(per_event[mid:])
            if early_rate > 0 and late_rate > early_rate * 1.3:
                # Accelerating — use quadratic model
                acceleration = (late_rate - early_rate) / mid
                # Quadratic: remaining = avg_rate*n + 0.5*acceleration*n²
                # Solve: 0.5*a*n² + r*n - remaining = 0
                a = 0.5 * acceleration
                b = avg_rate
                c = -remaining
                discriminant = b * b - 4 * a * c
                if discriminant > 0 and a > 0:
                    events_left = int((-b + math.sqrt(discriminant)) / (2 * a))
                    return ExhaustionForecast(
                        current_usage=current_usage,
                        projected_limit=limit,
                        events_until_exhaustion=max(0, events_left),
                        exhaustion_probability=min(1.0, 0.5 + (late_rate / early_rate - 1.0)),
                        growth_model="quadratic",
                        confidence=0.6,
                    )

        # Probability based on usage fraction and trend
        usage_fraction = current_usage / limit
        probability = min(1.0, usage_fraction * 1.3) if avg_rate > 50 else usage_fraction * 0.5

        return ExhaustionForecast(
            current_usage=current_usage,
            projected_limit=limit,
            events_until_exhaustion=max(0, events_left),
            exhaustion_probability=probability,
            growth_model="linear",
            confidence=0.7,
        )

    # ── Growth Segment Detection ────────────────────────────────────

    def _find_growth_segments(self, series: list[int]) -> list[GrowthSegment]:
        """Find segments of consistent growth in the series."""
        if len(series) < 3:
            return []

        segments: list[GrowthSegment] = []
        i = 0

        while i < len(series) - 2:
            # Start a potential segment
            j = i + 1
            while j < len(series) and series[j] >= series[j - 1]:
                j += 1

            seg_len = j - i
            if seg_len >= 3:
                seg_data = series[i:j]
                slope, r_sq = self._linear_fit(seg_data)
                segments.append(GrowthSegment(
                    start_index=i,
                    end_index=j - 1,
                    start_value=float(series[i]),
                    end_value=float(series[j - 1]),
                    slope=slope,
                    r_squared=r_sq,
                ))
            i = max(j, i + 1)

        return segments

    # ── Accumulation Profiles ───────────────────────────────────────

    def _compute_accumulation_profiles(self, events: list[Any]) -> list[AccumulationProfile]:
        """Profile token accumulation by event type."""
        type_data: dict[str, list[int]] = {}
        for e in events:
            et = e.event_type
            if et not in type_data:
                type_data[et] = []
            type_data[et].append(e.tokens_in + e.tokens_out)

        profiles = []
        for et, values in type_data.items():
            total = sum(values)
            avg = statistics.mean(values) if values else 0

            # Determine trend
            if len(values) >= 4:
                mid = len(values) // 2
                early = statistics.mean(values[:mid])
                late = statistics.mean(values[mid:])
                if early > 0 and late > early * 1.3:
                    trend = TrendDirection.GROWING
                elif late < early * 0.7:
                    trend = TrendDirection.DECELERATING
                else:
                    trend = TrendDirection.STABLE
            else:
                trend = TrendDirection.STABLE

            profiles.append(AccumulationProfile(
                event_type=et,
                total_contribution=total,
                avg_contribution=avg,
                growth_trend=trend,
                count=len(values),
            ))

        return profiles

    # ── Scoring & Classification ────────────────────────────────────

    def _compute_leak_score(self, signals: list[LeakSignal]) -> float:
        """Compute composite leak score 0-100."""
        if not signals:
            return 0.0

        severity_weights = {
            LeakSeverity.NONE: 0,
            LeakSeverity.LOW: 15,
            LeakSeverity.MODERATE: 35,
            LeakSeverity.HIGH: 60,
            LeakSeverity.CRITICAL: 90,
        }

        weighted_sum = sum(
            severity_weights[s.severity] * s.confidence
            for s in signals
        )

        # Normalize: more signals = worse, but diminishing returns
        signal_factor = 1.0 + math.log(1 + len(signals)) * 0.3
        raw_score = weighted_sum * signal_factor / max(len(signals), 1)

        return min(100.0, max(0.0, raw_score))

    def _classify_severity(self, score: float) -> LeakSeverity:
        """Classify overall severity from leak score."""
        if score < 10:
            return LeakSeverity.NONE
        elif score < 30:
            return LeakSeverity.LOW
        elif score < 55:
            return LeakSeverity.MODERATE
        elif score < 80:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    # ── Recommendation Engine ───────────────────────────────────────

    def _generate_recommendations(self, signals: list[LeakSignal], forecast: ExhaustionForecast | None) -> list[str]:
        """Generate actionable recommendations based on detected leaks."""
        recs: list[str] = []

        categories_found = {s.category for s in signals}

        if LeakCategory.TOKEN_GROWTH in categories_found or LeakCategory.UNBOUNDED_HISTORY in categories_found:
            recs.append("Implement sliding window context management — keep only the last N turns in full, summarize earlier ones")

        if LeakCategory.CONTEXT_SNOWBALL in categories_found:
            recs.append("Add progressive summarization — aggressively compress context that's more than 5 turns old")

        if LeakCategory.TOOL_OUTPUT_HOARDING in categories_found:
            recs.append("Summarize tool outputs before appending to context — extract only decision-relevant data")

        if LeakCategory.REPETITION_BLOAT in categories_found:
            recs.append("Deduplicate repeated context — detect and merge overlapping information across turns")

        if LeakCategory.DEAD_REFERENCE_RETENTION in categories_found:
            recs.append("Implement error context cleanup — remove full error traces after acknowledgment, keep only summary")

        if LeakCategory.PAYLOAD_INFLATION in categories_found:
            recs.append("Add input payload trimming — use structured extraction to keep only relevant fields")

        if forecast and forecast.events_until_exhaustion is not None and forecast.events_until_exhaustion < 50:
            recs.append(f"URGENT: Context window exhaustion projected in ~{forecast.events_until_exhaustion} events — implement emergency context compression")

        if not recs:
            recs.append("No significant leaks detected — context management appears healthy")

        return recs

    # ── Utility Methods ─────────────────────────────────────────────

    @staticmethod
    def _cumulative(series: list[int]) -> list[int]:
        """Compute cumulative sum."""
        result = []
        total = 0
        for v in series:
            total += v
            result.append(total)
        return result

    @staticmethod
    def _linear_fit(series: list[float | int]) -> tuple[float, float]:
        """Simple linear regression. Returns (slope, r_squared)."""
        n = len(series)
        if n < 2:
            return 0.0, 0.0

        x_mean = (n - 1) / 2.0
        y_mean = statistics.mean(series)

        ss_xy = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        ss_xx = sum((i - x_mean) ** 2 for i in range(n))
        ss_yy = sum((series[i] - y_mean) ** 2 for i in range(n))

        if ss_xx == 0 or ss_yy == 0:
            return 0.0, 0.0

        slope = ss_xy / ss_xx
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

        return slope, r_squared

    @staticmethod
    def _r_squared(series: list[int]) -> float:
        """Compute R² of linear fit for series."""
        _, r2 = MemoryLeakDetector._linear_fit(series)
        return r2

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        """Jaccard similarity between two sets."""
        if not a and not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _rate_to_severity(rate: float, series: list[int]) -> LeakSeverity:
        """Map growth rate to severity relative to average."""
        avg = statistics.mean(series) if series else 1
        if avg == 0:
            return LeakSeverity.LOW
        ratio = rate / avg
        if ratio < 0.05:
            return LeakSeverity.LOW
        elif ratio < 0.15:
            return LeakSeverity.MODERATE
        elif ratio < 0.3:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    @staticmethod
    def _acceleration_to_severity(ratio: float) -> LeakSeverity:
        """Map acceleration ratio to severity."""
        if ratio < 2.0:
            return LeakSeverity.LOW
        elif ratio < 3.0:
            return LeakSeverity.MODERATE
        elif ratio < 5.0:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    @staticmethod
    def _pct_to_severity(pct: float) -> LeakSeverity:
        """Map percentage growth to severity."""
        if pct < 100:
            return LeakSeverity.LOW
        elif pct < 200:
            return LeakSeverity.MODERATE
        elif pct < 400:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    @staticmethod
    def _factor_to_severity(factor: float) -> LeakSeverity:
        """Map growth factor to severity."""
        if factor < 3.0:
            return LeakSeverity.LOW
        elif factor < 5.0:
            return LeakSeverity.MODERATE
        elif factor < 10.0:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    @staticmethod
    def _rate_fraction_to_severity(fraction: float) -> LeakSeverity:
        """Map rate fraction to severity."""
        if fraction < 0.3:
            return LeakSeverity.LOW
        elif fraction < 0.5:
            return LeakSeverity.MODERATE
        elif fraction < 0.7:
            return LeakSeverity.HIGH
        else:
            return LeakSeverity.CRITICAL

    def _empty_report(self, session_id: str, event_count: int) -> MemoryLeakReport:
        """Return empty report when insufficient data."""
        return MemoryLeakReport(
            session_id=session_id,
            total_events=event_count,
            leak_signals=[],
            severity=LeakSeverity.NONE,
            leak_score=0.0,
            exhaustion_forecast=None,
            accumulation_profiles=[],
            growth_segments=[],
            peak_token_usage=0,
            total_token_growth=0,
            growth_rate_per_event=0.0,
            recommendations=["Insufficient data for analysis (need at least 5 events)"],
        )
