"""Tests for AlertRuleSynthesizer."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from agentlens import (
    AlertRuleSynthesizer,
    RuleSuggestion,
    RuleSynthesisReport,
    SuggestionPriority,
)
from agentlens.alert_rule_synthesizer import _round_to_nice
from agentlens.alerts import AlertManager, Condition, Severity
from agentlens.models import AgentEvent, ToolCall


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _ts(base: datetime, secs: float) -> datetime:
    return base + timedelta(seconds=secs)


def _make_events(
    n: int = 120,
    *,
    error_rate: float = 0.02,
    base_latency_ms: float = 200.0,
    seed: int = 42,
) -> list[AgentEvent]:
    rng = random.Random(seed)
    base = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    events: list[AgentEvent] = []
    for i in range(n):
        is_error = rng.random() < error_rate
        if is_error:
            etype = "error"
        elif i % 5 == 0:
            etype = "tool_call"
        else:
            etype = "llm_call"

        # Latency: lognormal-ish around base
        latency = max(1.0, rng.gauss(base_latency_ms, base_latency_ms * 0.3))
        if i == n - 1:
            latency = base_latency_ms * 10  # one big outlier

        tokens_in = rng.randint(50, 400)
        tokens_out = rng.randint(20, 250)

        tool_call = None
        if etype == "tool_call":
            tool_name = rng.choice(["search", "calculator", "db_query"])
            tool_call = ToolCall(tool_name=tool_name)

        events.append(AgentEvent(
            event_type=etype,
            timestamp=_ts(base, i * 2.0),  # 0.5/sec = 30/min
            duration_ms=latency,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tool_call=tool_call,
            output_data={"error_type": "Timeout"} if is_error else None,
        ))
    return events


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_round_to_nice_examples():
    assert _round_to_nice(0) == 0.0
    assert _round_to_nice(12) == 10.0
    # _round_to_nice quantises with 0.5 steps in the [1.5, 3.5) bucket
    assert _round_to_nice(17) == 15.0
    assert _round_to_nice(123) == 100.0
    assert _round_to_nice(89) == 100.0
    assert _round_to_nice(0.07) == pytest.approx(0.07, rel=1e-9)


def test_invalid_constructor_args():
    with pytest.raises(ValueError):
        AlertRuleSynthesizer(risk_appetite="reckless")
    with pytest.raises(ValueError):
        AlertRuleSynthesizer(target_fires_per_day=0)
    with pytest.raises(ValueError):
        AlertRuleSynthesizer(min_window_seconds=0)
    with pytest.raises(ValueError):
        AlertRuleSynthesizer(min_window_seconds=120, max_window_seconds=60)


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #


def test_profile_empty_stream():
    s = AlertRuleSynthesizer()
    prof = s.profile([])
    assert prof.total_events == 0
    assert prof.error_rate == 0.0
    assert prof.latency_p95_ms is None


def test_profile_basic_stats():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=100, error_rate=0.10)
    prof = s.profile(evs)
    assert prof.total_events == 100
    assert prof.error_events >= 5  # ~10% expected
    assert prof.latency_p50_ms is not None and prof.latency_p50_ms > 0
    assert prof.latency_p95_ms >= prof.latency_p50_ms
    assert prof.latency_p99_ms >= prof.latency_p95_ms
    assert prof.events_per_minute > 0
    assert prof.cost_p95 is not None and prof.cost_p95 > 0
    # top_tools should include at least one of our tools
    if prof.top_tools:
        tool_names = {n for n, _ in prof.top_tools}
        assert tool_names <= {"search", "calculator", "db_query"}


def test_profile_works_with_dict_events():
    s = AlertRuleSynthesizer()
    dicts = [
        {"event_type": "llm_call", "duration_ms": 100, "tokens_in": 50, "tokens_out": 50,
         "timestamp": "2026-05-17T12:00:00+00:00"},
        {"event_type": "error", "duration_ms": 5000, "tokens_in": 0, "tokens_out": 0,
         "timestamp": "2026-05-17T12:00:10+00:00",
         "output_data": {"error_type": "RateLimit"}},
    ]
    prof = s.profile(dicts)
    assert prof.total_events == 2
    assert prof.error_events == 1
    assert prof.top_error_types == [("RateLimit", 1)]


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #


def test_synthesize_empty_stream_returns_no_suggestions():
    s = AlertRuleSynthesizer()
    report = s.synthesize([], workload_label="empty")
    assert isinstance(report, RuleSynthesisReport)
    assert report.suggestions == []
    assert "nothing to synthesize" in report.summary.lower()
    assert report.notes  # has guidance note


def test_synthesize_produces_expected_rule_set():
    s = AlertRuleSynthesizer(risk_appetite="balanced")
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs, workload_label="prod-test")
    metrics = {sug.metric for sug in report.suggestions}
    # Should cover the four big agentlens dimensions
    assert "error_rate" in metrics
    assert "latency_p95" in metrics
    assert "heartbeat" in metrics
    assert "total_tokens" in metrics
    # And volume floor for balanced
    assert "event_count" in metrics


def test_aggressive_risk_appetite_drops_volume_floor():
    s = AlertRuleSynthesizer(risk_appetite="aggressive")
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    assert all(sug.metric != "event_count" for sug in report.suggestions)


def test_cautious_threshold_is_lower_than_balanced_for_high_bad_metric():
    evs = _make_events(n=200, error_rate=0.10)
    cautious = AlertRuleSynthesizer(risk_appetite="cautious").synthesize(evs)
    balanced = AlertRuleSynthesizer(risk_appetite="balanced").synthesize(evs)
    # Compare via the unrounded fires-per-day signature on the error_rate
    # rule, which uses the multiplier directly without aggressive rounding.
    c_err = next(s for s in cautious.suggestions if s.metric == "error_rate")
    b_err = next(s for s in balanced.suggestions if s.metric == "error_rate")
    assert c_err.threshold <= b_err.threshold
    # And cautious latency threshold cannot be HIGHER than balanced.
    c_latency = next(s for s in cautious.suggestions if s.metric == "latency_p95")
    b_latency = next(s for s in balanced.suggestions if s.metric == "latency_p95")
    assert c_latency.threshold <= b_latency.threshold


def test_priority_sorting_p0_first():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    seen_priorities = [sug.priority for sug in report.suggestions]
    ranks = [{"P0": 0, "P1": 1, "P2": 2}[p.value] for p in seen_priorities]
    assert ranks == sorted(ranks)


def test_error_rate_zero_uses_absolute_floor():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=80, error_rate=0.0)
    report = s.synthesize(evs)
    err_rule = next(sug for sug in report.suggestions if sug.metric == "error_rate")
    assert err_rule.threshold >= 0.05
    assert "absolute floor" in err_rule.reason.lower() or "floor" in err_rule.reason.lower()


def test_noisy_p0_demoted_to_p1():
    # Force a tiny target_fires_per_day so any P0 will get demoted
    s = AlertRuleSynthesizer(target_fires_per_day=0.0001)
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    # Should have at least one suggestion demoted with the demotion signal
    demoted = [
        sug for sug in report.suggestions
        if any("demoted_from_P0" in sig for sig in sug.signals)
    ]
    # We may or may not have demotions depending on rates — but if any P0
    # is left it must have est <= target.
    for sug in report.suggestions:
        if sug.priority == SuggestionPriority.P0:
            assert sug.estimated_fires_per_day <= s.target_fires_per_day + 1e-9
    # And the demoted ones should now be P1
    for sug in demoted:
        assert sug.priority == SuggestionPriority.P1


# --------------------------------------------------------------------------- #
# Materialisation / apply
# --------------------------------------------------------------------------- #


def test_build_rules_min_priority_inclusive():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    p0_p1 = report.build_rules(min_priority="P1")
    p0_only = report.build_rules(min_priority="P0")
    assert len(p0_only) <= len(p0_p1)
    for r in p0_p1:
        assert r.enabled is True


def test_apply_installs_rules_into_alert_manager():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    mgr = AlertManager()
    installed = report.apply(mgr, min_priority="P1", max_rules=2)
    assert len(installed) <= 2
    installed_names = {r.name for r in installed}
    assert installed_names.issubset({rr.name for rr in mgr.get_rules()})


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_render_text_includes_priority_buckets_and_summary():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs, workload_label="prod-x")
    txt = report.render_text()
    assert "prod-x" in txt
    assert "Synthesized" in txt
    assert "[P0]" in txt or "[P1]" in txt


def test_render_markdown_has_headers_and_tables():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    md = report.render_markdown()
    assert md.startswith("# Alert Rule Synthesis")
    assert "## Workload Profile" in md
    assert "| Rule | Metric |" in md


def test_render_json_is_valid_and_round_trips_structure():
    s = AlertRuleSynthesizer()
    evs = _make_events(n=200, error_rate=0.05)
    report = s.synthesize(evs)
    payload = json.loads(report.render_json())
    assert payload["workload_label"]
    assert "suggestions" in payload and len(payload["suggestions"]) >= 3
    assert all("threshold" in s and "priority" in s for s in payload["suggestions"])
