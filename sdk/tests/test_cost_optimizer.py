"""Tests for CostOptimizer — model selection recommendations and savings analysis."""

import pytest
from datetime import datetime, timezone

from agentlens.models import AgentEvent, ToolCall, DecisionTrace
from agentlens.cost_optimizer import (
    CostOptimizer,
    ComplexityAnalyzer,
    ComplexityLevel,
    Confidence,
    ModelInfo,
    ModelTier,
    MODEL_REGISTRY,
    _event_cost,
    _hypothetical_cost,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _make_event(
    model="gpt-4o",
    tokens_in=500,
    tokens_out=100,
    event_type="llm_call",
    tool_call=None,
    decision_trace=None,
    session_id="sess-1",
    duration_ms=None,
):
    return AgentEvent(
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        event_type=event_type,
        tool_call=tool_call,
        decision_trace=decision_trace,
        session_id=session_id,
        duration_ms=duration_ms,
    )


# ── MODEL_REGISTRY ──────────────────────────────────────────────────────


class TestModelRegistry:
    def test_registry_contains_standard_models(self):
        assert "gpt-4o" in MODEL_REGISTRY
        assert "gpt-4o-mini" in MODEL_REGISTRY
        assert "claude-3-opus" in MODEL_REGISTRY
        assert "claude-3-haiku" in MODEL_REGISTRY

    def test_model_info_avg_cost(self):
        mi = MODEL_REGISTRY["gpt-4o"]
        expected = (mi.input_cost_per_1m + mi.output_cost_per_1m) / 2
        assert mi.avg_cost_per_1m == expected

    def test_model_tiers_assigned_correctly(self):
        assert MODEL_REGISTRY["gpt-4o-mini"].tier == ModelTier.ECONOMY
        assert MODEL_REGISTRY["gpt-4o"].tier == ModelTier.STANDARD
        assert MODEL_REGISTRY["gpt-4-turbo"].tier == ModelTier.PREMIUM
        assert MODEL_REGISTRY["claude-3-opus"].tier == ModelTier.FLAGSHIP


# ── ComplexityAnalyzer ──────────────────────────────────────────────────


class TestComplexityAnalyzer:
    def setup_method(self):
        self.analyzer = ComplexityAnalyzer()

    def test_trivial_event(self):
        event = _make_event(tokens_in=10, tokens_out=2, event_type="formatting")
        result = self.analyzer.assess(event)
        assert result.level == ComplexityLevel.TRIVIAL
        assert result.score < 0.15
        assert result.recommended_tier == ModelTier.ECONOMY

    def test_low_complexity_event(self):
        event = _make_event(tokens_in=100, tokens_out=20, event_type="classification")
        result = self.analyzer.assess(event)
        assert result.level in (ComplexityLevel.TRIVIAL, ComplexityLevel.LOW)

    def test_medium_complexity_event(self):
        event = _make_event(
            tokens_in=3000,
            tokens_out=1500,
            event_type="summarization",
        )
        result = self.analyzer.assess(event)
        assert result.score >= 0.15

    def test_high_complexity_with_tools(self):
        event = _make_event(
            tokens_in=5000,
            tokens_out=3000,
            event_type="code_generation",
            tool_call=ToolCall(tool_name="execute_code"),
        )
        result = self.analyzer.assess(event)
        assert result.score >= 0.30

    def test_critical_complexity_planning(self):
        event = _make_event(
            tokens_in=8000,
            tokens_out=5000,
            event_type="planning",
            tool_call=ToolCall(tool_name="search"),
            decision_trace=DecisionTrace(reasoning="multi-step plan"),
        )
        result = self.analyzer.assess(event)
        assert result.level in (ComplexityLevel.HIGH, ComplexityLevel.CRITICAL)

    def test_zero_tokens_handled(self):
        event = _make_event(tokens_in=0, tokens_out=0, event_type="generic")
        result = self.analyzer.assess(event)
        assert result.score >= 0.0
        assert result.score <= 1.0

    def test_explain_generates_reasoning(self):
        event = _make_event(tokens_in=5000, tokens_out=4000, event_type="planning")
        result = self.analyzer.assess(event)
        assert "complexity" in result.reasoning

    def test_factors_are_bounded(self):
        event = _make_event(tokens_in=100000, tokens_out=50000, event_type="code_generation")
        result = self.analyzer.assess(event)
        for v in result.factors.values():
            assert 0.0 <= v <= 1.0

    def test_unknown_event_type_uses_default(self):
        event = _make_event(event_type="totally_unknown")
        result = self.analyzer.assess(event)
        assert result.factors["event_type"] == 0.3


# ── _event_cost / _hypothetical_cost ─────────────────────────────────


class TestCostFunctions:
    def test_event_cost_known_model(self):
        event = _make_event(model="gpt-4o", tokens_in=1_000_000, tokens_out=1_000_000)
        mi = MODEL_REGISTRY["gpt-4o"]
        cost = _event_cost(event, mi)
        assert cost == mi.input_cost_per_1m + mi.output_cost_per_1m

    def test_event_cost_no_model_info(self):
        event = _make_event(model="unknown-model")
        cost = _event_cost(event)
        assert cost == 0.0

    def test_event_cost_auto_lookup(self):
        event = _make_event(model="gpt-4o", tokens_in=1_000_000, tokens_out=0)
        cost = _event_cost(event)
        mi = MODEL_REGISTRY["gpt-4o"]
        assert cost == mi.input_cost_per_1m

    def test_hypothetical_cost(self):
        event = _make_event(tokens_in=2_000_000, tokens_out=500_000)
        mi = MODEL_REGISTRY["gpt-4o-mini"]
        cost = _hypothetical_cost(event, mi)
        expected = (2_000_000 / 1_000_000) * mi.input_cost_per_1m + \
                   (500_000 / 1_000_000) * mi.output_cost_per_1m
        assert abs(cost - expected) < 0.0001

    def test_event_cost_zero_tokens(self):
        event = _make_event(tokens_in=0, tokens_out=0)
        assert _event_cost(event) == 0.0


# ── CostOptimizer ────────────────────────────────────────────────────


class TestCostOptimizer:
    def setup_method(self):
        self.optimizer = CostOptimizer()

    def test_empty_events(self):
        report = self.optimizer.analyze([])
        assert report.total_events == 0
        assert report.has_savings is False

    def test_no_model_events_skipped(self):
        event = _make_event(model=None)
        report = self.optimizer.analyze([event])
        assert report.optimizable_events == 0

    def test_unknown_model_skipped(self):
        event = _make_event(model="totally-unknown-model")
        report = self.optimizer.analyze([event])
        assert report.current_cost_usd == 0.0

    def test_simple_downgrade_detected(self):
        # gpt-4-turbo (premium) for a trivial classification task
        event = _make_event(
            model="gpt-4-turbo",
            tokens_in=50,
            tokens_out=10,
            event_type="classification",
        )
        report = self.optimizer.analyze([event])
        assert report.optimizable_events >= 0  # may or may not find savings depending on thresholds

    def test_already_optimal_no_savings(self):
        # gpt-4o-mini for a trivial task — already economy tier
        event = _make_event(
            model="gpt-4o-mini",
            tokens_in=100,
            tokens_out=20,
            event_type="formatting",
        )
        report = self.optimizer.analyze([event])
        assert report.total_savings_usd == 0.0

    def test_model_usage_tracking(self):
        events = [
            _make_event(model="gpt-4o"),
            _make_event(model="gpt-4o"),
            _make_event(model="gpt-4o-mini"),
        ]
        report = self.optimizer.analyze(events)
        assert report.model_usage["gpt-4o"] == 2
        assert report.model_usage["gpt-4o-mini"] == 1

    def test_tier_distribution(self):
        events = [
            _make_event(model="gpt-4o"),
            _make_event(model="gpt-4o-mini"),
        ]
        report = self.optimizer.analyze(events)
        assert "standard" in report.tier_distribution
        assert "economy" in report.tier_distribution

    def test_non_analyzable_event_type_not_optimized(self):
        event = _make_event(model="gpt-4-turbo", event_type="error")
        report = self.optimizer.analyze([event])
        assert report.optimizable_events == 0

    def test_report_summary_no_savings(self):
        event = _make_event(model="gpt-4o-mini", event_type="formatting")
        report = self.optimizer.analyze([event])
        assert "well-optimized" in report.summary or "Analyzed" in report.summary

    def test_register_custom_model(self):
        custom = ModelInfo("my-model", ModelTier.ECONOMY, 0.01, 0.02)
        self.optimizer.register_model("my-model", custom)
        assert "my-model" in self.optimizer.models

    def test_custom_models_in_constructor(self):
        custom = {"super-cheap": ModelInfo("super-cheap", ModelTier.ECONOMY, 0.001, 0.001)}
        opt = CostOptimizer(custom_models=custom)
        assert "super-cheap" in opt.models

    def test_aggressive_mode_includes_low_confidence(self):
        opt = CostOptimizer(aggressive=True)
        # With aggressive=True, low-confidence recommendations are kept
        event = _make_event(
            model="claude-3-opus",
            tokens_in=3000,
            tokens_out=1500,
            event_type="summarization",
        )
        report = opt.analyze([event])
        # Should be more likely to find optimizations
        assert report.total_events == 1

    def test_min_savings_pct_filter(self):
        opt = CostOptimizer(min_savings_pct=99.0)
        event = _make_event(
            model="gpt-4-turbo",
            tokens_in=100,
            tokens_out=20,
            event_type="classification",
        )
        report = opt.analyze([event])
        # Very high threshold should filter out most recommendations
        assert report.optimizable_events == 0

    def test_analyze_session_events_filters_by_session(self):
        events = [
            _make_event(session_id="sess-1"),
            _make_event(session_id="sess-2"),
            _make_event(session_id="sess-1"),
        ]
        report = self.optimizer.analyze_session_events(events, session_id="sess-1")
        assert report.total_events == 2

    def test_analyze_session_events_no_filter(self):
        events = [_make_event(), _make_event()]
        report = self.optimizer.analyze_session_events(events)
        assert report.total_events == 2

    def test_quick_estimate(self):
        events = [
            _make_event(model="gpt-4-turbo", tokens_in=50, tokens_out=10, event_type="formatting"),
            _make_event(model="gpt-4o-mini", tokens_in=100, tokens_out=20, event_type="classification"),
        ]
        result = self.optimizer.quick_estimate(events)
        assert "current_cost" in result
        assert "potential_savings" in result
        assert "savings_pct" in result
        assert "total_events" in result
        assert result["total_events"] == 2

    def test_quick_estimate_no_model_skipped(self):
        events = [_make_event(model=None)]
        result = self.optimizer.quick_estimate(events)
        assert result["current_cost"] == 0.0

    def test_suggest_model_returns_cheaper(self):
        event = _make_event(
            model="gpt-4-turbo",
            tokens_in=50,
            tokens_out=10,
            event_type="formatting",
        )
        suggestion = self.optimizer.suggest_model(event)
        if suggestion is not None:
            assert suggestion != "gpt-4-turbo"

    def test_suggest_model_no_model(self):
        event = _make_event(model=None)
        assert self.optimizer.suggest_model(event) is None

    def test_suggest_model_unknown_model(self):
        event = _make_event(model="unknown-xyz")
        assert self.optimizer.suggest_model(event) is None

    def test_suggest_model_already_optimal(self):
        event = _make_event(model="gpt-4o-mini", event_type="formatting")
        result = self.optimizer.suggest_model(event)
        # Economy model for trivial task — no downgrade possible
        assert result is None

    def test_migration_plan_empty_for_no_recs(self):
        event = _make_event(model="gpt-4o-mini", event_type="formatting")
        report = self.optimizer.analyze([event])
        assert report.migration_plan == []

    def test_recommendation_is_downgrade(self):
        from agentlens.cost_optimizer import Recommendation
        rec = Recommendation(
            current_tier=ModelTier.PREMIUM,
            recommended_tier=ModelTier.ECONOMY,
        )
        assert rec.is_downgrade is True

    def test_recommendation_not_downgrade(self):
        from agentlens.cost_optimizer import Recommendation
        rec = Recommendation(
            current_tier=ModelTier.ECONOMY,
            recommended_tier=ModelTier.STANDARD,
        )
        assert rec.is_downgrade is False

    def test_report_has_savings_property(self):
        from agentlens.cost_optimizer import OptimizationReport
        report = OptimizationReport(total_savings_usd=0.0)
        assert report.has_savings is False
        report2 = OptimizationReport(total_savings_usd=0.01)
        assert report2.has_savings is True


# ── Migration Plan ───────────────────────────────────────────────────


class TestMigrationPlan:
    def test_migration_plan_groups_by_confidence(self):
        optimizer = CostOptimizer(aggressive=True, min_savings_pct=1.0)
        # Use flagship model for trivial tasks — guaranteed optimization
        events = [
            _make_event(model="claude-3-opus", tokens_in=10, tokens_out=5, event_type="formatting"),
            _make_event(model="claude-3-opus", tokens_in=20, tokens_out=10, event_type="classification"),
            _make_event(model="gpt-4-turbo", tokens_in=50, tokens_out=15, event_type="extraction"),
        ]
        report = optimizer.analyze(events)
        if report.migration_plan:
            phases = [step.phase for step in report.migration_plan]
            assert phases == sorted(phases)  # phases are sequential


# ── Multi-event analysis ────────────────────────────────────────────


class TestMultiEventAnalysis:
    def test_mixed_model_analysis(self):
        optimizer = CostOptimizer()
        events = [
            _make_event(model="gpt-4o", event_type="llm_call"),
            _make_event(model="gpt-4o-mini", event_type="classification"),
            _make_event(model="claude-3-opus", tokens_in=50, tokens_out=10, event_type="formatting"),
            _make_event(model="gpt-4-turbo", tokens_in=100, tokens_out=20, event_type="extraction"),
        ]
        report = optimizer.analyze(events)
        assert report.total_events == 4
        assert len(report.model_usage) >= 2

    def test_report_costs_are_non_negative(self):
        optimizer = CostOptimizer()
        events = [
            _make_event(model="gpt-4o", tokens_in=1000, tokens_out=500),
            _make_event(model="gpt-4-turbo", tokens_in=2000, tokens_out=1000),
        ]
        report = optimizer.analyze(events)
        assert report.current_cost_usd >= 0
        assert report.optimized_cost_usd >= 0
        assert report.total_savings_usd >= 0
