"""Tests for ``agentlens.cli_diff`` — side-by-side session comparison CLI.

Exercises:
    - Pure helpers: _pct_change, _color, _direction_indicator, _safe_get
    - Fetch helpers: _fetch_session, _fetch_costs, _fetch_events with success
      and exception paths (the latter two swallow errors and return safe
      defaults — that contract is tested here so refactors don't regress it).
    - cmd_diff: JSON output path (deterministic) and pretty-print path with
      and without colour, with empty + populated event/model breakdowns.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from agentlens import cli_diff


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestSafeGet:
    def test_returns_first_present_key(self):
        d = {"b": 2, "c": 3}
        assert cli_diff._safe_get(d, "a", "b", "c") == 2

    def test_default_when_missing(self):
        assert cli_diff._safe_get({}, "a", "b", default="x") == "x"

    def test_default_none(self):
        assert cli_diff._safe_get({}, "a") is None

    def test_first_match_wins(self):
        # 'a' present so 'b' is ignored even if it would also match
        assert cli_diff._safe_get({"a": 1, "b": 2}, "a", "b") == 1


class TestPctChange:
    def test_both_zero(self):
        assert cli_diff._pct_change(0, 0) == "—"

    def test_a_zero_b_nonzero(self):
        assert cli_diff._pct_change(0, 5) == "+∞"

    def test_increase(self):
        assert cli_diff._pct_change(100, 150) == "+50.0%"

    def test_decrease(self):
        # ((50-100)/100)*100 = -50.0 → no leading "+"
        assert cli_diff._pct_change(100, 50) == "-50.0%"

    def test_uses_absolute_denominator_for_negative_a(self):
        # ((10 - (-10)) / 10) * 100 = +200%
        assert cli_diff._pct_change(-10, 10) == "+200.0%"

    def test_no_change(self):
        # Zero change is not >0 so no leading '+' is added.
        assert cli_diff._pct_change(42, 42) == "0.0%"


class TestColor:
    def test_wraps_with_ansi(self):
        out = cli_diff._color("hi", "31")
        assert out.startswith("\033[31m") and out.endswith("\033[0m")
        assert "hi" in out


class TestDirectionIndicator:
    def test_equal_returns_eq(self):
        assert cli_diff._direction_indicator(5, 5) == "="

    def test_lower_is_better_decrease_is_green(self):
        out = cli_diff._direction_indicator(10, 5, lower_is_better=True)
        # Green = code 32
        assert "\033[32m" in out and "↓" in out

    def test_lower_is_better_increase_is_red(self):
        out = cli_diff._direction_indicator(5, 10, lower_is_better=True)
        assert "\033[31m" in out and "↑" in out

    def test_higher_is_better_increase_is_green(self):
        out = cli_diff._direction_indicator(5, 10, lower_is_better=False)
        assert "\033[32m" in out and "↑" in out

    def test_higher_is_better_decrease_is_red(self):
        out = cli_diff._direction_indicator(10, 5, lower_is_better=False)
        assert "\033[31m" in out and "↓" in out


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _ok(body):
    """Build a MagicMock response that returns *body* from .json()."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = body
    return resp


class TestFetchSession:
    def test_returns_json_body(self):
        client = MagicMock()
        client.get.return_value = _ok({"session_id": "s1", "agent_name": "a"})
        out = cli_diff._fetch_session(client, "s1")
        assert out["session_id"] == "s1"
        client.get.assert_called_once_with("/sessions/s1")

    def test_propagates_raise_for_status(self):
        client = MagicMock()
        bad = MagicMock()
        bad.raise_for_status.side_effect = RuntimeError("boom")
        client.get.return_value = bad
        with pytest.raises(RuntimeError):
            cli_diff._fetch_session(client, "s1")


class TestFetchCosts:
    def test_returns_dict(self):
        client = MagicMock()
        client.get.return_value = _ok({"total_cost": 0.42})
        assert cli_diff._fetch_costs(client, "s1") == {"total_cost": 0.42}

    def test_swallows_exception_and_returns_empty(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError("network down")
        assert cli_diff._fetch_costs(client, "s1") == {}

    def test_swallows_raise_for_status(self):
        client = MagicMock()
        bad = MagicMock()
        bad.raise_for_status.side_effect = RuntimeError("404")
        client.get.return_value = bad
        assert cli_diff._fetch_costs(client, "s1") == {}


class TestFetchEvents:
    def test_returns_list_directly(self):
        events = [{"event_type": "llm_call"}, {"event_type": "tool_call"}]
        client = MagicMock()
        client.get.return_value = _ok(events)
        assert cli_diff._fetch_events(client, "s1") == events

    def test_unwraps_dict_with_events_key(self):
        client = MagicMock()
        client.get.return_value = _ok({"events": [{"event_type": "x"}]})
        out = cli_diff._fetch_events(client, "s1")
        assert out == [{"event_type": "x"}]

    def test_dict_without_events_key_returns_empty(self):
        client = MagicMock()
        client.get.return_value = _ok({"meta": "no events here"})
        assert cli_diff._fetch_events(client, "s1") == []

    def test_swallows_exception(self):
        client = MagicMock()
        client.get.side_effect = RuntimeError("oops")
        assert cli_diff._fetch_events(client, "s1") == []

    def test_passes_limit_in_params(self):
        client = MagicMock()
        client.get.return_value = _ok([])
        cli_diff._fetch_events(client, "s1", limit=99)
        _, kwargs = client.get.call_args
        assert kwargs["params"] == {"session_id": "s1", "limit": 99}


# ---------------------------------------------------------------------------
# cmd_diff
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    defaults = dict(
        endpoint="http://localhost:3000",
        api_key="test",
        session_a="aaaaaaaaaaaa1111",
        session_b="bbbbbbbbbbbb2222",
        json_output=False,
        no_color=True,
        label_a=None,
        label_b=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _install_client(mock_get_client, session_a, session_b, events_a, events_b,
                    costs_a=None, costs_b=None):
    client = MagicMock()
    mock_get_client.return_value = (client, "http://localhost:3000")

    def fake_get(url, **kwargs):
        if url == f"/sessions/{session_a['session_id']}":
            return _ok(session_a)
        if url == f"/sessions/{session_b['session_id']}":
            return _ok(session_b)
        if url == f"/sessions/{session_a['session_id']}/costs":
            return _ok(costs_a or {})
        if url == f"/sessions/{session_b['session_id']}/costs":
            return _ok(costs_b or {})
        if url == "/events":
            sid = kwargs.get("params", {}).get("session_id")
            return _ok(events_a if sid == session_a["session_id"] else events_b)
        raise AssertionError(f"unexpected GET {url}")

    client.get.side_effect = fake_get
    return client


@patch("agentlens.cli_diff.get_client")
def test_cmd_diff_json_output_contains_all_sections(mock_gc, capsys):
    sa = {"session_id": "sa", "agent_name": "alpha", "status": "completed",
          "total_tokens": 100, "tokens_in": 60, "tokens_out": 40,
          "duration_ms": 1000, "event_count": 2}
    sb = {"session_id": "sb", "agent_name": "beta", "status": "completed",
          "total_tokens": 200, "tokens_in": 120, "tokens_out": 80,
          "duration_ms": 1500, "event_count": 3}
    events_a = [
        {"event_type": "llm_call", "model": "gpt-4o"},
        {"event_type": "tool_call", "model": "gpt-4o"},
    ]
    events_b = [
        {"event_type": "llm_call", "model": "gpt-4o-mini"},
        {"event_type": "error", "model": "gpt-4o-mini"},
        {"event_type": "tool_call", "model": "gpt-4o-mini"},
    ]
    _install_client(mock_gc, sa, sb, events_a, events_b,
                    costs_a={"total_cost": 0.01}, costs_b={"total_cost": 0.05})

    args = _make_args(session_a="sa", session_b="sb", json_output=True)
    cli_diff.cmd_diff(args)
    out = capsys.readouterr().out
    parsed = json.loads(out)

    assert parsed["session_a"] == "sa"
    assert parsed["session_b"] == "sb"
    assert parsed["metrics_a"]["total_tokens"] == 100
    assert parsed["metrics_b"]["total_tokens"] == 200
    assert parsed["metrics_b"]["errors"] == 1
    assert parsed["event_types_a"]["llm_call"] == 1
    assert parsed["event_types_b"]["error"] == 1
    assert parsed["models_a"]["gpt-4o"] == 2
    assert parsed["models_b"]["gpt-4o-mini"] == 3


@patch("agentlens.cli_diff.get_client")
def test_cmd_diff_pretty_no_color_includes_breakdowns(mock_gc, capsys):
    sa = {"session_id": "sa", "agent_name": "alpha", "status": "completed",
          "total_tokens": 50}
    sb = {"session_id": "sb", "agent_name": "beta", "status": "completed",
          "total_tokens": 75}
    events_a = [{"event_type": "llm_call", "model": "gpt-4o"}]
    events_b = [{"event_type": "tool_call", "model": "gpt-4o-mini"}]
    _install_client(mock_gc, sa, sb, events_a, events_b)

    args = _make_args(session_a="sa", session_b="sb", json_output=False,
                      no_color=True, label_a="A", label_b="B")
    cli_diff.cmd_diff(args)
    out = capsys.readouterr().out

    assert "Session Diff" in out
    assert "Total Tokens" in out
    assert "Event Type Breakdown" in out
    assert "Model Usage" in out
    assert "llm_call" in out
    assert "tool_call" in out
    # no_color=True → no ANSI escapes for the direction indicator
    assert "\033[31m" not in out
    assert "\033[32m" not in out


@patch("agentlens.cli_diff.get_client")
def test_cmd_diff_pretty_omits_breakdowns_when_empty(mock_gc, capsys):
    sa = {"session_id": "sa", "agent_name": "alpha", "status": "completed"}
    sb = {"session_id": "sb", "agent_name": "beta", "status": "completed"}
    _install_client(mock_gc, sa, sb, [], [])

    args = _make_args(session_a="sa", session_b="sb", no_color=True)
    cli_diff.cmd_diff(args)
    out = capsys.readouterr().out
    assert "Event Type Breakdown" not in out
    assert "Model Usage" not in out


@patch("agentlens.cli_diff.get_client")
def test_cmd_diff_pretty_uses_truncated_session_id_when_no_labels(mock_gc, capsys):
    sa = {"session_id": "abcdefghij1234", "agent_name": "a", "status": "ok"}
    sb = {"session_id": "zyxwvutsrq9876", "agent_name": "b", "status": "ok"}
    _install_client(mock_gc, sa, sb, [], [])

    args = _make_args(session_a="abcdefghij1234", session_b="zyxwvutsrq9876")
    cli_diff.cmd_diff(args)
    out = capsys.readouterr().out
    # Default label = first 12 chars of session id
    assert "abcdefghij12" in out
    assert "zyxwvutsrq98" in out


@patch("agentlens.cli_diff.get_client")
def test_cmd_diff_counts_errors_via_event_type(mock_gc, capsys):
    sa = {"session_id": "sa", "agent_name": "a", "status": "ok"}
    sb = {"session_id": "sb", "agent_name": "b", "status": "ok"}
    events_b = [
        {"event_type": "error"},
        {"event_type": "exception"},
        {"event_type": "llm_call"},
    ]
    _install_client(mock_gc, sa, sb, [], events_b)

    args = _make_args(session_a="sa", session_b="sb", json_output=True)
    cli_diff.cmd_diff(args)
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["metrics_a"]["errors"] == 0
    assert parsed["metrics_b"]["errors"] == 2
