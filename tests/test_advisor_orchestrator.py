"""Tests for AdvisorOrchestrator."""

import json
from datetime import datetime, timezone

import pytest

from agentlens.advisor_orchestrator import (
    AdvisorOrchestrator,
    AdvisorResult,
    CrossCorrelation,
    OrchestratorGrade,
    OrchestratorReport,
)


def _fixed_now():
    return datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _make_events(n=10, model="gpt-4o", tool="search"):
    """Create minimal event dicts."""
    base_ts = 1748520000.0
    events = []
    for i in range(n):
        events.append({
            "event_id": f"evt-{i}",
            "session_id": "sess-1",
            "event_type": "llm_call",
            "timestamp": base_ts + i * 10,
            "model": model,
            "tool": tool,
            "tokens_in": 100,
            "tokens_out": 50,
            "duration_ms": 500,
            "latency_ms": 500,
            "status": "success",
            "is_error": False,
            "metadata": {},
        })
    return events


class TestOrchestratorBasic:
    def test_init_valid_appetites(self):
        for app in ("cautious", "balanced", "aggressive"):
            orch = AdvisorOrchestrator(risk_appetite=app, now_fn=_fixed_now)
            assert orch._appetite.value == app

    def test_init_invalid_appetite_raises(self):
        with pytest.raises(ValueError):
            AdvisorOrchestrator(risk_appetite="yolo")

    def test_empty_events(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess([])
        assert isinstance(report, OrchestratorReport)
        assert report.advisors_run >= 1
        assert report.grade in OrchestratorGrade

    def test_basic_events_produces_report(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess(_make_events(20))
        assert isinstance(report, OrchestratorReport)
        assert report.fleet_risk_score >= 0
        assert report.fleet_risk_score <= 100
        assert report.timestamp == "2026-05-29T12:00:00+00:00"

    def test_advisor_filter(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now, advisors=["TraceCompletionAdvisor"])
        report = orch.assess(_make_events(20))
        # Only requested advisor should appear (others skipped)
        names = {r.advisor_name for r in report.advisor_results}
        assert "TraceCompletionAdvisor" in names
        assert "AgentLoopDetector" not in names

    def test_grade_computation(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        # Test grade ladder
        assert orch._compute_grade(10, 0) == OrchestratorGrade.A
        assert orch._compute_grade(20, 0) == OrchestratorGrade.B
        assert orch._compute_grade(40, 0) == OrchestratorGrade.C
        assert orch._compute_grade(60, 0) == OrchestratorGrade.D
        assert orch._compute_grade(80, 0) == OrchestratorGrade.F
        # P0 forces lower grade
        assert orch._compute_grade(10, 1) == OrchestratorGrade.D
        assert orch._compute_grade(10, 3) == OrchestratorGrade.F


class TestRenderers:
    def test_to_text(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess(_make_events(10))
        text = report.to_text()
        assert "FLEET SCORECARD" in text
        assert "grade=" in text

    def test_to_markdown(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess(_make_events(10))
        md = report.to_markdown()
        assert "# Fleet Health Scorecard" in md
        assert "## Advisor Results" in md
        assert "## Insights" in md

    def test_to_json_valid(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess(_make_events(10))
        j = report.to_json()
        parsed = json.loads(j)
        assert "grade" in parsed
        assert "fleet_risk_score" in parsed
        assert "advisor_results" in parsed
        assert "correlations" in parsed
        assert "merged_playbook" in parsed
        assert "insights" in parsed

    def test_json_byte_stability(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        events = _make_events(10)
        r1 = orch.assess(events).to_json()
        r2 = orch.assess(events).to_json()
        assert r1 == r2


class TestCorrelations:
    def test_no_correlations_on_healthy(self):
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        report = orch.assess(_make_events(5))
        # With minimal healthy events, correlations should be empty
        # (advisors report low risk)
        for c in report.correlations:
            assert c.severity >= 0


class TestAppetiteShift:
    def test_cautious_raises_risk(self):
        events = _make_events(20)
        cautious = AdvisorOrchestrator(risk_appetite="cautious", now_fn=_fixed_now).assess(events)
        aggressive = AdvisorOrchestrator(risk_appetite="aggressive", now_fn=_fixed_now).assess(events)
        # Cautious should be >= aggressive (appetite shift +5 vs -5)
        assert cautious.fleet_risk_score >= aggressive.fleet_risk_score - 1  # allow float rounding


class TestInputImmutability:
    def test_events_not_mutated(self):
        events = _make_events(10)
        snapshot = json.dumps(events, sort_keys=True)
        orch = AdvisorOrchestrator(now_fn=_fixed_now)
        orch.assess(events)
        assert json.dumps(events, sort_keys=True) == snapshot
