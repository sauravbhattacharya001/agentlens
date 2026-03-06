"""Tests for agentlens top-level init/start/end/track API."""

import re
from unittest.mock import MagicMock, patch

import pytest

import agentlens
from agentlens.models import AgentEvent, Session
from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture(autouse=True)
def reset_global_tracker():
    """Reset the global tracker before each test."""
    agentlens._tracker = None
    yield
    if agentlens._tracker is not None:
        try:
            agentlens._tracker.transport.close()
        except Exception:
            pass
    agentlens._tracker = None


class TestInit:
    def test_creates_tracker(self):
        with patch.object(Transport, "_flush_loop"):
            tracker = agentlens.init(api_key="test", endpoint="http://test:3000")
            assert isinstance(tracker, AgentTracker)
            assert agentlens._tracker is tracker
            tracker.transport.close()

    def test_reinit_closes_previous(self):
        with patch.object(Transport, "_flush_loop"):
            t1 = agentlens.init(api_key="k1", endpoint="http://test:3000")
            transport1 = t1.transport

            with patch.object(transport1, "close") as mock_close:
                agentlens.init(api_key="k2", endpoint="http://test:3000")
                mock_close.assert_called_once()

            agentlens._tracker.transport.close()


class TestNotInitialized:
    def test_start_session_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.start_session()

    def test_end_session_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.end_session()

    def test_track_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.track()

    def test_explain_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.explain()

    def test_export_session_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.export_session()

    def test_compare_sessions_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.compare_sessions("a", "b")


class TestIntegration:
    """End-to-end tests with mocked HTTP layer."""

    def test_full_lifecycle(self):
        mock_transport = MagicMock(spec=Transport)
        mock_transport.endpoint = "http://test:3000"
        mock_transport.api_key = "test"
        mock_transport._client = MagicMock()

        tracker = AgentTracker(transport=mock_transport)
        agentlens._tracker = tracker

        session = agentlens.start_session(agent_name="lifecycle-test")
        assert session.status == "active"

        event = agentlens.track(
            event_type="llm_call",
            model="gpt-4",
            tokens_in=100,
            tokens_out=50,
        )
        assert event.tokens_in == 100

        explanation = agentlens.explain()
        assert "lifecycle-test" in explanation

        agentlens.end_session()
        assert session.status == "completed"


# ---------------------------------------------------------------------------
# Helper fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized():
    """Set up an initialized AgentLens with mock transport."""
    mock_transport = MagicMock(spec=Transport)
    mock_transport.endpoint = "http://test:3000"
    mock_transport.api_key = "test"
    mock_transport._client = MagicMock()
    tracker = AgentTracker(transport=mock_transport)
    agentlens._tracker = tracker
    return tracker


# ---------------------------------------------------------------------------
# TestInitDefaults
# ---------------------------------------------------------------------------

class TestInitDefaults:
    def test_init_with_defaults(self):
        """init() without args uses api_key='default', endpoint='localhost:3000'."""
        with patch.object(Transport, "_flush_loop"):
            tracker = agentlens.init()
            assert tracker.transport.api_key == "default"
            assert "localhost:3000" in tracker.transport.endpoint
            tracker.transport.close()

    def test_init_returns_same_tracker_on_double_init(self):
        """Calling init twice replaces the tracker (returns a new one)."""
        with patch.object(Transport, "_flush_loop"):
            t1 = agentlens.init(api_key="k1", endpoint="http://test:3000")
            t2 = agentlens.init(api_key="k2", endpoint="http://test:3000")
            assert t1 is not t2
            assert agentlens._tracker is t2
            t2.transport.close()

    def test_init_reinit_doesnt_error_if_close_fails(self):
        """If previous transport.close() raises, init still succeeds."""
        with patch.object(Transport, "_flush_loop"):
            t1 = agentlens.init(api_key="k1", endpoint="http://test:3000")
            t1.transport.close = MagicMock(side_effect=RuntimeError("boom"))
            # Should not raise
            t2 = agentlens.init(api_key="k2", endpoint="http://test:3000")
            assert agentlens._tracker is t2
            t2.transport.close()


# ---------------------------------------------------------------------------
# TestStartSession
# ---------------------------------------------------------------------------

class TestStartSession:
    def test_start_session_returns_session(self, initialized):
        session = agentlens.start_session()
        assert isinstance(session, Session)
        assert session.status == "active"

    def test_start_session_with_metadata(self, initialized):
        meta = {"env": "test", "version": "1.0"}
        session = agentlens.start_session(metadata=meta)
        assert session.metadata == meta

    def test_start_session_custom_agent_name(self, initialized):
        session = agentlens.start_session(agent_name="my-agent")
        assert session.agent_name == "my-agent"

    def test_start_session_default_agent_name(self, initialized):
        session = agentlens.start_session()
        assert session.agent_name == "default-agent"

    def test_start_session_creates_session_id(self, initialized):
        session = agentlens.start_session()
        assert session.session_id
        assert len(session.session_id) > 0


# ---------------------------------------------------------------------------
# TestEndSession
# ---------------------------------------------------------------------------

class TestEndSession:
    def test_end_session_current(self, initialized):
        session = agentlens.start_session()
        assert session.status == "active"
        agentlens.end_session()
        assert session.status == "completed"

    def test_end_session_specific_id(self, initialized):
        s1 = agentlens.start_session(agent_name="first")
        s2 = agentlens.start_session(agent_name="second")
        agentlens.end_session(session_id=s1.session_id)
        assert s1.status == "completed"
        assert s2.status == "active"

    def test_end_session_flushes_events(self, initialized):
        agentlens.start_session()
        agentlens.end_session()
        initialized.transport.flush.assert_called()


# ---------------------------------------------------------------------------
# TestTrack
# ---------------------------------------------------------------------------

class TestTrack:
    def test_track_returns_event(self, initialized):
        agentlens.start_session()
        event = agentlens.track()
        assert isinstance(event, AgentEvent)

    def test_track_with_all_params(self, initialized):
        agentlens.start_session()
        event = agentlens.track(
            event_type="llm_call",
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
            model="gpt-4",
            tokens_in=100,
            tokens_out=50,
            reasoning="chose gpt-4 for quality",
            tool_name="search",
            tool_input={"q": "test"},
            tool_output={"results": []},
            duration_ms=250.0,
        )
        assert event.event_type == "llm_call"
        assert event.input_data == {"prompt": "hello"}
        assert event.output_data == {"response": "hi"}
        assert event.model == "gpt-4"
        assert event.tokens_in == 100
        assert event.tokens_out == 50
        assert event.tool_call is not None
        assert event.tool_call.tool_name == "search"
        assert event.decision_trace is not None
        assert event.decision_trace.reasoning == "chose gpt-4 for quality"
        assert event.duration_ms == 250.0

    def test_track_default_params(self, initialized):
        agentlens.start_session()
        event = agentlens.track()
        assert event.event_type == "generic"
        assert event.tokens_in == 0
        assert event.tokens_out == 0
        assert event.model is None
        assert event.tool_call is None
        assert event.decision_trace is None

    def test_track_with_model(self, initialized):
        agentlens.start_session()
        event = agentlens.track(model="claude-3-opus")
        assert event.model == "claude-3-opus"

    def test_track_with_tool_info(self, initialized):
        agentlens.start_session()
        event = agentlens.track(
            tool_name="calculator",
            tool_input={"expression": "2+2"},
            tool_output={"result": 4},
        )
        assert event.tool_call is not None
        assert event.tool_call.tool_name == "calculator"
        assert event.tool_call.tool_input == {"expression": "2+2"}
        assert event.tool_call.tool_output == {"result": 4}

    def test_track_with_reasoning(self, initialized):
        agentlens.start_session()
        event = agentlens.track(reasoning="used chain-of-thought")
        assert event.decision_trace is not None
        assert event.decision_trace.reasoning == "used chain-of-thought"


# ---------------------------------------------------------------------------
# TestExplain
# ---------------------------------------------------------------------------

class TestExplain:
    def test_explain_returns_string(self, initialized):
        agentlens.start_session(agent_name="test-bot")
        agentlens.track(event_type="llm_call", model="gpt-4", tokens_in=10)
        result = agentlens.explain()
        assert isinstance(result, str)
        assert len(result) > 0
        assert "test-bot" in result

    def test_explain_with_session_id(self, initialized):
        s1 = agentlens.start_session(agent_name="first-agent")
        agentlens.track(event_type="llm_call")
        s2 = agentlens.start_session(agent_name="second-agent")
        result = agentlens.explain(session_id=s1.session_id)
        assert "first-agent" in result


# ---------------------------------------------------------------------------
# TestExportSession
# ---------------------------------------------------------------------------

class TestExportSession:
    def test_export_session_json_format(self, initialized):
        """Default format='json' calls tracker.export_session."""
        agentlens.start_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "test", "events": []}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.get.return_value = mock_resp
        result = agentlens.export_session()
        assert result == {"session_id": "test", "events": []}

    def test_export_session_csv_format(self, initialized):
        agentlens.start_session()
        mock_resp = MagicMock()
        mock_resp.text = "session_id,event_type\ntest,llm_call"
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.get.return_value = mock_resp
        result = agentlens.export_session(format="csv")
        assert "session_id" in result

    def test_export_session_with_session_id(self, initialized):
        s = agentlens.start_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": s.session_id}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.get.return_value = mock_resp
        result = agentlens.export_session(session_id=s.session_id)
        assert result["session_id"] == s.session_id

    def test_export_session_defaults_to_current(self, initialized):
        """No session_id uses current session's id in the API call."""
        s = agentlens.start_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.get.return_value = mock_resp
        agentlens.export_session()
        # Verify the URL contained the current session id
        call_args = initialized.transport.get.call_args
        assert s.session_id in call_args[0][0]


# ---------------------------------------------------------------------------
# TestCompareSessionsAPI
# ---------------------------------------------------------------------------

class TestCompareSessionsAPI:
    def test_compare_sessions_calls_tracker(self, initialized):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deltas": {}}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.post.return_value = mock_resp
        result = agentlens.compare_sessions("sess-a", "sess-b")
        assert "deltas" in result

    def test_compare_sessions_returns_dict(self, initialized):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_a": {}, "session_b": {}, "deltas": {}}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.post.return_value = mock_resp
        result = agentlens.compare_sessions("a", "b")
        assert isinstance(result, dict)

    def test_compare_sessions_not_init_raises(self):
        with pytest.raises(RuntimeError, match="init"):
            agentlens.compare_sessions("a", "b")


# ---------------------------------------------------------------------------
# TestGetSetCostsAPI
# ---------------------------------------------------------------------------

class TestGetSetCostsAPI:
    def test_get_costs_delegates_to_tracker(self, initialized):
        agentlens.start_session()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total_cost": 0.05, "currency": "USD"}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.get.return_value = mock_resp
        result = agentlens.get_costs()
        assert result["total_cost"] == 0.05

    def test_set_pricing_delegates_to_tracker(self, initialized):
        pricing = {"gpt-4": {"input_cost_per_1m": 30.0, "output_cost_per_1m": 60.0}}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "updated": 1}
        mock_resp.raise_for_status = MagicMock()
        initialized.transport.put.return_value = mock_resp
        result = agentlens.set_pricing(pricing)
        assert result["updated"] == 1


# ---------------------------------------------------------------------------
# TestModuleExports
# ---------------------------------------------------------------------------

class TestModuleExports:
    def test_version_string(self):
        assert isinstance(agentlens.__version__, str)
        assert re.match(r"^\d+\.\d+\.\d+", agentlens.__version__)

    def test_all_exports(self):
        expected = {
            "init", "start_session", "end_session", "track",
            "explain", "export_session", "compare_sessions",
            "get_costs", "get_pricing", "set_pricing",
        }
        assert expected.issubset(set(agentlens.__all__))
