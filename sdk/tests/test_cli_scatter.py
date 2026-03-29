"""Tests for cli_scatter module."""

from __future__ import annotations

import json
import math
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_scatter import (
    _extract_metric,
    _format_axis_value,
    _linear_regression,
    _pearson,
    render_scatter,
    cmd_scatter,
)


class TestExtractMetric:
    def test_cost(self):
        assert _extract_metric({"total_cost": 1.5}, "cost") == 1.5

    def test_tokens(self):
        assert _extract_metric({"total_tokens": 500}, "tokens") == 500

    def test_tokens_fallback(self):
        assert _extract_metric({"prompt_tokens": 100, "completion_tokens": 200}, "tokens") == 300

    def test_events(self):
        assert _extract_metric({"event_count": 10}, "events") == 10

    def test_errors(self):
        assert _extract_metric({"error_count": 3}, "errors") == 3

    def test_tool_calls(self):
        assert _extract_metric({"tool_call_count": 7}, "tool_calls") == 7

    def test_duration_ms(self):
        assert _extract_metric({"duration_ms": 5000}, "duration") == 5.0

    def test_unknown(self):
        assert _extract_metric({}, "unknown") is None


class TestFormatAxisValue:
    def test_millions(self):
        assert _format_axis_value(1_500_000) == "1.5M"

    def test_thousands(self):
        assert _format_axis_value(2_500) == "2.5K"

    def test_integer(self):
        assert _format_axis_value(42.0) == "42"

    def test_decimal(self):
        assert _format_axis_value(3.14) == "3.14"

    def test_small(self):
        result = _format_axis_value(0.001)
        assert "e" in result


class TestLinearRegression:
    def test_basic(self):
        result = _linear_regression([1, 2, 3], [2, 4, 6])
        assert result is not None
        slope, intercept = result
        assert abs(slope - 2.0) < 0.001
        assert abs(intercept) < 0.001

    def test_too_few(self):
        assert _linear_regression([1], [2]) is None

    def test_constant_x(self):
        assert _linear_regression([1, 1, 1], [1, 2, 3]) is None


class TestPearson:
    def test_perfect_positive(self):
        r = _pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        assert r is not None
        assert abs(r - 1.0) < 0.001

    def test_perfect_negative(self):
        r = _pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])
        assert r is not None
        assert abs(r + 1.0) < 0.001

    def test_too_few(self):
        assert _pearson([1, 2], [3, 4]) is None


class TestRenderScatter:
    def test_basic(self):
        xs = [1, 2, 3, 4, 5]
        ys = [10, 20, 30, 40, 50]
        result = render_scatter(xs, ys, "cost", "tokens", width=40, height=10)
        assert "cost" in result
        assert "tokens" in result
        assert "•" in result or "●" in result

    def test_empty(self):
        result = render_scatter([], [], "cost", "tokens")
        assert "no data" in result

    def test_density(self):
        xs = [1.0] * 5
        ys = [1.0] * 5
        result = render_scatter(xs, ys, "x", "y", width=20, height=10)
        assert "█" in result

    def test_no_trend(self):
        xs = [1, 2, 3]
        ys = [1, 2, 3]
        result = render_scatter(xs, ys, "x", "y", width=20, height=10, show_trend=False)
        assert "╌" not in result


class TestCmdScatter:
    @patch("agentlens.cli_scatter.fetch_sessions")
    @patch("agentlens.cli_scatter.get_client")
    def test_json_output(self, mock_client, mock_fetch, capsys):
        mock_client.return_value = (MagicMock(), "http://localhost:3000")
        mock_fetch.return_value = [
            {"id": "s1", "total_cost": 1.0, "total_tokens": 100},
            {"id": "s2", "total_cost": 2.0, "total_tokens": 200},
            {"id": "s3", "total_cost": 3.0, "total_tokens": 300},
        ]
        args = MagicMock()
        args.x = "cost"
        args.y = "tokens"
        args.limit = 200
        args.width = 40
        args.height = 10
        args.agent = None
        args.no_trend = False
        args.format = "json"
        args.output = None

        cmd_scatter(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["count"] == 3
        assert data["x_metric"] == "cost"
        assert data["y_metric"] == "tokens"
        assert data["correlation"] is not None
