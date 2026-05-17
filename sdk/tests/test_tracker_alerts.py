"""Tests for AlertMixin SDK methods (agentlens.tracker_alerts)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


# -- Fixtures -----------------------------------------------------------


def _make_tracker():
    """Build an AgentTracker with a fully-mocked Transport.

    Each transport HTTP verb is a MagicMock that returns a response
    whose ``.json()`` yields whatever we configure per-test. We default
    everything to ``{}`` so unconfigured tests don't blow up.
    """
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
    """Make ``mock_verb.return_value.json()`` return *payload*."""
    resp = MagicMock()
    resp.json.return_value = payload
    mock_verb.return_value = resp


# -- list_alert_rules ---------------------------------------------------


class TestListAlertRules:
    def test_no_filter(self, tracker):
        _set_json(tracker.transport.get, {"rules": []})
        result = tracker.list_alert_rules()
        tracker.transport.get.assert_called_once_with(
            "/alerts/rules", params={}
        )
        assert result == {"rules": []}

    def test_enabled_true(self, tracker):
        tracker.list_alert_rules(enabled=True)
        _, kwargs = tracker.transport.get.call_args
        assert kwargs["params"] == {"enabled": "true"}

    def test_enabled_false(self, tracker):
        tracker.list_alert_rules(enabled=False)
        _, kwargs = tracker.transport.get.call_args
        assert kwargs["params"] == {"enabled": "false"}

    def test_enabled_none_omits_param(self, tracker):
        tracker.list_alert_rules(enabled=None)
        _, kwargs = tracker.transport.get.call_args
        assert "enabled" not in kwargs["params"]


# -- create_alert_rule --------------------------------------------------


class TestCreateAlertRule:
    def test_required_fields_only(self, tracker):
        _set_json(tracker.transport.post, {"id": "r1"})
        result = tracker.create_alert_rule(
            "high cost", "total_tokens", ">", 1000.0
        )
        tracker.transport.post.assert_called_once()
        args, kwargs = tracker.transport.post.call_args
        assert args[0] == "/alerts/rules"
        payload = kwargs["json"]
        assert payload == {
            "name": "high cost",
            "metric": "total_tokens",
            "operator": ">",
            "threshold": 1000.0,
            "window_minutes": 60,
            "cooldown_minutes": 15,
        }
        assert "agent_filter" not in payload
        assert result == {"id": "r1"}

    def test_custom_windows_and_filter(self, tracker):
        tracker.create_alert_rule(
            name="slow agent",
            metric="avg_duration_ms",
            operator=">=",
            threshold=2500.5,
            window_minutes=10,
            agent_filter="planner",
            cooldown_minutes=5,
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["window_minutes"] == 10
        assert payload["cooldown_minutes"] == 5
        assert payload["agent_filter"] == "planner"
        assert payload["threshold"] == 2500.5

    def test_empty_agent_filter_omitted(self, tracker):
        """Falsy agent_filter ("" or None) should not be sent."""
        tracker.create_alert_rule(
            "x", "error_rate", ">", 0.1, agent_filter=""
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert "agent_filter" not in payload


# -- update_alert_rule --------------------------------------------------


class TestUpdateAlertRule:
    def test_partial_update(self, tracker):
        _set_json(tracker.transport.put, {"updated": True})
        result = tracker.update_alert_rule(
            "rule-42", threshold=500, enabled=False
        )
        args, kwargs = tracker.transport.put.call_args
        assert args[0] == "/alerts/rules/rule-42"
        assert kwargs["json"] == {"threshold": 500, "enabled": False}
        assert result == {"updated": True}

    def test_no_fields_still_sends_empty_dict(self, tracker):
        tracker.update_alert_rule("rule-7")
        assert tracker.transport.put.call_args[1]["json"] == {}


# -- delete_alert_rule --------------------------------------------------


class TestDeleteAlertRule:
    def test_path(self, tracker):
        _set_json(tracker.transport.delete, {"deleted": "rule-9"})
        result = tracker.delete_alert_rule("rule-9")
        tracker.transport.delete.assert_called_once_with(
            "/alerts/rules/rule-9"
        )
        assert result == {"deleted": "rule-9"}

    def test_id_with_special_chars_passes_through(self, tracker):
        # The mixin doesn't URL-encode; the server / transport layer
        # is responsible. We just verify the raw path.
        tracker.delete_alert_rule("a/b")
        assert tracker.transport.delete.call_args[0][0] == "/alerts/rules/a/b"


# -- evaluate_alerts ----------------------------------------------------


class TestEvaluateAlerts:
    def test_posts_to_evaluate(self, tracker):
        _set_json(tracker.transport.post, {"triggered": 2})
        result = tracker.evaluate_alerts()
        tracker.transport.post.assert_called_once_with("/alerts/evaluate")
        assert result == {"triggered": 2}


# -- get_alert_events ---------------------------------------------------


class TestGetAlertEvents:
    def test_defaults(self, tracker):
        _set_json(tracker.transport.get, {"events": []})
        result = tracker.get_alert_events()
        args, kwargs = tracker.transport.get.call_args
        assert args[0] == "/alerts/events"
        assert kwargs["params"] == {"limit": 50}
        assert result == {"events": []}

    def test_all_filters(self, tracker):
        tracker.get_alert_events(
            rule_id="rule-1", acknowledged=True, limit=200
        )
        params = tracker.transport.get.call_args[1]["params"]
        assert params == {
            "limit": 200,
            "rule_id": "rule-1",
            "acknowledged": "true",
        }

    def test_acknowledged_false(self, tracker):
        tracker.get_alert_events(acknowledged=False)
        assert (
            tracker.transport.get.call_args[1]["params"]["acknowledged"]
            == "false"
        )

    def test_acknowledged_none_omitted(self, tracker):
        tracker.get_alert_events(acknowledged=None)
        assert (
            "acknowledged"
            not in tracker.transport.get.call_args[1]["params"]
        )


# -- acknowledge_alert --------------------------------------------------


class TestAcknowledgeAlert:
    def test_path(self, tracker):
        _set_json(tracker.transport.put, {"ack": True})
        result = tracker.acknowledge_alert("evt-1")
        tracker.transport.put.assert_called_once_with(
            "/alerts/events/evt-1/acknowledge"
        )
        assert result == {"ack": True}


# -- get_alert_metrics --------------------------------------------------


class TestGetAlertMetrics:
    def test_path(self, tracker):
        _set_json(
            tracker.transport.get,
            {"metrics": ["total_tokens", "error_rate"]},
        )
        result = tracker.get_alert_metrics()
        tracker.transport.get.assert_called_once_with("/alerts/metrics")
        assert "metrics" in result
