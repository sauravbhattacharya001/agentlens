"""Agent Stamina Profiler for AgentLens.

Detects intra-session performance degradation ("agent fatigue") by analyzing
how metrics evolve as a session progresses. Identifies latency creep, token
inflation, error rate increases, and tool success decay over the course of
long-running agent sessions.

Answers: "Is my agent getting tired? When should I intervene?"

Usage::

    from agentlens.stamina import StaminaProfiler

    profiler = StaminaProfiler()
    report = profiler.profile(session)
    print(report.format_report())
    print(f"Stamina score: {report.stamina_score}/100")
    print(f"Fatigue onset at event #{report.fatigue_onset_index}")

Pure Python, stdlib only (math, dataclasses, enum).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────


class StaminaStatus(Enum):
    """Overall stamina classification."""
    FRESH = "fresh"                    # No degradation
    MILD_FATIGUE = "mild_fatigue"      # Slight slowdown
    MODERATE_FATIGUE = "moderate_fatigue"  # Noticeable degradation
    SEVERE_FATIGUE = "severe_fatigue"  # Major degradation
    EXHAUSTED = "exhausted"            # Critical failure pattern

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class FatigueSignal(Enum):
    """Type of fatigue signal detected."""
    LATENCY_CREEP = "latency_creep"
    TOKEN_INFLATION = "token_inflation"
    ERROR_RATE_INCREASE = "error_rate_increase"
    TOOL_SUCCESS_DECAY = "tool_success_decay"
    OUTPUT_SHRINKAGE = "output_shrinkage"
    DECISION_HESITATION = "decision_hesitation"


class InterventionUrgency(Enum):
    """How urgent is the need to intervene."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class WindowMetrics:
    """Metrics computed for a window of events."""
    window_index: int
    start_event: int
    end_event: int
    avg_latency_ms: float = 0.0
    avg_tokens: float = 0.0
    error_rate: float = 0.0
    tool_success_rate: float = 1.0
    avg_output_tokens: float = 0.0
    event_count: int = 0


@dataclass
class FatigueDetection:
    """A detected fatigue signal."""
    signal: FatigueSignal
    severity: float          # 0-1 normalized severity
    onset_window: int        # Window index where it started
    onset_event: int         # Approximate event index
    trend_slope: float       # Rate of change (positive = worsening)
    detail: str              # Human-readable explanation
    evidence: list[float] = field(default_factory=list)  # Per-window values


@dataclass
class InterventionPoint:
    """Recommended intervention point."""
    event_index: int
    urgency: InterventionUrgency
    reason: str
    recommendation: str


@dataclass
class StaminaReport:
    """Complete stamina assessment of a session."""
    session_id: str
    event_count: int
    window_count: int
    stamina_score: float              # 0-100 (100 = perfectly consistent)
    status: StaminaStatus
    fatigue_onset_index: int | None   # Event index where fatigue began
    signals: list[FatigueDetection] = field(default_factory=list)
    windows: list[WindowMetrics] = field(default_factory=list)
    interventions: list[InterventionPoint] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def format_report(self) -> str:
        """Generate a human-readable stamina report."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  AGENT STAMINA PROFILE")
        lines.append("=" * 60)
        lines.append(f"  Session:        {self.session_id}")
        lines.append(f"  Events:         {self.event_count}")
        lines.append(f"  Windows:        {self.window_count}")
        lines.append(f"  Stamina Score:  {self.stamina_score:.0f}/100")
        lines.append(f"  Status:         {self.status.label}")
        if self.fatigue_onset_index is not None:
            lines.append(f"  Fatigue Onset:  Event #{self.fatigue_onset_index}")
        lines.append("")

        if self.signals:
            lines.append("─" * 60)
            lines.append("  FATIGUE SIGNALS")
            lines.append("─" * 60)
            for sig in self.signals:
                icon = "⚠️" if sig.severity < 0.5 else "🔴"
                lines.append(f"  {icon} {sig.signal.value}")
                lines.append(f"     Severity: {sig.severity:.0%}")
                lines.append(f"     Onset: Window {sig.onset_window} (event ~{sig.onset_event})")
                lines.append(f"     Trend: {sig.trend_slope:+.4f}/window")
                lines.append(f"     {sig.detail}")
                lines.append("")

        if self.interventions:
            lines.append("─" * 60)
            lines.append("  INTERVENTION POINTS")
            lines.append("─" * 60)
            for ip in self.interventions:
                lines.append(f"  [{ip.urgency.value.upper()}] Event #{ip.event_index}")
                lines.append(f"     Reason: {ip.reason}")
                lines.append(f"     Action: {ip.recommendation}")
                lines.append("")

        if self.recommendations:
            lines.append("─" * 60)
            lines.append("  RECOMMENDATIONS")
            lines.append("─" * 60)
            for rec in self.recommendations:
                lines.append(f"  • {rec}")
            lines.append("")

        # Sparkline of latency trend
        if self.windows:
            latencies = [w.avg_latency_ms for w in self.windows]
            if max(latencies) > 0:
                lines.append("─" * 60)
                lines.append("  LATENCY TREND")
                lines.append("─" * 60)
                lines.append(f"  {_sparkline(latencies)}")
                lines.append(f"  Start: {latencies[0]:.0f}ms → End: {latencies[-1]:.0f}ms")
                lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "session_id": self.session_id,
            "event_count": self.event_count,
            "window_count": self.window_count,
            "stamina_score": round(self.stamina_score, 1),
            "status": self.status.value,
            "fatigue_onset_index": self.fatigue_onset_index,
            "signals": [
                {
                    "signal": s.signal.value,
                    "severity": round(s.severity, 3),
                    "onset_window": s.onset_window,
                    "onset_event": s.onset_event,
                    "trend_slope": round(s.trend_slope, 6),
                    "detail": s.detail,
                    "evidence": [round(v, 2) for v in s.evidence],
                }
                for s in self.signals
            ],
            "interventions": [
                {
                    "event_index": ip.event_index,
                    "urgency": ip.urgency.value,
                    "reason": ip.reason,
                    "recommendation": ip.recommendation,
                }
                for ip in self.interventions
            ],
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# ── Helpers ─────────────────────────────────────────────────────────


def _sparkline(values: list[float]) -> str:
    """Generate a sparkline string from values."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1.0
    return "".join(blocks[min(len(blocks) - 1, int((v - mn) / rng * (len(blocks) - 1)))] for v in values)


def _linear_regression(values: list[float]) -> tuple[float, float]:
    """Simple OLS linear regression. Returns (slope, r_squared)."""
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    xs = list(range(n))
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    ss_yy = sum((y - y_mean) ** 2 for y in values)
    if ss_xx == 0:
        return 0.0, 0.0
    slope = ss_xy / ss_xx
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy != 0 else 0.0
    return slope, r_squared


def _find_changepoint(values: list[float]) -> int | None:
    """Find the point where values start increasing significantly (CUSUM-like).
    
    Returns the index of the changepoint, or None if no changepoint found.
    """
    if len(values) < 3:
        return None
    mean_val = sum(values) / len(values)
    # Target shift: 0.5 standard deviations
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return None
    allowance = std * 0.5
    cusum = 0.0
    threshold = std * 2.0
    for i, v in enumerate(values):
        cusum = max(0, cusum + (v - mean_val) - allowance)
        if cusum > threshold:
            return i
    return None


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class StaminaConfig:
    """Configuration for the stamina profiler."""
    window_size: int = 5             # Events per window
    min_events: int = 10             # Minimum events to analyze
    latency_slope_threshold: float = 50.0   # ms/window slope to flag
    token_slope_threshold: float = 100.0    # tokens/window slope to flag
    error_rate_threshold: float = 0.1       # Error rate increase to flag
    tool_decay_threshold: float = 0.1       # Tool success decrease to flag
    r_squared_threshold: float = 0.3        # Minimum R² to trust trend


# ── Main Profiler ───────────────────────────────────────────────────


class StaminaProfiler:
    """Profiles agent stamina by detecting intra-session degradation.
    
    Divides a session into windows and tracks how performance metrics
    change over time. Identifies fatigue signals and recommends when
    to intervene (reset context, switch model, or terminate session).
    """

    def __init__(self, config: StaminaConfig | None = None):
        self.config = config or StaminaConfig()

    def profile(self, session: Any) -> StaminaReport:
        """Profile a session for stamina degradation.
        
        Args:
            session: A Session object (or any object with .session_id and .events).
        
        Returns:
            StaminaReport with fatigue analysis.
        """
        events = getattr(session, "events", [])
        session_id = getattr(session, "session_id", "unknown")
        
        if len(events) < self.config.min_events:
            return StaminaReport(
                session_id=session_id,
                event_count=len(events),
                window_count=0,
                stamina_score=100.0,
                status=StaminaStatus.FRESH,
                fatigue_onset_index=None,
                recommendations=["Session too short to profile (need ≥{} events).".format(
                    self.config.min_events
                )],
            )

        # Build windows
        windows = self._build_windows(events)
        if len(windows) < 3:
            return StaminaReport(
                session_id=session_id,
                event_count=len(events),
                window_count=len(windows),
                stamina_score=100.0,
                status=StaminaStatus.FRESH,
                fatigue_onset_index=None,
                windows=windows,
                recommendations=["Not enough windows for trend analysis (need ≥3)."],
            )

        # Detect fatigue signals
        signals = self._detect_signals(windows)
        
        # Calculate stamina score
        stamina_score = self._calculate_score(signals)
        
        # Determine status
        status = self._classify_status(stamina_score)
        
        # Find fatigue onset
        fatigue_onset = self._find_fatigue_onset(signals, windows)
        
        # Generate intervention points
        interventions = self._generate_interventions(signals, windows)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(signals, status, len(events))
        
        return StaminaReport(
            session_id=session_id,
            event_count=len(events),
            window_count=len(windows),
            stamina_score=stamina_score,
            status=status,
            fatigue_onset_index=fatigue_onset,
            signals=signals,
            windows=windows,
            interventions=interventions,
            recommendations=recommendations,
        )

    def profile_multi(self, sessions: list[Any]) -> dict[str, StaminaReport]:
        """Profile multiple sessions and return reports keyed by session_id."""
        return {
            getattr(s, "session_id", f"session_{i}"): self.profile(s)
            for i, s in enumerate(sessions)
        }

    def aggregate_stamina(self, sessions: list[Any]) -> dict[str, Any]:
        """Compute aggregate stamina statistics across sessions.
        
        Returns summary with avg score, fatigue frequency, common signals,
        and session length correlation.
        """
        reports = [self.profile(s) for s in sessions]
        if not reports:
            return {"sessions_analyzed": 0}

        scores = [r.stamina_score for r in reports]
        fatigued = [r for r in reports if r.fatigue_onset_index is not None]
        
        # Signal frequency
        signal_counts: dict[str, int] = {}
        for r in reports:
            for sig in r.signals:
                signal_counts[sig.signal.value] = signal_counts.get(sig.signal.value, 0) + 1
        
        # Session length vs stamina correlation
        lengths = [r.event_count for r in reports]
        if len(lengths) >= 3:
            length_slope, length_r2 = _linear_regression(
                [scores[i] for i in sorted(range(len(lengths)), key=lambda x: lengths[x])]
            )
        else:
            length_slope, length_r2 = 0.0, 0.0

        return {
            "sessions_analyzed": len(reports),
            "avg_stamina_score": round(sum(scores) / len(scores), 1),
            "min_stamina_score": round(min(scores), 1),
            "max_stamina_score": round(max(scores), 1),
            "fatigue_rate": round(len(fatigued) / len(reports), 3),
            "common_signals": sorted(signal_counts.items(), key=lambda x: -x[1]),
            "length_correlation": {
                "slope": round(length_slope, 4),
                "r_squared": round(length_r2, 4),
                "interpretation": (
                    "Longer sessions correlate with lower stamina"
                    if length_slope < -0.5 and length_r2 > 0.3
                    else "No strong length-stamina correlation"
                ),
            },
            "status_distribution": {
                status.value: sum(1 for r in reports if r.status == status)
                for status in StaminaStatus
            },
        }

    # ── Internal Methods ────────────────────────────────────────────

    def _build_windows(self, events: list[Any]) -> list[WindowMetrics]:
        """Divide events into fixed-size windows and compute metrics."""
        windows: list[WindowMetrics] = []
        ws = self.config.window_size
        
        for i in range(0, len(events), ws):
            chunk = events[i:i + ws]
            if not chunk:
                continue
            
            latencies: list[float] = []
            tokens: list[float] = []
            output_tokens: list[float] = []
            errors = 0
            tool_calls = 0
            tool_successes = 0
            
            for ev in chunk:
                dur = getattr(ev, "duration_ms", None)
                if dur is not None:
                    latencies.append(float(dur))
                
                t_in = getattr(ev, "tokens_in", 0) or 0
                t_out = getattr(ev, "tokens_out", 0) or 0
                tokens.append(float(t_in + t_out))
                output_tokens.append(float(t_out))
                
                ev_type = getattr(ev, "event_type", "")
                if ev_type == "error":
                    errors += 1
                
                tc = getattr(ev, "tool_call", None)
                if tc is not None:
                    tool_calls += 1
                    if getattr(tc, "tool_output", None) is not None:
                        tool_successes += 1
            
            wm = WindowMetrics(
                window_index=len(windows),
                start_event=i,
                end_event=i + len(chunk) - 1,
                avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
                avg_tokens=sum(tokens) / len(tokens) if tokens else 0.0,
                error_rate=errors / len(chunk),
                tool_success_rate=(tool_successes / tool_calls) if tool_calls > 0 else 1.0,
                avg_output_tokens=sum(output_tokens) / len(output_tokens) if output_tokens else 0.0,
                event_count=len(chunk),
            )
            windows.append(wm)
        
        return windows

    def _detect_signals(self, windows: list[WindowMetrics]) -> list[FatigueDetection]:
        """Analyze window trends to detect fatigue signals."""
        signals: list[FatigueDetection] = []
        
        # Latency creep
        latencies = [w.avg_latency_ms for w in windows]
        sig = self._check_increasing_trend(
            latencies,
            FatigueSignal.LATENCY_CREEP,
            self.config.latency_slope_threshold,
            "Average latency increasing over session lifetime",
            windows,
        )
        if sig:
            signals.append(sig)
        
        # Token inflation
        tokens = [w.avg_tokens for w in windows]
        sig = self._check_increasing_trend(
            tokens,
            FatigueSignal.TOKEN_INFLATION,
            self.config.token_slope_threshold,
            "Token usage growing as session progresses",
            windows,
        )
        if sig:
            signals.append(sig)
        
        # Error rate increase
        errors = [w.error_rate for w in windows]
        sig = self._check_increasing_trend(
            errors,
            FatigueSignal.ERROR_RATE_INCREASE,
            self.config.error_rate_threshold,
            "Error frequency increasing over time",
            windows,
        )
        if sig:
            signals.append(sig)
        
        # Tool success decay (decreasing trend)
        tool_rates = [w.tool_success_rate for w in windows]
        sig = self._check_decreasing_trend(
            tool_rates,
            FatigueSignal.TOOL_SUCCESS_DECAY,
            self.config.tool_decay_threshold,
            "Tool call success rate declining",
            windows,
        )
        if sig:
            signals.append(sig)
        
        # Output shrinkage (decreasing output tokens)
        output_toks = [w.avg_output_tokens for w in windows]
        if output_toks[0] > 0:  # Only if there are output tokens
            sig = self._check_decreasing_trend(
                output_toks,
                FatigueSignal.OUTPUT_SHRINKAGE,
                output_toks[0] * 0.3,  # 30% decline threshold
                "Agent producing shorter responses over time",
                windows,
            )
            if sig:
                signals.append(sig)
        
        return signals

    def _check_increasing_trend(
        self,
        values: list[float],
        signal: FatigueSignal,
        threshold: float,
        description: str,
        windows: list[WindowMetrics],
    ) -> FatigueDetection | None:
        """Check if values show a significant increasing trend."""
        slope, r2 = _linear_regression(values)
        if slope <= 0 or r2 < self.config.r_squared_threshold:
            return None
        if slope < threshold and r2 < 0.6:
            return None
        
        # Find changepoint
        cp = _find_changepoint(values)
        onset_window = cp if cp is not None else 0
        onset_event = windows[onset_window].start_event if onset_window < len(windows) else 0
        
        # Severity: based on how much the metric increased end vs start
        if values[0] > 0:
            increase_ratio = (values[-1] - values[0]) / values[0]
        else:
            increase_ratio = 1.0 if values[-1] > 0 else 0.0
        severity = min(1.0, max(0.0, increase_ratio))
        
        return FatigueDetection(
            signal=signal,
            severity=severity,
            onset_window=onset_window,
            onset_event=onset_event,
            trend_slope=slope,
            detail=f"{description} (slope={slope:.2f}/window, R²={r2:.2f})",
            evidence=values,
        )

    def _check_decreasing_trend(
        self,
        values: list[float],
        signal: FatigueSignal,
        threshold: float,
        description: str,
        windows: list[WindowMetrics],
    ) -> FatigueDetection | None:
        """Check if values show a significant decreasing trend."""
        slope, r2 = _linear_regression(values)
        if slope >= 0 or r2 < self.config.r_squared_threshold:
            return None
        if abs(slope) < threshold and r2 < 0.6:
            return None
        
        cp = _find_changepoint([-v for v in values])  # Invert for changepoint
        onset_window = cp if cp is not None else 0
        onset_event = windows[onset_window].start_event if onset_window < len(windows) else 0
        
        # Severity: based on magnitude of decrease
        if values[0] > 0:
            decrease_ratio = (values[0] - values[-1]) / values[0]
        else:
            decrease_ratio = 0.0
        severity = min(1.0, max(0.0, decrease_ratio))
        
        return FatigueDetection(
            signal=signal,
            severity=severity,
            onset_window=onset_window,
            onset_event=onset_event,
            trend_slope=slope,
            detail=f"{description} (slope={slope:.2f}/window, R²={r2:.2f})",
            evidence=values,
        )

    def _calculate_score(self, signals: list[FatigueDetection]) -> float:
        """Calculate stamina score (100 = perfect, 0 = exhausted)."""
        if not signals:
            return 100.0
        
        # Each signal reduces score based on severity
        penalty = sum(s.severity * 25 for s in signals)
        return max(0.0, min(100.0, 100.0 - penalty))

    def _classify_status(self, score: float) -> StaminaStatus:
        """Classify stamina status from score."""
        if score >= 90:
            return StaminaStatus.FRESH
        elif score >= 70:
            return StaminaStatus.MILD_FATIGUE
        elif score >= 50:
            return StaminaStatus.MODERATE_FATIGUE
        elif score >= 25:
            return StaminaStatus.SEVERE_FATIGUE
        else:
            return StaminaStatus.EXHAUSTED

    def _find_fatigue_onset(
        self,
        signals: list[FatigueDetection],
        windows: list[WindowMetrics],
    ) -> int | None:
        """Find the earliest event index where fatigue started."""
        if not signals:
            return None
        onset_events = [s.onset_event for s in signals]
        return min(onset_events) if onset_events else None

    def _generate_interventions(
        self,
        signals: list[FatigueDetection],
        windows: list[WindowMetrics],
    ) -> list[InterventionPoint]:
        """Generate recommended intervention points."""
        interventions: list[InterventionPoint] = []
        
        if not signals:
            return interventions
        
        # Primary intervention at fatigue onset
        onset_events = sorted(s.onset_event for s in signals)
        if onset_events:
            earliest = onset_events[0]
            max_severity = max(s.severity for s in signals)
            
            if max_severity >= 0.7:
                urgency = InterventionUrgency.CRITICAL
            elif max_severity >= 0.5:
                urgency = InterventionUrgency.HIGH
            elif max_severity >= 0.3:
                urgency = InterventionUrgency.MEDIUM
            else:
                urgency = InterventionUrgency.LOW
            
            signal_names = ", ".join(s.signal.value for s in signals)
            interventions.append(InterventionPoint(
                event_index=earliest,
                urgency=urgency,
                reason=f"Fatigue signals detected: {signal_names}",
                recommendation=self._pick_intervention(signals),
            ))
        
        # If severe, recommend immediate stop at last window
        if any(s.severity >= 0.8 for s in signals) and windows:
            last_window = windows[-1]
            interventions.append(InterventionPoint(
                event_index=last_window.start_event,
                urgency=InterventionUrgency.CRITICAL,
                reason="Severe degradation in final window",
                recommendation="Terminate session immediately and start fresh",
            ))
        
        return interventions

    def _pick_intervention(self, signals: list[FatigueDetection]) -> str:
        """Choose the most appropriate intervention based on signals."""
        signal_types = {s.signal for s in signals}
        
        if FatigueSignal.TOKEN_INFLATION in signal_types:
            return "Reset or summarize conversation context to reduce token bloat"
        if FatigueSignal.ERROR_RATE_INCREASE in signal_types:
            return "Switch to a more capable model or reduce task complexity"
        if FatigueSignal.LATENCY_CREEP in signal_types:
            return "Start a new session — context window may be degrading performance"
        if FatigueSignal.TOOL_SUCCESS_DECAY in signal_types:
            return "Verify tool availability and reduce parallel tool calls"
        return "Consider starting a fresh session to restore performance"

    def _generate_recommendations(
        self,
        signals: list[FatigueDetection],
        status: StaminaStatus,
        event_count: int,
    ) -> list[str]:
        """Generate actionable recommendations."""
        recs: list[str] = []
        
        if status == StaminaStatus.FRESH:
            recs.append("Session performance is consistent — no intervention needed.")
            return recs
        
        if status in (StaminaStatus.SEVERE_FATIGUE, StaminaStatus.EXHAUSTED):
            recs.append(
                f"⚠️ Agent severely fatigued after {event_count} events. "
                "Consider shorter session limits."
            )
        
        for sig in signals:
            if sig.signal == FatigueSignal.LATENCY_CREEP:
                recs.append(
                    f"Latency increased {sig.severity:.0%} over session. "
                    "Set a max-events limit or implement context summarization."
                )
            elif sig.signal == FatigueSignal.TOKEN_INFLATION:
                recs.append(
                    f"Token usage grew {sig.severity:.0%}. "
                    "Use rolling context windows or periodic summarization."
                )
            elif sig.signal == FatigueSignal.ERROR_RATE_INCREASE:
                recs.append(
                    "Error rate climbed during session. "
                    "Implement automatic fallback to a fresh context after N errors."
                )
            elif sig.signal == FatigueSignal.TOOL_SUCCESS_DECAY:
                recs.append(
                    "Tool calls became less reliable over time. "
                    "Check for rate limiting or context confusion in tool descriptions."
                )
            elif sig.signal == FatigueSignal.OUTPUT_SHRINKAGE:
                recs.append(
                    "Agent responses became shorter — possible context pressure. "
                    "Trim older context or split into sub-tasks."
                )
        
        # General recommendation based on event count
        if event_count > 50 and status != StaminaStatus.FRESH:
            recs.append(
                f"Consider splitting sessions at ~{event_count // 2} events "
                "based on observed fatigue onset patterns."
            )
        
        return recs
