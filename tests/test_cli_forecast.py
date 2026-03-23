"""Tests for cli_forecast module."""

from __future__ import annotations

import pytest

from agentlens.cli_forecast import (
    _linear_regression,
    _exponential_smoothing,
    _spark,
    _aggregate_daily,
    _format_value,
)


class TestLinearRegression:
    def test_perfect_line(self):
        slope, intercept = _linear_regression([0.0, 1.0, 2.0], [0.0, 2.0, 4.0])
        assert abs(slope - 2.0) < 1e-6
        assert abs(intercept) < 1e-6

    def test_single_point(self):
        slope, intercept = _linear_regression([1.0], [5.0])
        assert slope == 0.0
        assert intercept == 5.0

    def test_empty(self):
        slope, intercept = _linear_regression([], [])
        assert slope == 0.0
        assert intercept == 0.0

    def test_flat(self):
        slope, intercept = _linear_regression([0.0, 1.0, 2.0], [3.0, 3.0, 3.0])
        assert abs(slope) < 1e-6
        assert abs(intercept - 3.0) < 1e-6


class TestExponentialSmoothing:
    def test_basic(self):
        result = _exponential_smoothing([10.0, 20.0, 30.0], alpha=0.5)
        assert len(result) == 3
        assert result[0] == 10.0
        assert abs(result[1] - 15.0) < 1e-6

    def test_empty(self):
        assert _exponential_smoothing([]) == []

    def test_single(self):
        assert _exponential_smoothing([42.0]) == [42.0]


class TestSpark:
    def test_basic(self):
        result = _spark([0, 1, 2, 3, 4])
        assert len(result) == 5
        assert result[0] == "▁"
        assert result[-1] == "█"

    def test_empty(self):
        assert _spark([]) == ""

    def test_constant(self):
        result = _spark([5, 5, 5])
        assert len(result) == 3


class TestFormatValue:
    def test_cost(self):
        assert _format_value(1234.56, "cost") == "$1,234.56"

    def test_tokens_millions(self):
        assert "M" in _format_value(2_500_000, "tokens")

    def test_tokens_thousands(self):
        assert "K" in _format_value(5_000, "tokens")

    def test_sessions(self):
        assert _format_value(42, "sessions") == "42"


class TestAggregate:
    def test_cost_aggregation(self):
        sessions = [
            {"created_at": "2026-03-01T10:00:00Z", "total_cost": 1.5},
            {"created_at": "2026-03-01T14:00:00Z", "total_cost": 0.5},
            {"created_at": "2026-03-02T10:00:00Z", "total_cost": 3.0},
        ]
        result = _aggregate_daily(sessions, "cost")
        assert len(result) == 2
        assert result[0] == ("2026-03-01", 2.0)
        assert result[1] == ("2026-03-02", 3.0)

    def test_sessions_aggregation(self):
        sessions = [
            {"created_at": "2026-03-01T10:00:00Z"},
            {"created_at": "2026-03-01T14:00:00Z"},
            {"created_at": "2026-03-02T10:00:00Z"},
        ]
        result = _aggregate_daily(sessions, "sessions")
        assert result[0] == ("2026-03-01", 2.0)
        assert result[1] == ("2026-03-02", 1.0)

    def test_gap_filling(self):
        sessions = [
            {"created_at": "2026-03-01T10:00:00Z", "total_cost": 1.0},
            {"created_at": "2026-03-03T10:00:00Z", "total_cost": 2.0},
        ]
        result = _aggregate_daily(sessions, "cost")
        assert len(result) == 3
        assert result[1] == ("2026-03-02", 0.0)

    def test_empty(self):
        assert _aggregate_daily([], "cost") == []
