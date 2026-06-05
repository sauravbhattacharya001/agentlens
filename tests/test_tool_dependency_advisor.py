"""Tests for ToolDependencyAdvisor."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))

from datetime import datetime, timezone, timedelta
from agentlens.tool_dependency_advisor import (
    ToolDependencyAdvisor,
    DependencyVerdict,
    DependencyIssueCode,
    DependencyGrade,
    ActionPriority,
    RiskAppetite,
)


def _ts(seconds: int) -> str:
    """Generate ISO timestamp offset by seconds from a base time."""
    base = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat()


def _tool_event(session_id: str, tool_name: str, seconds: int) -> dict:
    """Create a minimal tool call event dict."""
    return {
        "session_id": session_id,
        "event_type": "tool_call",
        "tool_call": {"tool_name": tool_name},
        "timestamp": _ts(seconds),
    }


def make_linear_chain_events():
    """Create events showing a linear chain: A -> B -> C -> D across sessions."""
    events = []
    for sid in ["s1", "s2", "s3", "s4"]:
        events.append(_tool_event(sid, "tool_a", 0))
        events.append(_tool_event(sid, "tool_b", 5))
        events.append(_tool_event(sid, "tool_c", 10))
        events.append(_tool_event(sid, "tool_d", 15))
    return events


def make_circular_events():
    """Create events showing circular dependency: A -> B -> C -> A."""
    events = []
    for sid in ["s1", "s2", "s3", "s4"]:
        events.append(_tool_event(sid, "tool_a", 0))
        events.append(_tool_event(sid, "tool_b", 5))
        events.append(_tool_event(sid, "tool_c", 10))
        events.append(_tool_event(sid, "tool_a", 15))
    return events


def make_fan_out_events():
    """Create events showing fan-out: hub calls 5 different tools."""
    events = []
    for sid in ["s1", "s2", "s3", "s4"]:
        events.append(_tool_event(sid, "hub", 0))
        events.append(_tool_event(sid, "spoke_1", 5))
        events.append(_tool_event(sid, "hub", 10))
        events.append(_tool_event(sid, "spoke_2", 15))
        events.append(_tool_event(sid, "hub", 20))
        events.append(_tool_event(sid, "spoke_3", 25))
        events.append(_tool_event(sid, "hub", 30))
        events.append(_tool_event(sid, "spoke_4", 35))
        events.append(_tool_event(sid, "hub", 40))
        events.append(_tool_event(sid, "spoke_5", 45))
    return events


def make_spof_events():
    """Create events where multiple tools all depend on one central tool."""
    events = []
    for sid in ["s1", "s2", "s3", "s4"]:
        events.append(_tool_event(sid, "caller_1", 0))
        events.append(_tool_event(sid, "central_db", 5))
        events.append(_tool_event(sid, "caller_2", 10))
        events.append(_tool_event(sid, "central_db", 15))
        events.append(_tool_event(sid, "caller_3", 20))
        events.append(_tool_event(sid, "central_db", 25))
        events.append(_tool_event(sid, "caller_4", 30))
        events.append(_tool_event(sid, "central_db", 35))
    return events


class TestToolDependencyAdvisorEmpty:
    def test_empty_events(self):
        advisor = ToolDependencyAdvisor()
        report = advisor.analyze([])
        assert report.total_tools == 0
        assert report.total_sessions == 0
        assert report.grade == DependencyGrade.A
        assert report.overall_risk == 0.0

    def test_no_tool_events(self):
        events = [{"session_id": "s1", "event_type": "llm_call", "timestamp": _ts(0)}]
        advisor = ToolDependencyAdvisor()
        report = advisor.analyze(events)
        assert report.total_tools == 0


class TestToolDependencyAdvisorChains:
    def test_detects_linear_chain(self):
        events = make_linear_chain_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        assert report.total_tools >= 4
        assert len(report.chains) >= 1
        longest = max(report.chains, key=lambda c: c.length)
        assert longest.length >= 3

    def test_chain_break_probability(self):
        events = make_linear_chain_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        if report.chains:
            for chain in report.chains:
                assert 0 < chain.break_probability < 1


class TestToolDependencyAdvisorCycles:
    def test_detects_circular_dependency(self):
        events = make_circular_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        assert len(report.cycles) >= 1
        cycle_tools = set()
        for cyc in report.cycles:
            cycle_tools.update(cyc.tools)
        assert "tool_a" in cycle_tools
        assert "tool_b" in cycle_tools
        assert "tool_c" in cycle_tools

    def test_cycle_triggers_p0_playbook(self):
        events = make_circular_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        p0_actions = [a for a in report.playbook if a.priority == ActionPriority.P0]
        assert len(p0_actions) >= 1
        assert any("CIRCULAR" in a.action_id for a in p0_actions)


class TestToolDependencyAdvisorFanOut:
    def test_detects_fan_out(self):
        events = make_fan_out_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        hub_node = None
        for node in report.tool_nodes:
            if node.name == "hub":
                hub_node = node
                break

        assert hub_node is not None
        assert hub_node.out_degree >= 4


class TestToolDependencyAdvisorSPOF:
    def test_detects_spof(self):
        events = make_spof_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)

        central_node = None
        for node in report.tool_nodes:
            if node.name == "central_db":
                central_node = node
                break

        assert central_node is not None
        assert central_node.in_degree >= 3
        assert DependencyIssueCode.OVER_DEPENDED in central_node.issues


class TestToolDependencyAdvisorAppetite:
    def test_cautious_higher_risk(self):
        events = make_circular_events()
        cautious = ToolDependencyAdvisor(risk_appetite="cautious", min_co_occurrence=0.5)
        aggressive = ToolDependencyAdvisor(risk_appetite="aggressive", min_co_occurrence=0.5)

        r_cautious = cautious.analyze(events)
        r_aggressive = aggressive.analyze(events)

        assert r_cautious.overall_risk >= r_aggressive.overall_risk

    def test_risk_appetite_parsing(self):
        assert RiskAppetite.parse("cautious") == RiskAppetite.CAUTIOUS
        assert RiskAppetite.parse("BALANCED") == RiskAppetite.BALANCED
        assert RiskAppetite.parse("unknown") == RiskAppetite.BALANCED
        assert RiskAppetite.parse(RiskAppetite.AGGRESSIVE) == RiskAppetite.AGGRESSIVE


class TestToolDependencyAdvisorRendering:
    def test_render_text(self):
        events = make_linear_chain_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        text = report.render_text()
        assert "VERDICT:" in text
        assert "Playbook" in text

    def test_render_markdown(self):
        events = make_circular_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        md = report.render_markdown()
        assert "## Tool Dependency Analysis Report" in md
        assert "| Metric | Value |" in md

    def test_render_json(self):
        events = make_linear_chain_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        json_str = report.render_json()
        import json
        data = json.loads(json_str)
        assert "grade" in data
        assert "overall_risk" in data
        assert "tool_nodes" in data
        assert "playbook" in data


class TestToolDependencyAdvisorGrade:
    def test_healthy_graph_gets_good_grade(self):
        # Two tools, one edge, no issues
        events = []
        for sid in ["s1", "s2", "s3"]:
            events.append(_tool_event(sid, "tool_x", 0))
            events.append(_tool_event(sid, "tool_y", 5))
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        assert report.grade in (DependencyGrade.A, DependencyGrade.B, DependencyGrade.C)

    def test_circular_gets_bad_grade(self):
        events = make_circular_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        assert report.grade in (DependencyGrade.D, DependencyGrade.F)


class TestToolDependencyAdvisorInsights:
    def test_generates_insights(self):
        events = make_linear_chain_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        assert len(report.insights) >= 1

    def test_cycle_insight(self):
        events = make_circular_events()
        advisor = ToolDependencyAdvisor(min_co_occurrence=0.5)
        report = advisor.analyze(events)
        assert any("circular" in i.lower() for i in report.insights)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])