"""Agent Failure Forecaster for AgentLens.

Predicts upcoming session failures by analyzing leading indicators across
historical session data. Uses multi-signal analysis to identify sessions and
agents that are trending toward failure before it happens.

Answers: "Which agents are about to fail? How soon? What should I do?"

Usage::

    from agentlens.failure_forecast import FailureForecaster, SessionSnapshot

    forecaster = FailureForecaster()

    # Feed session snapshots
    forecaster.add_snapshot(SessionSnapshot(
        session_id="sess-001",
        agent_id="agent-alpha",
        timestamp=datetime.now(timezone.utc),
        error_count=2,
        total_events=50,
        avg_latency_ms=320.0,
        retry_count=3,
        tool_failures=1,
        tool_calls=12,
        tokens_used=4500,
        token_budget=8000,
    ))

    # Get failure predictions
    report = forecaster.predict()
    for prediction in report.predictions:
        print(f"{prediction.agent_id}: {prediction.failure_probability:.0%} "
              f"risk in {prediction.estimated_events_to_failure} events")
        print(f"  Signals: {[s.signal_type.value for s in prediction.signals]}")
        print(f"  Action: {prediction.recommended_action}")

    # CLI-friendly output
    print(report.format_report())

Pure Python, stdlib only (math, statistics, dataclasses, enum).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class RiskLevel(Enum):
    """Predicted failure risk classification."""
    NOMINAL = "nominal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"
    IMMINENT = "imminent"

    @property
    def label(self) -> str:
        return self.value.title()

    @property
    def severity(self) -> int:
        return {
            "nominal": 0,
            "elevated": 1,
            "high": 2,
            "critical": 3,
            "imminent": 4,
        }[self.value]


class LeadingIndicator(Enum):
    """Type of leading failure indicator detected."""
    ERROR_ACCELERATION = "error_acceleration"
    LATENCY_SPIKE = "latency_spike"
    RETRY_ESCALATION = "retry_escalation"
    TOOL_SUCCESS_DECAY = "tool_success_decay"
    TOKEN_BUDGET_DEPLETION = "token_budget_depletion"
    EVENT_RATE_STALL = "event_rate_stall"
    CASCADING_ERRORS = "cascading_errors"
    RESPONSE_DEGRADATION = "response_degradation"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class RecommendedAction(Enum):
    """Recommended intervention action."""
    MONITOR = "monitor"
    ALERT_OWNER = "alert_owner"
    INCREASE_BUDGET = "increase_budget"
    RESTART_SESSION = "restart_session"
    FAILOVER = "failover"
    IMMEDIATE_INTERVENTION = "immediate_intervention"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class SessionSnapshot:
    """A point-in-time observation of a session's health metrics."""
    session_id: str
    agent_id: str
    timestamp: datetime
    error_count: int = 0
    total_events: int = 0
    avg_latency_ms: float = 0.0
    retry_count: int = 0
    tool_failures: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    token_budget: int = 0
    response_quality_score: float = 1.0  # 0.0 to 1.0
    consecutive_errors: int = 0
    event_rate_per_min: float = 0.0


@dataclass
class IndicatorSignal:
    """A detected leading indicator signal."""
    signal_type: LeadingIndicator
    strength: float  # 0.0 to 1.0
    trend_direction: str  # "rising", "stable", "falling"
    evidence: str
    measured_value: float = 0.0
    threshold: float = 0.0


@dataclass
class FailurePrediction:
    """A failure prediction for a specific session/agent."""
    session_id: str
    agent_id: str
    failure_probability: float  # 0.0 to 1.0
    risk_level: RiskLevel
    estimated_events_to_failure: Optional[int]
    estimated_time_to_failure: Optional[timedelta]
    signals: List[IndicatorSignal] = field(default_factory=list)
    recommended_action: RecommendedAction = RecommendedAction.MONITOR
    confidence: float = 0.0  # 0.0 to 1.0
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "failure_probability": round(self.failure_probability, 4),
            "risk_level": self.risk_level.value,
            "estimated_events_to_failure": self.estimated_events_to_failure,
            "estimated_time_to_failure_sec": (
                self.estimated_time_to_failure.total_seconds()
                if self.estimated_time_to_failure else None
            ),
            "signals": [
                {
                    "type": s.signal_type.value,
                    "strength": round(s.strength, 3),
                    "trend": s.trend_direction,
                    "evidence": s.evidence,
                }
                for s in self.signals
            ],
            "recommended_action": self.recommended_action.value,
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
        }


@dataclass
class ForecastReport:
    """Complete failure forecast report across all monitored sessions."""
    predictions: List[FailurePrediction] = field(default_factory=list)
    total_sessions_analyzed: int = 0
    sessions_at_risk: int = 0
    highest_risk_agent: Optional[str] = None
    fleet_health_score: float = 100.0  # 0 to 100
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def critical_predictions(self) -> List[FailurePrediction]:
        return [p for p in self.predictions if p.risk_level.severity >= 3]

    @property
    def elevated_predictions(self) -> List[FailurePrediction]:
        return [p for p in self.predictions if p.risk_level.severity >= 1]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_sessions_analyzed": self.total_sessions_analyzed,
            "sessions_at_risk": self.sessions_at_risk,
            "highest_risk_agent": self.highest_risk_agent,
            "fleet_health_score": round(self.fleet_health_score, 1),
            "generated_at": self.generated_at.isoformat(),
            "predictions": [p.to_dict() for p in self.predictions],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_report(self) -> str:
        lines: List[str] = []
        lines.append("=" * 70)
        lines.append("  AGENT FAILURE FORECAST REPORT")
        lines.append("=" * 70)
        lines.append("")
        lines.append(f"  Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"  Sessions Analyzed: {self.total_sessions_analyzed}")
        lines.append(f"  Sessions At Risk: {self.sessions_at_risk}")
        lines.append(f"  Fleet Health Score: {self.fleet_health_score:.1f}/100")
        lines.append("")

        if not self.predictions:
            lines.append("  ✅ No failure risks detected. All systems nominal.")
            lines.append("")
            lines.append("=" * 70)
            return "\n".join(lines)

        # Sort by risk (highest first)
        sorted_preds = sorted(
            self.predictions, key=lambda p: p.failure_probability, reverse=True
        )

        lines.append("─" * 70)
        lines.append("  PREDICTIONS (sorted by risk)")
        lines.append("─" * 70)

        for i, pred in enumerate(sorted_preds, 1):
            risk_icon = {
                RiskLevel.NOMINAL: "🟢",
                RiskLevel.ELEVATED: "🟡",
                RiskLevel.HIGH: "🟠",
                RiskLevel.CRITICAL: "🔴",
                RiskLevel.IMMINENT: "⚫",
            }.get(pred.risk_level, "⚪")

            lines.append("")
            lines.append(f"  {risk_icon} #{i} Agent: {pred.agent_id} | Session: {pred.session_id}")
            lines.append(f"     Risk: {pred.risk_level.label} ({pred.failure_probability:.0%})")
            lines.append(f"     Confidence: {pred.confidence:.0%}")

            if pred.estimated_events_to_failure is not None:
                lines.append(f"     Est. Events to Failure: ~{pred.estimated_events_to_failure}")
            if pred.estimated_time_to_failure is not None:
                mins = pred.estimated_time_to_failure.total_seconds() / 60
                if mins < 60:
                    lines.append(f"     Est. Time to Failure: ~{mins:.0f} min")
                else:
                    lines.append(f"     Est. Time to Failure: ~{mins / 60:.1f} hr")

            lines.append(f"     Action: {pred.recommended_action.label}")

            if pred.signals:
                lines.append("     Signals:")
                for sig in pred.signals:
                    arrow = {"rising": "↑", "stable": "→", "falling": "↓"}.get(
                        sig.trend_direction, "?"
                    )
                    lines.append(
                        f"       • {sig.signal_type.label} [{arrow}] "
                        f"strength={sig.strength:.2f}: {sig.evidence}"
                    )

            if pred.explanation:
                lines.append(f"     Summary: {pred.explanation}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)


# ── Core Engine ─────────────────────────────────────────────────────


class FailureForecaster:
    """Autonomous failure prediction engine.

    Analyzes sequences of session snapshots to detect leading indicators
    of failure and predict when sessions/agents will fail.

    Parameters
    ----------
    min_snapshots : int
        Minimum snapshots per session before generating a prediction.
    error_rate_threshold : float
        Error rate above which triggers error acceleration signal.
    latency_spike_factor : float
        Multiple of baseline latency that counts as a spike.
    retry_escalation_threshold : int
        Retry count per snapshot that signals escalation.
    token_depletion_warning : float
        Fraction of budget used that triggers depletion warning (0-1).
    tool_failure_threshold : float
        Tool failure rate above which triggers tool success decay signal.
    """

    def __init__(
        self,
        min_snapshots: int = 3,
        error_rate_threshold: float = 0.15,
        latency_spike_factor: float = 2.5,
        retry_escalation_threshold: int = 5,
        token_depletion_warning: float = 0.80,
        tool_failure_threshold: float = 0.25,
    ) -> None:
        self.min_snapshots = max(2, min_snapshots)
        self.error_rate_threshold = error_rate_threshold
        self.latency_spike_factor = latency_spike_factor
        self.retry_escalation_threshold = retry_escalation_threshold
        self.token_depletion_warning = token_depletion_warning
        self.tool_failure_threshold = tool_failure_threshold

        # session_id -> list of snapshots (ordered by timestamp)
        self._snapshots: Dict[str, List[SessionSnapshot]] = {}

    def add_snapshot(self, snapshot: SessionSnapshot) -> None:
        """Record a session snapshot for analysis."""
        if snapshot.session_id not in self._snapshots:
            self._snapshots[snapshot.session_id] = []
        self._snapshots[snapshot.session_id].append(snapshot)
        # Keep sorted by timestamp
        self._snapshots[snapshot.session_id].sort(key=lambda s: s.timestamp)

    def add_snapshots(self, snapshots: List[SessionSnapshot]) -> None:
        """Record multiple snapshots at once."""
        for snap in snapshots:
            self.add_snapshot(snap)

    def clear(self) -> None:
        """Reset all stored snapshots."""
        self._snapshots.clear()

    @property
    def session_count(self) -> int:
        return len(self._snapshots)

    def predict(self) -> ForecastReport:
        """Generate failure predictions for all monitored sessions."""
        predictions: List[FailurePrediction] = []

        for session_id, snapshots in self._snapshots.items():
            if len(snapshots) < self.min_snapshots:
                continue
            prediction = self._analyze_session(session_id, snapshots)
            if prediction is not None:
                predictions.append(prediction)

        # Compute fleet health
        total = len(self._snapshots)
        at_risk = sum(1 for p in predictions if p.risk_level.severity >= 1)
        fleet_health = 100.0
        if total > 0 and predictions:
            risk_sum = sum(p.failure_probability for p in predictions)
            fleet_health = max(0.0, 100.0 * (1 - risk_sum / max(total, 1)))

        highest_risk = None
        if predictions:
            worst = max(predictions, key=lambda p: p.failure_probability)
            highest_risk = worst.agent_id

        report = ForecastReport(
            predictions=predictions,
            total_sessions_analyzed=total,
            sessions_at_risk=at_risk,
            highest_risk_agent=highest_risk,
            fleet_health_score=min(100.0, max(0.0, fleet_health)),
        )
        return report

    def predict_session(self, session_id: str) -> Optional[FailurePrediction]:
        """Generate a failure prediction for a specific session."""
        snapshots = self._snapshots.get(session_id)
        if not snapshots or len(snapshots) < self.min_snapshots:
            return None
        return self._analyze_session(session_id, snapshots)

    def _analyze_session(
        self, session_id: str, snapshots: List[SessionSnapshot]
    ) -> Optional[FailurePrediction]:
        """Run all indicator detectors on a session's snapshot history."""
        agent_id = snapshots[-1].agent_id
        signals: List[IndicatorSignal] = []

        # Run each detector
        sig = self._detect_error_acceleration(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_latency_spike(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_retry_escalation(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_tool_success_decay(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_token_depletion(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_event_rate_stall(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_cascading_errors(snapshots)
        if sig:
            signals.append(sig)

        sig = self._detect_response_degradation(snapshots)
        if sig:
            signals.append(sig)

        if not signals:
            return None

        # Compute composite failure probability
        failure_prob = self._compute_failure_probability(signals)
        risk_level = self._classify_risk(failure_prob)
        action = self._recommend_action(risk_level, signals)
        confidence = self._compute_confidence(snapshots, signals)
        events_to_failure = self._estimate_events_to_failure(snapshots, signals)
        time_to_failure = self._estimate_time_to_failure(snapshots, events_to_failure)
        explanation = self._generate_explanation(signals, risk_level)

        return FailurePrediction(
            session_id=session_id,
            agent_id=agent_id,
            failure_probability=failure_prob,
            risk_level=risk_level,
            estimated_events_to_failure=events_to_failure,
            estimated_time_to_failure=time_to_failure,
            signals=signals,
            recommended_action=action,
            confidence=confidence,
            explanation=explanation,
        )

    # ── Indicator Detectors ─────────────────────────────────────────

    def _detect_error_acceleration(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect accelerating error rate over time."""
        if len(snapshots) < 2:
            return None

        error_rates: List[float] = []
        for snap in snapshots:
            rate = snap.error_count / max(snap.total_events, 1)
            error_rates.append(rate)

        # Check if error rate is rising
        recent_half = error_rates[len(error_rates) // 2:]
        early_half = error_rates[:len(error_rates) // 2]

        if not recent_half or not early_half:
            return None

        recent_avg = statistics.mean(recent_half)
        early_avg = statistics.mean(early_half)

        if recent_avg <= self.error_rate_threshold and early_avg <= self.error_rate_threshold:
            return None

        acceleration = recent_avg - early_avg
        if acceleration <= 0:
            return None

        # Strength based on how far above threshold
        strength = min(1.0, acceleration / self.error_rate_threshold)

        trend = "rising" if acceleration > 0.01 else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.ERROR_ACCELERATION,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Error rate rose from {early_avg:.1%} to {recent_avg:.1%} "
                f"(+{acceleration:.1%} acceleration)"
            ),
            measured_value=recent_avg,
            threshold=self.error_rate_threshold,
        )

    def _detect_latency_spike(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect latency spiking above baseline."""
        latencies = [s.avg_latency_ms for s in snapshots if s.avg_latency_ms > 0]
        if len(latencies) < 3:
            return None

        baseline = statistics.median(latencies[:len(latencies) // 2 + 1])
        if baseline <= 0:
            return None

        recent = latencies[-1]
        spike_ratio = recent / baseline

        if spike_ratio < self.latency_spike_factor:
            return None

        strength = min(1.0, (spike_ratio - 1) / (self.latency_spike_factor * 2))

        # Trend: check if last 3 are rising
        last_3 = latencies[-3:]
        trend = "rising" if last_3 == sorted(last_3) else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.LATENCY_SPIKE,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Latency at {recent:.0f}ms vs baseline {baseline:.0f}ms "
                f"({spike_ratio:.1f}x spike)"
            ),
            measured_value=recent,
            threshold=baseline * self.latency_spike_factor,
        )

    def _detect_retry_escalation(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect escalating retry counts."""
        retries = [s.retry_count for s in snapshots]
        if len(retries) < 2:
            return None

        recent_retries = retries[-1]
        if recent_retries < self.retry_escalation_threshold:
            return None

        avg_retries = statistics.mean(retries[:-1])
        escalation = recent_retries - avg_retries

        if escalation <= 0:
            return None

        strength = min(1.0, escalation / (self.retry_escalation_threshold * 2))
        trend = "rising" if retries[-1] > retries[-2] else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.RETRY_ESCALATION,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Retries at {recent_retries} vs average {avg_retries:.1f} "
                f"(+{escalation:.1f} escalation)"
            ),
            measured_value=float(recent_retries),
            threshold=float(self.retry_escalation_threshold),
        )

    def _detect_tool_success_decay(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect declining tool call success rate."""
        rates: List[float] = []
        for snap in snapshots:
            if snap.tool_calls > 0:
                success_rate = 1.0 - (snap.tool_failures / snap.tool_calls)
                rates.append(success_rate)

        if len(rates) < 2:
            return None

        recent = rates[-1]
        failure_rate = 1.0 - recent

        if failure_rate < self.tool_failure_threshold:
            return None

        early_avg = statistics.mean(rates[:len(rates) // 2 + 1])
        decay = early_avg - recent

        if decay <= 0:
            return None

        strength = min(1.0, decay / 0.5)
        trend = "rising" if rates[-1] < rates[-2] else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.TOOL_SUCCESS_DECAY,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Tool success rate dropped to {recent:.0%} from {early_avg:.0%} "
                f"(failure rate: {failure_rate:.0%})"
            ),
            measured_value=failure_rate,
            threshold=self.tool_failure_threshold,
        )

    def _detect_token_depletion(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect token budget depletion trend."""
        latest = snapshots[-1]
        if latest.token_budget <= 0:
            return None

        usage_fraction = latest.tokens_used / latest.token_budget
        if usage_fraction < self.token_depletion_warning:
            return None

        # Check velocity of token consumption
        if len(snapshots) >= 2:
            prev = snapshots[-2]
            if prev.token_budget > 0:
                prev_fraction = prev.tokens_used / prev.token_budget
                velocity = usage_fraction - prev_fraction
            else:
                velocity = 0.0
        else:
            velocity = 0.0

        strength = min(1.0, (usage_fraction - self.token_depletion_warning) /
                       (1.0 - self.token_depletion_warning))

        trend = "rising" if velocity > 0.01 else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.TOKEN_BUDGET_DEPLETION,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Token budget {usage_fraction:.0%} consumed "
                f"({latest.tokens_used}/{latest.token_budget} tokens, "
                f"velocity: +{velocity:.1%}/snapshot)"
            ),
            measured_value=usage_fraction,
            threshold=self.token_depletion_warning,
        )

    def _detect_event_rate_stall(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect agent event processing stalling (throughput drop)."""
        rates = [s.event_rate_per_min for s in snapshots if s.event_rate_per_min > 0]
        if len(rates) < 3:
            return None

        baseline = statistics.mean(rates[:len(rates) // 2 + 1])
        if baseline <= 0:
            return None

        recent = rates[-1]
        drop_ratio = 1.0 - (recent / baseline)

        if drop_ratio < 0.5:  # Need at least 50% drop
            return None

        strength = min(1.0, drop_ratio)
        trend = "falling"

        return IndicatorSignal(
            signal_type=LeadingIndicator.EVENT_RATE_STALL,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Event rate dropped to {recent:.1f}/min from baseline {baseline:.1f}/min "
                f"({drop_ratio:.0%} reduction)"
            ),
            measured_value=recent,
            threshold=baseline * 0.5,
        )

    def _detect_cascading_errors(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect cascading consecutive errors pattern."""
        consec = [s.consecutive_errors for s in snapshots]
        if not consec:
            return None

        latest_consec = consec[-1]
        if latest_consec < 3:
            return None

        # Check if consecutive errors are growing
        is_growing = len(consec) >= 2 and consec[-1] > consec[-2]
        strength = min(1.0, latest_consec / 10.0)
        trend = "rising" if is_growing else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.CASCADING_ERRORS,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"{latest_consec} consecutive errors detected "
                f"({'accelerating' if is_growing else 'sustained'})"
            ),
            measured_value=float(latest_consec),
            threshold=3.0,
        )

    def _detect_response_degradation(
        self, snapshots: List[SessionSnapshot]
    ) -> Optional[IndicatorSignal]:
        """Detect declining response quality scores."""
        scores = [s.response_quality_score for s in snapshots if s.response_quality_score > 0]
        if len(scores) < 3:
            return None

        early_avg = statistics.mean(scores[:len(scores) // 2 + 1])
        recent_avg = statistics.mean(scores[len(scores) // 2:])

        decline = early_avg - recent_avg
        if decline < 0.15:  # Need at least 15% quality drop
            return None

        strength = min(1.0, decline / 0.5)
        trend = "falling" if scores[-1] < scores[-2] else "stable"

        return IndicatorSignal(
            signal_type=LeadingIndicator.RESPONSE_DEGRADATION,
            strength=strength,
            trend_direction=trend,
            evidence=(
                f"Response quality dropped from {early_avg:.2f} to {recent_avg:.2f} "
                f"({decline:.0%} decline)"
            ),
            measured_value=recent_avg,
            threshold=early_avg - 0.15,
        )

    # ── Scoring & Classification ────────────────────────────────────

    def _compute_failure_probability(self, signals: List[IndicatorSignal]) -> float:
        """Compute composite failure probability from signals.

        Uses a weighted combination where more signals and higher strengths
        increase probability non-linearly.
        """
        if not signals:
            return 0.0

        # Weight by signal type criticality
        weights = {
            LeadingIndicator.CASCADING_ERRORS: 1.5,
            LeadingIndicator.ERROR_ACCELERATION: 1.3,
            LeadingIndicator.TOKEN_BUDGET_DEPLETION: 1.2,
            LeadingIndicator.TOOL_SUCCESS_DECAY: 1.1,
            LeadingIndicator.RETRY_ESCALATION: 1.0,
            LeadingIndicator.LATENCY_SPIKE: 0.8,
            LeadingIndicator.EVENT_RATE_STALL: 0.9,
            LeadingIndicator.RESPONSE_DEGRADATION: 1.0,
        }

        weighted_sum = sum(
            sig.strength * weights.get(sig.signal_type, 1.0) for sig in signals
        )

        # Multi-signal amplification: more signals = worse
        signal_count_factor = 1.0 + 0.15 * (len(signals) - 1)
        raw = weighted_sum * signal_count_factor / (len(signals) + 2)

        # Sigmoid-like clamping
        probability = min(0.99, max(0.01, raw))
        return probability

    def _classify_risk(self, probability: float) -> RiskLevel:
        """Classify failure probability into risk levels."""
        if probability >= 0.85:
            return RiskLevel.IMMINENT
        elif probability >= 0.65:
            return RiskLevel.CRITICAL
        elif probability >= 0.45:
            return RiskLevel.HIGH
        elif probability >= 0.25:
            return RiskLevel.ELEVATED
        else:
            return RiskLevel.NOMINAL

    def _recommend_action(
        self, risk: RiskLevel, signals: List[IndicatorSignal]
    ) -> RecommendedAction:
        """Determine recommended action based on risk and signals."""
        if risk == RiskLevel.IMMINENT:
            return RecommendedAction.IMMEDIATE_INTERVENTION
        elif risk == RiskLevel.CRITICAL:
            # Check if failover is more appropriate
            has_cascade = any(
                s.signal_type == LeadingIndicator.CASCADING_ERRORS for s in signals
            )
            return RecommendedAction.FAILOVER if has_cascade else RecommendedAction.RESTART_SESSION
        elif risk == RiskLevel.HIGH:
            has_token = any(
                s.signal_type == LeadingIndicator.TOKEN_BUDGET_DEPLETION for s in signals
            )
            return RecommendedAction.INCREASE_BUDGET if has_token else RecommendedAction.ALERT_OWNER
        elif risk == RiskLevel.ELEVATED:
            return RecommendedAction.ALERT_OWNER
        else:
            return RecommendedAction.MONITOR

    def _compute_confidence(
        self, snapshots: List[SessionSnapshot], signals: List[IndicatorSignal]
    ) -> float:
        """Compute prediction confidence based on data quality."""
        # More data = more confidence
        data_confidence = min(1.0, len(snapshots) / 10.0)

        # Consistent signals = more confidence
        if signals:
            rising_count = sum(1 for s in signals if s.trend_direction == "rising")
            trend_consistency = rising_count / len(signals)
        else:
            trend_consistency = 0.0

        # Combined confidence
        confidence = 0.5 * data_confidence + 0.5 * trend_consistency
        return min(1.0, max(0.1, confidence))

    def _estimate_events_to_failure(
        self, snapshots: List[SessionSnapshot], signals: List[IndicatorSignal]
    ) -> Optional[int]:
        """Estimate how many more events until likely failure."""
        if not signals:
            return None

        # Use error rate trend to project
        error_rates = [s.error_count / max(s.total_events, 1) for s in snapshots]
        if len(error_rates) < 2:
            return None

        # Simple linear projection to 100% error rate
        slope = (error_rates[-1] - error_rates[0]) / max(len(error_rates) - 1, 1)
        if slope <= 0:
            # Use signal strength as proxy
            max_strength = max(s.strength for s in signals)
            return max(5, int(50 * (1 - max_strength)))

        remaining = (1.0 - error_rates[-1]) / slope
        # Scale to events (assume ~10 events per snapshot interval)
        events = max(1, int(remaining * 10))
        return min(500, events)

    def _estimate_time_to_failure(
        self, snapshots: List[SessionSnapshot], events_to_failure: Optional[int]
    ) -> Optional[timedelta]:
        """Estimate time to failure based on event rate."""
        if events_to_failure is None or len(snapshots) < 2:
            return None

        # Calculate average time between snapshots
        timestamps = [s.timestamp for s in snapshots]
        if len(timestamps) < 2:
            return None

        total_duration = (timestamps[-1] - timestamps[0]).total_seconds()
        if total_duration <= 0:
            return None

        avg_event_rate = snapshots[-1].event_rate_per_min
        if avg_event_rate > 0:
            minutes = events_to_failure / avg_event_rate
            return timedelta(minutes=minutes)

        # Fallback: use snapshot interval
        interval = total_duration / (len(timestamps) - 1)
        events_per_interval = max(1, snapshots[-1].total_events / len(snapshots))
        intervals_left = events_to_failure / events_per_interval
        return timedelta(seconds=interval * intervals_left)

    def _generate_explanation(
        self, signals: List[IndicatorSignal], risk: RiskLevel
    ) -> str:
        """Generate a human-readable explanation of the prediction."""
        if not signals:
            return "No concerning signals detected."

        signal_names = [s.signal_type.label for s in sorted(
            signals, key=lambda s: s.strength, reverse=True
        )]

        if risk.severity >= 3:
            prefix = "Critical failure pattern detected"
        elif risk.severity >= 2:
            prefix = "Multiple warning signs indicate degradation"
        elif risk.severity >= 1:
            prefix = "Early warning indicators present"
        else:
            prefix = "Minor signals detected"

        top_signals = signal_names[:3]
        return f"{prefix}: {', '.join(top_signals)}."
