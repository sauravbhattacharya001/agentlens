"""Tests for Agent Tool Usage Profiler."""

from __future__ import annotations

import json
import math
import pytest

from agentlens.tool_usage import (
    AntiPattern,
    AntiPatternType,
    AgentToolProfile,
    CouplingStrength,
    OverrelianceLevel,
    Recommendation,
    RecommendationUrgency,
    ToolCoupling,
    ToolEvent,
    ToolHealthTier,
    ToolProfile,
    ToolUsageConfig,
    ToolUsageProfiler,
    ToolUsageReport,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _make_event(
    session_id: str = "sess-001",
    agent_id: str = "agent-a",
    tool_name: str = "web_search",
    success: bool = True,
    latency_ms: float = 100.0,
    tokens: int = 50,
    timestamp_ms: float = 0.0,
    retry_of: str = "",
    error_message: str = "",
) -> ToolEvent:
    return ToolEvent(
        session_id=session_id,
        agent_id=agent_id,
        tool_name=tool_name,
        success=success,
        latency_ms=latency_ms,
        tokens_consumed=tokens,
        timestamp_ms=timestamp_ms,
        retry_of=retry_of,
        error_message=error_message,
    )


def _make_profiler(events: list[ToolEvent]) -> ToolUsageProfiler:
    p = ToolUsageProfiler()
    p.add_events(events)
    return p


# ── ToolProfile dataclass ──────────────────────────────────────────


class TestToolProfile:
    def test_success_rate(self):
        tp = ToolProfile(tool_name="t", call_count=10, success_count=7, failure_count=3)
        assert tp.success_rate == pytest.approx(0.7)

    def test_failure_rate(self):
        tp = ToolProfile(tool_name="t", call_count=10, success_count=7, failure_count=3)
        assert tp.failure_rate == pytest.approx(0.3)

    def test_zero_calls(self):
        tp = ToolProfile(tool_name="t")
        assert tp.success_rate == 0.0
        assert tp.failure_rate == 0.0
        assert tp.avg_latency_ms == 0.0
        assert tp.avg_tokens == 0.0

    def test_p95_latency(self):
        tp = ToolProfile(tool_name="t", call_count=20, latencies=list(range(1, 21)))
        assert tp.p95_latency_ms == 19

    def test_p95_empty(self):
        tp = ToolProfile(tool_name="t")
        assert tp.p95_latency_ms == 0.0

    def test_to_dict(self):
        tp = ToolProfile(tool_name="web_search", call_count=5, success_count=4,
                         failure_count=1, total_latency_ms=500, total_tokens=250,
                         latencies=[100]*5)
        d = tp.to_dict()
        assert d["tool_name"] == "web_search"
        assert d["call_count"] == 5
        assert d["success_rate"] == pytest.approx(0.8)

    def test_session_spread(self):
        tp = ToolProfile(tool_name="t", sessions_used={"s1", "s2", "s3"})
        assert tp.session_spread == 3


# ── Empty profiler ──────────────────────────────────────────────────


class TestEmptyProfiler:
    def test_empty_report(self):
        p = ToolUsageProfiler()
        r = p.profile()
        assert r.total_events == 0
        assert r.health_score == 100.0

    def test_single_event(self):
        p = _make_profiler([_make_event()])
        r = p.profile()
        assert r.total_events == 1
        assert r.total_tools == 1
        assert r.total_agents == 1


# ── Engine 1: Tool Profile Aggregator ───────────────────────────────


class TestToolProfileAggregator:
    def test_basic_aggregation(self):
        events = [
            _make_event(tool_name="search", success=True, latency_ms=100, tokens=50),
            _make_event(tool_name="search", success=True, latency_ms=200, tokens=60),
            _make_event(tool_name="search", success=False, latency_ms=300, tokens=70,
                       error_message="timeout"),
        ]
        p = _make_profiler(events)
        r = p.profile()
        tp = [t for t in r.tool_profiles if t.tool_name == "search"][0]
        assert tp.call_count == 3
        assert tp.success_count == 2
        assert tp.failure_count == 1
        assert tp.avg_latency_ms == pytest.approx(200.0)

    def test_multiple_tools(self):
        events = [
            _make_event(tool_name="search"),
            _make_event(tool_name="read"),
            _make_event(tool_name="write"),
        ]
        r = _make_profiler(events).profile()
        assert r.total_tools == 3

    def test_retry_counting(self):
        events = [
            _make_event(tool_name="api_call", retry_of="api_call"),
            _make_event(tool_name="api_call", retry_of="api_call"),
            _make_event(tool_name="api_call"),
        ]
        r = _make_profiler(events).profile()
        tp = [t for t in r.tool_profiles if t.tool_name == "api_call"][0]
        assert tp.retry_count == 2

    def test_multi_session_tracking(self):
        events = [
            _make_event(session_id="s1", tool_name="search"),
            _make_event(session_id="s2", tool_name="search"),
            _make_event(session_id="s3", tool_name="search"),
        ]
        r = _make_profiler(events).profile()
        tp = [t for t in r.tool_profiles if t.tool_name == "search"][0]
        assert tp.session_spread == 3

    def test_error_message_collection(self):
        events = [
            _make_event(tool_name="api", success=False, error_message="timeout"),
            _make_event(tool_name="api", success=False, error_message="timeout"),
            _make_event(tool_name="api", success=False, error_message="rate_limited"),
        ]
        r = _make_profiler(events).profile()
        tp = [t for t in r.tool_profiles if t.tool_name == "api"][0]
        assert len(tp.error_messages) == 3


# ── Engine 2: Agent Diversity Analyzer ──────────────────────────────


class TestAgentDiversity:
    def test_single_tool_zero_diversity(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(10)]
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        assert ap.diversity_score == 0.0

    def test_uniform_high_diversity(self):
        events = []
        for tool in ["a", "b", "c", "d"]:
            for _ in range(5):
                events.append(_make_event(agent_id="x", tool_name=tool))
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "x"][0]
        assert ap.diversity_score == pytest.approx(1.0, abs=0.01)

    def test_skewed_diversity(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(18)]
        events += [_make_event(agent_id="a", tool_name="read") for _ in range(2)]
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        assert 0.0 < ap.diversity_score < 0.6

    def test_overreliance_detection(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(8)]
        events += [_make_event(agent_id="a", tool_name="other") for _ in range(2)]
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        assert "search" in ap.overreliance
        assert ap.overreliance["search"] in (
            OverrelianceLevel.MODERATE, OverrelianceLevel.SEVERE
        )

    def test_no_overreliance_balanced(self):
        events = []
        for tool in ["a", "b", "c", "d", "e"]:
            events += [_make_event(agent_id="x", tool_name=tool) for _ in range(4)]
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "x"][0]
        assert len(ap.overreliance) == 0

    def test_preferred_tools(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(10)]
        events += [_make_event(agent_id="a", tool_name="read") for _ in range(5)]
        events += [_make_event(agent_id="a", tool_name="write") for _ in range(1)]
        r = _make_profiler(events).profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        assert ap.preferred_tools[0] == "search"

    def test_avoided_tools(self):
        events = [_make_event(agent_id="a", tool_name="search")]
        events += [_make_event(agent_id="b", tool_name="read")]
        events += [_make_event(agent_id="b", tool_name="search")]
        r = _make_profiler(events).profile()
        ap_a = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        assert "read" in ap_a.avoided_tools

    def test_multiple_agents(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(5)]
        events += [_make_event(agent_id="b", tool_name="read") for _ in range(5)]
        r = _make_profiler(events).profile()
        assert r.total_agents == 2
        assert len(r.agent_profiles) == 2


# ── Engine 3: Coupling Detector ─────────────────────────────────────


class TestCouplingDetector:
    def test_co_occurrence(self):
        events = []
        for sid in ["s1", "s2", "s3"]:
            events.append(_make_event(session_id=sid, tool_name="search", timestamp_ms=1))
            events.append(_make_event(session_id=sid, tool_name="read", timestamp_ms=2))
        r = _make_profiler(events).profile()
        couplings = [c for c in r.couplings
                     if {c.tool_a, c.tool_b} == {"search", "read"}]
        assert len(couplings) >= 1
        assert couplings[0].co_occurrence_rate == pytest.approx(1.0)

    def test_sequential_counting(self):
        events = []
        for sid in ["s1", "s2"]:
            events.append(_make_event(session_id=sid, tool_name="search", timestamp_ms=1))
            events.append(_make_event(session_id=sid, tool_name="read", timestamp_ms=2))
        r = _make_profiler(events).profile()
        couplings = [c for c in r.couplings
                     if {c.tool_a, c.tool_b} == {"read", "search"}]
        assert len(couplings) >= 1
        assert couplings[0].sequential_count >= 2

    def test_locked_coupling(self):
        events = []
        for i in range(10):
            sid = f"s{i}"
            events.append(_make_event(session_id=sid, tool_name="auth", timestamp_ms=1))
            events.append(_make_event(session_id=sid, tool_name="api", timestamp_ms=2))
        r = _make_profiler(events).profile()
        locked = [c for c in r.couplings if c.strength == CouplingStrength.LOCKED]
        assert len(locked) >= 1

    def test_no_self_coupling(self):
        events = []
        for i in range(5):
            events.append(_make_event(session_id=f"s{i}", tool_name="search", timestamp_ms=1))
            events.append(_make_event(session_id=f"s{i}", tool_name="search", timestamp_ms=2))
        r = _make_profiler(events).profile()
        self_couplings = [c for c in r.couplings if c.tool_a == c.tool_b]
        assert len(self_couplings) == 0

    def test_weak_coupling_filtered(self):
        # Only 1 out of 20 sessions has co-occurrence
        events = []
        events.append(_make_event(session_id="s1", tool_name="search"))
        events.append(_make_event(session_id="s1", tool_name="rare"))
        for i in range(19):
            events.append(_make_event(session_id=f"s{i+2}", tool_name="search"))
        r = _make_profiler(events).profile()
        rare_couplings = [c for c in r.couplings
                          if "rare" in (c.tool_a, c.tool_b)
                          and c.strength == CouplingStrength.LOCKED]
        assert len(rare_couplings) == 0


# ── Engine 4: Anti-Pattern Scanner ──────────────────────────────────


class TestAntiPatternScanner:
    def test_overreliance_pattern(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(9)]
        events += [_make_event(agent_id="a", tool_name="other")]
        r = _make_profiler(events).profile()
        overreliance = [p for p in r.anti_patterns
                        if p.pattern_type == AntiPatternType.OVERRELIANCE]
        assert len(overreliance) >= 1

    def test_spray_and_pray(self):
        events = []
        for tool in ["a", "b", "c", "d", "e"]:
            for _ in range(3):
                events.append(_make_event(agent_id="x", tool_name=tool, success=False))
        r = _make_profiler(events).profile()
        spray = [p for p in r.anti_patterns
                 if p.pattern_type == AntiPatternType.SPRAY_AND_PRAY]
        assert len(spray) >= 1

    def test_retry_storm(self):
        events = []
        for i in range(10):
            events.append(_make_event(
                agent_id="a", tool_name="api",
                retry_of="api" if i > 3 else "",
            ))
        r = _make_profiler(events).profile()
        storms = [p for p in r.anti_patterns
                  if p.pattern_type == AntiPatternType.RETRY_STORM]
        assert len(storms) >= 1

    def test_failure_ignorance(self):
        events = [_make_event(agent_id="a", tool_name="broken", success=False)
                  for _ in range(6)]
        r = _make_profiler(events).profile()
        ignored = [p for p in r.anti_patterns
                   if p.pattern_type == AntiPatternType.FAILURE_IGNORANCE]
        assert len(ignored) >= 1

    def test_latency_blindness(self):
        events = [_make_event(agent_id="a", tool_name="slow", latency_ms=5000)
                  for _ in range(5)]
        r = _make_profiler(events).profile()
        blind = [p for p in r.anti_patterns
                 if p.pattern_type == AntiPatternType.LATENCY_BLINDNESS]
        assert len(blind) >= 1

    def test_token_waste(self):
        events = [_make_event(agent_id="a", tool_name="wasteful", tokens=2000)
                  for _ in range(5)]
        r = _make_profiler(events).profile()
        waste = [p for p in r.anti_patterns
                 if p.pattern_type == AntiPatternType.TOKEN_WASTE]
        assert len(waste) >= 1

    def test_sequential_lock(self):
        events = []
        for i in range(10):
            sid = f"s{i}"
            events.append(_make_event(session_id=sid, tool_name="auth", timestamp_ms=1))
            events.append(_make_event(session_id=sid, tool_name="api", timestamp_ms=2))
        r = _make_profiler(events).profile()
        locks = [p for p in r.anti_patterns
                 if p.pattern_type == AntiPatternType.SEQUENTIAL_LOCK]
        assert len(locks) >= 1

    def test_no_patterns_healthy_usage(self):
        # Use separate sessions to avoid locked coupling
        events = []
        for i, tool in enumerate(["a", "b", "c"]):
            for j in range(5):
                events.append(_make_event(
                    session_id=f"s-{tool}-{j}",
                    agent_id="x", tool_name=tool, success=True,
                    latency_ms=50, tokens=30,
                ))
        r = _make_profiler(events).profile()
        # Should have no anti-patterns with separate sessions
        severe = [p for p in r.anti_patterns if p.severity > 0.5]
        assert len(severe) == 0


# ── Engine 5: Recommendation Generator ──────────────────────────────


class TestRecommendations:
    def test_generates_from_anti_patterns(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(9)]
        events += [_make_event(agent_id="a", tool_name="other")]
        r = _make_profiler(events).profile()
        assert len(r.recommendations) >= 1

    def test_failure_rate_recommendation(self):
        events = [_make_event(tool_name="flaky", success=i < 7) for i in range(10)]
        r = _make_profiler(events).profile()
        failure_recs = [rec for rec in r.recommendations if "flaky" in rec.tool_name]
        assert len(failure_recs) >= 1

    def test_deduplication(self):
        events = [_make_event(agent_id="a", tool_name="broken", success=False)
                  for _ in range(10)]
        r = _make_profiler(events).profile()
        messages = [rec.message for rec in r.recommendations]
        assert len(messages) == len(set(messages))

    def test_urgency_ordering(self):
        events = [_make_event(tool_name="bad", success=False, latency_ms=5000, tokens=2000)
                  for _ in range(10)]
        r = _make_profiler(events).profile()
        if len(r.recommendations) >= 2:
            assert r.recommendations[0].urgency.severity >= r.recommendations[-1].urgency.severity

    def test_low_diversity_recommendation(self):
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(48)]
        events += [_make_event(agent_id="a", tool_name="other1") for _ in range(1)]
        events += [_make_event(agent_id="a", tool_name="other2") for _ in range(1)]
        r = _make_profiler(events).profile()
        div_recs = [rec for rec in r.recommendations if "diversity" in rec.message.lower()]
        assert len(div_recs) >= 1


# ── Engine 6: Health Scorer ─────────────────────────────────────────


class TestHealthScorer:
    def test_perfect_health(self):
        events = []
        for tool in ["a", "b", "c"]:
            for _ in range(5):
                events.append(_make_event(
                    agent_id="x", tool_name=tool,
                    success=True, latency_ms=50, tokens=30,
                ))
        r = _make_profiler(events).profile()
        assert r.health_score >= 75

    def test_terrible_health(self):
        events = [_make_event(
            agent_id="a", tool_name="broken",
            success=False, latency_ms=5000, tokens=2000,
        ) for _ in range(20)]
        r = _make_profiler(events).profile()
        assert r.health_score < 40

    def test_tier_classification(self):
        p = ToolUsageProfiler()
        assert p._classify_tier(90) == ToolHealthTier.EXCELLENT
        assert p._classify_tier(70) == ToolHealthTier.HEALTHY
        assert p._classify_tier(50) == ToolHealthTier.CONCERNING
        assert p._classify_tier(30) == ToolHealthTier.UNHEALTHY
        assert p._classify_tier(10) == ToolHealthTier.CRITICAL

    def test_score_range(self):
        events = [_make_event(success=i % 2 == 0, latency_ms=100 * i, tokens=50 * i)
                  for i in range(20)]
        r = _make_profiler(events).profile()
        assert 0 <= r.health_score <= 100

    def test_mixed_agents_health(self):
        events = []
        # Good agent
        for tool in ["a", "b", "c"]:
            for _ in range(5):
                events.append(_make_event(agent_id="good", tool_name=tool, success=True))
        # Bad agent
        for _ in range(15):
            events.append(_make_event(agent_id="bad", tool_name="x", success=False))
        r = _make_profiler(events).profile()
        assert 20 <= r.health_score <= 80


# ── Engine 7: Insight Generator ─────────────────────────────────────


class TestInsightGenerator:
    def test_generates_insights(self):
        events = []
        for tool in ["search", "read", "write"]:
            for _ in range(5):
                events.append(_make_event(tool_name=tool, latency_ms=100))
        r = _make_profiler(events).profile()
        assert len(r.insights) >= 1

    def test_most_used_tool_insight(self):
        events = [_make_event(tool_name="search") for _ in range(10)]
        events += [_make_event(tool_name="read") for _ in range(2)]
        r = _make_profiler(events).profile()
        most_used = [i for i in r.insights if "Most used" in i]
        assert len(most_used) >= 1
        assert "search" in most_used[0]

    def test_anti_pattern_summary_insight(self):
        events = [_make_event(agent_id="a", tool_name="broken", success=False)
                  for _ in range(10)]
        r = _make_profiler(events).profile()
        pattern_insights = [i for i in r.insights if "anti-pattern" in i.lower()]
        assert len(pattern_insights) >= 1

    def test_token_consumption_insight(self):
        events = [_make_event(tool_name="t", tokens=100) for _ in range(5)]
        r = _make_profiler(events).profile()
        token_insights = [i for i in r.insights if "token" in i.lower()]
        assert len(token_insights) >= 1


# ── Report formatting ───────────────────────────────────────────────


class TestReportFormatting:
    def test_format_report_string(self):
        events = []
        for tool in ["search", "read"]:
            for _ in range(5):
                events.append(_make_event(tool_name=tool, latency_ms=100))
        r = _make_profiler(events).profile()
        text = r.format_report()
        assert "TOOL USAGE PROFILE" in text
        assert "Health Score" in text

    def test_to_dict(self):
        events = [_make_event() for _ in range(3)]
        r = _make_profiler(events).profile()
        d = r.to_dict()
        assert "health_score" in d
        assert "tool_profiles" in d
        assert "agent_profiles" in d
        assert "couplings" in d
        assert "anti_patterns" in d
        assert "recommendations" in d
        assert "insights" in d

    def test_to_json(self):
        events = [_make_event() for _ in range(3)]
        r = _make_profiler(events).profile()
        j = r.to_json()
        parsed = json.loads(j)
        assert "health_score" in parsed

    def test_format_with_anti_patterns(self):
        events = [_make_event(agent_id="a", tool_name="broken", success=False)
                  for _ in range(10)]
        r = _make_profiler(events).profile()
        text = r.format_report()
        assert "ANTI-PATTERNS" in text

    def test_format_with_couplings(self):
        events = []
        for i in range(10):
            sid = f"s{i}"
            events.append(_make_event(session_id=sid, tool_name="auth", timestamp_ms=1))
            events.append(_make_event(session_id=sid, tool_name="api", timestamp_ms=2))
        r = _make_profiler(events).profile()
        text = r.format_report()
        assert "COUPLING" in text

    def test_empty_report_format(self):
        r = ToolUsageReport()
        text = r.format_report()
        assert "TOOL USAGE PROFILE" in text


# ── Configuration ───────────────────────────────────────────────────


class TestConfiguration:
    def test_custom_overreliance_threshold(self):
        config = ToolUsageConfig(overreliance_threshold=0.40)
        events = [_make_event(agent_id="a", tool_name="search") for _ in range(5)]
        events += [_make_event(agent_id="a", tool_name="other") for _ in range(5)]
        p = ToolUsageProfiler(config=config)
        p.add_events(events)
        r = p.profile()
        ap = [a for a in r.agent_profiles if a.agent_id == "a"][0]
        # 50% should trigger with 0.40 threshold
        assert "search" in ap.overreliance

    def test_custom_failure_threshold(self):
        config = ToolUsageConfig(failure_rate_warning=0.10)
        events = []
        for i in range(10):
            events.append(_make_event(tool_name="api", success=i < 8))
        p = ToolUsageProfiler(config=config)
        p.add_events(events)
        r = p.profile()
        recs = [rec for rec in r.recommendations if "api" in rec.tool_name]
        assert len(recs) >= 1

    def test_min_calls_filter(self):
        config = ToolUsageConfig(min_calls_for_analysis=5)
        events = [_make_event(tool_name="rare", success=False, latency_ms=5000)
                  for _ in range(2)]
        p = ToolUsageProfiler(config=config)
        p.add_events(events)
        r = p.profile()
        # Only 2 calls, below threshold — no latency/failure patterns should fire
        lat_patterns = [p for p in r.anti_patterns
                        if p.pattern_type == AntiPatternType.LATENCY_BLINDNESS]
        assert len(lat_patterns) == 0


# ── Enum properties ─────────────────────────────────────────────────


class TestEnums:
    def test_health_tier_label(self):
        assert ToolHealthTier.EXCELLENT.label == "Excellent"
        assert ToolHealthTier.CRITICAL.label == "Critical"

    def test_recommendation_urgency_severity(self):
        assert RecommendationUrgency.INFO.severity == 0
        assert RecommendationUrgency.CRITICAL.severity == 4

    def test_all_anti_pattern_types(self):
        assert len(AntiPatternType) == 8


# ── Data class serialization ────────────────────────────────────────


class TestSerialization:
    def test_tool_coupling_to_dict(self):
        c = ToolCoupling(tool_a="auth", tool_b="api", co_occurrence_count=5,
                         sequential_count=4, total_sessions=10,
                         strength=CouplingStrength.STRONG, co_occurrence_rate=0.5)
        d = c.to_dict()
        assert d["tool_a"] == "auth"
        assert d["strength"] == "strong"

    def test_anti_pattern_to_dict(self):
        ap = AntiPattern(
            pattern_type=AntiPatternType.OVERRELIANCE,
            agent_id="a", tool_name="search",
            severity=0.8, evidence="test", suggestion="fix it",
        )
        d = ap.to_dict()
        assert d["pattern_type"] == "overreliance"
        assert d["severity"] == 0.8

    def test_recommendation_to_dict(self):
        r = Recommendation(
            urgency=RecommendationUrgency.HIGH,
            message="Fix it", agent_id="a",
            expected_impact="Better",
        )
        d = r.to_dict()
        assert d["urgency"] == "high"

    def test_agent_profile_to_dict(self):
        ap = AgentToolProfile(
            agent_id="a",
            tool_counts={"search": 5},
            total_calls=5,
            diversity_score=0.0,
        )
        d = ap.to_dict()
        assert d["agent_id"] == "a"
        assert d["total_calls"] == 5


# ── Integration tests ───────────────────────────────────────────────


class TestIntegration:
    def test_full_workflow(self):
        """End-to-end test with realistic data."""
        events = []
        # Agent 1: balanced
        for tool in ["search", "read", "write", "calc"]:
            for i in range(5):
                events.append(_make_event(
                    session_id=f"s{i}", agent_id="balanced",
                    tool_name=tool, success=True,
                    latency_ms=50 + i * 10, tokens=40,
                ))
        # Agent 2: overreliant
        for i in range(20):
            events.append(_make_event(
                session_id=f"s{i % 5}", agent_id="overreliant",
                tool_name="search", success=True,
                latency_ms=200, tokens=100,
            ))
        events.append(_make_event(
            session_id="s0", agent_id="overreliant",
            tool_name="other", success=True,
        ))

        p = ToolUsageProfiler()
        p.add_events(events)
        r = p.profile()

        assert r.total_events == len(events)
        assert r.total_agents == 2
        assert r.total_tools >= 4
        assert 0 <= r.health_score <= 100
        assert r.health_tier in ToolHealthTier
        assert len(r.tool_profiles) >= 4
        assert len(r.agent_profiles) == 2
        assert len(r.insights) >= 1

        # Verify overreliant agent detected
        overreliant = [a for a in r.agent_profiles if a.agent_id == "overreliant"][0]
        assert "search" in overreliant.overreliance

    def test_add_event_vs_add_events(self):
        e1 = _make_event(tool_name="a")
        e2 = _make_event(tool_name="b")

        p1 = ToolUsageProfiler()
        p1.add_event(e1)
        p1.add_event(e2)
        r1 = p1.profile()

        p2 = ToolUsageProfiler()
        p2.add_events([e1, e2])
        r2 = p2.profile()

        assert r1.total_events == r2.total_events

    def test_large_dataset(self):
        """Profiler handles hundreds of events without error."""
        import random
        random.seed(123)
        tools = ["search", "read", "write", "exec", "calc", "summarize"]
        agents = ["alpha", "beta", "gamma"]
        events = []
        for i in range(500):
            events.append(_make_event(
                session_id=f"s{i % 20}",
                agent_id=random.choice(agents),
                tool_name=random.choice(tools),
                success=random.random() > 0.15,
                latency_ms=random.uniform(10, 3000),
                tokens=random.randint(10, 600),
                timestamp_ms=float(i * 100),
            ))
        r = _make_profiler(events).profile()
        assert r.total_events == 500
        assert 0 <= r.health_score <= 100
        assert len(r.tool_profiles) == 6
        assert len(r.agent_profiles) == 3
