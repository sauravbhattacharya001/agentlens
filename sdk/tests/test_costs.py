"""Tests for cost estimation — tracker.get_costs/get_pricing/set_pricing."""

from unittest.mock import MagicMock, patch
import json

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


class TestGetCosts:
    def test_get_costs_with_session(self, tracker, mock_transport):
        session = tracker.start_session(agent_name="cost-agent")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session_id": session.session_id,
            "total_cost": 0.0015,
            "total_input_cost": 0.0005,
            "total_output_cost": 0.001,
            "currency": "USD",
            "model_costs": {
                "gpt-4": {
                    "calls": 2,
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "input_cost": 0.003,
                    "output_cost": 0.003,
                    "total_cost": 0.006,
                    "matched": True,
                }
            },
            "event_costs": [],
            "unmatched_models": [],
        }
        mock_transport._client.get.return_value = mock_response

        result = tracker.get_costs()

        assert result["total_cost"] == 0.0015
        assert result["currency"] == "USD"
        assert "gpt-4" in result["model_costs"]
        mock_transport._client.get.assert_called_once()

    def test_get_costs_specific_session(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {"total_cost": 0.01}
        mock_transport._client.get.return_value = mock_response

        result = tracker.get_costs(session_id="abc123")

        assert result["total_cost"] == 0.01
        call_args = mock_transport._client.get.call_args
        assert "abc123" in call_args[0][0]

    def test_get_costs_no_session_raises(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.get_costs()

    def test_get_costs_includes_headers(self, tracker, mock_transport):
        session = tracker.start_session()

        mock_response = MagicMock()
        mock_response.json.return_value = {"total_cost": 0}
        mock_transport._client.get.return_value = mock_response

        tracker.get_costs()

        call_kwargs = mock_transport._client.get.call_args
        assert call_kwargs[1]["headers"]["X-API-Key"] == "test-key"


class TestGetPricing:
    def test_get_pricing(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "pricing": {
                "gpt-4": {"input_cost_per_1m": 30.0, "output_cost_per_1m": 60.0, "currency": "USD"},
                "gpt-4o": {"input_cost_per_1m": 2.5, "output_cost_per_1m": 10.0, "currency": "USD"},
            },
            "defaults": {
                "gpt-4": {"input": 30.0, "output": 60.0},
            },
        }
        mock_transport._client.get.return_value = mock_response

        result = tracker.get_pricing()

        assert "gpt-4" in result["pricing"]
        assert result["pricing"]["gpt-4"]["input_cost_per_1m"] == 30.0
        assert "defaults" in result

    def test_get_pricing_endpoint(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {"pricing": {}, "defaults": {}}
        mock_transport._client.get.return_value = mock_response

        tracker.get_pricing()

        call_args = mock_transport._client.get.call_args
        assert "/pricing" in call_args[0][0]


class TestSetPricing:
    def test_set_pricing(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok", "updated": 2}
        mock_transport._client.put.return_value = mock_response

        pricing = {
            "gpt-4": {"input_cost_per_1m": 25.0, "output_cost_per_1m": 50.0},
            "custom-model": {"input_cost_per_1m": 1.0, "output_cost_per_1m": 2.0},
        }
        result = tracker.set_pricing(pricing)

        assert result["status"] == "ok"
        assert result["updated"] == 2

    def test_set_pricing_sends_correct_body(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok", "updated": 1}
        mock_transport._client.put.return_value = mock_response

        pricing = {"gpt-4": {"input_cost_per_1m": 30.0, "output_cost_per_1m": 60.0}}
        tracker.set_pricing(pricing)

        call_kwargs = mock_transport._client.put.call_args
        assert call_kwargs[1]["json"]["pricing"] == pricing

    def test_set_pricing_endpoint(self, tracker, mock_transport):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok", "updated": 0}
        mock_transport._client.put.return_value = mock_response

        tracker.set_pricing({})

        call_args = mock_transport._client.put.call_args
        assert "/pricing" in call_args[0][0]


class TestCostInitModule:
    """Test module-level cost functions in agentlens.__init__."""

    def test_get_costs_uninitialized(self):
        import agentlens
        old_tracker = agentlens._tracker
        agentlens._tracker = None
        try:
            with pytest.raises(RuntimeError, match="init"):
                agentlens.get_costs()
        finally:
            agentlens._tracker = old_tracker

    def test_get_pricing_uninitialized(self):
        import agentlens
        old_tracker = agentlens._tracker
        agentlens._tracker = None
        try:
            with pytest.raises(RuntimeError, match="init"):
                agentlens.get_pricing()
        finally:
            agentlens._tracker = old_tracker

    def test_set_pricing_uninitialized(self):
        import agentlens
        old_tracker = agentlens._tracker
        agentlens._tracker = None
        try:
            with pytest.raises(RuntimeError, match="init"):
                agentlens.set_pricing({})
        finally:
            agentlens._tracker = old_tracker
