"""Tests for event search & filter — tracker.search_events()."""

from unittest.mock import MagicMock

import pytest

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


def make_search_response(**overrides):
    """Build a realistic search response dict."""
    defaults = {
        "session_id": "test-session-123",
        "total_events": 20,
        "matched": 5,
        "returned": 5,
        "offset": 0,
        "limit": 100,
        "summary": {
            "tokens_in": 1500,
            "tokens_out": 800,
            "total_tokens": 2300,
            "total_duration_ms": 450.5,
            "event_types": {"llm_call": 3, "tool_call": 2},
            "models": {"gpt-4": 2, "claude-3-sonnet": 1},
        },
        "events": [
            {
                "event_id": "evt-001",
                "event_type": "llm_call",
                "timestamp": "2024-06-15T10:01:00Z",
                "model": "gpt-4",
                "tokens_in": 500,
                "tokens_out": 200,
            },
        ],
    }
    defaults.update(overrides)
    return defaults


class TestSearchEvents:
    """Tests for AgentTracker.search_events()."""

    def test_search_with_current_session(self, tracker, mock_transport):
        """search_events uses current session when no session_id given."""
        session = tracker.start_session(agent_name="search-agent")

        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response(
            session_id=session.session_id
        )
        mock_transport._client.get.return_value = mock_response

        result = tracker.search_events()

        mock_transport._client.get.assert_called_once()
        call_args = mock_transport._client.get.call_args
        assert session.session_id in call_args[0][0]
        assert result["matched"] == 5

    def test_search_with_explicit_session(self, tracker, mock_transport):
        """search_events uses explicit session_id when provided."""
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response(
            session_id="explicit-session"
        )
        mock_transport._client.get.return_value = mock_response

        result = tracker.search_events(session_id="explicit-session")

        call_args = mock_transport._client.get.call_args
        assert "explicit-session" in call_args[0][0]

    def test_search_no_session_raises(self, tracker):
        """search_events raises RuntimeError when no session is available."""
        with pytest.raises(RuntimeError, match="No session to search"):
            tracker.search_events()

    def test_search_with_query(self, tracker, mock_transport):
        """search_events passes q parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(q="customer data")

        call_args = mock_transport._client.get.call_args
        params = call_args[1]["params"]
        assert params["q"] == "customer data"

    def test_search_with_event_type(self, tracker, mock_transport):
        """search_events passes type parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(event_type="llm_call,tool_call")

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["type"] == "llm_call,tool_call"

    def test_search_with_model(self, tracker, mock_transport):
        """search_events passes model parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(model="gpt-4")

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["model"] == "gpt-4"

    def test_search_with_min_tokens(self, tracker, mock_transport):
        """search_events passes min_tokens parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(min_tokens=500)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["min_tokens"] == 500

    def test_search_with_max_tokens(self, tracker, mock_transport):
        """search_events passes max_tokens parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(max_tokens=2000)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["max_tokens"] == 2000

    def test_search_with_min_duration(self, tracker, mock_transport):
        """search_events passes min_duration_ms parameter."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(min_duration_ms=100.5)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["min_duration_ms"] == 100.5

    def test_search_with_has_tools(self, tracker, mock_transport):
        """search_events passes has_tools boolean."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(has_tools=True)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["has_tools"] == "true"

    def test_search_with_has_reasoning(self, tracker, mock_transport):
        """search_events passes has_reasoning boolean."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(has_reasoning=True)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["has_reasoning"] == "true"

    def test_search_with_errors(self, tracker, mock_transport):
        """search_events passes errors boolean."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(errors=True)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["errors"] == "true"

    def test_search_with_time_range(self, tracker, mock_transport):
        """search_events passes after/before parameters."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(
            after="2024-06-15T10:00:00Z",
            before="2024-06-15T10:30:00Z",
        )

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["after"] == "2024-06-15T10:00:00Z"
        assert params["before"] == "2024-06-15T10:30:00Z"

    def test_search_with_pagination(self, tracker, mock_transport):
        """search_events passes limit/offset parameters."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(limit=25, offset=50)

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["limit"] == 25
        assert params["offset"] == 50

    def test_search_limit_clamped(self, tracker, mock_transport):
        """search_events clamps limit to 1-500 range."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(limit=9999)
        params = mock_transport._client.get.call_args[1]["params"]
        assert params["limit"] == 500

        tracker.search_events(limit=0)
        params = mock_transport._client.get.call_args[1]["params"]
        assert params["limit"] == 1

    def test_search_offset_clamped(self, tracker, mock_transport):
        """search_events clamps negative offset to 0."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(offset=-10)
        params = mock_transport._client.get.call_args[1]["params"]
        assert params["offset"] == 0

    def test_search_omits_unset_params(self, tracker, mock_transport):
        """search_events only sends params that are set."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events()

        params = mock_transport._client.get.call_args[1]["params"]
        # Only limit and offset should be set (defaults)
        assert "q" not in params
        assert "type" not in params
        assert "model" not in params
        assert "min_tokens" not in params
        assert "max_tokens" not in params
        assert "has_tools" not in params
        assert "has_reasoning" not in params
        assert "errors" not in params
        assert "after" not in params
        assert "before" not in params
        assert "limit" in params
        assert "offset" in params

    def test_search_zero_min_tokens_not_sent(self, tracker, mock_transport):
        """min_tokens=0 is not sent (treated as no filter)."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(min_tokens=0)

        params = mock_transport._client.get.call_args[1]["params"]
        assert "min_tokens" not in params

    def test_search_false_booleans_not_sent(self, tracker, mock_transport):
        """has_tools=False, etc. are not sent as params."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(has_tools=False, has_reasoning=False, errors=False)

        params = mock_transport._client.get.call_args[1]["params"]
        assert "has_tools" not in params
        assert "has_reasoning" not in params
        assert "errors" not in params

    def test_search_combined_filters(self, tracker, mock_transport):
        """search_events sends all filters when combined."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events(
            q="customer",
            event_type="llm_call",
            model="gpt-4",
            min_tokens=100,
            min_duration_ms=50,
            has_tools=True,
            limit=10,
            offset=5,
        )

        params = mock_transport._client.get.call_args[1]["params"]
        assert params["q"] == "customer"
        assert params["type"] == "llm_call"
        assert params["model"] == "gpt-4"
        assert params["min_tokens"] == 100
        assert params["min_duration_ms"] == 50
        assert params["has_tools"] == "true"
        assert params["limit"] == 10
        assert params["offset"] == 5

    def test_search_api_key_sent(self, tracker, mock_transport):
        """search_events sends API key in headers."""
        session = tracker.start_session(agent_name="test")
        mock_response = MagicMock()
        mock_response.json.return_value = make_search_response()
        mock_transport._client.get.return_value = mock_response

        tracker.search_events()

        headers = mock_transport._client.get.call_args[1]["headers"]
        assert headers["X-API-Key"] == "test-key"

    def test_search_returns_full_response(self, tracker, mock_transport):
        """search_events returns the complete response structure."""
        session = tracker.start_session(agent_name="test")
        expected = make_search_response(matched=3, total_events=10)
        mock_response = MagicMock()
        mock_response.json.return_value = expected
        mock_transport._client.get.return_value = mock_response

        result = tracker.search_events()

        assert result["session_id"] == "test-session-123"
        assert result["total_events"] == 10
        assert result["matched"] == 3
        assert result["summary"]["total_tokens"] == 2300
        assert len(result["events"]) == 1
