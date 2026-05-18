"""Agentic multi-window error-budget burn-rate advisor for AgentLens.

The classic Google SRE pattern: classify each Service-Level Objective (SLO)
into action verdicts using **multi-window, multi-burn-rate** thresholds, then
emit a ranked playbook of pre-emptive actions (page on-call, file a budget
ticket, freeze deploys, etc.).

This is a sibling to :mod:`agentlens.incident_radar` (cross-module risk fusion),
:mod:`agentlens.alert_rule_synthesizer` (rule auto-tuning), and
:mod:`agentlens.model_migration_advisor` (per-site migration planning). Like
those analyzers it is pure read, deterministic given a fixed ``now`` callable,
and never mutates its inputs. Stdlib + pydantic only.

Burn rate is defined as::

    burn_rate = failure_fraction / error_budget_fraction
              = (1 - compliance_fraction) / (1 - slo_percent / 100)

A burn rate of 1.0 means the SLO's monthly error budget would be exhausted
exactly over the monthly window (default 30d / 720h). A burn rate of 14.4
means it would be exhausted in roughly two hours.

Standard Google SRE matrix (encoded in :data:`_TIERS`):

==================  ============  ===========  ==============
Severity             Burn rate     Short win    Long win (confirm)
==================  ============  ===========  ==============
PAGE_FAST            >= 14.4       5m           1h
PAGE_MEDIUM          >= 6.0        30m          6h
TICKET_SLOW          >= 3.0        1h           1d
TICKET_TREND         >= 1.0        6h           3d
==================  ============  ===========  ==============

A short-window match without a matching long-window confirmation is
downgraded one severity tier (and the reason notes "no confirmation
window present"). ``risk_appetite`` modulates the thresholds: ``cautious``
multiplies them by 0.85 (more eager to page), ``aggressive`` by 1.15
(less eager). The portfolio score is shifted +/-8 the same direction.

Example
-------
::

    from agentlens import (
        SLOBurnRateAdvisor, ObjectiveBurnSnapshot, WindowSample, WindowLabel,
    )

    advisor = SLOBurnRateAdvisor(risk_appetite="balanced")
    report = advisor.assess([
        ObjectiveBurnSnapshot(
            objective_name="P95 latency <= 3000ms",
            samples=[
                WindowSample(window=WindowLabel.FIVE_MIN, result=res_5m),
                WindowSample(window=WindowLabel.ONE_HOUR, result=res_1h),
                WindowSample(window=WindowLabel.ONE_DAY,  result=res_1d),
            ],
        ),
    ])
    print(report.summary)
    print(report.render_markdown())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from agentlens.sla import ObjectiveResult


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class WindowLabel(Enum):
    """Standard observation windows for burn-rate evaluation."""

    FIVE_MIN = "5m"
    THIRTY_MIN = "30m"
    ONE_HOUR = "1h"
    SIX_HOURS = "6h"
    ONE_DAY = "1d"
    THREE_DAYS = "3d"

    @property
    def hours(self) -> float:
        return _WINDOW_HOURS[self.value]


_WINDOW_HOURS: dict[str, float] = {
    "5m": 5.0 / 60.0,
    "30m": 0.5,
    "1h": 1.0,
    "6h": 6.0,
    "1d": 24.0,
    "3d": 72.0,
}

# Short-to-long order, used to pick the "shortest" window for time-to-exhaustion.
_WINDOW_ORDER: list[str] = ["5m", "30m", "1h", "6h", "1d", "3d"]


class BurnSeverity(Enum):
    """Per-(objective, window) burn classification, worst-first."""

    PAGE_FAST = "PAGE_FAST"
    PAGE_MEDIUM = "PAGE_MEDIUM"
    TICKET_SLOW = "TICKET_SLOW"
    TICKET_TREND = "TICKET_TREND"
    WATCH = "WATCH"
    HEALTHY = "HEALTHY"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.value]

    @property
    def priority(self) -> str:
        return _SEVERITY_PRIORITY[self.value]

    @property
    def risk_weight(self) -> float:
        return _SEVERITY_WEIGHT[self.value]


_SEVERITY_RANK: dict[str, int] = {
    "PAGE_FAST": 0,
    "PAGE_MEDIUM": 1,
    "TICKET_SLOW": 2,
    "TICKET_TREND": 3,
    "WATCH": 4,
    "HEALTHY": 5,
}

_SEVERITY_PRIORITY: dict[str, str] = {
    "PAGE_FAST": "P0",
    "PAGE_MEDIUM": "P0",
    "TICKET_SLOW": "P1",
    "TICKET_TREND": "P1",
    "WATCH": "P2",
    "HEALTHY": "P3",
}

_SEVERITY_WEIGHT: dict[str, float] = {
    "PAGE_FAST": 100.0,
    "PAGE_MEDIUM": 85.0,
    "TICKET_SLOW": 60.0,
    "TICKET_TREND": 40.0,
    "WATCH": 20.0,
    "HEALTHY": 0.0,
}

# Order matters: severity, threshold, short_window, long_window.
_TIERS: list[tuple[BurnSeverity, float, WindowLabel, WindowLabel]] = [
    (BurnSeverity.PAGE_FAST, 14.4, WindowLabel.FIVE_MIN, WindowLabel.ONE_HOUR),
    (BurnSeverity.PAGE_MEDIUM, 6.0, WindowLabel.THIRTY_MIN, WindowLabel.SIX_HOURS),
    (BurnSeverity.TICKET_SLOW, 3.0, WindowLabel.ONE_HOUR, WindowLabel.ONE_DAY),
    (BurnSeverity.TICKET_TREND, 1.0, WindowLabel.SIX_HOURS, WindowLabel.THREE_DAYS),
]

# Ladder used to "downgrade by one priority" when a confirmation window is missing.
_DOWNGRADE: dict[BurnSeverity, BurnSeverity] = {
    BurnSeverity.PAGE_FAST: BurnSeverity.PAGE_MEDIUM,
    BurnSeverity.PAGE_MEDIUM: BurnSeverity.TICKET_SLOW,
    BurnSeverity.TICKET_SLOW: BurnSeverity.TICKET_TREND,
    BurnSeverity.TICKET_TREND: BurnSeverity.WATCH,
    BurnSeverity.WATCH: BurnSeverity.WATCH,
    BurnSeverity.HEALTHY: BurnSeverity.HEALTHY,
}


_RISK_APPETITES = ("cautious", "balanced", "aggressive")
_APPETITE_MULT: dict[str, float] = {
    "cautious": 0.85,
    "balanced": 1.0,
    "aggressive": 1.15,
}
_APPETITE_SCORE_SHIFT: dict[str, float] = {
    "cautious": +8.0,
    "balanced": 0.0,
    "aggressive": -8.0,
}


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WindowSample:
    """One ObjectiveResult captured at a particular observation window."""

    window: WindowLabel
    result: ObjectiveResult


@dataclass(frozen=True)
class ObjectiveBurnSnapshot:
    """All available window samples for a single SLO objective."""

    objective_name: str
    samples: tuple[WindowSample, ...] | list[WindowSample]


@dataclass
class BurnRateAssessment:
    """Per-(objective, window) classification."""

    objective_name: str
    window: WindowLabel
    burn_rate: float
    severity: BurnSeverity
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_name": self.objective_name,
            "window": self.window.value,
            "burn_rate": round(self.burn_rate, 4),
            "severity": self.severity.value,
            "reason": self.reason,
        }


@dataclass
class PlaybookAction:
    """One ranked, deduped pre-emptive action."""

    id: str
    priority: str  # "P0" | "P1" | "P2" | "P3"
    label: str
    reason: str
    owner: str
    blast_radius: int  # 1..5
    reversibility: str  # "low" | "medium" | "high"
    related_objectives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority,
            "label": self.label,
            "reason": self.reason,
            "owner": self.owner,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "related_objectives": list(self.related_objectives),
        }


@dataclass
class ObjectivePlan:
    """All the burn analysis for one SLO objective."""

    objective_name: str
    overall_severity: BurnSeverity
    risk_score: float
    time_to_exhaustion_hours: Optional[float]
    per_window: list[BurnRateAssessment]
    insights: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_name": self.objective_name,
            "overall_severity": self.overall_severity.value,
            "risk_score": round(self.risk_score, 2),
            "time_to_exhaustion_hours": (
                round(self.time_to_exhaustion_hours, 2)
                if self.time_to_exhaustion_hours is not None
                else None
            ),
            "per_window": [a.to_dict() for a in self.per_window],
            "insights": list(self.insights),
        }


@dataclass
class SLOBurnReport:
    """Full SLOBurnRateAdvisor.assess() output."""

    risk_appetite: str
    generated_at: datetime
    portfolio_risk_score: float
    grade: str
    objectives: list[ObjectivePlan]
    actions: list[PlaybookAction]
    insights: list[str]
    summary: str

    # -- serialisers --

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_appetite": self.risk_appetite,
            "generated_at": self.generated_at.isoformat(),
            "portfolio_risk_score": round(self.portfolio_risk_score, 2),
            "grade": self.grade,
            "objectives": [o.to_dict() for o in self.objectives],
            "actions": [a.to_dict() for a in self.actions],
            "insights": list(self.insights),
            "summary": self.summary,
        }

    def render_json(self, indent: int = 2) -> str:
        """Byte-stable JSON given a fixed ``now_fn``."""
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            default=str,
        )

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append(f"SLO Burn-Rate Report ({self.risk_appetite})")
        lines.append("=" * 55)
        lines.append(self.summary)
        lines.append("")
        for plan in self.objectives:
            tte = (
                f"{plan.time_to_exhaustion_hours:.1f}h"
                if plan.time_to_exhaustion_hours is not None
                else "n/a"
            )
            lines.append(
                f"  [{plan.overall_severity.priority}] {plan.objective_name}  "
                f"score={plan.risk_score:.0f}  exhaust={tte}"
            )
            for a in plan.per_window:
                lines.append(
                    f"    - {a.window.value:<4} burn={a.burn_rate:6.2f}  "
                    f"{a.severity.value}  ({a.reason})"
                )
            for s in plan.insights:
                lines.append(f"    ! {s}")
        if self.actions:
            lines.append("")
            lines.append("Playbook:")
            for a in self.actions:
                related = (
                    f"  [{', '.join(a.related_objectives)}]"
                    if a.related_objectives
                    else ""
                )
                lines.append(
                    f"  [{a.priority}] {a.id} - {a.label}  "
                    f"(owner={a.owner}, blast={a.blast_radius}, "
                    f"reversibility={a.reversibility}){related}"
                )
                lines.append(f"      {a.reason}")
        if self.insights:
            lines.append("")
            lines.append("Insights:")
            for s in self.insights:
                lines.append(f"  - {s}")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append(
            f"# SLO Burn-Rate Report ({self.risk_appetite})"
        )
        lines.append("")
        lines.append(
            f"**Portfolio risk:** {self.portfolio_risk_score:.1f}/100  "
            f"**Grade:** {self.grade}  "
            f"**Summary:** {self.summary}"
        )
        lines.append("")

        # Per-objective summary table
        lines.append("## Objectives")
        lines.append("")
        lines.append("| Objective | Severity | Priority | Risk | Time-to-exhaust |")
        lines.append("|---|---|---|---|---|")
        for plan in self.objectives:
            tte = (
                f"{plan.time_to_exhaustion_hours:.1f}h"
                if plan.time_to_exhaustion_hours is not None
                else "n/a"
            )
            lines.append(
                f"| {plan.objective_name} | {plan.overall_severity.value} | "
                f"{plan.overall_severity.priority} | "
                f"{plan.risk_score:.0f} | {tte} |"
            )
        lines.append("")

        # Per-window matrix
        lines.append("## Per-objective matrix")
        lines.append("")
        lines.append("| Objective | Window | Burn rate | Severity | Reason |")
        lines.append("|---|---|---|---|---|")
        for plan in self.objectives:
            for a in plan.per_window:
                lines.append(
                    f"| {plan.objective_name} | {a.window.value} | "
                    f"{a.burn_rate:.2f} | {a.severity.value} | {a.reason} |"
                )
        lines.append("")

        # Playbook
        lines.append("## Playbook")
        lines.append("")
        if not self.actions:
            lines.append("_No actions._")
        else:
            for a in self.actions:
                related = (
                    f" — objectives: {', '.join(a.related_objectives)}"
                    if a.related_objectives
                    else ""
                )
                lines.append(
                    f"- **[{a.priority}] {a.id}** — {a.label} "
                    f"(owner={a.owner}, blast_radius={a.blast_radius}, "
                    f"reversibility={a.reversibility}){related}"
                )
                lines.append(f"  - _Why:_ {a.reason}")
        lines.append("")

        # Insights
        lines.append("## Insights")
        lines.append("")
        if not self.insights:
            lines.append("_No insights._")
        else:
            for s in self.insights:
                lines.append(f"- {s}")
        lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Action catalogue
# --------------------------------------------------------------------------- #


# id -> (label, default_priority, owner, blast_radius, reversibility)
_ACTION_CATALOGUE: dict[str, tuple[str, str, str, int, str]] = {
    "PAGE_ONCALL": (
        "Page the on-call engineer", "P0", "oncall", 1, "high",
    ),
    "OPEN_INCIDENT": (
        "Open an incident channel and assign IC", "P0", "sre", 2, "low",
    ),
    "FILE_BUDGET_TICKET": (
        "File an error-budget ticket against the service owner",
        "P1", "service_owner", 2, "high",
    ),
    "FREEZE_RISKY_DEPLOYS": (
        "Freeze risky deploys until burn rate recovers",
        "P1", "release_mgr", 3, "high",
    ),
    "INVESTIGATE_TOP_OFFENDER": (
        "Investigate the worst-offending objective",
        "P1", "owner", 1, "high",
    ),
    "RAISE_SLO_OR_RELAX_TARGET": (
        "Review the SLO target: chronic burn without paging",
        "P2", "product", 2, "high",
    ),
    "INCREASE_OBSERVABILITY": (
        "Increase observability: only one window of data",
        "P2", "sre", 1, "high",
    ),
    "ALL_CLEAR_DOCUMENT_LEARNINGS": (
        "All clear - document learnings from any recent burn",
        "P3", "owner", 1, "high",
    ),
}


_PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _make_action(
    action_id: str,
    reason: str,
    related: list[str],
    priority_override: Optional[str] = None,
) -> PlaybookAction:
    label, default_priority, owner, blast, reversibility = _ACTION_CATALOGUE[action_id]
    return PlaybookAction(
        id=action_id,
        priority=priority_override or default_priority,
        label=label,
        reason=reason,
        owner=owner,
        blast_radius=blast,
        reversibility=reversibility,
        related_objectives=list(related),
    )


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class SLOBurnRateAdvisor:
    """Multi-window error-budget burn-rate analyzer.

    Args:
        risk_appetite: ``"cautious"`` lowers thresholds (more eager to
            page), ``"aggressive"`` raises them, ``"balanced"`` is the
            default Google SRE matrix verbatim.
        monthly_hours: Hours in the SLO's evaluation window. Used only
            for the ``time_to_exhaustion_hours`` projection.
        now_fn: Optional callable returning the "current time". Useful
            in tests for deterministic ``generated_at`` values.
    """

    def __init__(
        self,
        risk_appetite: str = "balanced",
        monthly_hours: float = 720.0,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"risk_appetite must be one of {_RISK_APPETITES!r}, "
                f"got {risk_appetite!r}"
            )
        if monthly_hours <= 0:
            raise ValueError("monthly_hours must be positive.")
        self.risk_appetite = risk_appetite
        self.monthly_hours = float(monthly_hours)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # -- public API ----------------------------------------------------------

    def assess(self, snapshots: list[ObjectiveBurnSnapshot]) -> SLOBurnReport:
        if not snapshots:
            raise ValueError("no snapshots provided")

        mult = _APPETITE_MULT[self.risk_appetite]
        plans: list[ObjectivePlan] = []
        for snap in snapshots:
            if not snap.samples:
                raise ValueError(
                    f"objective {snap.objective_name!r} has no window samples"
                )
            plans.append(self._assess_objective(snap, mult))

        # Portfolio score: weighted mean of per-objective scores, then shift.
        if plans:
            raw = sum(p.risk_score for p in plans) / len(plans)
        else:  # pragma: no cover - guarded above
            raw = 0.0
        shifted = raw + _APPETITE_SCORE_SHIFT[self.risk_appetite]
        portfolio = max(0.0, min(100.0, shifted))

        has_page_fast = any(
            p.overall_severity == BurnSeverity.PAGE_FAST for p in plans
        )
        grade = self._grade(portfolio, has_page_fast)

        actions = self._build_playbook(plans)
        insights = self._portfolio_insights(plans)

        # Summary line
        page_count = sum(
            1
            for p in plans
            if p.overall_severity in (BurnSeverity.PAGE_FAST, BurnSeverity.PAGE_MEDIUM)
        )
        worst = min(
            plans, key=lambda p: p.overall_severity.rank
        ).overall_severity.value
        summary = (
            f"portfolio={portfolio:.1f}/100 grade={grade} "
            f"objectives={len(plans)} worst={worst} pages={page_count}"
        )

        return SLOBurnReport(
            risk_appetite=self.risk_appetite,
            generated_at=self._now_fn(),
            portfolio_risk_score=portfolio,
            grade=grade,
            objectives=plans,
            actions=actions,
            insights=insights,
            summary=summary,
        )

    # -- per-objective -------------------------------------------------------

    def _assess_objective(
        self,
        snap: ObjectiveBurnSnapshot,
        mult: float,
    ) -> ObjectivePlan:
        # Compute burn_rate per window, ordered short->long.
        by_window: dict[WindowLabel, float] = {}
        ordered_samples = sorted(
            snap.samples,
            key=lambda s: _WINDOW_ORDER.index(s.window.value),
        )
        for s in ordered_samples:
            by_window[s.window] = self._burn_rate(s.result)

        # Per-window starting severity: HEALTHY if burn==0 else WATCH.
        window_sev: dict[WindowLabel, tuple[BurnSeverity, str]] = {}
        for w, br in by_window.items():
            if br <= 0.0:
                window_sev[w] = (BurnSeverity.HEALTHY, "burn rate 0")
            else:
                window_sev[w] = (
                    BurnSeverity.WATCH,
                    f"burn rate {br:.2f} (no tier matched)",
                )

        # Walk tiers high->low. If the short window matches, mark both
        # windows at this severity when the long window confirms; otherwise
        # downgrade by one priority tier.
        for tier_sev, threshold, short_w, long_w in _TIERS:
            short_burn = by_window.get(short_w)
            if short_burn is None:
                continue
            threshold_eff = threshold * mult
            if short_burn < threshold_eff:
                continue

            long_burn = by_window.get(long_w)
            confirmed = long_burn is not None and long_burn >= threshold_eff

            if confirmed:
                reason_short = (
                    f"burn {short_burn:.2f} >= {threshold_eff:.2f} over "
                    f"{short_w.value}, confirmed by {long_w.value} "
                    f"({long_burn:.2f})"
                )
                reason_long = (
                    f"burn {long_burn:.2f} >= {threshold_eff:.2f} over "
                    f"{long_w.value}, confirms {short_w.value} "
                    f"({short_burn:.2f})"
                )
                self._upgrade(window_sev, short_w, tier_sev, reason_short)
                self._upgrade(window_sev, long_w, tier_sev, reason_long)
            else:
                downgraded = _DOWNGRADE[tier_sev]
                why_missing = (
                    f"{long_w.value} not present"
                    if long_burn is None
                    else f"{long_w.value} burn {long_burn:.2f} < {threshold_eff:.2f}"
                )
                reason = (
                    f"burn {short_burn:.2f} >= {threshold_eff:.2f} over "
                    f"{short_w.value} but no confirmation window present "
                    f"({why_missing}); downgraded one priority to "
                    f"{downgraded.value}"
                )
                self._upgrade(window_sev, short_w, downgraded, reason)

        # Build per-window assessments in short->long order.
        per_window: list[BurnRateAssessment] = []
        for s in ordered_samples:
            sev, reason = window_sev[s.window]
            per_window.append(
                BurnRateAssessment(
                    objective_name=snap.objective_name,
                    window=s.window,
                    burn_rate=by_window[s.window],
                    severity=sev,
                    reason=reason,
                )
            )

        overall = min((a.severity for a in per_window), key=lambda s: s.rank)

        # Time-to-exhaustion via the shortest window's burn rate.
        shortest = ordered_samples[0]
        shortest_burn = by_window[shortest.window]
        budget_remaining_fraction = self._budget_remaining_fraction(shortest.result)
        if shortest_burn > 0:
            tte = (budget_remaining_fraction / shortest_burn) * self.monthly_hours
            tte = max(0.0, tte)
        else:
            tte = None

        # Per-objective insights.
        insights: list[str] = []
        if shortest.result.error_budget_remaining <= 0:
            insights.append("BUDGET_EXHAUSTED")
        if len(by_window) == 1 and shortest_burn >= 1.0:
            insights.append("INSUFFICIENT_DATA")
        chronic = sum(
            1 for a in per_window if a.burn_rate >= 1.0
        )
        is_page = overall in (BurnSeverity.PAGE_FAST, BurnSeverity.PAGE_MEDIUM)
        if chronic >= 2 and not is_page:
            insights.append("CHRONIC_BURN")
        if overall == BurnSeverity.HEALTHY:
            insights.append("HEALTHY")

        # Per-objective risk score: severity-weight of the overall verdict,
        # nudged up to the max per-window weight to ensure consistency.
        risk_score = max(a.severity.risk_weight for a in per_window)

        return ObjectivePlan(
            objective_name=snap.objective_name,
            overall_severity=overall,
            risk_score=risk_score,
            time_to_exhaustion_hours=tte,
            per_window=per_window,
            insights=insights,
        )

    @staticmethod
    def _upgrade(
        window_sev: dict[WindowLabel, tuple[BurnSeverity, str]],
        window: WindowLabel,
        sev: BurnSeverity,
        reason: str,
    ) -> None:
        current, _ = window_sev[window]
        if sev.rank < current.rank:
            window_sev[window] = (sev, reason)

    @staticmethod
    def _burn_rate(result: ObjectiveResult) -> float:
        slo_fraction = result.objective.slo_percent / 100.0
        budget_fraction = max(0.0, 1.0 - slo_fraction)
        if budget_fraction <= 0:
            return 0.0
        compliance_fraction = max(0.0, min(1.0, result.compliance_percent / 100.0))
        failure_fraction = max(0.0, 1.0 - compliance_fraction)
        return failure_fraction / budget_fraction

    @staticmethod
    def _budget_remaining_fraction(result: ObjectiveResult) -> float:
        total = result.error_budget_total
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, result.error_budget_remaining / total))

    # -- portfolio playbook --------------------------------------------------

    def _build_playbook(self, plans: list[ObjectivePlan]) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []

        page_plans = [
            p for p in plans
            if p.overall_severity in (BurnSeverity.PAGE_FAST, BurnSeverity.PAGE_MEDIUM)
        ]
        ticket_plans = [
            p for p in plans
            if p.overall_severity in (BurnSeverity.TICKET_SLOW, BurnSeverity.TICKET_TREND)
        ]
        burning_soon = [
            p for p in plans
            if p.time_to_exhaustion_hours is not None
            and p.time_to_exhaustion_hours < 24.0
        ]
        chronic_no_page = [
            p for p in plans
            if "CHRONIC_BURN" in p.insights
        ]
        insufficient = [p for p in plans if "INSUFFICIENT_DATA" in p.insights]

        if page_plans:
            actions.append(_make_action(
                "PAGE_ONCALL",
                reason=(
                    f"{len(page_plans)} objective(s) at PAGE-level burn rate"
                ),
                related=[p.objective_name for p in page_plans],
            ))
        if len(page_plans) >= 2:
            actions.append(_make_action(
                "OPEN_INCIDENT",
                reason=(
                    f"multi-objective page event ({len(page_plans)} objectives)"
                ),
                related=[p.objective_name for p in page_plans],
            ))
        for tp in ticket_plans:
            actions.append(_make_action(
                "FILE_BUDGET_TICKET",
                reason=(
                    f"{tp.objective_name} is burning at TICKET tier "
                    f"({tp.overall_severity.value})"
                ),
                related=[tp.objective_name],
            ))
        if burning_soon:
            soon_names = [p.objective_name for p in burning_soon]
            actions.append(_make_action(
                "FREEZE_RISKY_DEPLOYS",
                reason=(
                    f"{len(burning_soon)} objective(s) project budget "
                    f"exhaustion in <24h"
                ),
                related=soon_names,
            ))
        # Investigate top offender (worst overall severity, then highest score).
        if any(p.overall_severity != BurnSeverity.HEALTHY for p in plans):
            top = sorted(
                plans,
                key=lambda p: (p.overall_severity.rank, -p.risk_score),
            )[0]
            actions.append(_make_action(
                "INVESTIGATE_TOP_OFFENDER",
                reason=(
                    f"{top.objective_name} is the worst-offending objective "
                    f"({top.overall_severity.value}, risk {top.risk_score:.0f})"
                ),
                related=[top.objective_name],
            ))
        for cp in chronic_no_page:
            actions.append(_make_action(
                "RAISE_SLO_OR_RELAX_TARGET",
                reason=(
                    f"{cp.objective_name} chronically burns >= 1.0x across "
                    f"multiple windows without triggering a page"
                ),
                related=[cp.objective_name],
            ))
        for ip in insufficient:
            actions.append(_make_action(
                "INCREASE_OBSERVABILITY",
                reason=(
                    f"{ip.objective_name} has only one window of data while "
                    f"burning >= 1.0x"
                ),
                related=[ip.objective_name],
            ))
        if not actions and all(p.overall_severity == BurnSeverity.HEALTHY for p in plans):
            actions.append(_make_action(
                "ALL_CLEAR_DOCUMENT_LEARNINGS",
                reason="all objectives healthy, no recent burn",
                related=[],
            ))

        # Dedupe by id, keep first occurrence.
        seen: set[str] = set()
        deduped: list[PlaybookAction] = []
        for a in actions:
            if a.id in seen:
                continue
            seen.add(a.id)
            deduped.append(a)

        # P0-first stable sort.
        deduped.sort(key=lambda a: _PRIORITY_RANK[a.priority])
        return deduped

    # -- portfolio insights --------------------------------------------------

    @staticmethod
    def _portfolio_insights(plans: list[ObjectivePlan]) -> list[str]:
        out: list[str] = []
        page_count = sum(
            1 for p in plans
            if p.overall_severity in (BurnSeverity.PAGE_FAST, BurnSeverity.PAGE_MEDIUM)
        )
        if page_count >= 2:
            out.append("MULTI_OBJECTIVE_PAGE")
        if any(a.burn_rate >= 14.4 for p in plans for a in p.per_window):
            out.append("FAST_BURN_DETECTED")
        if any("BUDGET_EXHAUSTED" in p.insights for p in plans):
            out.append("BUDGET_EXHAUSTED")
        if any("INSUFFICIENT_DATA" in p.insights for p in plans):
            out.append("INSUFFICIENT_DATA")
        if plans and all(p.overall_severity == BurnSeverity.HEALTHY for p in plans):
            out.append("ALL_HEALTHY")
        return out

    # -- grading -------------------------------------------------------------

    @staticmethod
    def _grade(portfolio_score: float, has_page_fast: bool) -> str:
        if has_page_fast:
            return "F"
        if portfolio_score == 0:
            return "A"
        if portfolio_score < 20:
            return "B"
        if portfolio_score < 40:
            return "C"
        if portfolio_score < 60:
            return "D"
        return "F"


__all__ = [
    "WindowLabel",
    "BurnSeverity",
    "WindowSample",
    "ObjectiveBurnSnapshot",
    "BurnRateAssessment",
    "PlaybookAction",
    "ObjectivePlan",
    "SLOBurnReport",
    "SLOBurnRateAdvisor",
]
