"""Tests for the agentic ModelMigrationAdvisor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import json

import pytest

from agentlens import (
    AgentEvent,
    ModelMigrationAdvisor,
    MigrationVerdict,
    MigrationPriority,
    RollbackRisk,
)
from agentlens.models import ToolCall, DecisionTrace


FIXED_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return FIXED_NOW


def _ev(
    *,
    model: str = "gpt-4o",
    event_type: str = "llm_call",
    tokens_in: int = 100,
    tokens_out: int = 50,
    duration_ms: float = 500.0,
    tool_name: str | None = None,
    decision: bool = False,
    seconds_offset: float = 0.0,
) -> AgentEvent:
    tc = ToolCall(tool_name=tool_name) if tool_name else None
    dt = DecisionTrace(reasoning="why") if decision else None
    return AgentEvent(
        session_id="s1",
        event_type=event_type,
        timestamp=FIXED_NOW + timedelta(seconds=seconds_offset),
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        tool_call=tc,
        decision_trace=dt,
    )


# ---------------------------------------------------------------------------
# basic shape & happy path
# ---------------------------------------------------------------------------


def test_empty_events_returns_grade_A_with_zero_cost():
    adv = ModelMigrationAdvisor(now=_now)
    r = adv.recommend([], from_model="gpt-4o", to_model="gpt-4o-mini")
    assert r.sites == []
    assert r.playbook == []
    assert r.portfolio_grade == "A"
    assert r.portfolio_cost_per_day_old_usd == 0.0
    assert r.portfolio_cost_per_day_new_usd == 0.0


def test_simple_cheap_swap_is_migrate_now_P0():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(20)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    assert len(r.sites) == 1
    site = r.sites[0]
    assert site.verdict == MigrationVerdict.MIGRATE_NOW
    assert site.priority == MigrationPriority.P0
    assert site.projected_cost_delta_usd < 0
    assert site.rollback_risk == RollbackRisk.LOW
    assert r.portfolio_grade == "A"


def test_only_relevant_model_is_profiled():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(model="gpt-4o") for _ in range(5)] + [_ev(model="claude-3-opus") for _ in range(5)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    assert len(r.sites) == 1
    assert r.sites[0].profile.model == "gpt-4o"
    assert r.sites[0].profile.call_count == 5


# ---------------------------------------------------------------------------
# context-window handling
# ---------------------------------------------------------------------------


def test_oversize_call_blocks_migration():
    adv = ModelMigrationAdvisor(now=_now)
    # gpt-3.5-turbo is max_context 16k in defaults; a 32k call exceeds it
    evs = [_ev(tokens_in=32_000, tokens_out=200) for _ in range(3)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-3.5-turbo")
    assert r.sites[0].verdict == MigrationVerdict.BLOCK
    codes = {r2.code for r2 in r.sites[0].reasons}
    assert "EXCEEDS_NEW_CONTEXT" in codes
    # playbook MUST include KEEP_ON_LEGACY
    codes_pb = [a.code for a in r.playbook]
    assert "KEEP_ON_LEGACY" in codes_pb


def test_context_pressure_short_of_block_is_flagged():
    adv = ModelMigrationAdvisor(now=_now)
    # 14k tokens vs 16k context -> ~87% utilisation -> CONTEXT_PRESSURE
    evs = [_ev(tokens_in=14_000, tokens_out=80) for _ in range(3)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-3.5-turbo")
    codes = {r2.code for r2 in r.sites[0].reasons}
    assert "CONTEXT_PRESSURE" in codes
    assert r.sites[0].verdict != MigrationVerdict.BLOCK


# ---------------------------------------------------------------------------
# tool / decision density
# ---------------------------------------------------------------------------


def test_tool_heavy_site_gets_flagged_and_high_rollback_risk():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(tool_name="search") for _ in range(10)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    site = r.sites[0]
    codes = {x.code for x in site.reasons}
    assert "TOOL_HEAVY" in codes
    assert site.rollback_risk == RollbackRisk.HIGH


def test_decision_heavy_site_gets_flagged():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(decision=True) for _ in range(10)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    codes = {x.code for x in r.sites[0].reasons}
    assert "DECISION_HEAVY" in codes


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_high_error_rate_increases_risk():
    adv = ModelMigrationAdvisor(now=_now)
    ok = [_ev() for _ in range(7)]
    bad = [_ev(event_type="error") for _ in range(3)]
    r = adv.recommend(ok + bad, from_model="gpt-4o", to_model="gpt-4o-mini")
    codes = {x.code for x in r.sites[0].reasons}
    assert "HIGH_ERROR_RATE" in codes
    assert r.sites[0].profile.error_rate == pytest.approx(0.3)


def test_only_errors_observed_becomes_review():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(event_type="error") for _ in range(5)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    assert r.sites[0].verdict == MigrationVerdict.REVIEW
    assert r.sites[0].risk_score >= 60


# ---------------------------------------------------------------------------
# latency
# ---------------------------------------------------------------------------


def test_latency_sensitive_with_slower_target_flagged():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(duration_ms=5000.0) for _ in range(5)]
    # claude-3-opus has speed_factor 1.4 vs gpt-4o 1.0 -> slower target
    r = adv.recommend(evs, from_model="gpt-4o", to_model="claude-3-opus")
    site = r.sites[0]
    codes = {x.code for x in site.reasons}
    assert "LATENCY_SENSITIVE" in codes
    assert site.projected_latency_delta_pct is not None
    assert site.projected_latency_delta_pct > 0


# ---------------------------------------------------------------------------
# risk-appetite monotonicity
# ---------------------------------------------------------------------------


def test_cautious_yields_higher_or_equal_risk_than_aggressive():
    evs = [_ev(decision=True) for _ in range(8)]
    cautious = ModelMigrationAdvisor(now=_now, risk_appetite="cautious")
    aggressive = ModelMigrationAdvisor(now=_now, risk_appetite="aggressive")
    rc = cautious.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    ra = aggressive.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    assert rc.sites[0].risk_score >= ra.sites[0].risk_score


def test_risk_appetite_validated():
    with pytest.raises(ValueError):
        ModelMigrationAdvisor(risk_appetite="reckless")


# ---------------------------------------------------------------------------
# ordering & priority
# ---------------------------------------------------------------------------


def test_sites_sorted_by_priority_then_savings():
    adv = ModelMigrationAdvisor(now=_now)
    cheap = [_ev() for _ in range(10)]              # MIGRATE_NOW, big saving
    tooly = [_ev(tool_name="x") for _ in range(10)] # higher risk
    r = adv.recommend(cheap + tooly, from_model="gpt-4o", to_model="gpt-4o-mini")
    priorities = [s.priority for s in r.sites]
    # P0 sites must come before P1/P2
    indexes = {p: i for i, p in enumerate(priorities)}
    if MigrationPriority.P0 in indexes and MigrationPriority.P2 in indexes:
        assert indexes[MigrationPriority.P0] < indexes[MigrationPriority.P2]


# ---------------------------------------------------------------------------
# JSON byte-stability
# ---------------------------------------------------------------------------


def test_json_is_deterministic_with_fixed_now():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(10)] + [_ev(tool_name="t") for _ in range(5)]
    a = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini").format_json()
    b = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini").format_json()
    assert a == b
    # parses
    obj = json.loads(a)
    assert obj["from_model"] == "gpt-4o"
    assert obj["to_model"] == "gpt-4o-mini"
    assert "sites" in obj
    assert "playbook" in obj


# ---------------------------------------------------------------------------
# horizon scaling
# ---------------------------------------------------------------------------


def test_horizon_days_scales_cost_linearly():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(10)]
    r1 = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini", horizon_days=1.0)
    r7 = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini", horizon_days=7.0)
    assert r7.portfolio_cost_per_day_old_usd == pytest.approx(r1.portfolio_cost_per_day_old_usd * 7.0)


def test_invalid_horizon_raises():
    adv = ModelMigrationAdvisor(now=_now)
    with pytest.raises(ValueError):
        adv.recommend([], from_model="a", to_model="b", horizon_days=0)


def test_empty_model_names_raise():
    adv = ModelMigrationAdvisor(now=_now)
    with pytest.raises(ValueError):
        adv.recommend([], from_model="", to_model="b")


# ---------------------------------------------------------------------------
# model_specs override
# ---------------------------------------------------------------------------


def test_model_specs_override_used_when_pricing_missing():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev(model="unknown-x") for _ in range(5)]
    r = adv.recommend(
        evs,
        from_model="unknown-x",
        to_model="unknown-y",
        model_specs={
            "unknown-x": {"input_per_1m": 10.0, "output_per_1m": 30.0, "max_context": 8000, "speed_factor": 1.0},
            "unknown-y": {"input_per_1m": 1.0,  "output_per_1m": 3.0,  "max_context": 8000, "speed_factor": 1.0},
        },
    )
    assert r.portfolio_cost_delta_usd < 0


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------


def test_text_render_mentions_grade_and_each_site():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(5)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    text = r.format_text()
    assert "ModelMigrationAdvisor" in text
    assert "grade=" in text
    assert r.sites[0].site_id in text


def test_markdown_render_has_table_header():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(5)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    md = r.format_markdown()
    assert "# Model migration:" in md
    assert "| # | Priority |" in md


# ---------------------------------------------------------------------------
# by_priority
# ---------------------------------------------------------------------------


def test_by_priority_returns_only_that_bucket():
    adv = ModelMigrationAdvisor(now=_now)
    evs = [_ev() for _ in range(5)]
    r = adv.recommend(evs, from_model="gpt-4o", to_model="gpt-4o-mini")
    p0 = r.by_priority(MigrationPriority.P0)
    for s in p0:
        assert s.priority == MigrationPriority.P0
    # string form works too
    assert r.by_priority("P0") == p0
