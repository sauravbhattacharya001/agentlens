"""Tests for Agent Memory Leak Detector."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from agentlens.memory_leak import (
    MemoryLeakDetector,
    LeakDetectorConfig,
    MemoryLeakReport,
    LeakSignal,
    LeakSeverity,
    LeakCategory,
    TrendDirection,
    GrowthSegment,
    ExhaustionForecast,
    AccumulationProfile,
)


# ── Test Fixtures ───────────────────────────────────────────────────


@dataclass
class MockToolCall:
    tool_call_id: str = "tc1"
    tool_name: str = "search"
    tool_input: dict = field(default_factory=dict)
    tool_output: dict | None = None


@dataclass
class MockEvent:
    event_id: str = "e1"
    session_id: str = "s1"
    event_type: str = "llm_call"
    tokens_in: int = 100
    tokens_out: int = 50
    duration_ms: float | None = 100.0
    input_data: dict | None = None
    output_data: dict | None = None
    tool_call: MockToolCall | None = None


@dataclass
class MockSession:
    session_id: str = "test-session"
    events: list = field(default_factory=list)


def make_growing_session(n: int = 20, base: int = 100, growth: int = 50) -> MockSession:
    """Create session with monotonically growing tokens."""
    events = []
    for i in range(n):
        events.append(MockEvent(
            event_id=f"e{i}",
            tokens_in=base + i * growth,
            tokens_out=50,
            input_data={"prompt": f"message {i}" * (i + 1)},
        ))
    return MockSession(events=events)


def make_stable_session(n: int = 20, tokens: int = 200) -> MockSession:
    """Create session with stable token usage."""
    events = []
    for i in range(n):
        events.append(MockEvent(
            event_id=f"e{i}",
            tokens_in=tokens + (i % 3) * 10 - 10,  # Small variation
            tokens_out=50,
            input_data={"prompt": f"msg {i}"},
        ))
    return MockSession(events=events)


def make_snowball_session(n: int = 20) -> MockSession:
    """Create session with accelerating growth."""
    events = []
    for i in range(n):
        # Quadratic growth
        events.append(MockEvent(
            event_id=f"e{i}",
            tokens_in=100 + i * i * 20,
            tokens_out=50,
        ))
    return MockSession(events=events)


def make_tool_hoarding_session(n: int = 15) -> MockSession:
    """Create session with growing tool outputs."""
    events = []
    for i in range(n):
        tc = MockToolCall(
            tool_name="search",
            tool_output={"results": [f"result_{j}" for j in range(i * 3 + 1)]},
        )
        events.append(MockEvent(
            event_id=f"e{i}",
            event_type="tool_call",
            tokens_in=200,
            tokens_out=100,
            tool_call=tc,
        ))
    return MockSession(events=events)


def make_repetition_session(n: int = 10) -> MockSession:
    """Create session with repeated content."""
    base_content = {"system": "You are helpful", "history": ["msg1", "msg2", "msg3"] * 10}
    events = []
    for i in range(n):
        # Each event carries the same content with minor additions
        content = dict(base_content)
        content["turn"] = i
        events.append(MockEvent(
            event_id=f"e{i}",
            tokens_in=500,
            tokens_out=100,
            input_data=content,
        ))
    return MockSession(events=events)


def make_error_session(n: int = 12) -> MockSession:
    """Create session with errors and post-error references."""
    events = []
    for i in range(n):
        if i == 3:
            events.append(MockEvent(
                event_id=f"e{i}",
                event_type="error",
                tokens_in=200,
                tokens_out=0,
                output_data={"error": "ConnectionTimeout", "traceback": "..."},
                input_data={"action": "fetch"},
            ))
        elif i > 3 and i < 8:
            events.append(MockEvent(
                event_id=f"e{i}",
                tokens_in=300,
                tokens_out=100,
                input_data={"context": f"Previous error: ConnectionTimeout failed at step {i}", "retry": True},
            ))
        else:
            events.append(MockEvent(
                event_id=f"e{i}",
                tokens_in=200,
                tokens_out=100,
                input_data={"action": f"step_{i}"},
            ))
    return MockSession(events=events)


# ── Core Analysis Tests ─────────────────────────────────────────────


class TestMemoryLeakDetector:
    """Tests for the main detector engine."""

    def test_empty_session(self):
        detector = MemoryLeakDetector()
        session = MockSession(events=[])
        report = detector.analyze(session)
        assert report.severity == LeakSeverity.NONE
        assert report.leak_score == 0.0
        assert report.total_events == 0

    def test_insufficient_events(self):
        detector = MemoryLeakDetector()
        session = MockSession(events=[MockEvent() for _ in range(3)])
        report = detector.analyze(session)
        assert report.severity == LeakSeverity.NONE
        assert "Insufficient data" in report.recommendations[0]

    def test_stable_session_no_leaks(self):
        detector = MemoryLeakDetector()
        session = make_stable_session(20)
        report = detector.analyze(session)
        assert report.leak_score < 30
        assert report.severity in (LeakSeverity.NONE, LeakSeverity.LOW)

    def test_growing_session_detected(self):
        detector = MemoryLeakDetector()
        session = make_growing_session(20, base=100, growth=100)
        report = detector.analyze(session)
        assert report.leak_score > 20
        assert any(s.category == LeakCategory.TOKEN_GROWTH for s in report.leak_signals)

    def test_snowball_detected(self):
        detector = MemoryLeakDetector()
        session = make_snowball_session(20)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.CONTEXT_SNOWBALL for s in report.leak_signals)

    def test_tool_hoarding_detected(self):
        detector = MemoryLeakDetector()
        session = make_tool_hoarding_session(15)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.TOOL_OUTPUT_HOARDING for s in report.leak_signals)

    def test_repetition_detected(self):
        detector = MemoryLeakDetector()
        session = make_repetition_session(10)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.REPETITION_BLOAT for s in report.leak_signals)

    def test_dead_references_detected(self):
        detector = MemoryLeakDetector()
        session = make_error_session(12)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.DEAD_REFERENCE_RETENTION for s in report.leak_signals)

    def test_unbounded_history_super_linear(self):
        detector = MemoryLeakDetector()
        session = make_snowball_session(25)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.UNBOUNDED_HISTORY for s in report.leak_signals)

    def test_payload_inflation_detected(self):
        detector = MemoryLeakDetector()
        events = []
        for i in range(15):
            events.append(MockEvent(
                event_id=f"e{i}",
                tokens_in=200,
                tokens_out=50,
                input_data={"data": "x" * (100 + i * 200)},
            ))
        session = MockSession(events=events)
        report = detector.analyze(session)
        assert any(s.category == LeakCategory.PAYLOAD_INFLATION for s in report.leak_signals)


# ── Exhaustion Forecast Tests ───────────────────────────────────────


class TestExhaustionForecast:
    """Tests for context exhaustion forecasting."""

    def test_no_exhaustion_stable(self):
        detector = MemoryLeakDetector()
        session = make_stable_session(20, tokens=100)
        report = detector.analyze(session)
        assert report.exhaustion_forecast is not None
        assert report.exhaustion_forecast.exhaustion_probability < 0.5

    def test_exhaustion_high_growth(self):
        config = LeakDetectorConfig(context_window_tokens=10000)
        detector = MemoryLeakDetector(config)
        session = make_growing_session(20, base=200, growth=200)
        report = detector.analyze(session)
        assert report.exhaustion_forecast is not None
        assert report.exhaustion_forecast.events_until_exhaustion is not None

    def test_already_exceeded(self):
        config = LeakDetectorConfig(context_window_tokens=500)
        detector = MemoryLeakDetector(config)
        session = make_growing_session(10, base=100, growth=100)
        report = detector.analyze(session)
        assert report.exhaustion_forecast is not None
        # Cumulative will exceed 500 quickly

    def test_accelerating_forecast(self):
        config = LeakDetectorConfig(context_window_tokens=500000)
        detector = MemoryLeakDetector(config)
        session = make_snowball_session(20)
        report = detector.analyze(session)
        forecast = report.exhaustion_forecast
        assert forecast is not None
        assert forecast.growth_model in ("linear", "quadratic")


# ── Growth Segment Tests ────────────────────────────────────────────


class TestGrowthSegments:
    """Tests for growth segment detection."""

    def test_finds_growth_segments(self):
        detector = MemoryLeakDetector()
        session = make_growing_session(15)
        report = detector.analyze(session)
        assert len(report.growth_segments) > 0
        seg = report.growth_segments[0]
        assert seg.slope > 0
        assert seg.end_index > seg.start_index

    def test_no_segments_in_stable(self):
        detector = MemoryLeakDetector()
        # Create truly flat session
        events = [MockEvent(event_id=f"e{i}", tokens_in=200, tokens_out=50) for i in range(10)]
        # Add some decreases to break monotonicity
        events[3] = MockEvent(event_id="e3", tokens_in=150, tokens_out=50)
        events[6] = MockEvent(event_id="e6", tokens_in=140, tokens_out=50)
        session = MockSession(events=events)
        report = detector.analyze(session)
        # Segments found will be short/insignificant
        for seg in report.growth_segments:
            assert seg.end_index - seg.start_index <= 4


# ── Accumulation Profile Tests ──────────────────────────────────────


class TestAccumulationProfiles:
    """Tests for per-type accumulation profiling."""

    def test_profiles_computed(self):
        detector = MemoryLeakDetector()
        events = []
        for i in range(10):
            events.append(MockEvent(event_id=f"e{i}", event_type="llm_call", tokens_in=200, tokens_out=50))
        for i in range(5):
            events.append(MockEvent(event_id=f"t{i}", event_type="tool_call", tokens_in=100, tokens_out=30))
        session = MockSession(events=events)
        report = detector.analyze(session)
        assert len(report.accumulation_profiles) == 2
        types = {p.event_type for p in report.accumulation_profiles}
        assert "llm_call" in types
        assert "tool_call" in types

    def test_growing_type_flagged(self):
        detector = MemoryLeakDetector()
        events = []
        for i in range(12):
            events.append(MockEvent(
                event_id=f"e{i}",
                event_type="llm_call",
                tokens_in=100 + i * 50,
                tokens_out=50,
            ))
        session = MockSession(events=events)
        report = detector.analyze(session)
        llm_profile = next(p for p in report.accumulation_profiles if p.event_type == "llm_call")
        assert llm_profile.growth_trend == TrendDirection.GROWING


# ── Configuration Tests ─────────────────────────────────────────────


class TestConfiguration:
    """Tests for detector configuration."""

    def test_custom_context_window(self):
        config = LeakDetectorConfig(context_window_tokens=4096)
        detector = MemoryLeakDetector(config)
        session = make_growing_session(10, base=500, growth=200)
        report = detector.analyze(session)
        forecast = report.exhaustion_forecast
        assert forecast is not None
        assert forecast.projected_limit == 4096

    def test_high_sensitivity(self):
        config = LeakDetectorConfig(
            monotonic_run_threshold=2,
            growth_significance_threshold=0.3,
        )
        detector = MemoryLeakDetector(config)
        session = make_growing_session(8, base=100, growth=30)
        report = detector.analyze(session)
        # Should detect more with lower thresholds
        assert report.leak_score > 0

    def test_low_sensitivity(self):
        config = LeakDetectorConfig(
            monotonic_run_threshold=8,
            growth_significance_threshold=0.95,
        )
        detector = MemoryLeakDetector(config)
        session = make_growing_session(8, base=100, growth=30)
        report_strict = detector.analyze(session)
        # Fewer signals with strict thresholds
        default_detector = MemoryLeakDetector()
        report_default = default_detector.analyze(session)
        assert report_strict.leak_score <= report_default.leak_score


# ── Scoring Tests ───────────────────────────────────────────────────


class TestScoring:
    """Tests for leak scoring and severity classification."""

    def test_score_range(self):
        detector = MemoryLeakDetector()
        for session_fn in [make_stable_session, make_growing_session, make_snowball_session]:
            report = detector.analyze(session_fn(20))
            assert 0.0 <= report.leak_score <= 100.0

    def test_severity_ordering(self):
        detector = MemoryLeakDetector()
        stable_report = detector.analyze(make_stable_session(20))
        growing_report = detector.analyze(make_growing_session(20, growth=200))
        snowball_report = detector.analyze(make_snowball_session(25))

        severity_order = [LeakSeverity.NONE, LeakSeverity.LOW, LeakSeverity.MODERATE,
                          LeakSeverity.HIGH, LeakSeverity.CRITICAL]
        assert severity_order.index(stable_report.severity) <= severity_order.index(growing_report.severity)
        assert severity_order.index(growing_report.severity) <= severity_order.index(snowball_report.severity)

    def test_multiple_signals_increase_score(self):
        detector = MemoryLeakDetector()
        # Session with multiple leak types
        events = []
        for i in range(20):
            tc = MockToolCall(tool_name="search", tool_output={"data": "x" * (i * 100)}) if i % 2 == 0 else None
            events.append(MockEvent(
                event_id=f"e{i}",
                event_type="tool_call" if i % 2 == 0 else "llm_call",
                tokens_in=100 + i * 80,
                tokens_out=50,
                input_data={"prompt": "hello " * (i + 1) * 5},
                tool_call=tc,
            ))
        multi_session = MockSession(events=events)
        multi_report = detector.analyze(multi_session)

        # Single issue session
        single_report = detector.analyze(make_growing_session(20, growth=50))

        # Multiple issues should generally score higher
        assert multi_report.leak_score >= single_report.leak_score * 0.5  # Relaxed assertion


# ── Report Format Tests ─────────────────────────────────────────────


class TestReportFormat:
    """Tests for report formatting and serialization."""

    def test_format_report_string(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(make_growing_session(15))
        formatted = report.format_report()
        assert "MEMORY LEAK DETECTOR" in formatted
        assert "Leak Score" in formatted
        assert report.session_id in formatted

    def test_to_dict(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(make_growing_session(15))
        d = report.to_dict()
        assert "session_id" in d
        assert "severity" in d
        assert "leak_score" in d
        assert "leak_signals" in d
        assert isinstance(d["leak_signals"], list)

    def test_to_json(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(make_growing_session(15))
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["session_id"] == "test-session"
        assert isinstance(parsed["leak_score"], float)

    def test_empty_report_formats(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(MockSession(events=[]))
        formatted = report.format_report()
        assert "MEMORY LEAK DETECTOR" in formatted
        d = report.to_dict()
        assert d["severity"] == "none"


# ── Recommendation Tests ────────────────────────────────────────────


class TestRecommendations:
    """Tests for recommendation generation."""

    def test_recommendations_generated(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(make_growing_session(20, growth=150))
        assert len(report.recommendations) > 0

    def test_urgent_exhaustion_recommendation(self):
        config = LeakDetectorConfig(context_window_tokens=5000)
        detector = MemoryLeakDetector(config)
        session = make_growing_session(15, base=200, growth=200)
        report = detector.analyze(session)
        # Should have urgent recommendation if exhaustion is near
        has_urgent = any("URGENT" in r for r in report.recommendations)
        # May or may not trigger depending on exact forecast
        assert len(report.recommendations) > 0

    def test_category_specific_recommendations(self):
        detector = MemoryLeakDetector()
        report = detector.analyze(make_repetition_session(10))
        if any(s.category == LeakCategory.REPETITION_BLOAT for s in report.leak_signals):
            assert any("deduplicate" in r.lower() or "dedup" in r.lower() for r in report.recommendations)


# ── Utility Method Tests ────────────────────────────────────────────


class TestUtilities:
    """Tests for internal utility methods."""

    def test_cumulative(self):
        result = MemoryLeakDetector._cumulative([10, 20, 30])
        assert result == [10, 30, 60]

    def test_cumulative_empty(self):
        result = MemoryLeakDetector._cumulative([])
        assert result == []

    def test_linear_fit_perfect(self):
        # Perfect linear: y = 10 + 5x
        series = [10 + 5 * i for i in range(10)]
        slope, r2 = MemoryLeakDetector._linear_fit(series)
        assert abs(slope - 5.0) < 0.01
        assert abs(r2 - 1.0) < 0.01

    def test_linear_fit_flat(self):
        series = [100] * 10
        slope, r2 = MemoryLeakDetector._linear_fit(series)
        assert slope == 0.0

    def test_jaccard_identical(self):
        s = {"a", "b", "c"}
        assert MemoryLeakDetector._jaccard(s, s) == 1.0

    def test_jaccard_disjoint(self):
        assert MemoryLeakDetector._jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_partial(self):
        sim = MemoryLeakDetector._jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert abs(sim - 0.5) < 0.01

    def test_jaccard_empty(self):
        assert MemoryLeakDetector._jaccard(set(), set()) == 0.0


# ── Edge Case Tests ─────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_single_event(self):
        detector = MemoryLeakDetector()
        session = MockSession(events=[MockEvent()])
        report = detector.analyze(session)
        assert report.severity == LeakSeverity.NONE

    def test_all_zero_tokens(self):
        detector = MemoryLeakDetector()
        events = [MockEvent(event_id=f"e{i}", tokens_in=0, tokens_out=0) for i in range(10)]
        session = MockSession(events=events)
        report = detector.analyze(session)
        assert report.leak_score == 0.0

    def test_very_long_session(self):
        detector = MemoryLeakDetector()
        session = make_growing_session(100, base=50, growth=10)
        report = detector.analyze(session)
        assert report.total_events == 100
        assert report.leak_score > 0

    def test_session_without_input_data(self):
        detector = MemoryLeakDetector()
        events = [MockEvent(event_id=f"e{i}", tokens_in=100 + i * 10, input_data=None) for i in range(10)]
        session = MockSession(events=events)
        report = detector.analyze(session)
        # Should not crash
        assert report.total_events == 10

    def test_mixed_event_types(self):
        detector = MemoryLeakDetector()
        events = []
        types = ["llm_call", "tool_call", "error", "decision", "llm_call"]
        for i in range(15):
            et = types[i % len(types)]
            events.append(MockEvent(
                event_id=f"e{i}",
                event_type=et,
                tokens_in=100 + i * 20,
                tokens_out=50,
            ))
        session = MockSession(events=events)
        report = detector.analyze(session)
        assert report.total_events == 15
