"""Extended tests for ``agentlens.sampling_advisor``.

Complements ``test_sampling_advisor.py`` with coverage for:
  * ``WorkloadProfile`` / ``SamplingAdvice`` serialization helpers
  * Constructor validation
  * ``analyze()`` edge cases — dict events, ``output_data`` error markers,
    nested ``tool_call.duration_ms``, ``metadata.priority`` fallback,
    numeric & string timestamps, missing/invalid fields.
  * ``recommend()`` branches — ``target_keep_pct``, mandatory-already-meets-
    target clamp, no-target default, ``min_fallback`` floor, ``max_fallback``
    cap, slow-threshold floor, priority defaulting.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.sampling_advisor import (
    SamplingAdvice,
    SamplingAdvisor,
    WorkloadProfile,
    _get,
    _percentile,
    _to_dt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BASE = datetime(2026, 5, 18, 0, 0, 0, tzinfo=timezone.utc)


def _dict_event(**overrides):
    """Build a duck-typed event as a plain dict."""
    ev = {
        "session_id": "s",
        "event_type": "llm_call",
        "timestamp": _BASE,
        "model": "gpt-4o",
        "tokens_in": 10,
        "tokens_out": 5,
        "duration_ms": 100.0,
        "priority": None,
    }
    ev.update(overrides)
    return ev


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 0.5) == 0.0

    def test_single_value(self):
        assert _percentile([42.0], 0.99) == 42.0

    def test_interpolates_between_values(self):
        # p50 of [10, 20] = 15 (linear interp)
        assert _percentile([10.0, 20.0], 0.5) == 15.0

    def test_p95_of_known_series(self):
        vals = list(range(1, 101))
        # p95 with linear interpolation over 1..100
        assert _percentile(vals, 0.95) == pytest.approx(95.05)


class TestToDt:
    def test_none(self):
        assert _to_dt(None) is None

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2026, 1, 1, 12, 0)
        dt = _to_dt(naive)
        assert dt is not None
        assert dt.tzinfo is timezone.utc

    def test_aware_datetime_passthrough(self):
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert _to_dt(aware) is aware

    def test_int_epoch(self):
        dt = _to_dt(0)
        assert dt == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_float_epoch(self):
        dt = _to_dt(1_700_000_000.0)
        assert dt is not None and dt.tzinfo is timezone.utc

    def test_iso_z_suffix(self):
        dt = _to_dt("2026-05-18T12:34:56Z")
        assert dt == datetime(2026, 5, 18, 12, 34, 56, tzinfo=timezone.utc)

    def test_iso_naive_string_gets_utc(self):
        dt = _to_dt("2026-05-18T12:34:56")
        assert dt is not None and dt.tzinfo is timezone.utc

    def test_invalid_string_returns_none(self):
        assert _to_dt("not-a-date") is None

    def test_unknown_type_returns_none(self):
        assert _to_dt(object()) is None


class TestGetAccessor:
    def test_none(self):
        assert _get(None, "x", "default") == "default"

    def test_dict(self):
        assert _get({"a": 1}, "a") == 1
        assert _get({"a": 1}, "b", 99) == 99

    def test_object(self):
        class O:
            x = 7

        assert _get(O(), "x") == 7
        assert _get(O(), "missing", "d") == "d"


# ---------------------------------------------------------------------------
# Data class serialization
# ---------------------------------------------------------------------------


class TestWorkloadProfile:
    def test_to_dict_includes_all_fields(self):
        p = WorkloadProfile(sample_count=10, events_per_minute=5.0)
        d = p.to_dict()
        assert d["sample_count"] == 10
        assert d["events_per_minute"] == 5.0
        assert d["notes"] == []


class TestSamplingAdviceSerialization:
    def _make_advice(self) -> SamplingAdvice:
        return SamplingAdvice(
            fallback_rate=0.25,
            error_always_keep=True,
            slow_threshold_ms=500.0,
            important_threshold=5,
            target_events_per_minute=10.0,
            expected_keep_rate=0.30,
            expected_kept_per_minute=15.0,
            expected_volume_reduction_pct=70.0,
            reasoning=["because", "reasons"],
            profile=WorkloadProfile(sample_count=100),
        )

    def test_to_dict_serialises_timestamp(self):
        a = self._make_advice()
        d = a.to_dict()
        # ISO timestamp string
        assert isinstance(d["generated_at"], str)
        assert "T" in d["generated_at"]
        assert d["fallback_rate"] == 0.25

    def test_to_json_is_valid_json(self):
        a = self._make_advice()
        parsed = json.loads(a.to_json())
        assert parsed["important_threshold"] == 5
        assert parsed["reasoning"] == ["because", "reasons"]

    def test_summary_contains_reasoning(self):
        a = self._make_advice()
        out = a.summary()
        assert "fallback_rate" in out
        assert "because" in out
        assert "target/min" in out

    def test_summary_without_target(self):
        a = self._make_advice()
        a.target_events_per_minute = None
        out = a.summary()
        assert "target/min" not in out

    def test_summary_without_reasoning(self):
        a = self._make_advice()
        a.reasoning = []
        out = a.summary()
        assert "reasoning" not in out

    def test_to_markdown_with_and_without_target(self):
        a = self._make_advice()
        md = a.to_markdown()
        assert md.startswith("# Sampling Advisor")
        assert "| target/min |" in md
        assert "## Reasoning" in md

        a.target_events_per_minute = None
        a.reasoning = []
        md2 = a.to_markdown()
        assert "| target/min |" not in md2
        assert "## Reasoning" not in md2

    def test_format_aliases(self):
        a = self._make_advice()
        assert a.format("txt") == a.summary()
        assert a.format("summary") == a.summary()
        assert a.format("md") == a.to_markdown()
        assert a.format("MARKDOWN") == a.to_markdown()
        assert a.format("JSON") == a.to_json()

    def test_format_empty_defaults_to_text(self):
        a = self._make_advice()
        assert a.format("") == a.summary()

    def test_format_unknown_raises(self):
        a = self._make_advice()
        with pytest.raises(ValueError, match="unsupported format"):
            a.format("yaml")


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestAdvisorInit:
    def test_default_init(self):
        a = SamplingAdvisor()
        assert a.event_count == 0

    def test_custom_bounds(self):
        a = SamplingAdvisor(min_fallback=0.05, max_fallback=0.9, min_slow_threshold_ms=100.0)
        assert a._min_fallback == 0.05
        assert a._max_fallback == 0.9
        assert a._min_slow_threshold_ms == 100.0

    def test_inverted_bounds_raise(self):
        with pytest.raises(ValueError, match="min_fallback"):
            SamplingAdvisor(min_fallback=0.5, max_fallback=0.1)

    def test_out_of_range_bounds_raise(self):
        with pytest.raises(ValueError):
            SamplingAdvisor(min_fallback=-0.1)
        with pytest.raises(ValueError):
            SamplingAdvisor(max_fallback=1.5)

    def test_reset_clears_observations(self):
        a = SamplingAdvisor()
        a.observe([_dict_event()])
        assert a.event_count == 1
        a.reset()
        assert a.event_count == 0

    def test_observe_returns_self_for_chaining(self):
        a = SamplingAdvisor()
        ret = a.observe([_dict_event()])
        assert ret is a


# ---------------------------------------------------------------------------
# analyze() edge cases
# ---------------------------------------------------------------------------


class TestAnalyzeEdgeCases:
    def test_dict_events_supported(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(duration_ms=10.0, timestamp=_BASE),
                   _dict_event(duration_ms=20.0, timestamp=_BASE + timedelta(seconds=60))])
        p = a.analyze()
        assert p.sample_count == 2
        assert p.window_seconds == pytest.approx(60.0)
        assert p.events_per_minute == pytest.approx(2.0)
        assert p.latency_p50_ms == 15.0
        assert p.distinct_models == 1
        assert p.distinct_event_types == 1

    def test_no_timestamps_yields_raw_count_per_minute(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(timestamp=None) for _ in range(5)])
        p = a.analyze()
        # window_seconds == 0 -> treat sample_count as per-minute
        assert p.window_seconds == 0.0
        assert p.events_per_minute == 5.0
        assert any("no usable timestamps" in n for n in p.notes)

    def test_no_durations_disables_slow(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(duration_ms=None) for _ in range(3)])
        p = a.analyze()
        assert p.latency_p95_ms == 0.0
        assert any("slow detection disabled" in n for n in p.notes)

    def test_tool_call_duration_fallback(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(duration_ms=None, tool_call={"duration_ms": 77.0})])
        p = a.analyze()
        assert p.latency_p50_ms == 77.0

    def test_event_type_error_counts(self):
        a = SamplingAdvisor()
        a.observe([
            _dict_event(event_type="error"),
            _dict_event(event_type="Failure"),
            _dict_event(event_type="Exception"),
            _dict_event(event_type="llm_call"),
        ])
        p = a.analyze()
        assert p.error_count == 3
        assert p.error_rate == pytest.approx(0.75)

    def test_output_data_error_marker_counts(self):
        a = SamplingAdvisor()
        a.observe([
            _dict_event(output_data={"error": "boom"}),
            _dict_event(output_data={"status": "failed"}),
            _dict_event(output_data={"status": "ok"}),
        ])
        p = a.analyze()
        assert p.error_count == 2

    def test_priority_from_metadata(self):
        a = SamplingAdvisor()
        events = [_dict_event(priority=None, metadata={"priority": p}) for p in range(1, 11)]
        a.observe(events)
        p = a.analyze()
        # p90 of 1..10 ≈ 9.1
        assert p.priority_p90 > 8.0

    def test_priority_direct_field(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(priority=5)])
        p = a.analyze()
        assert p.priority_p90 == 5.0

    def test_invalid_priority_ignored(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(priority="not-a-number")])
        p = a.analyze()
        assert p.priority_p90 == 0.0
        assert any("no priority field" in n for n in p.notes)

    def test_invalid_duration_skipped(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(duration_ms="garbage")])
        p = a.analyze()
        assert p.latency_p95_ms == 0.0

    def test_invalid_tokens_skipped(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(tokens_in="x", tokens_out="y")])
        p = a.analyze()
        assert p.total_tokens == 0
        assert p.avg_tokens == 0.0

    def test_missing_event_type_defaults_to_generic(self):
        a = SamplingAdvisor()
        ev = _dict_event()
        del ev["event_type"]
        a.observe([ev])
        p = a.analyze()
        assert p.distinct_event_types == 1


# ---------------------------------------------------------------------------
# recommend() branches
# ---------------------------------------------------------------------------


class TestRecommendBranches:
    def test_no_target_uses_default_fallback(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(timestamp=_BASE + timedelta(seconds=i)) for i in range(30)])
        advice = a.recommend()
        # No target supplied -> default 0.20
        assert advice.fallback_rate == 0.2
        assert any("defaulting fallback_rate to 0.20" in r for r in advice.reasoning)

    def test_target_keep_pct_path(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(timestamp=_BASE + timedelta(seconds=i * 0.1)) for i in range(100)])
        advice = a.recommend(target_keep_pct=0.1)
        assert advice.target_events_per_minute is not None
        assert any("target_keep_pct" in r for r in advice.reasoning)

    def test_target_keep_pct_clamped_to_unit_interval(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(timestamp=_BASE + timedelta(seconds=i * 0.1)) for i in range(20)])
        # Out-of-range values are clamped, not rejected
        advice = a.recommend(target_keep_pct=2.0)
        assert advice.target_events_per_minute is not None

        advice2 = a.recommend(target_keep_pct=-1.0)
        assert advice2.target_events_per_minute == 0.0

    def test_mandatory_keeps_exceed_target_clamps_floor(self):
        # Lots of errors so mandatory keeps already dwarf target
        a = SamplingAdvisor()
        events = []
        for i in range(100):
            err = (i % 2 == 0)
            events.append(_dict_event(
                timestamp=_BASE + timedelta(seconds=i * 0.6),
                event_type="error" if err else "llm_call",
            ))
        a.observe(events)
        # 50% error rate -> max_error_keep_rate=0.8 keeps errors on
        advice = a.recommend(target_events_per_minute=1.0, max_error_keep_rate=0.8)
        assert advice.fallback_rate == pytest.approx(SamplingAdvisor.DEFAULT_MIN_FALLBACK)
        assert any("mandatory keeps already" in r for r in advice.reasoning)

    def test_fallback_capped_at_max(self):
        # Very high target so fallback would solve to >1.0
        a = SamplingAdvisor(max_fallback=0.5)
        a.observe([_dict_event(timestamp=_BASE + timedelta(seconds=i)) for i in range(10)])
        advice = a.recommend(target_events_per_minute=100000.0)
        assert advice.fallback_rate <= 0.5

    def test_slow_threshold_floored(self):
        # All durations tiny -> p95 below default floor of 250ms
        a = SamplingAdvisor(min_slow_threshold_ms=250.0)
        a.observe([_dict_event(duration_ms=5.0, timestamp=_BASE + timedelta(seconds=i))
                   for i in range(20)])
        advice = a.recommend()
        assert advice.slow_threshold_ms == 250.0
        assert any("below floor" in r for r in advice.reasoning)

    def test_slow_threshold_default_when_no_latency(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(duration_ms=None,
                                timestamp=_BASE + timedelta(seconds=i)) for i in range(5)])
        advice = a.recommend()
        assert advice.slow_threshold_ms == a._min_slow_threshold_ms
        assert any("no latency data" in r for r in advice.reasoning)

    def test_important_threshold_defaults_to_five(self):
        a = SamplingAdvisor()
        a.observe([_dict_event(timestamp=_BASE + timedelta(seconds=i)) for i in range(5)])
        advice = a.recommend()
        assert advice.important_threshold == 5
        assert any("important_threshold defaulted to 5" in r for r in advice.reasoning)

    def test_important_threshold_from_priority_p90(self):
        a = SamplingAdvisor()
        events = [_dict_event(priority=p, timestamp=_BASE + timedelta(seconds=i))
                  for i, p in enumerate(range(1, 11))]
        a.observe(events)
        advice = a.recommend()
        assert advice.important_threshold >= 9

    def test_zero_epm_uses_default_fallback(self):
        a = SamplingAdvisor()
        # No timestamps and an explicit target -> epm==sample_count (>0)
        # Force epm==0 by passing zero events but it short-circuits.
        # So instead use single event with no timestamp: events_per_minute=1.
        # The branch we want is epm<=0 with target provided. Achieve via empty.
        advice = a.recommend(target_events_per_minute=10.0)
        # With zero events: epm=0, target supplied -> default 0.2 path
        assert advice.fallback_rate == 0.2
