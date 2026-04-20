"""Tests for agentlens.cli_digest — pure logic helpers and rendering."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_digest import (
    _arrow,
    _build_digest,
    _count_errors,
    _filter_by_window,
    _model_breakdown,
    _parse_ts,
    _pct_change,
    _render_html,
    _render_markdown,
    _render_text,
    _sum_metric,
    _top_sessions,
    cmd_digest,
)


# ── _parse_ts ────────────────────────────────────────────────────────────


class TestParseTs:
    def test_none(self):
        assert _parse_ts(None) is None

    def test_epoch_seconds(self):
        dt = _parse_ts(1700000000)
        assert dt is not None
        assert dt.year == 2023

    def test_epoch_millis(self):
        dt = _parse_ts(1700000000000)
        assert dt is not None
        assert dt.year == 2023

    def test_iso_string_with_fractional(self):
        dt = _parse_ts("2024-06-15T10:30:00.000Z")
        assert dt is not None
        assert dt.month == 6 and dt.day == 15

    def test_iso_string_no_frac(self):
        dt = _parse_ts("2024-06-15T10:30:00Z")
        assert dt is not None
        assert dt.hour == 10

    def test_plain_datetime_string(self):
        dt = _parse_ts("2024-06-15 10:30:00")
        assert dt is not None

    def test_unrecognised_string(self):
        assert _parse_ts("not-a-date") is None

    def test_float_epoch(self):
        dt = _parse_ts(1700000000.5)
        assert dt is not None


# ── _filter_by_window ────────────────────────────────────────────────────


class TestFilterByWindow:
    def _session(self, hours_ago: float) -> dict:
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return {"created_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}

    def test_current_period(self):
        sessions = [self._session(1), self._session(2), self._session(30)]
        current, prev = _filter_by_window(sessions, days=1)
        assert len(current) == 2
        assert len(prev) == 1

    def test_empty(self):
        current, prev = _filter_by_window([], days=7)
        assert current == [] and prev == []

    def test_no_timestamp_skipped(self):
        sessions = [{"name": "no-ts"}]
        current, prev = _filter_by_window(sessions, days=1)
        assert len(current) == 0 and len(prev) == 0


# ── _sum_metric ──────────────────────────────────────────────────────────


class TestSumMetric:
    def test_direct_key(self):
        sessions = [{"cost": 1.5}, {"cost": 2.5}]
        assert _sum_metric(sessions, "cost") == 4.0

    def test_fallback_total_cost(self):
        sessions = [{"total_cost": 3.0}]
        assert _sum_metric(sessions, "cost") == 3.0

    def test_fallback_total_tokens(self):
        sessions = [{"total_tokens": 100}]
        assert _sum_metric(sessions, "tokens") == 100

    def test_none_values(self):
        sessions = [{"cost": None}]
        assert _sum_metric(sessions, "cost") == 0.0


# ── _count_errors ────────────────────────────────────────────────────────


class TestCountErrors:
    def test_error_count_field(self):
        assert _count_errors([{"error_count": 3}]) == 3

    def test_status_error(self):
        assert _count_errors([{"status": "error"}]) == 1

    def test_status_failed(self):
        assert _count_errors([{"status": "failed"}]) == 1

    def test_combined(self):
        assert _count_errors([{"error_count": 2, "status": "error"}]) == 3

    def test_no_errors(self):
        assert _count_errors([{"status": "ok"}]) == 0


# ── _pct_change ──────────────────────────────────────────────────────────


class TestPctChange:
    def test_positive(self):
        assert _pct_change(150, 100) == "+50.0%"

    def test_negative(self):
        assert _pct_change(50, 100) == "-50.0%"

    def test_zero_previous_with_current(self):
        assert _pct_change(10, 0) == "+∞"

    def test_zero_both(self):
        assert _pct_change(0, 0) == "—"

    def test_no_change(self):
        assert _pct_change(100, 100) == "+0.0%"


# ── _arrow ───────────────────────────────────────────────────────────────


class TestArrow:
    def test_increase_default(self):
        assert "🟢 ↑" in _arrow(10, 5)

    def test_increase_lower_is_better(self):
        assert "🔴 ↑" in _arrow(10, 5, lower_is_better=True)

    def test_decrease_default(self):
        assert "🔴 ↓" in _arrow(5, 10)

    def test_decrease_lower_is_better(self):
        assert "🟢 ↓" in _arrow(5, 10, lower_is_better=True)

    def test_equal(self):
        assert "⚪ →" in _arrow(5, 5)


# ── _model_breakdown ────────────────────────────────────────────────────


class TestModelBreakdown:
    def test_counts(self):
        sessions = [{"model": "gpt-4"}, {"model": "gpt-4"}, {"model": "claude"}]
        result = _model_breakdown(sessions)
        assert result["gpt-4"] == 2
        assert result["claude"] == 1

    def test_sorted_by_count(self):
        sessions = [{"model": "a"}] + [{"model": "b"}] * 3
        result = _model_breakdown(sessions)
        assert list(result.keys())[0] == "b"

    def test_fallback_unknown(self):
        sessions = [{}]
        result = _model_breakdown(sessions)
        assert "unknown" in result


# ── _top_sessions ────────────────────────────────────────────────────────


class TestTopSessions:
    def test_top_n(self):
        sessions = [{"cost": i} for i in range(10)]
        top = _top_sessions(sessions, n=3)
        assert len(top) == 3
        assert top[0]["cost"] == 9

    def test_uses_total_cost_fallback(self):
        sessions = [{"total_cost": 5}, {"total_cost": 10}]
        top = _top_sessions(sessions, n=1)
        assert top[0]["total_cost"] == 10


# ── Renderers ────────────────────────────────────────────────────────────


_SAMPLE_DIGEST = {
    "period": "day",
    "days": 1,
    "generated_at": "2024-06-15T12:00:00+00:00",
    "kpis": {
        "sessions": {"current": 42, "previous": 35, "change": "+20.0%"},
        "cost": {"current": 1.2345, "previous": 0.9876, "change": "+25.0%"},
        "tokens": {"current": 50000, "previous": 40000, "change": "+25.0%"},
        "errors": {"current": 2, "previous": 5, "change": "-60.0%"},
    },
    "top_sessions": [
        {"id": "sess-001", "model": "gpt-4", "cost": 0.5, "status": "ok"},
    ],
    "model_breakdown": {"gpt-4": 30, "claude": 12},
    "alerts_count": 3,
    "alerts_sample": [{"type": "cost", "message": "Budget exceeded"}],
    "recommendations": ["Error count decreased — nice!"],
}


class TestRenderText:
    def test_contains_kpis(self):
        text = _render_text(_SAMPLE_DIGEST)
        assert "42" in text
        assert "+20.0%" in text
        assert "AgentLens Digest" in text

    def test_contains_top_sessions(self):
        text = _render_text(_SAMPLE_DIGEST)
        assert "sess-001" in text

    def test_empty_alerts(self):
        d = {**_SAMPLE_DIGEST, "alerts_count": 0, "alerts_sample": []}
        text = _render_text(d)
        assert "Alerts" not in text


class TestRenderMarkdown:
    def test_contains_headers(self):
        md = _render_markdown(_SAMPLE_DIGEST)
        assert "# AgentLens Digest" in md
        assert "## 📊 Key Metrics" in md

    def test_table_format(self):
        md = _render_markdown(_SAMPLE_DIGEST)
        assert "| Sessions |" in md

    def test_no_recommendations_section_when_empty(self):
        d = {**_SAMPLE_DIGEST, "recommendations": []}
        md = _render_markdown(d)
        assert "Recommendations" not in md


class TestRenderHtml:
    def test_is_valid_html(self):
        html = _render_html(_SAMPLE_DIGEST)
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_contains_data(self):
        html = _render_html(_SAMPLE_DIGEST)
        assert "42" in html
        assert "gpt-4" in html


# ── cmd_digest integration ───────────────────────────────────────────────


class TestCmdDigest:
    @patch("agentlens.cli_digest._get_client")
    @patch("agentlens.cli_digest._fetch_sessions")
    @patch("agentlens.cli_digest._fetch_alerts")
    def test_text_output(self, mock_alerts, mock_sessions, mock_client, capsys):
        mock_client.return_value = (MagicMock(), "http://localhost")
        now = datetime.now(timezone.utc)
        mock_sessions.return_value = [
            {"created_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "cost": 1.0, "model": "gpt-4", "status": "ok", "id": "s1"},
        ]
        mock_alerts.return_value = []

        args = argparse.Namespace(period="day", format="text", output=None, top=5, open=False)
        cmd_digest(args)
        out = capsys.readouterr().out
        assert "AgentLens Digest" in out

    @patch("agentlens.cli_digest._get_client")
    @patch("agentlens.cli_digest._fetch_sessions")
    @patch("agentlens.cli_digest._fetch_alerts")
    def test_json_output(self, mock_alerts, mock_sessions, mock_client, capsys):
        mock_client.return_value = (MagicMock(), "http://localhost")
        mock_sessions.return_value = []
        mock_alerts.return_value = []

        args = argparse.Namespace(period="week", format="json", output=None, top=5, open=False)
        cmd_digest(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "kpis" in data

    def test_connect_error(self, capsys):
        import httpx
        with patch("agentlens.cli_digest._build_digest", side_effect=httpx.ConnectError("fail")):
            with pytest.raises(SystemExit):
                args = argparse.Namespace(period="day", format="text", output=None, top=5, open=False)
                cmd_digest(args)
