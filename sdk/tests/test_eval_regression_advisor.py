"""Tests for EvalRegressionAdvisor."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.eval_regression_advisor import (
    ActionPriority,
    EvalRegressionAdvisor,
    EvalRegressionOptions,
    EvalRegressionReport,
    RegressionGrade,
    RegressionVerdict,
    RiskAppetite,
)


FIXED_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


def _ev(
    i: int,
    *,
    model: str = "gpt-4o-mini",
    tool: str = "search",
    duration_ms: float = 800.0,
    tokens_in: int = 500,
    tokens_out: int = 100,
    is_error: bool = False,
    ts: datetime | None = None,
) -> dict:
    return {
        "event_id": f"ev-{i}",
        "session_id": f"s{i}",
        "event_type": "llm_call",
        "timestamp": ts or (FIXED_NOW - timedelta(minutes=60 - i)),
        "model": model,
        "tool": tool,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_ms": duration_ms,
        "is_error": is_error,
    }


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #


def test_empty_inputs_grade_a():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    r = advisor.analyze([], [])
    assert isinstance(r, EvalRegressionReport)
    assert r.portfolio.total_call_sites == 0
    assert r.portfolio.grade is RegressionGrade.A
    assert any(a.id == "no_regression_action_needed" for a in r.playbook)
    assert "NO_TRAFFIC" in r.insights


def test_stable_no_regression():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, duration_ms=800, is_error=False) for i in range(20)]
    c = [_ev(i + 100, duration_ms=820, is_error=False) for i in range(20)]
    r = advisor.analyze(b, c)
    assert len(r.slices) == 1
    s = r.slices[0]
    assert s.verdict is RegressionVerdict.STABLE
    assert s.priority is ActionPriority.P3
    assert r.portfolio.grade is RegressionGrade.A


def test_major_latency_regression_triggers_p0():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, duration_ms=500) for i in range(20)]
    c = [_ev(i + 100, duration_ms=1500) for i in range(20)]
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert s.verdict is RegressionVerdict.MAJOR_REGRESSION
    assert s.priority is ActionPriority.P0
    assert "LATENCY_P95_MAJOR" in s.reason_codes
    assert any(a.id == "rollback_or_freeze_deploys" for a in r.playbook)
    assert any(a.id == "page_oncall_for_regression" for a in r.playbook)
    assert r.portfolio.grade in (RegressionGrade.D, RegressionGrade.F)


def test_error_rate_spike_triggers_triage():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, is_error=False) for i in range(20)]
    # 25% errors current
    c = [_ev(i + 100, is_error=(i % 4 == 0)) for i in range(20)]
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert "ERROR_RATE_SPIKE" in s.reason_codes
    assert s.priority is ActionPriority.P0
    assert any(a.id == "triage_error_spike" for a in r.playbook)


def test_minor_regression_triggers_p1():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, duration_ms=500) for i in range(20)]
    c = [_ev(i + 100, duration_ms=750) for i in range(20)]  # +50% p95
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert s.verdict is RegressionVerdict.REGRESSION
    assert s.priority is ActionPriority.P1
    assert any(a.id == "raise_timeouts_or_optimize" for a in r.playbook)


def test_new_call_site_with_errors_is_new_failure_mode():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, tool="search") for i in range(20)]
    c_old = [_ev(i + 100, tool="search") for i in range(20)]
    c_new = [_ev(i + 200, tool="extract", is_error=(i % 3 == 0)) for i in range(10)]
    r = advisor.analyze(b, c_old + c_new)
    new_slice = next(s for s in r.slices if s.tool == "extract")
    assert new_slice.verdict is RegressionVerdict.NEW_FAILURE_MODE
    assert new_slice.priority is ActionPriority.P0
    assert any(a.id == "investigate_new_failure_mode" for a in r.playbook)


def test_disappeared_site():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b_search = [_ev(i, tool="search") for i in range(20)]
    b_extract = [_ev(i + 100, tool="extract") for i in range(20)]
    c = [_ev(i + 200, tool="search") for i in range(20)]
    r = advisor.analyze(b_search + b_extract, c)
    gone = next(s for s in r.slices if s.tool == "extract")
    assert gone.verdict is RegressionVerdict.DISAPPEARED
    assert any(a.id == "confirm_intentional_retirement" for a in r.playbook)


def test_tokens_in_inflation_flagged():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, tokens_in=500) for i in range(20)]
    c = [_ev(i + 100, tokens_in=800) for i in range(20)]  # +60%
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert "TOKENS_IN_INFLATED" in s.reason_codes
    assert s.priority in (ActionPriority.P1, ActionPriority.P0)
    assert any(a.id == "audit_prompt_growth" for a in r.playbook)


def test_improvement_surfaces():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, duration_ms=1500) for i in range(20)]
    c = [_ev(i + 100, duration_ms=500) for i in range(20)]
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert s.verdict is RegressionVerdict.IMPROVEMENT
    assert s.priority is ActionPriority.P3
    assert r.portfolio.grade is RegressionGrade.A


def test_insufficient_data_when_small_samples():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i) for i in range(3)]
    c = [_ev(i + 100, duration_ms=4000) for i in range(3)]
    r = advisor.analyze(b, c)
    s = r.slices[0]
    assert s.verdict is RegressionVerdict.INSUFFICIENT_DATA


def test_risk_appetite_shifts_thresholds():
    """Borderline regression should fire under cautious but not aggressive."""
    b = [_ev(i, duration_ms=500) for i in range(20)]
    c = [_ev(i + 100, duration_ms=650) for i in range(20)]  # +30% p95
    advisor = EvalRegressionAdvisor(now_fn=_now)
    cautious = advisor.analyze(b, c, risk_appetite="cautious")
    aggressive = advisor.analyze(b, c, risk_appetite="aggressive")
    assert any("LATENCY" in r for r in cautious.slices[0].reason_codes)
    # Aggressive should be more tolerant -> no LATENCY regression reason
    assert not any("LATENCY_P95_REGRESSED" in r for r in aggressive.slices[0].reason_codes) \
        or aggressive.slices[0].priority.value > cautious.slices[0].priority.value


def test_json_renderer_is_byte_stable():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i) for i in range(10)]
    c = [_ev(i + 100, duration_ms=1500) for i in range(10)]
    r1 = advisor.analyze(b, c)
    r2 = advisor.analyze(b, c)
    s1 = r1.to_json()
    s2 = r2.to_json()
    assert s1 == s2
    parsed = json.loads(s1)
    assert "portfolio" in parsed
    assert "slices" in parsed
    assert "playbook" in parsed


def test_markdown_has_all_sections():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i) for i in range(10)]
    c = [_ev(i + 100, duration_ms=1500) for i in range(10)]
    md = advisor.analyze(b, c).to_markdown()
    assert "## Summary" in md
    assert "## Call-sites" in md
    assert "## Playbook" in md
    assert "## Insights" in md


def test_text_renderer_has_headline():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    txt = advisor.analyze([], []).to_text()
    assert txt.startswith("VERDICT:")


def test_does_not_mutate_inputs():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i) for i in range(10)]
    c = [_ev(i + 100, duration_ms=1500) for i in range(10)]
    b_snap = json.dumps(b, default=str, sort_keys=True)
    c_snap = json.dumps(c, default=str, sort_keys=True)
    advisor.analyze(b, c)
    assert json.dumps(b, default=str, sort_keys=True) == b_snap
    assert json.dumps(c, default=str, sort_keys=True) == c_snap


def test_custom_key_fn_buckets_correctly():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i) for i in range(10)]
    c = [_ev(i + 100) for i in range(10)]
    r = advisor.analyze(b, c, key_fn=lambda e: "single_bucket")
    assert len(r.slices) == 1
    assert r.slices[0].key == "single_bucket"


def test_multiple_p0_triggers_grade_f():
    advisor = EvalRegressionAdvisor(now_fn=_now)
    b = [_ev(i, tool="t1") for i in range(20)] + [_ev(i + 50, tool="t2") for i in range(20)]
    c = (
        [_ev(i + 100, tool="t1", duration_ms=3000) for i in range(20)]  # major lat
        + [_ev(i + 200, tool="t2", is_error=(i % 3 == 0)) for i in range(20)]  # err spike
    )
    r = advisor.analyze(b, c)
    assert r.portfolio.grade is RegressionGrade.F
    assert r.portfolio.p0_count >= 2
    assert "MULTIPLE_P0_REGRESSIONS" in r.insights


def test_sdk_exposes_advisor():
    import agentlens
    assert hasattr(agentlens, "EvalRegressionAdvisor")
    assert hasattr(agentlens, "EvalRegressionReport")
    assert hasattr(agentlens, "RegressionVerdict")
