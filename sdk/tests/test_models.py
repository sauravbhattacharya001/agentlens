"""Tests for agentlens.models — Pydantic models and session logic."""

import uuid
from datetime import datetime, timezone

from agentlens.models import (
    AgentEvent,
    DecisionTrace,
    Session,
    ToolCall,
    _new_id,
    _utcnow,
)


# ── Utility helpers ──────────────────────────────────────────────────


class TestHelpers:
    def test_utcnow_is_utc(self):
        now = _utcnow()
        assert now.tzinfo == timezone.utc

    def test_new_id_is_hex(self):
        id_ = _new_id()
        assert len(id_) == 16
        int(id_, 16)  # should not raise


# ── ToolCall ─────────────────────────────────────────────────────────


class TestToolCall:
    def test_defaults(self):
        tc = ToolCall(tool_name="search")
        assert tc.tool_name == "search"
        assert tc.tool_input == {}
        assert tc.tool_output is None
        assert tc.duration_ms is None
        assert len(tc.tool_call_id) == 16

    def test_full(self):
        tc = ToolCall(
            tool_name="fetch",
            tool_input={"url": "https://example.com"},
            tool_output={"status": 200},
            duration_ms=42.5,
        )
        assert tc.tool_output["status"] == 200
        assert tc.duration_ms == 42.5


# ── DecisionTrace ───────────────────────────────────────────────────


class TestDecisionTrace:
    def test_defaults(self):
        dt = DecisionTrace()
        assert dt.step == 0
        assert dt.reasoning == ""
        assert dt.alternatives_considered == []
        assert dt.confidence is None

    def test_with_reasoning(self):
        dt = DecisionTrace(
            reasoning="Chose tool A because it was fastest",
            step=3,
            alternatives_considered=["tool B", "tool C"],
            confidence=0.92,
        )
        assert dt.step == 3
        assert dt.confidence == 0.92
        assert len(dt.alternatives_considered) == 2


# ── AgentEvent ───────────────────────────────────────────────────────


class TestAgentEvent:
    def test_defaults(self):
        ev = AgentEvent()
        assert ev.event_type == "generic"
        assert ev.session_id == ""
        assert ev.tokens_in == 0
        assert ev.tokens_out == 0
        assert ev.model is None
        assert ev.tool_call is None
        assert ev.decision_trace is None

    def test_to_api_dict(self):
        ev = AgentEvent(
            event_type="llm_call",
            session_id="abc123",
            model="gpt-4",
            tokens_in=100,
            tokens_out=50,
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
        )
        d = ev.to_api_dict()
        assert d["event_type"] == "llm_call"
        assert d["model"] == "gpt-4"
        assert d["tokens_in"] == 100
        assert "event_id" in d
        # None fields should be excluded
        assert "tool_call" not in d
        assert "decision_trace" not in d

    def test_to_api_dict_with_tool_call(self):
        tc = ToolCall(tool_name="search", tool_input={"q": "test"})
        ev = AgentEvent(event_type="tool_call", tool_call=tc)
        d = ev.to_api_dict()
        assert d["tool_call"]["tool_name"] == "search"


# ── Session ──────────────────────────────────────────────────────────


class TestSession:
    def test_creation(self):
        s = Session(agent_name="test-agent")
        assert s.agent_name == "test-agent"
        assert s.status == "active"
        assert s.ended_at is None
        assert s.total_tokens_in == 0
        assert s.total_tokens_out == 0
        assert s.events == []

    def test_add_event(self):
        s = Session()
        ev = AgentEvent(tokens_in=10, tokens_out=20)
        s.add_event(ev)

        assert len(s.events) == 1
        assert s.total_tokens_in == 10
        assert s.total_tokens_out == 20
        assert ev.session_id == s.session_id

    def test_add_multiple_events(self):
        s = Session()
        for i in range(5):
            s.add_event(AgentEvent(tokens_in=10, tokens_out=5))

        assert len(s.events) == 5
        assert s.total_tokens_in == 50
        assert s.total_tokens_out == 25

    def test_end_session(self):
        s = Session()
        assert s.status == "active"
        assert s.ended_at is None

        s.end()
        assert s.status == "completed"
        assert s.ended_at is not None
        assert s.ended_at.tzinfo == timezone.utc

    def test_to_api_dict(self):
        s = Session(agent_name="my-agent", metadata={"env": "test"})
        d = s.to_api_dict()

        assert d["agent_name"] == "my-agent"
        assert d["metadata"] == {"env": "test"}
        assert d["status"] == "active"
        assert d["ended_at"] is None
        assert "events" not in d  # API dict excludes events

    def test_to_api_dict_ended(self):
        s = Session()
        s.end()
        d = s.to_api_dict()
        assert d["ended_at"] is not None
        assert d["status"] == "completed"
