"""Tests for agentlens.slo_burn_rate_advisor.SLOBurnRateAdvisor."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agentlens.sla import (
    ComplianceStatus,
    ObjectiveKind,
    ObjectiveResult,
    SLObjective,
)
from agentlens.slo_burn_rate_advisor import (
    BurnRateAssessment,
    BurnSeverity,
    ObjectiveBurnSnapshot,
    ObjectivePlan,
    SLOBurnRateAdvisor,
    SLOBurnReport,
    WindowLabel,
    WindowSample,
)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _result(
    *,
    slo_percent: float = 99.0,
    compliance_percent: float,
    total_sessions: int = 1000,
    budget_remaining_fraction: float = 0.5,
    name: str = "P95 latency <= 3000ms",
    kind: ObjectiveKind = ObjectiveKind.LATENCY_P95,
    target: float = 3000.0,
) -> ObjectiveResult:
    obj = SLObjective(kind=kind, target=target, slo_percent=slo_percent, name=name)
    total = float(total_sessions)
    error_budget_total = total * (1.0 - slo_percent / 100.0)
    compliance_fraction = compliance_percent / 100.0
    violations_count = int(round(total * (1.0 - compliance_fraction)))
    compliant_count = total_sessions - violations_count
    error_budget_remaining = max(
        0.0, error_budget_total * budget_remaining_fraction
    )
    budget_pct = (
        (error_budget_remaining / error_budget_total * 100.0)
        if error_budget_total > 0
        else 100.0
    )
    if compliance_percent >= slo_percent:
        status = ComplianceStatus.COMPLIANT
    else:
        status = ComplianceStatus.VIOLATED
    return ObjectiveResult(
        objective=obj,
        compliant_sessions=compliant_count,
        total_sessions=total_sessions,
        compliance_percent=compliance_percent,
        status=status,
        violations=[f"s{i}" for i in range(violations_count)],
        error_budget_total=error_budget_total,
        error_budget_remaining=error_budget_remaining,
        error_budget_percent=budget_pct,
        measured_values=[0.0] * total_sessions,
    )


def _snap(
    name: str,
    samples: list[tuple[WindowLabel, ObjectiveResult]],
) -> ObjectiveBurnSnapshot:
    return ObjectiveBurnSnapshot(
        objective_name=name,
        samples=[WindowSample(window=w, result=r) for w, r in samples],
    )


_FIXED_NOW = datetime(2026, 5, 17, 18, 0, 0, tzinfo=timezone.utc)


def _advisor(risk_appetite: str = "balanced") -> SLOBurnRateAdvisor:
    return SLOBurnRateAdvisor(
        risk_appetite=risk_appetite,
        now_fn=lambda: _FIXED_NOW,
    )


def _burn_to_compliance(burn_rate: float, slo_percent: float) -> float:
    """Inverse of the advisor's burn-rate formula -- produce a fixture whose
    burn_rate exactly equals the requested value."""
    budget_fraction = 1.0 - slo_percent / 100.0
    failure_fraction = burn_rate * budget_fraction
    return max(0.0, min(100.0, (1.0 - failure_fraction) * 100.0))


# --------------------------------------------------------------------------- #
# 1. Healthy snapshot
# --------------------------------------------------------------------------- #


def test_all_healthy_grade_a_and_all_clear_action() -> None:
    advisor = _advisor()
    snap = _snap("P95 latency <= 3000ms", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=99.0, compliance_percent=100.0)),
        (WindowLabel.ONE_HOUR, _result(slo_percent=99.0, compliance_percent=100.0)),
        (WindowLabel.ONE_DAY,  _result(slo_percent=99.0, compliance_percent=100.0)),
    ])
    report = advisor.assess([snap])

    assert report.grade == "A"
    assert report.portfolio_risk_score == 0.0
    plan = report.objectives[0]
    assert plan.overall_severity is BurnSeverity.HEALTHY
    assert all(a.burn_rate == 0.0 for a in plan.per_window)
    ids = {a.id for a in report.actions}
    assert ids == {"ALL_CLEAR_DOCUMENT_LEARNINGS"}
    assert "ALL_HEALTHY" in report.insights


# --------------------------------------------------------------------------- #
# 2. PAGE_FAST: 5m + 1h both at >= 14.4 burn
# --------------------------------------------------------------------------- #


def test_page_fast_triggers_page_oncall_and_grade_f() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(15.0, slo)  # comfortably >= 14.4
    snap = _snap("P95 latency <= 3000ms", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_DAY,  _result(slo_percent=slo, compliance_percent=99.5)),
    ])
    report = advisor.assess([snap])

    plan = report.objectives[0]
    assert plan.overall_severity is BurnSeverity.PAGE_FAST
    assert report.grade == "F"  # forced F by PAGE_FAST
    ids = [a.id for a in report.actions]
    assert "PAGE_ONCALL" in ids
    assert ids[0] == "PAGE_ONCALL"  # P0 first
    assert "FAST_BURN_DETECTED" in report.insights


# --------------------------------------------------------------------------- #
# 3. PAGE_MEDIUM: 30m + 6h at >= 6
# --------------------------------------------------------------------------- #


def test_page_medium_triggers_page_oncall() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(7.0, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.THIRTY_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.SIX_HOURS,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])

    plan = report.objectives[0]
    assert plan.overall_severity is BurnSeverity.PAGE_MEDIUM
    assert "PAGE_ONCALL" in {a.id for a in report.actions}


# --------------------------------------------------------------------------- #
# 4. TICKET_SLOW: 1h + 1d at >= 3
# --------------------------------------------------------------------------- #


def test_ticket_slow_files_budget_ticket() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(3.5, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_DAY,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])

    plan = report.objectives[0]
    assert plan.overall_severity is BurnSeverity.TICKET_SLOW
    ids = [a.id for a in report.actions]
    assert "FILE_BUDGET_TICKET" in ids
    assert "PAGE_ONCALL" not in ids


# --------------------------------------------------------------------------- #
# 5. TICKET_TREND: 6h + 3d at >= 1
# --------------------------------------------------------------------------- #


def test_ticket_trend() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(1.5, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.SIX_HOURS,  _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.THREE_DAYS, _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])
    plan = report.objectives[0]
    assert plan.overall_severity is BurnSeverity.TICKET_TREND
    assert "FILE_BUDGET_TICKET" in {a.id for a in report.actions}


# --------------------------------------------------------------------------- #
# 6. Multi-objective PAGE_FAST -> OPEN_INCIDENT + MULTI_OBJECTIVE_PAGE
# --------------------------------------------------------------------------- #


def test_multi_objective_page_emits_open_incident_and_insight() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(15.0, slo)
    snap1 = _snap("P95 latency <= 3000ms", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
    ])
    snap2 = _snap("Error rate <= 1%", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap1, snap2])

    ids = [a.id for a in report.actions]
    assert "OPEN_INCIDENT" in ids
    assert "MULTI_OBJECTIVE_PAGE" in report.insights
    # OPEN_INCIDENT is P0 and so is PAGE_ONCALL: both should be ahead of any P1+
    p0_ids = {a.id for a in report.actions if a.priority == "P0"}
    assert "OPEN_INCIDENT" in p0_ids and "PAGE_ONCALL" in p0_ids


# --------------------------------------------------------------------------- #
# 7. Single-window high burn -> downgrade
# --------------------------------------------------------------------------- #


def test_single_window_high_burn_is_downgraded_with_reason() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(15.0, slo)
    snap = _snap("P95 latency <= 3000ms", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c)),
        # 1h sample deliberately missing
    ])
    report = advisor.assess([snap])
    plan = report.objectives[0]
    # PAGE_FAST downgraded one tier -> PAGE_MEDIUM
    assert plan.overall_severity is BurnSeverity.PAGE_MEDIUM
    reasons = " ".join(a.reason for a in plan.per_window)
    assert "no confirmation window" in reasons
    assert "INSUFFICIENT_DATA" in report.insights


# --------------------------------------------------------------------------- #
# 8. risk_appetite: borderline burn flips with cautious, stays with aggressive
# --------------------------------------------------------------------------- #


def test_risk_appetite_modulation() -> None:
    slo = 99.0
    # burn_rate ~13 < 14.4 baseline; >= 14.4*0.85 = 12.24 cautious; < 14.4*1.15=16.56 aggressive
    c = _burn_to_compliance(13.0, slo)
    snap = _snap("P95 latency <= 3000ms", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
    ])

    cautious = SLOBurnRateAdvisor(risk_appetite="cautious", now_fn=lambda: _FIXED_NOW).assess([snap])
    balanced = SLOBurnRateAdvisor(risk_appetite="balanced", now_fn=lambda: _FIXED_NOW).assess([snap])
    aggressive = SLOBurnRateAdvisor(risk_appetite="aggressive", now_fn=lambda: _FIXED_NOW).assess([snap])

    assert cautious.objectives[0].overall_severity is BurnSeverity.PAGE_FAST
    # Balanced: 13 < 14.4 PAGE_FAST; PAGE_MEDIUM tier short_w=30m not present;
    # TICKET_SLOW (>=3.0) on 1h matches but no 1d confirmation -> downgrade to TICKET_TREND.
    assert balanced.objectives[0].overall_severity is BurnSeverity.TICKET_TREND
    # aggressive: 13 < 14.4*1.15=16.56 and < 6*1.15=6.9 (no PAGE_MEDIUM either) and < 3*1.15=3.45 ... so TICKET_SLOW? no, 13>=3.45 -> TICKET_SLOW
    # Actually 13 >= 3.45 (TICKET_SLOW) and 1h confirms. So aggressive gives TICKET_SLOW or PAGE_MEDIUM?
    # PAGE_MEDIUM threshold aggressive = 6.9, 13>=6.9 -> but the tier is (30m, 6h) - 30m not present
    # Tier loop short_w=THIRTY_MIN; we don't have 30m. So PAGE_MEDIUM tier is skipped.
    # TICKET_SLOW tier short_w=1h, long_w=1d. 1h present (13>=3.45), 1d missing -> downgrade to TICKET_TREND.
    # Then TICKET_TREND tier short_w=6h: not present. So overall = TICKET_TREND.
    # But the 5m sample also matches PAGE_FAST short: 13 < 16.56 aggressive, so not matched. Good.
    # 5m also tries other tiers? No, only the tier with short_w==5m is PAGE_FAST.
    # So 5m stays WATCH. Overall worst = TICKET_TREND.
    assert aggressive.objectives[0].overall_severity in (
        BurnSeverity.TICKET_TREND,
        BurnSeverity.TICKET_SLOW,
        BurnSeverity.WATCH,
    )
    # Stricter than balanced
    assert cautious.objectives[0].overall_severity.rank <= balanced.objectives[0].overall_severity.rank
    assert balanced.objectives[0].overall_severity.rank <= aggressive.objectives[0].overall_severity.rank


# --------------------------------------------------------------------------- #
# 9. JSON byte-stability
# --------------------------------------------------------------------------- #


def test_render_json_is_byte_stable() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(7.0, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.THIRTY_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.SIX_HOURS,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])
    a = report.render_json()
    b = report.render_json()
    assert a == b
    # Valid JSON, sorted keys
    parsed = json.loads(a)
    assert isinstance(parsed, dict)


# --------------------------------------------------------------------------- #
# 10. Markdown rendering contains required headings
# --------------------------------------------------------------------------- #


def test_markdown_contains_playbook_and_matrix_sections() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(7.0, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.THIRTY_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.SIX_HOURS,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    md = advisor.assess([snap]).render_markdown()
    assert "## Playbook" in md
    assert "## Per-objective matrix" in md
    assert "## Objectives" in md
    assert "PAGE_MEDIUM" in md


# --------------------------------------------------------------------------- #
# 11. time_to_exhaustion math: budget_remaining=0.5, burn=2 -> 180h
# --------------------------------------------------------------------------- #


def test_time_to_exhaustion_math() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(2.0, slo)  # short burn = 2.0
    # budget_remaining_fraction = 0.5 -> tte = 0.5 / 2.0 * 720 = 180h
    r = _result(slo_percent=slo, compliance_percent=c, budget_remaining_fraction=0.5)
    snap = _snap("Error rate <= 1%", [(WindowLabel.SIX_HOURS, r)])
    report = advisor.assess([snap])
    plan = report.objectives[0]
    assert plan.time_to_exhaustion_hours is not None
    assert abs(plan.time_to_exhaustion_hours - 180.0) < 1e-6


# --------------------------------------------------------------------------- #
# 12. Empty snapshot list -> ValueError
# --------------------------------------------------------------------------- #


def test_empty_snapshot_list_raises() -> None:
    advisor = _advisor()
    with pytest.raises(ValueError, match="no snapshots provided"):
        advisor.assess([])


# --------------------------------------------------------------------------- #
# 13. Snapshot with no samples -> ValueError naming the objective
# --------------------------------------------------------------------------- #


def test_empty_samples_raises_with_objective_name() -> None:
    advisor = _advisor()
    snap = ObjectiveBurnSnapshot(objective_name="lonely_slo", samples=[])
    with pytest.raises(ValueError, match="lonely_slo"):
        advisor.assess([snap])


# --------------------------------------------------------------------------- #
# 14. invalid risk_appetite -> ValueError
# --------------------------------------------------------------------------- #


def test_invalid_risk_appetite_raises() -> None:
    with pytest.raises(ValueError, match="risk_appetite"):
        SLOBurnRateAdvisor(risk_appetite="paranoid")


# --------------------------------------------------------------------------- #
# 15. INSUFFICIENT_DATA fires for single-window burning objective
# --------------------------------------------------------------------------- #


def test_insufficient_data_insight() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(2.5, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])
    assert "INSUFFICIENT_DATA" in report.insights
    ids = {a.id for a in report.actions}
    assert "INCREASE_OBSERVABILITY" in ids


# --------------------------------------------------------------------------- #
# 16. Determinism modulo generated_at
# --------------------------------------------------------------------------- #


def test_determinism_modulo_generated_at() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(7.0, slo)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.THIRTY_MIN, _result(slo_percent=slo, compliance_percent=c)),
        (WindowLabel.SIX_HOURS,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    d1 = advisor.assess([snap]).to_dict()
    d2 = advisor.assess([snap]).to_dict()
    d1.pop("generated_at")
    d2.pop("generated_at")
    assert d1 == d2


# --------------------------------------------------------------------------- #
# 17. Per-objective BUDGET_EXHAUSTED insight
# --------------------------------------------------------------------------- #


def test_budget_exhausted_insight() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(2.0, slo)
    r_shortest = _result(slo_percent=slo, compliance_percent=c, budget_remaining_fraction=0.0)
    snap = _snap("Error rate <= 1%", [
        (WindowLabel.ONE_HOUR, r_shortest),
        (WindowLabel.ONE_DAY,  _result(slo_percent=slo, compliance_percent=c)),
    ])
    report = advisor.assess([snap])
    assert "BUDGET_EXHAUSTED" in report.objectives[0].insights
    assert "BUDGET_EXHAUSTED" in report.insights


# --------------------------------------------------------------------------- #
# 18. Actions are P0-first and deduped by id
# --------------------------------------------------------------------------- #


def test_actions_p0_first_and_deduped() -> None:
    advisor = _advisor()
    slo = 99.0
    c = _burn_to_compliance(15.0, slo)
    snap1 = _snap("A", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c, name="A")),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c, name="A")),
    ])
    snap2 = _snap("B", [
        (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=c, name="B")),
        (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=c, name="B")),
    ])
    report = advisor.assess([snap1, snap2])
    ids = [a.id for a in report.actions]
    # Dedup: PAGE_ONCALL once even though both objectives PAGE
    assert ids.count("PAGE_ONCALL") == 1
    # P0-first ordering
    priorities = [a.priority for a in report.actions]
    assert priorities == sorted(priorities, key=lambda p: {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[p])


# --------------------------------------------------------------------------- #
# 19. Force-F grade when PAGE_FAST present even if portfolio score < 60
# --------------------------------------------------------------------------- #


def test_force_f_grade_on_page_fast() -> None:
    advisor = _advisor()
    slo = 99.0
    page_c = _burn_to_compliance(15.0, slo)
    healthy_c = 100.0
    # 1 PAGE_FAST + 9 HEALTHY -> mean risk_score = 100/10 = 10 (< 60)
    snaps = [
        _snap("page1", [
            (WindowLabel.FIVE_MIN, _result(slo_percent=slo, compliance_percent=page_c, name="page1")),
            (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=page_c, name="page1")),
        ])
    ]
    for i in range(9):
        snaps.append(_snap(f"healthy{i}", [
            (WindowLabel.ONE_HOUR, _result(slo_percent=slo, compliance_percent=healthy_c, name=f"h{i}")),
        ]))
    report = advisor.assess(snaps)
    assert report.portfolio_risk_score < 60
    assert report.grade == "F"  # forced
