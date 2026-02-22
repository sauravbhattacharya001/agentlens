"""Tests for alert rule SDK methods."""

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
    return AgentTracker(transport=transport)


class TestListAlertRules:
    def test_list_all_rules(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": [{"rule_id": "r1", "name": "Test"}]}
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.list_alert_rules()
        assert result["rules"][0]["name"] == "Test"
        tracker.transport._client.get.assert_called_once()
        call_args = tracker.transport._client.get.call_args
        assert "/alerts/rules" in call_args[0][0]

    def test_list_enabled_rules(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": []}
        tracker.transport._client.get.return_value = mock_resp

        tracker.list_alert_rules(enabled=True)
        call_kwargs = tracker.transport._client.get.call_args
        assert call_kwargs[1]["params"]["enabled"] == "true"

    def test_list_disabled_rules(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": []}
        tracker.transport._client.get.return_value = mock_resp

        tracker.list_alert_rules(enabled=False)
        call_kwargs = tracker.transport._client.get.call_args
        assert call_kwargs[1]["params"]["enabled"] == "false"


class TestCreateAlertRule:
    def test_create_basic_rule(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule": {"rule_id": "r1", "name": "High Tokens"}}
        tracker.transport._client.post.return_value = mock_resp

        result = tracker.create_alert_rule(
            name="High Tokens",
            metric="total_tokens",
            operator=">",
            threshold=1000,
        )
        assert result["rule"]["name"] == "High Tokens"
        call_args = tracker.transport._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["name"] == "High Tokens"
        assert payload["metric"] == "total_tokens"
        assert payload["operator"] == ">"
        assert payload["threshold"] == 1000

    def test_create_rule_with_agent_filter(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule": {"rule_id": "r2"}}
        tracker.transport._client.post.return_value = mock_resp

        tracker.create_alert_rule(
            name="Alpha Alert",
            metric="error_rate",
            operator=">",
            threshold=10,
            agent_filter="agent-alpha",
        )
        payload = tracker.transport._client.post.call_args[1]["json"]
        assert payload["agent_filter"] == "agent-alpha"

    def test_create_rule_with_custom_windows(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule": {"rule_id": "r3"}}
        tracker.transport._client.post.return_value = mock_resp

        tracker.create_alert_rule(
            name="Custom",
            metric="avg_duration_ms",
            operator=">",
            threshold=5000,
            window_minutes=30,
            cooldown_minutes=60,
        )
        payload = tracker.transport._client.post.call_args[1]["json"]
        assert payload["window_minutes"] == 30
        assert payload["cooldown_minutes"] == 60

    def test_create_rule_raises_on_error(self, tracker):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("400 Bad Request")
        tracker.transport._client.post.return_value = mock_resp

        with pytest.raises(Exception):
            tracker.create_alert_rule(
                name="Bad",
                metric="invalid",
                operator=">",
                threshold=100,
            )


class TestUpdateAlertRule:
    def test_update_rule(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule": {"rule_id": "r1", "name": "Updated"}}
        tracker.transport._client.put.return_value = mock_resp

        result = tracker.update_alert_rule("r1", name="Updated", threshold=2000)
        assert result["rule"]["name"] == "Updated"
        call_args = tracker.transport._client.put.call_args
        assert "r1" in call_args[0][0]
        assert call_args[1]["json"]["name"] == "Updated"


class TestDeleteAlertRule:
    def test_delete_rule(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": True}
        tracker.transport._client.delete.return_value = mock_resp

        result = tracker.delete_alert_rule("r1")
        assert result["deleted"] is True


class TestEvaluateAlerts:
    def test_evaluate(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "evaluated": 2, "fired": 1, "cooldown": 0, "ok": 1,
            "results": [
                {"rule_id": "r1", "status": "fired"},
                {"rule_id": "r2", "status": "ok"},
            ]
        }
        tracker.transport._client.post.return_value = mock_resp

        result = tracker.evaluate_alerts()
        assert result["evaluated"] == 2
        assert result["fired"] == 1
        assert "/alerts/evaluate" in tracker.transport._client.post.call_args[0][0]


class TestGetAlertEvents:
    def test_get_all_events(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [{"alert_id": "a1"}], "count": 1}
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.get_alert_events()
        assert result["count"] == 1

    def test_get_unacknowledged(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [], "count": 0}
        tracker.transport._client.get.return_value = mock_resp

        tracker.get_alert_events(acknowledged=False)
        params = tracker.transport._client.get.call_args[1]["params"]
        assert params["acknowledged"] == "false"

    def test_get_by_rule_id(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [], "count": 0}
        tracker.transport._client.get.return_value = mock_resp

        tracker.get_alert_events(rule_id="r1")
        params = tracker.transport._client.get.call_args[1]["params"]
        assert params["rule_id"] == "r1"


class TestAcknowledgeAlert:
    def test_acknowledge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"acknowledged": True}
        tracker.transport._client.put.return_value = mock_resp

        result = tracker.acknowledge_alert("a1")
        assert result["acknowledged"] is True
        assert "a1" in tracker.transport._client.put.call_args[0][0]


class TestGetAlertMetrics:
    def test_get_metrics(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "metrics": [{"name": "total_tokens", "description": "Total tokens"}],
            "operators": [">", "<"],
        }
        tracker.transport._client.get.return_value = mock_resp

        result = tracker.get_alert_metrics()
        assert len(result["metrics"]) == 1
        assert result["metrics"][0]["name"] == "total_tokens"
