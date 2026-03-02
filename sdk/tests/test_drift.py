"""Tests for agentlens.drift — behavioral drift detection."""

import json
import math
import pytest

from agentlens.drift import (
    DriftDetector,
    DriftDirection,
    DriftReport,
    DriftStatus,
    MetricDrift,
    ToolUsageDrift,
    _cohens_d,
    _extract_session_metrics,
    _extract_tool_usage,
    _mean,
    _std,
    _total_events,
)
from agentlens.models import AgentEvent, Session, ToolCall


# ── Helpers ──────────────────────────────────────────────────────────

def _make_session(
    event_count: int = 5,
    avg_latency: float = 100.0,
    error_rate: float = 0.0,
    tokens_per_event: int = 50,
    tool_names: list | None = None,
) -> Session:
    """Create a test session with controlled metrics."""
    s = Session(agent_name="test-agent")
    errors = int(event_count * error_rate)
    for i in range(event_count):
        is_error = i < errors
        tc = None
        if tool_names and i < len(tool_names):
            tc = ToolCall(tool_name=tool_names[i], tool_input={})
        s.add_event(AgentEvent(
            event_type="error" if is_error else "llm_call",
            duration_ms=avg_latency + (i * 5),  # slight variation
            tokens_in=tokens_per_event,
            tokens_out=tokens_per_event,
            tool_call=tc,
        ))
    s.end()
    return s


def _make_stable_sessions(count: int = 10) -> list[Session]:
    """Create sessions with consistent behavior."""
    return [_make_session(event_count=5, avg_latency=100.0, tokens_per_event=50)
            for _ in range(count)]


def _make_degraded_sessions(count: int = 10) -> list[Session]:
    """Create sessions with worse performance."""
    return [_make_session(
        event_count=10, avg_latency=500.0, error_rate=0.3,
        tokens_per_event=200
    ) for _ in range(count)]


# ── Statistical helpers ──────────────────────────────────────────────

class TestStatHelpers:
    def test_mean_basic(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_mean_single(self):
        assert _mean([42.0]) == 42.0

    def test_std_basic(self):
        assert _std([5.0, 5.0, 5.0]) == 0.0

    def test_std_nonzero(self):
        s = _std([0.0, 10.0])
        assert abs(s - 5.0) < 1e-10

    def test_std_single(self):
        assert _std([7.0]) == 0.0

    def test_cohens_d_identical(self):
        d = _cohens_d(50.0, 10.0, 10, 50.0, 10.0, 10)
        assert abs(d) < 1e-10

    def test_cohens_d_different(self):
        d = _cohens_d(50.0, 10.0, 10, 70.0, 10.0, 10)
        assert d > 0  # current > baseline
        assert abs(d - 2.0) < 0.1  # 20/10 = 2.0

    def test_cohens_d_zero_variance(self):
        d = _cohens_d(5.0, 0.0, 5, 5.0, 0.0, 5)
        assert d == 0.0

    def test_cohens_d_zero_variance_diff_means(self):
        """Zero variance but different means → large effect size."""
        d = _cohens_d(5.0, 0.0, 5, 10.0, 0.0, 5)
        assert d > 1.0  # Should be very significant

    def test_cohens_d_too_few(self):
        assert _cohens_d(1.0, 0.0, 1, 2.0, 0.0, 1) == 0.0


# ── Metric extraction ───────────────────────────────────────────────

class TestExtractMetrics:
    def test_basic_extraction(self):
        s = _make_session(event_count=5, avg_latency=100.0, tokens_per_event=50)
        metrics = _extract_session_metrics(s)
        assert metrics["event_count"] == 5.0
        assert metrics["avg_latency_ms"] > 0
        assert metrics["total_tokens"] == 500.0  # 5 * (50+50)
        assert metrics["tokens_per_event"] == 100.0
        assert metrics["error_rate"] == 0.0

    def test_with_errors(self):
        s = _make_session(event_count=10, error_rate=0.3)
        metrics = _extract_session_metrics(s)
        assert abs(metrics["error_rate"] - 0.3) < 1e-10

    def test_empty_session(self):
        s = Session(agent_name="empty")
        s.end()
        metrics = _extract_session_metrics(s)
        assert metrics["event_count"] == 0.0
        assert metrics["avg_latency_ms"] == 0.0
        assert metrics["error_rate"] == 0.0

    def test_tool_call_rate(self):
        s = _make_session(
            event_count=4,
            tool_names=["search", "calculate"],
        )
        metrics = _extract_session_metrics(s)
        assert metrics["tool_call_rate"] == 0.5  # 2 of 4


class TestExtractToolUsage:
    def test_counts_tools(self):
        sessions = [
            _make_session(event_count=3, tool_names=["search", "search"]),
            _make_session(event_count=3, tool_names=["calculate"]),
        ]
        counts = _extract_tool_usage(sessions)
        assert counts["search"] == 2
        assert counts["calculate"] == 1

    def test_empty_sessions(self):
        sessions = [Session(agent_name="empty")]
        counts = _extract_tool_usage(sessions)
        assert len(counts) == 0

    def test_total_events(self):
        sessions = [
            _make_session(event_count=5),
            _make_session(event_count=3),
        ]
        assert _total_events(sessions) == 8


# ── DriftDetector ────────────────────────────────────────────────────

class TestDriftDetector:
    def test_stable_behavior(self):
        """Same behavior in both windows → stable."""
        baseline = _make_stable_sessions(10)
        current = _make_stable_sessions(10)
        report = DriftDetector.compare(baseline, current)
        assert report.status == DriftStatus.STABLE
        assert report.drift_score < 15
        assert len(report.drifting_metrics) == 0

    def test_degraded_behavior(self):
        """Worse performance in current → degraded."""
        baseline = _make_stable_sessions(10)
        current = _make_degraded_sessions(10)
        report = DriftDetector.compare(baseline, current)
        assert report.status in (DriftStatus.SIGNIFICANT_DRIFT, DriftStatus.DEGRADED)
        assert report.drift_score > 20
        assert len(report.drifting_metrics) > 0

    def test_improved_behavior(self):
        """Better performance in current → drift but not degraded."""
        baseline = _make_degraded_sessions(10)
        current = _make_stable_sessions(10)
        report = DriftDetector.compare(baseline, current)
        # Error rate decreased, which is drifting but not degrading
        assert report.status != DriftStatus.DEGRADED
        assert report.drift_score > 0

    def test_empty_baseline_raises(self):
        det = DriftDetector()
        det.add_current(_make_session())
        with pytest.raises(ValueError, match="Baseline"):
            det.detect()

    def test_empty_current_raises(self):
        det = DriftDetector()
        det.add_baseline(_make_session())
        with pytest.raises(ValueError, match="Current"):
            det.detect()

    def test_add_and_detect(self):
        det = DriftDetector()
        for s in _make_stable_sessions(5):
            det.add_baseline(s)
        for s in _make_stable_sessions(5):
            det.add_current(s)
        report = det.detect()
        assert isinstance(report, DriftReport)
        assert report.baseline_sessions == 5
        assert report.current_sessions == 5

    def test_clear(self):
        det = DriftDetector()
        det.add_baseline(_make_session())
        det.add_current(_make_session())
        det.clear()
        assert det.baseline_count == 0
        assert det.current_count == 0

    def test_counts(self):
        det = DriftDetector()
        det.add_baseline(_make_session())
        det.add_baseline(_make_session())
        det.add_current(_make_session())
        assert det.baseline_count == 2
        assert det.current_count == 1

    def test_custom_threshold(self):
        """Higher threshold → fewer things flagged as drifting."""
        baseline = _make_stable_sessions(10)
        current = _make_degraded_sessions(10)
        strict = DriftDetector.compare(baseline, current, drift_threshold=0.5)
        lenient = DriftDetector.compare(baseline, current, drift_threshold=2.0)
        assert len(strict.drifting_metrics) >= len(lenient.drifting_metrics)

    def test_invalid_threshold(self):
        with pytest.raises(ValueError, match="positive"):
            DriftDetector(drift_threshold=0)
        with pytest.raises(ValueError, match="positive"):
            DriftDetector(drift_threshold=-1)

    def test_minimum_sessions(self):
        """Should work with just 1 session per window."""
        baseline = [_make_session(event_count=5, avg_latency=100)]
        current = [_make_session(event_count=5, avg_latency=100)]
        report = DriftDetector.compare(baseline, current)
        assert isinstance(report, DriftReport)


# ── Tool usage drift ────────────────────────────────────────────────

class TestToolDrift:
    def test_new_tool_detected(self):
        baseline = [_make_session(event_count=3, tool_names=["search"])]
        current = [_make_session(event_count=3, tool_names=["calculate"])]
        report = DriftDetector.compare(baseline, current)
        new_tools = [t for t in report.tool_drifts if t.is_new]
        assert any(t.tool_name == "calculate" for t in new_tools)

    def test_dropped_tool_detected(self):
        baseline = [_make_session(event_count=3, tool_names=["search"])]
        current = [_make_session(event_count=3)]
        report = DriftDetector.compare(baseline, current)
        dropped = [t for t in report.tool_drifts if t.is_dropped]
        assert any(t.tool_name == "search" for t in dropped)

    def test_no_tool_drift_when_same(self):
        sessions = [_make_session(event_count=3, tool_names=["search"])]
        report = DriftDetector.compare(sessions, sessions)
        for t in report.tool_drifts:
            assert not t.is_new
            assert not t.is_dropped
            assert abs(t.change) < 1e-10


# ── MetricDrift ──────────────────────────────────────────────────────

class TestMetricDrift:
    def test_to_dict(self):
        md = MetricDrift(
            name="avg_latency_ms",
            baseline_mean=100.0, current_mean=200.0,
            baseline_std=10.0, current_std=15.0,
            relative_change=1.0, effect_size=1.5,
            direction=DriftDirection.INCREASED, is_drifting=True,
        )
        d = md.to_dict()
        assert d["name"] == "avg_latency_ms"
        assert d["is_drifting"] is True
        assert d["direction"] == "increased"


class TestToolUsageDrift:
    def test_to_dict(self):
        td = ToolUsageDrift(
            tool_name="search", baseline_rate=0.5, current_rate=0.3,
            change=-0.2, is_new=False, is_dropped=False,
        )
        d = td.to_dict()
        assert d["tool_name"] == "search"
        assert d["change"] == -0.2


# ── DriftReport ──────────────────────────────────────────────────────

class TestDriftReport:
    def test_format_report(self):
        baseline = _make_stable_sessions(5)
        current = _make_degraded_sessions(5)
        report = DriftDetector.compare(baseline, current)
        text = report.format_report()
        assert "Drift" in text
        assert "Score" in text
        assert str(report.drift_score) in text

    def test_to_dict(self):
        report = DriftDetector.compare(
            _make_stable_sessions(3), _make_stable_sessions(3)
        )
        d = report.to_dict()
        assert "drift_score" in d
        assert "status" in d
        assert "metric_drifts" in d
        assert "tool_drifts" in d
        assert isinstance(d["metric_drifts"], list)

    def test_to_json(self):
        report = DriftDetector.compare(
            _make_stable_sessions(3), _make_stable_sessions(3)
        )
        j = report.to_json()
        data = json.loads(j)
        assert data["status"] == "stable"
        assert isinstance(data["drift_score"], int)

    def test_summary_stable(self):
        report = DriftDetector.compare(
            _make_stable_sessions(5), _make_stable_sessions(5)
        )
        assert "consistent" in report.summary.lower()

    def test_summary_degraded(self):
        report = DriftDetector.compare(
            _make_stable_sessions(10), _make_degraded_sessions(10)
        )
        assert len(report.summary) > 0
        # Should mention some kind of change
        assert any(word in report.summary.lower()
                   for word in ["drift", "degrad", "increas"])


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_all_empty_sessions(self):
        baseline = [Session(agent_name="empty") for _ in range(3)]
        current = [Session(agent_name="empty") for _ in range(3)]
        for s in baseline + current:
            s.end()
        report = DriftDetector.compare(baseline, current)
        assert report.status == DriftStatus.STABLE
        assert report.drift_score == 0

    def test_mixed_empty_and_full(self):
        baseline = [_make_session(event_count=5)] + [Session(agent_name="e")]
        current = [_make_session(event_count=5)] + [Session(agent_name="e")]
        for s in baseline + current:
            if not s.ended_at:
                s.end()
        report = DriftDetector.compare(baseline, current)
        assert isinstance(report, DriftReport)

    def test_single_event_sessions(self):
        baseline = [_make_session(event_count=1) for _ in range(5)]
        current = [_make_session(event_count=1) for _ in range(5)]
        report = DriftDetector.compare(baseline, current)
        assert report.drift_score < 15

    def test_drift_score_capped_at_100(self):
        """Even extreme drift should cap at 100."""
        baseline = [_make_session(event_count=1, avg_latency=1.0)]
        current = [_make_session(event_count=100, avg_latency=10000.0,
                                  error_rate=1.0, tokens_per_event=10000)]
        report = DriftDetector.compare(baseline, current)
        assert report.drift_score <= 100

    def test_direction_stable_for_small_change(self):
        """Small effect size → direction is STABLE."""
        baseline = _make_stable_sessions(10)
        current = _make_stable_sessions(10)
        report = DriftDetector.compare(baseline, current)
        for md in report.metric_drifts:
            if abs(md.effect_size) < 0.2:
                assert md.direction == DriftDirection.STABLE

    def test_report_format_with_tools(self):
        """Report formatting includes tool section when tools are present."""
        baseline = [_make_session(event_count=3, tool_names=["search"])]
        current = [_make_session(event_count=3, tool_names=["calculate"])]
        report = DriftDetector.compare(baseline, current)
        text = report.format_report()
        assert "Tool Usage" in text
