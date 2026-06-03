"""Tests for cli_drift module."""

from __future__ import annotations

import pytest

from agentlens.cli_drift import _analyze_drift, _extract_metrics, _mean, _stddev, _z_score, _pct_change


# ── Unit tests for helpers ──────────────────────────────────────────

def test_mean_empty():
    assert _mean([]) == 0.0


def test_mean_values():
    assert _mean([1.0, 2.0, 3.0]) == 2.0


def test_stddev_single():
    assert _stddev([5.0]) == 0.0


def test_stddev_values():
    s = _stddev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert abs(s - 2.0) < 0.1


def test_z_score_zero_std():
    assert _z_score(5.0, 0.0, 5.0) == 0.0
    assert _z_score(5.0, 0.0, 6.0) == float("inf")


def test_z_score_normal():
    z = _z_score(10.0, 2.0, 14.0)
    assert abs(z - 2.0) < 0.001


def test_pct_change():
    assert _pct_change(100.0, 120.0) == 20.0
    assert _pct_change(100.0, 80.0) == -20.0
    assert _pct_change(0.0, 0.0) == 0.0


# ── Extract metrics ─────────────────────────────────────────────────

def test_extract_metrics_basic():
    sessions = [
        {"total_tokens": 100, "duration_ms": 500, "tool_call_count": 3,
         "event_count": 5, "total_cost": 0.01, "has_error": False},
        {"total_tokens": 200, "duration_ms": 600, "tool_call_count": 4,
         "event_count": 7, "total_cost": 0.02, "has_error": True},
    ]
    m = _extract_metrics(sessions)
    assert m["avg_tokens"] == [100.0, 200.0]
    assert m["error_rate"] == [0.0, 1.0]


def test_extract_metrics_empty():
    m = _extract_metrics([])
    for v in m.values():
        assert v == []


# ── Full analysis ───────────────────────────────────────────────────

def _make_sessions(n: int, tokens: float = 100, latency: float = 500) -> list[dict]:
    return [
        {"total_tokens": tokens + i, "duration_ms": latency + i * 10,
         "tool_call_count": 3, "event_count": 5, "total_cost": 0.01,
         "has_error": False}
        for i in range(n)
    ]


def test_analyze_stable():
    baseline = _make_sessions(20, tokens=100, latency=500)
    recent = _make_sessions(5, tokens=105, latency=510)
    result = _analyze_drift(baseline, recent, threshold=2.0)
    assert result["verdict"] == "STABLE"
    assert result["grade"] == "A"


def test_analyze_major_drift():
    baseline = _make_sessions(20, tokens=100, latency=500)
    # Huge spike in tokens
    recent = _make_sessions(5, tokens=500, latency=500)
    result = _analyze_drift(baseline, recent, threshold=2.0)
    # Should detect at least minor drift
    assert result["verdict"] in ("MINOR_DRIFT", "MODERATE_DRIFT", "SIGNIFICANT_DRIFT")


def test_analyze_insufficient_data():
    baseline = _make_sessions(2)  # too few
    recent = _make_sessions(2)
    result = _analyze_drift(baseline, recent, threshold=2.0)
    # Should still return a result gracefully
    assert "verdict" in result
    assert "dimensions" in result


def test_analyze_returns_all_dimensions():
    baseline = _make_sessions(10)
    recent = _make_sessions(5)
    result = _analyze_drift(baseline, recent, threshold=2.0)
    assert len(result["dimensions"]) == 6
    for d in result["dimensions"]:
        assert "dimension" in d
        assert "status" in d
