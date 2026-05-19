"""Tests for ToolReliabilityAdvisor."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.tool_reliability_advisor import (
    ActionPriority,
    ReliabilityBand,
    ToolReliabilityAdvisor,
    ToolReliabilityGrade,
    ToolReliabilityReport,
    ToolVerdict,
)


FIXED_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _now():
    return FIXED_NOW


def _call(tool, ts=None, duration_ms=100, session_id="s1", caller="a1"):
    return {
        "event_type": "tool_call",
        "tool_name": tool,
        "session_id": session_id,
        "agent_id": caller,
        "duration_ms": duration_ms,
        "timestamp": ts or FIXED_NOW,
    }


def _error(tool, code="ERR", ts=None, session_id="s1", caller="a1", duration_ms=100):
    return {
        "event_type": "tool_result",
        "tool_name": tool,
        "session_id": session_id,
        "agent_id": caller,
        "duration_ms": duration_ms,
        "error_code": code,
        "timestamp": ts or FIXED_NOW,
    }


# ---- 1. empty input -------------------------------------------------------- #
def test_empty_input_grade_a_empty_fleet():
    advisor = ToolReliabilityAdvisor(now_fn=_now)
    rep = advisor.analyze([])
    assert isinstance(rep, ToolReliabilityReport)
    assert rep.grade == ToolReliabilityGrade.A
    assert "EMPTY_FLEET" in rep.insights
    assert any(a.id == "HEALTHY_FLEET" for a in rep.playbook)
    assert rep.snapshots == []
    assert rep.portfolio.total_tools == 0


# ---- 2. single healthy tool ------------------------------------------------ #
def test_single_healthy_tool():
    # Many distinct callers + sessions, all successful, low latency.
    events = []
    for i in range(20):
        events.append(_call("search", session_id=f"s{i}", caller=f"a{i % 3}", duration_ms=200))
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    assert rep.grade == ToolReliabilityGrade.A
    snap = rep.snapshots[0]
    assert snap.verdict == ToolVerdict.HEALTHY
    assert snap.priority == ActionPriority.P3
    assert snap.error_rate == 0.0
    assert any(a.id == "HEALTHY_FLEET" for a in rep.playbook)


# ---- 3. circuit-break high error rate -------------------------------------- #
def test_circuit_break_on_high_error_rate():
    events = []
    for i in range(8):
        events.append(_call("flaky", session_id=f"s{i}", caller=f"a{i % 3}"))
    for i in range(6):  # 6/8 -> > 20% error rate
        events.append(_error("flaky", code="500", session_id=f"s{i}", caller=f"a{i % 3}"))
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = [s for s in rep.snapshots if s.tool_name == "flaky"][0]
    assert snap.verdict == ToolVerdict.CIRCUIT_BREAK
    assert snap.priority == ActionPriority.P0
    assert "HIGH_ERROR_RATE" in snap.reasons
    assert any(a.id == "CIRCUIT_BREAK_FAILING_TOOLS" for a in rep.playbook)
    assert rep.grade == ToolReliabilityGrade.F
    assert "RELIABILITY_CRISIS" in rep.insights


# ---- 4. latency outlier ---------------------------------------------------- #
def test_latency_outlier_triggers_optimize():
    events = []
    for i in range(20):
        events.append(_call("slowdb", session_id=f"s{i}", caller=f"a{i % 4}", duration_ms=6000))
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "LATENCY_OUTLIER" in snap.reasons
    assert any(a.id == "OPTIMIZE_SLOW_TOOL" for a in rep.playbook)


# ---- 5. retry storm -------------------------------------------------------- #
def test_retry_storm_triggers_tighten_backoff():
    events = []
    for i in range(10):
        e = _call("ratelimited", session_id=f"s{i}", caller=f"a{i % 3}")
        e["retry_count"] = 2
        events.append(e)
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "RETRY_STORM" in snap.reasons
    assert any(a.id == "TIGHTEN_RETRY_BACKOFF" for a in rep.playbook)


# ---- 6. single-caller dependency ------------------------------------------ #
def test_single_caller_dependency_triggers_redundancy():
    events = [_call("solo", session_id=f"s{i}", caller="only_agent") for i in range(8)]
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "SINGLE_CALLER_DEPENDENCY" in snap.reasons
    assert any(a.id == "ADD_REDUNDANT_INTEGRATION" for a in rep.playbook)


# ---- 7. stale tool --------------------------------------------------------- #
def test_stale_tool_detection():
    old_ts = FIXED_NOW - timedelta(days=30)
    events = [_call("ancient", ts=old_ts, session_id=f"s{i}", caller=f"a{i}") for i in range(6)]
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "STALE_TOOL" in snap.reasons
    assert snap.verdict == ToolVerdict.DEPRECATE_CANDIDATE


# ---- 8. new tool ----------------------------------------------------------- #
def test_new_tool_detection():
    recent = FIXED_NOW - timedelta(hours=1)
    events = [_call("freshly_deployed", ts=recent, session_id=f"s{i}", caller=f"a{i}") for i in range(20)]
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "NEW_TOOL" in snap.reasons


# ---- 9. dominant error cluster -------------------------------------------- #
def test_dominant_error_cluster_investigation():
    events = []
    for i in range(20):
        events.append(_call("payment_api", session_id=f"s{i}", caller=f"a{i % 4}"))
    # 4 errors all of same code => 100% dominance, error_rate = 20%
    for i in range(4):
        events.append(_error("payment_api", code="TIMEOUT", session_id=f"s{i}", caller=f"a{i}"))
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    snap = rep.snapshots[0]
    assert "DOMINANT_ERROR_CLUSTER" in snap.reasons
    assert any(a.id == "INVESTIGATE_DOMINANT_ERROR_CLUSTER" for a in rep.playbook)


# ---- 10. risk appetite monotonicity --------------------------------------- #
def test_risk_appetite_monotonic_scoring():
    events = []
    for i in range(20):
        events.append(_call("api", session_id=f"s{i}", caller=f"a{i % 4}", duration_ms=2500))
    for i in range(2):  # ~10% err
        events.append(_error("api", code="X", session_id=f"s{i}", caller=f"a{i}"))
    advisor = ToolReliabilityAdvisor(now_fn=_now)
    cautious = advisor.analyze(events, risk_appetite="cautious").snapshots[0].reliability_score
    balanced = advisor.analyze(events, risk_appetite="balanced").snapshots[0].reliability_score
    aggressive = advisor.analyze(events, risk_appetite="aggressive").snapshots[0].reliability_score
    assert cautious <= balanced <= aggressive


# ---- 11. cautious adds review action --------------------------------------- #
def test_cautious_adds_reliability_review_action():
    # Force grade D with one DEGRADED tool.
    events = []
    for i in range(20):
        events.append(_call("api", session_id=f"s{i}", caller=f"a{i % 3}", duration_ms=6000))
    for i in range(3):  # ~15% err
        events.append(_error("api", code="X", session_id=f"s{i}", caller=f"a{i}"))
    advisor = ToolReliabilityAdvisor(now_fn=_now)
    rep = advisor.analyze(events, risk_appetite="cautious")
    assert rep.grade in (ToolReliabilityGrade.D, ToolReliabilityGrade.F, ToolReliabilityGrade.C)
    assert any(a.id == "SCHEDULE_RELIABILITY_REVIEW" for a in rep.playbook)


# ---- 12. aggressive trims P3 when P0/P1 present ---------------------------- #
def test_aggressive_trims_p3_when_higher_priority_present():
    # P0-triggering tool + harmless second tool that would only generate P3.
    events = []
    for i in range(10):
        events.append(_call("bad", session_id=f"s{i}", caller=f"a{i}"))
    for i in range(8):  # 80% err => CIRCUIT_BREAK
        events.append(_error("bad", code="E", session_id=f"s{i}", caller=f"a{i}"))
    for i in range(15):
        events.append(_call("good", session_id=f"g{i}", caller=f"c{i % 4}"))
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events, risk_appetite="aggressive")
    assert not any(a.id == "HEALTHY_FLEET" for a in rep.playbook)
    assert any(a.priority == ActionPriority.P0 for a in rep.playbook)


# ---- 13. JSON byte-stability ---------------------------------------------- #
def test_json_byte_stability():
    events = []
    for i in range(10):
        events.append(_call("t", session_id=f"s{i}", caller=f"a{i % 3}"))
    advisor = ToolReliabilityAdvisor(now_fn=_now)
    j1 = advisor.analyze(events).to_json()
    j2 = advisor.analyze(events).to_json()
    assert j1 == j2
    # And it parses.
    obj = json.loads(j1)
    assert "portfolio" in obj
    assert "snapshots" in obj


# ---- 14. markdown sections ------------------------------------------------- #
def test_markdown_contains_required_sections():
    events = [_call("api", session_id=f"s{i}", caller=f"a{i}") for i in range(6)]
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    md = rep.to_markdown()
    assert "## Summary" in md
    assert "## Tools" in md
    assert "## Playbook" in md
    assert "## Insights" in md


# ---- 15. never mutates input ---------------------------------------------- #
def test_never_mutates_input_events():
    events = [_call("t", session_id=f"s{i}", caller=f"a{i}") for i in range(5)]
    before = copy.deepcopy(events)
    ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    assert events == before


# ---- 16. accepts attr-bearing objects ------------------------------------- #
class _ObjEvent:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_accepts_attr_bearing_objects():
    events = [
        _ObjEvent(
            event_type="tool_call",
            tool_name="obj_tool",
            session_id=f"s{i}",
            agent_id=f"a{i}",
            duration_ms=200,
            timestamp=FIXED_NOW,
        )
        for i in range(6)
    ]
    rep = ToolReliabilityAdvisor(now_fn=_now).analyze(events)
    assert rep.portfolio.total_tools == 1
    assert rep.snapshots[0].tool_name == "obj_tool"
