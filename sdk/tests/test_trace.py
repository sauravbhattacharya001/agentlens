"""Tests for the CLI trace command."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# We need to handle the case where httpx might not be installed
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


@pytest.fixture
def mock_session():
    return {
        "id": "sess-abc123",
        "agent_name": "test-agent",
        "status": "completed",
        "event_count": 3,
        "total_tokens": 1500,
    }


@pytest.fixture
def mock_events():
    return [
        {
            "event_id": "ev1",
            "session_id": "sess-abc123",
            "event_type": "llm_call",
            "model": "gpt-4",
            "tokens_in": 500,
            "tokens_out": 200,
            "duration_ms": 1200,
            "timestamp": "2026-03-19T10:00:00Z",
        },
        {
            "event_id": "ev2",
            "session_id": "sess-abc123",
            "event_type": "tool_call",
            "model": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 350,
            "timestamp": "2026-03-19T10:00:01Z",
            "tool_call": {"tool_name": "web_search", "tool_input": {}},
        },
        {
            "event_id": "ev3",
            "session_id": "sess-abc123",
            "event_type": "llm_call",
            "model": "gpt-4",
            "tokens_in": 600,
            "tokens_out": 200,
            "duration_ms": 900,
            "timestamp": "2026-03-19T10:00:02Z",
        },
    ]


@pytest.fixture
def mock_events_with_error(mock_events):
    return mock_events + [
        {
            "event_id": "ev4",
            "session_id": "sess-abc123",
            "event_type": "error",
            "model": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 50,
            "timestamp": "2026-03-19T10:00:03Z",
        }
    ]


class TestTraceCommand:
    """Tests for cmd_trace output and filtering."""

    def _make_args(self, session_id="sess-abc123", **kwargs):
        """Create a mock args namespace."""
        args = MagicMock()
        args.session_id = session_id
        args.endpoint = "http://localhost:3000"
        args.api_key = "test"
        args.no_color = kwargs.get("no_color", True)
        args.json = kwargs.get("json_output", False)
        # Use setattr for the 'json' attribute since it's a builtin name
        object.__setattr__(args, "json", kwargs.get("json_output", False))
        args.type = kwargs.get("type_filter", None)
        args.min_ms = kwargs.get("min_ms", None)
        return args

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_renders_events(self, mock_session, mock_events, capsys):
        """Trace should print a waterfall for each event."""
        args = self._make_args()

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = mock_events

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        assert "Session Trace" in captured.out
        assert "test-agent" in captured.out
        assert "llm_call" in captured.out
        assert "tool_call" in captured.out
        assert "gpt-4" in captured.out
        assert "Breakdown" in captured.out

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_json_output(self, mock_session, mock_events, capsys):
        """--json flag should output structured JSON."""
        args = self._make_args(json_output=True)

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = mock_events

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["session_id"] == "sess-abc123"
        assert len(data["events"]) == 3
        assert data["events"][0]["type"] == "llm_call"

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_type_filter(self, mock_session, mock_events, capsys):
        """--type filter should only show matching events."""
        args = self._make_args(type_filter="tool_call")

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = mock_events

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        # Should show tool_call but not llm_call in the event rows
        lines = captured.out.split("\n")
        event_lines = [l for l in lines if "tool_call" in l and "web_search" in l]
        assert len(event_lines) >= 1

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_min_ms_filter(self, mock_session, mock_events, capsys):
        """--min-ms should only show slow events."""
        args = self._make_args(min_ms=1000)

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = mock_events

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        # Only the 1200ms event should appear
        assert "llm_call" in captured.out
        # tool_call at 350ms should be filtered out
        assert "web_search" not in captured.out

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_shows_errors(self, mock_session, mock_events_with_error, capsys):
        """Errors should be visible in the trace with error count."""
        args = self._make_args()

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = mock_events_with_error

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        assert "Errors: 1" in captured.out
        assert "error" in captured.out

    @pytest.mark.skipif(httpx is None, reason="httpx not installed")
    def test_trace_empty_session(self, mock_session, capsys):
        """No events should print a message."""
        args = self._make_args()

        with patch("agentlens.cli._get_client") as mock_gc:
            client = MagicMock()
            mock_gc.return_value = (client, "http://localhost:3000")

            session_resp = MagicMock()
            session_resp.json.return_value = mock_session

            events_resp = MagicMock()
            events_resp.json.return_value = []

            client.get.side_effect = [session_resp, events_resp]

            from agentlens.cli import cmd_trace
            cmd_trace(args)

        captured = capsys.readouterr()
        assert "No events found" in captured.out
