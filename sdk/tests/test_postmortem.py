"""Tests for the Incident Postmortem Generator."""

import pytest
from datetime import datetime, timezone, timedelta

from agentlens.postmortem import (
    PostmortemGenerator,
    PostmortemConfig,
    PostmortemReport,
    Severity,
    IncidentPhase,
    RemediationCategory,
    RootCause,
    ImpactAssessment,
    Remediation,
    LessonLearned,
    TimelineEntry,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _ts(offset_ms: int = 0) -> str:
    """Generate ISO timestamp with millisecond offset from a base time."""
    base = datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc)
    dt = base + timedelta(milliseconds=offset_ms)
    return dt.isoformat()


def _event(event_type: str = "llm_call", offset_ms: int = 0, **kwargs) -> dict:
    """Create a minimal event dict."""
    e = {"event_type": event_type, "timestamp": _ts(offset_ms), "event_id": f"evt-{offset_ms}"}
    e.update(kwargs)
    return e


def _tool_error(tool_name: str, offset_ms: int, msg: str = "failed") -> dict:
    """Create a tool error event."""
    return _event(
        "tool_error", offset_ms,
        tool_call={"tool_name": tool_name},
        error_message=msg,
    )


def _make_session_with_errors(n_ok: int = 5, n_errors: int = 3) -> list[dict]:
    """Create a session with some OK events and some errors."""
    events = []
    for i in range(n_ok):
        events.append(_event("llm_call", i * 1000, model="gpt-4", tokens_in=100, tokens_out=50))
    for i in range(n_errors):
        events.append(_tool_error("search_api", (n_ok + i) * 1000, "connection refused"))
    events.append(_event("llm_call", (n_ok + n_errors) * 1000))  # recovery
    return events


# ── Basic generation ─────────────────────────────────────────────────


class TestBasicGeneration:
    def test_empty_events(self):
        gen = PostmortemGenerator()
        report = gen.generate([])
        assert report.incident_id == "INC-NONE"
        assert report.severity == Severity.SEV4

    def test_single_event_below_min(self):
        gen = PostmortemGenerator()
        report = gen.generate([_event("error", 0)])
        assert report.incident_id == "INC-NONE"

    def test_no_errors_returns_empty(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", i * 1000) for i in range(5)]
        report = gen.generate(events)
        assert report.incident_id == "INC-NONE"
        assert "No errors" in report.summary

    def test_basic_postmortem(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events, session_id="test-session")
        assert report.incident_id.startswith("INC-")
        assert report.incident_id != "INC-NONE"
        assert report.session_id == "test-session"
        assert report.event_count == len(events)
        assert report.severity in (Severity.SEV1, Severity.SEV2, Severity.SEV3, Severity.SEV4)

    def test_generated_at_is_set(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events)
        assert report.generated_at != ""

    def test_duration_calculated(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(5, 3)
        report = gen.generate(events)
        assert report.duration_ms > 0


# ── Severity classification ──────────────────────────────────────────


class TestSeverity:
    def test_sev1_high_error_rate(self):
        gen = PostmortemGenerator()
        # 6 errors out of 8 events = 75%
        events = [_event("error", i * 100) for i in range(6)]
        events += [_event("llm_call", 700), _event("llm_call", 800)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV1

    def test_sev2_moderate_error_rate(self):
        gen = PostmortemGenerator()
        # 3 errors out of 10 = 30%
        events = [_event("llm_call", i * 100) for i in range(7)]
        events += [_event("error", 800 + i * 100) for i in range(3)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV2

    def test_sev3_low_error_rate(self):
        gen = PostmortemGenerator()
        # 2 errors out of 10 = 20%
        events = [_event("llm_call", i * 100) for i in range(8)]
        events += [_event("error", 900), _event("error", 1000)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV3

    def test_sev4_minimal_errors(self):
        gen = PostmortemGenerator()
        # 2 errors out of 30 = 6.7%
        events = [_event("llm_call", i * 100) for i in range(28)]
        events += [_event("error", 2900), _event("error", 3000)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV4

    def test_custom_severity_thresholds(self):
        config = PostmortemConfig(sev1_error_rate=0.90, sev2_error_rate=0.70)
        gen = PostmortemGenerator(config)
        # 6/8 = 75% → would be SEV-1 by default, but SEV-2 with custom thresholds
        events = [_event("error", i * 100) for i in range(6)]
        events += [_event("llm_call", 700), _event("llm_call", 800)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV2


# ── Timeline ─────────────────────────────────────────────────────────


class TestTimeline:
    def test_timeline_includes_first_and_last(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events)
        assert len(report.timeline) >= 2
        # First entry should be earliest
        assert report.timeline[0].elapsed_ms == 0

    def test_timeline_includes_errors(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(5, 3)
        report = gen.generate(events)
        error_entries = [e for e in report.timeline if e.severity == "error"]
        assert len(error_entries) == 3

    def test_timeline_includes_slow_events(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", 0, duration_ms=15000)]
        events.append(_event("error", 1000))
        events.append(_event("llm_call", 2000))
        report = gen.generate(events)
        slow_entries = [e for e in report.timeline if e.severity == "warning"]
        assert len(slow_entries) >= 1

    def test_timeline_phases(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(3, 3)
        report = gen.generate(events)
        phases = {e.phase for e in report.timeline}
        # Should have at least detection
        assert IncidentPhase.DETECTION in phases

    def test_event_descriptions(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0, model="gpt-4"),
            _tool_error("search_api", 1000, "connection refused"),
            _event("timeout", 2000, duration_ms=35000),
            _event("rate_limit", 3000),
            _event("llm_call", 4000),
        ]
        report = gen.generate(events)
        descriptions = [e.description for e in report.timeline]
        desc_text = " ".join(descriptions)
        assert "connection refused" in desc_text
        assert "Timeout" in desc_text or "timeout" in desc_text


# ── Root cause analysis ──────────────────────────────────────────────


class TestRootCauseAnalysis:
    def test_tool_failure_detected(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _tool_error("search_api", 1000),
            _tool_error("search_api", 2000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        tool_causes = [rc for rc in report.root_causes if rc.category == "tool_failure"]
        assert len(tool_causes) == 1
        assert "search_api" in tool_causes[0].description

    def test_model_error_detected(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, model="gpt-4"),
            _event("error", 2000, model="gpt-4"),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        model_causes = [rc for rc in report.root_causes if rc.category == "model_error"]
        assert len(model_causes) == 1
        assert "gpt-4" in model_causes[0].description

    def test_cascading_failure_detection(self):
        gen = PostmortemGenerator()
        # Errors at decreasing intervals: 0, 500, 700ms (intervals 500, 200 = decreasing)
        events = [
            _event("llm_call", 0),
            _event("error", 1000),
            _event("error", 1500),  # 500ms after first error
            _event("error", 1700),  # 200ms after second error (accelerating)
            _event("llm_call", 2000),
        ]
        report = gen.generate(events)
        cascade = [rc for rc in report.root_causes if rc.category == "cascading_failure"]
        assert len(cascade) == 1

    def test_timeout_detected(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("timeout", 1000, duration_ms=35000),
            _event("timeout", 2000, duration_ms=40000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        timeout_causes = [rc for rc in report.root_causes if rc.category == "timeout"]
        assert len(timeout_causes) == 1

    def test_rate_limit_detected(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("rate_limit", 1000),
            _event("rate_limit", 2000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        rl_causes = [rc for rc in report.root_causes if rc.category == "rate_limit"]
        assert len(rl_causes) == 1
        assert rl_causes[0].confidence == 0.9

    def test_repeated_error_detected(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, error_message="connection reset"),
            _event("error", 2000, error_message="connection reset"),
            _event("error", 3000, error_message="connection reset"),
            _event("llm_call", 4000),
        ]
        report = gen.generate(events)
        repeated = [rc for rc in report.root_causes if rc.category == "repeated_error"]
        assert len(repeated) == 1
        assert repeated[0].affected_events == 3

    def test_root_causes_sorted_by_confidence(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("rate_limit", 1000),
            _tool_error("api", 2000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        if len(report.root_causes) >= 2:
            for i in range(len(report.root_causes) - 1):
                assert report.root_causes[i].confidence >= report.root_causes[i + 1].confidence


# ── Impact assessment ────────────────────────────────────────────────


class TestImpactAssessment:
    def test_error_rate(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(5, 3)
        report = gen.generate(events)
        assert report.impact.error_count == 3
        assert report.impact.total_events == 9  # 5 + 3 + 1 recovery
        assert 0.3 <= report.impact.error_rate <= 0.4

    def test_affected_tools(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _tool_error("api_a", 1000),
            _tool_error("api_b", 2000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        assert "api_a" in report.impact.affected_tools
        assert "api_b" in report.impact.affected_tools

    def test_tokens_wasted(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, tokens_in=500, tokens_out=200),
            _event("error", 2000, tokens_in=300, tokens_out=100),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        assert report.impact.tokens_wasted == 1100  # 500+200+300+100

    def test_cost_estimation(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, tokens_in=1000, tokens_out=500),
            _event("llm_call", 2000),
        ]
        report = gen.generate(events)
        # 1000/1000 * 0.003 + 500/1000 * 0.015 = 0.003 + 0.0075 = 0.0105
        assert abs(report.impact.estimated_cost_impact - 0.0105) < 0.001

    def test_user_facing_flag(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000),
            _event("llm_call", 2000),
        ]
        report = gen.generate(events)
        assert report.impact.user_facing is True

    def test_downtime_estimation(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, duration_ms=5000),
            _event("error", 2000, duration_ms=3000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        assert report.impact.downtime_ms == 8000


# ── Remediations ─────────────────────────────────────────────────────


class TestRemediations:
    def test_tool_failure_remediation(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", 0), _tool_error("api", 1000), _tool_error("api", 2000)]
        report = gen.generate(events)
        actions = [r.action for r in report.remediations]
        assert any("retry" in a.lower() for a in actions)

    def test_remediation_priorities(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events)
        if len(report.remediations) >= 2:
            priorities = [r.priority for r in report.remediations]
            # P1 should exist
            assert 1 in priorities

    def test_high_severity_adds_health_check(self):
        gen = PostmortemGenerator()
        # Many errors for SEV-1
        events = [_event("error", i * 100) for i in range(8)]
        events += [_event("llm_call", 900), _event("llm_call", 1000)]
        report = gen.generate(events)
        actions = [r.action.lower() for r in report.remediations]
        assert any("health check" in a for a in actions)

    def test_token_waste_remediation(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", 0)]
        events += [_event("error", i * 100 + 100, tokens_in=5000, tokens_out=2000) for i in range(3)]
        report = gen.generate(events)
        actions = [r.action.lower() for r in report.remediations]
        assert any("token" in a and "budget" in a for a in actions)


# ── Lessons learned ──────────────────────────────────────────────────


class TestLessonsLearned:
    def test_tool_failure_lesson(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", 0), _tool_error("api", 1000), _tool_error("api", 2000)]
        report = gen.generate(events)
        categories = [ll.category for ll in report.lessons_learned]
        assert "architecture" in categories

    def test_sev1_detection_lesson(self):
        gen = PostmortemGenerator()
        events = [_event("error", i * 100) for i in range(8)]
        events += [_event("llm_call", 900), _event("llm_call", 1000)]
        report = gen.generate(events)
        lessons = [ll.lesson.lower() for ll in report.lessons_learned]
        assert any("detection" in l for l in lessons)


# ── Contributing factors & what went well ────────────────────────────


class TestContributingFactors:
    def test_high_error_density_factor(self):
        gen = PostmortemGenerator()
        events = [_event("error", i * 100) for i in range(6)]
        events += [_event("llm_call", 700)]
        report = gen.generate(events)
        assert any("error density" in f.lower() for f in report.contributing_factors)

    def test_no_retry_factor(self):
        gen = PostmortemGenerator()
        events = [_event("llm_call", 0), _event("error", 1000), _event("error", 2000), _event("llm_call", 3000)]
        report = gen.generate(events)
        assert any("retry" in f.lower() for f in report.contributing_factors)


class TestWhatWentWell:
    def test_successful_events_noted(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(5, 2)
        report = gen.generate(events)
        assert any("successfully" in w.lower() for w in report.what_went_well)

    def test_session_recovery_noted(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors(3, 2)  # last event is recovery
        report = gen.generate(events)
        assert any("recovered" in w.lower() for w in report.what_went_well)

    def test_single_error_type_noted(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000),
            _event("error", 2000),
            _event("llm_call", 3000),
        ]
        report = gen.generate(events)
        assert any("isolated" in w.lower() for w in report.what_went_well)


# ── Serialization ────────────────────────────────────────────────────


class TestSerialization:
    def test_to_dict(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events, session_id="dict-test")
        d = report.to_dict()
        assert d["incident_id"].startswith("INC-")
        assert d["session_id"] == "dict-test"
        assert isinstance(d["timeline"], list)
        assert isinstance(d["root_causes"], list)
        assert isinstance(d["impact"], dict)
        assert isinstance(d["remediations"], list)
        assert d["severity"] in ("SEV-1", "SEV-2", "SEV-3", "SEV-4")

    def test_to_dict_impact_fields(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        d = gen.generate(events).to_dict()
        impact = d["impact"]
        assert "error_count" in impact
        assert "error_rate" in impact
        assert "affected_tools" in impact
        assert "tokens_wasted" in impact
        assert "user_facing" in impact

    def test_to_markdown(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        report = gen.generate(events, session_id="md-test")
        md = report.to_markdown()
        assert "# Incident Postmortem" in md
        assert "## Summary" in md
        assert "## Timeline" in md
        assert "## Root Cause Analysis" in md
        assert "## Impact Assessment" in md
        assert "## Remediation Actions" in md
        assert "md-test" in md

    def test_to_markdown_table(self):
        gen = PostmortemGenerator()
        events = _make_session_with_errors()
        md = gen.generate(events).to_markdown()
        # Timeline table
        assert "| Time (ms) |" in md
        # Remediation table
        assert "| Priority |" in md

    def test_empty_report_to_dict(self):
        gen = PostmortemGenerator()
        d = gen.generate([]).to_dict()
        assert d["incident_id"] == "INC-NONE"
        assert d["impact"]["error_count"] == 0

    def test_empty_report_to_markdown(self):
        gen = PostmortemGenerator()
        md = gen.generate([]).to_markdown()
        assert "No incident detected" in md


# ── Configuration ────────────────────────────────────────────────────


class TestConfiguration:
    def test_custom_error_types(self):
        config = PostmortemConfig(error_types=("custom_error",))
        gen = PostmortemGenerator(config)
        events = [
            _event("llm_call", 0),
            _event("custom_error", 1000),
            _event("custom_error", 2000),
        ]
        report = gen.generate(events)
        assert report.impact.error_count == 2

    def test_custom_cost_rates(self):
        config = PostmortemConfig(cost_per_1k_input=0.01, cost_per_1k_output=0.03)
        gen = PostmortemGenerator(config)
        events = [
            _event("llm_call", 0),
            _event("error", 1000, tokens_in=1000, tokens_out=1000),
            _event("llm_call", 2000),
        ]
        report = gen.generate(events)
        # 1000/1000 * 0.01 + 1000/1000 * 0.03 = 0.04
        assert abs(report.impact.estimated_cost_impact - 0.04) < 0.001

    def test_min_events_config(self):
        config = PostmortemConfig(min_events=5)
        gen = PostmortemGenerator(config)
        events = [_event("error", 0), _event("error", 100), _event("llm_call", 200)]
        report = gen.generate(events)
        assert report.incident_id == "INC-NONE"


# ── Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_errors(self):
        gen = PostmortemGenerator()
        events = [_event("error", i * 100) for i in range(5)]
        report = gen.generate(events)
        assert report.severity == Severity.SEV1
        assert report.impact.error_rate == 1.0

    def test_z_suffix_timestamps(self):
        gen = PostmortemGenerator()
        events = [
            {"event_type": "llm_call", "timestamp": "2026-03-05T12:00:00Z", "event_id": "e1"},
            {"event_type": "error", "timestamp": "2026-03-05T12:00:01Z", "event_id": "e2"},
            {"event_type": "llm_call", "timestamp": "2026-03-05T12:00:02Z", "event_id": "e3"},
        ]
        report = gen.generate(events)
        assert report.duration_ms == 2000

    def test_missing_optional_fields(self):
        gen = PostmortemGenerator()
        events = [
            {"event_type": "llm_call", "timestamp": _ts(0)},
            {"event_type": "error", "timestamp": _ts(1000)},
            {"event_type": "llm_call", "timestamp": _ts(2000)},
        ]
        # Should not raise
        report = gen.generate(events)
        assert report.impact.error_count == 1

    def test_output_data_error_extraction(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000, output_data={"error": "connection timeout"}),
            _event("error", 2000, output_data={"error": "connection timeout"}),
        ]
        report = gen.generate(events)
        repeated = [rc for rc in report.root_causes if rc.category == "repeated_error"]
        assert len(repeated) == 1

    def test_multiple_tool_failures(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _tool_error("api_a", 1000),
            _tool_error("api_a", 2000),
            _tool_error("api_b", 3000),
            _event("llm_call", 4000),
        ]
        report = gen.generate(events)
        # Should identify most common tool
        tool_causes = [rc for rc in report.root_causes if rc.category == "tool_failure"]
        assert tool_causes[0].description.find("api_a") >= 0

    def test_mixed_error_types(self):
        gen = PostmortemGenerator()
        events = [
            _event("llm_call", 0),
            _event("error", 1000),
            _event("timeout", 2000, duration_ms=35000),
            _event("rate_limit", 3000),
            _tool_error("api", 4000),
            _event("llm_call", 5000),
        ]
        report = gen.generate(events)
        categories = {rc.category for rc in report.root_causes}
        # Should detect multiple root cause categories
        assert len(categories) >= 2
