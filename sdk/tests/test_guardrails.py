"""Tests for the guardrails module."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.guardrails import (
    Guardrails,
    GuardrailSuite,
    Severity,
    SuiteReport,
    ValidationResult,
    Violation,
)
from agentlens.models import AgentEvent, Session, ToolCall


def _utcnow():
    return datetime.now(timezone.utc)


def _make_session(**kwargs):
    defaults = dict(session_id="test-sess", agent_name="test-agent", started_at=_utcnow())
    defaults.update(kwargs)
    return Session(**defaults)


def _make_event(**kwargs):
    defaults = dict(event_type="llm_call", tokens_in=100, tokens_out=50)
    defaults.update(kwargs)
    return AgentEvent(**defaults)


def _tool_event(tool_name, **kwargs):
    return _make_event(
        event_type="tool_call",
        tool_call=ToolCall(tool_name=tool_name),
        **kwargs,
    )


# ── Token limits ─────────────────────────────────────────────────────


class TestTokenLimits:
    def test_max_tokens_in_pass(self):
        s = _make_session(total_tokens_in=500)
        r = Guardrails("t").max_tokens_in(1000).validate(s)
        assert r.passed

    def test_max_tokens_in_fail(self):
        s = _make_session(total_tokens_in=1500)
        r = Guardrails("t").max_tokens_in(1000).validate(s)
        assert not r.passed
        assert r.violations[0].rule == "max_tokens_in"

    def test_max_tokens_out_fail(self):
        s = _make_session(total_tokens_out=2000)
        r = Guardrails("t").max_tokens_out(1000).validate(s)
        assert not r.passed

    def test_max_total_tokens_pass(self):
        s = _make_session(total_tokens_in=300, total_tokens_out=200)
        r = Guardrails("t").max_total_tokens(1000).validate(s)
        assert r.passed

    def test_max_total_tokens_fail(self):
        s = _make_session(total_tokens_in=600, total_tokens_out=500)
        r = Guardrails("t").max_total_tokens(1000).validate(s)
        assert not r.passed
        assert r.violations[0].actual == 1100


# ── Duration ─────────────────────────────────────────────────────────


class TestDuration:
    def test_max_duration_pass(self):
        now = _utcnow()
        s = _make_session(started_at=now, ended_at=now + timedelta(seconds=5))
        r = Guardrails("t").max_duration_ms(10_000).validate(s)
        assert r.passed

    def test_max_duration_fail(self):
        now = _utcnow()
        s = _make_session(started_at=now, ended_at=now + timedelta(seconds=15))
        r = Guardrails("t").max_duration_ms(10_000).validate(s)
        assert not r.passed

    def test_max_duration_no_end(self):
        s = _make_session(started_at=_utcnow())
        r = Guardrails("t").max_duration_ms(10_000).validate(s)
        assert r.passed  # No end time → no violation


# ── Tool constraints ─────────────────────────────────────────────────


class TestToolConstraints:
    def test_require_tools_pass(self):
        s = _make_session()
        s.add_event(_tool_event("safety_check"))
        r = Guardrails("t").require_tools(["safety_check"]).validate(s)
        assert r.passed

    def test_require_tools_fail(self):
        s = _make_session()
        s.add_event(_tool_event("web_search"))
        r = Guardrails("t").require_tools(["safety_check"]).validate(s)
        assert not r.passed
        assert "safety_check" in r.violations[0].message

    def test_forbid_tools_pass(self):
        s = _make_session()
        s.add_event(_tool_event("web_search"))
        r = Guardrails("t").forbid_tools(["rm_rf"]).validate(s)
        assert r.passed

    def test_forbid_tools_fail(self):
        s = _make_session()
        s.add_event(_tool_event("rm_rf"))
        r = Guardrails("t").forbid_tools(["rm_rf"]).validate(s)
        assert not r.passed

    def test_allow_tools_only_pass(self):
        s = _make_session()
        s.add_event(_tool_event("search"))
        s.add_event(_tool_event("read"))
        r = Guardrails("t").allow_tools_only(["search", "read", "write"]).validate(s)
        assert r.passed

    def test_allow_tools_only_fail(self):
        s = _make_session()
        s.add_event(_tool_event("search"))
        s.add_event(_tool_event("exec"))
        r = Guardrails("t").allow_tools_only(["search", "read"]).validate(s)
        assert not r.passed
        assert any("exec" in v.message for v in r.violations)


# ── Model constraints ────────────────────────────────────────────────


class TestModelConstraints:
    def test_allow_models_pass(self):
        s = _make_session()
        s.add_event(_make_event(model="gpt-4o"))
        r = Guardrails("t").allow_models(["gpt-4o", "gpt-4o-mini"]).validate(s)
        assert r.passed

    def test_allow_models_fail(self):
        s = _make_session()
        s.add_event(_make_event(model="gpt-3.5-turbo"))
        r = Guardrails("t").allow_models(["gpt-4o"]).validate(s)
        assert not r.passed


# ── Event counts ─────────────────────────────────────────────────────


class TestEventCounts:
    def test_max_events_pass(self):
        s = _make_session()
        for _ in range(5):
            s.add_event(_make_event())
        r = Guardrails("t").max_events(10).validate(s)
        assert r.passed

    def test_max_events_fail(self):
        s = _make_session()
        for _ in range(15):
            s.add_event(_make_event())
        r = Guardrails("t").max_events(10).validate(s)
        assert not r.passed

    def test_min_events_pass(self):
        s = _make_session()
        for _ in range(3):
            s.add_event(_make_event())
        r = Guardrails("t").min_events(2).validate(s)
        assert r.passed

    def test_min_events_fail(self):
        s = _make_session()
        r = Guardrails("t").min_events(1).validate(s)
        assert not r.passed


# ── Error threshold ──────────────────────────────────────────────────


class TestErrors:
    def test_max_errors_pass(self):
        s = _make_session()
        s.add_event(_make_event(event_type="error"))
        r = Guardrails("t").max_errors(1).validate(s)
        assert r.passed

    def test_max_errors_fail(self):
        s = _make_session()
        s.add_event(_make_event(event_type="error"))
        s.add_event(_make_event(event_type="error"))
        r = Guardrails("t").max_errors(1).validate(s)
        assert not r.passed


# ── Custom rules ─────────────────────────────────────────────────────


class TestCustomRules:
    def test_custom_pass(self):
        s = _make_session(agent_name="good-agent")
        r = (Guardrails("t")
             .add_rule("agent_name", lambda s: s.agent_name == "good-agent")
             .validate(s))
        assert r.passed

    def test_custom_fail(self):
        s = _make_session(agent_name="bad-agent")
        r = (Guardrails("t")
             .add_rule("agent_name", lambda s: s.agent_name == "good-agent",
                       message="Wrong agent")
             .validate(s))
        assert not r.passed
        assert r.violations[0].message == "Wrong agent"


# ── Severity ─────────────────────────────────────────────────────────


class TestSeverity:
    def test_warning_still_passes(self):
        s = _make_session(total_tokens_in=2000)
        r = Guardrails("t").max_tokens_in(1000, severity=Severity.WARNING).validate(s)
        assert r.passed  # Warnings don't fail
        assert r.warning_count == 1

    def test_info_still_passes(self):
        s = _make_session(total_tokens_in=2000)
        r = Guardrails("t").max_tokens_in(1000, severity=Severity.INFO).validate(s)
        assert r.passed


# ── Chaining ─────────────────────────────────────────────────────────


class TestChaining:
    def test_multiple_rules(self):
        s = _make_session(total_tokens_in=500, total_tokens_out=200)
        s.add_event(_tool_event("search"))
        r = (Guardrails("multi")
             .max_total_tokens(1000)
             .max_events(10)
             .require_tools(["search"])
             .validate(s))
        assert r.passed

    def test_multiple_failures(self):
        s = _make_session(total_tokens_in=5000, total_tokens_out=5000)
        for _ in range(20):
            s.add_event(_make_event(event_type="error"))
        r = (Guardrails("strict")
             .max_total_tokens(1000)
             .max_errors(0)
             .max_events(10)
             .validate(s))
        assert not r.passed
        assert r.error_count == 3


# ── ValidationResult rendering ───────────────────────────────────────


class TestResultRendering:
    def test_summary(self):
        r = ValidationResult(guardrail_name="test", session_id="s1")
        assert "PASSED" in r.summary()

    def test_render_text(self):
        r = ValidationResult(guardrail_name="test", session_id="s1")
        r.violations.append(Violation(rule="r", message="bad", severity=Severity.ERROR))
        text = r.render_text()
        assert "FAILED" in text
        assert "bad" in text

    def test_to_dict(self):
        r = ValidationResult(guardrail_name="test", session_id="s1")
        d = r.to_dict()
        assert d["passed"] is True
        assert d["violations"] == []

    def test_to_json(self):
        r = ValidationResult(guardrail_name="test", session_id="s1")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            r.to_json(path)
            data = json.loads(open(path).read())
            assert data["passed"] is True
        finally:
            os.unlink(path)


# ── GuardrailSuite ───────────────────────────────────────────────────


class TestSuite:
    def test_suite_all_pass(self):
        s = _make_session(total_tokens_in=100)
        g1 = Guardrails("budget").max_total_tokens(1000)
        g2 = Guardrails("safety").max_errors(5)
        suite = GuardrailSuite([g1, g2])
        report = suite.validate(s)
        assert report.passed
        assert len(report.results) == 2

    def test_suite_partial_fail(self):
        s = _make_session(total_tokens_in=5000, total_tokens_out=5000)
        g1 = Guardrails("budget").max_total_tokens(1000)
        g2 = Guardrails("safety").max_errors(5)
        suite = GuardrailSuite([g1, g2])
        report = suite.validate(s)
        assert not report.passed
        assert report.total_errors == 1

    def test_suite_add(self):
        suite = GuardrailSuite()
        suite.add(Guardrails("a")).add(Guardrails("b"))
        s = _make_session()
        report = suite.validate(s)
        assert len(report.results) == 2

    def test_suite_render_text(self):
        s = _make_session()
        g = Guardrails("check").max_events(10)
        report = GuardrailSuite([g]).validate(s)
        text = report.render_text()
        assert "SUITE REPORT" in text

    def test_suite_summary(self):
        s = _make_session()
        report = GuardrailSuite([Guardrails("x")]).validate(s)
        assert "PASSED" in report.summary()

    def test_suite_to_json(self):
        s = _make_session()
        report = GuardrailSuite([Guardrails("x")]).validate(s)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            report.to_json(path)
            data = json.loads(open(path).read())
            assert data["passed"] is True
        finally:
            os.unlink(path)


# ── Violation str ────────────────────────────────────────────────────


class TestViolation:
    def test_str(self):
        v = Violation(rule="test", message="oops", severity=Severity.WARNING)
        assert "[WARNING]" in str(v)
        assert "oops" in str(v)
