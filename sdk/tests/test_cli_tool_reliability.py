"""Tests for cli_tool_reliability — tool reliability scorecard CLI command."""

from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_tool_reliability import (
    _bar,
    _grade_icon,
    _pct_color,
    _render_table,
    _verdict_icon,
    cmd_tool_reliability,
    register,
)
from agentlens.tool_reliability_advisor import (
    ToolReliabilityAdvisor,
    ToolReliabilityGrade,
    ToolVerdict,
)


# ── Helper fixtures ──────────────────────────────────────────────────────────


def _make_tool_events(tool_name: str, count: int, error_rate: float = 0.0):
    """Generate synthetic tool call events."""
    events = []
    for i in range(count):
        ev = {
            "event_type": "tool_call",
            "tool_name": tool_name,
            "session_id": f"session-{i % 5}",
            "agent": "test-agent",
            "duration_ms": 50 + (i * 3),
            "timestamp": f"2024-06-01T12:{i % 60:02d}:00Z",
        }
        if i < int(count * error_rate):
            ev["error"] = "timeout"
            ev["error_code"] = "TIMEOUT"
        events.append(ev)
    return events


def _mock_args(**kwargs):
    defaults = {
        "endpoint": "http://localhost:3100",
        "api_key": None,
        "agent": None,
        "limit": 100,
        "appetite": "balanced",
        "format": "table",
        "output": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── Unit tests for display helpers ───────────────────────────────────────────


class TestDisplayHelpers:
    def test_grade_icon_all_grades(self):
        assert _grade_icon(ToolReliabilityGrade.A) == "🟢"
        assert _grade_icon(ToolReliabilityGrade.B) == "🔵"
        assert _grade_icon(ToolReliabilityGrade.C) == "🟡"
        assert _grade_icon(ToolReliabilityGrade.D) == "🟠"
        assert _grade_icon(ToolReliabilityGrade.F) == "🔴"

    def test_verdict_icon_all(self):
        assert _verdict_icon(ToolVerdict.HEALTHY) == "✅"
        assert _verdict_icon(ToolVerdict.WATCH) == "👀"
        assert _verdict_icon(ToolVerdict.FLAKY) == "⚡"
        assert _verdict_icon(ToolVerdict.DEGRADED) == "⚠️"
        assert _verdict_icon(ToolVerdict.CIRCUIT_BREAK) == "🚫"
        assert _verdict_icon(ToolVerdict.DEPRECATE_CANDIDATE) == "🗑️"
        assert _verdict_icon(ToolVerdict.INSUFFICIENT_DATA) == "❓"

    def test_bar_full(self):
        result = _bar(100, 100, 10)
        assert result == "██████████"

    def test_bar_half(self):
        result = _bar(50, 100, 10)
        assert result == "█████░░░░░"

    def test_bar_empty(self):
        result = _bar(0, 100, 10)
        assert result == "░░░░░░░░░░"

    def test_bar_zero_max(self):
        result = _bar(50, 0, 10)
        assert result == " " * 10

    def test_pct_color(self):
        assert _pct_color(25) == "CRITICAL"
        assert _pct_color(15) == "HIGH"
        assert _pct_color(7) == "MEDIUM"
        assert _pct_color(2) == "LOW"


# ── Integration tests with ToolReliabilityAdvisor ────────────────────────────


class TestRenderTable:
    def test_renders_healthy_report(self):
        events = _make_tool_events("web_search", 50, error_rate=0.02)
        events += _make_tool_events("code_exec", 30, error_rate=0.01)
        advisor = ToolReliabilityAdvisor()
        report = advisor.analyze(events)
        output = _render_table(report, session_count=5, event_count=80)
        assert "Tool Reliability Scorecard" in output
        assert "web_search" in output
        assert "code_exec" in output
        assert "Reliability" in output

    def test_renders_degraded_report(self):
        events = _make_tool_events("broken_tool", 40, error_rate=0.5)
        events += _make_tool_events("good_tool", 60, error_rate=0.01)
        advisor = ToolReliabilityAdvisor()
        report = advisor.analyze(events)
        output = _render_table(report, session_count=5, event_count=100)
        assert "broken_tool" in output
        assert "good_tool" in output
        assert "Playbook" in output

    def test_renders_empty_snapshots(self):
        advisor = ToolReliabilityAdvisor()
        report = advisor.analyze([])
        output = _render_table(report, session_count=0, event_count=0)
        assert "No tools observed" in output

    def test_renders_insights(self):
        events = _make_tool_events("flaky", 100, error_rate=0.3)
        advisor = ToolReliabilityAdvisor()
        report = advisor.analyze(events)
        output = _render_table(report, session_count=10, event_count=100)
        assert "Insights" in output


# ── CLI command tests ────────────────────────────────────────────────────────


class TestCmdToolReliability:
    @patch("agentlens.cli_tool_reliability.get_client_only")
    def test_json_output(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock sessions response
        sessions_resp = MagicMock()
        sessions_resp.json.return_value = {"sessions": [{"id": "s1"}]}
        sessions_resp.raise_for_status = MagicMock()

        # Mock events response
        events_resp = MagicMock()
        events_resp.json.return_value = {
            "events": _make_tool_events("search", 20, error_rate=0.1)
        }
        events_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = [sessions_resp, events_resp]

        args = _mock_args(format="json")
        captured = StringIO()
        with patch("sys.stdout", captured):
            cmd_tool_reliability(args)

        output = captured.getvalue()
        parsed = json.loads(output)
        assert "portfolio" in parsed
        assert "snapshots" in parsed
        assert "playbook" in parsed

    @patch("agentlens.cli_tool_reliability.get_client_only")
    def test_table_output(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        sessions_resp = MagicMock()
        sessions_resp.json.return_value = {"sessions": [{"id": "s1"}, {"id": "s2"}]}
        sessions_resp.raise_for_status = MagicMock()

        events_resp1 = MagicMock()
        events_resp1.json.return_value = {
            "events": _make_tool_events("api_call", 15, error_rate=0.05)
        }
        events_resp1.raise_for_status = MagicMock()

        events_resp2 = MagicMock()
        events_resp2.json.return_value = {
            "events": _make_tool_events("db_query", 10, error_rate=0.0)
        }
        events_resp2.raise_for_status = MagicMock()

        mock_client.get.side_effect = [sessions_resp, events_resp1, events_resp2]

        args = _mock_args(format="table")
        captured = StringIO()
        with patch("sys.stdout", captured):
            cmd_tool_reliability(args)

        output = captured.getvalue()
        assert "Tool Reliability Scorecard" in output

    @patch("agentlens.cli_tool_reliability.get_client_only")
    def test_no_events_exits_gracefully(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        sessions_resp = MagicMock()
        sessions_resp.json.return_value = {"sessions": [{"id": "s1"}]}
        sessions_resp.raise_for_status = MagicMock()

        events_resp = MagicMock()
        events_resp.json.return_value = {"events": []}
        events_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = [sessions_resp, events_resp]

        args = _mock_args()
        with pytest.raises(SystemExit) as exc_info:
            cmd_tool_reliability(args)
        assert exc_info.value.code == 0

    @patch("agentlens.cli_tool_reliability.get_client_only")
    def test_write_to_file(self, mock_get_client, tmp_path):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        sessions_resp = MagicMock()
        sessions_resp.json.return_value = {"sessions": [{"id": "s1"}]}
        sessions_resp.raise_for_status = MagicMock()

        events_resp = MagicMock()
        events_resp.json.return_value = {
            "events": _make_tool_events("tool_x", 25, error_rate=0.08)
        }
        events_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = [sessions_resp, events_resp]

        out_file = str(tmp_path / "report.json")
        args = _mock_args(format="json", output=out_file)
        cmd_tool_reliability(args)

        with open(out_file) as f:
            data = json.load(f)
        assert "portfolio" in data


# ── Parser registration test ─────────────────────────────────────────────────


class TestRegister:
    def test_registers_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        register(sub)
        # Should not raise
        parsed = parser.parse_args(["tool-reliability", "--appetite", "cautious", "--limit", "50"])
        assert parsed.appetite == "cautious"
        assert parsed.limit == 50
