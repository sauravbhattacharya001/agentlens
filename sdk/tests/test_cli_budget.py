"""Tests for CLI budget command."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_budget import cmd_budget, _bar, _status_icon


class TestHelpers:
    def test_status_icon_ok(self):
        assert _status_icon("ok") == "✅"

    def test_status_icon_warning(self):
        assert _status_icon("warning") == "⚠️"

    def test_status_icon_exceeded(self):
        assert _status_icon("exceeded") == "🚨"

    def test_bar_low(self):
        result = _bar(20)
        assert "█" in result
        assert "░" in result

    def test_bar_full(self):
        result = _bar(100)
        # Should be red (ANSI 31)
        assert "\033[31m" in result

    def test_bar_warning(self):
        result = _bar(85)
        assert "\033[33m" in result


class TestBudgetList:
    def test_list_empty(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"budgets": []}
        client.get.return_value = resp

        args = argparse.Namespace(budget_action="list", json=False)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "No budgets" in output

    def test_list_with_budgets(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"budgets": [
            {"scope": "global", "period": "daily", "limit_usd": 10.0,
             "current_spend": 3.5, "usage_pct": 35.0, "remaining": 6.5,
             "status": "ok", "model_breakdown": {}},
        ]}
        client.get.return_value = resp

        args = argparse.Namespace(budget_action="list", json=False)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "DAILY" in output
        assert "$3.5" in output

    def test_list_json(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        budgets = [{"scope": "global", "period": "daily", "limit_usd": 10}]
        resp.json.return_value = {"budgets": budgets}
        client.get.return_value = resp

        args = argparse.Namespace(budget_action="list", json=True)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert len(parsed) == 1


class TestBudgetSet:
    def test_set_budget(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"budget": {
            "scope": "global", "period": "daily", "limit_usd": 5.0,
            "current_spend": 1.0, "usage_pct": 20.0, "budget_status": "ok",
        }}
        client.put.return_value = resp

        args = argparse.Namespace(
            budget_action="set", scope="global", period="daily",
            limit_usd=5.0, warn_pct=80,
        )
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "Budget set" in output
        assert "$5.0" in output


class TestBudgetCheck:
    def test_check_ok(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {
            "session_id": "sess-1", "agent_name": "test-agent",
            "budgets": [{"scope": "global", "period": "daily",
                         "limit_usd": 10, "current_spend": 2,
                         "usage_pct": 20, "status": "ok"}],
            "any_exceeded": False, "any_warning": False,
        }
        client.get.return_value = resp

        args = argparse.Namespace(budget_action="check", session_id="sess-1", json=False)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "All budgets OK" in output

    def test_check_exceeded(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {
            "session_id": "sess-1", "agent_name": "test-agent",
            "budgets": [{"scope": "global", "period": "daily",
                         "limit_usd": 1, "current_spend": 2,
                         "usage_pct": 200, "status": "exceeded"}],
            "any_exceeded": True, "any_warning": True,
        }
        client.get.return_value = resp

        args = argparse.Namespace(budget_action="check", session_id="sess-1", json=False)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "EXCEEDED" in output


class TestBudgetDelete:
    def test_delete_with_period(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"status": "ok"}
        client.delete.return_value = resp

        args = argparse.Namespace(budget_action="delete", scope="global", period="daily")
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "Deleted budget" in output

    def test_delete_scope(self, capsys):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"status": "ok", "deleted": 3}
        client.delete.return_value = resp

        args = argparse.Namespace(budget_action="delete", scope="global", period=None)
        cmd_budget(args, client)

        output = capsys.readouterr().out
        assert "3 budget(s)" in output
