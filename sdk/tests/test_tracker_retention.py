"""Tests for RetentionMixin SDK methods (agentlens.tracker_retention)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


def _make_tracker():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    for verb in ("get", "post", "put", "delete"):
        resp = MagicMock()
        resp.json.return_value = {}
        getattr(transport, verb).return_value = resp
    return AgentTracker(transport=transport)


@pytest.fixture
def tracker():
    return _make_tracker()


def _set_json(mock_verb, payload):
    resp = MagicMock()
    resp.json.return_value = payload
    mock_verb.return_value = resp


# -- get_retention_config -----------------------------------------------


class TestGetRetentionConfig:
    def test_basic(self, tracker):
        _set_json(
            tracker.transport.get,
            {"config": {"max_age_days": 90, "auto_purge": True}},
        )
        result = tracker.get_retention_config()
        tracker.transport.get.assert_called_once_with("/retention/config")
        assert result["config"]["max_age_days"] == 90


# -- set_retention_config -----------------------------------------------


class TestSetRetentionConfig:
    def test_empty_call_raises(self, tracker):
        """Calling with no kwargs should raise — protects against
        clobbering server state with an empty PUT payload."""
        with pytest.raises(ValueError, match="At least one config"):
            tracker.set_retention_config()
        tracker.transport.put.assert_not_called()

    def test_max_age_only(self, tracker):
        tracker.set_retention_config(max_age_days=30)
        args, kwargs = tracker.transport.put.call_args
        assert args[0] == "/retention/config"
        assert kwargs["json"] == {"max_age_days": 30}

    def test_all_fields(self, tracker):
        tracker.set_retention_config(
            max_age_days=14,
            max_sessions=10_000,
            exempt_tags=["prod", "important"],
            auto_purge=True,
        )
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload == {
            "max_age_days": 14,
            "max_sessions": 10_000,
            "exempt_tags": ["prod", "important"],
            "auto_purge": True,
        }

    def test_zero_values_are_sent(self, tracker):
        """0 is a meaningful value (disables limit) — must not be
        dropped as "falsy"."""
        tracker.set_retention_config(max_age_days=0, max_sessions=0)
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload == {"max_age_days": 0, "max_sessions": 0}

    def test_false_auto_purge_is_sent(self, tracker):
        tracker.set_retention_config(auto_purge=False)
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload == {"auto_purge": False}

    def test_empty_exempt_tags_list_is_sent(self, tracker):
        """An empty list explicitly clears exempt tags — must be sent."""
        tracker.set_retention_config(exempt_tags=[])
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload == {"exempt_tags": []}

    def test_none_fields_skipped(self, tracker):
        tracker.set_retention_config(
            max_age_days=7, max_sessions=None, exempt_tags=None
        )
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload == {"max_age_days": 7}


# -- get_retention_stats ------------------------------------------------


class TestGetRetentionStats:
    def test_basic(self, tracker):
        _set_json(
            tracker.transport.get,
            {
                "sessions": 100,
                "events": 5000,
                "eligible_for_purge": 12,
            },
        )
        result = tracker.get_retention_stats()
        tracker.transport.get.assert_called_once_with("/retention/stats")
        assert result["sessions"] == 100
        assert result["eligible_for_purge"] == 12


# -- purge --------------------------------------------------------------


class TestPurge:
    def test_default_not_dry_run(self, tracker):
        _set_json(
            tracker.transport.post,
            {"dry_run": False, "purged_sessions": 5},
        )
        result = tracker.purge()
        args, kwargs = tracker.transport.post.call_args
        assert args[0] == "/retention/purge"
        # No dry_run param when False
        assert kwargs["params"] == {}
        assert kwargs["json"] == {}
        assert result["purged_sessions"] == 5

    def test_dry_run(self, tracker):
        _set_json(
            tracker.transport.post,
            {"dry_run": True, "would_purge_sessions": 7},
        )
        result = tracker.purge(dry_run=True)
        args, kwargs = tracker.transport.post.call_args
        assert args[0] == "/retention/purge"
        assert kwargs["params"] == {"dry_run": "true"}
        assert kwargs["json"] == {}
        assert result["would_purge_sessions"] == 7

    def test_real_purge_surfaces_batch_cap_fields(self, tracker):
        """When more sessions are eligible than the server's 500-session
        cap, the response carries ``total_eligible`` and ``remaining`` so
        the caller knows to purge again. The SDK must pass these through
        unchanged (documented in ``purge`` Returns)."""
        _set_json(
            tracker.transport.post,
            {
                "dry_run": False,
                "purged_sessions": 500,
                "purged_events": 12_345,
                "total_eligible": 720,
                "remaining": 220,
            },
        )
        result = tracker.purge()
        assert result["purged_sessions"] == 500
        assert result["total_eligible"] == 720
        # remaining > 0 is the documented signal to call purge() again
        assert result["remaining"] == 220

    def test_dry_run_surfaces_capped_flag(self, tracker):
        """A dry run over a backlog larger than the cap reports
        ``capped: true`` alongside ``total_eligible`` (documented in
        ``purge`` Returns)."""
        _set_json(
            tracker.transport.post,
            {
                "dry_run": True,
                "would_purge_sessions": 500,
                "would_purge_events": 9_000,
                "total_eligible": 640,
                "capped": True,
            },
        )
        result = tracker.purge(dry_run=True)
        assert result["capped"] is True
        assert result["total_eligible"] == 640
