"""Tests for the agentic SamplingAdvisor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentlens import SamplingAdvice, SamplingAdvisor, WorkloadProfile
from agentlens.models import AgentEvent


def _make_event(
    *,
    seconds_offset: float = 0.0,
    duration_ms: float = 100.0,
    tokens_in: int = 10,
    tokens_out: int = 5,
    model: str = "gpt-4o",
    event_type: str = "llm_call",
    priority: int | None = None,
    error: bool = False,
) -> AgentEvent:
    base = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
    ev = AgentEvent(
        session_id="s1",
        event_type=event_type if not error else "error",
        timestamp=base + timedelta(seconds=seconds_offset),
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
    )
    if priority is not None:
        # priority lives in metadata for AgentEvent (no dedicated field)
        ev.output_data = {"priority": priority}
        # SamplingAdvisor reads priority via _get -> attr/dict; mirror it.
        ev.__dict__.setdefault("priority", priority)
    return ev


def test_empty_advisor_returns_safe_defaults():
    advisor = SamplingAdvisor()
    profile = advisor.analyze()
    assert isinstance(profile, WorkloadProfile)
    assert profile.sample_count == 0
    assert "no events observed" in profile.notes

    advice = advisor.recommend()
    assert isinstance(advice, SamplingAdvice)
    assert 0.0 <= advice.fallback_rate <= 1.0
    # No latency data -> falls back to floor
    assert advice.slow_threshold_ms >= 250.0
    # Summary / json / markdown should work
    assert "SamplingAdvisor recommendation" in advice.summary()
    assert "fallback_rate" in advice.to_json()
    assert "Sampling Advisor" in advice.to_markdown()


def test_profile_computes_percentiles_and_rates():
    advisor = SamplingAdvisor()
    events = []
    # 100 events, durations 1..100 ms, one second apart so window=99s
    for i in range(100):
        events.append(_make_event(seconds_offset=i, duration_ms=float(i + 1)))
    # 5 explicit errors
    for i in range(5):
        events.append(
            _make_event(
                seconds_offset=100 + i,
                duration_ms=2000.0,
                error=True,
            )
        )
    advisor.observe(events)
    profile = advisor.analyze()

    assert profile.sample_count == 105
    assert profile.error_count == 5
    assert pytest.approx(profile.error_rate, abs=1e-6) == 5 / 105
    # p95 of 1..100 plus five 2000 outliers should be well above 90
    assert profile.latency_p95_ms >= 95.0
    assert profile.window_seconds > 0
    assert profile.events_per_minute > 0
    assert profile.distinct_models == 1


def test_recommend_solves_target_volume():
    advisor = SamplingAdvisor()
    # 600 events spread evenly across 60s = 600 events/min
    events = [
        _make_event(seconds_offset=i * 0.1, duration_ms=50.0 + i % 50)
        for i in range(600)
    ]
    advisor.observe(events)

    advice = advisor.recommend(target_events_per_minute=60.0)
    # Should keep roughly 60/min, well below 600/min
    assert advice.expected_kept_per_minute <= 90.0
    assert advice.expected_kept_per_minute >= 30.0
    assert 0.0 < advice.fallback_rate < 1.0
    assert advice.expected_volume_reduction_pct > 50.0
    # Reasoning trail not empty
    assert any("fallback_rate" in r or "target" in r for r in advice.reasoning)


def test_high_error_rate_disables_error_always_keep():
    advisor = SamplingAdvisor()
    # 50/50 errors vs successes
    events = []
    for i in range(50):
        events.append(_make_event(seconds_offset=i, duration_ms=10.0))
        events.append(_make_event(seconds_offset=i + 0.5, duration_ms=10.0, error=True))
    advisor.observe(events)

    advice = advisor.recommend(target_events_per_minute=5.0, max_error_keep_rate=0.30)
    assert advice.error_always_keep is False
    assert any("error_rate" in r for r in advice.reasoning)


def test_format_dispatch_and_build_sampler():
    advisor = SamplingAdvisor()
    advisor.observe([_make_event(seconds_offset=i, duration_ms=100.0) for i in range(10)])
    advice = advisor.recommend()

    assert advice.format("text") == advice.summary()
    assert advice.format("markdown").startswith("# Sampling Advisor")
    assert "fallback_rate" in advice.format("json")
    with pytest.raises(ValueError):
        advice.format("xml")

    sampler = advice.build_sampler()
    # PrioritySampler exposes a `name` property
    assert sampler.name == "priority"
