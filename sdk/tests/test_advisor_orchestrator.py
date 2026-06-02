"""Tests for agentlens.advisor_orchestrator module."""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import dataclasses
import json

import pytest

from agentlens.advisor_orchestrator import (
    AdvisorOrchestrator,
    AdvisorResult,
    CrossCorrelation,
    CorrelationType,
    OrchestratorGrade,
    OrchestratorPlaybookAction,
    OrchestratorReport,
    OrchestratorRiskAppetite,
    _action_to_dict,
    _extract_grade,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_result(
    name="TestAdvisor",
    grade="B",
    risk_score=30.0,
    p0=0,
    p1=0,
    playbook=None,
    insights=None,
    error=None,
):
    return AdvisorResult(
        advisor_name=name,
        grade=grade,
        risk_score=risk_score,
        p0_count=p0,
        p1_count=p1,
        playbook_actions=playbook or [],
        insights=insights or [],
        error=error,
    )


# ---------------------------------------------------------------------------
# AdvisorOrchestrator construction
# ---------------------------------------------------------------------------


class TestOrchestratorInit:
    def test_valid_appetites(self):
        for appetite in ("cautious", "balanced", "aggressive"):
            orch = AdvisorOrchestrator(risk_appetite=appetite)
            assert orch._appetite == OrchestratorRiskAppetite(appetite)

    def test_invalid_appetite_raises(self):
        with pytest.raises(ValueError, match="Invalid risk_appetite"):
            AdvisorOrchestrator(risk_appetite="yolo")

    def test_custom_now_fn(self):
        orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)
        assert orch._now_fn() == FIXED_NOW

    def test_advisor_filter(self):
        orch = AdvisorOrchestrator(advisors=["TraceCompletionAdvisor"])
        assert orch._advisor_filter == {"TraceCompletionAdvisor"}


# ---------------------------------------------------------------------------
# Grade computation
# ---------------------------------------------------------------------------


class TestComputeGrade:
    def setup_method(self):
        self.orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)

    def test_grade_A(self):
        assert self.orch._compute_grade(10, 0) == OrchestratorGrade.A

    def test_grade_B(self):
        assert self.orch._compute_grade(20, 0) == OrchestratorGrade.B

    def test_grade_C(self):
        assert self.orch._compute_grade(40, 0) == OrchestratorGrade.C

    def test_grade_D_from_risk(self):
        assert self.orch._compute_grade(60, 0) == OrchestratorGrade.D

    def test_grade_D_from_p0(self):
        assert self.orch._compute_grade(10, 1) == OrchestratorGrade.D

    def test_grade_F_from_risk(self):
        assert self.orch._compute_grade(80, 0) == OrchestratorGrade.F

    def test_grade_F_from_p0(self):
        assert self.orch._compute_grade(10, 3) == OrchestratorGrade.F


# ---------------------------------------------------------------------------
# Correlation detection
# ---------------------------------------------------------------------------


class TestDetectCorrelations:
    def setup_method(self):
        self.orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)

    def test_cost_and_loops(self):
        results = [
            _make_result("CostAttributionAdvisor", risk_score=50),
            _make_result("AgentLoopDetector", risk_score=40),
        ]
        corrs = self.orch._detect_correlations(results)
        assert any(c.correlation_type == CorrelationType.COST_AND_LOOPS.value for c in corrs)

    def test_drift_and_regression(self):
        results = [
            _make_result("PromptDriftAdvisor", risk_score=45),
            _make_result("EvalRegressionAdvisor", risk_score=35),
        ]
        corrs = self.orch._detect_correlations(results)
        assert any(c.correlation_type == CorrelationType.DRIFT_AND_REGRESSION.value for c in corrs)

    def test_no_correlation_below_threshold(self):
        results = [
            _make_result("CostAttributionAdvisor", risk_score=10),
            _make_result("AgentLoopDetector", risk_score=10),
        ]
        corrs = self.orch._detect_correlations(results)
        assert len(corrs) == 0

    def test_correlations_sorted_by_severity(self):
        results = [
            _make_result("CostAttributionAdvisor", risk_score=80),
            _make_result("AgentLoopDetector", risk_score=80),
            _make_result("CacheabilityAdvisor", risk_score=35),
        ]
        corrs = self.orch._detect_correlations(results)
        if len(corrs) >= 2:
            assert corrs[0].severity >= corrs[1].severity


# ---------------------------------------------------------------------------
# Playbook merging
# ---------------------------------------------------------------------------


class TestMergePlaybooks:
    def setup_method(self):
        self.orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)

    def test_deduplicates_by_label(self):
        results = [
            _make_result("A", risk_score=50, playbook=[
                {"label": "Fix X", "priority": "P0", "id": "1"},
                {"label": "Fix Y", "priority": "P1", "id": "2"},
            ]),
            _make_result("B", risk_score=30, playbook=[
                {"label": "Fix X", "priority": "P1", "id": "3"},  # duplicate
            ]),
        ]
        merged = self.orch._merge_playbooks(results)
        labels = [a.label for a in merged]
        assert labels.count("Fix X") == 1

    def test_priority_ordering(self):
        results = [
            _make_result("A", risk_score=50, playbook=[
                {"label": "Low", "priority": "P3", "id": "1"},
                {"label": "High", "priority": "P0", "id": "2"},
            ]),
        ]
        merged = self.orch._merge_playbooks(results)
        assert merged[0].label == "High"
        assert merged[1].label == "Low"

    def test_empty_playbooks(self):
        results = [_make_result("A"), _make_result("B")]
        merged = self.orch._merge_playbooks(results)
        assert merged == []


# ---------------------------------------------------------------------------
# Insights synthesis
# ---------------------------------------------------------------------------


class TestSynthesizeInsights:
    def setup_method(self):
        self.orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)

    def test_no_valid_results(self):
        insights = self.orch._synthesize_insights([], [], [], 0, OrchestratorGrade.A)
        assert "NO_ADVISORS_PRODUCED_RESULTS" in insights

    def test_multi_p0_cluster(self):
        valid = [_make_result("A", p0=2), _make_result("B", p0=2)]
        insights = self.orch._synthesize_insights(valid, [], [], 60, OrchestratorGrade.D)
        assert any("MULTI_ADVISOR_P0_CLUSTER" in i for i in insights)

    def test_hotspot_advisor(self):
        valid = [_make_result("Hot", risk_score=70)]
        insights = self.orch._synthesize_insights(valid, [], [], 70, OrchestratorGrade.D)
        assert any("HOTSPOT_ADVISOR" in i for i in insights)

    def test_fleet_healthy(self):
        valid = [_make_result("A", risk_score=5)]
        insights = self.orch._synthesize_insights(valid, [], [], 5, OrchestratorGrade.A)
        assert any("FLEET_HEALTHY" in i for i in insights)

    def test_fleet_critical(self):
        valid = [_make_result("A", risk_score=80, p0=3)]
        insights = self.orch._synthesize_insights(valid, [], [], 80, OrchestratorGrade.F)
        assert any("FLEET_CRITICAL" in i for i in insights)

    def test_advisor_failures_noted(self):
        valid = [_make_result("A")]
        failed = [_make_result("B", error="boom")]
        insights = self.orch._synthesize_insights(valid, failed, [], 20, OrchestratorGrade.B)
        assert any("ADVISOR_FAILURES" in i for i in insights)


# ---------------------------------------------------------------------------
# Full assess flow (mocked advisors)
# ---------------------------------------------------------------------------


class TestAssess:
    def test_assess_with_all_advisors_failing(self):
        """If all advisors fail, report should still be produced."""
        orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)
        # Patch _get_advisors to return advisors that always raise
        def _bad_advisors():
            def fail(events):
                raise RuntimeError("broken")
            return [("Bad1", fail), ("Bad2", fail)]

        orch._get_advisors = _bad_advisors
        report = orch.assess([])
        assert report.advisors_failed == 2
        assert report.grade == OrchestratorGrade.A  # no valid risk
        assert all(r.error is not None for r in report.advisor_results)

    def test_assess_with_mock_advisors(self):
        orch = AdvisorOrchestrator(now_fn=lambda: FIXED_NOW)

        def _mock_advisors():
            def good(events):
                return _make_result("MockAdvisor", risk_score=25)
            return [("MockAdvisor", good)]

        orch._get_advisors = _mock_advisors
        report = orch.assess([{"type": "span"}])
        assert report.advisors_run == 1
        assert report.advisors_failed == 0
        assert report.fleet_risk_score >= 0

    def test_assess_appetite_cautious_increases_risk(self):
        orch_cautious = AdvisorOrchestrator(risk_appetite="cautious", now_fn=lambda: FIXED_NOW)
        orch_aggressive = AdvisorOrchestrator(risk_appetite="aggressive", now_fn=lambda: FIXED_NOW)

        def _mock():
            def good(events):
                return _make_result("M", risk_score=50)
            return [("M", good)]

        orch_cautious._get_advisors = _mock
        orch_aggressive._get_advisors = _mock

        r_c = orch_cautious.assess([])
        r_a = orch_aggressive.assess([])
        assert r_c.fleet_risk_score > r_a.fleet_risk_score

    def test_assess_advisor_filter(self):
        orch = AdvisorOrchestrator(advisors=["OnlyThis"], now_fn=lambda: FIXED_NOW)

        def _mock():
            def a(events):
                return _make_result("OnlyThis", risk_score=20)
            def b(events):
                return _make_result("NotThis", risk_score=80)
            return [("OnlyThis", a), ("NotThis", b)]

        orch._get_advisors = _mock
        report = orch.assess([])
        assert report.advisors_run == 1
        assert report.advisor_results[0].advisor_name == "OnlyThis"


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------


class TestReportSerialization:
    def _make_report(self):
        return OrchestratorReport(
            grade=OrchestratorGrade.B,
            fleet_risk_score=25.0,
            advisor_results=[_make_result("X", risk_score=25)],
            correlations=[],
            merged_playbook=[
                OrchestratorPlaybookAction(
                    id="act1", priority="P1", label="Do thing",
                    reason="because", owner="platform",
                    blast_radius=2, reversibility="easy",
                    source_advisor="X",
                )
            ],
            insights=["FLEET_NOMINAL"],
            timestamp=FIXED_NOW.isoformat(),
            risk_appetite="balanced",
            advisors_run=1,
            advisors_failed=0,
        )

    def test_to_text(self):
        report = self._make_report()
        text = report.to_text()
        assert "FLEET SCORECARD" in text
        assert "grade=B" in text
        assert "Do thing" in text

    def test_to_markdown(self):
        report = self._make_report()
        md = report.to_markdown()
        assert "# Fleet Health Scorecard" in md
        assert "| X |" in md

    def test_to_json_valid(self):
        report = self._make_report()
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["grade"] == "B"
        assert parsed["fleet_risk_score"] == 25.0
        assert len(parsed["merged_playbook"]) == 1


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_action_to_dict_dataclass(self):
        @dataclasses.dataclass
        class FakeAction:
            label: str
            priority: str
        d = _action_to_dict(FakeAction(label="test", priority="P0"))
        assert d["label"] == "test"
        assert d["priority"] == "P0"

    def test_action_to_dict_int_priority(self):
        @dataclasses.dataclass
        class FakeAction:
            label: str
            priority: int
        d = _action_to_dict(FakeAction(label="x", priority=1))
        assert d["priority"] == "P1"

    def test_action_to_dict_enum_priority(self):
        from enum import Enum
        class P(Enum):
            HIGH = "P0"

        @dataclasses.dataclass
        class FakeAction:
            label: str
            priority: P
        d = _action_to_dict(FakeAction(label="x", priority=P.HIGH))
        assert d["priority"] == "P0"

    def test_action_to_dict_plain_object(self):
        class Obj:
            def __init__(self):
                self.label = "hello"
                self.priority = "P2"
        d = _action_to_dict(Obj())
        assert d["label"] == "hello"

    def test_extract_grade_with_value_attr(self):
        class R:
            grade = OrchestratorGrade.A
        assert _extract_grade(R()) == "A"

    def test_extract_grade_string(self):
        class R:
            grade = "C"
        assert _extract_grade(R()) == "C"

    def test_extract_grade_missing(self):
        class R:
            pass
        assert _extract_grade(R()) == "?"


# ---------------------------------------------------------------------------
# Enum / dataclass tests
# ---------------------------------------------------------------------------


class TestEnumsAndDataclasses:
    def test_orchestrator_grade_values(self):
        assert OrchestratorGrade.A.value == "A"
        assert OrchestratorGrade.F.value == "F"

    def test_risk_appetite_values(self):
        assert OrchestratorRiskAppetite.cautious.value == "cautious"

    def test_correlation_type_values(self):
        assert CorrelationType.COST_AND_LOOPS.value == "COST_AND_LOOPS"

    def test_advisor_result_frozen(self):
        r = _make_result()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.advisor_name = "changed"

    def test_cross_correlation_frozen(self):
        c = CrossCorrelation(
            correlation_type="X", description="d", severity=50, related_advisors=("A",)
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.severity = 99
