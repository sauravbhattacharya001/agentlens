"""Tests for agentlens.health — Session Health Scoring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentlens.health import (
    HealthGrade,
    HealthReport,
    HealthScorer,
    HealthThresholds,
    MetricScore,
)
from agentlens.models import AgentEvent, Session, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _perfect_events(n: int = 20) -> list[dict]:
    """Return *n* perfect events: no errors, low latency, modest tokens."""
    return [
        {
            "event_type": "llm_call",
            "duration_ms": 50.0,
            "tokens_in": 100,
            "tokens_out": 100,
            "tool_call": None,
        }
        for _ in range(n)
    ]


def _terrible_events(n: int = 20) -> list[dict]:
    """Return *n* catastrophic events: all errors, huge latency, tokens."""
    return [
        {
            "event_type": "error",
            "duration_ms": 20000.0,
            "tokens_in": 10000,
            "tokens_out": 10000,
            "tool_call": {
                "tool_name": "bad_tool",
                "tool_output": {"error": "crash"},
            },
        }
        for _ in range(n)
    ]


def _tool_events(success: int, fail: int) -> list[dict]:
    """Build a mix of successful / failing tool-call events."""
    events: list[dict] = []
    for _ in range(success):
        events.append({
            "event_type": "tool_call",
            "duration_ms": 100.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call": {
                "tool_name": "calc",
                "tool_output": {"result": 42},
            },
        })
    for _ in range(fail):
        events.append({
            "event_type": "tool_call",
            "duration_ms": 100.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tool_call": {
                "tool_name": "calc",
                "tool_output": {"error": "division by zero"},
            },
        })
    return events


def _session_with_events(events_data: list[dict]) -> Session:
    """Create a Session model and populate with AgentEvent objects."""
    session = Session(agent_name="test-agent")
    for ed in events_data:
        tc = None
        if ed.get("tool_call"):
            tc_data = ed["tool_call"]
            tc = ToolCall(
                tool_name=tc_data.get("tool_name", "unknown"),
                tool_output=tc_data.get("tool_output"),
            )
        event = AgentEvent(
            session_id=session.session_id,
            event_type=ed.get("event_type", "generic"),
            duration_ms=ed.get("duration_ms"),
            tokens_in=ed.get("tokens_in", 0),
            tokens_out=ed.get("tokens_out", 0),
            tool_call=tc,
        )
        session.add_event(event)
    return session


# ---------------------------------------------------------------------------
# HealthGrade
# ---------------------------------------------------------------------------

class TestHealthGrade:
    def test_values(self):
        assert HealthGrade.EXCELLENT.value == "A"
        assert HealthGrade.GOOD.value == "B"
        assert HealthGrade.FAIR.value == "C"
        assert HealthGrade.POOR.value == "D"
        assert HealthGrade.CRITICAL.value == "F"

    def test_grade_boundary_90(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(90.0) == HealthGrade.EXCELLENT

    def test_grade_boundary_89(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(89.9) == HealthGrade.GOOD

    def test_grade_boundary_80(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(80.0) == HealthGrade.GOOD

    def test_grade_boundary_79(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(79.9) == HealthGrade.FAIR

    def test_grade_boundary_70(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(70.0) == HealthGrade.FAIR

    def test_grade_boundary_69(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(69.9) == HealthGrade.POOR

    def test_grade_boundary_60(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(60.0) == HealthGrade.POOR

    def test_grade_boundary_59(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(59.9) == HealthGrade.CRITICAL

    def test_grade_zero(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(0.0) == HealthGrade.CRITICAL

    def test_grade_100(self):
        scorer = HealthScorer()
        assert scorer._calculate_grade(100.0) == HealthGrade.EXCELLENT


# ---------------------------------------------------------------------------
# HealthThresholds defaults
# ---------------------------------------------------------------------------

class TestHealthThresholds:
    def test_defaults(self):
        t = HealthThresholds()
        assert t.max_error_rate == 0.05
        assert t.max_avg_latency_ms == 5000.0
        assert t.max_p95_latency_ms == 10000.0
        assert t.min_tool_success_rate == 0.90
        assert t.max_tokens_per_event == 8000
        assert t.ideal_events_range == (2, 100)

    def test_custom(self):
        t = HealthThresholds(max_error_rate=0.10, max_avg_latency_ms=1000.0)
        assert t.max_error_rate == 0.10
        assert t.max_avg_latency_ms == 1000.0


# ---------------------------------------------------------------------------
# HealthScorer — perfect session
# ---------------------------------------------------------------------------

class TestPerfectSession:
    def test_grade_is_excellent(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.grade == HealthGrade.EXCELLENT

    def test_overall_score_is_100(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.overall_score == pytest.approx(100.0)

    def test_no_errors(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.error_count == 0

    def test_no_recommendations(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.recommendations == []

    def test_event_count(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.event_count == 20


# ---------------------------------------------------------------------------
# HealthScorer — terrible session
# ---------------------------------------------------------------------------

class TestTerribleSession:
    def test_grade_is_critical(self):
        report = HealthScorer().score(_terrible_events(20))
        assert report.grade == HealthGrade.CRITICAL

    def test_overall_score_is_very_low(self):
        report = HealthScorer().score(_terrible_events(20))
        # event_volume still scores 100 (20 is within ideal range),
        # so overall ≈ 10 (0.10 weight × 100). Everything else is 0.
        assert report.overall_score <= 15.0

    def test_all_errors(self):
        report = HealthScorer().score(_terrible_events(20))
        assert report.error_count == 20

    def test_has_recommendations(self):
        report = HealthScorer().score(_terrible_events(20))
        assert len(report.recommendations) > 0


# ---------------------------------------------------------------------------
# Individual metric scorers
# ---------------------------------------------------------------------------

class TestErrorRateScorer:
    def test_zero_errors(self):
        scorer = HealthScorer()
        ms = scorer._score_error_rate(_perfect_events(10))
        assert ms.score == 100.0
        assert ms.name == "error_rate"

    def test_all_errors(self):
        scorer = HealthScorer()
        ms = scorer._score_error_rate(_terrible_events(10))
        assert ms.score == 0.0

    def test_partial_errors(self):
        # 1 error in 40 events = 2.5% rate (below 5% threshold → partial penalty)
        events = _perfect_events(39) + [{"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        scorer = HealthScorer()
        ms = scorer._score_error_rate(events)
        assert 0 < ms.score < 100

    def test_weight(self):
        scorer = HealthScorer()
        ms = scorer._score_error_rate(_perfect_events(10))
        assert ms.weight == 0.25


class TestLatencyScorer:
    def test_low_latency(self):
        events = [{"event_type": "llm_call", "duration_ms": 50.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_latency(events)
        assert ms.score == 100.0

    def test_high_latency(self):
        events = [{"event_type": "llm_call", "duration_ms": 6000.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_latency(events)
        assert ms.score == 0.0

    def test_mid_latency(self):
        events = [{"event_type": "llm_call", "duration_ms": 2550.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_latency(events)
        assert 0 < ms.score < 100

    def test_no_durations(self):
        events = [{"event_type": "llm_call", "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_latency(events)
        assert ms.score == 100.0

    def test_weight(self):
        ms = HealthScorer()._score_latency(_perfect_events(5))
        assert ms.weight == 0.20


class TestP95LatencyScorer:
    def test_low_p95(self):
        events = [{"event_type": "llm_call", "duration_ms": 50.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None} for _ in range(20)]
        ms = HealthScorer()._score_p95_latency(events)
        assert ms.score == 100.0

    def test_high_p95(self):
        events = [{"event_type": "llm_call", "duration_ms": 15000.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_p95_latency(events)
        assert ms.score == 0.0

    def test_p95_index_calculation(self):
        # 20 events with durations 0, 100, 200, ..., 1900
        # P95 via linear interpolation: idx = 0.95 * 19 = 18.05
        # value = 1800 + 0.05 * (1900 - 1800) = 1805.0
        events = [{"event_type": "llm_call", "duration_ms": float(i * 100), "tokens_in": 0, "tokens_out": 0, "tool_call": None} for i in range(20)]
        ms = HealthScorer()._score_p95_latency(events)
        assert ms.value == 1805.0

    def test_no_durations(self):
        events = [{"event_type": "llm_call", "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        ms = HealthScorer()._score_p95_latency(events)
        assert ms.score == 100.0

    def test_weight(self):
        ms = HealthScorer()._score_p95_latency(_perfect_events(5))
        assert ms.weight == 0.15


class TestToolSuccessScorer:
    def test_all_success(self):
        ms = HealthScorer()._score_tool_success(_tool_events(10, 0))
        assert ms.score == 100.0

    def test_all_fail(self):
        ms = HealthScorer()._score_tool_success(_tool_events(0, 10))
        assert ms.score == 0.0

    def test_mixed(self):
        ms = HealthScorer()._score_tool_success(_tool_events(8, 2))
        assert 0 < ms.score <= 100

    def test_no_tool_calls(self):
        ms = HealthScorer()._score_tool_success(_perfect_events(5))
        assert ms.score == 100.0

    def test_weight(self):
        ms = HealthScorer()._score_tool_success(_perfect_events(5))
        assert ms.weight == 0.15


class TestTokenEfficiencyScorer:
    def test_low_tokens(self):
        events = [{"event_type": "llm_call", "duration_ms": 100.0, "tokens_in": 100, "tokens_out": 100, "tool_call": None}]
        ms = HealthScorer()._score_token_efficiency(events)
        assert ms.score == 100.0

    def test_high_tokens(self):
        events = [{"event_type": "llm_call", "duration_ms": 100.0, "tokens_in": 5000, "tokens_out": 5000, "tool_call": None}]
        ms = HealthScorer()._score_token_efficiency(events)
        assert ms.score == 0.0

    def test_mid_tokens(self):
        # Avg = 5000, half threshold = 4000, full threshold = 8000
        events = [{"event_type": "llm_call", "duration_ms": 100.0, "tokens_in": 2500, "tokens_out": 2500, "tool_call": None}]
        ms = HealthScorer()._score_token_efficiency(events)
        assert 0 < ms.score < 100

    def test_weight(self):
        ms = HealthScorer()._score_token_efficiency(_perfect_events(5))
        assert ms.weight == 0.15


class TestEventVolumeScorer:
    def test_in_range(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(50))
        assert ms.score == 100.0

    def test_too_few(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(1))
        assert ms.score < 100.0

    def test_too_many(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(150))
        assert ms.score < 100.0

    def test_zero_events(self):
        ms = HealthScorer()._score_event_volume([])
        assert ms.score == 0.0

    def test_weight(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(5))
        assert ms.weight == 0.10

    def test_exactly_at_min(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(2))
        assert ms.score == 100.0

    def test_exactly_at_max(self):
        ms = HealthScorer()._score_event_volume(_perfect_events(100))
        assert ms.score == 100.0


# ---------------------------------------------------------------------------
# Custom thresholds
# ---------------------------------------------------------------------------

class TestCustomThresholds:
    def test_stricter_error_rate(self):
        # 1 error in 10 events = 10% rate
        events = _perfect_events(9) + [{"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        strict = HealthThresholds(max_error_rate=0.10)
        ms = HealthScorer(strict)._score_error_rate(events)
        assert ms.score == 0.0  # 10% = max_error_rate → 0

    def test_lenient_error_rate(self):
        events = _perfect_events(9) + [{"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        lenient = HealthThresholds(max_error_rate=0.50)
        ms = HealthScorer(lenient)._score_error_rate(events)
        assert ms.score > 50.0

    def test_custom_latency_threshold(self):
        events = [{"event_type": "llm_call", "duration_ms": 500.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        tight = HealthThresholds(max_avg_latency_ms=600.0)
        ms = HealthScorer(tight)._score_latency(events)
        assert ms.score < 100.0

    def test_custom_events_range(self):
        t = HealthThresholds(ideal_events_range=(5, 10))
        ms = HealthScorer(t)._score_event_volume(_perfect_events(3))
        assert ms.score < 100.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_events(self):
        report = HealthScorer().score([])
        assert report.event_count == 0
        assert report.error_count == 0
        assert report.grade in HealthGrade

    def test_single_event(self):
        report = HealthScorer().score(_perfect_events(1))
        assert report.event_count == 1

    def test_no_duration_data(self):
        events = [{"event_type": "generic", "tokens_in": 0, "tokens_out": 0, "tool_call": None}]
        report = HealthScorer().score(events)
        assert report.total_duration_ms == 0.0

    def test_no_tool_calls(self):
        report = HealthScorer().score(_perfect_events(10))
        # tool_success should be 100 when no tool calls
        tool_metric = next(m for m in report.metrics if m.name == "tool_success")
        assert tool_metric.score == 100.0

    def test_session_id_passthrough(self):
        report = HealthScorer().score(_perfect_events(5), session_id="abc-123")
        assert report.session_id == "abc-123"

    def test_default_session_id(self):
        report = HealthScorer().score(_perfect_events(5))
        assert report.session_id == "unknown"


# ---------------------------------------------------------------------------
# HealthReport serialisation
# ---------------------------------------------------------------------------

class TestHealthReportToDict:
    def test_keys(self):
        report = HealthScorer().score(_perfect_events(10))
        d = report.to_dict()
        assert "session_id" in d
        assert "overall_score" in d
        assert "grade" in d
        assert "metrics" in d
        assert "recommendations" in d
        assert "event_count" in d
        assert "error_count" in d
        assert "total_tokens" in d
        assert "total_duration_ms" in d

    def test_grade_is_string(self):
        report = HealthScorer().score(_perfect_events(10))
        d = report.to_dict()
        assert isinstance(d["grade"], str)
        assert d["grade"] == "A"

    def test_metrics_list(self):
        report = HealthScorer().score(_perfect_events(10))
        d = report.to_dict()
        assert len(d["metrics"]) == 6
        for m in d["metrics"]:
            assert "name" in m
            assert "score" in m


class TestHealthReportRender:
    def test_contains_grade(self):
        report = HealthScorer().score(_perfect_events(10))
        text = report.render()
        assert "Grade: A" in text

    def test_contains_score(self):
        report = HealthScorer().score(_perfect_events(10))
        text = report.render()
        assert "100.0/100" in text

    def test_contains_metrics(self):
        report = HealthScorer().score(_perfect_events(10))
        text = report.render()
        assert "error_rate" in text
        assert "avg_latency" in text

    def test_contains_recommendations_when_present(self):
        report = HealthScorer().score(_terrible_events(10))
        text = report.render()
        assert "Recommendations:" in text


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_high_error_rate_recommendation(self):
        # 4 errors out of 10 = 40%
        events = _perfect_events(6) + [
            {"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None}
            for _ in range(4)
        ]
        report = HealthScorer().score(events)
        assert any("error rate" in r.lower() for r in report.recommendations)

    def test_high_latency_recommendation(self):
        events = [{"event_type": "llm_call", "duration_ms": 3000.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None} for _ in range(10)]
        report = HealthScorer().score(events)
        assert any("latency" in r.lower() for r in report.recommendations)

    def test_low_tool_success_recommendation(self):
        events = _tool_events(8, 5)
        report = HealthScorer().score(events)
        assert any("tool success" in r.lower() for r in report.recommendations)

    def test_high_token_recommendation(self):
        events = [{"event_type": "llm_call", "duration_ms": 50.0, "tokens_in": 4500, "tokens_out": 500, "tool_call": None} for _ in range(10)]
        report = HealthScorer().score(events)
        assert any("token" in r.lower() for r in report.recommendations)

    def test_too_few_events_recommendation(self):
        report = HealthScorer().score(_perfect_events(1))
        assert any("incomplete" in r.lower() or "under-instrumented" in r.lower() for r in report.recommendations)

    def test_too_many_events_recommendation(self):
        report = HealthScorer().score(_perfect_events(150))
        assert any("verbosity" in r.lower() or "batching" in r.lower() for r in report.recommendations)

    def test_no_recommendations_for_perfect(self):
        report = HealthScorer().score(_perfect_events(20))
        assert report.recommendations == []


# ---------------------------------------------------------------------------
# score() with raw dicts vs score_session() with Session objects
# ---------------------------------------------------------------------------

class TestScoreVsScoreSession:
    def test_same_grade(self):
        raw = _perfect_events(10)
        session = _session_with_events(raw)
        scorer = HealthScorer()
        r1 = scorer.score(raw)
        r2 = scorer.score_session(session)
        assert r1.grade == r2.grade

    def test_same_overall_score(self):
        raw = _perfect_events(10)
        session = _session_with_events(raw)
        scorer = HealthScorer()
        r1 = scorer.score(raw)
        r2 = scorer.score_session(session)
        assert r1.overall_score == pytest.approx(r2.overall_score, abs=0.1)

    def test_same_error_count(self):
        raw = _terrible_events(5)
        session = _session_with_events(raw)
        scorer = HealthScorer()
        r1 = scorer.score(raw)
        r2 = scorer.score_session(session)
        assert r1.error_count == r2.error_count

    def test_session_id_from_session(self):
        session = _session_with_events(_perfect_events(5))
        report = HealthScorer().score_session(session)
        assert report.session_id == session.session_id


# ---------------------------------------------------------------------------
# Weight validation
# ---------------------------------------------------------------------------

class TestWeightValidation:
    def test_weights_sum_to_one(self):
        report = HealthScorer().score(_perfect_events(10))
        total = sum(m.weight for m in report.metrics)
        assert total == pytest.approx(1.0)

    def test_six_metrics(self):
        report = HealthScorer().score(_perfect_events(10))
        assert len(report.metrics) == 6


# ---------------------------------------------------------------------------
# Session with only errors
# ---------------------------------------------------------------------------

class TestOnlyErrors:
    def test_all_error_events(self):
        events = [{"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None} for _ in range(10)]
        report = HealthScorer().score(events)
        assert report.error_count == 10
        error_metric = next(m for m in report.metrics if m.name == "error_rate")
        assert error_metric.score == 0.0

    def test_grade_is_below_excellent(self):
        events = [{"event_type": "error", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None} for _ in range(10)]
        report = HealthScorer().score(events)
        # error_rate tanks (0.25 weight) but other metrics are fine
        assert report.grade != HealthGrade.EXCELLENT


# ---------------------------------------------------------------------------
# Session with only tool calls
# ---------------------------------------------------------------------------

class TestOnlyToolCalls:
    def test_all_successful_tools(self):
        events = _tool_events(10, 0)
        report = HealthScorer().score(events)
        tool_metric = next(m for m in report.metrics if m.name == "tool_success")
        assert tool_metric.score == 100.0

    def test_all_failed_tools(self):
        events = _tool_events(0, 10)
        report = HealthScorer().score(events)
        tool_metric = next(m for m in report.metrics if m.name == "tool_success")
        assert tool_metric.score == 0.0


# ---------------------------------------------------------------------------
# Mixed sessions
# ---------------------------------------------------------------------------

class TestMixedSessions:
    def test_mix_of_types(self):
        events = (
            _perfect_events(5)
            + _tool_events(3, 1)
            + [{"event_type": "error", "duration_ms": 500.0, "tokens_in": 50, "tokens_out": 50, "tool_call": None}]
        )
        report = HealthScorer().score(events)
        assert 0 < report.overall_score < 100
        assert report.error_count >= 1

    def test_mixed_latencies(self):
        events = [
            {"event_type": "llm_call", "duration_ms": 50.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None},
            {"event_type": "llm_call", "duration_ms": 3000.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None},
        ]
        report = HealthScorer().score(events)
        lat_metric = next(m for m in report.metrics if m.name == "avg_latency")
        assert lat_metric.value == pytest.approx(1525.0)


# ---------------------------------------------------------------------------
# Tracker integration
# ---------------------------------------------------------------------------

class TestTrackerIntegration:
    def test_health_score_on_tracker(self):
        from agentlens.tracker import AgentTracker
        transport = MagicMock()
        tracker = AgentTracker(transport=transport)
        session = tracker.start_session(agent_name="test")
        tracker.track(event_type="llm_call", duration_ms=50.0, tokens_in=100, tokens_out=100)
        report = tracker.health_score()
        assert isinstance(report, HealthReport)
        assert report.grade in HealthGrade

    def test_health_score_by_session_id(self):
        from agentlens.tracker import AgentTracker
        transport = MagicMock()
        tracker = AgentTracker(transport=transport)
        s1 = tracker.start_session(agent_name="a1")
        tracker.track(event_type="llm_call", duration_ms=50.0, tokens_in=100, tokens_out=100)
        s2 = tracker.start_session(agent_name="a2")
        report = tracker.health_score(session_id=s1.session_id)
        assert report.session_id == s1.session_id

    def test_health_score_not_found(self):
        from agentlens.tracker import AgentTracker
        transport = MagicMock()
        tracker = AgentTracker(transport=transport)
        with pytest.raises(RuntimeError, match="Session not found"):
            tracker.health_score(session_id="nonexistent")

    def test_health_score_custom_thresholds(self):
        from agentlens.tracker import AgentTracker
        transport = MagicMock()
        tracker = AgentTracker(transport=transport)
        tracker.start_session(agent_name="test")
        tracker.track(event_type="llm_call", duration_ms=50.0, tokens_in=100, tokens_out=100)
        thresholds = HealthThresholds(max_error_rate=0.01)
        report = tracker.health_score(thresholds=thresholds)
        assert isinstance(report, HealthReport)


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------

class TestExports:
    def test_health_scorer_importable(self):
        from agentlens import HealthScorer as HS
        assert HS is HealthScorer

    def test_health_report_importable(self):
        from agentlens import HealthReport as HR
        assert HR is HealthReport

    def test_health_grade_importable(self):
        from agentlens import HealthGrade as HG
        assert HG is HealthGrade

    def test_health_thresholds_importable(self):
        from agentlens import HealthThresholds as HT
        assert HT is HealthThresholds

    def test_metric_score_importable(self):
        from agentlens import MetricScore as MS
        assert MS is MetricScore


# ---------------------------------------------------------------------------
# MetricScore dataclass
# ---------------------------------------------------------------------------

class TestMetricScore:
    def test_creation(self):
        ms = MetricScore(name="test", score=85.0, weight=0.5, value=42.0, threshold=100.0, detail="ok")
        assert ms.name == "test"
        assert ms.score == 85.0
        assert ms.weight == 0.5

    def test_detail_string(self):
        ms = MetricScore(name="test", score=85.0, weight=0.5, value=42.0, threshold=100.0, detail="some detail")
        assert ms.detail == "some detail"


# ---------------------------------------------------------------------------
# Total tokens & duration
# ---------------------------------------------------------------------------

class TestTotals:
    def test_total_tokens(self):
        events = [{"event_type": "llm_call", "duration_ms": 50.0, "tokens_in": 300, "tokens_out": 200, "tool_call": None} for _ in range(5)]
        report = HealthScorer().score(events)
        assert report.total_tokens == 2500

    def test_total_duration(self):
        events = [{"event_type": "llm_call", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None} for _ in range(5)]
        report = HealthScorer().score(events)
        assert report.total_duration_ms == 500.0

    def test_total_duration_with_none(self):
        events = [
            {"event_type": "llm_call", "duration_ms": 100.0, "tokens_in": 0, "tokens_out": 0, "tool_call": None},
            {"event_type": "llm_call", "tokens_in": 0, "tokens_out": 0, "tool_call": None},
        ]
        report = HealthScorer().score(events)
        assert report.total_duration_ms == 100.0
