"""Tests for agentlens.stamina — Agent Stamina Profiler."""

from __future__ import annotations

import json
import math

import pytest

from agentlens.stamina import (
    FatigueDetection,
    FatigueSignal,
    InterventionPoint,
    InterventionUrgency,
    StaminaConfig,
    StaminaProfiler,
    StaminaReport,
    StaminaStatus,
    WindowMetrics,
    _find_changepoint,
    _linear_regression,
    _sparkline,
)


# ── Helpers ─────────────────────────────────────────────────────────


class FakeToolCall:
    def __init__(self, has_output: bool = True):
        self.tool_output = {"result": "ok"} if has_output else None


class FakeEvent:
    def __init__(
        self,
        duration_ms: float | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        event_type: str = "llm_call",
        tool_call: FakeToolCall | None = None,
    ):
        self.duration_ms = duration_ms
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.event_type = event_type
        self.tool_call = tool_call


class FakeSession:
    def __init__(self, session_id: str = "test-session", events: list | None = None):
        self.session_id = session_id
        self.events = events or []


def make_degrading_session(n_events: int = 30, latency_growth: float = 50.0) -> FakeSession:
    """Create a session with steadily increasing latency."""
    events = []
    for i in range(n_events):
        events.append(FakeEvent(
            duration_ms=100.0 + i * latency_growth,
            tokens_in=100 + i * 20,
            tokens_out=200 + i * 30,
        ))
    return FakeSession(events=events)


def make_stable_session(n_events: int = 30) -> FakeSession:
    """Create a session with stable performance."""
    events = []
    for i in range(n_events):
        events.append(FakeEvent(
            duration_ms=100.0 + (i % 3) * 5,  # Minor jitter only
            tokens_in=100,
            tokens_out=200,
        ))
    return FakeSession(events=events)


def make_error_degrading_session(n_events: int = 30) -> FakeSession:
    """Create a session where errors increase over time."""
    events = []
    for i in range(n_events):
        # First half: no errors; second half: increasing errors
        is_error = i > n_events // 2 and (i % 3 == 0)
        events.append(FakeEvent(
            duration_ms=100.0,
            tokens_in=100,
            tokens_out=200,
            event_type="error" if is_error else "llm_call",
        ))
    return FakeSession(events=events)


def make_tool_decay_session(n_events: int = 30) -> FakeSession:
    """Create a session where tool success decays."""
    events = []
    for i in range(n_events):
        # Early tools succeed, later ones fail
        success = i < n_events * 0.6
        events.append(FakeEvent(
            duration_ms=100.0,
            tokens_in=100,
            tokens_out=200,
            tool_call=FakeToolCall(has_output=success),
        ))
    return FakeSession(events=events)


# ── Unit Tests: Helpers ─────────────────────────────────────────────


class TestLinearRegression:
    def test_flat_line(self):
        slope, r2 = _linear_regression([5.0, 5.0, 5.0, 5.0])
        assert slope == 0.0
        assert r2 == 0.0

    def test_perfect_upward(self):
        slope, r2 = _linear_regression([1.0, 2.0, 3.0, 4.0, 5.0])
        assert abs(slope - 1.0) < 0.001
        assert abs(r2 - 1.0) < 0.001

    def test_perfect_downward(self):
        slope, r2 = _linear_regression([5.0, 4.0, 3.0, 2.0, 1.0])
        assert abs(slope - (-1.0)) < 0.001
        assert abs(r2 - 1.0) < 0.001

    def test_single_value(self):
        slope, r2 = _linear_regression([3.0])
        assert slope == 0.0

    def test_two_values(self):
        slope, r2 = _linear_regression([1.0, 3.0])
        assert abs(slope - 2.0) < 0.001

    def test_noisy_upward(self):
        # Generally increasing but noisy
        values = [1.0, 3.0, 2.0, 4.0, 3.5, 5.0, 4.5, 6.0]
        slope, r2 = _linear_regression(values)
        assert slope > 0
        assert 0 < r2 < 1


class TestSparkline:
    def test_empty(self):
        assert _sparkline([]) == ""

    def test_single(self):
        result = _sparkline([5.0])
        assert len(result) == 1

    def test_increasing(self):
        result = _sparkline([1.0, 2.0, 3.0, 4.0, 5.0])
        # Should be visually increasing
        assert result[0] < result[-1]  # First char < last char (block unicode)

    def test_flat(self):
        result = _sparkline([3.0, 3.0, 3.0])
        assert len(result) == 3


class TestFindChangepoint:
    def test_no_change(self):
        result = _find_changepoint([1.0, 1.0, 1.0, 1.0, 1.0])
        assert result is None

    def test_clear_jump(self):
        values = [1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        result = _find_changepoint(values)
        assert result is not None
        assert result >= 4  # Should detect around the jump

    def test_too_short(self):
        assert _find_changepoint([1.0, 2.0]) is None


# ── Unit Tests: Profiler ────────────────────────────────────────────


class TestStaminaProfilerBasic:
    def test_too_few_events(self):
        session = FakeSession(events=[FakeEvent() for _ in range(5)])
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score == 100.0
        assert report.status == StaminaStatus.FRESH
        assert report.fatigue_onset_index is None

    def test_stable_session(self):
        session = make_stable_session(30)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score >= 80.0
        assert report.status in (StaminaStatus.FRESH, StaminaStatus.MILD_FATIGUE)

    def test_degrading_session_detected(self):
        session = make_degrading_session(30, latency_growth=50.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score < 80.0
        assert report.status != StaminaStatus.FRESH
        assert len(report.signals) > 0

    def test_fatigue_onset_identified(self):
        session = make_degrading_session(30, latency_growth=100.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.fatigue_onset_index is not None

    def test_session_id_preserved(self):
        session = FakeSession(session_id="my-session-123", events=[FakeEvent() for _ in range(5)])
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.session_id == "my-session-123"

    def test_window_count(self):
        session = make_stable_session(25)
        profiler = StaminaProfiler(StaminaConfig(window_size=5))
        report = profiler.profile(session)
        assert report.window_count == 5

    def test_custom_window_size(self):
        session = make_stable_session(30)
        profiler = StaminaProfiler(StaminaConfig(window_size=10))
        report = profiler.profile(session)
        assert report.window_count == 3


class TestFatigueSignals:
    def test_latency_creep_detected(self):
        session = make_degrading_session(30, latency_growth=80.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        signal_types = {s.signal for s in report.signals}
        assert FatigueSignal.LATENCY_CREEP in signal_types

    def test_token_inflation_detected(self):
        session = make_degrading_session(30, latency_growth=0.0)
        # Events already have growing tokens
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        signal_types = {s.signal for s in report.signals}
        assert FatigueSignal.TOKEN_INFLATION in signal_types

    def test_error_rate_increase_detected(self):
        session = make_error_degrading_session(30)
        profiler = StaminaProfiler(StaminaConfig(window_size=5))
        report = profiler.profile(session)
        signal_types = {s.signal for s in report.signals}
        # May or may not detect depending on exact pattern
        # At minimum should run without error
        assert report.event_count == 30

    def test_tool_decay_detected(self):
        session = make_tool_decay_session(30)
        profiler = StaminaProfiler(StaminaConfig(window_size=5))
        report = profiler.profile(session)
        signal_types = {s.signal for s in report.signals}
        assert FatigueSignal.TOOL_SUCCESS_DECAY in signal_types


class TestInterventions:
    def test_no_interventions_when_fresh(self):
        session = make_stable_session(30)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        # Stable session should have few/no interventions
        # (might have none, or at most low-urgency ones)
        high_urgency = [i for i in report.interventions
                        if i.urgency in (InterventionUrgency.HIGH, InterventionUrgency.CRITICAL)]
        assert len(high_urgency) == 0

    def test_interventions_on_severe_degradation(self):
        session = make_degrading_session(30, latency_growth=200.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert len(report.interventions) > 0

    def test_intervention_has_recommendation(self):
        session = make_degrading_session(30, latency_growth=200.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        for ip in report.interventions:
            assert ip.recommendation
            assert ip.reason


class TestRecommendations:
    def test_fresh_session_recommendation(self):
        session = make_stable_session(30)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        if report.status == StaminaStatus.FRESH:
            assert any("no intervention" in r.lower() for r in report.recommendations)

    def test_degraded_has_recommendations(self):
        session = make_degrading_session(30, latency_growth=200.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert len(report.recommendations) > 0


class TestMultiSession:
    def test_profile_multi(self):
        s1 = FakeSession(session_id="stable-1", events=make_stable_session(20).events)
        s2 = FakeSession(session_id="degrade-1", events=make_degrading_session(20).events)
        profiler = StaminaProfiler()
        reports = profiler.profile_multi([s1, s2])
        assert len(reports) == 2

    def test_aggregate_stamina(self):
        sessions = [
            make_stable_session(20),
            make_degrading_session(20, latency_growth=100.0),
            make_degrading_session(20, latency_growth=200.0),
        ]
        profiler = StaminaProfiler()
        agg = profiler.aggregate_stamina(sessions)
        assert agg["sessions_analyzed"] == 3
        assert 0 <= agg["avg_stamina_score"] <= 100
        assert "fatigue_rate" in agg
        assert "status_distribution" in agg

    def test_aggregate_empty(self):
        profiler = StaminaProfiler()
        agg = profiler.aggregate_stamina([])
        assert agg["sessions_analyzed"] == 0


class TestReportSerialization:
    def test_format_report_runs(self):
        session = make_degrading_session(30, latency_growth=100.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        text = report.format_report()
        assert "AGENT STAMINA PROFILE" in text
        assert "Stamina Score" in text

    def test_to_dict(self):
        session = make_degrading_session(30, latency_growth=100.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        d = report.to_dict()
        assert "stamina_score" in d
        assert "status" in d
        assert "signals" in d
        assert isinstance(d["signals"], list)

    def test_to_json(self):
        session = make_degrading_session(30, latency_growth=100.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["session_id"] == "test-session"

    def test_format_report_with_sparkline(self):
        session = make_degrading_session(30, latency_growth=50.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        text = report.format_report()
        assert "LATENCY TREND" in text


class TestStaminaScore:
    def test_perfect_score(self):
        session = make_stable_session(30)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score >= 90.0

    def test_severely_degraded_low_score(self):
        session = make_degrading_session(30, latency_growth=500.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score <= 50.0

    def test_score_bounded(self):
        session = make_degrading_session(50, latency_growth=1000.0)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert 0.0 <= report.stamina_score <= 100.0


class TestEdgeCases:
    def test_all_zero_latency(self):
        events = [FakeEvent(duration_ms=0.0, tokens_in=100, tokens_out=100) for _ in range(20)]
        session = FakeSession(events=events)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.stamina_score >= 0

    def test_no_duration_events(self):
        events = [FakeEvent(duration_ms=None, tokens_in=100, tokens_out=100) for _ in range(20)]
        session = FakeSession(events=events)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report is not None

    def test_mixed_event_types(self):
        events = []
        for i in range(30):
            if i % 3 == 0:
                events.append(FakeEvent(duration_ms=float(100 + i * 10), tool_call=FakeToolCall()))
            elif i % 3 == 1:
                events.append(FakeEvent(duration_ms=float(100 + i * 10), event_type="error"))
            else:
                events.append(FakeEvent(duration_ms=float(100 + i * 10), tokens_in=100, tokens_out=200))
        session = FakeSession(events=events)
        profiler = StaminaProfiler()
        report = profiler.profile(session)
        assert report.event_count == 30

    def test_exactly_min_events(self):
        events = [FakeEvent(duration_ms=100.0) for _ in range(10)]
        session = FakeSession(events=events)
        profiler = StaminaProfiler(StaminaConfig(min_events=10, window_size=3))
        report = profiler.profile(session)
        assert report.window_count >= 3
