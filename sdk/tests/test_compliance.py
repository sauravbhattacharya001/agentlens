"""Tests for ComplianceChecker — policy-based session validation."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from agentlens.models import AgentEvent, Session, ToolCall, DecisionTrace
from agentlens.compliance import (
    ComplianceChecker,
    CompliancePolicy,
    ComplianceReport,
    ComplianceRule,
    RuleKind,
    RuleResult,
    RuleVerdict,
    strict_policy,
    permissive_policy,
)


# ── Helpers ────────────────────────────────────────────────────

def make_session(
    tokens_in: int = 100,
    tokens_out: int = 50,
    events: list[AgentEvent] | None = None,
    ended: bool = True,
    duration_seconds: float = 10.0,
) -> Session:
    """Create a test session."""
    now = datetime.now(timezone.utc)
    s = Session(
        agent_name="test-agent",
        started_at=now - timedelta(seconds=duration_seconds),
        total_tokens_in=tokens_in,
        total_tokens_out=tokens_out,
    )
    if events:
        s.events = events
    if ended:
        s.ended_at = now
        s.status = "completed"
    return s


def make_event(
    event_type: str = "llm_call",
    model: str | None = "gpt-4o",
    tokens_in: int = 10,
    tokens_out: int = 5,
    tool_name: str | None = None,
    reasoning: str | None = None,
) -> AgentEvent:
    """Create a test event."""
    tool_call = None
    if tool_name:
        tool_call = ToolCall(tool_name=tool_name, tool_input={})

    decision = None
    if reasoning:
        decision = DecisionTrace(reasoning=reasoning, step=1)

    return AgentEvent(
        event_type=event_type,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_call=tool_call,
        decision_trace=decision,
    )


def make_policy(rules: list[dict]) -> CompliancePolicy:
    """Create a policy from rule dicts."""
    return CompliancePolicy.from_dict({"name": "test", "rules": rules})


# ── ComplianceRule ─────────────────────────────────────────────

class TestComplianceRule:
    def test_from_dict(self):
        rule = ComplianceRule.from_dict({
            "kind": "max_tokens",
            "limit": 1000,
            "severity": "warning",
        })
        assert rule.kind == "max_tokens"
        assert rule.limit == 1000
        assert rule.severity == "warning"

    def test_from_dict_defaults(self):
        rule = ComplianceRule.from_dict({"kind": "custom"})
        assert rule.severity == "error"
        assert rule.tools == []
        assert rule.models == []
        assert rule.limit is None

    def test_to_dict(self):
        rule = ComplianceRule(
            kind="forbidden_tools",
            tools=["rm", "shell"],
            severity="error",
        )
        d = rule.to_dict()
        assert d["kind"] == "forbidden_tools"
        assert d["tools"] == ["rm", "shell"]
        assert "severity" not in d  # default not included

    def test_to_dict_non_default_severity(self):
        rule = ComplianceRule(kind="max_tokens", limit=100, severity="warning")
        d = rule.to_dict()
        assert d["severity"] == "warning"


# ── CompliancePolicy ──────────────────────────────────────────

class TestCompliancePolicy:
    def test_from_dict(self):
        policy = CompliancePolicy.from_dict({
            "name": "prod",
            "description": "Production policy",
            "version": "2.0",
            "rules": [
                {"kind": "max_tokens", "limit": 5000},
                {"kind": "forbidden_tools", "tools": ["shell"]},
            ],
        })
        assert policy.name == "prod"
        assert policy.version == "2.0"
        assert len(policy.rules) == 2

    def test_from_json(self):
        j = '{"name": "test", "rules": [{"kind": "max_events", "limit": 10}]}'
        policy = CompliancePolicy.from_json(j)
        assert policy.name == "test"
        assert len(policy.rules) == 1

    def test_to_json_roundtrip(self):
        policy = make_policy([
            {"kind": "max_tokens", "limit": 1000},
            {"kind": "allowed_models", "models": ["gpt-4o"]},
        ])
        j = policy.to_json()
        restored = CompliancePolicy.from_json(j)
        assert restored.name == policy.name
        assert len(restored.rules) == 2

    def test_empty_rules(self):
        policy = CompliancePolicy(name="empty")
        assert policy.rules == []
        d = policy.to_dict()
        assert d["rules"] == []


# ── Token rules ───────────────────────────────────────────────

class TestTokenRules:
    def test_max_tokens_pass(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([{"kind": "max_tokens", "limit": 200}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant
        assert report.results[0].verdict == RuleVerdict.PASS

    def test_max_tokens_fail(self):
        session = make_session(tokens_in=100, tokens_out=150)
        policy = make_policy([{"kind": "max_tokens", "limit": 200}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert report.results[0].verdict == RuleVerdict.FAIL
        assert report.results[0].actual_value == 250

    def test_max_tokens_exact_limit(self):
        session = make_session(tokens_in=100, tokens_out=100)
        policy = make_policy([{"kind": "max_tokens", "limit": 200}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_min_tokens_pass(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([{"kind": "min_tokens", "limit": 100}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_min_tokens_fail(self):
        session = make_session(tokens_in=10, tokens_out=5)
        policy = make_policy([{"kind": "min_tokens", "limit": 100}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant


# ── Model rules ───────────────────────────────────────────────

class TestModelRules:
    def test_allowed_models_pass(self):
        events = [make_event(model="gpt-4o"), make_event(model="gpt-4o")]
        session = make_session(events=events)
        policy = make_policy([{"kind": "allowed_models", "models": ["gpt-4o"]}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_allowed_models_fail(self):
        events = [make_event(model="gpt-4o"), make_event(model="claude-3")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "allowed_models", "models": ["gpt-4o"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert "claude-3" in report.results[0].message

    def test_allowed_models_case_insensitive(self):
        events = [make_event(model="GPT-4O")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "allowed_models", "models": ["gpt-4o"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_allowed_models_no_events_skip(self):
        session = make_session(events=[])
        policy = make_policy([
            {"kind": "allowed_models", "models": ["gpt-4o"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP

    def test_forbidden_models_pass(self):
        events = [make_event(model="gpt-4o")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "forbidden_models", "models": ["claude-3"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_forbidden_models_fail(self):
        events = [make_event(model="claude-3")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "forbidden_models", "models": ["claude-3"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant


# ── Tool rules ────────────────────────────────────────────────

class TestToolRules:
    def test_required_tools_pass(self):
        events = [
            make_event(tool_name="safety_check"),
            make_event(tool_name="lookup"),
        ]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "required_tools", "tools": ["safety_check"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_required_tools_fail(self):
        events = [make_event(tool_name="lookup")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "required_tools", "tools": ["safety_check"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert "safety_check" in report.results[0].message

    def test_forbidden_tools_pass(self):
        events = [make_event(tool_name="search")]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "forbidden_tools", "tools": ["shell", "rm"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_forbidden_tools_fail(self):
        events = [
            make_event(tool_name="search"),
            make_event(tool_name="shell"),
        ]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "forbidden_tools", "tools": ["shell", "rm"]},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant

    def test_max_tool_calls_pass(self):
        events = [make_event(tool_name="a"), make_event(tool_name="b")]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_tool_calls", "limit": 5}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_max_tool_calls_fail(self):
        events = [make_event(tool_name=f"t{i}") for i in range(6)]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_tool_calls", "limit": 3}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert report.results[0].actual_value == 6


# ── Event count rules ─────────────────────────────────────────

class TestEventCountRules:
    def test_max_events_pass(self):
        events = [make_event() for _ in range(5)]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_events", "limit": 10}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_max_events_fail(self):
        events = [make_event() for _ in range(15)]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_events", "limit": 10}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant

    def test_min_events_pass(self):
        events = [make_event() for _ in range(5)]
        session = make_session(events=events)
        policy = make_policy([{"kind": "min_events", "limit": 3}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_min_events_fail(self):
        events = [make_event()]
        session = make_session(events=events)
        policy = make_policy([{"kind": "min_events", "limit": 5}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant


# ── Duration rule ─────────────────────────────────────────────

class TestDurationRule:
    def test_max_duration_pass(self):
        session = make_session(duration_seconds=5.0)
        policy = make_policy([{"kind": "max_duration_ms", "limit": 10000}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_max_duration_fail(self):
        session = make_session(duration_seconds=60.0)
        policy = make_policy([{"kind": "max_duration_ms", "limit": 10000}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant

    def test_max_duration_active_session_skip(self):
        session = make_session(ended=False)
        policy = make_policy([{"kind": "max_duration_ms", "limit": 10000}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP


# ── Reasoning rule ────────────────────────────────────────────

class TestReasoningRule:
    def test_require_reasoning_pass(self):
        events = [
            make_event(event_type="llm_call", reasoning="I chose this because..."),
            make_event(event_type="decision", reasoning="Analyzing options..."),
        ]
        session = make_session(events=events)
        policy = make_policy([{"kind": "require_reasoning", "threshold": 1.0}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_require_reasoning_fail(self):
        events = [
            make_event(event_type="llm_call", reasoning="I chose this"),
            make_event(event_type="llm_call"),  # no reasoning
        ]
        session = make_session(events=events)
        policy = make_policy([{"kind": "require_reasoning", "threshold": 1.0}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant

    def test_require_reasoning_partial_threshold(self):
        events = [
            make_event(event_type="llm_call", reasoning="Yes"),
            make_event(event_type="llm_call"),  # no reasoning
        ]
        session = make_session(events=events)
        policy = make_policy([
            {"kind": "require_reasoning", "threshold": 0.5},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_require_reasoning_no_decisions_skip(self):
        events = [make_event(event_type="tool_call", tool_name="search")]
        session = make_session(events=events)
        policy = make_policy([{"kind": "require_reasoning"}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP


# ── Error rate rule ───────────────────────────────────────────

class TestErrorRateRule:
    def test_max_error_rate_pass(self):
        events = [
            make_event(event_type="llm_call"),
            make_event(event_type="llm_call"),
            make_event(event_type="error"),
        ]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_error_rate", "threshold": 0.5}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant

    def test_max_error_rate_fail(self):
        events = [
            make_event(event_type="error"),
            make_event(event_type="error"),
            make_event(event_type="llm_call"),
        ]
        session = make_session(events=events)
        policy = make_policy([{"kind": "max_error_rate", "threshold": 0.5}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant

    def test_max_error_rate_no_events_skip(self):
        session = make_session(events=[])
        policy = make_policy([{"kind": "max_error_rate", "threshold": 0.1}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP


# ── Custom validators ─────────────────────────────────────────

class TestCustomValidators:
    def test_custom_validator(self):
        def check_metadata(session, rule):
            if session.metadata.get("environment") == "production":
                return RuleResult(
                    rule=rule, verdict=RuleVerdict.PASS,
                    message="Production session",
                )
            return RuleResult(
                rule=rule, verdict=RuleVerdict.FAIL,
                message="Not a production session",
            )

        session = make_session()
        session.metadata = {"environment": "production"}
        policy = make_policy([{"kind": "check_env"}])

        checker = ComplianceChecker()
        checker.register_validator("check_env", check_metadata)
        report = checker.check(session, policy)
        assert report.compliant

    def test_custom_validator_error_skips(self):
        def broken_validator(session, rule):
            raise ValueError("boom")

        session = make_session()
        policy = make_policy([{"kind": "broken"}])

        checker = ComplianceChecker()
        checker.register_validator("broken", broken_validator)
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP
        assert "error" in report.results[0].message.lower()

    def test_unknown_rule_skips(self):
        session = make_session()
        policy = make_policy([{"kind": "nonexistent_rule"}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.results[0].verdict == RuleVerdict.SKIP


# ── Report ────────────────────────────────────────────────────

class TestComplianceReport:
    def test_compliant_report(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([{"kind": "max_tokens", "limit": 1000}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant
        assert report.passed == 1
        assert report.failed == 0
        assert report.total_rules == 1

    def test_non_compliant_report(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([{"kind": "max_tokens", "limit": 100}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert report.failed == 1

    def test_warning_doesnt_break_compliance(self):
        session = make_session(tokens_in=200, tokens_out=200)
        policy = make_policy([
            {"kind": "max_tokens", "limit": 100, "severity": "warning"},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        # Warnings don't count as non-compliant
        assert report.compliant
        assert len(report.warnings) == 1
        assert len(report.errors) == 0

    def test_mixed_error_and_warning(self):
        session = make_session(tokens_in=200, tokens_out=200)
        policy = make_policy([
            {"kind": "max_tokens", "limit": 100, "severity": "error"},
            {"kind": "max_events", "limit": 1000, "severity": "warning"},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert report.failed == 1  # only the error
        assert report.passed == 1  # the warning rule passed

    def test_render(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([
            {"kind": "max_tokens", "limit": 200},
            {"kind": "max_events", "limit": 10},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        rendered = report.render()
        assert "Compliance Report" in rendered
        assert "test" in rendered  # policy name
        assert "COMPLIANT" in rendered

    def test_render_non_compliant(self):
        session = make_session(tokens_in=500, tokens_out=500)
        policy = make_policy([{"kind": "max_tokens", "limit": 100}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        rendered = report.render()
        assert "NON-COMPLIANT" in rendered
        assert "[X]" in rendered

    def test_to_json_valid(self):
        session = make_session(tokens_in=100, tokens_out=50)
        policy = make_policy([{"kind": "max_tokens", "limit": 200}])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        data = json.loads(report.to_json())
        assert "compliant" in data
        assert "results" in data
        assert data["compliant"] is True

    def test_to_dict_structure(self):
        session = make_session()
        policy = make_policy([
            {"kind": "max_tokens", "limit": 1000},
            {"kind": "max_events", "limit": 100},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        d = report.to_dict()
        assert d["policy_name"] == "test"
        assert d["passed"] == 2
        assert d["failed"] == 0
        assert len(d["results"]) == 2

    def test_skipped_count(self):
        session = make_session(events=[], ended=False)
        policy = make_policy([
            {"kind": "max_duration_ms", "limit": 10000},
            {"kind": "max_error_rate", "threshold": 0.1},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.skipped == 2
        assert report.compliant  # skipped rules don't break compliance


# ── Multiple sessions ─────────────────────────────────────────

class TestMultipleSessions:
    def test_check_multiple(self):
        sessions = [
            make_session(tokens_in=50, tokens_out=30),
            make_session(tokens_in=200, tokens_out=200),
        ]
        policy = make_policy([{"kind": "max_tokens", "limit": 100}])
        checker = ComplianceChecker()
        reports = checker.check_multiple(sessions, policy)
        assert len(reports) == 2
        assert reports[0].compliant
        assert not reports[1].compliant


# ── Multi-rule policies ──────────────────────────────────────

class TestMultiRulePolicies:
    def test_all_pass(self):
        events = [
            make_event(model="gpt-4o", tool_name="search",
                       reasoning="Searching for info"),
        ]
        session = make_session(
            tokens_in=100, tokens_out=50,
            events=events, duration_seconds=5.0,
        )
        policy = make_policy([
            {"kind": "max_tokens", "limit": 1000},
            {"kind": "allowed_models", "models": ["gpt-4o"]},
            {"kind": "forbidden_tools", "tools": ["shell"]},
            {"kind": "max_events", "limit": 10},
            {"kind": "max_duration_ms", "limit": 60000},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert report.compliant
        assert report.passed == 5

    def test_multiple_failures(self):
        events = [make_event(model="claude-3") for _ in range(20)]
        session = make_session(
            tokens_in=10000, tokens_out=10000, events=events,
        )
        policy = make_policy([
            {"kind": "max_tokens", "limit": 1000},
            {"kind": "allowed_models", "models": ["gpt-4o"]},
            {"kind": "max_events", "limit": 10},
        ])
        checker = ComplianceChecker()
        report = checker.check(session, policy)
        assert not report.compliant
        assert report.failed == 3


# ── Preset policies ──────────────────────────────────────────

class TestPresets:
    def test_strict_policy_exists(self):
        policy = strict_policy()
        assert policy.name == "strict"
        assert len(policy.rules) > 0

    def test_permissive_policy_exists(self):
        policy = permissive_policy()
        assert policy.name == "permissive"
        assert len(policy.rules) > 0

    def test_strict_vs_permissive_limits(self):
        s = strict_policy()
        p = permissive_policy()
        strict_token_rule = next(
            r for r in s.rules if r.kind == RuleKind.MAX_TOKENS
        )
        permissive_token_rule = next(
            r for r in p.rules if r.kind == RuleKind.MAX_TOKENS
        )
        assert permissive_token_rule.limit > strict_token_rule.limit

    def test_strict_policy_against_clean_session(self):
        events = [
            make_event(event_type="llm_call", model="gpt-4o",
                       reasoning="Thinking..."),
        ]
        session = make_session(
            tokens_in=100, tokens_out=50,
            events=events, duration_seconds=5.0,
        )
        checker = ComplianceChecker()
        report = checker.check(session, strict_policy())
        assert report.compliant

    def test_strict_policy_rejects_forbidden_tool(self):
        events = [
            make_event(event_type="llm_call", model="gpt-4o",
                       reasoning="Need to run code"),
            make_event(tool_name="execute_code"),
        ]
        session = make_session(events=events, duration_seconds=5.0)
        checker = ComplianceChecker()
        report = checker.check(session, strict_policy())
        assert not report.compliant
