"""Tests for the latency profiler module."""

import time
import pytest
from agentlens.latency import (
    LatencyProfiler,
    ProfilingSession,
    StepRecord,
    StepStatus,
    PercentileStats,
    SlowStepAlert,
    SessionReport,
    compute_percentiles,
)


class TestStepRecord:
    def test_pending_duration_is_none(self):
        s = StepRecord(name="x")
        assert s.duration_s is None
        assert s.duration_ms is None

    def test_completed_duration(self):
        s = StepRecord(name="x", start_time=1.0, end_time=1.5, status=StepStatus.COMPLETED)
        assert s.duration_s == pytest.approx(0.5)
        assert s.duration_ms == pytest.approx(500.0)


class TestProfilingSession:
    def test_step_context_manager(self):
        session = ProfilingSession(session_id="s1")
        with session.step("fast") as rec:
            pass
        assert rec.status == StepStatus.COMPLETED
        assert rec.duration_s is not None
        assert rec.duration_ms is not None
        assert rec.name == "fast"

    def test_step_failure(self):
        session = ProfilingSession(session_id="s1")
        with pytest.raises(ValueError):
            with session.step("bad"):
                raise ValueError("boom")
        assert session.steps[0].status == StepStatus.FAILED
        assert session.steps[0].error == "boom"
        assert session.steps[0].duration_s is not None

    def test_record_step_manual(self):
        session = ProfilingSession(session_id="s1")
        rec = session.record_step("manual", 0.25, model="gpt-4")
        assert rec.status == StepStatus.COMPLETED
        assert rec.duration_s == pytest.approx(0.25, abs=0.01)
        assert rec.metadata == {"model": "gpt-4"}

    def test_total_duration(self):
        session = ProfilingSession(session_id="s1")
        session.record_step("a", 1.0)
        session.record_step("b", 2.0)
        assert session.total_duration_s == pytest.approx(3.0, abs=0.05)

    def test_bottleneck(self):
        session = ProfilingSession(session_id="s1")
        session.record_step("fast", 0.1)
        session.record_step("slow", 5.0)
        session.record_step("medium", 1.0)
        bn = session.bottleneck
        assert bn is not None
        assert bn.name == "slow"

    def test_bottleneck_empty(self):
        session = ProfilingSession(session_id="s1")
        assert session.bottleneck is None

    def test_get_step(self):
        session = ProfilingSession(session_id="s1")
        session.record_step("alpha", 1.0)
        assert session.get_step("alpha") is not None
        assert session.get_step("missing") is None

    def test_completed_and_failed_steps(self):
        session = ProfilingSession(session_id="s1")
        session.record_step("ok", 1.0)
        try:
            with session.step("fail"):
                raise RuntimeError("x")
        except RuntimeError:
            pass
        assert len(session.completed_steps) == 1
        assert len(session.failed_steps) == 1

    def test_step_metadata(self):
        session = ProfilingSession(session_id="s1")
        with session.step("x", model="gpt-4", tokens=100) as rec:
            pass
        assert rec.metadata == {"model": "gpt-4", "tokens": 100}


class TestComputePercentiles:
    def test_empty(self):
        assert compute_percentiles([]) is None

    def test_single(self):
        stats = compute_percentiles([100.0])
        assert stats is not None
        assert stats.count == 1
        assert stats.min_ms == 100.0
        assert stats.max_ms == 100.0
        assert stats.mean_ms == 100.0
        assert stats.stdev_ms == 0.0

    def test_distribution(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        stats = compute_percentiles(values)
        assert stats is not None
        assert stats.count == 10
        assert stats.min_ms == 10.0
        assert stats.max_ms == 100.0
        assert stats.p90_ms > stats.median_ms
        assert stats.p99_ms >= stats.p95_ms >= stats.p90_ms

    def test_to_dict(self):
        stats = compute_percentiles([1.0, 2.0, 3.0])
        assert stats is not None
        d = stats.to_dict()
        assert "count" in d
        assert "p95_ms" in d
        assert isinstance(d["mean_ms"], float)


class TestLatencyProfiler:
    def test_start_and_get_session(self):
        p = LatencyProfiler()
        s = p.start_session("s1", label="test")
        assert p.get_session("s1") is s
        assert p.session_count == 1

    def test_auto_id(self):
        p = LatencyProfiler()
        s = p.start_session()
        assert s.session_id is not None
        assert p.session_count == 1

    def test_remove_session(self):
        p = LatencyProfiler()
        p.start_session("s1")
        assert p.remove_session("s1") is True
        assert p.remove_session("s1") is False
        assert p.session_count == 0

    def test_report(self):
        p = LatencyProfiler()
        s = p.start_session("s1", label="run")
        s.record_step("a", 1.0)
        s.record_step("b", 3.0)
        report = p.report("s1")
        assert isinstance(report, SessionReport)
        assert report.step_count == 2
        assert report.completed_count == 2
        assert report.failed_count == 0
        assert report.bottleneck_name == "b"
        assert report.bottleneck_pct is not None
        assert report.bottleneck_pct > 50

    def test_report_summary(self):
        p = LatencyProfiler()
        s = p.start_session("s1")
        s.record_step("x", 2.0)
        report = p.report("s1")
        assert "s1" in report.summary
        assert "1 steps" in report.summary

    def test_report_to_dict(self):
        p = LatencyProfiler()
        s = p.start_session("s1")
        s.record_step("x", 1.0)
        d = p.report("s1").to_dict()
        assert d["session_id"] == "s1"
        assert len(d["steps"]) == 1

    def test_report_not_found(self):
        p = LatencyProfiler()
        with pytest.raises(KeyError):
            p.report("missing")

    def test_step_baselines(self):
        p = LatencyProfiler()
        for i in range(5):
            s = p.start_session(f"s{i}")
            s.record_step("retrieve", 0.1 + i * 0.01)
            s.record_step("llm", 1.0 + i * 0.1)
        baselines = p.step_baselines()
        assert "retrieve" in baselines
        assert "llm" in baselines
        assert baselines["llm"].mean_ms > baselines["retrieve"].mean_ms

    def test_detect_slow_steps_no_alert(self):
        p = LatencyProfiler()
        for i in range(5):
            s = p.start_session(f"s{i}")
            s.record_step("step", 1.0)
        # Normal speed session
        test = p.start_session("test")
        test.record_step("step", 1.0)
        alerts = p.detect_slow_steps("test")
        assert len(alerts) == 0

    def test_detect_slow_steps_alert(self):
        p = LatencyProfiler()
        for i in range(10):
            s = p.start_session(f"s{i}")
            s.record_step("step", 1.0)
        # Way too slow
        test = p.start_session("test")
        test.record_step("step", 10.0)
        alerts = p.detect_slow_steps("test", threshold_factor=2.0)
        assert len(alerts) == 1
        assert alerts[0].step_name == "step"
        assert alerts[0].severity in ("critical", "high", "medium")

    def test_slow_step_alert_to_dict(self):
        alert = SlowStepAlert(
            step_name="llm",
            session_id="s1",
            actual_ms=5000,
            baseline_mean_ms=1000,
            threshold_factor=2.0,
            threshold_ms=2000,
            ratio=5.0,
        )
        d = alert.to_dict()
        assert d["severity"] == "critical"
        assert d["step_name"] == "llm"

    def test_slow_step_severity_levels(self):
        def _alert(ratio: float) -> SlowStepAlert:
            return SlowStepAlert("x", "s", 100, 50, 2.0, 100, ratio)
        assert _alert(5.0).severity == "critical"
        assert _alert(3.0).severity == "high"
        assert _alert(2.0).severity == "medium"
        assert _alert(1.5).severity == "low"

    def test_detect_slow_not_found(self):
        p = LatencyProfiler()
        with pytest.raises(KeyError):
            p.detect_slow_steps("missing")

    def test_compare_sessions(self):
        p = LatencyProfiler()
        s1 = p.start_session("s1")
        s1.record_step("llm", 1.0)
        s1.record_step("retrieve", 0.5)
        s2 = p.start_session("s2")
        s2.record_step("llm", 2.0)
        s2.record_step("retrieve", 0.3)
        comp = p.compare_sessions(["s1", "s2"])
        assert "llm" in comp
        assert len(comp["llm"]) == 2
        # Sorted by duration
        assert comp["llm"][0]["duration_ms"] < comp["llm"][1]["duration_ms"]

    def test_compare_missing_session(self):
        p = LatencyProfiler()
        s1 = p.start_session("s1")
        s1.record_step("x", 1.0)
        comp = p.compare_sessions(["s1", "missing"])
        assert "x" in comp

    def test_fleet_summary(self):
        p = LatencyProfiler()
        for i in range(3):
            s = p.start_session(f"s{i}")
            s.record_step("a", 1.0 + i)
            if i == 2:
                try:
                    with s.step("fail"):
                        raise RuntimeError("x")
                except RuntimeError:
                    pass
        summary = p.fleet_summary()
        assert summary["total_sessions"] == 3
        assert summary["total_steps"] == 4  # 3 ok + 1 failed
        assert summary["total_failed"] == 1
        assert summary["failure_rate"] > 0
        assert len(summary["slowest_sessions"]) == 3
        assert "step_baselines" in summary

    def test_fleet_summary_empty(self):
        p = LatencyProfiler()
        summary = p.fleet_summary()
        assert summary["total_sessions"] == 0
        assert summary["failure_rate"] == 0.0

    def test_baseline_window(self):
        p = LatencyProfiler(baseline_window=3)
        for i in range(10):
            s = p.start_session(f"s{i}")
            s.record_step("x", float(i))
        baselines = p.step_baselines()
        # Only last 3 sessions considered (values 7, 8, 9 -> mean ~8)
        assert baselines["x"].count == 3

    def test_report_with_failed_steps(self):
        p = LatencyProfiler()
        s = p.start_session("s1")
        s.record_step("ok", 1.0)
        try:
            with s.step("bad"):
                raise ValueError("err")
        except ValueError:
            pass
        report = p.report("s1")
        assert report.completed_count == 1
        assert report.failed_count == 1
        # Failed step should have error in steps data
        failed_data = [d for d in report.steps if d["status"] == "failed"]
        assert len(failed_data) == 1
        assert failed_data[0]["error"] == "err"

    def test_step_pct_of_total(self):
        p = LatencyProfiler()
        s = p.start_session("s1")
        s.record_step("a", 1.0)
        s.record_step("b", 3.0)
        report = p.report("s1")
        a_step = [d for d in report.steps if d["name"] == "a"][0]
        b_step = [d for d in report.steps if d["name"] == "b"][0]
        assert a_step["pct_of_total"] == pytest.approx(25.0, abs=2)
        assert b_step["pct_of_total"] == pytest.approx(75.0, abs=2)

    def test_real_timing(self):
        """Integration test with actual sleep."""
        p = LatencyProfiler()
        s = p.start_session("real")
        with s.step("pause"):
            time.sleep(0.05)
        assert s.steps[0].duration_ms is not None
        assert s.steps[0].duration_ms >= 40  # at least ~50ms

    def test_multiple_steps_same_name(self):
        """Multiple steps with same name — get_step returns last."""
        session = ProfilingSession(session_id="s1")
        session.record_step("retry", 1.0)
        session.record_step("retry", 2.0)
        assert len(session.steps) == 2
        got = session.get_step("retry")
        assert got is not None
        assert got.duration_s == pytest.approx(2.0, abs=0.05)
