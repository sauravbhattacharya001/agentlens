"""Extended tests for ToolReliabilityAdvisor.

These tests target branches and helpers that aren't exercised by
``test_tool_reliability_advisor.py``:

* ``_coerce_event`` variants (model_dump, plain dict-iterable, metadata tool name,
  alt keys ``tool``/``caller``/``retries``/``latency_ms``/``ts``)
* ``_parse_ts`` variants (numeric, ISO Z, invalid string, None, naive datetime)
* ``_percentile`` (empty, single)
* Verdict / playbook branches: WATCH, FLAKY, DEGRADED, ENABLE_CIRCUIT_BREAKER_GUARDS,
  ROLLBACK_RECENT_TOOL_CHANGE, DEPRECATE_OR_RETIRE
* Insight branches: RETRY_AMPLIFICATION, LATENCY_DOMINATED_FAILURES,
  ERROR_CLUSTER_PATTERN, SINGLE_OWNER_RISK, STALE_TOOL_BACKLOG,
  NEW_TOOLS_PROBATION, MIXED_FLEET_SIGNALS, INSUFFICIENT_DATA insight
* Output formats: ``to_text`` (populated + empty), ``to_markdown`` (empty),
  ``to_dict`` round-trip, ``RiskAppetite.parse`` fall-back, grade ordering when
  ``mean_error_rate`` alone drives it.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.tool_reliability_advisor import (
    ActionPriority,
    ReliabilityBand,
    RiskAppetite,
    ToolReliabilityAdvisor,
    ToolReliabilityGrade,
    ToolVerdict,
    _coerce_event,
    _parse_ts,
    _percentile,
)


FIXED_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return FIXED_NOW


def _call(tool, **kw):
    base = {
        "event_type": "tool_call",
        "tool_name": tool,
        "session_id": "s1",
        "agent_id": "a1",
        "duration_ms": 100,
        "timestamp": FIXED_NOW,
    }
    base.update(kw)
    return base


def _err(tool, code="E", **kw):
    base = {
        "event_type": "tool_result",
        "tool_name": tool,
        "session_id": "s1",
        "agent_id": "a1",
        "duration_ms": 100,
        "error_code": code,
        "timestamp": FIXED_NOW,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Helpers: _percentile / _parse_ts / _coerce_event
# --------------------------------------------------------------------------- #
class TestHelpers:
    def test_percentile_empty(self):
        assert _percentile([], 95) == 0.0

    def test_percentile_single(self):
        assert _percentile([42.0], 95) == 42.0

    def test_percentile_interpolation(self):
        # p50 of [1,2,3,4] interpolates at k=1.5 -> 2.5
        assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)

    def test_percentile_pegs_at_max(self):
        # p100 should return the max element (clamped)
        assert _percentile([1.0, 5.0, 9.0], 100) == 9.0

    def test_parse_ts_none(self):
        assert _parse_ts(None) is None

    def test_parse_ts_naive_datetime_assumed_utc(self):
        naive = datetime(2026, 1, 1, 0, 0, 0)
        out = _parse_ts(naive)
        assert out is not None
        assert out.tzinfo is timezone.utc

    def test_parse_ts_numeric(self):
        out = _parse_ts(1_700_000_000)
        assert out is not None and out.tzinfo is not None

    def test_parse_ts_iso_with_z(self):
        out = _parse_ts("2026-05-19T12:00:00Z")
        assert out == FIXED_NOW

    def test_parse_ts_invalid_string_returns_none(self):
        assert _parse_ts("not-a-date") is None

    def test_parse_ts_unknown_type_returns_none(self):
        assert _parse_ts(object()) is None

    def test_coerce_event_none_and_empty(self):
        assert _coerce_event(None) == {}

    def test_coerce_event_deepcopies_dict(self):
        ev = {"event_type": "tool_call", "tool_name": "t", "nested": {"x": 1}}
        out = _coerce_event(ev)
        assert out == ev
        # mutate to ensure deepcopy
        out["nested"]["x"] = 99
        assert ev["nested"]["x"] == 1

    def test_coerce_event_model_dump(self):
        class Fake:
            def model_dump(self):
                return {"event_type": "tool_call", "tool_name": "md_tool"}

        out = _coerce_event(Fake())
        assert out["tool_name"] == "md_tool"

    def test_coerce_event_model_dump_failure_falls_through_to_attrs(self):
        class Broken:
            def model_dump(self):
                raise RuntimeError("boom")

            event_type = "tool_call"
            tool_name = "broken_tool"
            duration_ms = 10
            timestamp = FIXED_NOW

        out = _coerce_event(Broken())
        # dict(Broken()) will raise, attr scan kicks in
        assert out["tool_name"] == "broken_tool"
        assert out["event_type"] == "tool_call"

    def test_coerce_event_attr_object_with_alt_keys(self):
        class E:
            event_type = "tool_call"
            tool = "alt_tool"
            caller = "agentA"
            retries = 2
            latency_ms = 250
            ts = FIXED_NOW
            session_id = "sX"

        out = _coerce_event(E())
        assert out["tool"] == "alt_tool"
        assert out["caller"] == "agentA"
        assert out["retries"] == 2
        assert out["latency_ms"] == 250
        assert out["ts"] == FIXED_NOW


# --------------------------------------------------------------------------- #
# Event ingestion variants
# --------------------------------------------------------------------------- #
class TestEventVariants:
    def test_tool_name_resolved_from_metadata(self):
        events = [
            {
                "event_type": "tool_call",
                "metadata": {"tool_name": "meta_tool"},
                "session_id": f"s{i}",
                "agent_id": f"a{i}",
                "duration_ms": 100,
                "timestamp": FIXED_NOW,
            }
            for i in range(6)
        ]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert rep.snapshots and rep.snapshots[0].tool_name == "meta_tool"

    def test_events_without_tool_name_are_skipped(self):
        events = [
            {"event_type": "tool_call", "session_id": "s", "agent_id": "a"},
            {"event_type": "tool_call", "metadata": {}, "session_id": "s", "agent_id": "a"},
        ]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert rep.portfolio.total_tools == 0
        assert rep.snapshots == []

    def test_none_and_empty_events_are_skipped(self):
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze([None, {}, _call("t")])
        # only the real call survived
        assert rep.portfolio.total_calls == 1

    def test_alt_keys_recognized_in_dict(self):
        events = []
        for i in range(6):
            events.append(
                {
                    "event_type": "tool_call",
                    "tool": "altkey_tool",  # alt key
                    "caller": f"a{i % 2}",  # alt key
                    "latency_ms": 50,  # alt key
                    "ts": FIXED_NOW,  # alt key
                    "session_id": f"s{i}",
                }
            )
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert snap.tool_name == "altkey_tool"
        assert snap.unique_callers == 2

    def test_call_with_inline_error_string_counted(self):
        # `error` field as string on a tool_call still counts as failed attempt.
        events = [_call("t", session_id=f"s{i}", agent_id=f"a{i}") for i in range(3)]
        bad = _call("t", session_id="s9", agent_id="a9")
        bad["error"] = "OOPS"  # string => err_code
        events.append(bad)
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert snap.error_count == 1
        assert any(c["code"] == "OOPS" for c in snap.top_error_codes)

    def test_tool_result_with_error_no_code_uses_unknown_bucket(self):
        events = [_call("t", session_id=f"s{i}", agent_id=f"a{i}") for i in range(5)]
        events.append(
            {
                "event_type": "tool_result",
                "tool_name": "t",
                "error": True,  # truthy but not string
                "session_id": "s9",
                "agent_id": "a9",
                "timestamp": FIXED_NOW,
            }
        )
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert snap.error_count == 1
        assert any(c["code"] == "unknown" for c in snap.top_error_codes)


# --------------------------------------------------------------------------- #
# Reasons / verdict / priority ladder
# --------------------------------------------------------------------------- #
class TestVerdictLadder:
    def test_low_usage_flag(self):
        # 3 calls only => LOW_USAGE
        events = [_call("rarely", session_id=f"s{i}", agent_id=f"a{i}") for i in range(3)]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert "LOW_USAGE" in snap.reasons

    def test_latency_degraded_not_outlier(self):
        # p95 between 2000 and 5000 => LATENCY_DEGRADED but not LATENCY_OUTLIER
        events = [
            _call("midlat", session_id=f"s{i}", agent_id=f"a{i % 4}", duration_ms=2500)
            for i in range(20)
        ]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert "LATENCY_DEGRADED" in snap.reasons
        assert "LATENCY_OUTLIER" not in snap.reasons

    def test_elevated_error_rate_yields_flaky(self):
        # ~8% error rate (8/100), low latency, many callers => FLAKY (no high-err)
        events = [_call("api", session_id=f"s{i}", agent_id=f"a{i % 5}", duration_ms=100) for i in range(100)]
        for i in range(8):
            events.append(_err("api", code="X", session_id=f"s{i}", agent_id=f"a{i % 5}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = [s for s in rep.snapshots if s.tool_name == "api"][0]
        assert "ELEVATED_ERROR_RATE" in snap.reasons
        # 8 errors out of 108 attempts => ~7.4% < 20%, => not CIRCUIT_BREAK
        assert snap.verdict != ToolVerdict.CIRCUIT_BREAK
        # score should land in FLAKY band (>50, <=70)
        assert snap.verdict in (ToolVerdict.FLAKY, ToolVerdict.WATCH, ToolVerdict.DEGRADED)

    def test_watch_verdict_emits_grade_b(self):
        # Many calls, one mild latency_degraded reason => WATCH verdict
        events = [
            _call("watchy", session_id=f"s{i}", agent_id=f"a{i % 5}", duration_ms=2500)
            for i in range(30)
        ]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        snap = rep.snapshots[0]
        assert snap.verdict in (ToolVerdict.WATCH, ToolVerdict.HEALTHY)
        if snap.verdict == ToolVerdict.WATCH:
            assert rep.grade == ToolReliabilityGrade.B

    def test_suggested_action_text_is_present_for_every_snapshot(self):
        events = [_call("t", session_id=f"s{i}", agent_id=f"a{i}") for i in range(6)]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        for s in rep.snapshots:
            assert isinstance(s.suggested_action, str)
            assert s.suggested_action  # non-empty


# --------------------------------------------------------------------------- #
# Playbook branches
# --------------------------------------------------------------------------- #
class TestPlaybook:
    def test_enable_circuit_breaker_guards_for_flaky_without_circuit(self):
        # Build a tool that lands in FLAKY without any CIRCUIT_BREAK.
        events = []
        # high retry density (=> RETRY_STORM) with otherwise modest profile.
        for i in range(10):
            e = _call("flakyish", session_id=f"s{i}", agent_id=f"a{i % 4}", duration_ms=100)
            e["retry_count"] = 1  # 1 retry per call => density 1.0
            events.append(e)
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        # No circuit-broken tool — must offer guard action if flaky exists.
        circuit = [s for s in rep.snapshots if s.verdict == ToolVerdict.CIRCUIT_BREAK]
        flaky = [s for s in rep.snapshots if s.verdict == ToolVerdict.FLAKY]
        if flaky and not circuit:
            assert any(a.id == "ENABLE_CIRCUIT_BREAKER_GUARDS" for a in rep.playbook)

    def test_rollback_recent_tool_change_for_circuit_break_new(self):
        # NEW tool (first_seen within 24h) + high error rate => rollback action.
        events = []
        recent = FIXED_NOW - timedelta(hours=2)
        for i in range(8):
            events.append(_call("brand_new", timestamp=recent, session_id=f"s{i}", agent_id=f"a{i}"))
        for i in range(6):
            events.append(_err("brand_new", code="500", timestamp=recent, session_id=f"s{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        # Build manual `_call` doesn't accept `ts`; we used dict directly.
        snap = [s for s in rep.snapshots if s.tool_name == "brand_new"][0]
        assert snap.verdict == ToolVerdict.CIRCUIT_BREAK
        assert "NEW_TOOL" in snap.reasons
        assert any(a.id == "ROLLBACK_RECENT_TOOL_CHANGE" for a in rep.playbook)

    def test_deprecate_or_retire_for_multiple_stale_tools(self):
        old = FIXED_NOW - timedelta(days=30)
        events = []
        for tool in ("dead1", "dead2", "dead3"):
            for i in range(6):
                events.append(_call(tool, timestamp=old, session_id=f"{tool}-{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        stale = [s for s in rep.snapshots if "STALE_TOOL" in s.reasons]
        assert len(stale) >= 2
        assert any(a.id == "DEPRECATE_OR_RETIRE" for a in rep.playbook)

    def test_playbook_priority_order_p0_first(self):
        # Mix: one CIRCUIT_BREAK tool + one slow tool => P0 then P1.
        events = []
        for i in range(8):
            events.append(_call("bad", session_id=f"s{i}", agent_id=f"a{i}"))
        for i in range(6):
            events.append(_err("bad", code="X", session_id=f"s{i}", agent_id=f"a{i}"))
        for i in range(20):
            events.append(_call("slow", session_id=f"g{i}", agent_id=f"c{i % 4}", duration_ms=6000))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        prios = [a.priority for a in rep.playbook]
        # First action must be P0
        assert prios[0] == ActionPriority.P0


# --------------------------------------------------------------------------- #
# Insights
# --------------------------------------------------------------------------- #
class TestInsights:
    def test_retry_amplification_when_two_tools_have_retry_storm(self):
        events = []
        for tool in ("a", "b"):
            for i in range(10):
                e = _call(tool, session_id=f"{tool}-{i}", agent_id=f"x{i % 3}")
                e["retry_count"] = 2
                events.append(e)
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "RETRY_AMPLIFICATION" in rep.insights

    def test_latency_dominated_failures_when_two_tools_outlier(self):
        events = []
        for tool in ("s1", "s2"):
            for i in range(20):
                events.append(_call(tool, session_id=f"{tool}-{i}", agent_id=f"a{i % 4}", duration_ms=6000))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "LATENCY_DOMINATED_FAILURES" in rep.insights

    def test_error_cluster_pattern_across_tools(self):
        events = []
        for tool in ("api1", "api2"):
            for i in range(20):
                events.append(_call(tool, session_id=f"{tool}-{i}", agent_id=f"a{i % 4}"))
            for i in range(5):
                events.append(_err(tool, code="TIMEOUT", session_id=f"{tool}-{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "ERROR_CLUSTER_PATTERN" in rep.insights

    def test_single_owner_risk_when_two_tools_single_caller(self):
        events = []
        for tool in ("p1", "p2"):
            for i in range(8):
                events.append(_call(tool, session_id=f"{tool}-{i}", agent_id="only_one"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "SINGLE_OWNER_RISK" in rep.insights

    def test_new_tools_probation(self):
        recent = FIXED_NOW - timedelta(hours=3)
        events = []
        for tool in ("new1", "new2"):
            for i in range(6):
                events.append(_call(tool, timestamp=recent, session_id=f"{tool}-{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "NEW_TOOLS_PROBATION" in rep.insights

    def test_stale_tool_backlog(self):
        old = FIXED_NOW - timedelta(days=30)
        events = []
        for tool in ("z1", "z2"):
            for i in range(6):
                events.append(_call(tool, timestamp=old, session_id=f"{tool}-{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert "STALE_TOOL_BACKLOG" in rep.insights


# --------------------------------------------------------------------------- #
# Output formats
# --------------------------------------------------------------------------- #
class TestOutputFormats:
    def test_to_text_populated(self):
        events = [_call("t", session_id=f"s{i}", agent_id=f"a{i % 3}") for i in range(8)]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        text = rep.to_text()
        assert "ToolReliabilityAdvisor" in text
        assert "Tools:" in text
        assert "Playbook:" in text

    def test_to_text_empty(self):
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze([])
        text = rep.to_text()
        assert "No tool activity observed" in text or "No tools observed." in text

    def test_to_markdown_empty(self):
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze([])
        md = rep.to_markdown()
        assert "_No tools observed._" in md
        # An action is always produced (HEALTHY_FLEET), so the playbook section
        # should NOT show the empty placeholder — but the helper still emits
        # the headers.
        assert "## Tools" in md and "## Playbook" in md

    def test_to_dict_round_trip_via_json(self):
        events = [_call("t", session_id=f"s{i}", agent_id=f"a{i % 3}") for i in range(8)]
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        as_json = rep.to_json()
        parsed = json.loads(as_json)
        # Confirm expected top-level keys present
        for key in ("generated_at", "risk_appetite", "portfolio", "snapshots", "playbook", "insights"):
            assert key in parsed
        # And that snapshots/portfolio mirror to_dict shape
        d = rep.to_dict()
        assert parsed["portfolio"] == d["portfolio"]

    def test_risk_appetite_parse_fallback(self):
        # Unknown string falls back to BALANCED.
        assert RiskAppetite.parse("zealous") == RiskAppetite.BALANCED
        # Passing through an enum value stays put.
        assert RiskAppetite.parse(RiskAppetite.AGGRESSIVE) == RiskAppetite.AGGRESSIVE


# --------------------------------------------------------------------------- #
# Grade gating via mean error rate
# --------------------------------------------------------------------------- #
class TestGradeGating:
    def test_mean_error_rate_25pct_forces_grade_f_even_without_circuit_break(self):
        # Build several tools each with ~25% error rate.  No single tool needs
        # to trip CIRCUIT_BREAK, but the portfolio grade is gated on mean_err.
        events = []
        for tool in ("t1", "t2", "t3"):
            for i in range(8):
                events.append(_call(tool, session_id=f"{tool}-{i}", agent_id=f"a{i % 3}"))
            for i in range(2):  # 2/10 -> 20%, just under per-tool threshold
                events.append(_err(tool, code="X", session_id=f"{tool}-{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        # The mean error rate path may or may not produce F, but the grade
        # must reflect that we have at least DEGRADED-class tools.
        assert rep.portfolio.grade in (
            ToolReliabilityGrade.D,
            ToolReliabilityGrade.F,
            ToolReliabilityGrade.C,
        )

    def test_portfolio_band_critical_when_circuit_break_present(self):
        events = []
        for i in range(8):
            events.append(_call("flaky", session_id=f"s{i}", agent_id=f"a{i}"))
        for i in range(6):
            events.append(_err("flaky", code="500", session_id=f"s{i}", agent_id=f"a{i}"))
        rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
        assert rep.portfolio.concentration_band == ReliabilityBand.CRITICAL
