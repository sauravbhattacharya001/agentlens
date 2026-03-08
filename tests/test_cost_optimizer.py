"""Tests for CostOptimizer — model selection recommendations (40 tests)."""

import pytest
from agentlens.models import AgentEvent, ToolCall, DecisionTrace
from agentlens.cost_optimizer import (
    CostOptimizer, ComplexityAnalyzer, ComplexityLevel, ModelTier, ModelInfo,
    Confidence, OptimizationReport, Recommendation, MODEL_REGISTRY,
    _event_cost, _hypothetical_cost,
)


def _ev(model="gpt-4o", ti=500, to=100, et="llm_call", **kw):
    return AgentEvent(model=model, tokens_in=ti, tokens_out=to, event_type=et, **kw)


class TestModelRegistry:
    def test_all_have_pricing(self):
        for i in MODEL_REGISTRY.values():
            assert i.input_cost_per_1m > 0 and i.output_cost_per_1m > 0

    def test_economy_cheaper(self):
        eco = min(m.avg_cost_per_1m for m in MODEL_REGISTRY.values() if m.tier == ModelTier.ECONOMY)
        std = min(m.avg_cost_per_1m for m in MODEL_REGISTRY.values() if m.tier == ModelTier.STANDARD)
        assert eco < std

    def test_avg_cost(self):
        assert ModelInfo("t", ModelTier.ECONOMY, 1.0, 3.0).avg_cost_per_1m == 2.0


class TestComplexityAnalyzer:
    def setup_method(self):
        self.a = ComplexityAnalyzer()

    def test_trivial(self):
        r = self.a.assess(_ev(ti=10, to=5, et="formatting"))
        assert r.level in (ComplexityLevel.TRIVIAL, ComplexityLevel.LOW)

    def test_classification_low(self):
        assert self.a.assess(_ev(ti=200, to=10, et="classification")).level in (ComplexityLevel.TRIVIAL, ComplexityLevel.LOW)

    def test_decision_high(self):
        assert self.a.assess(_ev(ti=2000, to=1500, et="decision",
                                  decision_trace=DecisionTrace(reasoning="c"))).score >= 0.40

    def test_tool_increases(self):
        b = self.a.assess(_ev(ti=500, to=100))
        t = self.a.assess(_ev(ti=500, to=100, tool_call=ToolCall(tool_name="s", tool_input={})))
        assert t.score > b.score

    def test_volume_increases(self):
        assert self.a.assess(_ev(ti=8000, to=2000)).score > self.a.assess(_ev(ti=100, to=50)).score

    def test_output_ratio(self):
        assert self.a.assess(_ev(ti=100, to=2000)).factors["output_ratio"] > \
               self.a.assess(_ev(ti=1000, to=10)).factors["output_ratio"]

    def test_bounded(self):
        r = self.a.assess(_ev(ti=50000, to=50000, et="planning",
                               decision_trace=DecisionTrace(reasoning="x"),
                               tool_call=ToolCall(tool_name="t")))
        assert 0.0 <= r.score <= 1.0

    def test_zero_tokens(self):
        assert self.a.assess(_ev(ti=0, to=0)).score >= 0.0

    def test_reasoning_str(self):
        assert len(self.a.assess(_ev()).reasoning) > 0

    def test_unknown_type_default(self):
        assert self.a.assess(_ev(et="custom_weird")).factors["event_type"] == 0.3


class TestCostCalc:
    def test_known(self):
        assert _event_cost(_ev(model="gpt-4o", ti=1_000_000, to=1_000_000)) == pytest.approx(12.50, rel=0.01)

    def test_unknown(self):
        assert _event_cost(_ev(model="unknown")) == 0.0

    def test_no_model(self):
        assert _event_cost(AgentEvent(tokens_in=100, tokens_out=50)) == 0.0

    def test_hypothetical(self):
        assert _hypothetical_cost(_ev(ti=1_000_000, to=1_000_000), MODEL_REGISTRY["gpt-4o-mini"]) == pytest.approx(0.75, rel=0.01)

    def test_zero(self):
        assert _event_cost(_ev(ti=0, to=0)) == 0.0


class TestOptimizer:
    def setup_method(self):
        self.o = CostOptimizer()

    def test_empty(self):
        r = self.o.analyze([])
        assert r.total_events == 0 and not r.has_savings

    def test_already_optimal(self):
        assert self.o.analyze([_ev(model="gpt-4o-mini", ti=100, to=20, et="classification")]).optimizable_events == 0

    def test_downgrade_simple(self):
        r = self.o.analyze([_ev(model="gpt-4", ti=200, to=10, et="classification")])
        assert r.optimizable_events >= 1 and r.has_savings

    def test_no_downgrade_complex(self):
        assert self.o.analyze([_ev(model="gpt-4-turbo", ti=5000, to=3000, et="planning",
                                    decision_trace=DecisionTrace(reasoning="d"),
                                    tool_call=ToolCall(tool_name="s"))]).optimizable_events == 0

    def test_model_usage(self):
        r = self.o.analyze([_ev(), _ev(), _ev(model="claude-3-sonnet")])
        assert r.model_usage["gpt-4o"] == 2

    def test_tier_dist(self):
        r = self.o.analyze([_ev(model="gpt-4o-mini"), _ev(), _ev(model="gpt-4")])
        assert "economy" in r.tier_distribution

    def test_savings_balance(self):
        r = self.o.analyze([_ev(model="gpt-4", ti=500, to=50, et="classification"),
                            _ev(model="gpt-4o", ti=200, to=20, et="formatting")])
        assert abs(r.optimized_cost_usd - (r.current_cost_usd - r.total_savings_usd)) < 0.0001

    def test_unknown_skipped(self):
        assert self.o.analyze([_ev(model="unknown")]).optimizable_events == 0

    def test_no_model_skipped(self):
        assert self.o.analyze([AgentEvent(tokens_in=100, tokens_out=50)]).optimizable_events == 0

    def test_aggressive(self):
        assert isinstance(CostOptimizer(aggressive=True).analyze([_ev(model="gpt-4", ti=2000, to=800)]).optimizable_events, int)

    def test_min_savings(self):
        for r in CostOptimizer(min_savings_pct=90.0).analyze([_ev(model="gpt-4o", ti=100, to=10, et="classification")]).recommendations:
            assert r.savings_pct >= 90.0

    def test_custom_model(self):
        assert "my" in CostOptimizer(custom_models={"my": ModelInfo("my", ModelTier.ECONOMY, 0.05, 0.10, 32000)}).models

    def test_register(self):
        self.o.register_model("n", ModelInfo("n", ModelTier.STANDARD, 1.0, 2.0))
        assert "n" in self.o.models

    def test_summary(self):
        assert len(self.o.analyze([_ev(model="gpt-4", ti=500, to=50, et="classification")]).summary) > 0

    def test_summary_no_savings(self):
        r = self.o.analyze([_ev(model="gpt-4o-mini", ti=100, to=10)])
        assert "well-optimized" in r.summary or r.has_savings


class TestRecommendation:
    def test_is_downgrade(self):
        assert Recommendation(current_tier=ModelTier.PREMIUM, recommended_tier=ModelTier.ECONOMY).is_downgrade

    def test_not_downgrade(self):
        assert not Recommendation(current_tier=ModelTier.ECONOMY, recommended_tier=ModelTier.ECONOMY).is_downgrade

    def test_unique_ids(self):
        r = CostOptimizer().analyze([_ev(model="gpt-4", ti=100, to=10, et="formatting"),
                                      _ev(model="gpt-4", ti=200, to=20, et="classification")])
        ids = [x.rec_id for x in r.recommendations]
        assert len(ids) == len(set(ids))


class TestMigration:
    def test_empty(self):
        assert len(CostOptimizer().analyze([_ev(model="gpt-4o-mini", ti=100, to=10)]).migration_plan) == 0

    def test_phases_sequential(self):
        r = CostOptimizer(aggressive=True).analyze([_ev(model="gpt-4", ti=50, to=5, et="formatting"),
                                                     _ev(model="gpt-4-turbo", ti=2000, to=500)])
        assert [s.phase for s in r.migration_plan] == sorted(s.phase for s in r.migration_plan)


class TestQuickEstimate:
    def test_structure(self):
        r = CostOptimizer().quick_estimate([_ev()])
        for k in ("current_cost", "potential_savings", "savings_pct", "overprovisioned_count", "total_events"):
            assert k in r

    def test_empty(self):
        assert CostOptimizer().quick_estimate([])["current_cost"] == 0.0


class TestSuggestModel:
    def test_simple(self):
        s = CostOptimizer().suggest_model(_ev(model="gpt-4", ti=100, to=10, et="formatting"))
        if s:
            assert s != "gpt-4"

    def test_optimal_none(self):
        assert CostOptimizer().suggest_model(_ev(model="gpt-4o-mini", ti=100, to=10, et="classification")) is None

    def test_unknown_none(self):
        assert CostOptimizer().suggest_model(_ev(model="unknown")) is None


class TestSessionFilter:
    def test_filter(self):
        evs = [_ev(), _ev()]
        evs[0].session_id = "a"
        evs[1].session_id = "b"
        assert CostOptimizer().analyze_session_events(evs, session_id="a").total_events == 1

    def test_no_filter(self):
        assert CostOptimizer().analyze_session_events([_ev(), _ev()]).total_events == 2


class TestReportProps:
    def test_no_savings(self):
        assert not OptimizationReport().has_savings

    def test_has_savings(self):
        assert OptimizationReport(total_savings_usd=0.01).has_savings

    def test_timestamp_and_id(self):
        r = CostOptimizer().analyze([])
        assert r.timestamp and r.report_id
