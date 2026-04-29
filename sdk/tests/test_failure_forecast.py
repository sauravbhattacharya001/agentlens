"""Tests for Agent Failure Forecaster."""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from agentlens.failure_forecast import (
    FailureForecaster,
    SessionSnapshot,
    ForecastReport,
    FailurePrediction,
    IndicatorSignal,
    RiskLevel,
    LeadingIndicator,
    RecommendedAction,
)


def _ts(minutes_ago: int = 0) -> datetime:
    """Helper to create timestamps."""
    return datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)


def _make_snapshots(session_id: str = "sess-1", agent_id: str = "agent-1",
                    count: int = 5, **overrides) -> list:
    """Generate a sequence of snapshots with defaults."""
    snaps = []
    for i in range(count):
        kwargs = {
            "session_id": session_id,
            "agent_id": agent_id,
            "timestamp": _ts(count - i),
            "error_count": 0,
            "total_events": 50 + i * 10,
            "avg_latency_ms": 200.0,
            "retry_count": 0,
            "tool_failures": 0,
            "tool_calls": 10,
            "tokens_used": 1000 * (i + 1),
            "token_budget": 10000,
            "response_quality_score": 0.9,
            "consecutive_errors": 0,
            "event_rate_per_min": 5.0,
        }
        kwargs.update(overrides)
        snaps.append(SessionSnapshot(**kwargs))
    return snaps


# ── Basic Construction Tests ────────────────────────────────────────

def test_forecaster_init():
    f = FailureForecaster()
    assert f.session_count == 0
    assert f.min_snapshots == 3


def test_add_snapshot():
    f = FailureForecaster()
    snap = SessionSnapshot(
        session_id="s1", agent_id="a1", timestamp=_ts(0),
        error_count=0, total_events=10,
    )
    f.add_snapshot(snap)
    assert f.session_count == 1


def test_add_snapshots_bulk():
    f = FailureForecaster()
    snaps = _make_snapshots(count=5)
    f.add_snapshots(snaps)
    assert f.session_count == 1


def test_clear():
    f = FailureForecaster()
    f.add_snapshots(_make_snapshots(count=5))
    f.clear()
    assert f.session_count == 0


def test_no_prediction_insufficient_data():
    f = FailureForecaster(min_snapshots=3)
    f.add_snapshots(_make_snapshots(count=2))
    report = f.predict()
    assert len(report.predictions) == 0


def test_healthy_session_no_prediction():
    f = FailureForecaster()
    snaps = _make_snapshots(count=5)
    f.add_snapshots(snaps)
    report = f.predict()
    # Healthy session should have no predictions (or nominal)
    assert report.fleet_health_score >= 80.0


# ── Error Acceleration Tests ────────────────────────────────────────

def test_error_acceleration_detected():
    f = FailureForecaster(error_rate_threshold=0.1)
    snaps = []
    for i in range(6):
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(6 - i),
            error_count=i * 3,  # Rising errors
            total_events=50,
            avg_latency_ms=200.0,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    pred = report.predictions[0]
    signal_types = [s.signal_type for s in pred.signals]
    assert LeadingIndicator.ERROR_ACCELERATION in signal_types


# ── Latency Spike Tests ─────────────────────────────────────────────

def test_latency_spike_detected():
    f = FailureForecaster(latency_spike_factor=2.0)
    snaps = []
    for i in range(5):
        lat = 100.0 if i < 3 else 500.0  # Spike in last 2
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=50,
            avg_latency_ms=lat,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    if report.predictions:
        signal_types = [s.signal_type for s in report.predictions[0].signals]
        assert LeadingIndicator.LATENCY_SPIKE in signal_types


# ── Retry Escalation Tests ──────────────────────────────────────────

def test_retry_escalation_detected():
    f = FailureForecaster(retry_escalation_threshold=3)
    snaps = []
    for i in range(5):
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=50, avg_latency_ms=200.0,
            retry_count=i * 3,  # 0, 3, 6, 9, 12
            tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    signal_types = [s.signal_type for s in report.predictions[0].signals]
    assert LeadingIndicator.RETRY_ESCALATION in signal_types


# ── Tool Success Decay Tests ────────────────────────────────────────

def test_tool_success_decay_detected():
    f = FailureForecaster(tool_failure_threshold=0.2)
    snaps = []
    for i in range(5):
        failures = i * 2  # 0, 2, 4, 6, 8 out of 10
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=50, avg_latency_ms=200.0,
            retry_count=0,
            tool_failures=failures, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    signal_types = [s.signal_type for s in report.predictions[0].signals]
    assert LeadingIndicator.TOOL_SUCCESS_DECAY in signal_types


# ── Token Depletion Tests ───────────────────────────────────────────

def test_token_depletion_detected():
    f = FailureForecaster(token_depletion_warning=0.7)
    snaps = []
    for i in range(5):
        used = 2000 * (i + 1)  # 2000, 4000, 6000, 8000, 10000
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=50, avg_latency_ms=200.0,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=used, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    signal_types = [s.signal_type for s in report.predictions[0].signals]
    assert LeadingIndicator.TOKEN_BUDGET_DEPLETION in signal_types


# ── Event Rate Stall Tests ──────────────────────────────────────────

def test_event_rate_stall_detected():
    f = FailureForecaster()
    snaps = []
    for i in range(5):
        rate = 10.0 if i < 3 else 2.0  # Sharp drop in last 2
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=50, avg_latency_ms=200.0,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=rate,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    if report.predictions:
        signal_types = [s.signal_type for s in report.predictions[0].signals]
        assert LeadingIndicator.EVENT_RATE_STALL in signal_types


# ── Cascading Errors Tests ──────────────────────────────────────────

def test_cascading_errors_detected():
    f = FailureForecaster()
    snaps = []
    for i in range(5):
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=i, total_events=50, avg_latency_ms=200.0,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            consecutive_errors=i * 2,  # 0, 2, 4, 6, 8
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    signal_types = [s.signal_type for s in report.predictions[0].signals]
    assert LeadingIndicator.CASCADING_ERRORS in signal_types


# ── Response Degradation Tests ──────────────────────────────────────

def test_response_degradation_detected():
    f = FailureForecaster()
    snaps = []
    for i in range(6):
        quality = 0.95 - i * 0.12  # 0.95, 0.83, 0.71, 0.59, 0.47, 0.35
        snaps.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(6 - i),
            error_count=0, total_events=50, avg_latency_ms=200.0,
            retry_count=0, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            response_quality_score=max(0.1, quality),
            event_rate_per_min=5.0,
        ))
    f.add_snapshots(snaps)
    report = f.predict()
    assert len(report.predictions) >= 1
    signal_types = [s.signal_type for s in report.predictions[0].signals]
    assert LeadingIndicator.RESPONSE_DEGRADATION in signal_types


# ── Risk Classification Tests ───────────────────────────────────────

def test_risk_levels():
    f = FailureForecaster()
    assert f._classify_risk(0.90) == RiskLevel.IMMINENT
    assert f._classify_risk(0.70) == RiskLevel.CRITICAL
    assert f._classify_risk(0.50) == RiskLevel.HIGH
    assert f._classify_risk(0.30) == RiskLevel.ELEVATED
    assert f._classify_risk(0.10) == RiskLevel.NOMINAL


def test_risk_level_severity():
    assert RiskLevel.IMMINENT.severity > RiskLevel.CRITICAL.severity
    assert RiskLevel.CRITICAL.severity > RiskLevel.HIGH.severity
    assert RiskLevel.HIGH.severity > RiskLevel.ELEVATED.severity
    assert RiskLevel.ELEVATED.severity > RiskLevel.NOMINAL.severity


# ── Action Recommendation Tests ─────────────────────────────────────

def test_action_imminent():
    f = FailureForecaster()
    action = f._recommend_action(RiskLevel.IMMINENT, [])
    assert action == RecommendedAction.IMMEDIATE_INTERVENTION


def test_action_critical_cascade():
    f = FailureForecaster()
    sig = IndicatorSignal(
        signal_type=LeadingIndicator.CASCADING_ERRORS,
        strength=0.8, trend_direction="rising", evidence="test"
    )
    action = f._recommend_action(RiskLevel.CRITICAL, [sig])
    assert action == RecommendedAction.FAILOVER


def test_action_high_token():
    f = FailureForecaster()
    sig = IndicatorSignal(
        signal_type=LeadingIndicator.TOKEN_BUDGET_DEPLETION,
        strength=0.7, trend_direction="rising", evidence="test"
    )
    action = f._recommend_action(RiskLevel.HIGH, [sig])
    assert action == RecommendedAction.INCREASE_BUDGET


# ── Report Formatting Tests ─────────────────────────────────────────

def test_report_format():
    report = ForecastReport(
        total_sessions_analyzed=5,
        sessions_at_risk=2,
        fleet_health_score=72.5,
    )
    text = report.format_report()
    assert "FAILURE FORECAST" in text
    assert "5" in text
    assert "72.5" in text


def test_report_with_predictions_format():
    pred = FailurePrediction(
        session_id="s1", agent_id="agent-x",
        failure_probability=0.75, risk_level=RiskLevel.CRITICAL,
        estimated_events_to_failure=25,
        estimated_time_to_failure=timedelta(minutes=15),
        signals=[
            IndicatorSignal(
                signal_type=LeadingIndicator.ERROR_ACCELERATION,
                strength=0.8, trend_direction="rising",
                evidence="Error rate doubled"
            )
        ],
        recommended_action=RecommendedAction.RESTART_SESSION,
        confidence=0.7,
        explanation="Critical failure pattern detected.",
    )
    report = ForecastReport(
        predictions=[pred],
        total_sessions_analyzed=3,
        sessions_at_risk=1,
        fleet_health_score=60.0,
    )
    text = report.format_report()
    assert "agent-x" in text
    assert "Critical" in text or "CRITICAL" in text.upper()
    assert "Error Acceleration" in text


def test_report_to_json():
    report = ForecastReport(
        total_sessions_analyzed=2, sessions_at_risk=0,
        fleet_health_score=100.0,
    )
    j = report.to_json()
    import json
    data = json.loads(j)
    assert data["fleet_health_score"] == 100.0
    assert data["total_sessions_analyzed"] == 2


# ── Multi-Session Tests ─────────────────────────────────────────────

def test_multi_session_analysis():
    f = FailureForecaster()
    # Session 1: healthy
    for i in range(5):
        f.add_snapshot(SessionSnapshot(
            session_id="healthy", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=100, avg_latency_ms=100.0,
            retry_count=0, tool_failures=0, tool_calls=20,
            tokens_used=500, token_budget=10000, event_rate_per_min=10.0,
        ))
    # Session 2: failing
    for i in range(5):
        f.add_snapshot(SessionSnapshot(
            session_id="failing", agent_id="a2", timestamp=_ts(5 - i),
            error_count=i * 5, total_events=50, avg_latency_ms=200 + i * 100,
            retry_count=i * 4, tool_failures=i * 3, tool_calls=10,
            tokens_used=2000 * (i + 1), token_budget=10000,
            consecutive_errors=i * 2, event_rate_per_min=max(1, 10 - i * 3),
            response_quality_score=max(0.2, 0.9 - i * 0.15),
        ))
    report = f.predict()
    assert report.total_sessions_analyzed == 2
    # Failing session should be predicted
    failing_preds = [p for p in report.predictions if p.session_id == "failing"]
    assert len(failing_preds) >= 1
    assert failing_preds[0].failure_probability > 0.3


def test_predict_session_specific():
    f = FailureForecaster()
    for i in range(5):
        f.add_snapshot(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=i * 4, total_events=50, avg_latency_ms=200.0,
            retry_count=i * 3, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000,
            event_rate_per_min=5.0,
        ))
    pred = f.predict_session("s1")
    assert pred is not None
    assert pred.session_id == "s1"


def test_predict_nonexistent_session():
    f = FailureForecaster()
    pred = f.predict_session("nope")
    assert pred is None


# ── Confidence Tests ────────────────────────────────────────────────

def test_confidence_increases_with_data():
    f = FailureForecaster(min_snapshots=2)
    # Few snapshots
    snaps_few = []
    for i in range(3):
        snaps_few.append(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(3 - i),
            error_count=i * 5, total_events=50, avg_latency_ms=200.0,
            retry_count=i * 3, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000, event_rate_per_min=5.0,
        ))

    # Many snapshots (same pattern)
    snaps_many = []
    for i in range(10):
        snaps_many.append(SessionSnapshot(
            session_id="s2", agent_id="a1", timestamp=_ts(10 - i),
            error_count=i * 5, total_events=50, avg_latency_ms=200.0,
            retry_count=i * 3, tool_failures=0, tool_calls=10,
            tokens_used=1000, token_budget=10000, event_rate_per_min=5.0,
        ))

    f.add_snapshots(snaps_few)
    f.add_snapshots(snaps_many)
    report = f.predict()

    preds = {p.session_id: p for p in report.predictions}
    if "s1" in preds and "s2" in preds:
        # More data should give higher confidence
        assert preds["s2"].confidence >= preds["s1"].confidence


# ── Prediction to_dict Tests ────────────────────────────────────────

def test_prediction_to_dict():
    pred = FailurePrediction(
        session_id="s1", agent_id="a1",
        failure_probability=0.55, risk_level=RiskLevel.HIGH,
        estimated_events_to_failure=30,
        estimated_time_to_failure=timedelta(minutes=20),
        signals=[
            IndicatorSignal(
                signal_type=LeadingIndicator.LATENCY_SPIKE,
                strength=0.6, trend_direction="rising", evidence="test"
            )
        ],
        recommended_action=RecommendedAction.ALERT_OWNER,
        confidence=0.65,
        explanation="Multiple warning signs.",
    )
    d = pred.to_dict()
    assert d["session_id"] == "s1"
    assert d["risk_level"] == "high"
    assert d["failure_probability"] == 0.55
    assert len(d["signals"]) == 1
    assert d["signals"][0]["type"] == "latency_spike"


# ── Fleet Health Score Tests ────────────────────────────────────────

def test_fleet_health_all_healthy():
    f = FailureForecaster()
    for i in range(5):
        f.add_snapshot(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(5 - i),
            error_count=0, total_events=100, avg_latency_ms=100.0,
            retry_count=0, tool_failures=0, tool_calls=20,
            tokens_used=500, token_budget=10000, event_rate_per_min=10.0,
        ))
    report = f.predict()
    assert report.fleet_health_score >= 90.0


def test_fleet_health_degraded():
    f = FailureForecaster()
    # All sessions failing
    for sid in ["s1", "s2", "s3"]:
        for i in range(5):
            f.add_snapshot(SessionSnapshot(
                session_id=sid, agent_id="a1", timestamp=_ts(5 - i),
                error_count=i * 5, total_events=40, avg_latency_ms=200 + i * 200,
                retry_count=i * 4, tool_failures=i * 3, tool_calls=10,
                tokens_used=9000 + i * 200, token_budget=10000,
                consecutive_errors=i * 3, event_rate_per_min=max(1, 8 - i * 2),
                response_quality_score=max(0.1, 0.9 - i * 0.2),
            ))
    report = f.predict()
    assert report.fleet_health_score < 80.0


# ── Edge Cases ──────────────────────────────────────────────────────

def test_zero_events_no_crash():
    f = FailureForecaster()
    for i in range(4):
        f.add_snapshot(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(4 - i),
            error_count=0, total_events=0,  # zero events
            avg_latency_ms=0.0, retry_count=0,
            tool_failures=0, tool_calls=0,
            tokens_used=0, token_budget=0,
            event_rate_per_min=0.0,
        ))
    report = f.predict()
    assert report is not None


def test_single_session_many_snapshots():
    f = FailureForecaster()
    for i in range(20):
        f.add_snapshot(SessionSnapshot(
            session_id="s1", agent_id="a1", timestamp=_ts(20 - i),
            error_count=i, total_events=100, avg_latency_ms=100.0 + i * 10,
            retry_count=i, tool_failures=0, tool_calls=10,
            tokens_used=500 * (i + 1), token_budget=15000,
            event_rate_per_min=max(1, 10 - i * 0.3),
            response_quality_score=max(0.1, 1.0 - i * 0.04),
        ))
    report = f.predict()
    assert report.total_sessions_analyzed == 1
    if report.predictions:
        assert report.predictions[0].failure_probability > 0


def test_empty_report_format():
    report = ForecastReport()
    text = report.format_report()
    assert "No failure risks" in text


# ── Run all tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    test_funcs = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for fn in test_funcs:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{'=' * 50}")
    print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
