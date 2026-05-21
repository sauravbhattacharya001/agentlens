"""Tests for CacheabilityAdvisor."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.cacheability_advisor import (
    ActionPriority,
    CacheabilityAdvisor,
    CacheabilityGrade,
    CacheabilityOptions,
    CacheabilityReport,
    CacheabilityVerdict,
    RiskAppetite,
)


FIXED_NOW = datetime(2026, 5, 21, 15, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


def _ev(
    *,
    session_id: str = "s1",
    model: str = "gpt-4o",
    system_prompt: str = "",
    user_prompt: str = "hi",
    tokens_in: int = 1500,
    tokens_out: int = 200,
    event_type: str = "llm_call",
) -> dict:
    return {
        "event_id": f"e-{session_id}-{tokens_in}-{tokens_out}-{hash((system_prompt, user_prompt)) & 0xffff}",
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": FIXED_NOW,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "input_data": {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
    }


_LONG_SYSTEM = (
    "You are a careful, methodical research assistant. " * 200
)
_OTHER_SYSTEM = (
    "You are a terse code-review assistant. " * 200
)


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #


def test_empty_input_grade_a():
    advisor = CacheabilityAdvisor(now_fn=_now)
    report = advisor.analyze([])
    assert isinstance(report, CacheabilityReport)
    assert report.portfolio.total_events == 0
    assert report.portfolio.portfolio_grade is CacheabilityGrade.A
    assert report.slices == []
    # Playbook has a fallback action.
    assert any(a.id == "no_cache_action_needed" for a in report.playbook)
    assert "NO_LLM_ACTIVITY" in report.insights


def test_singleton_no_action():
    advisor = CacheabilityAdvisor(now_fn=_now)
    report = advisor.analyze([_ev(system_prompt="short", user_prompt="q?")])
    assert report.portfolio.hot_candidate_count == 0
    assert report.portfolio.warm_candidate_count == 0
    assert report.portfolio.duplicate_heavy_count == 0
    assert report.portfolio.projected_savings_usd == 0.0


def test_non_llm_events_ignored():
    advisor = CacheabilityAdvisor(now_fn=_now)
    bogus = {"event_type": "tool_call", "session_id": "s1", "timestamp": FIXED_NOW}
    report = advisor.analyze([bogus, bogus, bogus])
    assert report.portfolio.total_events == 3
    assert report.portfolio.total_llm_events == 0


# --------------------------------------------------------------------------- #
# Hot prefix candidates
# --------------------------------------------------------------------------- #


def test_hot_prefix_candidate_detected_and_priority_assigned():
    advisor = CacheabilityAdvisor(now_fn=_now)
    events = [
        _ev(
            session_id=f"s{i}",
            system_prompt=_LONG_SYSTEM,
            user_prompt=f"question {i}",
            tokens_in=2000,
        )
        for i in range(6)
    ]
    report = advisor.analyze(events)
    hot = [s for s in report.slices if s.verdict is CacheabilityVerdict.HOT_CACHE_CANDIDATE]
    assert len(hot) == 1
    slice_ = hot[0]
    assert slice_.hit_count == 6
    assert slice_.priority in (ActionPriority.P0, ActionPriority.P1)
    assert slice_.projected_savings_usd > 0.0
    assert any(a.id == "turn_on_prompt_prefix_cache" for a in report.playbook)


def test_warm_prefix_candidate_for_three_hits():
    opts = CacheabilityOptions(hot_min_hits=5, warm_min_hits=3)
    advisor = CacheabilityAdvisor(opts, now_fn=_now)
    events = [
        _ev(
            session_id=f"s{i}",
            system_prompt=_LONG_SYSTEM[:2000],  # >= min_prefix_tokens chars (~500 tok)
            user_prompt=f"q {i}",
            tokens_in=900,
        )
        for i in range(3)
    ]
    report = advisor.analyze(events)
    warm = [s for s in report.slices if s.verdict is CacheabilityVerdict.WARM_PREFIX_CANDIDATE]
    assert len(warm) == 1
    assert warm[0].projected_savings_usd > 0.0


# --------------------------------------------------------------------------- #
# Duplicate-heavy (response cache)
# --------------------------------------------------------------------------- #


def test_duplicate_heavy_takes_priority_over_warm():
    advisor = CacheabilityAdvisor(now_fn=_now)
    # 5 byte-identical prompts -> response cache opportunity.
    events = [
        _ev(
            session_id=f"s{i}",
            system_prompt=_LONG_SYSTEM,
            user_prompt="identical user question",
            tokens_in=2000,
        )
        for i in range(5)
    ]
    report = advisor.analyze(events)
    dup = [s for s in report.slices if s.verdict is CacheabilityVerdict.DUPLICATE_HEAVY]
    assert len(dup) == 1
    assert dup[0].priority is ActionPriority.P0
    assert any(a.id == "enable_response_cache" for a in report.playbook)
    assert "RESPONSE_CACHE_AVAILABLE" in report.insights


# --------------------------------------------------------------------------- #
# Risk appetite
# --------------------------------------------------------------------------- #


def test_risk_appetite_trims_p3_when_aggressive():
    advisor = CacheabilityAdvisor(now_fn=_now)
    events = [
        _ev(session_id=f"s{i}", system_prompt=_LONG_SYSTEM, user_prompt=f"q{i}", tokens_in=2000)
        for i in range(6)
    ]
    cautious = advisor.analyze(events, risk_appetite="cautious")
    aggressive = advisor.analyze(events, risk_appetite="aggressive")
    # Cautious shrinks projected savings vs aggressive.
    assert cautious.portfolio.projected_savings_usd <= aggressive.portfolio.projected_savings_usd
    # No P3 filler when aggressive has real actions.
    assert all(a.priority is not ActionPriority.P3 for a in aggressive.playbook)


def test_cautious_adds_review_action_when_grade_low():
    advisor = CacheabilityAdvisor(now_fn=_now)
    # Make sure we hit grade <= C: 5 duplicate-heavy slices.
    events = []
    for prefix_idx in range(3):
        sys_prompt = _LONG_SYSTEM + f" prefix variant {prefix_idx}"
        for hit in range(4):
            events.append(
                _ev(
                    session_id=f"s-{prefix_idx}-{hit}",
                    system_prompt=sys_prompt,
                    user_prompt="same q",
                    tokens_in=2200,
                )
            )
    report = advisor.analyze(events, risk_appetite="cautious")
    assert report.portfolio.portfolio_grade in (
        CacheabilityGrade.C,
        CacheabilityGrade.D,
        CacheabilityGrade.F,
    )
    assert any(a.id == "schedule_cache_review" for a in report.playbook)


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #


def test_to_text_and_markdown_have_required_sections():
    advisor = CacheabilityAdvisor(now_fn=_now)
    events = [
        _ev(session_id=f"s{i}", system_prompt=_LONG_SYSTEM, user_prompt=f"q{i}", tokens_in=2000)
        for i in range(6)
    ]
    report = advisor.analyze(events)
    text = advisor.to_text(report)
    md = advisor.to_markdown(report)
    assert "Cacheability advisor" in text
    assert "Top slices" in text
    assert "Playbook" in text
    assert "## Summary" in md
    assert "## Top slices" in md
    assert "## Playbook" in md
    assert "## Insights" in md


def test_to_json_is_byte_stable_and_sorted():
    advisor = CacheabilityAdvisor(now_fn=_now)
    events = [
        _ev(session_id=f"s{i}", system_prompt=_LONG_SYSTEM, user_prompt=f"q{i}", tokens_in=2000)
        for i in range(5)
    ]
    a = advisor.to_json(advisor.analyze(events))
    b = advisor.to_json(advisor.analyze(list(reversed(events))))
    # Same set of events -> same JSON (independent of input order).
    assert a == b
    payload = json.loads(a)
    assert payload["portfolio"]["total_llm_events"] == 5
    assert sorted(payload.keys()) == list(payload.keys())


# --------------------------------------------------------------------------- #
# Input safety
# --------------------------------------------------------------------------- #


def test_inputs_are_not_mutated():
    advisor = CacheabilityAdvisor(now_fn=_now)
    ev = _ev(system_prompt=_LONG_SYSTEM, user_prompt="q", tokens_in=2000)
    snapshot = json.dumps(ev, default=str, sort_keys=True)
    advisor.analyze([ev, ev, ev])
    assert json.dumps(ev, default=str, sort_keys=True) == snapshot


def test_accepts_pydantic_agent_event():
    from agentlens.models import AgentEvent

    advisor = CacheabilityAdvisor(now_fn=_now)
    events = []
    for i in range(5):
        events.append(
            AgentEvent(
                session_id=f"s{i}",
                event_type="llm_call",
                model="gpt-4o",
                tokens_in=2000,
                tokens_out=300,
                input_data={
                    "messages": [
                        {"role": "system", "content": _LONG_SYSTEM},
                        {"role": "user", "content": f"q{i}"},
                    ],
                },
            )
        )
    report = advisor.analyze(events)
    assert report.portfolio.total_llm_events == 5
    assert any(
        s.verdict is CacheabilityVerdict.HOT_CACHE_CANDIDATE for s in report.slices
    )


def test_other_model_segregated():
    advisor = CacheabilityAdvisor(now_fn=_now)
    events = [
        _ev(session_id=f"a{i}", system_prompt=_LONG_SYSTEM, user_prompt=f"q{i}", model="gpt-4o", tokens_in=2000)
        for i in range(5)
    ]
    events += [
        _ev(session_id=f"b{i}", system_prompt=_OTHER_SYSTEM, user_prompt=f"q{i}", model="gpt-4o-mini", tokens_in=2000)
        for i in range(5)
    ]
    report = advisor.analyze(events)
    models_seen = {s.model for s in report.slices}
    assert models_seen == {"gpt-4o", "gpt-4o-mini"}


def test_sdk_manifest_exposes_advisor():
    import agentlens

    assert hasattr(agentlens, "CacheabilityAdvisor")
    assert hasattr(agentlens, "CacheabilityVerdict")
    assert agentlens.CacheabilityVerdict.HOT_CACHE_CANDIDATE.value == "hot_cache_candidate"
