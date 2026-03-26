"""Tests for the CLI ``profile`` command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_profile import cmd_profile, _percentile


# -- _percentile ---------------------------------------------------------


def test_percentile_empty():
    assert _percentile([], 50) == 0.0


def test_percentile_single():
    assert _percentile([42.0], 50) == 42.0


def test_percentile_basic():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 50) == 30.0
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 100) == 50.0


def test_percentile_p95():
    values = list(range(1, 101))  # 1..100
    p95 = _percentile([float(v) for v in values], 95)
    assert 95 <= p95 <= 96


# -- cmd_profile ----------------------------------------------------------


def _make_session(agent: str, cost: float = 0.01, status: str = "completed",
                  tokens: int = 500, events: int = 5, created: str | None = None):
    if created is None:
        created = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"sess-{agent}-{cost}",
        "agent_name": agent,
        "status": status,
        "total_cost": cost,
        "total_tokens": tokens,
        "total_tokens_in": tokens // 2,
        "total_tokens_out": tokens // 2,
        "event_count": events,
        "created_at": created,
        "events": [
            {"model": "gpt-4", "tokens_in": 100, "tokens_out": 50, "duration_ms": 200,
             "event_type": "llm_call", "tool_call": None},
            {"model": "gpt-4", "tokens_in": 80, "tokens_out": 40, "duration_ms": 150,
             "event_type": "tool_call", "tool_call": {"tool_name": "search", "tool_input": {}}},
        ],
    }


@pytest.fixture
def mock_args():
    args = MagicMock()
    args.agent_name = "test-agent"
    args.days = 30
    args.json_output = True
    args.endpoint = "http://localhost:3000"
    args.api_key = "default"
    return args


def test_profile_no_sessions(mock_args, capsys):
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    with patch("agentlens.cli_profile.get_client") as gc:
        client = MagicMock()
        client.get.return_value = mock_resp
        gc.return_value = (client, "http://localhost:3000")
        cmd_profile(mock_args)

    out = capsys.readouterr().out
    assert "No sessions found" in out


def test_profile_json_output(mock_args, capsys):
    sessions = [
        _make_session("test-agent", cost=0.05, tokens=1000),
        _make_session("test-agent", cost=0.02, tokens=600, status="error"),
        _make_session("test-agent", cost=0.03, tokens=800),
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = sessions
    mock_resp.raise_for_status = MagicMock()

    with patch("agentlens.cli_profile.get_client") as gc:
        client = MagicMock()
        client.get.return_value = mock_resp
        gc.return_value = (client, "http://localhost:3000")
        cmd_profile(mock_args)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["agent"] == "test-agent"
    assert data["sessions"] == 3
    assert data["total_cost"] > 0
    assert data["error_rate_pct"] > 0
    assert "gpt-4" in data["models"]
    assert "search" in data["tools"]


def test_profile_table_output(mock_args, capsys):
    mock_args.json_output = False
    sessions = [_make_session("test-agent", cost=0.04)]
    mock_resp = MagicMock()
    mock_resp.json.return_value = sessions
    mock_resp.raise_for_status = MagicMock()

    with patch("agentlens.cli_profile.get_client") as gc:
        client = MagicMock()
        client.get.return_value = mock_resp
        gc.return_value = (client, "http://localhost:3000")
        cmd_profile(mock_args)

    out = capsys.readouterr().out
    assert "Agent Profile" in out
    assert "Cost" in out
    assert "Tokens" in out
    assert "Reliability" in out
