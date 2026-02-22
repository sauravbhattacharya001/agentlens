"""Tests for session tags SDK methods."""

import pytest
from unittest.mock import MagicMock, patch

from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture
def tracker():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    t = AgentTracker(transport=transport)
    t._current_session_id = "test-session-123"
    return t


@pytest.fixture
def tracker_no_session():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    return AgentTracker(transport=transport)


class TestAddTags:
    def test_add_tags_to_current_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "test-session-123",
            "added": 2,
            "tags": ["production", "v2.1"],
        }
        tracker.transport._client.post.return_value = mock_resp

        result = tracker.add_tags(["production", "v2.1"])
        assert result["added"] == 2
        assert result["tags"] == ["production", "v2.1"]
        tracker.transport._client.post.assert_called_once()
        call_args = tracker.transport._client.post.call_args
        assert "/sessions/test-session-123/tags" in call_args[0][0]
        assert call_args[1]["json"] == {"tags": ["production", "v2.1"]}

    def test_add_tags_to_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "other", "added": 1, "tags": ["test"]}
        tracker.transport._client.post.return_value = mock_resp

        result = tracker.add_tags(["test"], session_id="other")
        call_args = tracker.transport._client.post.call_args
        assert "/sessions/other/tags" in call_args[0][0]

    def test_add_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session to tag"):
            tracker_no_session.add_tags(["test"])

    def test_add_tags_empty_list_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty list"):
            tracker.add_tags([])

    def test_add_tags_not_list_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty list"):
            tracker.add_tags("not-a-list")

    def test_add_tags_none_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty list"):
            tracker.add_tags(None)


class TestRemoveTags:
    def test_remove_specific_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "test-session-123",
            "removed": 1,
            "tags": ["remaining"],
        }
        tracker.transport._client.request.return_value = mock_resp

        result = tracker.remove_tags(["old-tag"])
        assert result["removed"] == 1
        call_args = tracker.transport._client.request.call_args
        assert call_args[0][0] == "DELETE"
        assert "/sessions/test-session-123/tags" in call_args[0][1]
        assert call_args[1]["json"] == {"tags": ["old-tag"]}

    def test_remove_all_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "test-session-123",
            "removed": 3,
            "tags": [],
        }
        tracker.transport._client.request.return_value = mock_resp

        result = tracker.remove_tags()
        assert result["removed"] == 3
        assert result["tags"] == []
        call_args = tracker.transport._client.request.call_args
        assert call_args[1]["json"] == {}

    def test_remove_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session to untag"):
            tracker_no_session.remove_tags(["test"])

    def test_remove_tags_from_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "other", "removed": 1, "tags": []}
        tracker.transport._client.request.return_value = mock_resp

        tracker.remove_tags(["tag"], session_id="other")
        call_args = tracker.transport._client.request.call_args
        assert "/sessions/other/tags" in call_args[0][1]


class TestGetTags:
    def test_get_tags_current_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "test-session-123",
            "tags": ["alpha", "beta"],
        }
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.get_tags()
        assert result == ["alpha", "beta"]
        call_args = tracker.transport._client.get.call_args
        assert "/sessions/test-session-123/tags" in call_args[0][0]

    def test_get_tags_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "other", "tags": ["x"]}
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.get_tags(session_id="other")
        assert result == ["x"]

    def test_get_tags_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session to query"):
            tracker_no_session.get_tags()

    def test_get_tags_empty(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "test-session-123", "tags": []}
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.get_tags()
        assert result == []


class TestListAllTags:
    def test_list_all_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tags": [
                {"tag": "production", "session_count": 42},
                {"tag": "staging", "session_count": 10},
            ]
        }
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.list_all_tags()
        assert len(result) == 2
        assert result[0]["tag"] == "production"
        assert result[0]["session_count"] == 42

    def test_list_all_tags_empty(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tags": []}
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.list_all_tags()
        assert result == []


class TestListSessionsByTag:
    def test_list_sessions_by_tag(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": [{"session_id": "s1", "tags": ["prod"]}],
            "total": 1,
            "limit": 50,
            "offset": 0,
            "tag": "prod",
        }
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.list_sessions_by_tag("prod")
        assert result["total"] == 1
        assert result["tag"] == "prod"
        call_args = tracker.transport._client.get.call_args
        assert "/sessions/by-tag/prod" in call_args[0][0]

    def test_list_sessions_by_tag_with_pagination(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": [],
            "total": 0,
            "limit": 10,
            "offset": 20,
            "tag": "test",
        }
        tracker.transport._client.get.return_value = mock_resp

        tracker.list_sessions_by_tag("test", limit=10, offset=20)
        call_args = tracker.transport._client.get.call_args
        assert call_args[1]["params"]["limit"] == 10
        assert call_args[1]["params"]["offset"] == 20

    def test_list_sessions_by_tag_empty_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty string"):
            tracker.list_sessions_by_tag("")

    def test_list_sessions_by_tag_none_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty string"):
            tracker.list_sessions_by_tag(None)

    def test_list_sessions_by_tag_non_string_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty string"):
            tracker.list_sessions_by_tag(123)
