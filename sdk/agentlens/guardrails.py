"""Session Guardrails — constraint validation for agent sessions.

Define rules that sessions must satisfy and validate them automatically.
Useful for CI/CD gates, production monitoring, and compliance checks.

Supported constraints:
- **Token budgets** — max input/output/total tokens
- **Duration limits** — max session wall-clock time
- **Tool allowlists/blocklists** — require or forbid specific tools
- **Model restrictions** — only allow certain models
- **Event count limits** — min/max events per session
- **Error thresholds** — max number of error events
- **Custom predicates** — arbitrary Python functions

Example::

    from agentlens.guardrails import Guardrails

    g = (Guardrails("production-budget")
         .max_total_tokens(50_000)
         .max_duration_ms(30_000)
         .forbid_tools(["dangerous_exec", "rm_rf"])
         .require_tools(["safety_check"])
         .allow_models(["gpt-4o", "gpt-4o-mini"])
         .max_errors(0))

    result = g.validate(session)
    assert result.passed, result.summary()

    # Multiple guardrail sets
    suite = GuardrailSuite([budget_guard, safety_guard, compliance_guard])
    report = suite.validate(session)
    print(report.render_text())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agentlens.models import Session


class Severity(str, Enum):
    """How serious a guardrail violation is."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Violation:
    """A single guardrail violation."""
    rule: str
    message: str
    severity: Severity = Severity.ERROR
    actual: Any = None
    limit: Any = None

    def __str__(self) -> str:
        sev = self.severity.value.upper()
        return f"[{sev}] {self.rule}: {self.message}"


@dataclass
class ValidationResult:
    """Result of validating a session against guardrails."""
    guardrail_name: str
    session_id: str
    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == Severity.ERROR for v in self.violations)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.WARNING)

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        parts = [f"{self.guardrail_name}: {status}"]
        if self.violations:
            parts.append(f"{self.error_count} errors, {self.warning_count} warnings")
        return " | ".join(parts)

    def render_text(self) -> str:
        lines = [
            f"Guardrail: {self.guardrail_name}",
            f"Session:   {self.session_id}",
            f"Result:    {'PASSED' if self.passed else 'FAILED'}",
        ]
        if self.violations:
            lines.append("")
            for v in self.violations:
                lines.append(f"  {v}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardrail_name": self.guardrail_name,
            "session_id": self.session_id,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "violations": [
                {
                    "rule": v.rule,
                    "message": v.message,
                    "severity": v.severity.value,
                    "actual": v.actual,
                    "limit": v.limit,
                }
                for v in self.violations
            ],
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


class Guardrails:
    """Fluent builder for session constraints.

    Chain methods to add rules, then call ``validate(session)``::

        result = (Guardrails("my-rules")
                  .max_total_tokens(10_000)
                  .max_errors(2)
                  .validate(session))
    """

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._rules: list[Callable[[Session], list[Violation]]] = []

    # ── Token limits ─────────────────────────────────────────────────

    def max_tokens_in(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum input tokens allowed."""
        def check(s: Session) -> list[Violation]:
            if s.total_tokens_in > limit:
                return [Violation(
                    rule="max_tokens_in",
                    message=f"Input tokens {s.total_tokens_in} exceed limit {limit}",
                    severity=severity,
                    actual=s.total_tokens_in,
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    def max_tokens_out(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum output tokens allowed."""
        def check(s: Session) -> list[Violation]:
            if s.total_tokens_out > limit:
                return [Violation(
                    rule="max_tokens_out",
                    message=f"Output tokens {s.total_tokens_out} exceed limit {limit}",
                    severity=severity,
                    actual=s.total_tokens_out,
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    def max_total_tokens(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum total tokens (in + out)."""
        def check(s: Session) -> list[Violation]:
            total = s.total_tokens_in + s.total_tokens_out
            if total > limit:
                return [Violation(
                    rule="max_total_tokens",
                    message=f"Total tokens {total} exceed limit {limit}",
                    severity=severity,
                    actual=total,
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    # ── Duration ─────────────────────────────────────────────────────

    def max_duration_ms(self, limit: float, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum session duration in milliseconds."""
        def check(s: Session) -> list[Violation]:
            if s.ended_at and s.started_at:
                dur = (s.ended_at - s.started_at).total_seconds() * 1000
                if dur > limit:
                    return [Violation(
                        rule="max_duration_ms",
                        message=f"Duration {dur:.0f}ms exceeds limit {limit:.0f}ms",
                        severity=severity,
                        actual=dur,
                        limit=limit,
                    )]
            return []
        self._rules.append(check)
        return self

    # ── Tool constraints ─────────────────────────────────────────────

    def require_tools(self, tools: list[str], severity: Severity = Severity.ERROR) -> Guardrails:
        """Require that specific tools were called at least once."""
        def check(s: Session) -> list[Violation]:
            used = {e.tool_call.tool_name for e in s.events if e.tool_call}
            violations = []
            for t in tools:
                if t not in used:
                    violations.append(Violation(
                        rule="require_tools",
                        message=f"Required tool '{t}' was not called",
                        severity=severity,
                        actual=sorted(used),
                        limit=tools,
                    ))
            return violations
        self._rules.append(check)
        return self

    def forbid_tools(self, tools: list[str], severity: Severity = Severity.ERROR) -> Guardrails:
        """Forbid specific tools from being called."""
        def check(s: Session) -> list[Violation]:
            used = {e.tool_call.tool_name for e in s.events if e.tool_call}
            violations = []
            for t in tools:
                if t in used:
                    count = sum(1 for e in s.events if e.tool_call and e.tool_call.tool_name == t)
                    violations.append(Violation(
                        rule="forbid_tools",
                        message=f"Forbidden tool '{t}' was called {count} time(s)",
                        severity=severity,
                        actual=t,
                        limit=tools,
                    ))
            return violations
        self._rules.append(check)
        return self

    def allow_tools_only(self, tools: list[str], severity: Severity = Severity.ERROR) -> Guardrails:
        """Only allow listed tools; anything else is a violation."""
        allowed = set(tools)
        def check(s: Session) -> list[Violation]:
            used = {e.tool_call.tool_name for e in s.events if e.tool_call}
            violations = []
            for t in sorted(used - allowed):
                violations.append(Violation(
                    rule="allow_tools_only",
                    message=f"Tool '{t}' is not in the allowlist",
                    severity=severity,
                    actual=t,
                    limit=tools,
                ))
            return violations
        self._rules.append(check)
        return self

    # ── Model constraints ────────────────────────────────────────────

    def allow_models(self, models: list[str], severity: Severity = Severity.ERROR) -> Guardrails:
        """Only allow listed models."""
        allowed = set(models)
        def check(s: Session) -> list[Violation]:
            used = {e.model for e in s.events if e.model}
            violations = []
            for m in sorted(used - allowed):
                violations.append(Violation(
                    rule="allow_models",
                    message=f"Model '{m}' is not in the allowlist",
                    severity=severity,
                    actual=m,
                    limit=models,
                ))
            return violations
        self._rules.append(check)
        return self

    # ── Event count ──────────────────────────────────────────────────

    def max_events(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum number of events in a session."""
        def check(s: Session) -> list[Violation]:
            if len(s.events) > limit:
                return [Violation(
                    rule="max_events",
                    message=f"Event count {len(s.events)} exceeds limit {limit}",
                    severity=severity,
                    actual=len(s.events),
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    def min_events(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Minimum number of events expected."""
        def check(s: Session) -> list[Violation]:
            if len(s.events) < limit:
                return [Violation(
                    rule="min_events",
                    message=f"Event count {len(s.events)} below minimum {limit}",
                    severity=severity,
                    actual=len(s.events),
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    # ── Error threshold ──────────────────────────────────────────────

    def max_errors(self, limit: int, severity: Severity = Severity.ERROR) -> Guardrails:
        """Maximum number of error events allowed."""
        def check(s: Session) -> list[Violation]:
            errors = sum(1 for e in s.events if e.event_type == "error")
            if errors > limit:
                return [Violation(
                    rule="max_errors",
                    message=f"Error count {errors} exceeds limit {limit}",
                    severity=severity,
                    actual=errors,
                    limit=limit,
                )]
            return []
        self._rules.append(check)
        return self

    # ── Custom predicates ────────────────────────────────────────────

    def add_rule(
        self,
        name: str,
        predicate: Callable[[Session], bool],
        message: str = "Custom rule failed",
        severity: Severity = Severity.ERROR,
    ) -> Guardrails:
        """Add a custom validation rule.

        The predicate should return ``True`` if the session is valid.
        """
        def check(s: Session) -> list[Violation]:
            if not predicate(s):
                return [Violation(rule=name, message=message, severity=severity)]
            return []
        self._rules.append(check)
        return self

    # ── Validate ─────────────────────────────────────────────────────

    def validate(self, session: Session) -> ValidationResult:
        """Validate a session against all configured rules."""
        result = ValidationResult(
            guardrail_name=self.name,
            session_id=session.session_id,
        )
        for rule_fn in self._rules:
            result.violations.extend(rule_fn(session))
        return result


@dataclass
class SuiteReport:
    """Aggregated report from running multiple guardrail sets."""
    session_id: str
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(r.error_count for r in self.results)

    @property
    def total_warnings(self) -> int:
        return sum(r.warning_count for r in self.results)

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        parts = [f"Suite: {status}"]
        parts.append(f"{len(self.results)} guardrails")
        parts.append(f"{self.total_errors} errors, {self.total_warnings} warnings")
        return " | ".join(parts)

    def render_text(self) -> str:
        lines = [
            "=" * 50,
            "GUARDRAIL SUITE REPORT",
            "=" * 50,
            f"Session: {self.session_id}",
            f"Result:  {'PASSED' if self.passed else 'FAILED'}",
            f"Checks:  {len(self.results)}",
            "",
        ]
        for r in self.results:
            icon = "✓" if r.passed else "✗"
            lines.append(f"  {icon} {r.guardrail_name}: {'PASSED' if r.passed else 'FAILED'}")
            for v in r.violations:
                lines.append(f"      {v}")
        lines.append("")
        lines.append("=" * 50)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "passed": self.passed,
            "total_errors": self.total_errors,
            "total_warnings": self.total_warnings,
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


class GuardrailSuite:
    """Run multiple guardrail sets against a session.

    Example::

        suite = GuardrailSuite([budget_guard, safety_guard])
        report = suite.validate(session)
        assert report.passed
    """

    def __init__(self, guardrails: list[Guardrails] | None = None) -> None:
        self._guardrails: list[Guardrails] = guardrails or []

    def add(self, guardrail: Guardrails) -> GuardrailSuite:
        self._guardrails.append(guardrail)
        return self

    def validate(self, session: Session) -> SuiteReport:
        report = SuiteReport(session_id=session.session_id)
        for g in self._guardrails:
            report.results.append(g.validate(session))
        return report
