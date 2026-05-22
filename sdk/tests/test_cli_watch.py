"""Tests for agentlens.cli_watch.

Covers the pure helpers (_format_cost, _rate_indicator, _fetch_snapshot,
_render_dashboard) and the new WatchOptions dataclass introduced by the
refactor. The cmd_watch live loop is exercised with a 1-tick duration limit
and a mocked client so we don't sleep or talk to a real backend.
"""

from __future__ import annotations

import argparse
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from agentlens import cli_watch
from agentlens.cli_watch import (
    WatchOptions,
    _fetch_snapshot,
    _format_cost,
    _rate_indicator,
    _render_dashboard,
)


# ---------------------------------------------------------------------------
# _format_cost
# ---------------------------------------------------------------------------


class TestFormatCost:
    def test_tiny_uses_four_decimals(self):
        assert _format_cost(0.001234) == "$0.0012"

    def test_sub_dollar_uses_three_decimals(self):
        assert _format_cost(0.5) == "$0.500"

    def test_above_dollar_uses_two_decimals(self):
        assert _format_cost(12.345) == "$12.35"

    def test_zero(self):
        assert _format_cost(0) == "$0.0000"


# ---------------------------------------------------------------------------
# _rate_indicator
# ---------------------------------------------------------------------------


class TestRateIndicator:
    def test_both_zero_is_horizontal_arrow(self):
        assert _rate_indicator(0, 0) == "→"

    def test_previous_zero_current_positive_is_up_arrow(self):
        assert _rate_indicator(5, 0) == "↑"

    def test_strong_increase_shows_percent_up(self):
        # +50% should trip the >10% branch.
        out = _rate_indicator(15, 10)
        assert out.startswith("↑")
        assert "+50" in out

    def test_strong_decrease_shows_percent_down(self):
        out = _rate_indicator(5, 10)
        assert out.startswith("↓")
        assert "-50" in out

    def test_small_change_is_horizontal_arrow(self):
        # +5% should stay in the "flat" band.
        assert _rate_indicator(105, 100) == "→"


# ---------------------------------------------------------------------------
# WatchOptions
# ---------------------------------------------------------------------------


class TestWatchOptions:
    def test_defaults(self):
        opts = WatchOptions()
        assert opts.interval == 5
        assert opts.show_spark is True
        assert opts.compact is False
        assert opts.alert_threshold is None
        assert opts.agent_filter is None
        assert opts.metric_filter is None

    def test_is_frozen(self):
        opts = WatchOptions()
        with pytest.raises(Exception):
            opts.interval = 10  # type: ignore[misc]

    def test_equality(self):
        assert WatchOptions(interval=3) == WatchOptions(interval=3)
        assert WatchOptions(interval=3) != WatchOptions(interval=4)


# ---------------------------------------------------------------------------
# _fetch_snapshot
# ---------------------------------------------------------------------------


def _resp(status: int, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class TestFetchSnapshot:
    def test_merges_analytics_and_sessions(self):
        client = MagicMock()

        def fake_get(path, params=None):
            if path == "/api/analytics":
                return _resp(200, {
                    "total_sessions": 7,
                    "total_cost": 1.23,
                    "total_tokens": 5000,
                    "total_events": 42,
                    "by_model": [
                        {"model": "gpt-4o", "cost": 0.9, "tokens": 3000},
                    ],
                })
            if path == "/api/sessions":
                return _resp(200, [
                    {"agent_name": "alpha", "total_cost": 0.4, "error_count": 1},
                    {"agent_name": "alpha", "total_cost": 0.3, "error_count": 0},
                    {"agent_name": "beta",  "total_cost": 0.5, "error_count": 2},
                ])
            return _resp(404, {})

        client.get.side_effect = fake_get
        snap = _fetch_snapshot(client)

        # Sessions endpoint wins for "sessions" (it's hit second).
        assert snap["sessions"] == 3
        assert snap["models"]["gpt-4o"]["cost"] == 0.9
        # Cost accumulates from sessions on top of analytics base.
        assert snap["total_cost"] == pytest.approx(1.23 + 0.4 + 0.3 + 0.5)
        assert snap["errors"] == 3
        assert snap["agents"]["alpha"]["sessions"] == 2
        assert snap["agents"]["beta"]["sessions"] == 1

    def test_analytics_non_200_is_silently_skipped(self):
        client = MagicMock()
        client.get.side_effect = lambda path, **_: _resp(500, {}) if path == "/api/analytics" else _resp(200, [])
        snap = _fetch_snapshot(client)
        assert snap["total_cost"] == 0.0
        assert snap["sessions"] == 0

    def test_network_errors_are_swallowed(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError("boom")
        snap = _fetch_snapshot(client)
        # Defaults survive.
        assert snap["sessions"] == 0
        assert snap["total_cost"] == 0.0
        assert "timestamp" in snap


# ---------------------------------------------------------------------------
# _render_dashboard
# ---------------------------------------------------------------------------


def _snap(**kw):
    base = {
        "sessions": 5,
        "total_cost": 1.50,
        "total_tokens": 1234,
        "total_events": 100,
        "errors": 2,
        "agents": {"alpha": {"sessions": 5, "cost": 1.5, "errors": 2}},
        "models": {"gpt-4o": {"cost": 1.5, "tokens": 1234}},
        "timestamp": "2026-05-21T12:00:00+00:00",
    }
    base.update(kw)
    return base


class TestRenderDashboard:
    def test_renders_all_sections_by_default(self):
        history: deque = deque([_snap(sessions=3, total_cost=1.0, errors=1), _snap()])
        out = _render_dashboard(_snap(), history, tick=1, options=WatchOptions())
        assert "AgentLens Watch" in out
        assert "Sessions:" in out
        assert "Cost:" in out
        assert "Tokens:" in out
        assert "Errors:" in out
        assert "alpha" in out      # agent breakdown
        assert "gpt-4o" in out     # model breakdown
        assert "Ctrl+C" in out

    def test_metric_filter_hides_other_rows(self):
        opts = WatchOptions(metric_filter="cost")
        out = _render_dashboard(_snap(), deque([_snap()]), tick=0, options=opts)
        assert "Cost:" in out
        assert "Sessions:" not in out
        assert "Tokens:" not in out
        assert "Errors:" not in out

    def test_compact_mode_drops_breakdown_tables(self):
        opts = WatchOptions(compact=True)
        out = _render_dashboard(_snap(), deque([_snap()]), tick=0, options=opts)
        assert "alpha" not in out
        assert "gpt-4o" not in out

    def test_alert_threshold_triggers_cost_alert(self):
        opts = WatchOptions(alert_threshold=1.0)
        out = _render_dashboard(_snap(total_cost=5.0), deque([_snap()]), tick=0, options=opts)
        assert "Cost $5.00 exceeds threshold" in out

    def test_alert_high_error_rate(self):
        # errors=50 of total_events=100 -> 50% error rate.
        snap = _snap(errors=50, total_events=100)
        out = _render_dashboard(snap, deque([snap]), tick=0,
                                options=WatchOptions(alert_threshold=999.0))
        assert "Error rate" in out
        assert "is high" in out

    def test_agent_filter_narrows_breakdown(self):
        snap = _snap()
        snap["agents"] = {
            "alpha": {"sessions": 5, "cost": 1.5, "errors": 2},
            "beta":  {"sessions": 3, "cost": 0.5, "errors": 0},
        }
        opts = WatchOptions(agent_filter="alpha")
        out = _render_dashboard(snap, deque([snap]), tick=0, options=opts)
        assert "alpha" in out
        assert "beta" not in out

    def test_no_spark_disables_sparklines(self):
        # Force history with varied values then disable spark and assert
        # none of the sparkline glyphs leak into the output.
        history = deque([_snap(sessions=1), _snap(sessions=3), _snap(sessions=7)])
        opts = WatchOptions(show_spark=False)
        out = _render_dashboard(_snap(sessions=7), history, tick=2, options=opts)
        for ch in "▁▂▃▄▅▆▇█":
            assert ch not in out

    def test_elapsed_under_60s_renders_seconds_only(self):
        out = _render_dashboard(_snap(), deque([_snap()]), tick=3,
                                options=WatchOptions(interval=5))
        assert "elapsed: 15s" in out

    def test_elapsed_over_60s_renders_minutes(self):
        out = _render_dashboard(_snap(), deque([_snap()]), tick=20,
                                options=WatchOptions(interval=5))
        # 20 * 5 = 100s = "1m 40s"
        assert "elapsed: 1m 40s" in out


# ---------------------------------------------------------------------------
# cmd_watch (live loop, 1-tick smoke)
# ---------------------------------------------------------------------------


class TestCmdWatch:
    def test_duration_limit_exits_cleanly(self, capsys):
        # 1 minute duration / interval 60s -> max_ticks == 1 -> loop runs once.
        fake_client = MagicMock()
        fake_client.get.return_value = _resp(200, {})

        args = argparse.Namespace(
            endpoint=None, api_key=None,
            interval=60, metric=None, agent=None,
            alert_threshold=None, compact=False, no_spark=True,
            duration=1,
        )

        with patch.object(cli_watch, "_get_client", return_value=fake_client), \
             patch.object(cli_watch, "_clear_screen"), \
             patch.object(cli_watch, "time") as mock_time:
            cli_watch.cmd_watch(args)

        out = capsys.readouterr().out
        assert "Duration limit reached" in out

    def test_keyboard_interrupt_prints_summary(self, capsys):
        fake_client = MagicMock()
        fake_client.get.return_value = _resp(200, {})

        args = argparse.Namespace(
            endpoint=None, api_key=None,
            interval=1, metric=None, agent=None,
            alert_threshold=None, compact=False, no_spark=True,
            duration=None,
        )

        with patch.object(cli_watch, "_get_client", return_value=fake_client), \
             patch.object(cli_watch, "_clear_screen"), \
             patch.object(cli_watch, "time") as mock_time:
            mock_time.sleep.side_effect = KeyboardInterrupt()
            cli_watch.cmd_watch(args)

        out = capsys.readouterr().out
        assert "Watch stopped" in out
