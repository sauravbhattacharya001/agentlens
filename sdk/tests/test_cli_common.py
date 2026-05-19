"""Tests for ``agentlens.cli_common`` — shared CLI helpers.

These helpers (``get_client``, ``print_json``, ``fetch_sessions``,
``percentile``, ``linear_regression``, ``sparkline``, ``bar_chart``)
are imported by ~30 CLI sub-commands.  Regressions here ripple across
the entire CLI surface, so we exercise:

* environment-variable resolution order for endpoint / api-key
* explicit-flag overrides
* ``print_json`` pretty-printing (incl. non-serialisable fallback)
* ``fetch_sessions`` for both list-shaped and dict-shaped backends
* ``percentile`` empty / single / interpolation
* ``linear_regression`` argument-order quirk (xs, ys)
* ``sparkline`` empty / constant / width down-sampling / no-truncate default
* ``bar_chart`` full / partial / zero-max edge case
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import httpx
import pytest

from agentlens import cli_common
from agentlens.cli_common import (
    bar_chart,
    fetch_sessions,
    get_client,
    get_client_only,
    linear_regression,
    percentile,
    print_json,
    sparkline,
)


# --------------------------------------------------------------------------- #
# get_client / get_client_only
# --------------------------------------------------------------------------- #


def _ns(**kw):
    return argparse.Namespace(**kw)


def test_get_client_uses_explicit_flags_over_env(monkeypatch):
    monkeypatch.setenv("AGENTLENS_ENDPOINT", "http://ignored:1")
    monkeypatch.setenv("AGENTLENS_API_KEY", "ignored-key")
    args = _ns(endpoint="http://flag:9", api_key="flag-key")
    client, endpoint = get_client(args)
    try:
        assert endpoint == "http://flag:9"
        assert str(client.base_url).rstrip("/") == "http://flag:9"
        assert client.headers["x-api-key"] == "flag-key"
    finally:
        client.close()


def test_get_client_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("AGENTLENS_ENDPOINT", "http://env:1234")
    monkeypatch.setenv("AGENTLENS_API_KEY", "env-key")
    args = _ns(endpoint=None, api_key=None)
    client, endpoint = get_client(args)
    try:
        assert endpoint == "http://env:1234"
        assert client.headers["x-api-key"] == "env-key"
    finally:
        client.close()


def test_get_client_uses_builtin_defaults(monkeypatch):
    monkeypatch.delenv("AGENTLENS_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENTLENS_API_KEY", raising=False)
    client, endpoint = get_client(_ns())
    try:
        assert endpoint == "http://localhost:3000"
        assert client.headers["x-api-key"] == "default"
    finally:
        client.close()


def test_get_client_strips_trailing_slash(monkeypatch):
    monkeypatch.delenv("AGENTLENS_ENDPOINT", raising=False)
    args = _ns(endpoint="http://srv:3000/")
    client, endpoint = get_client(args)
    try:
        assert endpoint == "http://srv:3000"
    finally:
        client.close()


def test_get_client_only_returns_just_the_client(monkeypatch):
    monkeypatch.delenv("AGENTLENS_ENDPOINT", raising=False)
    c = get_client_only(_ns())
    try:
        assert isinstance(c, httpx.Client)
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# print_json
# --------------------------------------------------------------------------- #


def test_print_json_emits_pretty_indented_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_json({"b": 2, "a": 1})
    out = buf.getvalue()
    # Must be valid JSON when stripped of trailing newline.
    parsed = json.loads(out)
    assert parsed == {"b": 2, "a": 1}
    # And it should be indented (multi-line).
    assert "\n" in out.strip()


def test_print_json_handles_non_serializable_via_default_str():
    class Weird:
        def __str__(self) -> str:
            return "weird-thing"

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_json({"x": Weird()})
    assert "weird-thing" in buf.getvalue()


# --------------------------------------------------------------------------- #
# fetch_sessions
# --------------------------------------------------------------------------- #


def _mock_client(json_payload):
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_payload
    client.get.return_value = resp
    return client


def test_fetch_sessions_accepts_list_response():
    client = _mock_client([{"id": "s1"}, {"id": "s2"}])
    result = fetch_sessions(client, limit=50)
    assert result == [{"id": "s1"}, {"id": "s2"}]
    client.get.assert_called_once_with("/api/sessions", params={"limit": 50})


def test_fetch_sessions_accepts_dict_wrapper():
    client = _mock_client({"sessions": [{"id": "s3"}]})
    result = fetch_sessions(client)
    assert result == [{"id": "s3"}]


def test_fetch_sessions_dict_without_key_returns_empty():
    client = _mock_client({"unrelated": "payload"})
    assert fetch_sessions(client) == []


# --------------------------------------------------------------------------- #
# percentile
# --------------------------------------------------------------------------- #


def test_percentile_empty_returns_zero():
    assert percentile([], 95) == 0.0


def test_percentile_single_value():
    assert percentile([42.0], 50) == 42.0


def test_percentile_sorts_internally():
    # Pass deliberately unsorted input — wrapper must sort.
    assert percentile([5, 1, 3, 2, 4], 50) == 3


def test_percentile_p95_of_1_to_100():
    p95 = percentile([float(v) for v in range(1, 101)], 95)
    # _utils.percentile uses linear interpolation between ranks.
    assert 95.0 <= p95 <= 96.0


# --------------------------------------------------------------------------- #
# linear_regression wrapper (xs, ys) ordering
# --------------------------------------------------------------------------- #


def test_linear_regression_perfect_line_through_origin():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [0.0, 2.0, 4.0, 6.0]
    slope, intercept = linear_regression(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(0.0)


def test_linear_regression_constant_y_has_zero_slope():
    xs = [0.0, 1.0, 2.0]
    ys = [5.0, 5.0, 5.0]
    slope, intercept = linear_regression(xs, ys)
    assert slope == pytest.approx(0.0)
    assert intercept == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# sparkline
# --------------------------------------------------------------------------- #


def test_sparkline_empty():
    assert sparkline([]) == ""


def test_sparkline_constant_values_does_not_divide_by_zero():
    out = sparkline([3.0, 3.0, 3.0])
    assert len(out) == 3
    # All glyphs identical because spread is zero.
    assert len(set(out)) == 1


def test_sparkline_default_emits_one_glyph_per_value():
    # Backwards-compat guarantee: callers in cli_trends / cli_watch /
    # stamina rely on len(out) == len(input).
    values = list(range(20))
    out = sparkline([float(v) for v in values])
    assert len(out) == 20


def test_sparkline_uses_full_glyph_range_on_monotonic_series():
    out = sparkline([float(v) for v in range(8)])
    # min → lowest glyph, max → highest glyph.
    bars = cli_common._SPARKLINE_BARS
    assert out[0] == bars[0]
    assert out[-1] == bars[-1]


def test_sparkline_width_down_samples_when_input_longer():
    out = sparkline([float(v) for v in range(100)], width=10)
    assert len(out) == 10


def test_sparkline_width_no_op_when_input_shorter():
    out = sparkline([1.0, 2.0, 3.0], width=10)
    assert len(out) == 3


# --------------------------------------------------------------------------- #
# bar_chart
# --------------------------------------------------------------------------- #


def test_bar_chart_full():
    assert bar_chart(100, 100, 10) == "█" * 10


def test_bar_chart_half():
    bar = bar_chart(50, 100, 10)
    assert bar.count("█") == 5
    assert bar.count("░") == 5
    assert len(bar) == 10


def test_bar_chart_zero():
    assert bar_chart(0, 100, 10) == "░" * 10


def test_bar_chart_zero_max_returns_all_empty():
    # Documented behaviour: max_val <= 0 → all empty glyphs (NOT "").
    bar = bar_chart(50, 0, 20)
    assert bar == "░" * 20


def test_bar_chart_clamps_overflow():
    # value > max_val must still be clamped to width.
    assert bar_chart(500, 100, 8) == "█" * 8
