"""Tests for session annotations SDK methods."""

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
    t._current_session_id = "test-session-123"
    return t


@pytest.fixture
def tracker_no_session():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    return AgentTracker(transport=transport)


# ── annotate() ───────────────────────────────────────────────────────

class TestAnnotate:
    def test_basic_annotation(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "annotation_id": "ann-123",
            "session_id": "test-session-123",
            "text": "Bug found here",
            "author": "sdk",
            "type": "note",
            "event_id": None,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.annotate("Bug found here")
        assert result["annotation_id"] == "ann-123"
        assert result["text"] == "Bug found here"
        call_args = tracker.transport.post.call_args
        assert "/sessions/test-session-123/annotations" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["text"] == "Bug found here"
        assert payload["author"] == "sdk"
        assert payload["type"] == "note"

    def test_with_all_fields(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "annotation_id": "ann-456",
            "text": "Performance degraded",
            "author": "alice",
            "type": "warning",
            "event_id": "evt-001",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.annotate(
            "Performance degraded",
            author="alice",
            annotation_type="warning",
            event_id="evt-001",
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["author"] == "alice"
        assert payload["type"] == "warning"
        assert payload["event_id"] == "evt-001"

    def test_with_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-789", "session_id": "other"}
        tracker.transport.post.return_value = mock_resp

        tracker.annotate("Note", session_id="other")
        call_url = tracker.transport.post.call_args[0][0]
        assert "/sessions/other/annotations" in call_url

    def test_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session to annotate"):
            tracker_no_session.annotate("test")

    def test_empty_text_raises(self, tracker):
        with pytest.raises(ValueError, match="text must be a non-empty string"):
            tracker.annotate("")

    def test_whitespace_text_raises(self, tracker):
        with pytest.raises(ValueError, match="text must be a non-empty string"):
            tracker.annotate("   ")

    def test_none_text_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.annotate(None)

    def test_each_annotation_type(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-x"}
        tracker.transport.post.return_value = mock_resp

        for t in ["note", "bug", "insight", "warning", "milestone"]:
            tracker.annotate("Test", annotation_type=t)
            payload = tracker.transport.post.call_args[1]["json"]
            assert payload["type"] == t


# ── get_annotations() ────────────────────────────────────────────────

class TestGetAnnotations:
    def test_get_annotations(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "test-session-123",
            "total": 3,
            "returned": 3,
            "annotations": [
                {"annotation_id": "a1", "text": "Note 1", "type": "note"},
                {"annotation_id": "a2", "text": "Note 2", "type": "bug"},
                {"annotation_id": "a3", "text": "Note 3", "type": "insight"},
            ],
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_annotations()
        assert result["total"] == 3
        assert len(result["annotations"]) == 3

    def test_with_filters(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "test-session-123", "total": 1, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(annotation_type="bug", author="alice", limit=10, offset=5)
        call_args = tracker.transport.get.call_args
        params = call_args[1]["params"]
        assert params["type"] == "bug"
        assert params["author"] == "alice"
        assert params["limit"] == 10
        assert params["offset"] == 5

    def test_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "other", "total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(session_id="other")
        call_url = tracker.transport.get.call_args[0][0]
        assert "/sessions/other/annotations" in call_url

    def test_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session to query"):
            tracker_no_session.get_annotations()

    def test_limit_clamped(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(limit=9999)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 500


# ── update_annotation() ──────────────────────────────────────────────

class TestUpdateAnnotation:
    def test_update_text(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "annotation_id": "ann-123",
            "text": "Updated",
            "type": "note",
        }
        tracker.transport.put.return_value = mock_resp

        result = tracker.update_annotation("ann-123", text="Updated")
        assert result["text"] == "Updated"
        call_args = tracker.transport.put.call_args
        assert "/sessions/test-session-123/annotations/ann-123" in call_args[0][0]
        assert call_args[1]["json"] == {"text": "Updated"}

    def test_update_type(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-123", "type": "bug"}
        tracker.transport.put.return_value = mock_resp

        tracker.update_annotation("ann-123", annotation_type="bug")
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["type"] == "bug"

    def test_update_author(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-123"}
        tracker.transport.put.return_value = mock_resp

        tracker.update_annotation("ann-123", author="charlie")
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["author"] == "charlie"

    def test_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session specified"):
            tracker_no_session.update_annotation("ann-123", text="test")

    def test_no_annotation_id_raises(self, tracker):
        with pytest.raises(ValueError, match="annotation_id is required"):
            tracker.update_annotation("", text="test")

    def test_no_fields_raises(self, tracker):
        with pytest.raises(ValueError, match="At least one field"):
            tracker.update_annotation("ann-123")

    def test_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-123"}
        tracker.transport.put.return_value = mock_resp

        tracker.update_annotation("ann-123", session_id="other", text="test")
        call_url = tracker.transport.put.call_args[0][0]
        assert "/sessions/other/annotations/ann-123" in call_url


# ── delete_annotation() ──────────────────────────────────────────────

class TestDeleteAnnotation:
    def test_delete_annotation(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": True, "annotation_id": "ann-123"}
        tracker.transport.delete.return_value = mock_resp

        result = tracker.delete_annotation("ann-123")
        assert result["deleted"] is True
        call_url = tracker.transport.delete.call_args[0][0]
        assert "/sessions/test-session-123/annotations/ann-123" in call_url

    def test_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError, match="No session specified"):
            tracker_no_session.delete_annotation("ann-123")

    def test_no_annotation_id_raises(self, tracker):
        with pytest.raises(ValueError, match="annotation_id is required"):
            tracker.delete_annotation("")

    def test_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": True}
        tracker.transport.delete.return_value = mock_resp

        tracker.delete_annotation("ann-123", session_id="other")
        call_url = tracker.transport.delete.call_args[0][0]
        assert "/sessions/other/annotations/ann-123" in call_url


# ── list_recent_annotations() ────────────────────────────────────────

class TestListRecentAnnotations:
    def test_list_recent(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total": 2,
            "annotations": [
                {"annotation_id": "a1", "agent_name": "agent-1", "text": "Recent 1"},
                {"annotation_id": "a2", "agent_name": "agent-2", "text": "Recent 2"},
            ],
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_recent_annotations()
        assert result["total"] == 2
        call_url = tracker.transport.get.call_args[0][0]
        assert "/annotations" in call_url

    def test_with_type_filter(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_recent_annotations(annotation_type="bug")
        params = tracker.transport.get.call_args[1]["params"]
        assert params["type"] == "bug"

    def test_limit_clamped(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_recent_annotations(limit=999)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 200
