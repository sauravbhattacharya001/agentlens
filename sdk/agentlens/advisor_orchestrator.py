"""AdvisorOrchestrator - unified fleet health scorecard from all advisors.

Runs multiple AgentLens advisors in a single pass over an event stream and
produces a consolidated scorecard with cross-advisor correlations, a merged
priority-ordered playbook, and fleet-wide insights no single advisor can see.

Usage:
    from agentlens import AdvisorOrchestrator

    orch = AdvisorOrchestrator(risk_appetite="cautious")
    report = orch.assess(events)
    print(report.to_markdown())
"""

from __future__ import annotations

import dataclasses
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Sequence


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class OrchestratorGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class OrchestratorRiskAppetite(str, Enum):
    cautious = "cautious"
    balanced = "balanced"
    aggressive = "aggressive"


class CorrelationType(str, Enum):
    COST_AND_LOOPS = "COST_AND_LOOPS"
    DRIFT_AND_REGRESSION = "DRIFT_AND_REGRESSION"
    LEAKS_AND_DRIFT = "LEAKS_AND_DRIFT"
    LOOPS_AND_INCOMPLETE = "LOOPS_AND_INCOMPLETE"
    CACHE_AND_COST = "CACHE_AND_COST"
    BURN_AND_REGRESSION = "BURN_AND_REGRESSION"


@dataclasses.dataclass(frozen=True)
class AdvisorResult:
    """Summary of one advisor's output."""

    advisor_name: str
    grade: str
    risk_score: float
    p0_count: int
    p1_count: int
    playbook_actions: list[dict[str, Any]]
    insights: list[str]
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class CrossCorrelation:
    """A detected cross-advisor pattern."""

    correlation_type: str
    description: str
    severity: int  # 0-100
    related_advisors: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class OrchestratorPlaybookAction:
    id: str
    priority: str  # P0/P1/P2/P3
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    source_advisor: str


@dataclasses.dataclass
class OrchestratorReport:
    """Unified fleet scorecard."""

    grade: OrchestratorGrade
    fleet_risk_score: float
    advisor_results: list[AdvisorResult]
    correlations: list[CrossCorrelation]
    merged_playbook: list[OrchestratorPlaybookAction]
    insights: list[str]
    timestamp: str
    risk_appetite: str
    advisors_run: int
    advisors_failed: int

    def to_text(self) -> str:
        lines = [
            f"FLEET SCORECARD: grade={self.grade.value} risk={self.fleet_risk_score:.0f} "
            f"advisors={self.advisors_run} failed={self.advisors_failed} appetite={self.risk_appetite}",
            "",
        ]
        lines.append("--- Advisor Summary ---")
        for ar in self.advisor_results:
            status = f"ERROR: {ar.error}" if ar.error else f"grade={ar.grade} risk={ar.risk_score:.0f} P0={ar.p0_count} P1={ar.p1_count}"
            lines.append(f"  {ar.advisor_name}: {status}")
        if self.correlations:
            lines.append("")
            lines.append("--- Cross-Advisor Correlations ---")
            for c in self.correlations:
                lines.append(f"  [{c.severity}] {c.correlation_type}: {c.description}")
        lines.append("")
        lines.append("--- Merged Playbook (top 10) ---")
        for a in self.merged_playbook[:10]:
            lines.append(f"  {a.priority} {a.label} ({a.source_advisor}) -> {a.owner}")
        lines.append("")
        lines.append("--- Insights ---")
        for i in self.insights:
            lines.append(f"  • {i}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines = [
            f"# Fleet Health Scorecard",
            "",
            f"**Grade:** {self.grade.value} | **Risk:** {self.fleet_risk_score:.0f}/100 | "
            f"**Advisors:** {self.advisors_run} run, {self.advisors_failed} failed | "
            f"**Appetite:** {self.risk_appetite}",
            "",
            "## Advisor Results",
            "",
            "| Advisor | Grade | Risk | P0 | P1 | Status |",
            "|---------|-------|------|----|----|--------|",
        ]
        for ar in self.advisor_results:
            if ar.error:
                lines.append(f"| {ar.advisor_name} | - | - | - | - | ❌ {ar.error[:40]} |")
            else:
                lines.append(f"| {ar.advisor_name} | {ar.grade} | {ar.risk_score:.0f} | {ar.p0_count} | {ar.p1_count} | ✅ |")
        if self.correlations:
            lines.append("")
            lines.append("## Cross-Advisor Correlations")
            lines.append("")
            lines.append("| Type | Severity | Description | Advisors |")
            lines.append("|------|----------|-------------|----------|")
            for c in self.correlations:
                lines.append(f"| {c.correlation_type} | {c.severity} | {c.description} | {', '.join(c.related_advisors)} |")
        lines.append("")
        lines.append("## Merged Playbook")
        lines.append("")
        lines.append("| # | Priority | Action | Owner | Source |")
        lines.append("|---|----------|--------|-------|--------|")
        for i, a in enumerate(self.merged_playbook[:15], 1):
            lines.append(f"| {i} | {a.priority} | {a.label} | {a.owner} | {a.source_advisor} |")
        lines.append("")
        lines.append("## Insights")
        lines.append("")
        for ins in self.insights:
            lines.append(f"- {ins}")
        return "\n".join(lines)

    def to_json(self) -> str:
        def _ser(obj: Any) -> Any:
            if isinstance(obj, Enum):
                return obj.value
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return dataclasses.asdict(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        payload = {
            "grade": self.grade.value,
            "fleet_risk_score": round(self.fleet_risk_score, 2),
            "advisors_run": self.advisors_run,
            "advisors_failed": self.advisors_failed,
            "risk_appetite": self.risk_appetite,
            "timestamp": self.timestamp,
            "advisor_results": [dataclasses.asdict(ar) for ar in self.advisor_results],
            "correlations": [dataclasses.asdict(c) for c in self.correlations],
            "merged_playbook": [dataclasses.asdict(a) for a in self.merged_playbook],
            "insights": self.insights,
        }
        return json.dumps(payload, sort_keys=True, indent=2, default=_ser)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class AdvisorOrchestrator:
    """Run all available advisors and produce a unified fleet scorecard.

    Args:
        risk_appetite: cautious / balanced / aggressive
        now_fn: Injectable clock for determinism.
        advisors: Optional explicit list of advisor names to run.
            Default runs all available advisors.
    """

    def __init__(
        self,
        risk_appetite: str = "balanced",
        now_fn: Callable[[], datetime] | None = None,
        advisors: Sequence[str] | None = None,
    ):
        if risk_appetite not in ("cautious", "balanced", "aggressive"):
            raise ValueError(f"Invalid risk_appetite: {risk_appetite}")
        self._appetite = OrchestratorRiskAppetite(risk_appetite)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._advisor_filter = set(advisors) if advisors else None

    def assess(self, events: Sequence[Any]) -> OrchestratorReport:
        """Run advisors and return unified scorecard."""
        results: list[AdvisorResult] = []
        for name, runner in self._get_advisors():
            if self._advisor_filter and name not in self._advisor_filter:
                continue
            try:
                result = runner(events)
                results.append(result)
            except Exception as e:
                results.append(AdvisorResult(
                    advisor_name=name,
                    grade="?",
                    risk_score=0,
                    p0_count=0,
                    p1_count=0,
                    playbook_actions=[],
                    insights=[],
                    error=str(e)[:200],
                ))

        # Compute fleet score
        valid = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]

        if valid:
            scores = [r.risk_score for r in valid]
            fleet_risk = statistics.mean(sorted(scores, reverse=True)[:max(3, len(scores) // 3 + 1)])
        else:
            fleet_risk = 0.0

        # Apply appetite
        appetite_shift = {"cautious": 5, "balanced": 0, "aggressive": -5}[self._appetite.value]
        fleet_risk = max(0, min(100, fleet_risk + appetite_shift))

        # Grade
        total_p0 = sum(r.p0_count for r in valid)
        grade = self._compute_grade(fleet_risk, total_p0)

        # Correlations
        correlations = self._detect_correlations(valid)

        # Merged playbook - deduplicate by label, keep highest priority
        merged = self._merge_playbooks(valid)

        # Insights
        insights = self._synthesize_insights(valid, failed, correlations, fleet_risk, grade)

        return OrchestratorReport(
            grade=grade,
            fleet_risk_score=round(fleet_risk, 1),
            advisor_results=results,
            correlations=correlations,
            merged_playbook=merged,
            insights=insights,
            timestamp=self._now_fn().isoformat(),
            risk_appetite=self._appetite.value,
            advisors_run=len(results),
            advisors_failed=len(failed),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_grade(self, risk: float, total_p0: int) -> OrchestratorGrade:
        if total_p0 >= 3 or risk >= 75:
            return OrchestratorGrade.F
        if total_p0 >= 1 or risk >= 55:
            return OrchestratorGrade.D
        if risk >= 35:
            return OrchestratorGrade.C
        if risk >= 18:
            return OrchestratorGrade.B
        return OrchestratorGrade.A

    def _detect_correlations(self, results: list[AdvisorResult]) -> list[CrossCorrelation]:
        correlations: list[CrossCorrelation] = []
        by_name = {r.advisor_name: r for r in results}

        # Cost + Loops correlation
        cost = by_name.get("CostAttributionAdvisor")
        loops = by_name.get("AgentLoopDetector")
        if cost and loops and cost.risk_score >= 30 and loops.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.COST_AND_LOOPS.value,
                description="High cost coincides with loop detection - loops may be driving spend",
                severity=min(100, int((cost.risk_score + loops.risk_score) / 2)),
                related_advisors=("CostAttributionAdvisor", "AgentLoopDetector"),
            ))

        # Prompt drift + eval regression
        drift = by_name.get("PromptDriftAdvisor")
        regression = by_name.get("EvalRegressionAdvisor")
        if drift and regression and drift.risk_score >= 30 and regression.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.DRIFT_AND_REGRESSION.value,
                description="Prompt drift detected alongside performance regression - drift may be the root cause",
                severity=min(100, int((drift.risk_score + regression.risk_score) / 2)),
                related_advisors=("PromptDriftAdvisor", "EvalRegressionAdvisor"),
            ))

        # Data leaks + prompt drift
        leaks = by_name.get("DataLeakAdvisor")
        if leaks and drift and leaks.risk_score >= 30 and drift.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.LEAKS_AND_DRIFT.value,
                description="Data leak signals alongside prompt drift - drifted prompts may be exposing data",
                severity=min(100, int((leaks.risk_score + drift.risk_score) / 2)),
                related_advisors=("DataLeakAdvisor", "PromptDriftAdvisor"),
            ))

        # Loops + incomplete traces
        traces = by_name.get("TraceCompletionAdvisor")
        if loops and traces and loops.risk_score >= 30 and traces.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.LOOPS_AND_INCOMPLETE.value,
                description="Loops correlate with incomplete traces - loops may be causing hangs/timeouts",
                severity=min(100, int((loops.risk_score + traces.risk_score) / 2)),
                related_advisors=("AgentLoopDetector", "TraceCompletionAdvisor"),
            ))

        # Cacheability + cost
        cache = by_name.get("CacheabilityAdvisor")
        if cache and cost and cache.risk_score >= 30 and cost.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.CACHE_AND_COST.value,
                description="High cache opportunity with high cost - enabling caching may significantly reduce spend",
                severity=min(100, int((cache.risk_score + cost.risk_score) / 2)),
                related_advisors=("CacheabilityAdvisor", "CostAttributionAdvisor"),
            ))

        # SLO burn + regression
        burn = by_name.get("SLOBurnRateAdvisor")
        if burn and regression and burn.risk_score >= 30 and regression.risk_score >= 30:
            correlations.append(CrossCorrelation(
                correlation_type=CorrelationType.BURN_AND_REGRESSION.value,
                description="SLO budget burning alongside regression - regression is consuming error budget",
                severity=min(100, int((burn.risk_score + regression.risk_score) / 2)),
                related_advisors=("SLOBurnRateAdvisor", "EvalRegressionAdvisor"),
            ))

        correlations.sort(key=lambda c: -c.severity)
        return correlations

    def _merge_playbooks(self, results: list[AdvisorResult]) -> list[OrchestratorPlaybookAction]:
        all_actions: list[OrchestratorPlaybookAction] = []
        seen_labels: set[str] = set()

        # Collect all actions with source
        raw: list[tuple[str, dict]] = []
        for r in results:
            for a in r.playbook_actions:
                raw.append((r.advisor_name, a))

        # Sort by priority then by risk_score of source advisor (higher risk first)
        risk_by_name = {r.advisor_name: r.risk_score for r in results}
        raw.sort(key=lambda x: (
            _PRIORITY_ORDER.get(x[1].get("priority", "P3"), 3),
            -risk_by_name.get(x[0], 0),
        ))

        for source, action in raw:
            label = action.get("label", action.get("id", "unknown"))
            if label in seen_labels:
                continue
            seen_labels.add(label)
            all_actions.append(OrchestratorPlaybookAction(
                id=action.get("id", label),
                priority=action.get("priority", "P3"),
                label=label,
                reason=action.get("reason", ""),
                owner=action.get("owner", "platform"),
                blast_radius=action.get("blast_radius", 1),
                reversibility=action.get("reversibility", "medium"),
                source_advisor=source,
            ))

        return all_actions

    def _synthesize_insights(
        self,
        valid: list[AdvisorResult],
        failed: list[AdvisorResult],
        correlations: list[CrossCorrelation],
        fleet_risk: float,
        grade: OrchestratorGrade,
    ) -> list[str]:
        insights: list[str] = []

        if not valid:
            insights.append("NO_ADVISORS_PRODUCED_RESULTS")
            return insights

        total_p0 = sum(r.p0_count for r in valid)
        total_p1 = sum(r.p1_count for r in valid)

        if total_p0 >= 3:
            insights.append(f"MULTI_ADVISOR_P0_CLUSTER: {total_p0} P0 actions across fleet")
        if correlations:
            insights.append(f"CROSS_ADVISOR_CORRELATIONS_DETECTED: {len(correlations)} pattern(s)")

        # Find worst advisor
        if valid:
            worst = max(valid, key=lambda r: r.risk_score)
            if worst.risk_score >= 50:
                insights.append(f"HOTSPOT_ADVISOR: {worst.advisor_name} (risk={worst.risk_score:.0f})")

        # Check for widespread issues
        high_risk_count = sum(1 for r in valid if r.risk_score >= 40)
        if high_risk_count >= 3:
            insights.append(f"WIDESPREAD_RISK: {high_risk_count}/{len(valid)} advisors reporting elevated risk")

        if failed:
            insights.append(f"ADVISOR_FAILURES: {len(failed)} advisor(s) errored out")

        # Grade-based
        if grade == OrchestratorGrade.A:
            insights.append("FLEET_HEALTHY: all advisors report low risk")
        elif grade == OrchestratorGrade.F:
            insights.append("FLEET_CRITICAL: immediate attention required")

        if not insights:
            insights.append("FLEET_NOMINAL")

        return insights

    def _get_advisors(self) -> list[tuple[str, Callable]]:
        """Return (name, runner_fn) for each available advisor."""
        advisors: list[tuple[str, Callable]] = []

        # Each runner takes events and returns AdvisorResult
        advisors.append(("TraceCompletionAdvisor", self._run_trace_completion))
        advisors.append(("AgentLoopDetector", self._run_loop_detector))
        advisors.append(("CostAttributionAdvisor", self._run_cost_attribution))
        advisors.append(("DataLeakAdvisor", self._run_data_leak))
        advisors.append(("CacheabilityAdvisor", self._run_cacheability))

        return advisors

    def _run_trace_completion(self, events: Sequence[Any]) -> AdvisorResult:
        from agentlens.trace_completion_advisor import TraceCompletionAdvisor

        adv = TraceCompletionAdvisor(
            risk_appetite=self._appetite.value, now_fn=self._now_fn
        )
        report = adv.analyze(events)
        # ``incompletion_score`` is the advisor's native 0-100 risk signal.
        risk = _coerce_risk(
            getattr(report, "incompletion_score", None), report
        )
        return _result_from_report("TraceCompletionAdvisor", report, risk)

    def _run_loop_detector(self, events: Sequence[Any]) -> AdvisorResult:
        from agentlens.agent_loop_detector import AgentLoopDetector

        adv = AgentLoopDetector(risk_appetite=self._appetite.value, now_fn=self._now_fn)
        report = adv.analyze(events)
        # The loop report exposes ``overall_loop_risk`` (0-100).
        risk = _coerce_risk(
            getattr(report, "overall_loop_risk", None), report
        )
        return _result_from_report("AgentLoopDetector", report, risk)

    def _run_cost_attribution(self, events: Sequence[Any]) -> AdvisorResult:
        from agentlens.cost_attribution_advisor import CostAttributionAdvisor

        adv = CostAttributionAdvisor(
            risk_appetite=self._appetite.value, now_fn=self._now_fn
        )
        report = adv.analyze(events)
        # Cost has no single 0-100 score; derive it from the spend grade so a
        # top-heavy / overspending portfolio surfaces as elevated fleet risk.
        risk = _coerce_risk(None, report)
        return _result_from_report("CostAttributionAdvisor", report, risk)

    def _run_data_leak(self, events: Sequence[Any]) -> AdvisorResult:
        from agentlens.data_leak_advisor import DataLeakAdvisor

        # DataLeakAdvisor takes ``risk_appetite`` on analyze(), not __init__.
        adv = DataLeakAdvisor(now_fn=self._now_fn)
        report = adv.analyze(events, risk_appetite=self._appetite.value)
        # Risk is grade-driven (portfolio_grade); a secret/PII leak grades low.
        risk = _coerce_risk(None, report)
        return _result_from_report("DataLeakAdvisor", report, risk)

    def _run_cacheability(self, events: Sequence[Any]) -> AdvisorResult:
        from agentlens.cacheability_advisor import CacheabilityAdvisor

        # CacheabilityAdvisor takes ``risk_appetite`` on analyze(), not __init__.
        adv = CacheabilityAdvisor(now_fn=self._now_fn)
        report = adv.analyze(events, risk_appetite=self._appetite.value)
        # The cache "risk" is really an opportunity: the share of spend that is
        # cacheable. A high savings share means high waste -> high risk.
        portfolio = getattr(report, "portfolio", None)
        savings_share = getattr(portfolio, "projected_savings_share", None)
        explicit = savings_share * 100 if isinstance(savings_share, (int, float)) else None
        risk = _coerce_risk(explicit, report)
        return _result_from_report("CacheabilityAdvisor", report, risk)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action_to_dict(action: Any) -> dict[str, Any]:
    """Convert a playbook action (dataclass or object) to dict."""
    if dataclasses.is_dataclass(action) and not isinstance(action, type):
        d = dataclasses.asdict(action)
    elif hasattr(action, "__dict__"):
        d = dict(action.__dict__)
    else:
        d = {"label": str(action)}
    # Normalize priority to string
    p = d.get("priority", "P3")
    if hasattr(p, "value"):
        d["priority"] = p.value
    elif isinstance(p, int):
        d["priority"] = f"P{p}"
    else:
        d["priority"] = str(p)
    return d


def _extract_grade(report: Any) -> str:
    """Extract grade string from a report object.

    Looks first at the top-level report, then at ``report.portfolio`` since a
    few advisors (cost/leak/cacheability) carry the grade on the portfolio
    summary rather than the report itself.
    """
    grade_attrs = (
        "grade",
        "portfolio_grade",
        "completion_grade",
        "leak_grade",
        "cost_grade",
        "loop_grade",
    )
    for source in (report, getattr(report, "portfolio", None)):
        if source is None:
            continue
        for attr in grade_attrs:
            val = getattr(source, attr, None)
            if val is not None:
                return val.value if hasattr(val, "value") else str(val)
    return "?"


# Map a letter grade to a representative 0-100 risk score. Used for advisors
# (cost, data-leak) that grade their portfolio but do not publish a single
# numeric risk value, so they still contribute meaningfully to the fleet score.
_GRADE_TO_RISK = {
    "A": 8.0,
    "B": 25.0,
    "C": 45.0,
    "D": 65.0,
    "F": 88.0,
}


def _grade_to_risk(grade: str) -> float:
    """Translate a letter grade into a representative 0-100 risk score."""
    return _GRADE_TO_RISK.get(str(grade).strip().upper(), 0.0)


def _coerce_risk(explicit: Any, report: Any) -> float:
    """Return a 0-100 risk score for an advisor report.

    Prefers an *explicit* numeric signal supplied by the caller (the advisor's
    native score). When that is missing or non-numeric, falls back to deriving
    risk from the report's letter grade so the advisor still influences the
    fleet score. The result is always clamped to ``[0, 100]``.
    """
    if isinstance(explicit, bool):
        explicit = None  # guard against True/False being treated as 1/0
    if isinstance(explicit, (int, float)):
        return max(0.0, min(100.0, float(explicit)))
    return _grade_to_risk(_extract_grade(report))


def _result_from_report(name: str, report: Any, risk: float) -> AdvisorResult:
    """Build an :class:`AdvisorResult` from a concrete advisor report.

    Centralises the playbook/grade/insight extraction shared by every advisor
    runner so the per-advisor methods only have to compute the risk score.
    """
    playbook = [_action_to_dict(a) for a in getattr(report, "playbook", [])]
    return AdvisorResult(
        advisor_name=name,
        grade=_extract_grade(report),
        risk_score=float(risk),
        p0_count=sum(1 for a in playbook if a.get("priority") == "P0"),
        p1_count=sum(1 for a in playbook if a.get("priority") == "P1"),
        playbook_actions=playbook,
        insights=[str(i) for i in getattr(report, "insights", [])],
    )
