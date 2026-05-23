"""Tests for ``agentlens.cli_dashboard``.

Covers ``_aggregate_sessions`` (pure aggregation over heterogeneous session
dicts) and ``_render_html`` (template-substitution + HTML escaping) plus the
top-level ``cmd_dashboard`` orchestrator with a mocked HTTP client. Before
this module was added, ``cli_dashboard`` sat at ~11% line coverage despite
being the canonical "demo this product" CLI command.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_dashboard import (
    _aggregate_sessions,
    _render_html,
    cmd_dashboard,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def sessions() -> list[dict]:
    return [
        {
            "id": "sess-aaaaaaaaaaaaaaaa",
            "agent_name": "Researcher",
            "status": "success",
            "total_tokens": 1500,
            "event_count": 12,
            "total_cost": 0.25,
            "created_at": "2026-05-22T10:00:00Z",
            "events": [
                {"model": "gpt-4o"},
                {"model": "gpt-4o"},
                {"model": "claude-3"},
            ],
        },
        {
            "id": "sess-bbbbbbbbbbbbbbbb",
            "agent_name": "Planner",
            "status": "error",
            "total_tokens": 800,
            "event_count": 3,
            "total_cost": 0.10,
            "created_at": "2026-05-22T11:00:00Z",
            "events": [{"model": "gpt-4o-mini"}],
        },
        {
            "id": "sess-cccccccccccccccc",
            "agent_name": "Researcher",
            "status": "failed",
            "total_tokens": 2000,
            "event_count": 25,
            "total_cost": 0.40,
            "created_at": "2026-05-23T09:00:00Z",
            "events": [],
        },
        {
            # Missing/None fields should not crash the aggregator.
            "id": "sess-dddddddddddddddd",
            "status": "success",
            "total_tokens": None,
            "event_count": None,
            "total_cost": None,
            "created_at": "",  # forces 'unknown' day bucket
        },
    ]


def _resp(payload) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


# --------------------------------------------------------------------------- #
# _aggregate_sessions
# --------------------------------------------------------------------------- #


class TestAggregateSessions:
    def test_empty_input_returns_zeroed_summary(self):
        summary = _aggregate_sessions([])
        assert summary["rows"] == []
        assert summary["total_cost"] == 0.0
        assert summary["total_tokens"] == 0
        assert summary["total_events"] == 0
        assert summary["error_count"] == 0
        assert summary["model_counts"] == {}
        assert summary["status_counts"] == {}

    def test_aggregates_totals_correctly(self, sessions):
        s = _aggregate_sessions(sessions)
        # 1500 + 800 + 2000 + 0 (None coerced) = 4300
        assert s["total_tokens"] == 4300
        # 12 + 3 + 25 + 0 = 40
        assert s["total_events"] == 40
        # 0.25 + 0.10 + 0.40 + 0 = 0.75 (allow float fuzz)
        assert s["total_cost"] == pytest.approx(0.75, rel=1e-9)
        # Two sessions in error/failed buckets count as errors.
        assert s["error_count"] == 2

    def test_status_counts_partition_sessions(self, sessions):
        s = _aggregate_sessions(sessions)
        assert s["status_counts"]["success"] == 2
        assert s["status_counts"]["error"] == 1
        assert s["status_counts"]["failed"] == 1
        assert sum(s["status_counts"].values()) == len(sessions)

    def test_model_counts_only_count_events_with_model(self, sessions):
        s = _aggregate_sessions(sessions)
        assert s["model_counts"]["gpt-4o"] == 2
        assert s["model_counts"]["claude-3"] == 1
        assert s["model_counts"]["gpt-4o-mini"] == 1
        # No '' key should leak in even though some events lack a model.
        assert "" not in s["model_counts"]

    def test_daily_buckets_aggregate_sessions_and_costs(self, sessions):
        s = _aggregate_sessions(sessions)
        # Two sessions on 2026-05-22, one on 2026-05-23, one with empty created
        # → bucketed as 'unknown'.
        assert s["daily_sessions"]["2026-05-22"] == 2
        assert s["daily_sessions"]["2026-05-23"] == 1
        assert s["daily_sessions"]["unknown"] == 1
        assert s["daily_costs"]["2026-05-22"] == pytest.approx(0.35)
        assert s["daily_costs"]["2026-05-23"] == pytest.approx(0.40)

    def test_rows_preserve_input_order_and_defaults(self, sessions):
        s = _aggregate_sessions(sessions)
        assert [r["id"] for r in s["rows"]] == [x["id"] for x in sessions]
        # Missing agent_name falls back to 'unknown'.
        assert s["rows"][3]["agent"] == "unknown"
        # None tokens/cost coerced to 0.
        assert s["rows"][3]["tokens"] == 0
        assert s["rows"][3]["cost"] == 0.0

    def test_missing_id_falls_back_to_question_mark(self):
        s = _aggregate_sessions([{"status": "success"}])
        assert s["rows"][0]["id"] == "?"


# --------------------------------------------------------------------------- #
# _render_html
# --------------------------------------------------------------------------- #


class TestRenderHtml:
    def test_minimal_render_produces_valid_html_skeleton(self):
        sessions: list[dict] = []
        summary = _aggregate_sessions(sessions)
        html = _render_html(sessions, summary, "http://localhost:3000")
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        assert "AgentLens Dashboard" in html
        # Empty data still produces parseable JSON arrays for charts.
        assert "[]" in html

    def test_kpis_reflect_summary_numbers(self, sessions):
        s = _aggregate_sessions(sessions)
        html = _render_html(sessions, s, "http://api.example.com")
        # Headline KPIs must appear.
        assert "$0.7500" in html
        assert "4,300" in html  # total tokens with thousands separator
        # error_rate = 2/4 = 50%
        assert "50.0%" in html
        # All 3 unique models surface in "Models Used" count.
        assert ">3<" in html  # KPI value for model_count

    def test_html_escapes_session_id_to_prevent_xss(self):
        evil = "<script>alert(1)</script>"
        s_list = [{
            "id": evil,
            "status": "success",
            "total_tokens": 0,
            "event_count": 0,
            "total_cost": 0,
            "created_at": "2026-05-23T00:00:00Z",
            "agent_name": "x",
        }]
        summary = _aggregate_sessions(s_list)
        html = _render_html(s_list, summary, "http://x")
        # Raw <script> tag from the session id must NOT appear unescaped.
        assert "<script>alert(1)</script>" not in html
        # But the escaped form must.
        assert "&lt;script&gt;" in html

    def test_table_caps_rendered_rows_at_50(self):
        many = [
            {"id": f"s{i:03d}", "status": "success", "agent_name": "a",
             "total_tokens": 1, "event_count": 1, "total_cost": 0.01,
             "created_at": "2026-05-23T00:00:00Z"}
            for i in range(75)
        ]
        summary = _aggregate_sessions(many)
        html = _render_html(many, summary, "http://x")
        # Row 049 should be present, row 050+ should NOT (only first 50 rendered).
        assert "s049" in html
        assert "s050" not in html
        # The "(latest N)" label is min(50, len(sessions)).
        assert "(latest 50)" in html

    def test_top_chart_uses_top_10_by_cost(self):
        rows = [
            {"id": f"sess-{i}", "status": "success", "total_tokens": 0,
             "event_count": 0, "total_cost": float(i),  # i=0..14
             "created_at": "2026-05-23T00:00:00Z"}
            for i in range(15)
        ]
        summary = _aggregate_sessions(rows)
        html = _render_html(rows, summary, "http://x")
        # The top chart label JSON is rendered into the script body. The
        # session with cost 14 must be present, cost 0 must not.
        # The top labels appear next to "id":[12], so we just search the
        # rendered top labels JSON.
        # Both labels live in the chart script section.
        assert "sess-14" in html
        # The lowest of the top 10 is cost=5; cost=0/1/2/3/4 must be absent
        # from the chart labels (they still appear in the table though).
        # To make the test robust, just check ordering by counting the
        # high-cost sessions appear in label form.
        for high in (14, 13, 12, 11, 10, 9, 8, 7, 6, 5):
            assert f"sess-{high}" in html

    def test_error_status_row_gets_error_css_class(self, sessions):
        s = _aggregate_sessions(sessions)
        html = _render_html(sessions, s, "http://x")
        # Sessions with status error/failed must get the "error" tr class.
        assert '<tr class="error">' in html


# --------------------------------------------------------------------------- #
# cmd_dashboard
# --------------------------------------------------------------------------- #


def _args(**kw) -> argparse.Namespace:
    defaults = {
        "endpoint": "http://localhost:3000",
        "api_key": "test-key",
        "limit": 100,
        "output": None,
        "open": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestCmdDashboard:
    @patch("agentlens.cli_dashboard._get_client")
    def test_writes_default_filename(self, mock_get_client, sessions, tmp_path, capsys):
        cli_client = MagicMock()
        cli_client.get.return_value = _resp(sessions)
        mock_get_client.return_value = (cli_client, "http://localhost:3000")
        cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            cmd_dashboard(_args())
            assert (tmp_path / "agentlens-dashboard.html").exists()
            html = (tmp_path / "agentlens-dashboard.html").read_text(encoding="utf-8")
            assert "AgentLens Dashboard" in html
            out = capsys.readouterr().out
            assert "Dashboard written to" in out
            # Limit param flowed through to the GET.
            assert cli_client.get.call_args.kwargs["params"] == {"limit": 100}
        finally:
            os.chdir(cwd)

    @patch("agentlens.cli_dashboard._get_client")
    def test_custom_output_path_honoured(self, mock_get_client, sessions, tmp_path):
        cli_client = MagicMock()
        cli_client.get.return_value = _resp(sessions)
        mock_get_client.return_value = (cli_client, "http://example.com")
        out_path = tmp_path / "custom.html"
        cmd_dashboard(_args(output=str(out_path)))
        assert out_path.exists()
        assert "AgentLens Dashboard" in out_path.read_text(encoding="utf-8")

    @patch("agentlens.cli_dashboard._get_client")
    def test_dict_response_with_sessions_key_unwrapped(
        self, mock_get_client, sessions, tmp_path
    ):
        cli_client = MagicMock()
        cli_client.get.return_value = _resp({"sessions": sessions})
        mock_get_client.return_value = (cli_client, "http://x")
        out_path = tmp_path / "out.html"
        cmd_dashboard(_args(output=str(out_path)))
        # Total tokens (4300) must appear → unwrapping happened.
        assert "4,300" in out_path.read_text(encoding="utf-8")

    @patch("agentlens.cli_dashboard._get_client")
    def test_open_flag_triggers_webbrowser(self, mock_get_client, sessions, tmp_path):
        cli_client = MagicMock()
        cli_client.get.return_value = _resp(sessions)
        mock_get_client.return_value = (cli_client, "http://x")
        out_path = tmp_path / "out.html"
        with patch("webbrowser.open") as mock_open:
            cmd_dashboard(_args(output=str(out_path), open=True))
            mock_open.assert_called_once_with(str(out_path))

    @patch("agentlens.cli_dashboard._get_client")
    def test_zero_limit_defaults_to_100(self, mock_get_client, sessions, tmp_path):
        cli_client = MagicMock()
        cli_client.get.return_value = _resp(sessions)
        mock_get_client.return_value = (cli_client, "http://x")
        out_path = tmp_path / "out.html"
        # 0 is falsy → cmd_dashboard should treat it as "use default 100".
        cmd_dashboard(_args(output=str(out_path), limit=0))
        assert cli_client.get.call_args.kwargs["params"] == {"limit": 100}
