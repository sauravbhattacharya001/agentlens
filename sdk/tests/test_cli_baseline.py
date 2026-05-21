"""Tests for ``agentlens.cli_baseline`` — baseline management CLI.

Covers:
    - Formatting helpers: _fmt_num, _print_baseline_table (empty + populated)
    - Detail / check renderers: _print_baseline_detail, _print_check_result
    - Subcommand handlers _cmd_list / _cmd_show / _cmd_record / _cmd_check /
      _cmd_delete: happy path, JSON output, 404 path (SystemExit), and 400.
    - cmd_baseline dispatch: missing action errors, every action routes to
      the right handler.
    - register_baseline_parser: every subcommand is wired and accepts the
      documented arguments.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from agentlens import cli_baseline


# ---------------------------------------------------------------------------
# _fmt_num
# ---------------------------------------------------------------------------

class TestFmtNum:
    def test_none_renders_dash(self):
        assert cli_baseline._fmt_num(None) == "—"

    def test_small_float_one_decimal(self):
        assert cli_baseline._fmt_num(12.345) == "12.3"

    def test_large_value_uses_thousands_separator(self):
        assert cli_baseline._fmt_num(1_234_567) == "1,234,567"

    def test_negative_large(self):
        assert cli_baseline._fmt_num(-9999.0) == "-9,999"

    def test_zero(self):
        assert cli_baseline._fmt_num(0.0) == "0.0"


# ---------------------------------------------------------------------------
# Table / detail / check renderers (smoke + content checks)
# ---------------------------------------------------------------------------

class TestPrintBaselineTable:
    def test_empty_message(self, capsys):
        cli_baseline._print_baseline_table([])
        assert "No baselines recorded yet." in capsys.readouterr().out

    def test_populated_includes_header_and_rows(self, capsys):
        rows = [
            {"agent_name": "alpha", "samples": 5,
             "avg_total_tokens": 1234.0, "avg_event_count": 10.0,
             "avg_error_count": 0.0, "avg_processing_ms": 500.0,
             "updated_at": "2026-05-20T12:00:00Z"},
            {"agent_name": "beta", "samples": 12,
             "avg_total_tokens": 99.0, "avg_event_count": 7.5,
             "avg_error_count": 1.5, "avg_processing_ms": 2400.0,
             "updated_at": "2026-05-19T09:00:00Z"},
        ]
        cli_baseline._print_baseline_table(rows)
        out = capsys.readouterr().out
        assert "alpha" in out and "beta" in out
        # Header label appears
        assert "Avg Tokens" in out
        # Date truncated to YYYY-MM-DD
        assert "2026-05-20" in out
        assert "(2 baseline(s))" in out


class TestPrintBaselineDetail:
    def test_includes_all_metric_labels(self, capsys):
        data = {
            "agent_name": "alpha",
            "samples": 10,
            "updated_at": "2026-05-20T12:00:00Z",
            "avg_tokens_in": 100.0,
            "avg_tokens_out": 200.0,
            "avg_total_tokens": 300.0,
            "avg_event_count": 5.0,
            "avg_error_count": 0.0,
            "avg_processing_ms": 1500.0,
            "avg_duration_ms": 2000.0,
            "p95_total_tokens": 400.0,
            "p95_processing_ms": 2500.0,
            "recent_session_ids": [f"s{i}" for i in range(8)],
        }
        cli_baseline._print_baseline_detail(data)
        out = capsys.readouterr().out
        for label in ("Avg Tokens In", "Avg Tokens Out", "P95 Tokens",
                      "P95 Processing (ms)"):
            assert label in out
        # Recent sessions: shows last 5 + "and N more"
        assert "Recent sessions (8)" in out
        assert "and 3 more" in out

    def test_handles_missing_recent_sessions(self, capsys):
        data = {"agent_name": "a", "samples": 1, "updated_at": "x"}
        cli_baseline._print_baseline_detail(data)
        out = capsys.readouterr().out
        assert "Recent sessions" not in out


class TestPrintCheckResult:
    def test_full_check_renders_metrics(self, capsys):
        data = {
            "agent_name": "alpha",
            "session_id": "sid123",
            "baseline_samples": 5,
            "verdict": "warning",
            "checks": {
                "total_tokens": {"baseline": 100.0, "actual": 150.0,
                                  "delta_pct": 50.0, "status": "warning"},
                "tokens_in":    {"baseline": 50.0, "actual": 60.0,
                                  "delta_pct": 20.0, "status": "normal"},
                "tokens_out":   {"baseline": 50.0, "actual": 90.0,
                                  "delta_pct": 80.0, "status": "regression"},
                "event_count":  {"baseline": 5.0, "actual": 4.0,
                                  "delta_pct": -20.0, "status": "improvement"},
                # Skipped (no check entry): error_count, processing_ms
            },
        }
        cli_baseline._print_check_result(data)
        out = capsys.readouterr().out
        # Verdict icon rendered
        assert "warning" in out
        # Each rendered metric appears
        for label in ("Total Tokens", "Tokens In", "Tokens Out", "Event Count"):
            assert label in out
        # Deltas are signed
        assert "+50.0%" in out
        assert "-20.0%" in out
        # Status icons (✅ etc.) included for at least one row
        assert "✅" in out or "⚠️" in out

    def test_unknown_verdict_falls_back_to_raw(self, capsys):
        cli_baseline._print_check_result(
            {"agent_name": "a", "session_id": "s",
             "baseline_samples": 0, "verdict": "mystery"}
        )
        out = capsys.readouterr().out
        assert "Verdict:  mystery" in out

    def test_missing_delta_renders_dash(self, capsys):
        cli_baseline._print_check_result({
            "agent_name": "a", "session_id": "s",
            "baseline_samples": 1, "verdict": "healthy",
            "checks": {
                "total_tokens": {"baseline": 1.0, "actual": 1.0,
                                  "delta_pct": None, "status": "normal"},
            },
        })
        out = capsys.readouterr().out
        assert "—" in out


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _ok(body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    return resp


def _err(body, status):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    return resp


class TestCmdList:
    def test_text_output(self, capsys):
        client = MagicMock()
        client.get.return_value = _ok(
            {"baselines": [{"agent_name": "a", "samples": 1,
                             "avg_total_tokens": 1.0, "avg_event_count": 1.0,
                             "avg_error_count": 0.0, "avg_processing_ms": 1.0,
                             "updated_at": "2026-05-20T00:00:00Z"}]}
        )
        args = argparse.Namespace(json=False)
        cli_baseline._cmd_list(client, args)
        out = capsys.readouterr().out
        assert "a" in out and "(1 baseline(s))" in out

    def test_json_output(self, capsys):
        client = MagicMock()
        body = {"baselines": []}
        client.get.return_value = _ok(body)
        args = argparse.Namespace(json=True)
        cli_baseline._cmd_list(client, args)
        out = capsys.readouterr().out
        assert json.loads(out) == body

    def test_no_json_attr_defaults_to_text(self, capsys):
        client = MagicMock()
        client.get.return_value = _ok({"baselines": []})
        # Namespace without `json` attribute exercises the getattr default.
        cli_baseline._cmd_list(client, argparse.Namespace())
        assert "No baselines recorded yet." in capsys.readouterr().out


class TestCmdShow:
    def test_404_exits_with_message(self, capsys):
        client = MagicMock()
        client.get.return_value = _err({}, 404)
        with pytest.raises(SystemExit) as exc:
            cli_baseline._cmd_show(client, argparse.Namespace(
                agent_name="ghost", json=False))
        assert exc.value.code == 1
        assert "No baseline found" in capsys.readouterr().err

    def test_text_output(self, capsys):
        client = MagicMock()
        client.get.return_value = _ok({"agent_name": "alpha", "samples": 3,
                                        "updated_at": "2026-05-20T00:00:00Z",
                                        "avg_tokens_in": 10.0})
        cli_baseline._cmd_show(client, argparse.Namespace(
            agent_name="alpha", json=False))
        out = capsys.readouterr().out
        assert "alpha" in out and "Avg Tokens In" in out

    def test_json_output(self, capsys):
        client = MagicMock()
        body = {"agent_name": "alpha"}
        client.get.return_value = _ok(body)
        cli_baseline._cmd_show(client, argparse.Namespace(
            agent_name="alpha", json=True))
        assert json.loads(capsys.readouterr().out) == body


class TestCmdRecord:
    def test_success(self, capsys):
        client = MagicMock()
        client.post.return_value = _ok({"message": "ok", "agent_name": "a", "samples": 5})
        cli_baseline._cmd_record(client, argparse.Namespace(session_id="s1"))
        out = capsys.readouterr().out
        assert "✅" in out and "Agent: a" in out

    @pytest.mark.parametrize("status", [400, 404])
    def test_client_error_exits(self, status, capsys):
        client = MagicMock()
        client.post.return_value = _err({"error": "bad session"}, status)
        with pytest.raises(SystemExit) as exc:
            cli_baseline._cmd_record(client, argparse.Namespace(session_id="s1"))
        assert exc.value.code == 1
        assert "bad session" in capsys.readouterr().err


class TestCmdCheck:
    def test_json_output(self, capsys):
        client = MagicMock()
        body = {"agent_name": "a", "session_id": "s", "baseline_samples": 1,
                "verdict": "healthy", "checks": {}}
        client.post.return_value = _ok(body)
        cli_baseline._cmd_check(client, argparse.Namespace(
            session_id="s1", json=True))
        assert json.loads(capsys.readouterr().out) == body

    def test_text_output(self, capsys):
        client = MagicMock()
        client.post.return_value = _ok({"agent_name": "a", "session_id": "s",
                                         "baseline_samples": 1,
                                         "verdict": "healthy", "checks": {}})
        cli_baseline._cmd_check(client, argparse.Namespace(
            session_id="s1", json=False))
        out = capsys.readouterr().out
        assert "Agent:" in out and "Verdict:" in out

    def test_400_exits(self, capsys):
        client = MagicMock()
        client.post.return_value = _err({"error": "missing session"}, 400)
        with pytest.raises(SystemExit):
            cli_baseline._cmd_check(client, argparse.Namespace(
                session_id="s1", json=False))
        assert "missing session" in capsys.readouterr().err


class TestCmdDelete:
    def test_success(self, capsys):
        client = MagicMock()
        client.delete.return_value = _ok({"message": "deleted"})
        cli_baseline._cmd_delete(client, argparse.Namespace(agent_name="a"))
        assert "🗑️" in capsys.readouterr().out

    def test_404_exits(self, capsys):
        client = MagicMock()
        client.delete.return_value = _err({}, 404)
        with pytest.raises(SystemExit):
            cli_baseline._cmd_delete(client, argparse.Namespace(agent_name="ghost"))
        assert "No baseline found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class TestCmdBaseline:
    @patch("agentlens.cli_baseline.get_client")
    def test_no_action_exits(self, mock_gc, capsys):
        mock_gc.return_value = (MagicMock(), "http://x")
        with pytest.raises(SystemExit) as exc:
            cli_baseline.cmd_baseline(argparse.Namespace(
                endpoint="x", api_key="k", baseline_action=None))
        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err

    @pytest.mark.parametrize("action,handler_attr", [
        ("list", "_cmd_list"),
        ("show", "_cmd_show"),
        ("record", "_cmd_record"),
        ("check", "_cmd_check"),
        ("delete", "_cmd_delete"),
    ])
    @patch("agentlens.cli_baseline.get_client")
    def test_dispatches_to_handler(self, mock_gc, action, handler_attr):
        client = MagicMock()
        mock_gc.return_value = (client, "http://x")
        args = argparse.Namespace(
            endpoint="x", api_key="k", baseline_action=action,
            json=False, agent_name="a", session_id="s",
        )
        with patch.object(cli_baseline, handler_attr) as fake:
            cli_baseline.cmd_baseline(args)
            fake.assert_called_once_with(client, args)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

class TestRegisterParser:
    def _build(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command")
        cli_baseline.register_baseline_parser(sub)
        return p

    def test_list(self):
        args = self._build().parse_args(["baseline", "list", "--json"])
        assert args.baseline_action == "list" and args.json is True

    def test_show_requires_agent(self):
        args = self._build().parse_args(["baseline", "show", "alpha"])
        assert args.agent_name == "alpha" and args.json is False

    def test_record_requires_session_id(self):
        args = self._build().parse_args(["baseline", "record", "s-1"])
        assert args.session_id == "s-1"

    def test_check_with_json(self):
        args = self._build().parse_args(["baseline", "check", "s-2", "--json"])
        assert args.session_id == "s-2" and args.json is True

    def test_delete_requires_agent(self):
        args = self._build().parse_args(["baseline", "delete", "alpha"])
        assert args.agent_name == "alpha"

    def test_unknown_subcommand_errors(self):
        with pytest.raises(SystemExit):
            self._build().parse_args(["baseline", "show"])  # missing agent
