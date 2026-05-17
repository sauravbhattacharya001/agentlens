"""Tests for agentlens.incident_radar.IncidentRiskRadar."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agentlens.anomaly import (
    Anomaly,
    AnomalyKind,
    AnomalyReport,
    AnomalySeverity,
)
from agentlens.budget import BudgetReport, BudgetStatus
from agentlens.drift import DriftReport, DriftStatus
from agentlens.error_fingerprint import ErrorCluster, ErrorReport, Resolution, Trend
from agentlens.health import HealthGrade, HealthReport
from agentlens.latency import SessionReport as LatencySessionReport
from agentlens.retry_tracker import RetryReport, RetryStorm

from agentlens.incident_radar import (
    ActionPriority,
    IncidentRiskRadar,
    RadarInputs,
    RiskBand,
)


# --------------------------------------------------------------------------- #
# Builders for minimal report fixtures
# --------------------------------------------------------------------------- #


def _anom(crit: int = 0, warn: int = 0) -> AnomalyReport:
    anomalies = []
    for _ in range(crit):
        anomalies.append(Anomaly(
            kind=AnomalyKind.LATENCY_SPIKE,
            severity=AnomalySeverity.CRITICAL,
            metric_name="duration_ms",
            observed=1000.0, expected=200.0, std_dev=50.0, z_score=16.0,
            description="latency p95 spiked",
        ))
    for _ in range(warn):
        anomalies.append(Anomaly(
            kind=AnomalyKind.ERROR_BURST,
            severity=AnomalySeverity.WARNING,
            metric_name="error_rate",
            observed=0.1, expected=0.02, std_dev=0.02, z_score=4.0,
            description="error burst",
        ))
    return AnomalyReport(session_id="s1", anomalies=anomalies)


def _drift(status: DriftStatus, score: int = 50,
           drifting: list[str] | None = None) -> DriftReport:
    return DriftReport(
        drift_score=score,
        status=status,
        baseline_sessions=10,
        current_sessions=10,
        drifting_metrics=list(drifting or []),
        summary="test drift",
    )


def _errors(total: int = 0, unique: int = 0, sessions: int = 0,
            rising_clusters: int = 0) -> ErrorReport:
    clusters = []
    for i in range(rising_clusters):
        clusters.append(ErrorCluster(
            fingerprint_id=f"fp{i}",
            error_type="ValueError",
            template="bad input",
            frame_signature="frame",
            occurrence_count=5,
            trend=Trend.RISING,
            resolution=Resolution.OPEN,
        ))
    return ErrorReport(
        unique_count=unique or rising_clusters,
        total_count=total,
        top_clusters=clusters,
        sessions_affected=sessions,
    )


def _retries(total: int = 0, storms: int = 0,
             retry_rate: float = 0.0) -> RetryReport:
    storm_list = []
    for i in range(storms):
        storm_list.append(RetryStorm(
            session_id=f"sess{i}",
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-01-01T00:01:00Z",
            retry_count=10,
            unique_chains=3,
            dominant_error="TimeoutError",
            affected_tools=["search"],
            affected_models=["gpt-4o"],
        ))
    return RetryReport(
        total_events=max(total, 1),
        total_retries=total,
        retry_rate=retry_rate,
        chains=[],
        success_rate=1.0 - retry_rate,
        avg_attempts=1.5,
        max_attempts=3,
        retry_tax_tokens=0,
        retry_tax_duration_ms=0.0,
        retries_by_type={},
        retries_by_tool={},
        retries_by_model={},
        retries_by_error={},
        storms=storm_list,
        recommendations=[],
    )


def _latency(failed: int = 0, bottleneck_pct: float | None = None,
             bottleneck_name: str | None = None) -> LatencySessionReport:
    return LatencySessionReport(
        session_id="s1",
        label="run",
        total_duration_s=1.0,
        step_count=5,
        completed_count=5 - failed,
        failed_count=failed,
        bottleneck_name=bottleneck_name,
        bottleneck_duration_ms=None if bottleneck_pct is None else 500.0,
        bottleneck_pct=bottleneck_pct,
        steps=[],
        created_at=datetime.now(timezone.utc),
    )


def _budget(util: float = 0.0, status: BudgetStatus = BudgetStatus.ACTIVE,
            cost: float = 0.0) -> BudgetReport:
    return BudgetReport(
        budget_id="b1",
        session_id="s1",
        agent_name="agent",
        status=status,
        total_tokens=int(util * 1000),
        total_tokens_in=int(util * 500),
        total_tokens_out=int(util * 500),
        total_cost_usd=cost,
        max_tokens=1000,
        max_cost_usd=10.0,
        token_utilization=util,
        cost_utilization=util,
        utilization=util,
        remaining_tokens=int((1 - util) * 1000),
        remaining_cost=10.0 - cost,
        warn_at=0.8,
        hard_limit=True,
        entry_count=1,
        created_at=datetime.now(timezone.utc),
        model="gpt-4o",
    )


def _health(overall: float = 95.0,
            grade: HealthGrade = HealthGrade.EXCELLENT,
            errors: int = 0) -> HealthReport:
    return HealthReport(
        session_id="s1",
        overall_score=overall,
        grade=grade,
        metrics=[],
        recommendations=[],
        event_count=10,
        error_count=errors,
        total_tokens=100,
        total_duration_ms=1000.0,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_empty_inputs_are_calm():
    rep = IncidentRiskRadar().assess(RadarInputs())
    assert rep.fused_score == 0.0
    assert rep.band is RiskBand.CALM
    assert rep.signals == []
    assert rep.actions == []


def test_only_excellent_health_stays_calm_or_watch():
    rep = IncidentRiskRadar().assess(RadarInputs(
        health_report=_health(overall=95.0, grade=HealthGrade.EXCELLENT),
    ))
    # health score = 5, fused = 5 -> CALM
    assert rep.band is RiskBand.CALM
    assert len(rep.signals) == 1
    assert rep.signals[0].source == "health"


def test_critical_anomalies_push_to_critical_with_oncall():
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=2),
    ))
    # score = min(100, 120) = 100, single signal -> fused=100 -> CRITICAL
    assert rep.band is RiskBand.CRITICAL
    keys = [a.key for a in rep.actions]
    assert "page_oncall" in keys
    assert "snapshot_state" in keys
    # rollback triggered because anomaly>=80
    assert "rollback_last_change" in keys


def test_drift_significant_triggers_elevated_and_rollback():
    rep = IncidentRiskRadar().assess(RadarInputs(
        drift_report=_drift(DriftStatus.SIGNIFICANT_DRIFT,
                            score=80, drifting=["latency", "error_rate"]),
    ))
    # status name SIGNIFICANT_DRIFT -> 90 -> CRITICAL band
    assert rep.band is RiskBand.CRITICAL
    keys = [a.key for a in rep.actions]
    assert "rollback_last_change" in keys
    assert "freeze_deploys" in keys


def test_budget_high_util_triggers_raise_budget():
    rep = IncidentRiskRadar().assess(RadarInputs(
        budget_report=_budget(util=0.95, status=BudgetStatus.WARNING),
    ))
    # score = 0.95 * 110 = 104.5 capped at 100 -> CRITICAL
    assert rep.band is RiskBand.CRITICAL
    keys = [a.key for a in rep.actions]
    assert "raise_budget" in keys


def test_latency_failures_and_bottleneck_recommend_scale_and_throttle():
    rep = IncidentRiskRadar().assess(RadarInputs(
        latency_report=_latency(failed=3, bottleneck_pct=60.0,
                                bottleneck_name="search"),
    ))
    # score = 15*3 + 60 = 105 -> capped 100 -> CRITICAL
    assert rep.band is RiskBand.CRITICAL
    keys = [a.key for a in rep.actions]
    assert "scale_up_capacity" in keys
    assert "throttle_traffic" in keys


def test_cautious_appetite_scores_higher_than_aggressive_on_leading():
    inputs = lambda app: RadarInputs(
        anomaly_report=_anom(crit=1),       # leading, score=60
        health_report=_health(overall=80),  # trailing, score=20
        risk_appetite=app,
    )
    cautious = IncidentRiskRadar("cautious").assess(inputs("cautious"))
    aggressive = IncidentRiskRadar("aggressive").assess(inputs("aggressive"))
    assert cautious.fused_score > aggressive.fused_score


def test_action_deduplication():
    # Many overlapping signals -> no duplicate keys.
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=2),
        drift_report=_drift(DriftStatus.SIGNIFICANT_DRIFT, score=90,
                            drifting=["m1"]),
        error_report=_errors(total=20, unique=3, sessions=2),
        retry_report=_retries(total=20, storms=2, retry_rate=0.3),
        latency_report=_latency(failed=2, bottleneck_pct=70.0,
                                bottleneck_name="x"),
        budget_report=_budget(util=0.9, status=BudgetStatus.WARNING),
        health_report=_health(overall=20, grade=HealthGrade.CRITICAL,
                              errors=5),
    ))
    keys = [a.key for a in rep.actions]
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"


def test_action_ordering_p0_before_p1():
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=2),
        retry_report=_retries(total=20, storms=2, retry_rate=0.3),
        budget_report=_budget(util=0.95, status=BudgetStatus.WARNING),
    ))
    priorities = [a.priority for a in rep.actions]
    # No P1 should appear before any P0
    seen_p1 = False
    for p in priorities:
        if p is ActionPriority.P1:
            seen_p1 = True
        elif p is ActionPriority.P0:
            assert not seen_p1, "P1 appeared before a P0"


def test_renderers_contain_band_and_score_and_json_roundtrips():
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=1),
        budget_report=_budget(util=0.5),
    ))
    text = rep.render_text()
    md = rep.render_markdown()
    assert rep.band.value.upper() in text
    assert rep.band.value.upper() in md
    assert f"{rep.fused_score:.1f}" in text
    assert f"{rep.fused_score:.1f}" in md

    payload = rep.render_json()
    parsed = json.loads(payload)
    assert parsed["band"] == rep.band.value
    assert parsed["fused_score"] == round(rep.fused_score, 2)
    assert parsed["window_label"] == rep.window_label


def test_top_actions_truncates_to_n():
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=2),
        drift_report=_drift(DriftStatus.SIGNIFICANT_DRIFT, score=90),
        retry_report=_retries(total=10, storms=1, retry_rate=0.2),
        budget_report=_budget(util=0.95, status=BudgetStatus.WARNING),
    ))
    top2 = rep.top_actions(2)
    assert len(top2) <= 2
    assert top2 == rep.actions[:2]


def test_poor_health_only_reaches_at_least_watch():
    rep = IncidentRiskRadar().assess(RadarInputs(
        health_report=_health(overall=30, grade=HealthGrade.POOR, errors=2),
    ))
    # health score = 70 -> single-signal fused=70 -> HIGH
    assert rep.band in (RiskBand.WATCH, RiskBand.ELEVATED, RiskBand.HIGH)
    assert rep.signals[0].source == "health"
    assert rep.signals[0].score == pytest.approx(70.0)


def test_summary_format():
    rep = IncidentRiskRadar().assess(RadarInputs(
        anomaly_report=_anom(crit=2),
    ))
    s = rep.summary
    assert s.startswith(f"[{rep.band.value.upper()}]")
    assert "fused" in s


def test_band_boundaries():
    assert RiskBand.from_score(0) is RiskBand.CALM
    assert RiskBand.from_score(19.9) is RiskBand.CALM
    assert RiskBand.from_score(20.0) is RiskBand.WATCH
    assert RiskBand.from_score(39.9) is RiskBand.WATCH
    assert RiskBand.from_score(40.0) is RiskBand.ELEVATED
    assert RiskBand.from_score(60.0) is RiskBand.HIGH
    assert RiskBand.from_score(79.9) is RiskBand.HIGH
    assert RiskBand.from_score(80.0) is RiskBand.CRITICAL
    assert RiskBand.from_score(100.0) is RiskBand.CRITICAL


def test_invalid_appetite_raises():
    with pytest.raises(ValueError):
        IncidentRiskRadar("paranoid")
    with pytest.raises(ValueError):
        IncidentRiskRadar().assess(RadarInputs(risk_appetite="paranoid"))


def test_errors_rising_clusters_boost_score():
    base = IncidentRiskRadar._score_errors(_errors(total=5, unique=2,
                                                   sessions=2))
    boosted = IncidentRiskRadar._score_errors(_errors(
        total=5, unique=2, sessions=2, rising_clusters=1))
    assert boosted.score > base.score
