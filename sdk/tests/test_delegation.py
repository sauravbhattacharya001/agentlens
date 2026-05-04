"""Tests for Agent Delegation Analyzer."""

from __future__ import annotations

import json
import pytest

from agentlens.delegation import (
    AntiPattern,
    AntiPatternType,
    AgentDelegationProfile,
    DelegationAnalyzer,
    DelegationConfig,
    DelegationEdge,
    DelegationEvent,
    DelegationHealthTier,
    DelegationReport,
    Recommendation,
    RecommendationUrgency,
    Severity,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _make_event(
    session_id: str = "sess-001",
    parent: str = "coordinator",
    child: str = "worker-1",
    success: bool = True,
    latency_ms: float = 500.0,
    tokens: int = 100,
    depth: int = 1,
    delegation_type: str = "sub_agent",
    was_re_delegated: bool = False,
    timestamp_ms: float = 0.0,
    error_message: str = "",
) -> DelegationEvent:
    return DelegationEvent(
        session_id=session_id,
        parent_agent_id=parent,
        child_agent_id=child,
        success=success,
        latency_ms=latency_ms,
        tokens_consumed=tokens,
        depth=depth,
        delegation_type=delegation_type,
        was_re_delegated=was_re_delegated,
        timestamp_ms=timestamp_ms,
        error_message=error_message,
    )


def _make_analyzer(events: list[DelegationEvent], config: DelegationConfig | None = None) -> DelegationAnalyzer:
    a = DelegationAnalyzer(config=config)
    a.add_events(events)
    return a


# ── Basic Tests ─────────────────────────────────────────────────────


class TestBasic:
    def test_empty_analyzer(self):
        a = DelegationAnalyzer()
        report = a.analyze()
        assert report.total_events == 0
        assert report.health_score == 100.0

    def test_single_event(self):
        a = DelegationAnalyzer()
        a.add_event(_make_event())
        report = a.analyze()
        assert report.total_events == 1
        assert report.total_agents == 2
        assert report.health_score > 0

    def test_add_events_batch(self):
        events = [_make_event(session_id=f"s-{i}") for i in range(5)]
        a = _make_analyzer(events)
        report = a.analyze()
        assert report.total_events == 5

    def test_health_tier_excellent(self):
        events = [_make_event(session_id=f"s-{i}", child=f"w-{i%3}") for i in range(10)]
        report = _make_analyzer(events).analyze()
        assert report.health_tier == DelegationHealthTier.EXCELLENT
        assert report.health_score >= 80

    def test_delegation_types(self):
        events = [
            _make_event(delegation_type="sub_agent"),
            _make_event(delegation_type="tool_call"),
            _make_event(delegation_type="human_escalation"),
        ]
        report = _make_analyzer(events).analyze()
        assert report.total_events == 3


# ── Graph Builder Tests ─────────────────────────────────────────────


class TestGraphBuilder:
    def test_single_edge(self):
        events = [_make_event(parent="A", child="B")]
        report = _make_analyzer(events).analyze()
        assert len(report.delegation_graph) == 1
        edge = report.delegation_graph[0]
        assert edge.parent == "A"
        assert edge.child == "B"
        assert edge.count == 1

    def test_multiple_edges(self):
        events = [
            _make_event(parent="A", child="B"),
            _make_event(parent="A", child="C"),
            _make_event(parent="B", child="C"),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.delegation_graph) == 3

    def test_edge_aggregation(self):
        events = [
            _make_event(parent="A", child="B", success=True, latency_ms=100),
            _make_event(parent="A", child="B", success=True, latency_ms=300),
            _make_event(parent="A", child="B", success=False, latency_ms=200),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.delegation_graph) == 1
        edge = report.delegation_graph[0]
        assert edge.count == 3
        assert edge.successes == 2
        assert abs(edge.success_rate - 2 / 3) < 0.01
        assert abs(edge.avg_latency_ms - 200.0) < 0.01

    def test_total_agents_counted(self):
        events = [
            _make_event(parent="A", child="B"),
            _make_event(parent="B", child="C"),
            _make_event(parent="A", child="C"),
        ]
        report = _make_analyzer(events).analyze()
        assert report.total_agents == 3


# ── Depth Analyzer Tests ────────────────────────────────────────────


class TestDepthAnalyzer:
    def test_depth_distribution(self):
        events = [
            _make_event(depth=1),
            _make_event(depth=1),
            _make_event(depth=2),
            _make_event(depth=3),
        ]
        report = _make_analyzer(events).analyze()
        assert report.depth_distribution == {1: 2, 2: 1, 3: 1}

    def test_max_chain_depth(self):
        events = [_make_event(depth=i) for i in range(1, 6)]
        report = _make_analyzer(events).analyze()
        assert report.max_chain_depth == 5

    def test_avg_chain_depth(self):
        events = [_make_event(depth=1), _make_event(depth=3)]
        report = _make_analyzer(events).analyze()
        assert abs(report.avg_chain_depth - 2.0) < 0.01

    def test_deep_chain_anti_pattern(self):
        events = [_make_event(depth=5, session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events).analyze()
        deep_patterns = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.DEEP_CHAIN]
        assert len(deep_patterns) == 1
        assert deep_patterns[0].evidence["max_depth"] == 5

    def test_no_deep_chain_when_within_threshold(self):
        events = [_make_event(depth=2) for _ in range(10)]
        report = _make_analyzer(events).analyze()
        deep_patterns = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.DEEP_CHAIN]
        assert len(deep_patterns) == 0


# ── Bottleneck Detector Tests ───────────────────────────────────────


class TestBottleneckDetector:
    def test_bottleneck_detected(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=5)
        events = [_make_event(parent=f"p-{i}", child="bottleneck", session_id=f"s-{i}") for i in range(8)]
        report = _make_analyzer(events, config=config).analyze()
        assert "bottleneck" in report.bottleneck_agents

    def test_no_bottleneck_below_threshold(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=10)
        events = [_make_event(parent=f"p-{i}", child="target", session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events, config=config).analyze()
        assert "target" not in report.bottleneck_agents

    def test_bottleneck_severity_critical(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=5)
        events = [_make_event(parent=f"p-{i}", child="hotspot", session_id=f"s-{i}") for i in range(12)]
        report = _make_analyzer(events, config=config).analyze()
        patterns = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.BOTTLENECK_AGENT]
        assert any(p.severity == Severity.CRITICAL for p in patterns)


# ── Over-Delegation Tests ───────────────────────────────────────────


class TestOverDelegation:
    def test_over_delegation_detected(self):
        config = DelegationConfig(over_delegation_threshold=0.7, min_events_for_pattern=3)
        # Agent "boss" delegates 10 tasks, self-handles 0
        events = [_make_event(parent="boss", child=f"w-{i%3}", session_id=f"s-{i}") for i in range(10)]
        report = _make_analyzer(events, config=config).analyze()
        over_del = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.OVER_DELEGATION]
        assert len(over_del) >= 1
        assert any(ap.agent_id == "boss" for ap in over_del)

    def test_no_over_delegation_when_balanced(self):
        config = DelegationConfig(over_delegation_threshold=0.8, min_events_for_pattern=3)
        # Agent does some delegating and some receiving (self-handling)
        events = [
            _make_event(parent="agent", child="helper", session_id="s-1"),
            _make_event(parent="agent", child="helper", session_id="s-2"),
            _make_event(parent="agent", child="helper", session_id="s-3"),
            # Agent also receives work in other sessions (self-handles)
            _make_event(parent="boss", child="agent", session_id="s-4"),
            _make_event(parent="boss", child="agent", session_id="s-5"),
            _make_event(parent="boss", child="agent", session_id="s-6"),
            _make_event(parent="boss", child="agent", session_id="s-7"),
        ]
        report = _make_analyzer(events, config=config).analyze()
        over_del = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.OVER_DELEGATION and ap.agent_id == "agent"]
        # Agent delegates 3 but self-handles in 4 sessions — ratio is reasonable
        assert len(over_del) == 0

    def test_rubber_stamping_detected(self):
        config = DelegationConfig(min_events_for_pattern=3)
        # "middleman" receives work and re-delegates it
        events = []
        for i in range(6):
            events.append(_make_event(parent="boss", child="middleman", session_id=f"s-{i}", was_re_delegated=True))
            events.append(_make_event(parent="middleman", child=f"w-{i%2}", session_id=f"s-{i}"))
        report = _make_analyzer(events, config=config).analyze()
        rubber = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.RUBBER_STAMPING]
        assert len(rubber) >= 1


# ── Accountability Gap Tests ────────────────────────────────────────


class TestAccountabilityGap:
    def test_gap_detected_high_failure_rate(self):
        config = DelegationConfig(accountability_gap_threshold=0.5, min_events_for_pattern=3)
        # parent delegates to child with 80% failure
        events = [
            _make_event(parent="mgr", child="flaky", success=False, session_id="s-1"),
            _make_event(parent="mgr", child="flaky", success=False, session_id="s-2"),
            _make_event(parent="mgr", child="flaky", success=False, session_id="s-3"),
            _make_event(parent="mgr", child="flaky", success=True, session_id="s-4"),
        ]
        report = _make_analyzer(events, config=config).analyze()
        gaps = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.ACCOUNTABILITY_GAP]
        assert len(gaps) >= 1
        assert gaps[0].agent_id == "mgr"

    def test_no_gap_when_success_high(self):
        config = DelegationConfig(accountability_gap_threshold=0.5, min_events_for_pattern=3)
        events = [
            _make_event(parent="mgr", child="reliable", success=True, session_id=f"s-{i}")
            for i in range(5)
        ]
        report = _make_analyzer(events, config=config).analyze()
        gaps = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.ACCOUNTABILITY_GAP]
        assert len(gaps) == 0


# ── Circular Delegation Tests ───────────────────────────────────────


class TestCircularDelegation:
    def test_circular_detected(self):
        events = [
            _make_event(parent="A", child="B", session_id="s-1"),
            _make_event(parent="B", child="A", session_id="s-2"),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.circular_pairs) == 1
        pair = report.circular_pairs[0]
        assert set(pair) == {"A", "B"}

    def test_no_circular_unidirectional(self):
        events = [
            _make_event(parent="A", child="B"),
            _make_event(parent="A", child="C"),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.circular_pairs) == 0

    def test_multiple_circular_pairs(self):
        events = [
            _make_event(parent="A", child="B", session_id="s-1"),
            _make_event(parent="B", child="A", session_id="s-2"),
            _make_event(parent="C", child="D", session_id="s-3"),
            _make_event(parent="D", child="C", session_id="s-4"),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.circular_pairs) == 2


# ── Unbalanced Load Tests ───────────────────────────────────────────


class TestUnbalancedLoad:
    def test_unbalanced_detected(self):
        config = DelegationConfig(imbalance_gini_threshold=0.4)
        # One agent gets 20 delegations, others get 1 each
        events = [_make_event(parent=f"p-{i}", child="heavy", session_id=f"s-{i}") for i in range(20)]
        events += [_make_event(parent="x", child="light-1", session_id="s-20")]
        events += [_make_event(parent="y", child="light-2", session_id="s-21")]
        events += [_make_event(parent="z", child="light-3", session_id="s-22")]
        report = _make_analyzer(events, config=config).analyze()
        unbal = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.UNBALANCED_LOAD]
        assert len(unbal) >= 1

    def test_balanced_load_no_pattern(self):
        config = DelegationConfig(imbalance_gini_threshold=0.6)
        events = []
        for i in range(9):
            events.append(_make_event(parent="coord", child=f"w-{i%3}", session_id=f"s-{i}"))
        report = _make_analyzer(events, config=config).analyze()
        unbal = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.UNBALANCED_LOAD]
        assert len(unbal) == 0


# ── Health Score Tests ──────────────────────────────────────────────


class TestHealthScore:
    def test_perfect_health(self):
        events = [_make_event(session_id=f"s-{i}", child=f"w-{i%3}", depth=1) for i in range(10)]
        report = _make_analyzer(events).analyze()
        assert report.health_score >= 80

    def test_score_decreases_with_deep_chains(self):
        events = [_make_event(depth=5, session_id=f"s-{i}") for i in range(10)]
        report = _make_analyzer(events).analyze()
        assert report.health_score < 100

    def test_score_decreases_with_bottlenecks(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=3)
        events = [_make_event(parent=f"p-{i}", child="bot", session_id=f"s-{i}") for i in range(10)]
        report = _make_analyzer(events, config=config).analyze()
        assert report.health_score < 100

    def test_score_clamped_to_zero(self):
        # Create a horrible delegation setup
        config = DelegationConfig(
            bottleneck_fan_in_threshold=2,
            over_delegation_threshold=0.5,
            accountability_gap_threshold=0.3,
            min_events_for_pattern=2,
        )
        events = []
        # Deep chains + failures + circular + bottleneck
        for i in range(20):
            events.append(_make_event(parent="A", child="B", depth=6, success=False, session_id=f"s-{i}"))
        for i in range(20):
            events.append(_make_event(parent="B", child="A", depth=5, success=False, session_id=f"s2-{i}"))
        for i in range(20):
            events.append(_make_event(parent=f"x-{i}", child="B", session_id=f"s3-{i}"))
        report = _make_analyzer(events, config=config).analyze()
        assert report.health_score >= 0

    def test_tier_classification(self):
        # Just verify tiers align with score ranges
        a = DelegationAnalyzer()
        a.add_events([_make_event()])
        report = a.analyze()
        if report.health_score >= 80:
            assert report.health_tier == DelegationHealthTier.EXCELLENT
        elif report.health_score >= 60:
            assert report.health_tier == DelegationHealthTier.HEALTHY


# ── Insight Generator Tests ─────────────────────────────────────────


class TestInsights:
    def test_insights_generated(self):
        events = [
            _make_event(parent="A", child="B"),
            _make_event(parent="A", child="C"),
        ]
        report = _make_analyzer(events).analyze()
        assert len(report.insights) > 0

    def test_delegation_type_insight(self):
        events = [_make_event(delegation_type="sub_agent")]
        report = _make_analyzer(events).analyze()
        assert any("Delegation types" in ins for ins in report.insights)

    def test_success_rate_insight(self):
        events = [_make_event(success=True), _make_event(success=False)]
        report = _make_analyzer(events).analyze()
        assert any("success rate" in ins for ins in report.insights)

    def test_top_delegator_insight(self):
        events = [_make_event(parent="boss", child=f"w-{i}", session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events).analyze()
        assert any("boss" in ins for ins in report.insights)

    def test_re_delegation_insight(self):
        events = [_make_event(was_re_delegated=True) for _ in range(3)]
        report = _make_analyzer(events).analyze()
        assert any("Re-delegation" in ins for ins in report.insights)

    def test_depth_warning_insight(self):
        events = [_make_event(depth=5)]
        report = _make_analyzer(events).analyze()
        assert any("chain depth" in ins for ins in report.insights)


# ── Recommendation Tests ────────────────────────────────────────────


class TestRecommendations:
    def test_recommendations_generated_for_patterns(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=3, min_events_for_pattern=2)
        events = [_make_event(parent=f"p-{i}", child="bot", session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events, config=config).analyze()
        assert len(report.recommendations) > 0

    def test_recommendation_has_urgency(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=3, min_events_for_pattern=2)
        events = [_make_event(parent=f"p-{i}", child="bot", session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events, config=config).analyze()
        for rec in report.recommendations:
            assert isinstance(rec.urgency, RecommendationUrgency)


# ── Serialization Tests ─────────────────────────────────────────────


class TestSerialization:
    def test_to_dict(self):
        events = [_make_event(parent="A", child="B")]
        report = _make_analyzer(events).analyze()
        d = report.to_dict()
        assert "health_score" in d
        assert "health_tier" in d
        assert "delegation_graph" in d
        assert "anti_patterns" in d
        assert "insights" in d

    def test_to_dict_json_serializable(self):
        events = [
            _make_event(parent="A", child="B"),
            _make_event(parent="B", child="C", success=False),
        ]
        report = _make_analyzer(events).analyze()
        # Should not raise
        json_str = json.dumps(report.to_dict())
        assert len(json_str) > 0

    def test_format_report(self):
        events = [_make_event(parent="A", child="B")]
        report = _make_analyzer(events).analyze()
        text = report.format_report()
        assert "AGENT DELEGATION ANALYZER" in text
        assert "Health Score" in text

    def test_format_report_with_patterns(self):
        config = DelegationConfig(bottleneck_fan_in_threshold=2, min_events_for_pattern=2)
        events = [_make_event(parent=f"p-{i}", child="bot", session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events, config=config).analyze()
        text = report.format_report()
        assert "Anti-Patterns" in text

    def test_edge_to_dict(self):
        edge = DelegationEdge(parent="A", child="B", count=10, successes=8, total_latency_ms=5000.0)
        d = edge.to_dict()
        assert d["parent"] == "A"
        assert d["success_rate"] == 0.8
        assert d["avg_latency_ms"] == 500.0

    def test_anti_pattern_to_dict(self):
        ap = AntiPattern(
            pattern_type=AntiPatternType.DEEP_CHAIN,
            severity=Severity.HIGH,
            agent_id="test",
            description="Too deep",
        )
        d = ap.to_dict()
        assert d["pattern_type"] == "deep_chain"
        assert d["severity"] == "high"

    def test_recommendation_to_dict(self):
        rec = Recommendation(
            urgency=RecommendationUrgency.HIGH,
            message="Fix it",
            target_agent="agent-x",
        )
        d = rec.to_dict()
        assert d["urgency"] == "high"
        assert d["target_agent"] == "agent-x"


# ── Profile Tests ───────────────────────────────────────────────────


class TestProfiles:
    def test_delegation_ratio(self):
        prof = AgentDelegationProfile(agent_id="x", delegations_sent=8, tasks_self_handled=2)
        assert abs(prof.delegation_ratio - 0.8) < 0.01

    def test_delegation_ratio_zero_total(self):
        prof = AgentDelegationProfile(agent_id="x")
        assert prof.delegation_ratio == 0.0

    def test_fan_in_out(self):
        prof = AgentDelegationProfile(agent_id="x", unique_children=3, unique_parents=2)
        assert prof.fan_out == 3
        assert prof.fan_in == 2

    def test_profile_to_dict(self):
        prof = AgentDelegationProfile(agent_id="x", delegations_sent=5, delegations_received=3)
        d = prof.to_dict()
        assert d["agent_id"] == "x"
        assert d["delegations_sent"] == 5


# ── Config Tests ────────────────────────────────────────────────────


class TestConfig:
    def test_custom_config(self):
        config = DelegationConfig(max_healthy_depth=5, over_delegation_threshold=0.9)
        events = [_make_event(depth=4, session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events, config=config).analyze()
        # depth 4 is within threshold of 5 — no deep chain pattern
        deep = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.DEEP_CHAIN]
        assert len(deep) == 0

    def test_config_defaults(self):
        config = DelegationConfig()
        assert config.max_healthy_depth == 3
        assert config.over_delegation_threshold == 0.80
        assert config.bottleneck_fan_in_threshold == 10


# ── Edge Cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_failures(self):
        events = [_make_event(success=False, session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events).analyze()
        assert report.total_events == 5
        assert any("0.0%" in ins for ins in report.insights)

    def test_same_parent_and_child(self):
        # Self-delegation edge case
        events = [_make_event(parent="self", child="self")]
        report = _make_analyzer(events).analyze()
        assert report.total_agents == 1

    def test_large_event_count(self):
        events = [_make_event(session_id=f"s-{i}", child=f"w-{i%10}") for i in range(100)]
        report = _make_analyzer(events).analyze()
        assert report.total_events == 100

    def test_very_deep_chain(self):
        events = [_make_event(depth=20, session_id=f"s-{i}") for i in range(5)]
        report = _make_analyzer(events).analyze()
        assert report.max_chain_depth == 20

    def test_gini_all_equal(self):
        gini = DelegationAnalyzer._gini_coefficient([5, 5, 5, 5])
        assert abs(gini) < 0.01

    def test_gini_all_to_one(self):
        gini = DelegationAnalyzer._gini_coefficient([0, 0, 0, 100])
        assert gini > 0.5

    def test_gini_empty(self):
        gini = DelegationAnalyzer._gini_coefficient([])
        assert gini == 0.0
