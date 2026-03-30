"""Tests for cli_bottleneck module."""

from agentlens.cli_bottleneck import _percentile, _bar, _severity


def test_percentile_empty():
    assert _percentile([], 95) == 0.0


def test_percentile_single():
    assert _percentile([42.0], 50) == 42.0


def test_percentile_basic():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    p50 = _percentile(vals, 50)
    assert p50 == 30.0


def test_percentile_p95():
    vals = list(range(1, 101))
    p95 = _percentile([float(v) for v in vals], 95)
    assert 95 <= p95 <= 96


def test_bar_full():
    b = _bar(100, 100, 10)
    assert b == "██████████"


def test_bar_half():
    b = _bar(50, 100, 10)
    assert b == "█████░░░░░"


def test_bar_zero():
    b = _bar(0, 100, 10)
    assert b == "░░░░░░░░░░"


def test_bar_zero_max():
    assert _bar(50, 0) == ""


def test_severity_critical():
    assert "CRITICAL" in _severity(40)


def test_severity_high():
    assert "HIGH" in _severity(25)


def test_severity_medium():
    assert "MEDIUM" in _severity(10)


def test_severity_low():
    assert "LOW" in _severity(5)
