"""Tests for NarrativeGenerator."""

import json
import pytest
from datetime import datetime, timezone, timedelta

from agentlens.models import AgentEvent, Session, ToolCall, DecisionTrace
from agentlens.narrative import (
    NarrativeGenerator, NarrativeConfig, NarrativeStyle,
    Narrative, NarrativeSection, ToolSummary,
)


def _ts(offset_s=0):
    return datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _make_session(events=None, status="completed", agent="test-agent"):
    s = Session(
        session_id="sess-001",
        agent_name=agent,
        started_at=_ts(0),
        ended_at=_ts(120),
        status=status,
    )
    for e in (events or []):
        s.add_event(e)
    return s


def _llm_event(offset=0, model="gpt-4o", tokens_in=100, tokens_out=50):
    return AgentEvent(
        event_type="llm_call", model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        timestamp=_ts(offset),
    )


def _tool_event(offset=0, name="search", duration_ms=50.0, error=False):
    tc = ToolCall(tool_name=name, duration_ms=duration_ms)
    out = {"error": "something failed"} if error else {"result": "ok"}
    return AgentEvent(
        event_type="tool_call", tool_call=tc,
        output_data=out, timestamp=_ts(offset),
    )


def _decision_event(offset=0, reasoning="chose path A", confidence=0.9, alts=None):
    dt = DecisionTrace(
        reasoning=reasoning, confidence=confidence,
        alternatives_considered=alts or ["path B"],
    )
    return AgentEvent(
        event_type="decision", decision_trace=dt,
        timestamp=_ts(offset),
    )


def _error_event(offset=0, msg="timeout"):
    return AgentEvent(
        event_type="error",
        output_data={"error": msg},
        timestamp=_ts(offset),
    )


# --- Tests ---

class TestNarrativeGenerator:
    def test_empty_session(self):
        gen = NarrativeGenerator()
        session = _make_session()
        n = gen.generate(session)
        assert n.session_id == "sess-001"
        assert n.total_events == 0
        assert n.total_tokens == 0
        assert n.error_count == 0

    def test_basic_generation(self):
        gen = NarrativeGenerator()
        session = _make_session([_llm_event(10), _tool_event(20)])
        n = gen.generate(session)
        assert n.total_events == 2
        assert n.total_tokens == 150  # 100+50
        assert len(n.tool_summaries) == 1
        assert n.tool_summaries[0].tool_name == "search"

    def test_summary_contains_session_id(self):
        gen = NarrativeGenerator()
        session = _make_session([_llm_event()])
        n = gen.generate(session)
        assert "sess-001" in n.summary

    def test_technical_style(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(style="technical")
        session = _make_session([_llm_event()])
        n = gen.generate(session, cfg)
        assert n.style == NarrativeStyle.TECHNICAL
        assert "session_id=" in n.summary

    def test_executive_style(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(style="executive")
        session = _make_session([_llm_event()])
        n = gen.generate(session, cfg)
        assert n.style == NarrativeStyle.EXECUTIVE
        assert "completed" in n.summary.lower()

    def test_casual_style(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(style="casual")
        session = _make_session([_llm_event()])
        n = gen.generate(session, cfg)
        assert n.style == NarrativeStyle.CASUAL
        assert "ran for" in n.summary.lower() or "session" in n.summary.lower()

    def test_tool_summaries(self):
        gen = NarrativeGenerator()
        events = [
            _tool_event(10, "search", 30),
            _tool_event(20, "search", 50),
            _tool_event(30, "calculator", 10),
            _tool_event(40, "search", 20, error=True),
        ]
        session = _make_session(events)
        n = gen.generate(session)
        search = next(t for t in n.tool_summaries if t.tool_name == "search")
        assert search.call_count == 3
        assert search.success_count == 2
        assert search.failure_count == 1
        assert search.avg_duration_ms == pytest.approx(100 / 3, abs=0.1)
        calc = next(t for t in n.tool_summaries if t.tool_name == "calculator")
        assert calc.call_count == 1

    def test_error_counting(self):
        gen = NarrativeGenerator()
        events = [_error_event(10, "err1"), _error_event(20, "err2")]
        session = _make_session(events)
        n = gen.generate(session)
        assert n.error_count == 2

    def test_decision_counting(self):
        gen = NarrativeGenerator()
        events = [_decision_event(10), _decision_event(20, reasoning="chose B")]
        session = _make_session(events)
        n = gen.generate(session)
        assert n.decision_count == 2

    def test_cost_estimation(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(cost_per_1k_input=0.01, cost_per_1k_output=0.03)
        events = [_llm_event(10, tokens_in=1000, tokens_out=1000)]
        session = _make_session(events)
        n = gen.generate(session, cfg)
        # 1000/1000*0.01 + 1000/1000*0.03 = 0.04
        assert n.total_cost_usd == pytest.approx(0.04, abs=0.001)

    def test_cost_disabled(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(include_costs=False)
        events = [_llm_event(10, tokens_in=1000, tokens_out=1000)]
        session = _make_session(events)
        n = gen.generate(session, cfg)
        assert n.total_cost_usd == 0.0

    def test_to_markdown(self):
        gen = NarrativeGenerator()
        events = [_llm_event(10), _tool_event(20)]
        session = _make_session(events)
        n = gen.generate(session)
        md = n.to_markdown()
        assert "# Session Narrative" in md
        assert "sess-001" in md
        assert "## Summary" in md

    def test_to_dict(self):
        gen = NarrativeGenerator()
        events = [_llm_event(10)]
        session = _make_session(events)
        n = gen.generate(session)
        d = n.to_dict()
        assert d["session_id"] == "sess-001"
        assert d["total_events"] == 1
        assert "summary" in d
        assert "body" in d
        json.dumps(d)  # Should be JSON-serializable

    def test_timeline_section(self):
        gen = NarrativeGenerator()
        events = [_llm_event(10), _tool_event(20), _error_event(30)]
        session = _make_session(events)
        n = gen.generate(session)
        timeline = next((s for s in n.sections if s.title == "Timeline"), None)
        assert timeline is not None
        assert "LLM call" in timeline.content
        assert "Tool: search" in timeline.content
        assert "Error" in timeline.content

    def test_decisions_section(self):
        gen = NarrativeGenerator()
        events = [_decision_event(10, "chose path A", 0.95, ["path B", "path C"])]
        session = _make_session(events)
        n = gen.generate(session)
        dec = next((s for s in n.sections if s.title == "Key Decisions"), None)
        assert dec is not None
        assert "chose path A" in dec.content
        assert "path B" in dec.content

    def test_errors_section(self):
        gen = NarrativeGenerator()
        events = [_error_event(10, "connection timeout")]
        session = _make_session(events)
        n = gen.generate(session)
        err = next((s for s in n.sections if s.title == "Errors & Issues"), None)
        assert err is not None
        assert "connection timeout" in err.content

    def test_models_section(self):
        gen = NarrativeGenerator()
        events = [_llm_event(10, "gpt-4o"), _llm_event(20, "claude-3")]
        session = _make_session(events)
        n = gen.generate(session)
        models = next((s for s in n.sections if s.title == "Models Used"), None)
        assert models is not None
        assert "gpt-4o" in models.content
        assert "claude-3" in models.content

    def test_max_steps(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(max_steps=3)
        events = [_llm_event(i * 10) for i in range(10)]
        session = _make_session(events)
        n = gen.generate(session, cfg)
        assert n.total_events == 3

    def test_generate_batch(self):
        gen = NarrativeGenerator()
        s1 = _make_session([_llm_event()], agent="agent-1")
        s2 = _make_session([_tool_event()], agent="agent-2")
        results = gen.generate_batch([s1, s2])
        assert len(results) == 2
        assert results[0].agent_name == "agent-1"
        assert results[1].agent_name == "agent-2"

    def test_compare(self):
        gen = NarrativeGenerator()
        s1 = _make_session([_llm_event(10), _tool_event(20, "search")])
        s2 = _make_session([_llm_event(10), _error_event(20)])
        s2.session_id = "sess-002"
        comparison = gen.compare(s1, s2)
        assert "Session Comparison" in comparison
        assert "sess-001" in comparison
        assert "sess-002" in comparison

    def test_duration_formatting(self):
        gen = NarrativeGenerator()
        # Short session
        s = _make_session([_llm_event()])
        s.started_at = _ts(0)
        s.ended_at = _ts(45)
        n = gen.generate(s)
        assert "45s" in n.summary or "0m 45s" in n.summary

    def test_long_duration(self):
        gen = NarrativeGenerator()
        s = _make_session([_llm_event()])
        s.started_at = _ts(0)
        s.ended_at = _ts(7200)
        n = gen.generate(s)
        assert "2h" in n.summary

    def test_active_session(self):
        gen = NarrativeGenerator()
        s = _make_session([_llm_event()], status="active")
        s.ended_at = None
        n = gen.generate(s)
        assert "active" in n.summary.lower()

    def test_body_mentions_tools(self):
        gen = NarrativeGenerator()
        events = [_tool_event(10, "search"), _tool_event(20, "calculator")]
        session = _make_session(events)
        n = gen.generate(session)
        assert "search" in n.body
        assert "calculator" in n.body

    def test_body_mentions_errors(self):
        gen = NarrativeGenerator()
        events = [_error_event(10)]
        session = _make_session(events)
        n = gen.generate(session)
        assert "error" in n.body.lower()

    def test_disable_all_sections(self):
        gen = NarrativeGenerator()
        cfg = NarrativeConfig(
            include_tools=False,
            include_decisions=False,
            include_costs=False,
            include_errors=False,
            include_timeline=False,
        )
        events = [_llm_event(10), _tool_event(20), _decision_event(30), _error_event(40)]
        session = _make_session(events)
        n = gen.generate(session, cfg)
        assert n.total_cost_usd == 0.0
        # Timeline and decisions sections should not be present
        section_titles = [s.title for s in n.sections]
        assert "Timeline" not in section_titles

    def test_markdown_tool_table(self):
        gen = NarrativeGenerator()
        events = [_tool_event(10, "search", 100), _tool_event(20, "search", 200)]
        session = _make_session(events)
        n = gen.generate(session)
        md = n.to_markdown()
        assert "| Tool | Calls |" in md
        assert "| search |" in md

    def test_narrative_style_enum(self):
        assert NarrativeStyle("technical") == NarrativeStyle.TECHNICAL
        assert NarrativeStyle("executive") == NarrativeStyle.EXECUTIVE
        assert NarrativeStyle("casual") == NarrativeStyle.CASUAL

    def test_config_style_string(self):
        cfg = NarrativeConfig(style="executive")
        assert cfg.style == NarrativeStyle.EXECUTIVE

    def test_to_dict_json_serializable(self):
        gen = NarrativeGenerator()
        events = [_llm_event(), _tool_event(10), _decision_event(20), _error_event(30)]
        session = _make_session(events)
        n = gen.generate(session)
        d = n.to_dict()
        serialized = json.dumps(d)
        assert len(serialized) > 0
        parsed = json.loads(serialized)
        assert parsed["session_id"] == "sess-001"
