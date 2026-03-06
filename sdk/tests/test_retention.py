"""Tests for data retention & cleanup SDK methods."""

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
    return AgentTracker(transport=transport)


class TestGetRetentionConfig:
    def test_returns_config(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "config": {
                "max_age_days": 90,
                "max_sessions": 0,
                "exempt_tags": [],
                "auto_purge": False,
            }
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_retention_config()
        assert result["config"]["max_age_days"] == 90
        assert result["config"]["max_sessions"] == 0
        assert result["config"]["exempt_tags"] == []
        assert result["config"]["auto_purge"] is False

    def test_calls_correct_endpoint(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}}
        tracker.transport.get.return_value = mock_resp

        tracker.get_retention_config()
        call_args = tracker.transport.get.call_args
        assert "/retention/config" in call_args[0][0]

    def test_sends_api_key(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}}
        tracker.transport.get.return_value = mock_resp

        tracker.get_retention_config()
        # Auth headers are now handled internally by Transport
        tracker.transport.get.assert_called_once()


class TestSetRetentionConfig:
    def test_updates_max_age_days(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"max_age_days": 30}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        result = tracker.set_retention_config(max_age_days=30)
        assert result["config"]["max_age_days"] == 30
        assert result["updated"] == 1

    def test_updates_max_sessions(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"max_sessions": 500}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        result = tracker.set_retention_config(max_sessions=500)
        call_json = tracker.transport.put.call_args[1]["json"]
        assert call_json["max_sessions"] == 500

    def test_updates_exempt_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"exempt_tags": ["prod"]}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(exempt_tags=["prod"])
        call_json = tracker.transport.put.call_args[1]["json"]
        assert call_json["exempt_tags"] == ["prod"]

    def test_updates_auto_purge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"auto_purge": True}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(auto_purge=True)
        call_json = tracker.transport.put.call_args[1]["json"]
        assert call_json["auto_purge"] is True

    def test_updates_multiple_fields(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}, "updated": 3}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(max_age_days=14, max_sessions=100, auto_purge=True)
        call_json = tracker.transport.put.call_args[1]["json"]
        assert call_json["max_age_days"] == 14
        assert call_json["max_sessions"] == 100
        assert call_json["auto_purge"] is True

    def test_raises_when_no_fields(self, tracker):
        with pytest.raises(ValueError, match="At least one config field"):
            tracker.set_retention_config()

    def test_calls_correct_endpoint(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(max_age_days=30)
        call_args = tracker.transport.put.call_args
        assert "/retention/config" in call_args[0][0]

    def test_sends_api_key(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(max_age_days=30)
        # Auth headers are now handled internally by Transport
        tracker.transport.put.assert_called_once()


class TestGetRetentionStats:
    def test_returns_stats(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": 42,
            "events": 300,
            "avg_events_per_session": 7.1,
            "oldest_session": "2025-01-01T00:00:00Z",
            "newest_session": "2026-02-22T00:00:00Z",
            "age_breakdown": {"last_24h": 5, "last_7d": 10, "last_30d": 15, "last_90d": 8, "older": 4},
            "status_breakdown": {"completed": 35, "active": 7},
            "eligible_for_purge": 4,
            "config": {},
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_retention_stats()
        assert result["sessions"] == 42
        assert result["events"] == 300
        assert result["avg_events_per_session"] == 7.1
        assert result["eligible_for_purge"] == 4
        assert result["age_breakdown"]["last_24h"] == 5

    def test_calls_correct_endpoint(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        tracker.transport.get.return_value = mock_resp

        tracker.get_retention_stats()
        call_args = tracker.transport.get.call_args
        assert "/retention/stats" in call_args[0][0]


class TestPurge:
    def test_purge_actual(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "dry_run": False,
            "purged_sessions": 3,
            "purged_events": 25,
            "details": [
                {"session_id": "s1", "reason": "age", "events_deleted": 10},
                {"session_id": "s2", "reason": "age", "events_deleted": 8},
                {"session_id": "s3", "reason": "count", "events_deleted": 7},
            ],
            "message": "Purged 3 sessions and 25 events",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.purge()
        assert result["dry_run"] is False
        assert result["purged_sessions"] == 3
        assert result["purged_events"] == 25
        assert len(result["details"]) == 3

    def test_purge_dry_run(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "dry_run": True,
            "would_purge_sessions": 2,
            "would_purge_events": 15,
            "details": [],
            "message": "Would purge 2 sessions",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.purge(dry_run=True)
        assert result["dry_run"] is True
        assert result["would_purge_sessions"] == 2

        call_kwargs = tracker.transport.post.call_args[1]
        assert call_kwargs["params"]["dry_run"] == "true"

    def test_purge_no_dry_run_flag(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"dry_run": False, "purged_sessions": 0}
        tracker.transport.post.return_value = mock_resp

        tracker.purge()
        call_kwargs = tracker.transport.post.call_args[1]
        assert call_kwargs.get("params", {}) == {}

    def test_purge_calls_correct_endpoint(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        tracker.transport.post.return_value = mock_resp

        tracker.purge()
        call_args = tracker.transport.post.call_args
        assert "/retention/purge" in call_args[0][0]

    def test_purge_sends_api_key(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        tracker.transport.post.return_value = mock_resp

        tracker.purge()
        # Auth headers are now handled internally by Transport
        tracker.transport.post.assert_called_once()

    def test_purge_sends_empty_json_body(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        tracker.transport.post.return_value = mock_resp

        tracker.purge()
        call_kwargs = tracker.transport.post.call_args[1]
        assert call_kwargs["json"] == {}

    def test_purge_nothing_to_purge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "dry_run": False,
            "purged_sessions": 0,
            "purged_events": 0,
            "details": [],
            "message": "No sessions eligible for purge",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.purge()
        assert result["purged_sessions"] == 0
        assert "No sessions" in result["message"]
