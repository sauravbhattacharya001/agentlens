"""Tests for TagMixin (tracker_tags.py) — session tagging and search."""

import pytest
from unittest.mock import MagicMock

from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture
def tracker():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    t = AgentTracker(transport=transport)
    t._current_session_id = "sess-001"
    return t


@pytest.fixture
def tracker_no_session():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    return AgentTracker(transport=transport)


# ── add_tags ─────────────────────────────────────────────


class TestAddTags:
    def test_add_tags_default_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "sess-001",
            "added": 2,
            "tags": ["production", "v2.1"],
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.add_tags(["production", "v2.1"])
        assert result["added"] == 2
        assert "production" in result["tags"]
        call_args = tracker.transport.post.call_args
        assert "/sessions/sess-001/tags" in call_args[0][0]
        assert call_args[1]["json"]["tags"] == ["production", "v2.1"]

    def test_add_tags_explicit_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "sess-099", "added": 1, "tags": ["debug"]}
        tracker.transport.post.return_value = mock_resp

        result = tracker.add_tags(["debug"], session_id="sess-099")
        assert result["session_id"] == "sess-099"
        call_args = tracker.transport.post.call_args
        assert "/sessions/sess-099/tags" in call_args[0][0]

    def test_add_tags_empty_list_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty list"):
            tracker.add_tags([])

    def test_add_tags_non_list_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty list"):
            tracker.add_tags("single-string")

    def test_add_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(Exception):
            tracker_no_session.add_tags(["tag1"])


# ── remove_tags ──────────────────────────────────────────


class TestRemoveTags:
    def test_remove_specific_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "sess-001",
            "removed": 1,
            "tags": ["production"],
        }
        tracker.transport.delete.return_value = mock_resp

        result = tracker.remove_tags(["debug"])
        assert result["removed"] == 1
        call_args = tracker.transport.delete.call_args
        assert "/sessions/sess-001/tags" in call_args[0][0]
        assert call_args[1]["json"]["tags"] == ["debug"]

    def test_remove_all_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "sess-001", "removed": 3, "tags": []}
        tracker.transport.delete.return_value = mock_resp

        result = tracker.remove_tags()
        assert result["tags"] == []
        call_args = tracker.transport.delete.call_args
        # When no tags specified, body should be empty dict
        assert call_args[1]["json"] == {}

    def test_remove_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(Exception):
            tracker_no_session.remove_tags(["tag1"])


# ── get_tags ─────────────────────────────────────────────


class TestGetTags:
    def test_get_tags_default_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tags": ["production", "v2.1", "regression"]}
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_tags()
        assert result == ["production", "v2.1", "regression"]
        call_args = tracker.transport.get.call_args
        assert "/sessions/sess-001/tags" in call_args[0][0]

    def test_get_tags_explicit_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tags": []}
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_tags(session_id="sess-empty")
        assert result == []
        call_args = tracker.transport.get.call_args
        assert "/sessions/sess-empty/tags" in call_args[0][0]

    def test_get_tags_missing_key_returns_empty(self, tracker):
        """If server omits 'tags' key, should return empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_tags()
        assert result == []

    def test_get_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(Exception):
            tracker_no_session.get_tags()


# ── list_all_tags ────────────────────────────────────────


class TestListAllTags:
    def test_list_all_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tags": [
                {"tag": "production", "session_count": 42},
                {"tag": "debug", "session_count": 7},
            ]
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_all_tags()
        assert len(result) == 2
        assert result[0]["tag"] == "production"
        assert result[0]["session_count"] == 42
        call_args = tracker.transport.get.call_args
        assert "/sessions/tags" in call_args[0][0]

    def test_list_all_tags_empty(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tags": []}
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_all_tags()
        assert result == []


# ── list_sessions_by_tag ─────────────────────────────────


class TestListSessionsByTag:
    def test_list_sessions_by_tag(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": [{"session_id": "s1", "tags": ["production"]}],
            "total": 1,
            "limit": 50,
            "offset": 0,
            "tag": "production",
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_sessions_by_tag("production")
        assert result["total"] == 1
        assert result["sessions"][0]["tags"] == ["production"]
        call_args = tracker.transport.get.call_args
        assert "/sessions/by-tag/production" in call_args[0][0]
        assert call_args[1]["params"]["limit"] == 50
        assert call_args[1]["params"]["offset"] == 0

    def test_list_sessions_by_tag_with_pagination(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 100, "limit": 10, "offset": 20}
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_sessions_by_tag("debug", limit=10, offset=20)
        call_args = tracker.transport.get.call_args
        assert call_args[1]["params"]["limit"] == 10
        assert call_args[1]["params"]["offset"] == 20

    def test_list_sessions_by_tag_empty_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty string"):
            tracker.list_sessions_by_tag("")

    def test_list_sessions_by_tag_non_string_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty string"):
            tracker.list_sessions_by_tag(123)


# ── search_sessions ──────────────────────────────────────


class TestSearchSessions:
    def test_search_basic(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": [{"session_id": "s1", "agent_name": "planner"}],
            "total": 1,
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.search_sessions(q="planner")
        assert result["total"] == 1
        call_args = tracker.transport.get.call_args
        assert "/sessions/search" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["q"] == "planner"
        assert params["sort"] == "started_at"
        assert params["order"] == "desc"

    def test_search_with_all_filters(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions(
            q="test",
            agent="planner",
            status="completed",
            after="2025-01-01T00:00:00Z",
            before="2025-12-31T23:59:59Z",
            min_tokens=100,
            max_tokens=50000,
            tags=["production", "v2"],
            sort="total_tokens",
            order="asc",
            limit=25,
            offset=10,
        )
        params = tracker.transport.get.call_args[1]["params"]
        assert params["q"] == "test"
        assert params["agent"] == "planner"
        assert params["status"] == "completed"
        assert params["after"] == "2025-01-01T00:00:00Z"
        assert params["before"] == "2025-12-31T23:59:59Z"
        assert params["min_tokens"] == 100
        assert params["max_tokens"] == 50000
        assert params["tags"] == "production,v2"
        assert params["sort"] == "total_tokens"
        assert params["order"] == "asc"
        assert params["limit"] == 25
        assert params["offset"] == 10

    def test_search_limit_clamped_to_200(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions(limit=999)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 200

    def test_search_limit_clamped_to_1(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions(limit=0)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 1

    def test_search_offset_clamped_to_0(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions(offset=-5)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["offset"] == 0

    def test_search_zero_tokens_excluded(self, tracker):
        """min_tokens=0 and max_tokens=0 should not be sent as params."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions(min_tokens=0, max_tokens=0)
        params = tracker.transport.get.call_args[1]["params"]
        assert "min_tokens" not in params
        assert "max_tokens" not in params

    def test_search_no_filters(self, tracker):
        """Calling with no arguments should still work with defaults."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"sessions": [], "total": 0}
        tracker.transport.get.return_value = mock_resp

        tracker.search_sessions()
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 50
        assert params["offset"] == 0
        assert "q" not in params
        assert "agent" not in params
        assert "tags" not in params
