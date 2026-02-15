"""Tests for agentlens.tracker — session management and event tracking."""

from unittest.mock import MagicMock, patch

import pytest

from agentlens.models import AgentEvent, Session
from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture
def mock_transport():
    """Transport with all network calls mocked."""
    t = MagicMock(spec=Transport)
    t.endpoint = "http://test:3000"
    t.api_key = "test-key"
    t._client = MagicMock()
    return t


@pytest.fixture
def tracker(mock_transport):
    return AgentTracker(transport=mock_transport)


class TestStartSession:
    def test_creates_session(self, tracker, mock_transport):
        session = tracker.start_session(agent_name="test-agent")

        assert session.agent_name == "test-agent"
        assert session.status == "active"
        assert session.session_id in tracker.sessions
        assert tracker._current_session_id == session.session_id
        mock_transport.send_events.assert_called_once()

    def test_sends_session_start_event(self, tracker, mock_transport):
        tracker.start_session(agent_name="my-agent", metadata={"version": "1.0"})

        call_args = mock_transport.send_events.call_args[0][0]
        assert len(call_args) == 1
        event = call_args[0]
        assert event["event_type"] == "session_start"
        assert event["agent_name"] == "my-agent"
        assert event["metadata"] == {"version": "1.0"}

    def test_multiple_sessions(self, tracker, mock_transport):
        s1 = tracker.start_session(agent_name="agent-1")
        s2 = tracker.start_session(agent_name="agent-2")

        assert len(tracker.sessions) == 2
        assert tracker._current_session_id == s2.session_id


class TestEndSession:
    def test_ends_current_session(self, tracker, mock_transport):
        session = tracker.start_session()
        tracker.end_session()

        assert session.status == "completed"
        assert session.ended_at is not None
        assert tracker._current_session_id is None
        mock_transport.flush.assert_called_once()

    def test_ends_specific_session(self, tracker, mock_transport):
        s1 = tracker.start_session(agent_name="a")
        s2 = tracker.start_session(agent_name="b")

        tracker.end_session(session_id=s1.session_id)

        assert s1.status == "completed"
        # Current session should still be s2 (we ended s1 explicitly)
        assert tracker._current_session_id == s2.session_id

    def test_end_nonexistent_session_noop(self, tracker, mock_transport):
        # Should not raise
        tracker.end_session(session_id="nonexistent")

    def test_end_with_no_session_noop(self, tracker, mock_transport):
        tracker.end_session()


class TestTrack:
    def test_track_generic_event(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track(
            event_type="generic",
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
        )

        assert event.event_type == "generic"
        assert event.input_data == {"prompt": "hello"}
        mock_transport.send_events.call_count == 2  # start + track

    def test_track_llm_call(self, tracker, mock_transport):
        session = tracker.start_session()
        event = tracker.track(
            event_type="llm_call",
            model="gpt-4",
            tokens_in=100,
            tokens_out=50,
        )

        assert event.event_type == "llm_call"
        assert event.model == "gpt-4"
        assert session.total_tokens_in == 100
        assert session.total_tokens_out == 50

    def test_track_with_tool_call(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track(
            event_type="tool_call",
            tool_name="web_search",
            tool_input={"query": "test"},
            tool_output={"results": []},
            duration_ms=150.0,
        )

        assert event.tool_call is not None
        assert event.tool_call.tool_name == "web_search"
        assert event.tool_call.tool_input == {"query": "test"}
        assert event.tool_call.duration_ms == 150.0

    def test_track_with_reasoning(self, tracker, mock_transport):
        session = tracker.start_session()
        event = tracker.track(
            event_type="agent_call",
            reasoning="Chose search because the question is factual",
        )

        assert event.decision_trace is not None
        assert event.decision_trace.reasoning == "Chose search because the question is factual"
        assert event.decision_trace.step == 1  # first event

    def test_track_without_session(self, mock_transport):
        tracker = AgentTracker(transport=mock_transport)
        # Should not raise — just tracks with empty session_id
        event = tracker.track(event_type="generic")
        assert event.session_id == ""

    def test_track_tool_convenience(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track_tool(
            tool_name="calculator",
            tool_input={"expr": "2+2"},
            tool_output={"result": 4},
            duration_ms=0.5,
        )

        assert event.event_type == "tool_call"
        assert event.tool_call.tool_name == "calculator"


class TestCurrentSession:
    def test_no_session(self, tracker):
        assert tracker.current_session is None

    def test_with_session(self, tracker, mock_transport):
        s = tracker.start_session()
        assert tracker.current_session is s

    def test_after_end(self, tracker, mock_transport):
        tracker.start_session()
        tracker.end_session()
        assert tracker.current_session is None


class TestExplain:
    def test_explain_no_session(self, tracker):
        result = tracker.explain()
        assert result == "No active session."

    def test_explain_nonexistent_session(self, tracker):
        result = tracker.explain(session_id="nope")
        assert "not found" in result

    def test_explain_with_events(self, tracker, mock_transport):
        tracker.start_session(agent_name="explainer")
        tracker.track(
            event_type="llm_call",
            model="gpt-4",
            tokens_in=10,
            tokens_out=5,
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
        )
        tracker.track(
            event_type="tool_call",
            tool_name="search",
            tool_input={"q": "test"},
        )

        explanation = tracker.explain()
        assert "explainer" in explanation
        assert "llm_call" in explanation
        assert "tool_call" in explanation
        assert "search" in explanation
