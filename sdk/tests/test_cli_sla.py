"""Tests for CLI sla command."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_sla import cmd_sla, _build_custom_policy, _progress_bar, _status_icon
from agentlens.sla import ComplianceStatus


class TestHelpers:
    def test_status_icon_compliant(self):
        assert _status_icon(ComplianceStatus.COMPLIANT) == "✅"

    def test_status_icon_violated(self):
        assert _status_icon(ComplianceStatus.VIOLATED) == "❌"

    def test_progress_bar_full(self):
        bar = _progress_bar(100.0, width=10)
        assert "100%" in bar

    def test_progress_bar_empty(self):
        bar = _progress_bar(0.0, width=10)
        assert "0%" in bar

    def test_build_custom_policy_none(self):
        args = argparse.Namespace(latency=None, error_rate_target=None, token_budget=None, slo=99.0)
        assert _build_custom_policy(args) is None

    def test_build_custom_policy_latency(self):
        args = argparse.Namespace(latency=2000.0, error_rate_target=None, token_budget=None, slo=95.0)
        policy = _build_custom_policy(args)
        assert policy is not None
        assert policy.name == "custom"
        assert len(policy.objectives) == 1
        assert policy.objectives[0].target == 2000.0

    def test_build_custom_policy_multiple(self):
        args = argparse.Namespace(latency=3000.0, error_rate_target=5.0, token_budget=10000, slo=99.0)
        policy = _build_custom_policy(args)
        assert policy is not None
        assert len(policy.objectives) == 3


class TestCmdSla:
    def _make_args(self, **kwargs):
        defaults = {
            "endpoint": "http://localhost:3000",
            "api_key": "default",
            "limit": 50,
            "json_output": True,
            "agent": None,
            "policy": "production",
            "verbose": False,
            "latency": None,
            "error_rate_target": None,
            "token_budget": None,
            "slo": 99.0,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @patch("agentlens.cli_sla._get_client")
    def test_no_sessions(self, mock_gc, capsys):
        client = MagicMock()
        mock_gc.return_value = client
        client.get.return_value = MagicMock(status_code=200)
        client.get.return_value.json.return_value = []
        client.get.return_value.raise_for_status = MagicMock()

        cmd_sla(self._make_args())
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "error" in parsed

    @patch("agentlens.cli_sla._get_client")
    def test_compliant_sessions(self, mock_gc, capsys):
        client = MagicMock()
        mock_gc.return_value = client

        sessions = [
            {"session_id": f"s{i}", "agent_name": "test"}
            for i in range(10)
        ]
        events = [
            {"event_type": "llm_call", "duration_ms": 100, "tokens_in": 50, "tokens_out": 50, "tool_call": None}
            for _ in range(5)
        ]

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if url == "/sessions":
                resp.json.return_value = sessions
            else:
                resp.json.return_value = events
            return resp

        client.get.side_effect = side_effect

        cmd_sla(self._make_args())
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["policy_name"] == "production"
        assert parsed["total_sessions"] == 10
