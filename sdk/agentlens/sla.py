"""SLA Monitor for AgentLens.

Defines service-level objectives (SLOs) and evaluates agent sessions
against them, reporting compliance percentages, violation counts,
error budgets, and at-risk metrics.

Distinct from health scoring (per-session, one-time) and alerts
(real-time threshold triggers): SLA monitoring tracks ongoing
service-level compliance across many sessions over time windows.

Example::

    from agentlens.sla import SLAEvaluator, SLObjective, SLAPolicy

    policy = SLAPolicy(
        name="production",
        objectives=[
            SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0),
            SLObjective.error_rate(target_rate=0.01, slo_percent=99.5),
            SLObjective.token_budget(target_per_session=5000, slo_percent=95.0),
        ],
    )

    evaluator = SLAEvaluator()
    report = evaluator.evaluate(sessions, policy)
    print(report.render())
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Objective kinds
# ---------------------------------------------------------------------------

class ObjectiveKind(Enum):
    """Type of service-level objective."""
    LATENCY_P95 = "latency_p95"
    LATENCY_AVG = "latency_avg"
    ERROR_RATE = "error_rate"
    TOKEN_BUDGET = "token_budget"
    TOOL_SUCCESS_RATE = "tool_success_rate"
    THROUGHPUT = "throughput"


class ComplianceStatus(Enum):
    """Overall SLA compliance status."""
    COMPLIANT = "compliant"
    AT_RISK = "at_risk"       # within 5% of target
    VIOLATED = "violated"


# ---------------------------------------------------------------------------
# Objective definition
# ---------------------------------------------------------------------------

@dataclass
class SLObjective:
    """A single service-level objective.

    Attributes:
        kind: The metric being monitored.
        target: Target threshold value.
        slo_percent: Percentage of sessions that must meet the target
            (e.g. 99.0 means 99% of sessions must comply).
        name: Human-readable objective name (auto-generated if omitted).
    """
    kind: ObjectiveKind
    target: float
    slo_percent: float = 99.0
    name: str = ""

    def __post_init__(self) -> None:
        if self.slo_percent <= 0 or self.slo_percent > 100:
            raise ValueError("slo_percent must be between 0 (exclusive) and 100 (inclusive).")
        if not self.name:
            self.name = f"{self.kind.value} < {self.target}"

    # -- convenience constructors -------------------------------------------

    @classmethod
    def latency_p95(cls, target_ms: float, slo_percent: float = 99.0) -> SLObjective:
        """Create a P95 latency SLO."""
        return cls(
            kind=ObjectiveKind.LATENCY_P95,
            target=target_ms,
            slo_percent=slo_percent,
            name=f"P95 latency \u2264 {target_ms:.0f}ms",
        )

    @classmethod
    def latency_avg(cls, target_ms: float, slo_percent: float = 99.0) -> SLObjective:
        """Create an average latency SLO."""
        return cls(
            kind=ObjectiveKind.LATENCY_AVG,
            target=target_ms,
            slo_percent=slo_percent,
            name=f"Avg latency \u2264 {target_ms:.0f}ms",
        )

    @classmethod
    def error_rate(cls, target_rate: float, slo_percent: float = 99.5) -> SLObjective:
        """Create an error-rate SLO.

        Args:
            target_rate: Max error rate per session (e.g. 0.01 = 1%).
            slo_percent: Percentage of sessions that must stay under.
        """
        return cls(
            kind=ObjectiveKind.ERROR_RATE,
            target=target_rate,
            slo_percent=slo_percent,
            name=f"Error rate \u2264 {target_rate:.1%}",
        )

    @classmethod
    def token_budget(cls, target_per_session: int, slo_percent: float = 95.0) -> SLObjective:
        """Create a token-budget SLO."""
        return cls(
            kind=ObjectiveKind.TOKEN_BUDGET,
            target=float(target_per_session),
            slo_percent=slo_percent,
            name=f"Tokens \u2264 {target_per_session}/session",
        )

    @classmethod
    def tool_success_rate(cls, target_rate: float = 0.95, slo_percent: float = 99.0) -> SLObjective:
        """Create a tool-success-rate SLO."""
        return cls(
            kind=ObjectiveKind.TOOL_SUCCESS_RATE,
            target=target_rate,
            slo_percent=slo_percent,
            name=f"Tool success \u2265 {target_rate:.0%}",
        )

    @classmethod
    def throughput(cls, min_events: int, slo_percent: float = 95.0) -> SLObjective:
        """Create a minimum-throughput SLO (events per session)."""
        return cls(
            kind=ObjectiveKind.THROUGHPUT,
            target=float(min_events),
            slo_percent=slo_percent,
            name=f"Events \u2265 {min_events}/session",
        )


# ---------------------------------------------------------------------------
# Policy = collection of objectives
# ---------------------------------------------------------------------------

@dataclass
class SLAPolicy:
    """A named collection of service-level objectives.

    Attributes:
        name: Policy name (e.g. ``"production"``).
        objectives: List of SLO definitions.
        description: Optional description.
    """
    name: str
    objectives: list[SLObjective] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Policy name cannot be empty.")


# ---------------------------------------------------------------------------
# Evaluation result types
# ---------------------------------------------------------------------------

@dataclass
class ObjectiveResult:
    """Evaluation result for a single SLO."""
    objective: SLObjective
    compliant_sessions: int
    total_sessions: int
    compliance_percent: float       # actual compliance %
    status: ComplianceStatus
    violations: list[str]           # session IDs that violated
    error_budget_total: float       # allowed violations (as count)
    error_budget_remaining: float   # remaining budget
    error_budget_percent: float     # % of budget remaining
    measured_values: list[float]    # per-session measured values

    @property
    def violation_count(self) -> int:
        return len(self.violations)


@dataclass
class SLAReport:
    """Complete SLA evaluation report."""
    policy_name: str
    total_sessions: int
    overall_status: ComplianceStatus
    results: list[ObjectiveResult]
    compliant_objectives: int
    violated_objectives: int
    at_risk_objectives: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict representation."""
        return {
            "policy_name": self.policy_name,
            "total_sessions": self.total_sessions,
            "overall_status": self.overall_status.value,
            "compliant_objectives": self.compliant_objectives,
            "violated_objectives": self.violated_objectives,
            "at_risk_objectives": self.at_risk_objectives,
            "objectives": [
                {
                    "name": r.objective.name,
                    "kind": r.objective.kind.value,
                    "target": r.objective.target,
                    "slo_percent": r.objective.slo_percent,
                    "compliance_percent": round(r.compliance_percent, 2),
                    "status": r.status.value,
                    "compliant_sessions": r.compliant_sessions,
                    "total_sessions": r.total_sessions,
                    "violation_count": r.violation_count,
                    "error_budget_total": round(r.error_budget_total, 2),
                    "error_budget_remaining": round(r.error_budget_remaining, 2),
                    "error_budget_percent": round(r.error_budget_percent, 2),
                }
                for r in self.results
            ],
        }

    def render(self) -> str:
        """Return a human-readable summary."""
        lines: list[str] = []
        status_icon = {
            ComplianceStatus.COMPLIANT: "\u2705",
            ComplianceStatus.AT_RISK: "\u26A0\uFE0F",
            ComplianceStatus.VIOLATED: "\u274C",
        }

        lines.append(f"SLA Report: {self.policy_name}")
        lines.append("=" * 55)
        lines.append(
            f"Status: {status_icon.get(self.overall_status, '?')} "
            f"{self.overall_status.value.upper()}  |  "
            f"Sessions: {self.total_sessions}"
        )
        lines.append(
            f"Objectives: {self.compliant_objectives} compliant, "
            f"{self.at_risk_objectives} at-risk, "
            f"{self.violated_objectives} violated"
        )
        lines.append("")

        for r in self.results:
            icon = status_icon.get(r.status, "?")
            lines.append(f"  {icon} {r.objective.name}")
            lines.append(
                f"    Target: {r.objective.slo_percent:.1f}%  |  "
                f"Actual: {r.compliance_percent:.2f}%  |  "
                f"Violations: {r.violation_count}"
            )
            budget_pct = r.error_budget_percent
            lines.append(
                f"    Error budget: {r.error_budget_remaining:.1f}/"
                f"{r.error_budget_total:.1f} remaining ({budget_pct:.1f}%)"
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class SLAEvaluator:
    """Evaluates sessions against an SLA policy.

    Sessions can be provided as either:
    - Raw session dicts with ``session_id`` and ``events`` (list of event dicts).
    - Session model objects with ``session_id`` and ``events`` attributes.

    Example::

        evaluator = SLAEvaluator()
        report = evaluator.evaluate(sessions, policy)
        if report.overall_status == ComplianceStatus.VIOLATED:
            print("SLA BREACH:", report.render())
    """

    # Margin within which compliance is "at risk" (percentage points)
    AT_RISK_MARGIN = 5.0

    def evaluate(self, sessions: list[Any], policy: SLAPolicy) -> SLAReport:
        """Evaluate sessions against the given SLA policy.

        Args:
            sessions: List of session dicts or session objects.
            policy: The SLA policy to evaluate against.

        Returns:
            An :class:`SLAReport` with per-objective results.

        Raises:
            ValueError: If sessions is empty or policy has no objectives.
        """
        if not sessions:
            raise ValueError("No sessions to evaluate.")
        if not policy.objectives:
            raise ValueError("Policy has no objectives.")

        # Normalize sessions to dicts
        normalized = [self._normalize_session(s) for s in sessions]

        results: list[ObjectiveResult] = []
        for obj in policy.objectives:
            result = self._evaluate_objective(normalized, obj)
            results.append(result)

        # Aggregate status
        compliant = sum(1 for r in results if r.status == ComplianceStatus.COMPLIANT)
        at_risk = sum(1 for r in results if r.status == ComplianceStatus.AT_RISK)
        violated = sum(1 for r in results if r.status == ComplianceStatus.VIOLATED)

        if violated > 0:
            overall = ComplianceStatus.VIOLATED
        elif at_risk > 0:
            overall = ComplianceStatus.AT_RISK
        else:
            overall = ComplianceStatus.COMPLIANT

        return SLAReport(
            policy_name=policy.name,
            total_sessions=len(normalized),
            overall_status=overall,
            results=results,
            compliant_objectives=compliant,
            violated_objectives=violated,
            at_risk_objectives=at_risk,
        )

    # -- per-objective evaluation -------------------------------------------

    def _evaluate_objective(
        self,
        sessions: list[dict[str, Any]],
        objective: SLObjective,
    ) -> ObjectiveResult:
        """Evaluate a single objective across all sessions."""
        violations: list[str] = []
        measured_values: list[float] = []
        total = len(sessions)

        for s in sessions:
            sid = s.get("session_id", "unknown")
            events = s.get("events", [])
            value = self._measure(events, objective.kind)
            measured_values.append(value)

            if self._is_violated(value, objective):
                violations.append(sid)

        compliant_count = total - len(violations)
        compliance_pct = (compliant_count / total * 100.0) if total > 0 else 100.0

        # Error budget: how many violations are allowed
        allowed_violations = total * (1.0 - objective.slo_percent / 100.0)
        budget_remaining = allowed_violations - len(violations)
        budget_pct = (budget_remaining / allowed_violations * 100.0) if allowed_violations > 0 else (
            100.0 if len(violations) == 0 else 0.0
        )

        # Determine status
        if compliance_pct >= objective.slo_percent:
            # Check if at-risk (within margin)
            if compliance_pct < objective.slo_percent + self.AT_RISK_MARGIN and len(violations) > 0:
                status = ComplianceStatus.AT_RISK
            else:
                status = ComplianceStatus.COMPLIANT
        else:
            status = ComplianceStatus.VIOLATED

        return ObjectiveResult(
            objective=objective,
            compliant_sessions=compliant_count,
            total_sessions=total,
            compliance_percent=compliance_pct,
            status=status,
            violations=violations,
            error_budget_total=allowed_violations,
            error_budget_remaining=max(0.0, budget_remaining),
            error_budget_percent=max(0.0, budget_pct),
            measured_values=measured_values,
        )

    # -- measurement helpers -------------------------------------------------

    def _measure(self, events: list[dict[str, Any]], kind: ObjectiveKind) -> float:
        """Compute the metric value for a session's events."""
        if kind == ObjectiveKind.LATENCY_P95:
            return self._measure_p95_latency(events)
        elif kind == ObjectiveKind.LATENCY_AVG:
            return self._measure_avg_latency(events)
        elif kind == ObjectiveKind.ERROR_RATE:
            return self._measure_error_rate(events)
        elif kind == ObjectiveKind.TOKEN_BUDGET:
            return self._measure_total_tokens(events)
        elif kind == ObjectiveKind.TOOL_SUCCESS_RATE:
            return self._measure_tool_success_rate(events)
        elif kind == ObjectiveKind.THROUGHPUT:
            return float(len(events))
        else:
            return 0.0

    @staticmethod
    def _measure_p95_latency(events: list[dict[str, Any]]) -> float:
        durations = sorted(
            e.get("duration_ms", 0.0) or 0.0
            for e in events
            if e.get("duration_ms") is not None
        )
        if not durations:
            return 0.0
        idx = 0.95 * (len(durations) - 1)
        lo = int(math.floor(idx))
        hi = min(lo + 1, len(durations) - 1)
        frac = idx - lo
        return durations[lo] + (durations[hi] - durations[lo]) * frac

    @staticmethod
    def _measure_avg_latency(events: list[dict[str, Any]]) -> float:
        durations = [
            e.get("duration_ms", 0.0) or 0.0
            for e in events
            if e.get("duration_ms") is not None
        ]
        if not durations:
            return 0.0
        return sum(durations) / len(durations)

    @staticmethod
    def _measure_error_rate(events: list[dict[str, Any]]) -> float:
        if not events:
            return 0.0
        errors = 0
        for e in events:
            if e.get("event_type") == "error":
                errors += 1
                continue
            tc = e.get("tool_call")
            if isinstance(tc, dict):
                out = tc.get("tool_output")
                if isinstance(out, dict) and out.get("error"):
                    errors += 1
        return errors / len(events)

    @staticmethod
    def _measure_total_tokens(events: list[dict[str, Any]]) -> float:
        return float(sum(
            (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
            for e in events
        ))

    @staticmethod
    def _measure_tool_success_rate(events: list[dict[str, Any]]) -> float:
        tool_events = [e for e in events if e.get("tool_call") is not None]
        if not tool_events:
            return 1.0  # No tool calls = no failures
        failures = 0
        for e in tool_events:
            tc = e.get("tool_call")
            if isinstance(tc, dict):
                out = tc.get("tool_output")
                if isinstance(out, dict) and out.get("error"):
                    failures += 1
        return 1.0 - (failures / len(tool_events))

    @staticmethod
    def _is_violated(value: float, objective: SLObjective) -> bool:
        """Check if a measured value violates the objective target.

        For most metrics, the value must be <= target (lower is better).
        For TOOL_SUCCESS_RATE and THROUGHPUT, value must be >= target
        (higher is better).
        """
        if objective.kind in (ObjectiveKind.TOOL_SUCCESS_RATE, ObjectiveKind.THROUGHPUT):
            return value < objective.target
        return value > objective.target

    @staticmethod
    def _normalize_session(session: Any) -> dict[str, Any]:
        """Normalize a session to a dict with session_id and events."""
        if isinstance(session, dict):
            return session

        # Model object — extract fields
        sid = getattr(session, "session_id", "unknown")
        events_raw = getattr(session, "events", [])
        events: list[dict[str, Any]] = []

        for ev in events_raw:
            if isinstance(ev, dict):
                events.append(ev)
            else:
                d: dict[str, Any] = {
                    "event_type": getattr(ev, "event_type", "generic"),
                    "duration_ms": getattr(ev, "duration_ms", None),
                    "tokens_in": getattr(ev, "tokens_in", 0),
                    "tokens_out": getattr(ev, "tokens_out", 0),
                }
                tc = getattr(ev, "tool_call", None)
                if tc is not None:
                    if isinstance(tc, dict):
                        d["tool_call"] = tc
                    else:
                        d["tool_call"] = {
                            "tool_name": getattr(tc, "tool_name", ""),
                            "tool_output": getattr(tc, "tool_output", None),
                        }
                else:
                    d["tool_call"] = None
                events.append(d)

        return {"session_id": sid, "events": events}


# ---------------------------------------------------------------------------
# Preset policies
# ---------------------------------------------------------------------------

def production_policy() -> SLAPolicy:
    """Strict SLA policy suitable for production deployments.

    - P95 latency ≤ 3000ms (99% of sessions)
    - Error rate ≤ 1% (99.5% of sessions)
    - Token budget ≤ 10000/session (95% of sessions)
    - Tool success ≥ 95% (99% of sessions)
    """
    return SLAPolicy(
        name="production",
        description="Strict SLA targets for production agent deployments",
        objectives=[
            SLObjective.latency_p95(target_ms=3000.0, slo_percent=99.0),
            SLObjective.error_rate(target_rate=0.01, slo_percent=99.5),
            SLObjective.token_budget(target_per_session=10000, slo_percent=95.0),
            SLObjective.tool_success_rate(target_rate=0.95, slo_percent=99.0),
        ],
    )


def development_policy() -> SLAPolicy:
    """Relaxed SLA policy suitable for development and testing.

    - P95 latency ≤ 10000ms (90% of sessions)
    - Error rate ≤ 5% (90% of sessions)
    - Token budget ≤ 50000/session (80% of sessions)
    """
    return SLAPolicy(
        name="development",
        description="Relaxed SLA targets for development and testing",
        objectives=[
            SLObjective.latency_p95(target_ms=10000.0, slo_percent=90.0),
            SLObjective.error_rate(target_rate=0.05, slo_percent=90.0),
            SLObjective.token_budget(target_per_session=50000, slo_percent=80.0),
        ],
    )
