"""Tests for agentlens.context_utilization — Agent Context Utilization Analyzer."""

from __future__ import annotations

import json
import math

import pytest

from agentlens.context_utilization import (
    ContextUtilizationAnalyzer,
    ContextUtilizationConfig,
    ContextUtilizationReport,
    EfficiencyGrade,
    InsightCategory,
    InsightSeverity,
    PollutionEvent,
    PollutionType,
    PressurePoint,
    RedundantFetch,
    TokenDensityResult,
    UtilizationInsight,
    WorkingMemorySnapshot,
)


# ── Helpers ─────────────────────────────────────────────────────────


class FakeToolCall:
    def __init__(self, tool_name: str = "search", tool_input: str = "query",
                 tool_output=None):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_output = tool_output


class FakeEvent:
    def __init__(
        self,
        tokens_in: int = 0,
        tokens_out: int = 0,
        event_type: str = "llm_call",
        content: str = "",
        tool_call: FakeToolCall | None = None,
        duration_ms: float | None = None,
    ):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.event_type = event_type
        self.content = content
        self.tool_call = tool_call
        self.duration_ms = duration_ms


class FakeSession:
    def __init__(self, session_id: str = "test-session", events: list | None = None):
        self.session_id = session_id
        self.events = events or []


# ── Factory Helpers ─────────────────────────────────────────────────


def make_efficient_session(n: int = 20) -> FakeSession:
    """Session with diverse, concise content and no repetition."""
    events = []
    topics = [
        "Analyzing customer data trends for quarterly review",
        "Machine learning model accuracy evaluation metrics",
        "Database optimization query execution plan review",
        "API endpoint security vulnerability assessment report",
        "Frontend component rendering performance profiling",
        "Network latency diagnostic troubleshooting results",
        "Container orchestration deployment strategy planning",
        "Microservice architecture communication pattern analysis",
        "Cache invalidation strategy implementation details",
        "Load balancing algorithm comparison benchmark results",
        "Distributed system consensus protocol verification",
        "Event sourcing aggregate state reconstruction logic",
        "Graph database traversal optimization techniques",
        "Stream processing pipeline backpressure management",
        "Observability telemetry correlation identifier tracking",
        "Feature flag gradual rollout percentage configuration",
        "Schema migration backward compatibility validation",
        "Rate limiting token bucket algorithm implementation",
        "Circuit breaker failure threshold configuration tuning",
        "Blue green deployment traffic switching automation",
    ]
    for i in range(n):
        events.append(FakeEvent(
            tokens_in=100 + i * 5,
            tokens_out=80 + i * 3,
            content=topics[i % len(topics)],
        ))
    return FakeSession(events=events)


def make_wasteful_session(n: int = 20) -> FakeSession:
    """Session with lots of repetition, filler, and waste."""
    events = []
    repeated = "Well basically actually this is really just very simply the same thing repeated over and over literally"
    for i in range(n):
        events.append(FakeEvent(
            tokens_in=200,
            tokens_out=200,
            content=repeated,
        ))
    return FakeSession(events=events)


def make_high_pressure_session(n: int = 30, tokens_per: int = 5000) -> FakeSession:
    """Session approaching context limit fast."""
    events = []
    for i in range(n):
        events.append(FakeEvent(
            tokens_in=tokens_per,
            tokens_out=tokens_per,
            content=f"Large event payload number {i} with significant data volume",
        ))
    return FakeSession(events=events)


def make_tool_heavy_session(n: int = 15) -> FakeSession:
    """Session with many verbose tool outputs."""
    events = []
    for i in range(n):
        verbose_output = "x" * 800 if i % 2 == 0 else "ok"
        events.append(FakeEvent(
            tokens_in=50,
            tokens_out=50,
            tool_call=FakeToolCall(
                tool_name="search",
                tool_input=f"query {i}",
                tool_output=verbose_output,
            ),
        ))
    return FakeSession(events=events)


def make_redundant_fetch_session() -> FakeSession:
    """Session with repeated tool calls to same endpoint."""
    events = []
    for i in range(20):
        # Same tool call repeated every 4 events
        query = f"query {i % 4}"
        events.append(FakeEvent(
            tokens_in=100,
            tokens_out=100,
            tool_call=FakeToolCall(
                tool_name="search",
                tool_input=query,
                tool_output=f"result for {query}",
            ),
        ))
    return FakeSession(events=events)


# ── Basic Tests ─────────────────────────────────────────────────────


class TestAnalyzerBasic:
    def test_empty_session(self):
        analyzer = ContextUtilizationAnalyzer()
        report = analyzer.analyze(FakeSession(events=[]))
        assert report.utilization_score >= 0
        assert report.total_events == 0
        assert isinstance(report.grade, EfficiencyGrade)

    def test_single_event(self):
        session = FakeSession(events=[FakeEvent(tokens_in=100, tokens_out=50, content="Hello world test")])
        analyzer = ContextUtilizationAnalyzer()
        report = analyzer.analyze(session)
        assert report.total_events == 1
        assert report.total_tokens == 150
        assert 0 <= report.utilization_score <= 100

    def test_session_id_propagated(self):
        session = FakeSession(session_id="my-session-123", events=[FakeEvent(tokens_in=10)])
        report = ContextUtilizationAnalyzer().analyze(session)
        assert report.session_id == "my-session-123"

    def test_dict_session(self):
        session = {
            "session_id": "dict-test",
            "events": [
                {"tokens_in": 100, "tokens_out": 50, "content": "test content"},
                {"tokens_in": 80, "tokens_out": 40, "content": "more content"},
            ],
        }
        report = ContextUtilizationAnalyzer().analyze(session)
        assert report.session_id == "dict-test"
        assert report.total_events == 2

    def test_efficient_session_scores_high(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        assert report.utilization_score > 50
        assert report.grade in (EfficiencyGrade.A, EfficiencyGrade.B, EfficiencyGrade.C)

    def test_wasteful_session_scores_lower(self):
        efficient = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        wasteful = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        assert efficient.utilization_score > wasteful.utilization_score


# ── Grade Tests ─────────────────────────────────────────────────────


class TestGrades:
    def test_grade_a(self):
        assert ContextUtilizationAnalyzer._score_to_grade(95) == EfficiencyGrade.A

    def test_grade_b(self):
        assert ContextUtilizationAnalyzer._score_to_grade(80) == EfficiencyGrade.B

    def test_grade_c(self):
        assert ContextUtilizationAnalyzer._score_to_grade(65) == EfficiencyGrade.C

    def test_grade_d(self):
        assert ContextUtilizationAnalyzer._score_to_grade(45) == EfficiencyGrade.D

    def test_grade_f(self):
        assert ContextUtilizationAnalyzer._score_to_grade(20) == EfficiencyGrade.F

    def test_grade_boundary_90(self):
        assert ContextUtilizationAnalyzer._score_to_grade(90) == EfficiencyGrade.A

    def test_grade_boundary_75(self):
        assert ContextUtilizationAnalyzer._score_to_grade(75) == EfficiencyGrade.B

    def test_grade_boundary_60(self):
        assert ContextUtilizationAnalyzer._score_to_grade(60) == EfficiencyGrade.C

    def test_grade_boundary_40(self):
        assert ContextUtilizationAnalyzer._score_to_grade(40) == EfficiencyGrade.D


# ── Token Density Tests ─────────────────────────────────────────────


class TestTokenDensity:
    def test_diverse_content_high_density(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        assert report.density.unique_concept_count > 10
        assert report.density.score > 30

    def test_repetitive_content_low_density(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        assert report.density.filler_pct > 0.05

    def test_filler_detection(self):
        events = [
            FakeEvent(
                tokens_in=100, tokens_out=100,
                content="Well basically actually this is really just very simply quite honestly literally totally absolutely"
            )
            for _ in range(5)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.density.filler_pct > 0.1

    def test_per_window_density_computed(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session(20))
        assert len(report.density.per_window_density) > 0

    def test_zero_token_event_handled(self):
        events = [FakeEvent(tokens_in=0, tokens_out=0, content="hello")]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.density.total_tokens == 0 or report.density.score >= 0


# ── Pollution Tests ─────────────────────────────────────────────────


class TestPollution:
    def test_no_pollution_in_diverse_session(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        assert report.pollution_score > 70

    def test_repeated_content_detected(self):
        same_text = "This is exactly the same repeated content that keeps showing up in every single event"
        events = [FakeEvent(tokens_in=100, tokens_out=100, content=same_text) for _ in range(10)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        redundant = [p for p in report.pollution_events if p.pollution_type == PollutionType.REDUNDANT_CONTENT]
        assert len(redundant) > 0

    def test_repeated_tool_calls_detected(self):
        report = ContextUtilizationAnalyzer().analyze(make_redundant_fetch_session())
        stale = [p for p in report.pollution_events if p.pollution_type == PollutionType.STALE_TOOL_OUTPUT]
        assert len(stale) > 0

    def test_filler_heavy_events_detected(self):
        events = [
            FakeEvent(
                tokens_in=100, tokens_out=100,
                content="Well basically actually just really very quite honestly literally totally completely this really is basically very"
            )
            for _ in range(5)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        filler_events = [p for p in report.pollution_events if p.pollution_type == PollutionType.IRRELEVANT_FILLER]
        assert len(filler_events) > 0

    def test_pollution_score_decreases_with_waste(self):
        clean_report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        dirty_report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        assert clean_report.pollution_score >= dirty_report.pollution_score


# ── Working Memory Tests ────────────────────────────────────────────


class TestWorkingMemory:
    def test_snapshots_created(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        assert len(report.working_memory_snapshots) == 20

    def test_efficiency_ratio_bounded(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        for snap in report.working_memory_snapshots:
            assert 0 <= snap.efficiency_ratio <= 1.0

    def test_dead_weight_increases_over_time(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session(30))
        snaps = report.working_memory_snapshots
        # Later snapshots should have more dead weight than early ones
        if len(snaps) > 10:
            assert snaps[-1].dead_weight_tokens >= snaps[0].dead_weight_tokens

    def test_short_session_high_efficiency(self):
        events = [FakeEvent(tokens_in=100, tokens_out=50) for _ in range(3)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.working_memory_score > 50


# ── Overhead Tests ──────────────────────────────────────────────────


class TestOverhead:
    def test_low_overhead_high_score(self):
        events = [FakeEvent(tokens_in=10, tokens_out=10)] + [
            FakeEvent(tokens_in=100, tokens_out=100) for _ in range(19)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.overhead_score > 70

    def test_system_prompt_overhead(self):
        events = [FakeEvent(tokens_in=5000, tokens_out=0, event_type="system_prompt")] + [
            FakeEvent(tokens_in=100, tokens_out=100) for _ in range(10)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.overhead_pct > 0.1

    def test_no_events_no_overhead(self):
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=[]))
        assert report.overhead_pct == 0.0
        assert report.overhead_score == 100.0


# ── Tool Output Tests ───────────────────────────────────────────────


class TestToolOutput:
    def test_verbose_outputs_detected(self):
        report = ContextUtilizationAnalyzer().analyze(make_tool_heavy_session())
        assert report.tool_output_verbose_count > 0
        assert report.tool_output_total_waste > 0

    def test_concise_outputs_score_high(self):
        events = [
            FakeEvent(
                tokens_in=50, tokens_out=50,
                tool_call=FakeToolCall(tool_output="ok"),
            )
            for _ in range(10)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.tool_output_score > 80

    def test_no_tool_events_perfect_score(self):
        events = [FakeEvent(tokens_in=100, tokens_out=100) for _ in range(5)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.tool_output_score == 100.0


# ── Pressure Tests ──────────────────────────────────────────────────


class TestPressure:
    def test_low_pressure_high_score(self):
        events = [FakeEvent(tokens_in=100, tokens_out=100) for _ in range(10)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.pressure_score > 80

    def test_high_pressure_low_score(self):
        report = ContextUtilizationAnalyzer(context_limit_tokens=10000).analyze(
            make_high_pressure_session(n=20, tokens_per=500)
        )
        assert report.pressure_score < 80

    def test_pressure_points_created(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        assert len(report.pressure_points) == 20
        for pp in report.pressure_points:
            assert 0 <= pp.usage_pct <= 1.0

    def test_projected_exhaustion_computed(self):
        report = ContextUtilizationAnalyzer(context_limit_tokens=50000).analyze(
            make_efficient_session(20)
        )
        # Some later points should have projections
        with_proj = [p for p in report.pressure_points if p.projected_exhaustion_events is not None]
        assert len(with_proj) > 0

    def test_cumulative_tokens_monotonic(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        prev = 0
        for pp in report.pressure_points:
            assert pp.cumulative_tokens >= prev
            prev = pp.cumulative_tokens


# ── Retrieval Efficiency Tests ──────────────────────────────────────


class TestRetrievalEfficiency:
    def test_redundant_fetches_detected(self):
        report = ContextUtilizationAnalyzer().analyze(make_redundant_fetch_session())
        assert len(report.redundant_fetches) > 0

    def test_unique_fetches_score_high(self):
        events = [
            FakeEvent(
                tokens_in=50, tokens_out=50,
                tool_call=FakeToolCall(tool_name="search", tool_input=f"unique query {i}"),
            )
            for i in range(10)
        ]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.retrieval_score == 100.0

    def test_no_tool_events_perfect_retrieval(self):
        events = [FakeEvent(tokens_in=100, tokens_out=100) for _ in range(5)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.retrieval_score == 100.0


# ── Insight Generation Tests ────────────────────────────────────────


class TestInsights:
    def test_wasteful_session_has_insights(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        assert len(report.insights) > 0

    def test_insights_sorted_by_severity(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        if len(report.insights) > 1:
            sev_order = {InsightSeverity.CRITICAL: 0, InsightSeverity.WARNING: 1, InsightSeverity.INFO: 2}
            for i in range(len(report.insights) - 1):
                assert sev_order[report.insights[i].severity] <= sev_order[report.insights[i + 1].severity]

    def test_insight_has_recommendation(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        for ins in report.insights:
            assert ins.recommendation

    def test_insight_categories_valid(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        for ins in report.insights:
            assert isinstance(ins.category, InsightCategory)
            assert isinstance(ins.severity, InsightSeverity)

    def test_high_pressure_triggers_insight(self):
        report = ContextUtilizationAnalyzer(context_limit_tokens=5000).analyze(
            make_high_pressure_session(n=10, tokens_per=500)
        )
        pressure_insights = [i for i in report.insights if i.category == InsightCategory.PRESSURE]
        assert len(pressure_insights) > 0

    def test_redundant_fetch_triggers_insight(self):
        report = ContextUtilizationAnalyzer().analyze(make_redundant_fetch_session())
        fetch_insights = [i for i in report.insights if i.category == InsightCategory.REDUNDANT_FETCH]
        assert len(fetch_insights) > 0


# ── Report Output Tests ─────────────────────────────────────────────


class TestReportOutput:
    def test_format_report_produces_string(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        text = report.format_report()
        assert isinstance(text, str)
        assert len(text) > 100
        assert "CONTEXT UTILIZATION" in text

    def test_format_report_contains_score(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        text = report.format_report()
        assert "Utilization Score:" in text

    def test_format_report_contains_engines(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        text = report.format_report()
        assert "ENGINE SCORES" in text
        assert "Token Density" in text

    def test_to_dict_is_json_serializable(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        d = report.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["session_id"] == "test-session"

    def test_to_dict_has_all_engines(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        d = report.to_dict()
        assert "engines" in d
        engines = d["engines"]
        for key in ("token_density", "pollution_control", "working_memory",
                     "prompt_overhead", "tool_output", "window_pressure",
                     "retrieval_efficiency"):
            assert key in engines
            assert "score" in engines[key]

    def test_to_dict_grade_is_string(self):
        report = ContextUtilizationAnalyzer().analyze(make_efficient_session())
        d = report.to_dict()
        assert d["grade"] in ("A", "B", "C", "D", "F")

    def test_to_dict_insights_list(self):
        report = ContextUtilizationAnalyzer().analyze(make_wasteful_session())
        d = report.to_dict()
        assert isinstance(d["insights"], list)
        if d["insights"]:
            ins = d["insights"][0]
            assert "category" in ins
            assert "severity" in ins
            assert "description" in ins
            assert "recommendation" in ins


# ── Config Tests ────────────────────────────────────────────────────


class TestConfig:
    def test_custom_context_limit(self):
        analyzer = ContextUtilizationAnalyzer(context_limit_tokens=10000)
        report = analyzer.analyze(make_high_pressure_session(n=5, tokens_per=1500))
        # With smaller limit, pressure should be higher
        assert report.pressure_score < 90

    def test_custom_window_size(self):
        analyzer = ContextUtilizationAnalyzer(window_size=3)
        report = analyzer.analyze(make_efficient_session(10))
        assert len(report.working_memory_snapshots) == 10

    def test_config_object(self):
        cfg = ContextUtilizationConfig(
            context_limit_tokens=50000,
            window_size=3,
            filler_threshold=0.20,
        )
        analyzer = ContextUtilizationAnalyzer(config=cfg)
        assert analyzer.config.context_limit_tokens == 50000
        assert analyzer.config.window_size == 3

    def test_default_config(self):
        analyzer = ContextUtilizationAnalyzer()
        assert analyzer.config.context_limit_tokens == 128000
        assert analyzer.config.window_size == 5


# ── Edge Cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_zero_tokens(self):
        events = [FakeEvent(tokens_in=0, tokens_out=0) for _ in range(5)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert 0 <= report.utilization_score <= 100

    def test_very_large_session(self):
        events = [FakeEvent(tokens_in=50, tokens_out=50, content=f"Event {i} content") for i in range(100)]
        report = ContextUtilizationAnalyzer().analyze(FakeSession(events=events))
        assert report.total_events == 100
        assert 0 <= report.utilization_score <= 100

    def test_score_bounded_0_100(self):
        for factory in [make_efficient_session, make_wasteful_session, make_high_pressure_session]:
            report = ContextUtilizationAnalyzer().analyze(factory())
            assert 0 <= report.utilization_score <= 100

    def test_enum_values(self):
        assert EfficiencyGrade.A.value == "A"
        assert PollutionType.REDUNDANT_CONTENT.value == "redundant_content"
        assert InsightSeverity.CRITICAL.value == "critical"
        assert InsightCategory.DENSITY.value == "density"
