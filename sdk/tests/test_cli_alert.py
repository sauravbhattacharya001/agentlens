"""Tests for ``agentlens.cli_alert``.

Covers the pure formatting helpers and every sub-command dispatcher
(history, rules, test, ack, silence, unsilence, stats) in table/json
modes using a mocked ``httpx.Client``. Prior to this module, ``cli_alert``
sat at ~18% line coverage despite exposing seven user-facing CLI verbs.
"""

from __future__ import annotations

import argparse
import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from agentlens import cli_alert
from agentlens.cli_alert import (
    _colorize,
    _print_table,
    _severity_icon,
    _SEV_COLORS,
    cmd_alert,
    register_alert_parser,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ns(**kw) -> argparse.Namespace:
    """Build an argparse Namespace with sensible defaults for cli_alert."""
    defaults = {
        "endpoint": "http://localhost:3000",
        "api_key": "test-key",
        "alert_sub": None,
        "format": "table",
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _resp(payload, *, status: int = 200) -> MagicMock:
    """Build a MagicMock that behaves like an ``httpx.Response``."""
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = status
    r.raise_for_status = MagicMock()
    return r


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestSeverityHelpers:
    @pytest.mark.parametrize(
        "sev,expected",
        [
            ("info", "ℹ️ "),
            ("warning", "⚠️ "),
            ("critical", "🔴"),
            ("unknown", "  "),
            ("", "  "),
        ],
    )
    def test_severity_icon(self, sev, expected):
        assert _severity_icon(sev) == expected

    def test_colorize_known_severity_wraps_with_ansi(self):
        out = _colorize("HELLO", "critical")
        assert _SEV_COLORS["critical"] in out
        assert "HELLO" in out
        assert out.endswith("\033[0m")

    def test_colorize_unknown_severity_returns_raw_string(self):
        # Unknown severity → no ANSI wrapping at all (not even reset).
        assert _colorize("HELLO", "bogus") == "HELLO"

    def test_colorize_handles_all_known_severities(self):
        for sev in ("info", "warning", "critical"):
            assert _SEV_COLORS[sev] in _colorize("x", sev)


class TestPrintTable:
    def test_empty_rows_prints_no_results_placeholder(self, capsys):
        _print_table([], ["a", "b"])
        out = capsys.readouterr().out
        assert "(no results)" in out

    def test_renders_header_separator_and_rows(self, capsys):
        rows = [{"a": "1", "b": "two"}, {"a": "3", "b": "four"}]
        _print_table(rows, ["a", "b"])
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        # header + separator + 2 rows
        assert len(lines) == 4
        assert "a" in lines[0] and "b" in lines[0]
        assert set(lines[1]) <= {"-", "+", " "}
        assert "1" in lines[2] and "two" in lines[2]
        assert "3" in lines[3] and "four" in lines[3]

    def test_long_values_truncate_with_ellipsis(self, capsys):
        long_val = "x" * 100
        _print_table([{"col": long_val}], ["col"], max_width=10)
        out = capsys.readouterr().out
        # The truncated representation must end with the ellipsis char
        # and be no wider than max_width.
        assert "…" in out
        for line in out.splitlines():
            if line.strip() and "col" not in line and "-" not in line:
                assert len(line.rstrip()) <= 10

    def test_missing_columns_default_to_empty(self, capsys):
        _print_table([{"a": "x"}], ["a", "b"])
        out = capsys.readouterr().out
        # 'b' has no value but the column header is still rendered.
        assert "b" in out
        assert "x" in out


# --------------------------------------------------------------------------- #
# _cmd_history
# --------------------------------------------------------------------------- #


class TestCmdHistory:
    @pytest.fixture
    def alerts_payload(self):
        return [
            {
                "id": "alert-aaaaaaaaaaaaaaaa",
                "severity": "critical",
                "rule_id": "high-cost",
                "message": "Cost spike detected",
                "acknowledged": False,
                "created_at": "2026-05-23T10:00:00Z",
            },
            {
                "id": "alert-bbbbbbbbbbbbbbbb",
                "severity": "warning",
                "rule_name": "slow-latency",
                "message": "P99 above SLO",
                "acknowledged": True,
                "created_at": "2026-05-23T09:30:00Z",
            },
        ]

    def _mock_client(self, payload):
        client = MagicMock()
        client.get.return_value = _resp(payload)
        return client

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_table_default(self, mock_get_client, alerts_payload, capsys):
        mock_get_client.return_value = self._mock_client(alerts_payload)
        args = _ns(alert_sub="history", severity=None, since=None, limit=None,
                   ack=False, unack=False)
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "CRITICAL" in out
        assert "WARNING" in out
        assert "Cost spike detected" in out
        assert "Total: 2 alert(s)" in out
        # Acked row shows checkmark.
        assert "✓" in out

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_json_emits_raw_array(self, mock_get_client, alerts_payload, capsys):
        mock_get_client.return_value = self._mock_client(alerts_payload)
        args = _ns(alert_sub="history", severity=None, since=None, limit=None,
                   ack=False, unack=False, format="json")
        cmd_alert(args)
        out = capsys.readouterr().out
        # JSON output must be parseable and contain the IDs.
        import json
        parsed = json.loads(out)
        assert len(parsed) == 2
        assert {p["severity"] for p in parsed} == {"critical", "warning"}

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_empty_response_prints_message(self, mock_get_client, capsys):
        mock_get_client.return_value = self._mock_client([])
        args = _ns(alert_sub="history", severity=None, since=None, limit=None,
                   ack=False, unack=False)
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "No alerts found" in out

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_filters_translated_to_query_params(
        self, mock_get_client, alerts_payload
    ):
        client = self._mock_client(alerts_payload)
        mock_get_client.return_value = client
        args = _ns(
            alert_sub="history",
            severity="critical",
            since=24.0,
            limit=10,
            ack=True,
            unack=False,
        )
        cmd_alert(args)
        client.get.assert_called_once()
        call_kwargs = client.get.call_args.kwargs
        params = call_kwargs["params"]
        assert params["severity"] == "critical"
        assert params["limit"] == 10
        assert params["acknowledged"] == "true"
        # since converted to an ISO timestamp
        assert "since" in params and "T" in params["since"]

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_unack_filter(self, mock_get_client, alerts_payload):
        client = self._mock_client(alerts_payload)
        mock_get_client.return_value = client
        args = _ns(alert_sub="history", severity=None, since=None, limit=None,
                   ack=False, unack=True)
        cmd_alert(args)
        params = client.get.call_args.kwargs["params"]
        assert params["acknowledged"] == "false"

    @patch("agentlens.cli_alert.get_client_only")
    def test_history_handles_dict_response_with_alerts_key(self, mock_get_client, capsys):
        mock_get_client.return_value = self._mock_client(
            {"alerts": [{"id": "x", "severity": "info", "message": "hi"}]}
        )
        args = _ns(alert_sub="history", severity=None, since=None, limit=None,
                   ack=False, unack=False)
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "Total: 1 alert" in out


# --------------------------------------------------------------------------- #
# _cmd_rules
# --------------------------------------------------------------------------- #


class TestCmdRules:
    @pytest.fixture
    def rules_payload(self):
        return [
            {
                "id": "rule-high-cost",
                "metric": "session_cost",
                "condition": ">",
                "threshold": 1.0,
                "severity": "critical",
            },
            {
                "id": "rule-noisy",
                "metric": "error_rate",
                "condition": ">",
                "threshold": 0.05,
                "severity": "warning",
                "silenced_until": "2026-12-31T00:00:00Z",
            },
        ]

    @patch("agentlens.cli_alert.get_client_only")
    def test_rules_table(self, mock_get_client, rules_payload, capsys):
        client = MagicMock()
        client.get.return_value = _resp(rules_payload)
        mock_get_client.return_value = client
        args = _ns(alert_sub="rules")
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "session_cost" in out
        assert "silenced" in out
        assert "active" in out
        assert "Total: 2 rule(s)" in out

    @patch("agentlens.cli_alert.get_client_only")
    def test_rules_json(self, mock_get_client, rules_payload, capsys):
        client = MagicMock()
        client.get.return_value = _resp(rules_payload)
        mock_get_client.return_value = client
        args = _ns(alert_sub="rules", format="json")
        cmd_alert(args)
        import json
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed) == 2

    @patch("agentlens.cli_alert.get_client_only")
    def test_rules_empty(self, mock_get_client, capsys):
        client = MagicMock()
        client.get.return_value = _resp([])
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="rules"))
        assert "No alert rules" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# _cmd_test / _cmd_ack / _cmd_silence / _cmd_unsilence
# --------------------------------------------------------------------------- #


class TestCmdTest:
    @patch("agentlens.cli_alert.get_client_only")
    def test_would_fire_path(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({
            "would_fire": True,
            "metric_value": 2.5,
            "threshold": 1.0,
            "details": "exceeded by 1.5",
        })
        mock_get_client.return_value = client
        args = _ns(alert_sub="test", rule_id="r1", session_id="s1")
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "WOULD FIRE" in out
        assert "2.5" in out
        assert "exceeded by 1.5" in out
        # Body must include session id under POST.
        post_kwargs = client.post.call_args.kwargs
        assert post_kwargs["json"] == {"session_id": "s1"}

    @patch("agentlens.cli_alert.get_client_only")
    def test_would_not_fire_path(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({"triggered": False})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="test", rule_id="r1", session_id="s1"))
        out = capsys.readouterr().out
        assert "Would NOT fire" in out


class TestCmdAck:
    @patch("agentlens.cli_alert.get_client_only")
    def test_ack_without_note(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({"ok": True})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="ack", alert_id="A1", note=None))
        out = capsys.readouterr().out
        assert "acknowledged" in out
        # No note must mean empty body.
        assert client.post.call_args.kwargs["json"] == {}

    @patch("agentlens.cli_alert.get_client_only")
    def test_ack_with_note(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({"ok": True})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="ack", alert_id="A2", note="false positive"))
        out = capsys.readouterr().out
        assert "false positive" in out
        assert client.post.call_args.kwargs["json"] == {"note": "false positive"}


class TestCmdSilence:
    @patch("agentlens.cli_alert.get_client_only")
    def test_silence_uses_explicit_duration(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({"ok": True})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="silence", rule_id="r1", duration=15))
        out = capsys.readouterr().out
        assert "silenced for 15 minutes" in out
        assert client.post.call_args.kwargs["json"] == {"duration_minutes": 15}

    @patch("agentlens.cli_alert.get_client_only")
    def test_silence_defaults_to_60_min_when_falsy(self, mock_get_client, capsys):
        client = MagicMock()
        client.post.return_value = _resp({"ok": True})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="silence", rule_id="r1", duration=0))
        assert client.post.call_args.kwargs["json"] == {"duration_minutes": 60}


class TestCmdUnsilence:
    @patch("agentlens.cli_alert.get_client_only")
    def test_unsilence_calls_delete(self, mock_get_client, capsys):
        client = MagicMock()
        client.delete.return_value = _resp({"ok": True})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="unsilence", rule_id="r1"))
        client.delete.assert_called_once_with("/alert-rules/r1/silence")
        assert "unsilenced" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# _cmd_stats
# --------------------------------------------------------------------------- #


class TestCmdStats:
    @pytest.fixture
    def stats_payload(self):
        return {
            "total": 100,
            "acknowledged": 60,
            "by_severity": {"critical": 5, "warning": 25, "info": 70},
            "by_rule": {"r1": 40, "r2": 35, "r3": 25},
            "mean_time_to_ack_minutes": 12.345,
        }

    @patch("agentlens.cli_alert.get_client_only")
    def test_stats_table_renders_all_sections(self, mock_get_client, stats_payload, capsys):
        client = MagicMock()
        client.get.return_value = _resp(stats_payload)
        mock_get_client.return_value = client
        args = _ns(alert_sub="stats", period="week")
        cmd_alert(args)
        out = capsys.readouterr().out
        assert "Alert Statistics" in out
        assert "Total alerts:" in out and "100" in out
        assert "Acknowledged:" in out and "60" in out
        assert "Unacknowledged:" in out and "40" in out
        assert "By severity:" in out
        assert "critical: 5" in out
        assert "Top rules:" in out
        assert "r1: 40" in out
        assert "12.3 min" in out
        # Period propagates as a query param.
        assert client.get.call_args.kwargs["params"]["period"] == "week"

    @patch("agentlens.cli_alert.get_client_only")
    def test_stats_json(self, mock_get_client, stats_payload, capsys):
        client = MagicMock()
        client.get.return_value = _resp(stats_payload)
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="stats", period=None, format="json"))
        import json
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["total"] == 100

    @patch("agentlens.cli_alert.get_client_only")
    def test_stats_handles_minimal_payload(self, mock_get_client, capsys):
        client = MagicMock()
        client.get.return_value = _resp({"total": 0, "acknowledged": 0})
        mock_get_client.return_value = client
        cmd_alert(_ns(alert_sub="stats", period=None))
        out = capsys.readouterr().out
        # No mean-time, no by_severity, no by_rule — these sections must
        # be silently omitted, not crash.
        assert "Mean time to ack" not in out
        assert "By severity:" not in out
        assert "Top rules:" not in out


# --------------------------------------------------------------------------- #
# Dispatcher / parser
# --------------------------------------------------------------------------- #


class TestDispatcher:
    @patch("agentlens.cli_alert.get_client_only")
    def test_unknown_sub_exits_with_usage(self, mock_get_client, capsys):
        mock_get_client.return_value = MagicMock()
        with pytest.raises(SystemExit) as exc:
            cmd_alert(_ns(alert_sub="nope"))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "Usage:" in out

    @patch("agentlens.cli_alert.get_client_only")
    def test_missing_sub_exits_with_usage(self, mock_get_client, capsys):
        mock_get_client.return_value = MagicMock()
        with pytest.raises(SystemExit):
            cmd_alert(_ns(alert_sub=None))


class TestRegisterParser:
    def test_register_alert_parser_wires_all_subcommands(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        register_alert_parser(subs)
        # Each sub-verb should round-trip through argparse cleanly.
        verbs = [
            ("history", []),
            ("rules", []),
            ("test", ["rule1", "sess1"]),
            ("ack", ["alert1"]),
            ("silence", ["rule1"]),
            ("unsilence", ["rule1"]),
            ("stats", []),
        ]
        for verb, extra in verbs:
            ns = parser.parse_args(["alert", verb, *extra])
            assert ns.cmd == "alert"
            assert ns.alert_sub == verb

    def test_history_parser_accepts_filters(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        register_alert_parser(subs)
        ns = parser.parse_args([
            "alert", "history",
            "--severity", "warning",
            "--since", "12",
            "--limit", "5",
            "--ack",
            "--format", "json",
        ])
        assert ns.severity == "warning"
        assert ns.since == 12.0
        assert ns.limit == 5
        assert ns.ack is True
        assert ns.format == "json"

    def test_severity_choices_are_constrained(self):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        register_alert_parser(subs)
        with pytest.raises(SystemExit):
            parser.parse_args(["alert", "history", "--severity", "bogus"])
