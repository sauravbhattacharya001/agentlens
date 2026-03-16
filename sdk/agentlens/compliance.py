"""Compliance Checker — policy-based session validation.

Define compliance policies with rules (token limits, allowed/forbidden
tools, model allowlists, event count bounds, duration limits) and check
sessions against them. Produces structured pass/fail reports with
per-rule details.

Unlike alerts (threshold monitoring over time) or health scoring
(general quality grades), compliance checking is about **policy
enforcement**: "Does this session meet our organization's rules?"

Usage::

    from agentlens.compliance import CompliancePolicy, ComplianceChecker

    policy = CompliancePolicy(name="production-policy", rules=[
        {"kind": "max_tokens", "limit": 50000},
        {"kind": "forbidden_tools", "tools": ["execute_code", "rm"]},
        {"kind": "allowed_models", "models": ["gpt-4o", "claude-3.5-sonnet"]},
        {"kind": "max_events", "limit": 200},
        {"kind": "max_duration_ms", "limit": 300000},
        {"kind": "required_tools", "tools": ["safety_check"]},
    ])

    checker = ComplianceChecker()
    report = checker.check(session, policy)

    print(report.render())
    print(f"Compliant: {report.compliant}")
    print(f"Passed: {report.passed}/{report.total_rules}")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agentlens.models import Session


class RuleKind(str, Enum):
    """Supported compliance rule types."""
    MAX_TOKENS = "max_tokens"
    MIN_TOKENS = "min_tokens"
    ALLOWED_MODELS = "allowed_models"
    FORBIDDEN_MODELS = "forbidden_models"
    REQUIRED_TOOLS = "required_tools"
    FORBIDDEN_TOOLS = "forbidden_tools"
    MAX_EVENTS = "max_events"
    MIN_EVENTS = "min_events"
    MAX_DURATION_MS = "max_duration_ms"
    MAX_TOOL_CALLS = "max_tool_calls"
    REQUIRE_REASONING = "require_reasoning"
    MAX_ERROR_RATE = "max_error_rate"
    CUSTOM = "custom"


class RuleVerdict(str, Enum):
    """Result of evaluating a single rule."""
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"  # rule not applicable (e.g. no events)


@dataclass
class ComplianceRule:
    """A single compliance rule within a policy."""
    kind: str
    description: str = ""
    # Rule-specific parameters
    limit: float | int | None = None
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    threshold: float | None = None
    severity: str = "error"  # error, warning

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ComplianceRule":
        """Create a rule from a dictionary."""
        return cls(
            kind=d.get("kind", "custom"),
            description=d.get("description", ""),
            limit=d.get("limit"),
            tools=d.get("tools", []),
            models=d.get("models", []),
            threshold=d.get("threshold"),
            severity=d.get("severity", "error"),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.description:
            d["description"] = self.description
        if self.limit is not None:
            d["limit"] = self.limit
        if self.tools:
            d["tools"] = self.tools
        if self.models:
            d["models"] = self.models
        if self.threshold is not None:
            d["threshold"] = self.threshold
        if self.severity != "error":
            d["severity"] = self.severity
        return d


@dataclass
class RuleResult:
    """Result of checking a single rule."""
    rule: ComplianceRule
    verdict: str  # pass, fail, skip
    message: str = ""
    actual_value: Any = None
    expected_value: Any = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompliancePolicy:
    """A named set of compliance rules."""
    name: str
    rules: list[ComplianceRule] = field(default_factory=list)
    description: str = ""
    version: str = "1.0"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompliancePolicy":
        """Create a policy from a dictionary."""
        rules = [ComplianceRule.from_dict(r) for r in d.get("rules", [])]
        return cls(
            name=d.get("name", "unnamed-policy"),
            rules=rules,
            description=d.get("description", ""),
            version=d.get("version", "1.0"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "CompliancePolicy":
        """Parse a policy from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "rules": [r.to_dict() for r in self.rules],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class ComplianceReport:
    """Complete compliance check results."""
    policy_name: str
    session_id: str
    agent_name: str
    checked_at: str
    results: list[RuleResult]

    @property
    def compliant(self) -> bool:
        """True if all error-severity rules passed."""
        return all(
            r.verdict != RuleVerdict.FAIL
            for r in self.results
            if r.rule.severity == "error"
        )

    @property
    def total_rules(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.verdict == RuleVerdict.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.verdict == RuleVerdict.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.verdict == RuleVerdict.SKIP)

    @property
    def warnings(self) -> list[RuleResult]:
        """Failed rules that are only warnings."""
        return [
            r for r in self.results
            if r.verdict == RuleVerdict.FAIL and r.rule.severity == "warning"
        ]

    @property
    def errors(self) -> list[RuleResult]:
        """Failed rules that are errors."""
        return [
            r for r in self.results
            if r.verdict == RuleVerdict.FAIL and r.rule.severity == "error"
        ]

    def render(self) -> str:
        """Render a human-readable compliance report."""
        status = "COMPLIANT" if self.compliant else "NON-COMPLIANT"
        icon = "+" if self.compliant else "X"

        lines = [
            "=" * 50,
            f"  Compliance Report: {self.policy_name}",
            "=" * 50,
            "",
            f"  Session:    {self.session_id}",
            f"  Agent:      {self.agent_name}",
            f"  Checked:    {self.checked_at}",
            f"  Status:     [{icon}] {status}",
            f"  Rules:      {self.passed}/{self.total_rules} passed"
            + (f", {self.skipped} skipped" if self.skipped else ""),
            "",
            "-" * 50,
        ]

        icons = {
            RuleVerdict.PASS: "[+]",
            RuleVerdict.FAIL: "[X]",
            RuleVerdict.SKIP: "[-]",
        }

        for r in self.results:
            icon = icons.get(r.verdict, "[?]")
            sev = f" ({r.rule.severity})" if r.verdict == RuleVerdict.FAIL else ""
            kind_label = r.rule.kind
            if r.rule.description:
                kind_label = r.rule.description
            lines.append(f"  {icon} {kind_label}{sev}")
            if r.message:
                lines.append(f"      {r.message}")

        lines.append("")
        lines.append("=" * 50)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "checked_at": self.checked_at,
            "compliant": self.compliant,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_rules": self.total_rules,
            "results": [
                {
                    "kind": r.rule.kind,
                    "severity": r.rule.severity,
                    "verdict": r.verdict,
                    "message": r.message,
                    "actual_value": r.actual_value,
                    "expected_value": r.expected_value,
                }
                for r in self.results
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ── Compliance Checker ─────────────────────────────────────────

class ComplianceChecker:
    """Evaluates sessions against compliance policies.

    Supports built-in rule kinds (token limits, tool/model restrictions,
    event count bounds, duration limits, reasoning requirements, error
    rate limits) and custom rules via callable validators.
    """

    def __init__(self) -> None:
        self._custom_validators: dict[str, Any] = {}

    def register_validator(
        self,
        name: str,
        validator: Any,
    ) -> None:
        """Register a custom rule validator.

        The validator must be a callable accepting (session, rule) and
        returning a RuleResult.

        Args:
            name: Name to match against rule kind.
            validator: Callable(session, rule) -> RuleResult.
        """
        self._custom_validators[name] = validator

    def check(
        self,
        session: Session,
        policy: CompliancePolicy,
    ) -> ComplianceReport:
        """Check a session against a compliance policy.

        Args:
            session: The session to check.
            policy: The policy with rules to enforce.

        Returns:
            A ComplianceReport with per-rule results.
        """
        results: list[RuleResult] = []

        for rule in policy.rules:
            result = self._evaluate_rule(session, rule)
            results.append(result)

        return ComplianceReport(
            policy_name=policy.name,
            session_id=session.session_id,
            agent_name=session.agent_name,
            checked_at=datetime.now(timezone.utc).isoformat(),
            results=results,
        )

    def check_multiple(
        self,
        sessions: list[Session],
        policy: CompliancePolicy,
    ) -> list[ComplianceReport]:
        """Check multiple sessions against a policy.

        Args:
            sessions: List of sessions to check.
            policy: The policy to enforce.

        Returns:
            List of ComplianceReports, one per session.
        """
        return [self.check(s, policy) for s in sessions]

    def _evaluate_rule(
        self,
        session: Session,
        rule: ComplianceRule,
    ) -> RuleResult:
        """Evaluate a single rule against a session."""
        kind = rule.kind

        dispatch = {
            RuleKind.MAX_TOKENS: self._check_max_tokens,
            RuleKind.MIN_TOKENS: self._check_min_tokens,
            RuleKind.ALLOWED_MODELS: self._check_allowed_models,
            RuleKind.FORBIDDEN_MODELS: self._check_forbidden_models,
            RuleKind.REQUIRED_TOOLS: self._check_required_tools,
            RuleKind.FORBIDDEN_TOOLS: self._check_forbidden_tools,
            RuleKind.MAX_EVENTS: self._check_max_events,
            RuleKind.MIN_EVENTS: self._check_min_events,
            RuleKind.MAX_DURATION_MS: self._check_max_duration_ms,
            RuleKind.MAX_TOOL_CALLS: self._check_max_tool_calls,
            RuleKind.REQUIRE_REASONING: self._check_require_reasoning,
            RuleKind.MAX_ERROR_RATE: self._check_max_error_rate,
        }

        # Check built-in rules
        if kind in dispatch:
            return dispatch[kind](session, rule)

        # Check custom validators
        if kind in self._custom_validators:
            try:
                return self._custom_validators[kind](session, rule)
            except Exception as e:
                return RuleResult(
                    rule=rule,
                    verdict=RuleVerdict.SKIP,
                    message=f"Custom validator error: {e}",
                )

        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.SKIP,
            message=f"Unknown rule kind: {kind}",
        )

    # ── Rule evaluators ───────────────────────────────────────

    def _check_max_tokens(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = rule.limit or 0
        total = session.total_tokens_in + session.total_tokens_out
        if total <= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Total tokens {total} within limit {limit}",
                actual_value=total,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Total tokens {total} exceeds limit {limit}",
            actual_value=total,
            expected_value=limit,
        )

    def _check_min_tokens(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = rule.limit or 0
        total = session.total_tokens_in + session.total_tokens_out
        if total >= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Total tokens {total} meets minimum {limit}",
                actual_value=total,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Total tokens {total} below minimum {limit}",
            actual_value=total,
            expected_value=limit,
        )

    def _check_allowed_models(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        allowed = set(m.lower() for m in rule.models)
        used_models = set()
        for event in session.events:
            if event.model:
                used_models.add(event.model.lower())

        if not used_models:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.SKIP,
                message="No models used in session",
            )

        violations = used_models - allowed
        if not violations:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"All models in allowlist: {', '.join(sorted(used_models))}",
                actual_value=sorted(used_models),
                expected_value=sorted(allowed),
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Unauthorized models used: {', '.join(sorted(violations))}",
            actual_value=sorted(violations),
            expected_value=sorted(allowed),
            details={"violations": sorted(violations)},
        )

    def _check_forbidden_models(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        forbidden = set(m.lower() for m in rule.models)
        used_models = set()
        for event in session.events:
            if event.model:
                used_models.add(event.model.lower())

        violations = used_models & forbidden
        if not violations:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message="No forbidden models used",
                actual_value=sorted(used_models),
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Forbidden models used: {', '.join(sorted(violations))}",
            actual_value=sorted(violations),
            details={"violations": sorted(violations)},
        )

    def _check_required_tools(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        required = set(t.lower() for t in rule.tools)
        used_tools: set[str] = set()
        for event in session.events:
            if event.tool_call and event.tool_call.tool_name:
                used_tools.add(event.tool_call.tool_name.lower())

        missing = required - used_tools
        if not missing:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"All required tools used: {', '.join(sorted(required))}",
                actual_value=sorted(used_tools),
                expected_value=sorted(required),
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Required tools not used: {', '.join(sorted(missing))}",
            actual_value=sorted(used_tools),
            expected_value=sorted(required),
            details={"missing": sorted(missing)},
        )

    def _check_forbidden_tools(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        forbidden = set(t.lower() for t in rule.tools)
        used_tools: set[str] = set()
        for event in session.events:
            if event.tool_call and event.tool_call.tool_name:
                used_tools.add(event.tool_call.tool_name.lower())

        violations = used_tools & forbidden
        if not violations:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message="No forbidden tools used",
                actual_value=sorted(used_tools),
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Forbidden tools used: {', '.join(sorted(violations))}",
            actual_value=sorted(violations),
            details={"violations": sorted(violations)},
        )

    def _check_max_events(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = int(rule.limit or 0)
        count = len(session.events)
        if count <= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Event count {count} within limit {limit}",
                actual_value=count,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Event count {count} exceeds limit {limit}",
            actual_value=count,
            expected_value=limit,
        )

    def _check_min_events(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = int(rule.limit or 0)
        count = len(session.events)
        if count >= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Event count {count} meets minimum {limit}",
                actual_value=count,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Event count {count} below minimum {limit}",
            actual_value=count,
            expected_value=limit,
        )

    def _check_max_duration_ms(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = rule.limit or 0
        if not session.ended_at:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.SKIP,
                message="Session still active, cannot check duration",
            )

        duration_ms = (
            session.ended_at - session.started_at
        ).total_seconds() * 1000

        if duration_ms <= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Duration {duration_ms:.0f}ms within limit {limit}ms",
                actual_value=duration_ms,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Duration {duration_ms:.0f}ms exceeds limit {limit}ms",
            actual_value=duration_ms,
            expected_value=limit,
        )

    def _check_max_tool_calls(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        limit = int(rule.limit or 0)
        count = sum(1 for e in session.events if e.tool_call)
        if count <= limit:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=f"Tool calls {count} within limit {limit}",
                actual_value=count,
                expected_value=limit,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=f"Tool calls {count} exceeds limit {limit}",
            actual_value=count,
            expected_value=limit,
        )

    def _check_require_reasoning(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        total_decisions = 0
        with_reasoning = 0

        for event in session.events:
            if event.event_type in ("llm_call", "decision"):
                total_decisions += 1
                if (event.decision_trace
                        and event.decision_trace.reasoning
                        and event.decision_trace.reasoning.strip()):
                    with_reasoning += 1

        if total_decisions == 0:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.SKIP,
                message="No decision events found",
            )

        threshold = rule.threshold if rule.threshold is not None else 1.0
        rate = with_reasoning / total_decisions

        if rate >= threshold:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=(
                    f"Reasoning coverage {rate:.0%} "
                    f"meets threshold {threshold:.0%}"
                ),
                actual_value=rate,
                expected_value=threshold,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=(
                f"Reasoning coverage {rate:.0%} "
                f"below threshold {threshold:.0%} "
                f"({with_reasoning}/{total_decisions} decisions)"
            ),
            actual_value=rate,
            expected_value=threshold,
        )

    def _check_max_error_rate(
        self, session: Session, rule: ComplianceRule
    ) -> RuleResult:
        if not session.events:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.SKIP,
                message="No events in session",
            )

        threshold = rule.threshold if rule.threshold is not None else 0.1
        errors = sum(1 for e in session.events if e.event_type == "error")
        rate = errors / len(session.events)

        if rate <= threshold:
            return RuleResult(
                rule=rule,
                verdict=RuleVerdict.PASS,
                message=(
                    f"Error rate {rate:.1%} within threshold {threshold:.1%}"
                ),
                actual_value=rate,
                expected_value=threshold,
            )
        return RuleResult(
            rule=rule,
            verdict=RuleVerdict.FAIL,
            message=(
                f"Error rate {rate:.1%} exceeds threshold {threshold:.1%} "
                f"({errors}/{len(session.events)} events)"
            ),
            actual_value=rate,
            expected_value=threshold,
        )


# ── Preset policies ───────────────────────────────────────────

def strict_policy() -> CompliancePolicy:
    """A strict compliance policy suitable for production agents.

    Enforces:
    - Max 50k tokens per session
    - Max 200 events per session
    - Max 5 minute duration
    - Max 10% error rate
    - Require reasoning on all decisions
    - No ``execute_code`` or ``shell`` tools
    """
    return CompliancePolicy(
        name="strict",
        description="Strict production policy",
        rules=[
            ComplianceRule(
                kind=RuleKind.MAX_TOKENS,
                description="Token budget",
                limit=50000,
            ),
            ComplianceRule(
                kind=RuleKind.MAX_EVENTS,
                description="Event count limit",
                limit=200,
            ),
            ComplianceRule(
                kind=RuleKind.MAX_DURATION_MS,
                description="Session duration",
                limit=300000,
            ),
            ComplianceRule(
                kind=RuleKind.MAX_ERROR_RATE,
                description="Error rate",
                threshold=0.10,
            ),
            ComplianceRule(
                kind=RuleKind.REQUIRE_REASONING,
                description="Reasoning coverage",
                threshold=1.0,
            ),
            ComplianceRule(
                kind=RuleKind.FORBIDDEN_TOOLS,
                description="Dangerous tools blocked",
                tools=["execute_code", "shell"],
            ),
        ],
    )


def permissive_policy() -> CompliancePolicy:
    """A permissive policy for development/testing.

    Higher limits, warnings instead of errors for some rules.
    """
    return CompliancePolicy(
        name="permissive",
        description="Permissive development policy",
        rules=[
            ComplianceRule(
                kind=RuleKind.MAX_TOKENS,
                description="Token budget",
                limit=500000,
            ),
            ComplianceRule(
                kind=RuleKind.MAX_EVENTS,
                description="Event count limit",
                limit=1000,
            ),
            ComplianceRule(
                kind=RuleKind.MAX_ERROR_RATE,
                description="Error rate",
                threshold=0.25,
                severity="warning",
            ),
        ],
    )
