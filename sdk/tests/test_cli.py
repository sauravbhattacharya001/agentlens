"""Tests for the AgentLens CLI module."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli import main, _print_table


class TestPrintTable:
    def test_empty(self, capsys):
        _print_table([], ["a", "b"])
        assert "(no data)" in capsys.readouterr().out

    def test_basic(self, capsys):
        rows = [{"name": "alice", "age": 30}, {"name": "bob", "age": 25}]
        _print_table(rows, ["name", "age"])
        out = capsys.readouterr().out
        assert "alice" in out
        assert "bob" in out
        assert "name" in out

    def test_truncation(self, capsys):
        rows = [{"val": "x" * 100}]
        _print_table(rows, ["val"], max_width=10)
        out = capsys.readouterr().out
        assert "x" * 10 in out
        assert "x" * 11 not in out


class TestStatusCommand:
    @patch("agentlens.cli.httpx.Client")
    def test_healthy(self, mock_client_cls, capsys):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "uptime": 1234}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("sys.argv", ["agentlens", "--endpoint", "http://test:3000", "status"]):
            main()

        out = capsys.readouterr().out
        assert "✅" in out or "healthy" in out.lower()

    @patch("agentlens.cli.httpx.Client")
    def test_unreachable(self, mock_client_cls, capsys):
        import httpx as real_httpx

        mock_client = MagicMock()
        mock_client.get.side_effect = real_httpx.ConnectError("refused")
        mock_client_cls.return_value = mock_client

        with patch("sys.argv", ["agentlens", "--endpoint", "http://bad:3000", "status"]):
            with pytest.raises(SystemExit):
                main()

        out = capsys.readouterr().out
        assert "❌" in out or "Cannot" in out


class TestSessionsCommand:
    @patch("agentlens.cli.httpx.Client")
    def test_list(self, mock_client_cls, capsys):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "id": "s1",
                "agent_name": "test-agent",
                "status": "active",
                "event_count": 5,
                "total_tokens": 1000,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("sys.argv", ["agentlens", "--endpoint", "http://test:3000", "sessions"]):
            main()

        out = capsys.readouterr().out
        assert "test-agent" in out
        assert "s1" in out


class TestCostsCommand:
    @patch("agentlens.cli.httpx.Client")
    def test_costs(self, mock_client_cls, capsys):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total_cost": 0.05,
            "total_input_cost": 0.03,
            "total_output_cost": 0.02,
            "model_costs": {"gpt-4": {"total": 0.05}},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("sys.argv", ["agentlens", "--endpoint", "http://test:3000", "costs", "s1"]):
            main()

        out = capsys.readouterr().out
        assert "0.05" in out
        assert "gpt-4" in out


class TestExportCommand:
    @patch("agentlens.cli.httpx.Client")
    def test_json_stdout(self, mock_client_cls, capsys):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session": "s1", "events": []}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("sys.argv", ["agentlens", "--endpoint", "http://test:3000", "export", "s1"]):
            main()

        out = capsys.readouterr().out
        assert "s1" in out
