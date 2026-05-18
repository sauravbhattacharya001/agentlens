"""Tests for agentlens.agent_loop_detector.AgentLoopDetector."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.agent_loop_detector import (
    AgentLoopDetector,
    AgentLoopReport,
    LoopGrade,
    LoopIssueCode,
    LoopVerdict,
    ActionPriority,
)


FIXED_NOW = datetime(2026, 5, 18, 9, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


def _ev(sid: str, etype: str, *, offset_s: int = 0, tool_name: str | None = None,
        tool_input: dict | None = None, error_message: str | None = None,
        decision_key: str | None = None, output_text: str | None = None) -> dict:
    ts = FIXED_NOW - timedelta(seconds=300 - offset_s)
    ev: dict = {
        "session_id": sid,
        "event_type": etype,
        "timestamp": ts,
    }
    if tool_name is not None:
        ev["tool_call"] = {
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        }
    if error_message is not None:
        ev["error_message"] = error_message
    if decision_key is not None:
        ev["decision_trace"] = {"key": decision_key, "reasoning": decision_key}
    if output_text is not None:
        ev["output_data"] = {"content": output_text}
    return ev


# ---------------------------------------------------------------------------
# 1. Empty input
# ---------------------------------------------------------------------------

def test_empty_input_grade_a_no_action_needed() -> None:
    adv = AgentLoopDetector(now_fn=_now)
    rep = adv.analyze([])
    assert rep.total_traces == 0
    assert rep.grade is LoopGrade.A
    assert rep.snapshots == []
    assert rep.playbook
    assert rep.playbook[0].id == "no_action_needed"
    assert rep.playbook[0].priority is ActionPriority.P3


# ---------------------------------------------------------------------------
# 2. Healthy single session
# ---------------------------------------------------------------------------

def test_single_healthy_session() -> None:
    evs = [_ev("s1", "tool_call", offset_s=i * 10, tool_name=f"tool_{i}") for i in range(5)]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    assert len(rep.snapshots) == 1
    snap = rep.snapshots[0]
    assert snap.verdict is LoopVerdict.HEALTHY
    assert rep.grade is LoopGrade.A
    assert rep.looping_traces == 0


# ---------------------------------------------------------------------------
# 3. Repeated identical tool call -> TIGHT_LOOP
# ---------------------------------------------------------------------------

def test_tight_loop_repeated_tool_call() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 5,
            tool_name="search_web", tool_input={"q": "weather"})
        for i in range(4)
    ]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    snap = rep.snapshots[0]
    assert snap.verdict is LoopVerdict.TIGHT_LOOP
    assert snap.priority is ActionPriority.P1
    assert any(i.code is LoopIssueCode.REPEATED_TOOL_CALL for i in snap.issues)
    ids = {a.id for a in rep.playbook}
    assert "cap_tool_invocations" in ids


# ---------------------------------------------------------------------------
# 4. Infinite loop suspected -> P0 + grade F
# ---------------------------------------------------------------------------

def test_infinite_loop_force_terminate() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 2,
            tool_name="search_web", tool_input={"q": "weather"})
        for i in range(10)
    ]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    snap = rep.snapshots[0]
    assert snap.verdict is LoopVerdict.INFINITE_LOOP_SUSPECTED
    assert snap.priority is ActionPriority.P0
    assert rep.grade is LoopGrade.F
    ids = {a.id for a in rep.playbook}
    assert "force_terminate_loops" in ids


# ---------------------------------------------------------------------------
# 5. Bouncing tool pair A<->B
# ---------------------------------------------------------------------------

def test_bouncing_tool_pair() -> None:
    seq = ["A", "B"] * 5  # 10 alternations
    evs = [_ev("s1", "tool_call", offset_s=i * 3, tool_name=seq[i]) for i in range(len(seq))]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    snap = rep.snapshots[0]
    assert snap.verdict in (LoopVerdict.INFINITE_LOOP_SUSPECTED, LoopVerdict.TIGHT_LOOP)
    assert any(i.code is LoopIssueCode.BOUNCING_TOOL_PAIR for i in snap.issues)


# ---------------------------------------------------------------------------
# 6. Error storm
# ---------------------------------------------------------------------------

def test_error_storm() -> None:
    evs = [
        _ev("s1", "error", offset_s=i * 5, error_message="ConnectionError: refused")
        for i in range(5)
    ]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    snap = rep.snapshots[0]
    assert snap.verdict is LoopVerdict.ERROR_STORM
    assert snap.priority is ActionPriority.P0
    ids = {a.id for a in rep.playbook}
    assert "triage_error_storm" in ids


# ---------------------------------------------------------------------------
# 7. Risk appetite monotonicity
# ---------------------------------------------------------------------------

def test_risk_appetite_monotonic() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 5,
            tool_name="search_web", tool_input={"q": "x"})
        for i in range(5)
    ]
    risks = {}
    for app in ("cautious", "balanced", "aggressive"):
        rep = AgentLoopDetector(risk_appetite=app, now_fn=_now).analyze(copy.deepcopy(evs))
        risks[app] = rep.snapshots[0].loop_risk
    assert risks["cautious"] >= risks["balanced"] >= risks["aggressive"]


# ---------------------------------------------------------------------------
# 8. Cross-trace tool cluster insight + circuit_break
# ---------------------------------------------------------------------------

def test_tool_loop_cluster_circuit_break() -> None:
    evs = []
    for sid in ("s1", "s2"):
        evs.extend(
            _ev(sid, "tool_call", offset_s=i * 4,
                tool_name="flaky_api", tool_input={"q": sid})
            for i in range(5)
        )
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    assert any("TOOL_LOOP_CLUSTER" in i for i in rep.insights)
    ids = {a.id for a in rep.playbook}
    assert "circuit_break_tool" in ids


# ---------------------------------------------------------------------------
# 9. JSON byte-stability
# ---------------------------------------------------------------------------

def test_json_byte_stable() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 5,
            tool_name="t", tool_input={"q": "x"})
        for i in range(4)
    ]
    a = AgentLoopDetector(now_fn=_now).analyze(copy.deepcopy(evs)).render_json()
    b = AgentLoopDetector(now_fn=_now).analyze(copy.deepcopy(evs)).render_json()
    assert a == b
    parsed = json.loads(a)
    assert "snapshots" in parsed
    assert "grade" in parsed


# ---------------------------------------------------------------------------
# 10. Markdown sections
# ---------------------------------------------------------------------------

def test_markdown_sections() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 3,
            tool_name="t", tool_input={"q": "x"})
        for i in range(6)
    ]
    md = AgentLoopDetector(now_fn=_now).analyze(evs).render_markdown()
    assert "## Summary" in md
    assert "## Playbook" in md
    assert "Looping traces" in md


# ---------------------------------------------------------------------------
# 11. Text renderer mentions looping vs healthy
# ---------------------------------------------------------------------------

def test_text_renderer_clean_vs_loop() -> None:
    rep_clean = AgentLoopDetector(now_fn=_now).analyze([
        _ev("s1", "tool_call", offset_s=i * 10, tool_name=f"t{i}") for i in range(3)
    ])
    text_clean = rep_clean.render_text()
    assert "No looping traces detected." in text_clean

    rep_loop = AgentLoopDetector(now_fn=_now).analyze([
        _ev("s1", "tool_call", offset_s=i * 3, tool_name="t", tool_input={"q": "x"})
        for i in range(5)
    ])
    text_loop = rep_loop.render_text()
    assert "Looping traces:" in text_loop


# ---------------------------------------------------------------------------
# 12. Inputs not mutated
# ---------------------------------------------------------------------------

def test_inputs_not_mutated() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 3, tool_name="t", tool_input={"q": "x"})
        for i in range(5)
    ]
    snapshot = copy.deepcopy(evs)
    AgentLoopDetector(now_fn=_now).analyze(evs)
    assert evs == snapshot


# ---------------------------------------------------------------------------
# 13. Mixed dict + AgentEvent
# ---------------------------------------------------------------------------

def test_mixed_dict_and_agent_event() -> None:
    pytest.importorskip("pydantic")
    from agentlens.models import AgentEvent, ToolCall

    ae = AgentEvent(
        session_id="s1",
        event_type="tool_call",
        timestamp=FIXED_NOW - timedelta(seconds=200),
        tool_call=ToolCall(tool_name="t", tool_input={"q": "x"}),
    )
    raw = _ev("s1", "tool_call", offset_s=50, tool_name="t", tool_input={"q": "x"})
    rep = AgentLoopDetector(now_fn=_now).analyze([ae, raw, raw, raw])
    assert len(rep.snapshots) == 1
    assert rep.snapshots[0].tool_call_count == 4


# ---------------------------------------------------------------------------
# 14. dominant_signature populated for TIGHT_LOOP
# ---------------------------------------------------------------------------

def test_dominant_signature_for_tight_loop() -> None:
    evs = [
        _ev("s1", "tool_call", offset_s=i * 3,
            tool_name="search_web", tool_input={"q": "weather"})
        for i in range(5)
    ]
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    snap = rep.snapshots[0]
    assert snap.verdict in (LoopVerdict.TIGHT_LOOP, LoopVerdict.INFINITE_LOOP_SUSPECTED)
    assert snap.dominant_signature is not None
    assert "search_web" in snap.dominant_signature


# ---------------------------------------------------------------------------
# 15. Grade F gating on any infinite, regardless of count
# ---------------------------------------------------------------------------

def test_grade_f_on_any_infinite() -> None:
    # 1 infinite-looping session alongside many healthy ones still flips F.
    evs = []
    for sid in ("h1", "h2", "h3"):
        evs.extend(_ev(sid, "tool_call", offset_s=i * 10, tool_name=f"t{i}_{sid}") for i in range(3))
    evs.extend(
        _ev("loop", "tool_call", offset_s=i * 2,
            tool_name="search_web", tool_input={"q": "y"})
        for i in range(10)
    )
    rep = AgentLoopDetector(now_fn=_now).analyze(evs)
    assert rep.infinite_suspected_count == 1
    assert rep.grade is LoopGrade.F


# ---------------------------------------------------------------------------
# 16. Public API re-export
# ---------------------------------------------------------------------------

def test_public_reexport() -> None:
    import agentlens
    assert hasattr(agentlens, "AgentLoopDetector")
    assert hasattr(agentlens, "AgentLoopReport")
    assert hasattr(agentlens, "LoopVerdict")
