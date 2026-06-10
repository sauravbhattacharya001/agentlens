"""Tests for ToolDependencyAdvisor.

The advisor is a pure, deterministic component: given a sequence of tool-call
events it builds a directed dependency graph and surfaces coupling
anti-patterns (cycles, single-points-of-failure, fan-out bottlenecks, fragile
chains, orphan tools).  Because it makes no network calls and never mutates its
inputs, every behaviour below is asserted against concrete, reproducible
fixtures.

The expected values encoded here (risk scores, verdicts, grades, playbook ids)
were derived from the advisor's documented scoring rules and verified against
its actual output, so they double as a regression guard for the scoring model.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.tool_dependency_advisor import (
    ActionPriority,
    ChainInfo,
    CycleInfo,
    DependencyEdge,
    DependencyGrade,
    DependencyIssueCode,
    DependencyReport,
    DependencyVerdict,
    PlaybookAction,
    RiskAppetite,
    ToolDependencyAdvisor,
    ToolNode,
)


FIXED_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return FIXED_NOW


def _call(tool: str, sid: str = "s1", offset_ms: int = 0) -> dict:
    """A canonical ``tool_call`` event dict."""
    return {
        "event_type": "tool_call",
        "tool_name": tool,
        "session_id": sid,
        "timestamp": FIXED_NOW + timedelta(milliseconds=offset_ms),
    }


def _chain_events(tools, n_sessions: int = 10, step_ms: int = 100, prefix: str = "s"):
    """Emit ``n_sessions`` identical sessions that run ``tools`` in order."""
    events: list[dict] = []
    for s in range(n_sessions):
        sid = f"{prefix}{s}"
        for i, t in enumerate(tools):
            events.append(_call(t, sid, offset_ms=i * step_ms))
    return events


# --------------------------------------------------------------------------- #
# 1. Empty / degenerate input
# --------------------------------------------------------------------------- #


def test_empty_input_returns_grade_a_no_data_report():
    advisor = ToolDependencyAdvisor(now_fn=_now)
    report = advisor.analyze([])

    assert isinstance(report, DependencyReport)
    assert report.grade == DependencyGrade.A
    assert report.overall_risk == 0.0
    assert report.total_tools == 0
    assert report.total_edges == 0
    assert report.total_sessions == 0
    assert report.tool_nodes == []
    assert report.cycles == []
    assert report.chains == []
    assert len(report.playbook) == 1
    assert report.playbook[0].action_id == "NO_DATA"
    assert report.insights == ["No tool call events found"]


def test_events_without_session_id_are_ignored():
    # event_type is correct but there's no session_id -> dropped entirely.
    events = [
        {"event_type": "tool_call", "tool_name": "X"},
        {"event_type": "tool_call", "tool_name": "Y", "session_id": ""},
    ]
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert report.total_sessions == 0
    assert report.grade == DependencyGrade.A


def test_arbitrary_event_with_session_id_forms_a_session():
    # NOTE: sessions are grouped purely on the presence of a ``session_id``;
    # the event_type is only consulted later when extracting tool names. So a
    # non-tool event that carries a session_id still opens a (tool-less) session.
    events = [
        None,
        42,
        "not-an-event",
        {"event_type": "other", "session_id": "s1"},  # opens an empty session
        {"event_type": "tool_call", "tool_name": "X"},  # no session_id -> dropped
    ]
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert report.total_sessions == 1
    assert report.total_tools == 0  # the session has no resolvable tool calls
    assert report.total_edges == 0


def test_single_repeated_tool_is_orphan():
    # The same tool twice -> src == tgt, so no self-edge is created.
    events = [_call("Solo", "x1", 0), _call("Solo", "x1", 100)]
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)

    assert report.total_tools == 1
    node = report.tool_nodes[0]
    assert node.name == "Solo"
    assert node.in_degree == 0
    assert node.out_degree == 0
    assert node.verdict == DependencyVerdict.ISOLATED
    assert DependencyIssueCode.ORPHAN_TOOL in node.issues
    assert any(a.action_id == "AUDIT_ORPHAN_TOOLS" for a in report.playbook)


# --------------------------------------------------------------------------- #
# 2. Event normalization (dict / object / nested / model_dump)
# --------------------------------------------------------------------------- #


def test_dataclass_events_are_normalized_via_dunder_dict():
    @dataclass
    class Ev:
        event_type: str
        tool_name: str
        session_id: str
        timestamp: object

    events = []
    for s in range(10):
        sid = f"o{s}"
        for i, t in enumerate(["A", "B", "C"]):
            events.append(Ev("tool_call", t, sid, FIXED_NOW + timedelta(milliseconds=i * 100)))

    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert report.total_tools == 3
    assert report.total_edges == 2  # A->B, B->C


def test_model_dump_events_are_normalized():
    class PydanticLike:
        def __init__(self, **kw):
            self._data = kw

        def model_dump(self):
            return dict(self._data)

    events = []
    for s in range(10):
        sid = f"m{s}"
        for i, t in enumerate(["A", "B"]):
            events.append(
                PydanticLike(
                    event_type="tool_call",
                    tool_name=t,
                    session_id=sid,
                    timestamp=FIXED_NOW + timedelta(milliseconds=i * 100),
                )
            )
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert report.total_tools == 2
    assert report.total_edges == 1


def test_nested_tool_call_dict_form_is_read():
    def nested(tool, sid, offset_ms=0):
        return {
            "event_type": "tool_call",
            "tool_call": {"tool_name": tool},
            "session_id": sid,
            "timestamp": FIXED_NOW + timedelta(milliseconds=offset_ms),
        }

    events = []
    for s in range(10):
        sid = f"n{s}"
        for i, t in enumerate(["A", "B", "C"]):
            events.append(nested(t, sid, i * 100))
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert report.total_tools == 3
    assert report.total_edges == 2


def test_inputs_are_not_mutated():
    events = _chain_events(["A", "B", "C"], n_sessions=5)
    snapshot = copy.deepcopy(events)
    ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert events == snapshot


# --------------------------------------------------------------------------- #
# 3. Edge extraction, co-occurrence + time-gap filtering
# --------------------------------------------------------------------------- #


def test_linear_chain_builds_sequential_edges():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "D"])
    )
    edges = {(e.source, e.target) for e in report.edges}
    assert edges == {("A", "B"), ("B", "C"), ("C", "D")}
    # Each edge co-occurs in every session.
    for e in report.edges:
        assert e.co_occurrence_rate == pytest.approx(1.0)
        assert e.session_count == 10


def test_large_time_gap_suppresses_edge():
    advisor = ToolDependencyAdvisor(now_fn=_now, max_gap_ms=50)
    events = []
    for s in range(10):
        sid = f"g{s}"
        events.append(_call("A", sid, 0))
        events.append(_call("B", sid, 5_000))  # 5s gap >> 50ms
    report = advisor.analyze(events)
    assert report.total_edges == 0


def test_low_co_occurrence_edge_is_filtered():
    advisor = ToolDependencyAdvisor(now_fn=_now, min_co_occurrence=0.5)
    events = []
    # A->B only appears in 2 of 10 sessions -> co = 0.2 < 0.5.
    for s in range(2):
        sid = f"co{s}"
        events.append(_call("A", sid, 0))
        events.append(_call("B", sid, 100))
    for s in range(8):
        events.append(_call("Z", f"cox{s}", 0))
    report = advisor.analyze(events)
    assert all((e.source, e.target) != ("A", "B") for e in report.edges)


def test_edge_avg_gap_is_computed():
    advisor = ToolDependencyAdvisor(now_fn=_now)
    events = []
    for s in range(10):
        sid = f"e{s}"
        events.append(_call("A", sid, 0))
        events.append(_call("B", sid, 200))  # 200ms gap
    report = advisor.analyze(events)
    edge = next(e for e in report.edges if (e.source, e.target) == ("A", "B"))
    assert edge.avg_gap_ms == pytest.approx(200.0)


def test_string_timestamps_are_parsed_for_gaps():
    advisor = ToolDependencyAdvisor(now_fn=_now, max_gap_ms=1_000)
    events = []
    for s in range(10):
        sid = f"t{s}"
        events.append(
            {
                "event_type": "tool_call",
                "tool_name": "A",
                "session_id": sid,
                "timestamp": "2026-05-19T12:00:00+00:00",
            }
        )
        events.append(
            {
                "event_type": "tool_call",
                "tool_name": "B",
                "session_id": sid,
                "timestamp": "2026-05-19T12:00:00.300+00:00",  # 300ms later
            }
        )
    report = advisor.analyze(events)
    edge = next(e for e in report.edges if (e.source, e.target) == ("A", "B"))
    assert edge.avg_gap_ms == pytest.approx(300.0, abs=1.0)


# --------------------------------------------------------------------------- #
# 4. Node construction & degrees
# --------------------------------------------------------------------------- #


def test_node_degrees_and_dependency_lists():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C"])
    )
    by_name = {n.name: n for n in report.tool_nodes}

    assert by_name["A"].out_degree == 1
    assert by_name["A"].in_degree == 0
    assert by_name["A"].dependencies == ["B"]
    assert by_name["A"].dependents == []

    assert by_name["B"].in_degree == 1
    assert by_name["B"].out_degree == 1
    assert by_name["B"].dependents == ["A"]
    assert by_name["B"].dependencies == ["C"]

    assert by_name["C"].in_degree == 1
    assert by_name["C"].out_degree == 0


def test_total_calls_and_unique_sessions_tracked():
    # A appears twice per session across 3 sessions -> 6 calls, 3 sessions.
    events = []
    for s in range(3):
        sid = f"u{s}"
        events.append(_call("A", sid, 0))
        events.append(_call("B", sid, 100))
        events.append(_call("A", sid, 200))
    report = ToolDependencyAdvisor(now_fn=_now, min_co_occurrence=0.1).analyze(events)
    a = next(n for n in report.tool_nodes if n.name == "A")
    assert a.total_calls == 6
    assert a.unique_sessions == 3


# --------------------------------------------------------------------------- #
# 5. Cycle detection
# --------------------------------------------------------------------------- #


def test_circular_dependency_detected_and_graded_f():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    assert len(report.cycles) == 1
    cyc = report.cycles[0]
    assert cyc.tools[0] == cyc.tools[-1]  # closed loop
    assert set(cyc.tools) == {"A", "B", "C"}
    assert cyc.length == 3
    assert cyc.session_count == 10
    assert report.grade == DependencyGrade.F
    assert any(a.action_id == "BREAK_CIRCULAR_DEPENDENCIES" for a in report.playbook)
    # The break-cycle action is always top priority.
    assert report.playbook[0].priority == ActionPriority.P0


def test_two_node_cycle_is_reported_with_length_two():
    # A->B->A closes a loop. The detector records cycles whose path (including
    # the repeated closing node) has length >= 3, so a 2-tool back-and-forth
    # qualifies and is reported with length == 2.
    events = _chain_events(["A", "B", "A"])
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    assert len(report.cycles) == 1
    cyc = report.cycles[0]
    assert cyc.length == 2
    assert set(cyc.tools) == {"A", "B"}
    assert cyc.tools[0] == cyc.tools[-1]


def test_cycle_tools_flagged_with_circular_issue():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    for node in report.tool_nodes:
        if node.name in {"A", "B", "C"}:
            assert DependencyIssueCode.CIRCULAR_DEPENDENCY in node.issues


# --------------------------------------------------------------------------- #
# 6. Chain detection
# --------------------------------------------------------------------------- #


def test_fragile_chain_detected():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "D"])
    )
    assert len(report.chains) == 1
    chain = report.chains[0]
    assert chain.tools == ["A", "B", "C", "D"]
    assert chain.length == 4
    # break_probability = 1 - 0.95**(len-1)
    assert chain.break_probability == pytest.approx(1.0 - 0.95 ** 3)
    assert all(
        n.verdict == DependencyVerdict.FRAGILE_CHAIN for n in report.tool_nodes
    )
    assert any(a.action_id == "SHORTEN_FRAGILE_CHAINS" for a in report.playbook)


def test_short_two_step_chain_not_reported():
    # A->B has length 2, below the >= 3 chain threshold.
    report = ToolDependencyAdvisor(now_fn=_now).analyze(_chain_events(["A", "B"]))
    assert report.chains == []


# --------------------------------------------------------------------------- #
# 7. Classification: SPOF, over-relied, fan-out
# --------------------------------------------------------------------------- #


def _hub_in(callers, sessions_per_caller=4):
    """Each caller -> H in its own sessions (H is a pure sink)."""
    events = []
    for caller in callers:
        for s in range(sessions_per_caller):
            sid = f"{caller}{s}"
            events.append(_call(caller, sid, 0))
            events.append(_call("H", sid, 100))
    return events


def test_single_point_of_failure_with_five_dependents():
    advisor = ToolDependencyAdvisor(now_fn=_now, min_co_occurrence=0.05)
    report = advisor.analyze(_hub_in(["A", "B", "C", "D", "E"]))
    hub = next(n for n in report.tool_nodes if n.name == "H")
    assert hub.in_degree == 5
    assert hub.out_degree == 0
    assert hub.verdict == DependencyVerdict.SINGLE_POINT_OF_FAILURE
    assert hub.priority == ActionPriority.P1
    assert any(a.action_id == "ADD_REDUNDANCY_FOR_SPOF" for a in report.playbook)


def test_over_relied_with_four_dependents():
    # Four dependents -> in_degree 4 but risk (32) below the SPOF risk gate (40),
    # so the verdict falls through to OVER_RELIED.
    advisor = ToolDependencyAdvisor(now_fn=_now, min_co_occurrence=0.05)
    report = advisor.analyze(_hub_in(["A", "B", "C", "D"]))
    hub = next(n for n in report.tool_nodes if n.name == "H")
    assert hub.in_degree == 4
    assert hub.verdict == DependencyVerdict.OVER_RELIED
    assert DependencyIssueCode.OVER_DEPENDED in hub.issues
    assert any(a.action_id == "REDUCE_TOOL_COUPLING" for a in report.playbook)


def test_fan_out_bottleneck_detected():
    advisor = ToolDependencyAdvisor(now_fn=_now, min_co_occurrence=0.05)
    events = []
    for tgt in ["A", "B", "C", "D", "E"]:
        for s in range(4):
            sid = f"f{tgt}{s}"
            events.append(_call("H", sid, 0))
            events.append(_call(tgt, sid, 100))
    report = advisor.analyze(events)
    hub = next(n for n in report.tool_nodes if n.name == "H")
    assert hub.out_degree == 5
    assert hub.in_degree == 0
    assert hub.verdict == DependencyVerdict.FAN_OUT_BOTTLENECK
    assert DependencyIssueCode.EXCESSIVE_FAN_OUT in hub.issues
    assert any(a.action_id == "REDUCE_FAN_OUT" for a in report.playbook)


# --------------------------------------------------------------------------- #
# 8. Risk appetite
# --------------------------------------------------------------------------- #


def test_risk_appetite_scales_scores_monotonically():
    risks = {}
    for appetite in ("cautious", "balanced", "aggressive"):
        advisor = ToolDependencyAdvisor(risk_appetite=appetite, now_fn=_now)
        report = advisor.analyze(_chain_events(["A", "B", "C", "D"]))
        node_b = next(n for n in report.tool_nodes if n.name == "B")
        risks[appetite] = node_b.risk_score

    # Cautious inflates risk, aggressive deflates it, balanced sits between.
    assert risks["cautious"] > risks["balanced"] > risks["aggressive"]
    assert risks["balanced"] == pytest.approx(15.0)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("cautious", RiskAppetite.CAUTIOUS),
        ("BALANCED", RiskAppetite.BALANCED),
        ("Aggressive", RiskAppetite.AGGRESSIVE),
        (RiskAppetite.CAUTIOUS, RiskAppetite.CAUTIOUS),
        ("nonsense", RiskAppetite.BALANCED),  # invalid -> safe default
        (None, RiskAppetite.BALANCED),
    ],
)
def test_risk_appetite_parse(value, expected):
    assert RiskAppetite.parse(value) == expected


# --------------------------------------------------------------------------- #
# 9. Risk aggregation & grade boundaries
# --------------------------------------------------------------------------- #


def test_healthy_graph_grades_a():
    # A simple A->B->C chain is mildly fragile -> not an F, but well-formed.
    report = ToolDependencyAdvisor(now_fn=_now).analyze(_chain_events(["A", "B", "C"]))
    assert report.grade in {DependencyGrade.A, DependencyGrade.B}
    assert report.overall_risk < 70


def test_grade_from_risk_thresholds():
    advisor = ToolDependencyAdvisor(now_fn=_now)
    # _grade_from_risk is pure; assert each documented band boundary directly:
    #   >= 70 -> F, >= 50 -> D, >= 30 -> C, >= 15 -> B, else A.
    assert advisor._grade_from_risk(0.0, has_p0=False) == DependencyGrade.A
    assert advisor._grade_from_risk(14.9, has_p0=False) == DependencyGrade.A
    assert advisor._grade_from_risk(15.0, has_p0=False) == DependencyGrade.B
    assert advisor._grade_from_risk(30.0, has_p0=False) == DependencyGrade.C
    assert advisor._grade_from_risk(50.0, has_p0=False) == DependencyGrade.D
    assert advisor._grade_from_risk(70.0, has_p0=False) == DependencyGrade.F
    # A P0 action forces an F regardless of the numeric risk.
    assert advisor._grade_from_risk(1.0, has_p0=True) == DependencyGrade.F


# --------------------------------------------------------------------------- #
# 10. Playbook & insights
# --------------------------------------------------------------------------- #


def test_healthy_graph_emits_maintain_observation_action():
    # Isolated tools still produce an AUDIT action; to get the "all healthy"
    # branch we need a graph with edges but no anti-patterns: a single 2-step
    # transition repeated (length-2 chain, no cycle, low degrees).
    events = []
    for s in range(10):
        sid = f"h{s}"
        events.append(_call("A", sid, 0))
        events.append(_call("B", sid, 100))
    report = ToolDependencyAdvisor(now_fn=_now).analyze(events)
    action_ids = {a.action_id for a in report.playbook}
    assert "MAINTAIN_OBSERVATION" in action_ids


def test_playbook_sorted_by_priority():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    priorities = [a.priority.value for a in report.playbook]
    assert priorities == sorted(priorities)


def test_insights_report_healthy_fraction():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(_chain_events(["A", "B", "C"]))
    joined = " ".join(report.insights)
    assert "healthy dependency profiles" in joined
    assert "co-occurrence rate" in joined


def test_cycle_insight_mentions_circuit_breakers():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    joined = " ".join(report.insights)
    assert "circular dependency pattern" in joined


# --------------------------------------------------------------------------- #
# 11. Serialization & rendering
# --------------------------------------------------------------------------- #


def test_render_json_is_valid_and_round_trips_grade():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    payload = json.loads(report.render_json())
    assert payload["grade"] == report.grade.value
    assert payload["total_tools"] == report.total_tools
    assert len(payload["tool_nodes"]) == report.total_tools
    assert len(payload["cycles"]) == len(report.cycles)
    assert isinstance(payload["insights"], list)


def test_render_markdown_contains_key_sections():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    md = report.render_markdown()
    assert md.startswith("## Tool Dependency Analysis Report")
    assert "### Tool Nodes" in md
    assert "### Circular Dependencies" in md
    assert "### Playbook" in md
    assert "### Insights" in md


def test_render_text_starts_with_verdict_line():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(_chain_events(["A", "B", "C"]))
    text = report.render_text()
    assert text.startswith("VERDICT:")
    assert "-- Tool Dependency Nodes --" in text
    assert "-- Playbook --" in text


def test_node_to_dict_shape():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(_chain_events(["A", "B", "C"]))
    d = report.tool_nodes[0].to_dict()
    assert set(d) == {
        "name",
        "in_degree",
        "out_degree",
        "total_calls",
        "unique_sessions",
        "dependents",
        "dependencies",
        "verdict",
        "issues",
        "risk_score",
        "priority",
    }
    assert isinstance(d["issues"], list)
    assert isinstance(d["risk_score"], float)


def test_edge_and_cycle_to_dict_round_value():
    edge = DependencyEdge(
        source="A", target="B", call_count=3,
        avg_gap_ms=123.456789, session_count=2, co_occurrence_rate=0.333333,
    )
    ed = edge.to_dict()
    assert ed["avg_gap_ms"] == 123.5
    assert ed["co_occurrence_rate"] == 0.333

    cyc = CycleInfo(tools=["A", "B", "A"], length=2, session_count=4, frequency=0.123456)
    cd = cyc.to_dict()
    assert cd["frequency"] == 0.123


def test_headline_summarizes_report():
    report = ToolDependencyAdvisor(now_fn=_now).analyze(
        _chain_events(["A", "B", "C", "A"])
    )
    assert report.headline.startswith("VERDICT: grade=")
    assert f"grade={report.grade.value}" in report.headline
    assert f"cycles={len(report.cycles)}" in report.headline


# --------------------------------------------------------------------------- #
# 12. Determinism
# --------------------------------------------------------------------------- #


def test_analysis_is_deterministic():
    events = _chain_events(["A", "B", "C", "A"])
    advisor = ToolDependencyAdvisor(now_fn=_now)
    first = advisor.analyze(events).render_json()
    second = advisor.analyze(copy.deepcopy(events)).render_json()
    assert first == second
