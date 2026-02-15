"""Tests for agentlens top-level init/start/end/track API."""

from unittest.mock import MagicMock, patch

import pytest

import agentlens
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
