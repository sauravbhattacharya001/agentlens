"""Tests for ``agentlens._metrics`` — shared session metric extraction.

``extract_session_metrics`` is the single-pass event scan used by both
the anomaly detector and the drift detector.  A subtle bug here would
silently corrupt every downstream signal in the SDK, so these tests
exhaustively cover:

* empty-session shape contract (all metric keys present, all zero)
* single happy-path event (every metric computed correctly)
* tool-event detection via both ``tool_call`` attribute *and* the
  ``"tool"`` substring in ``event_type``
* error classification via the ``"error"`` substring in ``event_type``
* combined tool + error events (``tool_failure_rate``)
* p95 latency rank at small and large N
* graceful handling of ``None`` token fields and missing attributes
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentlens._metrics import extract_session_metrics


def _session(events):
    return SimpleNamespace(events=events)


def _event(**kw):
    # Use SimpleNamespace so getattr(..., default) works naturally.
    return SimpleNamespace(**kw)


# --------------------------------------------------------------------------- #
# Empty / degenerate inputs
# --------------------------------------------------------------------------- #

EXPECTED_KEYS = {
    "event_count",
    "avg_latency_ms",
    "p95_latency_ms",
    "total_tokens",
    "tokens_per_event",
    "error_rate",
    "tool_call_rate",
    "tool_failure_rate",
}


def test_empty_session_returns_all_zero_metrics():
    m = extract_session_metrics(_session([]))
    assert set(m.keys()) == EXPECTED_KEYS
    for v in m.values():
        assert v == 0.0


def test_missing_events_attribute_treated_as_empty():
    # No ``events`` attribute at all → must not raise.
    m = extract_session_metrics(SimpleNamespace())
    assert m["event_count"] == 0.0
    assert m["avg_latency_ms"] == 0.0


def test_events_attribute_is_none():
    m = extract_session_metrics(SimpleNamespace(events=None))
    assert m["event_count"] == 0.0


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #


def test_avg_latency_simple():
    s = _session([_event(duration_ms=10), _event(duration_ms=30)])
    m = extract_session_metrics(s)
    assert m["avg_latency_ms"] == 20.0


def test_events_without_duration_are_excluded_from_latency():
    s = _session([_event(duration_ms=100), _event()])  # 2nd has no duration
    m = extract_session_metrics(s)
    # Avg considers only events that *have* a duration.
    assert m["avg_latency_ms"] == 100.0
    assert m["event_count"] == 2.0


def test_p95_at_small_n_matches_max_or_near_max():
    # 5 values → p95 idx = min(int(5*0.95), 4) = 4 → max.
    s = _session([_event(duration_ms=v) for v in (1, 2, 3, 4, 100)])
    m = extract_session_metrics(s)
    assert m["p95_latency_ms"] == 100


def test_p95_at_large_n():
    # 100 sequential values 1..100 → p95 idx = min(95, 99) = 95 → value 96.
    s = _session([_event(duration_ms=v) for v in range(1, 101)])
    m = extract_session_metrics(s)
    assert m["p95_latency_ms"] == 96


def test_no_durations_keeps_zero_latency():
    s = _session([_event(event_type="tick"), _event(event_type="tick")])
    m = extract_session_metrics(s)
    assert m["avg_latency_ms"] == 0.0
    assert m["p95_latency_ms"] == 0.0


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def test_total_tokens_sums_in_and_out():
    s = _session([
        _event(tokens_in=10, tokens_out=5),
        _event(tokens_in=2, tokens_out=3),
    ])
    m = extract_session_metrics(s)
    assert m["total_tokens"] == 20.0
    assert m["tokens_per_event"] == 10.0


def test_none_token_fields_treated_as_zero():
    s = _session([
        _event(tokens_in=None, tokens_out=None),
        _event(tokens_in=4, tokens_out=None),
    ])
    m = extract_session_metrics(s)
    assert m["total_tokens"] == 4.0
    assert m["tokens_per_event"] == 2.0


def test_missing_token_attrs_treated_as_zero():
    s = _session([_event(), _event()])
    m = extract_session_metrics(s)
    assert m["total_tokens"] == 0.0
    assert m["tokens_per_event"] == 0.0


# --------------------------------------------------------------------------- #
# Error & tool classification
# --------------------------------------------------------------------------- #


def test_error_rate_substring_match_is_case_insensitive():
    s = _session([
        _event(event_type="ToolError"),
        _event(event_type="normal"),
        _event(event_type="agent_ERROR_x"),
        _event(event_type="ok"),
    ])
    m = extract_session_metrics(s)
    assert m["error_rate"] == pytest.approx(0.5)


def test_tool_call_detected_via_attribute():
    s = _session([
        _event(event_type="custom", tool_call={"name": "search"}),
        _event(event_type="custom"),
    ])
    m = extract_session_metrics(s)
    assert m["tool_call_rate"] == 0.5


def test_tool_call_detected_via_event_type_substring():
    s = _session([
        _event(event_type="tool_invoke"),
        _event(event_type="agent_step"),
    ])
    m = extract_session_metrics(s)
    assert m["tool_call_rate"] == 0.5


def test_tool_failure_rate_is_tool_errors_over_tool_calls():
    s = _session([
        _event(event_type="tool_call"),        # tool, not error
        _event(event_type="tool_error"),       # tool + error
        _event(event_type="agent_error"),      # error but not a tool
        _event(event_type="agent_step"),       # neither
    ])
    m = extract_session_metrics(s)
    assert m["tool_call_rate"] == 0.5
    # Of the 2 tool events, 1 was an error.
    assert m["tool_failure_rate"] == 0.5
    # Of all 4 events, 2 were errors (tool_error + agent_error).
    assert m["error_rate"] == 0.5


def test_zero_tool_calls_gives_zero_tool_failure_rate_without_div_by_zero():
    s = _session([
        _event(event_type="agent_step"),
        _event(event_type="agent_error"),
    ])
    m = extract_session_metrics(s)
    assert m["tool_call_rate"] == 0.0
    assert m["tool_failure_rate"] == 0.0  # not NaN, not exception


# --------------------------------------------------------------------------- #
# Full happy-path smoke
# --------------------------------------------------------------------------- #


def test_full_metrics_shape_is_floats_and_complete():
    s = _session([
        _event(event_type="agent_step", duration_ms=50, tokens_in=10, tokens_out=5),
        _event(event_type="tool_call", duration_ms=100, tool_call={"name": "x"}),
        _event(event_type="tool_error", duration_ms=200, tool_call={"name": "y"}),
    ])
    m = extract_session_metrics(s)
    assert set(m.keys()) == EXPECTED_KEYS
    # event_count is stored as a float (per implementation contract).
    assert isinstance(m["event_count"], float)
    assert m["event_count"] == 3.0
    assert m["total_tokens"] == 15.0
