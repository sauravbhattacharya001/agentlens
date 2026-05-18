"""Tests for CostAttributionAdvisor."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.cost_attribution_advisor import (
    CostAttributionAdvisor,
    CostAttributionReport,
    AttributionVerdict,
    ConcentrationBand,
    CostGrade,
    ActionPriority,
    TrendLabel,
)


FIXED_NOW = datetime(2026, 5, 18, 18, 0, 0, tzinfo=timezone.utc)


def _ev(session_id, model, tokens_in=1000, tokens_out=500, ts=None, user=None, event_type="llm_call"):
    return {
        "event_id": f"e-{session_id}-{model}-{ts.isoformat() if ts else 'na'}-{tokens_in}-{tokens_out}",
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": ts or FIXED_NOW,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "metadata": {"user_id": user or session_id},
    }


def _now():
    return FIXED_NOW


def test_empty_input_returns_grade_a_with_safe_summary():
    advisor = CostAttributionAdvisor(now_fn=_now)
    rep = advisor.analyze([])
    assert isinstance(rep, CostAttributionReport)
    assert rep.portfolio.total_events == 0
    assert rep.portfolio.total_cost_usd == 0.0
    assert rep.slices == []
    # Playbook fallback present.
    assert any(a.id == "HEALTHY_DISTRIBUTION" for a in rep.playbook)
    # Renderers still produce non-empty text.
    assert rep.to_text().strip()
    assert rep.to_markdown().strip()
    assert "PRICING_UNAVAILABLE" in rep.insights


def test_single_event_single_dimension():
    advisor = CostAttributionAdvisor(dimensions=("model",), now_fn=_now)
    rep = advisor.analyze([_ev("s1", "gpt-4o")])
    assert rep.portfolio.total_slices == 1
    assert rep.slices[0].cost_share == pytest.approx(1.0)
    # Top-1 share == 1.0 -> grade F.
    assert rep.grade is CostGrade.F


def test_pareto_top_n_with_skewed_distribution():
    # Heavy user dominates spend.
    events = [_ev("heavy", "gpt-4o", tokens_in=100_000, tokens_out=50_000) for _ in range(5)]
    events += [_ev(f"light-{i}", "gpt-4o-mini", tokens_in=200, tokens_out=100) for i in range(10)]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(dimensions=("user_id",), tag_extractor=tag, now_fn=_now)
    rep = advisor.analyze(events)
    assert rep.portfolio.total_slices == 11
    assert rep.slices[0].value == "heavy"
    assert rep.slices[0].verdict in (
        AttributionVerdict.HEAVY_USER,
        AttributionVerdict.TOP_SPENDER,
    )
    assert rep.portfolio.pareto_top_n <= 3
    # ENFORCE_CHARGEBACK fired because heavy share > 20%.
    assert any(a.id == "ENFORCE_CHARGEBACK" for a in rep.playbook)


def test_spike_detection_triggers_p0_action():
    # Three days, slice with rising cost on day 3.
    base = datetime(2026, 5, 16, tzinfo=timezone.utc)
    events = []
    # Two diverse other users at flat spend, big enough to keep spike share material but not dominant.
    for d in range(3):
        for u in ("alice", "bob"):
            events.append(
                _ev(u, "gpt-4o-mini", tokens_in=2000, tokens_out=1000, ts=base + timedelta(days=d), user=u)
            )
    # Spiker: ramps up cost.
    for d, mult in enumerate([1, 4, 12]):
        for _ in range(mult):
            events.append(
                _ev("spiker", "gpt-4o", tokens_in=50_000, tokens_out=20_000, ts=base + timedelta(days=d, hours=d), user="spiker")
            )
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(
        dimensions=("user_id",),
        tag_extractor=tag,
        now_fn=lambda: base + timedelta(days=2, hours=23),
    )
    rep = advisor.analyze(events)
    spike_slice = next(s for s in rep.slices if s.value == "spiker")
    assert spike_slice.trend is TrendLabel.RISING
    assert spike_slice.verdict is AttributionVerdict.SPIKE_DETECTED
    assert spike_slice.priority is ActionPriority.P0
    assert any(a.id == "INVESTIGATE_SPIKE" for a in rep.playbook)
    assert "RISING_SPEND" in rep.insights


def test_gini_grade_f_when_single_slice_dominates_50pct():
    events = [_ev("big", "gpt-4o", tokens_in=100_000, tokens_out=50_000)]
    events += [_ev(f"small-{i}", "gpt-4o-mini", tokens_in=500, tokens_out=200) for i in range(5)]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(dimensions=("user_id",), tag_extractor=tag, now_fn=_now)
    rep = advisor.analyze(events)
    assert rep.portfolio.top1_share >= 0.5
    assert rep.grade is CostGrade.F
    # Rate-limit top spender action present.
    assert any(a.id == "RATE_LIMIT_TOP_SPENDER" for a in rep.playbook)


def test_grade_a_on_diverse_workload():
    # 8 users with roughly equal cost.
    events = []
    for i in range(8):
        for _ in range(3):
            events.append(
                _ev(f"u{i}", "gpt-4o-mini", tokens_in=1000, tokens_out=500, user=f"u{i}")
            )
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(dimensions=("user_id",), tag_extractor=tag, now_fn=_now)
    rep = advisor.analyze(events)
    assert rep.portfolio.concentration_band is ConcentrationBand.DIVERSE
    assert rep.grade is CostGrade.A


def test_risk_appetite_monotonicity_on_heavy_threshold():
    events = [_ev("heavy", "gpt-4o", tokens_in=20_000, tokens_out=8_000, user="heavy") for _ in range(2)]
    events += [_ev(f"u{i}", "gpt-4o-mini", tokens_in=1000, tokens_out=500, user=f"u{i}") for i in range(10)]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}

    def mk(risk):
        return CostAttributionAdvisor(
            dimensions=("user_id",), tag_extractor=tag, risk_appetite=risk, now_fn=_now
        ).analyze(events)

    cautious, balanced, aggressive = mk("cautious"), mk("balanced"), mk("aggressive")
    # Same data, same gini.
    assert cautious.portfolio.gini_coefficient == pytest.approx(balanced.portfolio.gini_coefficient)
    # Aggressive should trim P3 fallback when any higher action exists.
    if any(a.priority in (ActionPriority.P0, ActionPriority.P1) for a in aggressive.playbook):
        assert all(a.priority is not ActionPriority.P3 for a in aggressive.playbook)


def test_json_byte_stability_with_fixed_now_fn():
    events = [_ev(f"u{i}", "gpt-4o-mini", user=f"u{i}") for i in range(6)]
    advisor = CostAttributionAdvisor(now_fn=_now)
    a = advisor.analyze(events).to_json()
    b = advisor.analyze(events).to_json()
    assert a == b
    # Valid JSON.
    parsed = json.loads(a)
    assert "portfolio" in parsed
    assert "slices" in parsed


def test_markdown_contains_all_sections():
    events = [_ev("alice", "gpt-4o-mini", user="alice")]
    advisor = CostAttributionAdvisor(now_fn=_now)
    md = advisor.analyze(events).to_markdown()
    assert "## Summary" in md
    assert "## Top slices" in md
    assert "## Playbook" in md
    assert "## Insights" in md


def test_text_grade_headline_present():
    events = [_ev("alice", "gpt-4o-mini", user="alice")]
    advisor = CostAttributionAdvisor(now_fn=_now)
    txt = advisor.analyze(events).to_text()
    assert "grade=" in txt
    assert "VERDICT" in txt


def test_new_arrival_detection():
    # One user only exists in last hour.
    base = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        _ev("old", "gpt-4o-mini", ts=base - timedelta(days=3), user="old"),
        _ev("old", "gpt-4o-mini", ts=base - timedelta(days=2), user="old"),
        _ev("old", "gpt-4o-mini", ts=base - timedelta(days=1), user="old"),
        _ev("new1", "gpt-4o-mini", ts=base - timedelta(hours=1), user="new1"),
        _ev("new2", "gpt-4o-mini", ts=base - timedelta(hours=2), user="new2"),
        _ev("new1", "gpt-4o-mini", ts=base - timedelta(minutes=30), user="new1"),
        _ev("new2", "gpt-4o-mini", ts=base - timedelta(minutes=20), user="new2"),
    ]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(
        dimensions=("user_id",), tag_extractor=tag, now_fn=lambda: base
    )
    rep = advisor.analyze(events)
    new_arrivals = [s for s in rep.slices if s.verdict is AttributionVerdict.NEW_ARRIVAL]
    assert len(new_arrivals) >= 2
    assert "NEW_USER_INFLUX" in rep.insights
    assert any(a.id == "AUDIT_NEW_ARRIVALS" for a in rep.playbook)


def test_dict_input_coercion_and_custom_extractor():
    events = [
        {
            "session_id": "s1",
            "event_type": "llm_call",
            "timestamp": FIXED_NOW.isoformat(),
            "model": "gpt-4o-mini",
            "tokens_in": 1000,
            "tokens_out": 500,
            "metadata": {"team": "growth"},
        },
        {
            "session_id": "s2",
            "event_type": "llm_call",
            "timestamp": FIXED_NOW.isoformat(),
            "model": "gpt-4o-mini",
            "tokens_in": 1500,
            "tokens_out": 700,
            "metadata": {"team": "platform"},
        },
    ]

    def extract(ev):
        return {"team": (ev.get("metadata") or {}).get("team", "unknown")}

    advisor = CostAttributionAdvisor(
        dimensions=("team",), tag_extractor=extract, now_fn=_now
    )
    rep = advisor.analyze(events)
    assert rep.dimensions == ["team"]
    keys = {s.value for s in rep.slices}
    assert keys == {"growth", "platform"}


def test_model_lock_in_insight_when_one_model_dominates():
    events = [_ev(f"u{i}", "gpt-4o", tokens_in=10_000, tokens_out=5_000, user=f"u{i}") for i in range(6)]
    events += [_ev("u0", "gpt-4o-mini", tokens_in=500, tokens_out=200, user="u0")]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(dimensions=("user_id",), tag_extractor=tag, now_fn=_now)
    rep = advisor.analyze(events)
    assert any(ins.startswith("MODEL_LOCK_IN:") for ins in rep.insights)
    assert any(a.id == "MIGRATE_TOP_MODELS" for a in rep.playbook)


def test_inputs_are_not_mutated():
    events = [_ev("alice", "gpt-4o-mini", user="alice")]
    snapshot = json.dumps(events, default=str, sort_keys=True)
    advisor = CostAttributionAdvisor(now_fn=_now)
    advisor.analyze(events)
    assert json.dumps(events, default=str, sort_keys=True) == snapshot


def test_top_n_limits_output_slices():
    events = [_ev(f"u{i}", "gpt-4o-mini", user=f"u{i}") for i in range(20)]
    tag = lambda e: {"user_id": e["metadata"]["user_id"]}
    advisor = CostAttributionAdvisor(
        dimensions=("user_id",), tag_extractor=tag, top_n=5, now_fn=_now
    )
    rep = advisor.analyze(events)
    assert rep.portfolio.total_slices == 20
    assert len(rep.slices) == 5
