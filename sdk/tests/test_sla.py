"""Tests for the SLA Monitor module."""

import math
import pytest
from agentlens.sla import (
    ComplianceStatus,
    ObjectiveKind,
    ObjectiveResult,
    SLAEvaluator,
    SLAPolicy,
    SLAReport,
    SLObjective,
    development_policy,
    production_policy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(sid: str, events: list[dict]) -> dict:
    return {"session_id": sid, "events": events}


def _event(
    event_type: str = "llm_call",
    duration_ms: float | None = 100.0,
    tokens_in: int = 50,
    tokens_out: int = 50,
    tool_call: dict | None = None,
) -> dict:
    return {
        "event_type": event_type,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tool_call": tool_call,
    }


def _tool_event(
    tool_name: str = "search",
    error: bool = False,
    duration_ms: float = 100.0,
) -> dict:
    output = {"error": "failed"} if error else {"result": "ok"}
    return _event(
        event_type="tool_call",
        duration_ms=duration_ms,
        tool_call={
            "tool_name": tool_name,
            "tool_output": output,
        },
    )


def _fast_session(sid: str, n_events: int = 10) -> dict:
    return _session(sid, [_event(duration_ms=50.0) for _ in range(n_events)])


def _slow_session(sid: str, latency: float = 5000.0, n_events: int = 10) -> dict:
    return _session(sid, [_event(duration_ms=latency) for _ in range(n_events)])


def _error_session(sid: str, error_rate: float = 0.5, n_events: int = 10) -> dict:
    n_errors = int(n_events * error_rate)
    events = [_event(event_type="error") for _ in range(n_errors)]
    events += [_event() for _ in range(n_events - n_errors)]
    return _session(sid, events)


# ---------------------------------------------------------------------------
# SLObjective tests
# ---------------------------------------------------------------------------

class TestSLObjective:
    def test_latency_p95_factory(self):
        obj = SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0)
        assert obj.kind == ObjectiveKind.LATENCY_P95
        assert obj.target == 3000.0
        assert obj.slo_percent == 99.0
        assert "P95" in obj.name

    def test_latency_avg_factory(self):
        obj = SLObjective.latency_avg(target_ms=1000.0)
        assert obj.kind == ObjectiveKind.LATENCY_AVG
        assert obj.target == 1000.0

    def test_error_rate_factory(self):
        obj = SLObjective.error_rate(target_rate=0.01, slo_percent=99.5)
        assert obj.kind == ObjectiveKind.ERROR_RATE
        assert obj.target == 0.01
        assert obj.slo_percent == 99.5

    def test_token_budget_factory(self):
        obj = SLObjective.token_budget(target_per_session=5000, slo_percent=95.0)
        assert obj.kind == ObjectiveKind.TOKEN_BUDGET
        assert obj.target == 5000.0

    def test_tool_success_rate_factory(self):
        obj = SLObjective.tool_success_rate(target_rate=0.95)
        assert obj.kind == ObjectiveKind.TOOL_SUCCESS_RATE
        assert obj.target == 0.95

    def test_throughput_factory(self):
        obj = SLObjective.throughput(min_events=5, slo_percent=90.0)
        assert obj.kind == ObjectiveKind.THROUGHPUT
        assert obj.target == 5.0
        assert obj.slo_percent == 90.0

    def test_auto_generated_name(self):
        obj = SLObjective(kind=ObjectiveKind.ERROR_RATE, target=0.05)
        assert "error_rate" in obj.name
        assert "0.05" in obj.name

    def test_custom_name(self):
        obj = SLObjective(kind=ObjectiveKind.LATENCY_P95, target=1000, name="Custom")
        assert obj.name == "Custom"

    def test_slo_percent_validation_zero(self):
        with pytest.raises(ValueError, match="slo_percent"):
            SLObjective(kind=ObjectiveKind.ERROR_RATE, target=0.01, slo_percent=0)

    def test_slo_percent_validation_over_100(self):
        with pytest.raises(ValueError, match="slo_percent"):
            SLObjective(kind=ObjectiveKind.ERROR_RATE, target=0.01, slo_percent=101)

    def test_slo_percent_boundary_100(self):
        obj = SLObjective(kind=ObjectiveKind.ERROR_RATE, target=0.01, slo_percent=100.0)
        assert obj.slo_percent == 100.0

    def test_default_slo_percent(self):
        obj = SLObjective(kind=ObjectiveKind.LATENCY_P95, target=3000)
        assert obj.slo_percent == 99.0


# ---------------------------------------------------------------------------
# SLAPolicy tests
# ---------------------------------------------------------------------------

class TestSLAPolicy:
    def test_basic_policy(self):
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01)],
        )
        assert policy.name == "test"
        assert len(policy.objectives) == 1

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            SLAPolicy(name="")

    def test_whitespace_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            SLAPolicy(name="   ")

    def test_empty_objectives(self):
        policy = SLAPolicy(name="minimal")
        assert len(policy.objectives) == 0

    def test_description(self):
        policy = SLAPolicy(name="x", description="A test policy")
        assert policy.description == "A test policy"


# ---------------------------------------------------------------------------
# Preset policies
# ---------------------------------------------------------------------------

class TestPresetPolicies:
    def test_production_policy(self):
        p = production_policy()
        assert p.name == "production"
        assert len(p.objectives) == 4
        kinds = {o.kind for o in p.objectives}
        assert ObjectiveKind.LATENCY_P95 in kinds
        assert ObjectiveKind.ERROR_RATE in kinds
        assert ObjectiveKind.TOKEN_BUDGET in kinds
        assert ObjectiveKind.TOOL_SUCCESS_RATE in kinds

    def test_development_policy(self):
        p = development_policy()
        assert p.name == "development"
        assert len(p.objectives) == 3
        # More relaxed targets
        latency = next(o for o in p.objectives if o.kind == ObjectiveKind.LATENCY_P95)
        assert latency.target == 10000.0
        assert latency.slo_percent == 90.0


# ---------------------------------------------------------------------------
# SLAEvaluator tests
# ---------------------------------------------------------------------------

class TestSLAEvaluator:
    def setup_method(self):
        self.evaluator = SLAEvaluator()

    # -- validation ---------------------------------------------------------

    def test_empty_sessions_raises(self):
        policy = SLAPolicy(name="test", objectives=[SLObjective.error_rate(0.01)])
        with pytest.raises(ValueError, match="No sessions"):
            self.evaluator.evaluate([], policy)

    def test_empty_objectives_raises(self):
        sessions = [_fast_session("s1")]
        policy = SLAPolicy(name="test")
        with pytest.raises(ValueError, match="no objectives"):
            self.evaluator.evaluate(sessions, policy)

    # -- all compliant scenario ---------------------------------------------

    def test_all_compliant(self):
        sessions = [_fast_session(f"s{i}") for i in range(100)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.overall_status == ComplianceStatus.COMPLIANT
        assert report.violated_objectives == 0
        assert report.compliant_objectives == 1
        assert report.total_sessions == 100
        assert report.results[0].compliance_percent == 100.0

    # -- all violated scenario ----------------------------------------------

    def test_all_violated(self):
        sessions = [_slow_session(f"s{i}", latency=5000.0) for i in range(10)]
        policy = SLAPolicy(
            name="strict",
            objectives=[SLObjective.latency_p95(target_ms=100.0, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.overall_status == ComplianceStatus.VIOLATED
        assert report.violated_objectives == 1
        assert report.results[0].violation_count == 10
        assert report.results[0].compliance_percent == 0.0

    # -- error rate SLO -----------------------------------------------------

    def test_error_rate_compliant(self):
        sessions = [_fast_session(f"s{i}") for i in range(100)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_error_rate_violated(self):
        # All sessions have 50% error rate → all violate the 1% target
        sessions = [_error_session(f"s{i}", error_rate=0.5) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED
        assert report.results[0].violation_count == 10

    def test_error_rate_edge_equal_target(self):
        # Session with exactly 1% error rate (1/100 events)
        events = [_event(event_type="error")] + [_event() for _ in range(99)]
        sessions = [_session("s1", events)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        # 1% == target, so not violated (> target is violated)
        assert report.results[0].violation_count == 0

    # -- token budget SLO ---------------------------------------------------

    def test_token_budget_compliant(self):
        sessions = [_fast_session(f"s{i}", n_events=5) for i in range(20)]
        # Each event has 100 tokens → 500/session, target 10000
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.token_budget(target_per_session=10000, slo_percent=95.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_token_budget_violated(self):
        # 100 tokens/event × 200 events = 20000 tokens/session → exceeds 5000
        sessions = [_session(f"s{i}", [_event() for _ in range(200)]) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.token_budget(target_per_session=5000, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED

    # -- tool success rate SLO ----------------------------------------------

    def test_tool_success_rate_compliant(self):
        sessions = [
            _session(f"s{i}", [_tool_event() for _ in range(10)])
            for i in range(20)
        ]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.tool_success_rate(target_rate=0.95, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_tool_success_rate_violated(self):
        # 50% tool failure rate
        sessions = [
            _session(f"s{i}", [
                _tool_event(error=True),
                _tool_event(error=False),
            ])
            for i in range(10)
        ]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.tool_success_rate(target_rate=0.95, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED

    def test_no_tool_calls_counts_as_success(self):
        sessions = [_fast_session(f"s{i}") for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.tool_success_rate(target_rate=0.95)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].compliance_percent == 100.0

    # -- throughput SLO -----------------------------------------------------

    def test_throughput_compliant(self):
        sessions = [_fast_session(f"s{i}", n_events=10) for i in range(20)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.throughput(min_events=5, slo_percent=90.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_throughput_violated(self):
        sessions = [_fast_session(f"s{i}", n_events=2) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.throughput(min_events=5, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED

    # -- latency SLO --------------------------------------------------------

    def test_latency_p95_compliant(self):
        sessions = [_fast_session(f"s{i}") for i in range(50)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_latency_avg_violated(self):
        sessions = [_slow_session(f"s{i}", latency=5000.0) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_avg(target_ms=1000.0, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED

    def test_latency_no_duration_data(self):
        sessions = [_session(f"s{i}", [_event(duration_ms=None)]) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        # No duration data → 0ms → under target
        assert report.results[0].compliance_percent == 100.0

    # -- error budget -------------------------------------------------------

    def test_error_budget_calculation(self):
        # 100 sessions, 99% SLO → 1 allowed violation
        sessions = [_fast_session(f"s{i}") for i in range(100)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        result = report.results[0]
        assert result.error_budget_total == pytest.approx(1.0)
        assert result.error_budget_remaining == pytest.approx(1.0)
        assert result.error_budget_percent == pytest.approx(100.0)

    def test_error_budget_partially_consumed(self):
        # 100 sessions, 95% SLO → 5 allowed. 2 slow sessions → 3 remaining
        sessions = [_fast_session(f"s{i}") for i in range(98)]
        sessions += [_slow_session(f"slow{i}", latency=5000.0) for i in range(2)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0, slo_percent=95.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        result = report.results[0]
        assert result.error_budget_total == pytest.approx(5.0)
        assert result.error_budget_remaining == pytest.approx(3.0)
        assert result.error_budget_percent == pytest.approx(60.0)

    def test_error_budget_exhausted(self):
        # All 10 sessions violate, 99% SLO → budget = 0.1, remaining = 0
        sessions = [_slow_session(f"s{i}", latency=5000.0) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=100.0, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        result = report.results[0]
        assert result.error_budget_remaining == 0.0
        assert result.error_budget_percent == 0.0

    # -- at-risk status -----------------------------------------------------

    def test_at_risk_status(self):
        # 100 sessions, 95% SLO (5 allowed violations).
        # 4 violations → 96% compliance (above 95% but within 5% margin)
        sessions = [_fast_session(f"s{i}") for i in range(96)]
        sessions += [_slow_session(f"slow{i}", latency=5000.0) for i in range(4)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0, slo_percent=95.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.AT_RISK

    def test_at_risk_overall(self):
        sessions = [_fast_session(f"s{i}") for i in range(96)]
        sessions += [_slow_session(f"slow{i}", latency=5000.0) for i in range(4)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0, slo_percent=95.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.overall_status == ComplianceStatus.AT_RISK

    # -- multiple objectives ------------------------------------------------

    def test_multiple_objectives_all_pass(self):
        sessions = [_fast_session(f"s{i}") for i in range(50)]
        report = self.evaluator.evaluate(sessions, production_policy())
        assert report.compliant_objectives == len(production_policy().objectives)
        assert report.violated_objectives == 0

    def test_multiple_objectives_one_fails(self):
        # Fast sessions + high error rate
        sessions = [_error_session(f"s{i}", error_rate=0.3) for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[
                SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0),
                SLObjective.error_rate(target_rate=0.01, slo_percent=99.0),
            ],
        )
        report = self.evaluator.evaluate(sessions, policy)
        # Latency should pass, error rate should fail
        assert report.overall_status == ComplianceStatus.VIOLATED
        assert report.violated_objectives >= 1
        assert report.total_sessions == 10

    # -- single session edge case -------------------------------------------

    def test_single_session_compliant(self):
        sessions = [_fast_session("s1")]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.total_sessions == 1
        assert report.results[0].compliance_percent == 100.0

    def test_single_session_violated(self):
        sessions = [_error_session("s1", error_rate=0.5)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=99.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert report.results[0].violation_count == 1
        assert report.results[0].compliance_percent == 0.0

    # -- empty events session -----------------------------------------------

    def test_session_with_no_events(self):
        sessions = [_session("s1", [])]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        # 0 events → 0% error rate → compliant
        assert report.results[0].compliance_percent == 100.0

    # -- violation list tracks session IDs ----------------------------------

    def test_violations_contain_session_ids(self):
        sessions = [_fast_session("fast1"), _slow_session("slow1", latency=5000.0)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=100.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert "slow1" in report.results[0].violations

    # -- measured values populated ------------------------------------------

    def test_measured_values_populated(self):
        sessions = [_fast_session("s1"), _slow_session("s2", latency=2000.0)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_avg(target_ms=1000.0)],
        )
        report = self.evaluator.evaluate(sessions, policy)
        assert len(report.results[0].measured_values) == 2


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------

class TestSLAReport:
    def test_to_dict(self):
        sessions = [_fast_session(f"s{i}") for i in range(10)]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.error_rate(target_rate=0.01)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        d = report.to_dict()
        assert d["policy_name"] == "test"
        assert d["total_sessions"] == 10
        assert d["overall_status"] == "compliant"
        assert len(d["objectives"]) == 1
        obj = d["objectives"][0]
        assert "compliance_percent" in obj
        assert "error_budget_total" in obj

    def test_render(self):
        sessions = [_fast_session(f"s{i}") for i in range(10)]
        policy = SLAPolicy(
            name="demo",
            objectives=[
                SLObjective.error_rate(target_rate=0.01),
                SLObjective.latency_p95(target_ms=3000.0),
            ],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        text = report.render()
        assert "SLA Report: demo" in text
        assert "Sessions: 10" in text
        assert "Error rate" in text
        assert "P95" in text

    def test_render_violated(self):
        sessions = [_error_session(f"s{i}", error_rate=0.5) for i in range(10)]
        policy = SLAPolicy(
            name="strict",
            objectives=[SLObjective.error_rate(target_rate=0.01)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        text = report.render()
        assert "VIOLATED" in text


# ---------------------------------------------------------------------------
# ObjectiveResult properties
# ---------------------------------------------------------------------------

class TestObjectiveResult:
    def test_violation_count_property(self):
        result = ObjectiveResult(
            objective=SLObjective.error_rate(0.01),
            compliant_sessions=8,
            total_sessions=10,
            compliance_percent=80.0,
            status=ComplianceStatus.VIOLATED,
            violations=["s1", "s2"],
            error_budget_total=1.0,
            error_budget_remaining=0.0,
            error_budget_percent=0.0,
            measured_values=[],
        )
        assert result.violation_count == 2


# ---------------------------------------------------------------------------
# ComplianceStatus enum
# ---------------------------------------------------------------------------

class TestComplianceStatus:
    def test_values(self):
        assert ComplianceStatus.COMPLIANT.value == "compliant"
        assert ComplianceStatus.AT_RISK.value == "at_risk"
        assert ComplianceStatus.VIOLATED.value == "violated"


# ---------------------------------------------------------------------------
# ObjectiveKind enum
# ---------------------------------------------------------------------------

class TestObjectiveKind:
    def test_all_kinds(self):
        assert len(ObjectiveKind) == 6
        assert ObjectiveKind.LATENCY_P95.value == "latency_p95"
        assert ObjectiveKind.LATENCY_AVG.value == "latency_avg"
        assert ObjectiveKind.ERROR_RATE.value == "error_rate"
        assert ObjectiveKind.TOKEN_BUDGET.value == "token_budget"
        assert ObjectiveKind.TOOL_SUCCESS_RATE.value == "tool_success_rate"
        assert ObjectiveKind.THROUGHPUT.value == "throughput"


# ---------------------------------------------------------------------------
# Session normalization (model objects)
# ---------------------------------------------------------------------------

class _FakeToolCall:
    def __init__(self, name, output):
        self.tool_name = name
        self.tool_output = output


class _FakeEvent:
    def __init__(self, event_type="llm_call", duration_ms=100.0,
                 tokens_in=50, tokens_out=50, tool_call=None):
        self.event_type = event_type
        self.duration_ms = duration_ms
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.tool_call = tool_call


class _FakeSession:
    def __init__(self, session_id, events):
        self.session_id = session_id
        self.events = events


class TestSessionNormalization:
    def test_model_objects_normalized(self):
        sessions = [
            _FakeSession("s1", [
                _FakeEvent(event_type="llm_call", duration_ms=50.0),
                _FakeEvent(event_type="tool_call",
                           tool_call=_FakeToolCall("search", {"result": "ok"})),
            ])
        ]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=3000.0)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        assert report.total_sessions == 1
        assert report.results[0].compliance_percent == 100.0

    def test_model_objects_with_tool_errors(self):
        sessions = [
            _FakeSession("s1", [
                _FakeEvent(event_type="tool_call",
                           tool_call=_FakeToolCall("search", {"error": "timeout"})),
                _FakeEvent(event_type="tool_call",
                           tool_call=_FakeToolCall("search", {"result": "ok"})),
            ])
        ]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.tool_success_rate(target_rate=0.95)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        # 50% tool success < 95% target → violated
        assert report.results[0].violation_count == 1


# ---------------------------------------------------------------------------
# P95 calculation edge cases
# ---------------------------------------------------------------------------

class TestP95Calculation:
    def test_single_event(self):
        sessions = [_session("s1", [_event(duration_ms=500.0)])]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=1000.0)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        assert report.results[0].measured_values[0] == pytest.approx(500.0)

    def test_two_events(self):
        sessions = [_session("s1", [
            _event(duration_ms=100.0),
            _event(duration_ms=1000.0),
        ])]
        policy = SLAPolicy(
            name="test",
            objectives=[SLObjective.latency_p95(target_ms=2000.0)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        # P95 of [100, 1000] with linear interpolation
        p95 = report.results[0].measured_values[0]
        assert p95 >= 100.0
        assert p95 <= 1000.0


# ---------------------------------------------------------------------------
# 100% SLO (zero tolerance)
# ---------------------------------------------------------------------------

class TestZeroTolerance:
    def test_100_percent_slo_all_pass(self):
        sessions = [_fast_session(f"s{i}") for i in range(10)]
        policy = SLAPolicy(
            name="strict",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=100.0)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.COMPLIANT

    def test_100_percent_slo_one_violation(self):
        sessions = [_fast_session(f"s{i}") for i in range(9)]
        sessions.append(_error_session("bad", error_rate=0.5))
        policy = SLAPolicy(
            name="strict",
            objectives=[SLObjective.error_rate(target_rate=0.01, slo_percent=100.0)],
        )
        report = SLAEvaluator().evaluate(sessions, policy)
        assert report.results[0].status == ComplianceStatus.VIOLATED
