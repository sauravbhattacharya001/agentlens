"""Tests for CapacityPlanner module."""

import pytest
from datetime import datetime, timedelta

from agentlens.capacity import (
    CapacityPlanner, WorkloadSample, ResourceKind, ScalingAction,
    BottleneckSeverity, TrendDirection, WorkloadProjection, Bottleneck,
    ResourceSizing, ScalingRecommendation, CapacityReport,
)


def _sample(hours_ago: float = 0, rpm: float = 100, sessions: int = 10,
            latency: float = 200, tokens: float = 5000, errors: float = 0.01,
            cpu: float = 0.5, mem: float = 0.4) -> WorkloadSample:
    return WorkloadSample(
        timestamp=datetime(2026, 3, 8, 12, 0) - timedelta(hours=hours_ago),
        active_sessions=sessions, requests_per_minute=rpm,
        avg_latency_ms=latency, token_throughput=tokens,
        error_rate=errors, cpu_utilization=cpu, memory_utilization=mem,
    )


class TestWorkloadSample:
    def test_valid_sample(self):
        s = _sample()
        assert s.active_sessions == 10
        assert s.requests_per_minute == 100

    def test_negative_sessions_rejected(self):
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), active_sessions=-1)

    def test_negative_rpm_rejected(self):
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), requests_per_minute=-5)

    def test_error_rate_bounds(self):
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), error_rate=1.5)
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), error_rate=-0.1)

    def test_cpu_bounds(self):
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), cpu_utilization=1.1)

    def test_memory_bounds(self):
        with pytest.raises(ValueError):
            WorkloadSample(timestamp=datetime.now(), memory_utilization=-0.1)


class TestCapacityPlannerBasics:
    def test_empty_planner(self):
        p = CapacityPlanner()
        assert p.sample_count == 0
        assert p.current_utilization()["cpu"] == 0
        assert p.headroom_score() == 100.0

    def test_add_sample(self):
        p = CapacityPlanner()
        p.add_sample(_sample())
        assert p.sample_count == 1

    def test_add_samples_bulk(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i) for i in range(5)])
        assert p.sample_count == 5

    def test_clear(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i) for i in range(3)])
        p.clear()
        assert p.sample_count == 0

    def test_max_samples_cap(self):
        p = CapacityPlanner(max_samples=5)
        p.add_samples([_sample(i) for i in range(10)])
        assert p.sample_count == 5

    def test_current_utilization(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.6, mem=0.4))
        cur = p.current_utilization()
        assert cur["cpu"] == pytest.approx(0.6)
        assert cur["memory"] == pytest.approx(0.4)

    def test_peak_utilization(self):
        p = CapacityPlanner()
        p.add_samples([_sample(2, cpu=0.3), _sample(1, cpu=0.8), _sample(0, cpu=0.5)])
        peak = p.peak_utilization()
        assert peak["cpu"] == pytest.approx(0.8)


class TestTrends:
    def test_stable_trend(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, cpu=0.5) for i in range(10)])
        trends = p.compute_trends()
        assert trends["cpu"] == TrendDirection.STABLE

    def test_rising_trend(self):
        p = CapacityPlanner()
        for i in range(10):
            p.add_sample(_sample(10 - i, cpu=0.3 + i * 0.05))
        trends = p.compute_trends()
        assert trends["cpu"] == TrendDirection.RISING

    def test_falling_trend(self):
        p = CapacityPlanner()
        for i in range(10):
            p.add_sample(_sample(10 - i, cpu=0.8 - i * 0.05))
        trends = p.compute_trends()
        assert trends["cpu"] == TrendDirection.FALLING

    def test_single_sample_stable(self):
        p = CapacityPlanner()
        p.add_sample(_sample())
        trends = p.compute_trends()
        assert all(v == TrendDirection.STABLE for v in trends.values())


class TestProjections:
    def test_no_projections_with_single_sample(self):
        p = CapacityPlanner()
        p.add_sample(_sample())
        assert p.project_workload() == []

    def test_projections_count(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, rpm=100 + i * 10) for i in range(10)])
        projs = p.project_workload(horizon_hours=24, steps=6)
        assert len(projs) == 6

    def test_projections_are_future(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i) for i in range(5)])
        projs = p.project_workload()
        ss = sorted(p._samples, key=lambda s: s.timestamp)
        for proj in projs:
            assert proj.timestamp > ss[-1].timestamp

    def test_confidence_decays(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i) for i in range(5)])
        projs = p.project_workload()
        # First projection should have higher confidence than last
        assert projs[0].confidence > projs[-1].confidence

    def test_projected_values_non_negative(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, rpm=max(0, 50 - i * 10)) for i in range(10)])
        for proj in p.project_workload():
            assert proj.projected_rpm >= 0
            assert proj.projected_sessions >= 0
            assert proj.projected_tokens >= 0


class TestBottlenecks:
    def test_no_bottlenecks_healthy(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, cpu=0.3, mem=0.3, errors=0.01) for i in range(5)])
        assert len(p.detect_bottlenecks()) == 0

    def test_cpu_critical(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.96, mem=0.3))
        bns = p.detect_bottlenecks()
        cpu_bns = [b for b in bns if b.resource == ResourceKind.COMPUTE]
        assert len(cpu_bns) == 1
        assert cpu_bns[0].severity == BottleneckSeverity.CRITICAL

    def test_cpu_high(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.85, mem=0.3))
        bns = p.detect_bottlenecks()
        cpu_bns = [b for b in bns if b.resource == ResourceKind.COMPUTE]
        assert len(cpu_bns) == 1
        assert cpu_bns[0].severity == BottleneckSeverity.HIGH

    def test_memory_bottleneck(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.3, mem=0.90))
        bns = p.detect_bottlenecks()
        mem_bns = [b for b in bns if b.resource == ResourceKind.MEMORY]
        assert len(mem_bns) == 1

    def test_error_rate_bottleneck(self):
        p = CapacityPlanner()
        p.add_sample(_sample(errors=0.08))
        bns = p.detect_bottlenecks()
        err_bns = [b for b in bns if b.resource == ResourceKind.API_RATE]
        assert len(err_bns) == 1

    def test_rising_cpu_projected(self):
        p = CapacityPlanner()
        for i in range(10):
            p.add_sample(_sample(10 - i, cpu=0.5 + i * 0.02))
        bns = p.detect_bottlenecks()
        cpu_bns = [b for b in bns if b.resource == ResourceKind.COMPUTE]
        assert len(cpu_bns) >= 1
        assert any(b.projected_saturation_hours is not None for b in cpu_bns)

    def test_empty_no_bottlenecks(self):
        p = CapacityPlanner()
        assert p.detect_bottlenecks() == []


class TestResourceSizing:
    def test_basic_sizing(self):
        p = CapacityPlanner()
        sizing = p.size_resources(target_rpm=500)
        assert sizing.recommended_instances >= 1
        assert sizing.target_rpm == 500

    def test_headroom_applied(self):
        p = CapacityPlanner(headroom_factor=2.0)
        sizing = p.size_resources(target_rpm=100, max_rpm_per_instance=100)
        assert sizing.recommended_instances >= 2

    def test_sizing_with_data(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, rpm=100, cpu=0.5, tokens=5000) for i in range(5)])
        sizing = p.size_resources(target_rpm=300)
        assert sizing.recommended_instances >= 1
        assert sizing.estimated_monthly_tokens > 0

    def test_low_latency_note(self):
        p = CapacityPlanner()
        sizing = p.size_resources(target_rpm=100, target_latency_ms=100)
        assert any("GPU" in n for n in sizing.notes)

    def test_large_cluster_note(self):
        p = CapacityPlanner()
        sizing = p.size_resources(target_rpm=1000, max_rpm_per_instance=50)
        assert any("auto-scaling" in n for n in sizing.notes)


class TestScalingRecommendations:
    def test_healthy_no_action(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, cpu=0.3, mem=0.3, errors=0.01) for i in range(5)])
        recs = p.scaling_recommendations()
        assert any(r.action == ScalingAction.NONE for r in recs)

    def test_critical_bottleneck_urgent(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.96))
        recs = p.scaling_recommendations()
        assert any(r.action == ScalingAction.URGENT for r in recs)

    def test_falling_demand_scale_down(self):
        p = CapacityPlanner()
        for i in range(10):
            p.add_sample(_sample(10 - i, cpu=0.5 - i * 0.03, rpm=200 - i * 15))
        recs = p.scaling_recommendations()
        assert any(r.action == ScalingAction.SCALE_DOWN for r in recs)

    def test_high_errors_optimize(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, errors=0.08, cpu=0.4) for i in range(5)])
        recs = p.scaling_recommendations()
        assert any(r.action == ScalingAction.OPTIMIZE for r in recs)

    def test_empty_planner_no_recs(self):
        p = CapacityPlanner()
        assert p.scaling_recommendations() == []


class TestHeadroomScore:
    def test_full_headroom(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.0, mem=0.0, errors=0.0))
        assert p.headroom_score() == 100.0

    def test_no_headroom(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=1.0, mem=1.0, errors=0.05))
        assert p.headroom_score() == 0.0

    def test_moderate_headroom(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.5, mem=0.5, errors=0.02))
        score = p.headroom_score()
        assert 30 < score < 80


class TestCapacityReport:
    def test_report_structure(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, cpu=0.4 + i * 0.02) for i in range(10)])
        report = p.report()
        assert isinstance(report, CapacityReport)
        assert report.sample_count == 10
        assert report.headroom_score > 0
        assert len(report.summary) > 0

    def test_report_to_dict(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i) for i in range(5)])
        d = p.report().to_dict()
        assert "generated_at" in d
        assert "headroom_score" in d
        assert "summary" in d

    def test_critical_report_summary(self):
        p = CapacityPlanner()
        p.add_sample(_sample(cpu=0.96, mem=0.95, errors=0.08))
        report = p.report()
        assert "Critical" in report.summary or "critical" in report.summary.lower()

    def test_healthy_report_summary(self):
        p = CapacityPlanner()
        p.add_samples([_sample(i, cpu=0.2, mem=0.2, errors=0.0) for i in range(5)])
        report = p.report()
        assert "Healthy" in report.summary or "healthy" in report.summary.lower()

    def test_empty_report(self):
        p = CapacityPlanner()
        report = p.report()
        assert report.sample_count == 0
        assert report.headroom_score == 100.0


class TestCustomThresholds:
    def test_custom_cpu_threshold(self):
        p = CapacityPlanner(max_cpu_threshold=0.60)
        p.add_sample(_sample(cpu=0.65))
        bns = p.detect_bottlenecks()
        assert any(b.resource == ResourceKind.COMPUTE for b in bns)

    def test_custom_error_threshold(self):
        p = CapacityPlanner(max_error_threshold=0.02)
        p.add_sample(_sample(errors=0.03))
        bns = p.detect_bottlenecks()
        assert any(b.resource == ResourceKind.API_RATE for b in bns)

    def test_custom_memory_threshold(self):
        p = CapacityPlanner(max_memory_threshold=0.70)
        p.add_sample(_sample(mem=0.75))
        bns = p.detect_bottlenecks()
        assert any(b.resource == ResourceKind.MEMORY for b in bns)
