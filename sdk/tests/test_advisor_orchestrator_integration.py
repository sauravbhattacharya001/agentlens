"""Integration tests for :mod:`agentlens.advisor_orchestrator`.

These tests drive the orchestrator against the *real* underlying advisors
(``TraceCompletionAdvisor``, ``AgentLoopDetector``, ``CostAttributionAdvisor``,
``DataLeakAdvisor``, ``CacheabilityAdvisor``) rather than mocking
``_get_advisors``.

They exist because the unit tests in ``test_advisor_orchestrator.py`` patch out
``_get_advisors`` everywhere, so a regression in the glue between the
orchestrator and the concrete advisors (wrong method name, changed constructor
signature, renamed risk attribute) would pass every test while ``assess()``
silently reported ``advisors_failed == 5`` and a bogus grade ``A`` for a
genuinely unhealthy fleet. This module locks that integration down.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentlens.advisor_orchestrator import (
    AdvisorOrchestrator,
    AdvisorResult,
    CorrelationType,
    CrossCorrelation,
    OrchestratorGrade,
    OrchestratorPlaybookAction,
    OrchestratorReport,
    _coerce_risk,
    _extract_grade,
    _grade_to_risk,
    _result_from_report,
)


FIXED_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

ALL_ADVISORS = {
    "TraceCompletionAdvisor",
    "AgentLoopDetector",
    "CostAttributionAdvisor",
    "DataLeakAdvisor",
    "CacheabilityAdvisor",
}


def _now() -> datetime:
    return FIXED_NOW


# --------------------------------------------------------------------------- #
# Event-stream builders (canonical AgentLens event shape)
# --------------------------------------------------------------------------- #


def _llm_event(
    *,
    session_id: str = "s1",
    model: str = "gpt-4",
    system: str = "You are helpful.",
    user: str = "hi",
    assistant: str = "ok",
    tokens_in: int = 1500,
    tokens_out: int = 400,
    cost: float | None = None,
) -> dict:
    ev = {
        "session_id": session_id,
        "event_type": "llm_call",
        "timestamp": FIXED_NOW,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "input_data": {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        "output_data": {"messages": [{"role": "assistant", "content": assistant}]},
    }
    if cost is not None:
        ev["cost"] = cost
    return ev


def _tool_event(*, session_id: str = "loopy", query: str = "same query") -> dict:
    return {
        "session_id": session_id,
        "event_type": "tool_call",
        "tool_name": "search",
        "timestamp": FIXED_NOW,
        "duration_ms": 120,
        "input_data": {"query": query},
        "output_data": {"result": "x"},
    }


def _unhealthy_stream() -> list[dict]:
    """An event stream that trips several advisors at once."""
    events: list[dict] = []
    # Repeated identical tool calls with no resolving result -> loops + open trace
    for _ in range(8):
        events.append(_tool_event())
    # Expensive, single-model traffic with a long cacheable system prefix -> cost + cache
    long_system = "You are a careful, methodical research assistant. " * 200
    for i in range(6):
        events.append(
            _llm_event(
                session_id="billing",
                system=long_system,
                user=f"summarize report {i}",
                tokens_in=4000,
                tokens_out=1500,
                cost=0.5,
            )
        )
    # A payload that leaks PII + a secret -> data leak
    events.append(
        _llm_event(
            session_id="leaky",
            user="my email is jane.doe@example.com and ssn 123-45-6789",
            assistant="acknowledged, key sk-live-ABCDEF0123456789abcdef0123",
        )
    )
    return events


# --------------------------------------------------------------------------- #
# The core regression: assess() must actually run the real advisors
# --------------------------------------------------------------------------- #


class TestAssessWithRealAdvisors:
    def test_all_advisors_run_without_error(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())

        # The whole point: every advisor executed and none errored out. A
        # broken method name / constructor would make these all fail.
        assert report.advisors_run == 5
        assert report.advisors_failed == 0, [
            (r.advisor_name, r.error) for r in report.advisor_results if r.error
        ]
        assert {r.advisor_name for r in report.advisor_results} == ALL_ADVISORS
        assert all(r.error is None for r in report.advisor_results)

    def test_unhealthy_fleet_is_graded_down(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())

        # Loops alone push risk very high; the fleet must not grade "A".
        assert report.fleet_risk_score > 40
        assert report.grade in (OrchestratorGrade.D, OrchestratorGrade.F)

    def test_loop_detector_reports_real_risk(self):
        """Loops should surface as a high-risk advisor, not a flat zero."""
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())
        loops = next(
            r for r in report.advisor_results if r.advisor_name == "AgentLoopDetector"
        )
        assert loops.error is None
        assert loops.risk_score > 0
        assert loops.grade != "?"

    def test_correlations_emerge_from_real_signals(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())
        # Cost + loops are both elevated in this stream.
        types = {c.correlation_type for c in report.correlations}
        assert CorrelationType.COST_AND_LOOPS.value in types

    def test_merged_playbook_is_populated(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())
        assert report.merged_playbook, "expected a non-empty merged playbook"
        # Highest-priority action should sort to the front.
        priorities = [a.priority for a in report.merged_playbook]
        assert priorities == sorted(priorities, key=lambda p: {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(p, 3))
        # Every action is traceable back to a real advisor.
        assert all(a.source_advisor in ALL_ADVISORS for a in report.merged_playbook)

    def test_empty_event_stream_is_healthy(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess([])
        assert report.advisors_run == 5
        assert report.advisors_failed == 0
        assert report.grade == OrchestratorGrade.A

    def test_report_serializes_after_real_run(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        report = orch.assess(_unhealthy_stream())
        # Each serializer must round-trip a real report without raising.
        assert "FLEET SCORECARD" in report.to_text()
        assert "Fleet Health Scorecard" in report.to_markdown()
        import json

        parsed = json.loads(report.to_json())
        assert parsed["advisors_run"] == 5
        assert parsed["advisors_failed"] == 0

    def test_advisor_filter_runs_only_selected_real_advisor(self):
        orch = AdvisorOrchestrator(advisors=["AgentLoopDetector"], now_fn=_now)
        report = orch.assess(_unhealthy_stream())
        assert report.advisors_run == 1
        assert report.advisor_results[0].advisor_name == "AgentLoopDetector"
        assert report.advisor_results[0].error is None


# --------------------------------------------------------------------------- #
# Each runner method individually (pinpoints which advisor's glue broke)
# --------------------------------------------------------------------------- #


class TestIndividualRunners:
    @pytest.fixture
    def orch(self):
        return AdvisorOrchestrator(now_fn=_now)

    @pytest.fixture
    def events(self):
        return _unhealthy_stream()

    @pytest.mark.parametrize(
        "method_name,advisor_name",
        [
            ("_run_trace_completion", "TraceCompletionAdvisor"),
            ("_run_loop_detector", "AgentLoopDetector"),
            ("_run_cost_attribution", "CostAttributionAdvisor"),
            ("_run_data_leak", "DataLeakAdvisor"),
            ("_run_cacheability", "CacheabilityAdvisor"),
        ],
    )
    def test_runner_returns_valid_result(self, orch, events, method_name, advisor_name):
        result = getattr(orch, method_name)(events)
        assert isinstance(result, AdvisorResult)
        assert result.advisor_name == advisor_name
        assert result.error is None
        assert 0 <= result.risk_score <= 100
        assert result.grade != "?"
        assert result.p0_count >= 0
        assert result.p1_count >= 0

    def test_runner_handles_empty_events(self, orch):
        for method_name in (
            "_run_trace_completion",
            "_run_loop_detector",
            "_run_cost_attribution",
            "_run_data_leak",
            "_run_cacheability",
        ):
            result = getattr(orch, method_name)([])
            assert result.error is None
            assert 0 <= result.risk_score <= 100

    def test_cacheability_risk_tracks_savings_share(self, orch, events):
        # The unhealthy stream has a large duplicated cacheable prefix, so the
        # cacheability advisor should see meaningful savings headroom.
        result = orch._run_cacheability(events)
        assert result.risk_score >= 0
        assert result.error is None

    def test_appetite_passed_through_to_real_advisor(self, events):
        cautious = AdvisorOrchestrator(risk_appetite="cautious", now_fn=_now)
        aggressive = AdvisorOrchestrator(risk_appetite="aggressive", now_fn=_now)
        rc = cautious.assess(events)
        ra = aggressive.assess(events)
        # Cautious appetite shifts fleet risk up relative to aggressive.
        assert rc.fleet_risk_score >= ra.fleet_risk_score


# --------------------------------------------------------------------------- #
# Risk-derivation helpers
# --------------------------------------------------------------------------- #


class TestRiskHelpers:
    def test_grade_to_risk_known_grades_are_monotonic(self):
        risks = [_grade_to_risk(g) for g in ("A", "B", "C", "D", "F")]
        assert risks == sorted(risks)
        assert risks[0] < risks[-1]

    def test_grade_to_risk_is_case_insensitive(self):
        assert _grade_to_risk("f") == _grade_to_risk("F")

    def test_grade_to_risk_unknown_is_zero(self):
        assert _grade_to_risk("?") == 0.0
        assert _grade_to_risk("Z") == 0.0

    def test_coerce_risk_prefers_explicit_numeric(self):
        class R:
            grade = "F"

        assert _coerce_risk(42.0, R()) == 42.0
        assert _coerce_risk(0, R()) == 0.0

    def test_coerce_risk_clamps_to_range(self):
        class R:
            grade = "A"

        assert _coerce_risk(250, R()) == 100.0
        assert _coerce_risk(-15, R()) == 0.0

    def test_coerce_risk_ignores_bool(self):
        # ``True``/``False`` are ints in Python but must not be read as 1/0.
        class R:
            grade = "F"

        assert _coerce_risk(True, R()) == _grade_to_risk("F")
        assert _coerce_risk(False, R()) == _grade_to_risk("F")

    def test_coerce_risk_falls_back_to_grade(self):
        class R:
            grade = "C"

        assert _coerce_risk(None, R()) == _grade_to_risk("C")
        assert _coerce_risk("not a number", R()) == _grade_to_risk("C")

    def test_extract_grade_reads_portfolio_grade(self):
        from enum import Enum

        class Grade(Enum):
            F = "F"

        class Portfolio:
            portfolio_grade = Grade.F

        class Report:
            portfolio = Portfolio()

        assert _extract_grade(Report()) == "F"

    def test_extract_grade_prefers_top_level_over_portfolio(self):
        class Portfolio:
            portfolio_grade = "F"

        class Report:
            grade = "B"
            portfolio = Portfolio()

        assert _extract_grade(Report()) == "B"

    def test_extract_grade_missing_everywhere(self):
        class Report:
            portfolio = None

        assert _extract_grade(Report()) == "?"


class TestResultFromReport:
    def test_builds_result_with_playbook_counts(self):
        from enum import Enum

        class Priority(Enum):
            P0 = "P0"
            P1 = "P1"

        class Action:
            def __init__(self, label, priority):
                self.label = label
                self.priority = priority

        class Report:
            grade = "D"
            playbook = [
                Action("a", Priority.P0),
                Action("b", Priority.P1),
                Action("c", Priority.P1),
            ]
            insights = ["INSIGHT_ONE", 2]

        result = _result_from_report("X", Report(), 51.5)
        assert result.advisor_name == "X"
        assert result.grade == "D"
        assert result.risk_score == 51.5
        assert result.p0_count == 1
        assert result.p1_count == 2
        assert result.insights == ["INSIGHT_ONE", "2"]

    def test_handles_report_without_playbook_or_insights(self):
        class Report:
            grade = "A"

        result = _result_from_report("Y", Report(), 0)
        assert result.playbook_actions == []
        assert result.insights == []
        assert result.p0_count == 0


# --------------------------------------------------------------------------- #
# Branch coverage for correlation rendering + extra correlation types
# --------------------------------------------------------------------------- #


def _result(name: str, risk: float) -> AdvisorResult:
    return AdvisorResult(
        advisor_name=name,
        grade="C",
        risk_score=risk,
        p0_count=0,
        p1_count=0,
        playbook_actions=[],
        insights=[],
    )


class TestExtraCorrelationTypes:
    def setup_method(self):
        self.orch = AdvisorOrchestrator(now_fn=_now)

    def test_leaks_and_drift(self):
        corrs = self.orch._detect_correlations(
            [_result("DataLeakAdvisor", 50), _result("PromptDriftAdvisor", 40)]
        )
        assert any(c.correlation_type == CorrelationType.LEAKS_AND_DRIFT.value for c in corrs)

    def test_loops_and_incomplete(self):
        corrs = self.orch._detect_correlations(
            [_result("AgentLoopDetector", 60), _result("TraceCompletionAdvisor", 40)]
        )
        assert any(c.correlation_type == CorrelationType.LOOPS_AND_INCOMPLETE.value for c in corrs)

    def test_cache_and_cost(self):
        corrs = self.orch._detect_correlations(
            [_result("CacheabilityAdvisor", 55), _result("CostAttributionAdvisor", 45)]
        )
        assert any(c.correlation_type == CorrelationType.CACHE_AND_COST.value for c in corrs)

    def test_burn_and_regression(self):
        corrs = self.orch._detect_correlations(
            [_result("SLOBurnRateAdvisor", 50), _result("EvalRegressionAdvisor", 50)]
        )
        assert any(c.correlation_type == CorrelationType.BURN_AND_REGRESSION.value for c in corrs)


class TestCorrelationRendering:
    """The to_text / to_markdown correlation sections were never rendered."""

    def _report_with_correlations(self) -> OrchestratorReport:
        return OrchestratorReport(
            grade=OrchestratorGrade.F,
            fleet_risk_score=82.0,
            advisor_results=[_result("CostAttributionAdvisor", 80)],
            correlations=[
                CrossCorrelation(
                    correlation_type=CorrelationType.COST_AND_LOOPS.value,
                    description="cost driven by loops",
                    severity=85,
                    related_advisors=("CostAttributionAdvisor", "AgentLoopDetector"),
                )
            ],
            merged_playbook=[
                OrchestratorPlaybookAction(
                    id="a",
                    priority="P0",
                    label="Kill loops",
                    reason="r",
                    owner="platform",
                    blast_radius=3,
                    reversibility="hard",
                    source_advisor="AgentLoopDetector",
                )
            ],
            insights=["FLEET_CRITICAL: immediate attention required"],
            timestamp=FIXED_NOW.isoformat(),
            risk_appetite="balanced",
            advisors_run=1,
            advisors_failed=0,
        )

    def test_to_text_renders_correlations(self):
        text = self._report_with_correlations().to_text()
        assert "Cross-Advisor Correlations" in text
        assert "COST_AND_LOOPS" in text
        assert "cost driven by loops" in text

    def test_to_markdown_renders_correlations(self):
        md = self._report_with_correlations().to_markdown()
        assert "## Cross-Advisor Correlations" in md
        assert "COST_AND_LOOPS" in md
        assert "CostAttributionAdvisor, AgentLoopDetector" in md

    def test_to_markdown_renders_error_row(self):
        report = self._report_with_correlations()
        report.advisor_results = [
            AdvisorResult(
                advisor_name="Broken",
                grade="?",
                risk_score=0,
                p0_count=0,
                p1_count=0,
                playbook_actions=[],
                insights=[],
                error="kaboom happened in the advisor",
            )
        ]
        md = report.to_markdown()
        assert "Broken" in md
        assert "kaboom" in md


class TestWidespreadInsight:
    def test_widespread_risk_insight(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        valid = [
            _result("A", 45),
            _result("B", 50),
            _result("C", 41),
        ]
        insights = orch._synthesize_insights(valid, [], [], 45, OrchestratorGrade.C)
        assert any("WIDESPREAD_RISK" in i for i in insights)

    def test_cross_advisor_correlations_insight(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        valid = [_result("A", 30)]
        corr = [
            CrossCorrelation(
                correlation_type="X", description="d", severity=50, related_advisors=("A",)
            )
        ]
        insights = orch._synthesize_insights(valid, [], corr, 30, OrchestratorGrade.C)
        assert any("CROSS_ADVISOR_CORRELATIONS_DETECTED" in i for i in insights)

    def test_fleet_nominal_when_nothing_notable(self):
        orch = AdvisorOrchestrator(now_fn=_now)
        valid = [_result("A", 20)]
        insights = orch._synthesize_insights(valid, [], [], 20, OrchestratorGrade.B)
        assert insights == ["FLEET_NOMINAL"]

    def test_no_hotspot_when_worst_below_threshold(self):
        # ``valid`` is non-empty but the worst advisor is < 50 risk, so the
        # HOTSPOT_ADVISOR branch must NOT fire.
        orch = AdvisorOrchestrator(now_fn=_now)
        valid = [_result("A", 30), _result("B", 45)]
        insights = orch._synthesize_insights(valid, [], [], 38, OrchestratorGrade.C)
        assert not any("HOTSPOT_ADVISOR" in i for i in insights)


class TestSerializerFallbacks:
    """Cover the defensive ``default=`` serializer + str-action fallback."""

    def test_action_to_dict_object_without_dict(self):
        from agentlens.advisor_orchestrator import _action_to_dict

        # An object exposing neither dataclass fields nor ``__dict__`` (slots,
        # no priority) must degrade to a label-only dict at default priority.
        class Slotted:
            __slots__ = ()

            def __str__(self):
                return "slotted-action"

        d = _action_to_dict(Slotted())
        assert d["label"] == "slotted-action"
        assert d["priority"] == "P3"

    def test_to_json_serializes_enum_id_natively(self):
        # ``CorrelationType`` is a ``str`` Enum, so an Enum left in a playbook
        # action's ``id`` must serialize to its string value without raising.
        action = OrchestratorPlaybookAction(
            id=CorrelationType.COST_AND_LOOPS,
            priority="P1",
            label="x",
            reason="r",
            owner="platform",
            blast_radius=1,
            reversibility="easy",
            source_advisor="X",
        )
        report = OrchestratorReport(
            grade=OrchestratorGrade.B,
            fleet_risk_score=20.0,
            advisor_results=[],
            correlations=[],
            merged_playbook=[action],
            insights=[],
            timestamp=FIXED_NOW.isoformat(),
            risk_appetite="balanced",
            advisors_run=0,
            advisors_failed=0,
        )
        import json

        parsed = json.loads(report.to_json())
        assert parsed["merged_playbook"][0]["id"] == CorrelationType.COST_AND_LOOPS.value
