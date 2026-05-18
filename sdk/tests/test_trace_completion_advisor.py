"""Tests for agentlens.trace_completion_advisor.TraceCompletionAdvisor."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.trace_completion_advisor import (
    ActionPriority,
    CompletionGrade,
    RiskAppetite,
    TraceCompletionAdvisor,
    TraceCompletionReport,
    TraceIssueCode,
    TraceVerdict,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


BASE = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _ev(
    session_id: str,
    event_type: str,
    *,
    offset_s: float = 0.0,
    duration_ms: float | None = None,
    tool_call_id: str | None = None,
    decision: bool = False,
) -> dict:
    out: dict = {
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": (BASE + timedelta(seconds=offset_s)).isoformat(),
    }
    if duration_ms is not None:
        out["duration_ms"] = duration_ms
    if tool_call_id is not None:
        out["tool_call"] = {"tool_call_id": tool_call_id, "tool_name": "x"}
    if decision:
        out["decision_trace"] = {"trace_id": "d1", "reasoning": "because"}
    return out


def _advisor(*, now_offset_s: float = 0.0, **kwargs) -> TraceCompletionAdvisor:
    now = BASE + timedelta(seconds=now_offset_s)
    kwargs.setdefault("now_fn", lambda: now)
    return TraceCompletionAdvisor(**kwargs)


# --------------------------------------------------------------------------- #
# Basic shape
# --------------------------------------------------------------------------- #


def test_empty_input_grade_A_and_no_traces():
    advisor = _advisor()
    report = advisor.analyze([])
    assert isinstance(report, TraceCompletionReport)
    assert report.total_traces == 0
    assert report.completion_rate == 1.0
    assert report.grade is CompletionGrade.A
    assert report.summary == "No traces in window."
    # Playbook still emits at least the "all clear" item.
    assert report.playbook
    assert report.playbook[0].id == "all_clear"


def test_event_without_session_id_is_dropped():
    advisor = _advisor()
    report = advisor.analyze([{"event_type": "tool_call", "timestamp": BASE.isoformat()}])
    assert report.total_traces == 0


# --------------------------------------------------------------------------- #
# Per-verdict classification
# --------------------------------------------------------------------------- #


def test_complete_trace_with_terminal_event():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=1, duration_ms=200),
        _ev("s1", "session_complete", offset_s=2),
    ]
    advisor = _advisor(now_offset_s=5)
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.COMPLETE
    assert t.incompletion_risk == 0
    assert t.priority is ActionPriority.P3
    assert report.completed_traces == 1
    assert report.grade is CompletionGrade.A


def test_hung_trace_with_long_pending_tool_call():
    # A tool_call with no matching result, started long ago.
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "tool_call", offset_s=1, tool_call_id="tc1"),
    ]
    # now = 200s later -> pending op is 199s = 199_000ms, well over 60_000.
    advisor = _advisor(now_offset_s=200)
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.HUNG
    assert t.priority is ActionPriority.P0
    assert any(i.code is TraceIssueCode.HUNG_OPERATION for i in t.issues)
    # Cross-trace playbook surfaces terminate_hung_traces at P0.
    p0 = [a for a in report.playbook if a.priority is ActionPriority.P0]
    assert any(a.id == "terminate_hung_traces" for a in p0)
    assert report.grade is CompletionGrade.F


def test_errored_open_trace():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "error", offset_s=2),
    ]
    advisor = _advisor(now_offset_s=5)
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.ERRORED_OPEN
    assert t.priority is ActionPriority.P0
    assert any(i.code is TraceIssueCode.UNRESOLVED_ERROR for i in t.issues)


def test_abandoned_trace_no_terminal_long_idle():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=10),
    ]
    advisor = _advisor(now_offset_s=2000)  # 1990s idle, > 600 abandon threshold
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.ABANDONED
    assert any(i.code is TraceIssueCode.ABANDONED for i in t.issues)


def test_silent_trace_only_session_start():
    events = [_ev("s1", "session_start", offset_s=0)]
    advisor = _advisor(now_offset_s=300)  # > silent_min_age 120
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.SILENT
    assert any(i.code is TraceIssueCode.SILENT_TRACE for i in t.issues)


def test_near_timeout_trace():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=5),
    ]
    advisor = _advisor(now_offset_s=350)  # age 350s > 300 near_timeout, idle 345 < 600 abandon
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.NEAR_TIMEOUT
    assert any(i.code is TraceIssueCode.NEAR_TIMEOUT for i in t.issues)
    assert t.priority is ActionPriority.P2


def test_in_progress_trace():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=5),
    ]
    advisor = _advisor(now_offset_s=10)
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.verdict is TraceVerdict.IN_PROGRESS
    assert t.priority is ActionPriority.P3


# --------------------------------------------------------------------------- #
# Issue codes
# --------------------------------------------------------------------------- #


def test_orphan_tool_call_paired_with_result():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "tool_call", offset_s=1, tool_call_id="tc1"),
        _ev("s1", "tool_result", offset_s=2, tool_call_id="tc1"),
        _ev("s1", "session_complete", offset_s=3),
    ]
    advisor = _advisor(now_offset_s=5)
    report = advisor.analyze(events)
    [t] = report.traces
    assert t.open_tool_calls == 0
    assert t.verdict is TraceVerdict.COMPLETE


def test_retry_storm_issue_emitted():
    events = [_ev("s1", "session_start", offset_s=0)]
    for i in range(5):
        events.append(_ev("s1", "retry", offset_s=1 + i))
    advisor = _advisor(now_offset_s=10)
    report = advisor.analyze(events)
    [t] = report.traces
    assert any(i.code is TraceIssueCode.RETRY_STORM for i in t.issues)
    # Should surface the break_retry_storm playbook entry.
    assert any(a.id == "break_retry_storm" for a in report.playbook)


def test_no_decision_context_when_tool_call_without_decision():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "tool_call", offset_s=1, tool_call_id="tc1"),
        _ev("s1", "tool_result", offset_s=2, tool_call_id="tc1"),
    ]
    advisor = _advisor(now_offset_s=5)
    report = advisor.analyze(events)
    [t] = report.traces
    assert any(i.code is TraceIssueCode.NO_DECISION_CONTEXT for i in t.issues)


# --------------------------------------------------------------------------- #
# Risk-appetite modulation (monotonic)
# --------------------------------------------------------------------------- #


def test_risk_appetite_monotonic_on_near_timeout():
    # A trace just past the balanced near_timeout window.
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=5),
    ]
    now_offset = 310
    cautious = _advisor(now_offset_s=now_offset, risk_appetite="cautious").analyze(events).traces[0].incompletion_risk
    balanced = _advisor(now_offset_s=now_offset, risk_appetite="balanced").analyze(events).traces[0].incompletion_risk
    aggressive = _advisor(now_offset_s=now_offset, risk_appetite="aggressive").analyze(events).traces[0].incompletion_risk
    assert cautious >= balanced >= aggressive


def test_aggressive_trims_p2_actions_from_playbook():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "llm_call", offset_s=5),
    ]
    # Use balanced to trigger near-timeout (P2 action).
    balanced_report = _advisor(now_offset_s=350).analyze(events)
    assert any(a.priority is ActionPriority.P2 for a in balanced_report.playbook)
    # Aggressive should drop the P2 noise.
    aggressive_report = _advisor(now_offset_s=350, risk_appetite="aggressive").analyze(events)
    assert all(a.priority is not ActionPriority.P2 for a in aggressive_report.playbook)


# --------------------------------------------------------------------------- #
# Portfolio + insights
# --------------------------------------------------------------------------- #


def test_portfolio_grade_F_with_p0_signal():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "tool_call", offset_s=1, tool_call_id="tc1"),  # hung
    ]
    advisor = _advisor(now_offset_s=300)
    report = advisor.analyze(events)
    assert report.grade is CompletionGrade.F


def test_hung_cluster_insight():
    events = []
    for i in range(3):
        sid = f"s{i}"
        events.append(_ev(sid, "session_start", offset_s=0))
        events.append(_ev(sid, "tool_call", offset_s=1, tool_call_id=f"tc{i}"))
    advisor = _advisor(now_offset_s=300)
    report = advisor.analyze(events)
    assert any("HUNG_CLUSTER" in i for i in report.insights)


# --------------------------------------------------------------------------- #
# Renderers / serialisation
# --------------------------------------------------------------------------- #


def test_text_render_contains_summary():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "session_complete", offset_s=1),
    ]
    advisor = _advisor(now_offset_s=2)
    txt = advisor.analyze(events).render_text()
    assert "TraceCompletionAdvisor" in txt
    assert "complete" in txt.lower()


def test_markdown_render_has_required_sections_when_risky():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "tool_call", offset_s=1, tool_call_id="tc1"),
    ]
    md = _advisor(now_offset_s=300).analyze(events).render_markdown()
    assert "# Trace Completion Advisor" in md
    assert "## Summary" in md
    assert "## At-risk traces" in md
    assert "## Playbook" in md


def test_json_round_trip_is_deterministic():
    events = [
        _ev("s1", "session_start", offset_s=0),
        _ev("s1", "error", offset_s=2),
    ]
    a = _advisor(now_offset_s=5).analyze(events).to_json()
    b = _advisor(now_offset_s=5).analyze(events).to_json()
    assert a == b
    # And it parses.
    parsed = json.loads(a)
    assert parsed["total_traces"] == 1
    assert parsed["traces"][0]["verdict"] == "errored_open"


# --------------------------------------------------------------------------- #
# Inputs are never mutated
# --------------------------------------------------------------------------- #


def test_inputs_never_mutated():
    event = _ev("s1", "session_complete", offset_s=0)
    snapshot = json.dumps(event, sort_keys=True)
    _advisor(now_offset_s=1).analyze([event])
    assert json.dumps(event, sort_keys=True) == snapshot


# --------------------------------------------------------------------------- #
# Public re-export
# --------------------------------------------------------------------------- #


def test_public_reexport():
    import agentlens

    assert hasattr(agentlens, "TraceCompletionAdvisor")
    assert hasattr(agentlens, "TraceCompletionReport")
    assert hasattr(agentlens, "TraceVerdict")
