"""Tests for the Session Autopsy module."""

import pytest
from types import SimpleNamespace

from agentlens.autopsy import (
    SessionAutopsy,
    AutopsyConfig,
    AutopsyReport,
    Evidence,
    EvidenceSource,
    Hypothesis,
    RemediationAction,
    IncidentPriority,
    EffortLevel,
    CausalRelation,
    CausalLink,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _make_event(
    event_type="llm_call",
    duration_ms=100,
    tokens_in=500,
    tokens_out=200,
    tool_call=None,
):
    return SimpleNamespace(
        event_type=event_type,
        duration_ms=duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_call=tool_call,
    )


def _make_session(session_id="test-001", events=None):
    return SimpleNamespace(
        session_id=session_id,
        events=events or [],
    )


def _make_healthy_session(session_id="healthy"):
    """A session with good metrics."""
    events = [
        _make_event(duration_ms=100, tokens_in=300, tokens_out=100),
        _make_event(duration_ms=120, tokens_in=350, tokens_out=120),
        _make_event(duration_ms=90, tokens_in=280, tokens_out=90),
        _make_event(duration_ms=110, tokens_in=320, tokens_out=110),
        _make_event(duration_ms=105, tokens_in=310, tokens_out=105),
    ]
    return _make_session(session_id=session_id, events=events)


def _make_failing_session(session_id="failing"):
    """A session with many errors and high latency."""
    tc_fail = SimpleNamespace(
        tool_name="web_search",
        tool_output={"error": "Connection timeout"},
    )
    events = [
        _make_event(event_type="error", duration_ms=5000, tokens_in=2000, tokens_out=500, tool_call=tc_fail),
        _make_event(event_type="error", duration_ms=6000, tokens_in=2500, tokens_out=600, tool_call=tc_fail),
        _make_event(event_type="error", duration_ms=4500, tokens_in=1800, tokens_out=400),
        _make_event(event_type="llm_call", duration_ms=3000, tokens_in=1500, tokens_out=500),
        _make_event(event_type="error", duration_ms=7000, tokens_in=3000, tokens_out=800),
    ]
    return _make_session(session_id=session_id, events=events)


# ── Basic Tests ─────────────────────────────────────────────────────


class TestSessionAutopsyInit:
    def test_default_config(self):
        autopsy = SessionAutopsy()
        assert autopsy.config.health_poor_threshold == 70.0
        assert autopsy.baseline_count == 0

    def test_custom_config(self):
        config = AutopsyConfig(
            health_poor_threshold=80.0,
            error_rate_concern=0.10,
        )
        autopsy = SessionAutopsy(config=config)
        assert autopsy.config.health_poor_threshold == 80.0
        assert autopsy.config.error_rate_concern == 0.10

    def test_add_baseline(self):
        autopsy = SessionAutopsy()
        for i in range(5):
            autopsy.add_baseline(_make_healthy_session(f"base-{i}"))
        assert autopsy.baseline_count == 5

    def test_clear_baseline(self):
        autopsy = SessionAutopsy()
        autopsy.add_baseline(_make_healthy_session())
        autopsy.clear_baseline()
        assert autopsy.baseline_count == 0

    def test_add_baseline_metrics(self):
        autopsy = SessionAutopsy()
        autopsy.add_baseline_metrics({
            "avg_latency_ms": 100.0,
            "error_rate": 0.01,
            "total_tokens": 2000.0,
        })
        assert autopsy.baseline_count == 1


# ── Investigation Tests ─────────────────────────────────────────────


class TestInvestigation:
    def test_healthy_session_low_priority(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_healthy_session())
        assert isinstance(report, AutopsyReport)
        assert report.priority in (IncidentPriority.P3, IncidentPriority.P4)
        assert report.health_score > 70
        assert len(report.engines_run) >= 4

    def test_failing_session_high_priority(self):
        autopsy = SessionAutopsy()
        # Add baselines for anomaly detection
        for i in range(5):
            autopsy.add_baseline(_make_healthy_session(f"base-{i}"))
        report = autopsy.investigate(_make_failing_session())
        assert report.priority in (IncidentPriority.P0, IncidentPriority.P1)
        assert report.health_score < 70
        assert report.anomaly_count > 0
        assert len(report.evidence) > 0
        assert len(report.hypotheses) > 0

    def test_evidence_collected(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_failing_session())
        assert len(report.evidence) >= 2
        sources = {e.source for e in report.evidence}
        assert len(sources) >= 2  # Multiple engines contributed

    def test_causal_links_generated(self):
        autopsy = SessionAutopsy()
        for i in range(5):
            autopsy.add_baseline(_make_healthy_session(f"base-{i}"))
        report = autopsy.investigate(_make_failing_session())
        # With tool failures + errors + latency, should have causal links
        if report.causal_links:
            link = report.causal_links[0]
            assert isinstance(link.relation, CausalRelation)
            assert link.explanation

    def test_hypotheses_ranked_by_confidence(self):
        autopsy = SessionAutopsy()
        for i in range(5):
            autopsy.add_baseline(_make_healthy_session(f"base-{i}"))
        report = autopsy.investigate(_make_failing_session())
        if len(report.hypotheses) >= 2:
            for i in range(len(report.hypotheses) - 1):
                assert report.hypotheses[i].confidence >= report.hypotheses[i + 1].confidence

    def test_playbook_generated(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_failing_session())
        assert len(report.playbook) >= 1
        for action in report.playbook:
            assert isinstance(action.effort, EffortLevel)
            assert action.description
            assert action.expected_impact

    def test_engines_all_run(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_healthy_session())
        expected = {"health_scoring", "anomaly_detection", "error_analysis",
                    "latency_analysis", "token_analysis", "tool_analysis"}
        assert expected == set(report.engines_run)

    def test_empty_session(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_session(events=[]))
        assert isinstance(report, AutopsyReport)
        assert report.session_id == "test-001"

    def test_session_id_propagated(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_session(session_id="my-session-42"))
        assert report.session_id == "my-session-42"


# ── Investigate Metrics Tests ───────────────────────────────────────


class TestInvestigateMetrics:
    def test_metrics_only_investigation(self):
        autopsy = SessionAutopsy()
        for i in range(5):
            autopsy.add_baseline_metrics({
                "avg_latency_ms": 100.0,
                "error_rate": 0.01,
                "total_tokens": 2000.0,
                "tokens_per_event": 400.0,
                "event_count": 5.0,
                "p95_latency_ms": 150.0,
                "tool_failure_rate": 0.0,
            })
        report = autopsy.investigate_metrics({
            "avg_latency_ms": 8000.0,
            "error_rate": 0.30,
            "total_tokens": 50000.0,
            "tokens_per_event": 10000.0,
            "event_count": 5.0,
            "p95_latency_ms": 12000.0,
            "tool_failure_rate": 0.5,
        }, session_id="metrics-only")
        assert report.session_id == "metrics-only"
        assert len(report.evidence) > 0
        assert report.anomaly_count > 0


# ── Serialization Tests ─────────────────────────────────────────────


class TestSerialization:
    def test_report_to_dict(self):
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_failing_session())
        d = report.to_dict()
        assert d["session_id"] == "failing"
        assert "priority" in d
        assert "priority_label" in d
        assert "summary" in d
        assert isinstance(d["evidence"], list)
        assert isinstance(d["hypotheses"], list)
        assert isinstance(d["playbook"], list)
        assert isinstance(d["engines_run"], list)

    def test_report_render(self):
        autopsy = SessionAutopsy()
        for i in range(5):
            autopsy.add_baseline(_make_healthy_session(f"base-{i}"))
        report = autopsy.investigate(_make_failing_session())
        text = report.render()
        assert "Session Autopsy" in text
        assert "failing" in text
        assert "Priority:" in text
        assert "Health Score:" in text

    def test_evidence_to_dict(self):
        e = Evidence(
            source=EvidenceSource.ANOMALY,
            title="Test finding",
            detail="Some detail",
            severity_weight=0.75,
            metric_name="test_metric",
            observed_value=100.0,
            expected_value=50.0,
            tags=["test"],
        )
        d = e.to_dict()
        assert d["source"] == "anomaly_detection"
        assert d["title"] == "Test finding"
        assert d["severity_weight"] == 0.75

    def test_hypothesis_to_dict(self):
        h = Hypothesis(
            title="Test Hypothesis",
            explanation="Testing",
            confidence=0.85,
            category="test",
        )
        d = h.to_dict()
        assert d["confidence"] == 0.85
        assert d["evidence_count"] == 0

    def test_remediation_to_dict(self):
        a = RemediationAction(
            description="Fix it",
            effort=EffortLevel.QUICK_FIX,
            priority=1,
            addresses=["test"],
            expected_impact="Better performance",
            category="infra",
        )
        d = a.to_dict()
        assert d["effort"] == "quick_fix"
        assert d["priority"] == 1


# ── Priority Assessment Tests ───────────────────────────────────────


class TestPriorityAssessment:
    def test_p0_critical(self):
        """Very poor health triggers P0."""
        autopsy = SessionAutopsy()
        # Create a session with 100% error rate
        events = [
            _make_event(event_type="error", duration_ms=10000, tokens_in=5000, tokens_out=2000),
            _make_event(event_type="error", duration_ms=12000, tokens_in=6000, tokens_out=2500),
            _make_event(event_type="error", duration_ms=8000, tokens_in=4000, tokens_out=1500),
        ]
        session = _make_session(session_id="critical", events=events)
        report = autopsy.investigate(session)
        assert report.priority == IncidentPriority.P0

    def test_p4_clean(self):
        """Clean session gets P4."""
        autopsy = SessionAutopsy()
        report = autopsy.investigate(_make_healthy_session())
        assert report.priority == IncidentPriority.P4


# ── Hypothesis Category Tests ───────────────────────────────────────


class TestHypothesisCategories:
    def test_tool_failure_hypothesis(self):
        """Tool failures generate tool_failure hypothesis."""
        autopsy = SessionAutopsy()
        tc_fail = SimpleNamespace(
            tool_name="database_query",
            tool_output={"error": "Connection refused"},
        )
        events = [
            _make_event(event_type="error", duration_ms=200, tokens_in=500, tokens_out=100, tool_call=tc_fail),
            _make_event(event_type="error", duration_ms=250, tokens_in=500, tokens_out=100, tool_call=tc_fail),
            _make_event(event_type="llm_call", duration_ms=100, tokens_in=400, tokens_out=80),
        ]
        session = _make_session(events=events)
        report = autopsy.investigate(session)
        categories = {h.category for h in report.hypotheses}
        assert "tool_failure" in categories

    def test_latency_hypothesis(self):
        """High latency without tool failures generates capacity hypothesis."""
        autopsy = SessionAutopsy()
        events = [
            _make_event(duration_ms=5000, tokens_in=500, tokens_out=200),
            _make_event(duration_ms=8000, tokens_in=500, tokens_out=200),
            _make_event(duration_ms=3000, tokens_in=500, tokens_out=200),
            _make_event(duration_ms=10000, tokens_in=500, tokens_out=200),
        ]
        session = _make_session(events=events)
        report = autopsy.investigate(session)
        categories = {h.category for h in report.hypotheses}
        # Should have capacity or model_issue
        assert categories & {"capacity", "model_issue"}


# ── Causal Link Tests ───────────────────────────────────────────────


class TestCausalLinks:
    def test_tool_to_error_link(self):
        """Tool failures should link to error rate."""
        autopsy = SessionAutopsy()
        tc_fail = SimpleNamespace(
            tool_name="api_call",
            tool_output={"error": "500 Internal Server Error"},
        )
        events = [
            _make_event(event_type="error", tokens_in=500, tokens_out=100, tool_call=tc_fail),
            _make_event(event_type="error", tokens_in=500, tokens_out=100, tool_call=tc_fail),
            _make_event(event_type="error", tokens_in=500, tokens_out=100, tool_call=tc_fail),
        ]
        session = _make_session(events=events)
        report = autopsy.investigate(session)
        if report.causal_links:
            relations = {l.relation for l in report.causal_links}
            assert CausalRelation.CAUSES in relations


# ── Report Properties Tests ─────────────────────────────────────────


class TestReportProperties:
    def test_hypothesis_count(self):
        report = AutopsyReport(
            session_id="test",
            priority=IncidentPriority.P3,
            summary="test",
            hypotheses=[
                Hypothesis(title="H1", explanation="E1", confidence=0.8),
                Hypothesis(title="H2", explanation="E2", confidence=0.6),
            ],
        )
        assert report.hypothesis_count == 2

    def test_top_hypothesis(self):
        report = AutopsyReport(
            session_id="test",
            priority=IncidentPriority.P3,
            summary="test",
            hypotheses=[
                Hypothesis(title="H1", explanation="E1", confidence=0.6),
                Hypothesis(title="H2", explanation="E2", confidence=0.9),
            ],
        )
        assert report.top_hypothesis is not None
        assert report.top_hypothesis.title == "H2"

    def test_top_hypothesis_none_when_empty(self):
        report = AutopsyReport(
            session_id="test",
            priority=IncidentPriority.P4,
            summary="test",
        )
        assert report.top_hypothesis is None

    def test_action_count(self):
        report = AutopsyReport(
            session_id="test",
            priority=IncidentPriority.P3,
            summary="test",
            playbook=[
                RemediationAction("A", EffortLevel.QUICK_FIX, 1, [], "impact"),
                RemediationAction("B", EffortLevel.SMALL, 2, [], "impact"),
            ],
        )
        assert report.action_count == 2


# ── Hypothesis Properties Tests ─────────────────────────────────────


class TestHypothesisProperties:
    def test_evidence_count(self):
        h = Hypothesis(
            title="T", explanation="E", confidence=0.5,
            supporting_evidence=[
                Evidence(EvidenceSource.ANOMALY, "E1", "D", 0.5),
                Evidence(EvidenceSource.ERROR, "E2", "D", 0.7),
            ],
        )
        assert h.evidence_count == 2

    def test_avg_severity(self):
        h = Hypothesis(
            title="T", explanation="E", confidence=0.5,
            supporting_evidence=[
                Evidence(EvidenceSource.ANOMALY, "E1", "D", 0.4),
                Evidence(EvidenceSource.ERROR, "E2", "D", 0.8),
            ],
        )
        assert abs(h.avg_severity - 0.6) < 0.001

    def test_avg_severity_empty(self):
        h = Hypothesis(title="T", explanation="E", confidence=0.5)
        assert h.avg_severity == 0.0


# ── Enum Tests ──────────────────────────────────────────────────────


class TestEnums:
    def test_priority_labels(self):
        assert IncidentPriority.P0.label == "Critical"
        assert IncidentPriority.P1.label == "Major"
        assert IncidentPriority.P2.label == "Moderate"
        assert IncidentPriority.P3.label == "Minor"
        assert IncidentPriority.P4.label == "Informational"

    def test_evidence_source_values(self):
        assert EvidenceSource.ANOMALY.value == "anomaly_detection"
        assert EvidenceSource.HEALTH.value == "health_scoring"

    def test_effort_levels(self):
        assert EffortLevel.QUICK_FIX.value == "quick_fix"
        assert EffortLevel.LARGE.value == "large"

    def test_causal_relations(self):
        assert CausalRelation.CAUSES.value == "causes"
        assert CausalRelation.SYMPTOM_OF.value == "symptom_of"
