"""Tests for ``agentlens.cli_audit``.

Covers the formatting helpers (pure functions over dicts) plus the
``cmd_audit`` dispatcher with ``urllib`` mocked out.  No tests for
internal CSV row mapping are skipped — every code path that produces
user-visible output is exercised.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from agentlens import cli_audit
from agentlens.cli_audit import (
    _export_csv,
    _format_detail,
    _format_table,
    _severity_badge,
    _summary_stats,
    _truncate,
    _ts,
    cmd_audit,
    register_audit_parser,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def sample_entry() -> dict:
    return {
        "id": "audit-1",
        "timestamp": "2026-05-17T10:00:00Z",
        "severity": "warning",
        "agent_name": "ResearcherAgent",
        "agent_id": "agent-abc",
        "session_id": "sess-42",
        "action": "llm_call",
        "model": "gpt-4o",
        "total_tokens": 1234,
        "cost_usd": 0.0567,
        "detail": "Routine completion",
        "metadata": {"prompt_id": "p-7", "retries": 0},
    }


@pytest.fixture
def sample_entries(sample_entry) -> list[dict]:
    other = {
        "id": "audit-2",
        "timestamp": "2026-05-17T11:30:00Z",
        "severity": "critical",
        "agent_name": "PlannerAgent",
        "session_id": "sess-99",
        "action": "tool_use",
        "model": "claude-3",
        "total_tokens": 256,
        "cost_usd": 0.001,
        "message": "Tool failed to respond",
    }
    third = {
        "id": "audit-3",
        "timestamp": "2026-05-17T09:00:00Z",
        "severity": "info",
        "agent_id": "agent-xyz",  # no agent_name → falls back to agent_id
        "action": "llm_call",
        "model": "gpt-4o",
        "total_tokens": 42,
    }
    return [sample_entry, other, third]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestTimestampHelper:
    def test_ts_valid_iso(self):
        # parse_iso renders to '%Y-%m-%d %H:%M:%S'
        out = _ts("2026-05-17T10:00:00Z")
        assert "2026" in out and "10:00:00" in out

    def test_ts_blank_string(self):
        assert _ts("") == "?"

    def test_ts_malformed_returns_original(self):
        # parse_iso returns None → falls through to ``iso or "?"``
        bad = "not-a-real-timestamp"
        assert _ts(bad) == bad


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_empty_returns_empty(self):
        assert _truncate("") == ""

    def test_newlines_collapsed_and_stripped(self):
        assert _truncate("  a\nb  ") == "a b"

    def test_long_string_is_capped_with_ellipsis(self):
        s = "x" * 80
        out = _truncate(s, max_len=20)
        assert len(out) == 20
        assert out.endswith("...")
        assert out.startswith("x" * 17)


class TestSeverityBadge:
    def test_no_color_returns_plain_brackets(self):
        assert _severity_badge("critical", no_color=True) == "[CRITICAL]"

    def test_color_wraps_with_ansi(self):
        out = _severity_badge("warning", no_color=False)
        assert out.startswith("\033[")
        assert out.endswith("\033[0m")
        assert "[WARNING]" in out

    def test_unknown_severity_still_renders_no_color(self):
        # Unknown severity → no color prefix in lookup → just reset wrapper
        out = _severity_badge("trivia", no_color=False)
        assert "[TRIVIA]" in out
        assert out.endswith("\033[0m")


# --------------------------------------------------------------------------- #
# Table & detail rendering
# --------------------------------------------------------------------------- #


class TestFormatTable:
    def test_empty_entries_returns_placeholder(self):
        assert _format_table([]) == "  No audit entries found."

    def test_entries_render_each_row(self, sample_entries):
        out = _format_table(sample_entries, no_color=True)
        assert "Timestamp" in out  # header
        assert "ResearcherAgent" in out
        assert "PlannerAgent" in out
        # third entry has no agent_name, so agent_id is used (truncated to 15)
        assert "agent-xyz" in out
        # Action column present
        assert "llm_call" in out and "tool_use" in out

    def test_no_color_table_omits_ansi(self, sample_entries):
        out = _format_table(sample_entries, no_color=True)
        assert "\033[" not in out

    def test_colored_table_includes_ansi(self, sample_entries):
        out = _format_table(sample_entries, no_color=False)
        assert "\033[" in out


class TestFormatDetail:
    def test_renders_all_fields(self, sample_entry):
        out = _format_detail(sample_entry, no_color=True)
        assert "audit-1" in out
        assert "Severity:" in out and "[WARNING]" in out
        assert "ResearcherAgent" in out
        assert "sess-42" in out
        assert "llm_call" in out
        assert "gpt-4o" in out
        assert "1234" in out
        assert "$0.0567" in out
        assert "Routine completion" in out
        # metadata JSON dumped inline
        assert "prompt_id" in out
        assert "retries" in out

    def test_falls_back_to_message_when_no_detail(self):
        e = {
            "id": "a",
            "timestamp": "",
            "severity": "info",
            "agent_id": "agent-x",
            "action": "x",
            "message": "fallback-msg",
            "cost_usd": 0,
        }
        out = _format_detail(e, no_color=True)
        assert "fallback-msg" in out

    def test_uses_agent_id_when_no_agent_name(self):
        e = {"id": "a", "timestamp": "", "severity": "info",
             "agent_id": "fallback-id", "action": "x", "cost_usd": 0}
        out = _format_detail(e, no_color=True)
        assert "fallback-id" in out


# --------------------------------------------------------------------------- #
# Summary stats
# --------------------------------------------------------------------------- #


class TestSummaryStats:
    def test_empty_returns_empty_string(self):
        assert _summary_stats([]) == ""

    def test_counts_total_severity_and_actions(self, sample_entries):
        out = _summary_stats(sample_entries)
        assert "Total entries: 3" in out
        # severity breakdown shows each present level
        assert "warning" in out
        assert "critical" in out
        assert "info" in out
        # top actions: llm_call appears twice, tool_use once
        assert "llm_call: 2" in out
        assert "tool_use: 1" in out

    def test_total_cost_only_shown_when_positive(self):
        entries = [{"cost_usd": 0, "severity": "info", "action": "x",
                    "agent_id": "a", "timestamp": "2026-05-17T00:00:00Z"}]
        out = _summary_stats(entries)
        assert "Total cost" not in out

    def test_total_cost_shown_when_positive(self):
        entries = [{"cost_usd": 1.25, "severity": "info", "action": "x",
                    "agent_id": "a", "timestamp": "2026-05-17T00:00:00Z"}]
        out = _summary_stats(entries)
        assert "Total cost: $1.2500" in out

    def test_top_agents_uses_agent_id_fallback(self):
        entries = [
            {"agent_id": "only-id", "severity": "info", "action": "x",
             "timestamp": "2026-05-17T00:00:00Z", "cost_usd": 0},
            {"agent_id": "only-id", "severity": "info", "action": "x",
             "timestamp": "2026-05-17T01:00:00Z", "cost_usd": 0},
        ]
        out = _summary_stats(entries)
        assert "only-id: 2" in out


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #


class TestExportCsv:
    def test_csv_to_stdout(self, sample_entries, capsys):
        _export_csv(sample_entries, None)
        out = capsys.readouterr().out
        # header
        assert "timestamp" in out and "severity" in out and "cost_usd" in out
        # rows
        assert "ResearcherAgent" in out
        assert "tool_use" in out

    def test_csv_uses_agent_id_when_no_agent_name(self, capsys):
        entry = {
            "timestamp": "2026-05-17T00:00:00Z",
            "severity": "info",
            "agent_id": "fallback-id",
            "action": "llm_call",
            "cost_usd": 0,
        }
        _export_csv([entry], None)
        out = capsys.readouterr().out
        # No agent_name column value, so agent_id should be filled in
        # Look for it on a comma-separated row, not the header.
        rows = [r for r in out.splitlines() if "fallback-id" in r]
        assert rows, "expected fallback agent_id to appear in csv body"

    def test_csv_uses_message_when_no_detail(self, capsys):
        entry = {
            "timestamp": "2026-05-17T00:00:00Z",
            "severity": "info",
            "agent_id": "a",
            "action": "x",
            "message": "msg-fallback",
            "cost_usd": 0,
        }
        _export_csv([entry], None)
        out = capsys.readouterr().out
        assert "msg-fallback" in out

    def test_csv_writes_file(self, sample_entries, tmp_path, capsys):
        out_path = tmp_path / "audit.csv"
        _export_csv(sample_entries, str(out_path))
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "ResearcherAgent" in text
        printed = capsys.readouterr().out
        assert "Exported 3 entries" in printed


# --------------------------------------------------------------------------- #
# cmd_audit with urllib mocked
# --------------------------------------------------------------------------- #


def _make_response(body: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    # support context-manager protocol used by urlopen
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, exc_type, exc, tb: False
    return resp


def _base_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        endpoint=None,
        api_key="k",
        agent=None,
        action_filter=None,
        severity=None,
        model=None,
        session=None,
        since=24,
        limit=50,
        format="table",
        output=None,
        stats=False,
        no_color=True,
        json_output=False,
        entry_id=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdAudit:
    def test_table_output_uses_filters_and_renders_entries(
        self, sample_entries, capsys, monkeypatch
    ):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            return _make_response({"entries": sample_entries})

        monkeypatch.delenv("AGENTLENS_ENDPOINT", raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        args = _base_args(agent="Researcher", severity="warning", since=12,
                          stats=True)
        cmd_audit(args)
        out = capsys.readouterr().out

        # URL was built against the default endpoint and includes the filters
        assert captured["url"].startswith("http://localhost:3000/audit?")
        assert "agent=Researcher" in captured["url"]
        assert "severity=warning" in captured["url"]
        assert "since_hours=12" in captured["url"]
        # api-key forwarded via headers (urllib normalizes header names)
        header_keys_lower = {k.lower() for k in captured["headers"]}
        assert "x-api-key" in header_keys_lower
        # Filters appear in the title banner
        assert "agent=Researcher" in out
        # Table rendered
        assert "ResearcherAgent" in out
        # Stats block printed
        assert "Audit Summary" in out

    def test_url_encodes_special_characters_in_filter(
        self, capsys, monkeypatch
    ):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _make_response({"entries": []})

        monkeypatch.delenv("AGENTLENS_ENDPOINT", raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        args = _base_args(agent="hello world&evil=1")
        cmd_audit(args)
        # urlencode must escape '&' so it can't break out of the query value
        assert "evil=1" not in captured["url"].split("agent=", 1)[1].split(
            "&", 1
        )[0]
        assert "hello+world" in captured["url"] or "hello%20world" in captured[
            "url"
        ]

    def test_json_output_path(self, sample_entries, capsys, monkeypatch):
        body = {"entries": sample_entries}
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: _make_response(body),
        )

        args = _base_args(json_output=True)
        cmd_audit(args)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == body

    def test_csv_format_branch(self, sample_entries, capsys, monkeypatch):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: _make_response({"entries": sample_entries}),
        )

        args = _base_args(format="csv")
        cmd_audit(args)
        out = capsys.readouterr().out
        assert out.startswith("timestamp,severity,")
        assert "ResearcherAgent" in out

    def test_detail_view_with_entry_id(self, sample_entry, capsys, monkeypatch):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: _make_response(
                {"entries": [sample_entry]}
            ),
        )

        args = _base_args(entry_id="audit-1")
        cmd_audit(args)
        out = capsys.readouterr().out
        assert "Audit Entry" in out
        assert "audit-1" in out
        assert "ResearcherAgent" in out

    def test_detail_view_missing_entry_prints_not_found(
        self, capsys, monkeypatch
    ):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: _make_response({"entries": []}),
        )

        args = _base_args(entry_id="missing")
        cmd_audit(args)
        out = capsys.readouterr().out
        assert "Entry not found." in out

    def test_table_output_saves_to_file(
        self, sample_entries, tmp_path, capsys, monkeypatch
    ):
        body = {"entries": sample_entries}
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: _make_response(body),
        )

        out_path = tmp_path / "audit.json"
        args = _base_args(output=str(out_path))
        cmd_audit(args)
        assert out_path.exists()
        on_disk = json.loads(out_path.read_text(encoding="utf-8"))
        assert on_disk == body
        printed = capsys.readouterr().out
        assert f"Saved to {out_path}" in printed

    def test_uses_env_endpoint_when_no_flag(
        self, sample_entries, capsys, monkeypatch
    ):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _make_response({"entries": sample_entries})

        monkeypatch.setenv("AGENTLENS_ENDPOINT", "https://audit.example.com/")
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        cmd_audit(_base_args())
        # Trailing slash stripped, /audit appended
        assert captured["url"].startswith("https://audit.example.com/audit")


# --------------------------------------------------------------------------- #
# Argparse registration
# --------------------------------------------------------------------------- #


class TestRegisterParser:
    def test_register_audit_parser_round_trip(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register_audit_parser(sub)

        ns = parser.parse_args([
            "audit",
            "--agent", "researcher",
            "--severity", "warning",
            "--action", "llm_call",
            "--model", "gpt-4o",
            "--session", "sess-1",
            "--since", "6",
            "--limit", "10",
            "--format", "csv",
            "--stats",
            "--no-color",
            "--json",
            "--endpoint", "http://x",
            "--api-key", "y",
        ])

        assert ns.cmd == "audit"
        assert ns.agent == "researcher"
        assert ns.severity == "warning"
        assert ns.action_filter == "llm_call"
        assert ns.model == "gpt-4o"
        assert ns.session == "sess-1"
        assert ns.since == 6
        assert ns.limit == 10
        assert ns.format == "csv"
        assert ns.stats is True
        assert ns.no_color is True
        assert ns.json_output is True
        assert ns.endpoint == "http://x"
        assert ns.api_key == "y"
        assert ns.func is cmd_audit

    def test_register_audit_parser_defaults(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register_audit_parser(sub)

        ns = parser.parse_args(["audit"])
        assert ns.since == 24
        assert ns.limit == 50
        assert ns.format == "table"
        assert ns.stats is False
        assert ns.no_color is False
        assert ns.json_output is False
        assert ns.entry_id is None
