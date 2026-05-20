"""Tests for cli_bottleneck module."""

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_bottleneck import _percentile, _bar, _severity, cmd_bottleneck


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
    # ``_bar`` is ``cli_common.bar_chart``; documented behaviour for
    # ``max_val <= 0`` is to return an all-empty bar of the default width
    # (NOT an empty string). The legacy ``""`` expectation never matched
    # the implementation — it only passed when the import was broken.
    assert _bar(50, 0) == "\u2591" * 20


def test_severity_critical():
    assert "CRITICAL" in _severity(40)


def test_severity_high():
    assert "HIGH" in _severity(25)


def test_severity_medium():
    assert "MEDIUM" in _severity(10)


def test_severity_low():
    assert "LOW" in _severity(5)


# --------------------------------------------------------------------------- #
# cmd_bottleneck — integration tests with mocked HTTP client
# --------------------------------------------------------------------------- #


def _make_args(**overrides):
    defaults = dict(
        by="agent",
        metric="latency",
        limit=10,
        min_sessions=1,
        format="table",
        output=None,
        endpoint="http://test",
        api_key="k",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _stub_client(sessions, events_by_session):
    """Return a MagicMock httpx.Client whose .get() returns sessions/events."""
    client = MagicMock()

    def _get(path, params=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if path == "/api/sessions":
            resp.json.return_value = sessions
        elif path == "/api/events":
            sid = (params or {}).get("session", "")
            resp.json.return_value = events_by_session.get(sid, [])
        else:  # pragma: no cover — defensive
            resp.json.return_value = []
        return resp

    client.get.side_effect = _get
    return client


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_table_output_groups_by_agent(mock_get_client, capsys):
    sessions = [{"id": "s1"}, {"id": "s2"}]
    events = {
        "s1": [
            {"agent": "alpha", "duration_ms": 100, "cost": 0.1},
            {"agent": "alpha", "duration_ms": 200, "cost": 0.2},
            {"agent": "beta", "duration_ms": 50},
        ],
        "s2": [
            {"agent": "alpha", "duration_ms": 700, "cost": 0.7, "error": True},
        ],
    }
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args())
    out = capsys.readouterr().out

    assert "Bottleneck Analysis" in out
    # alpha (1000ms) >> beta (50ms), so alpha is ranked first.
    alpha_pos = out.find("alpha")
    beta_pos = out.find("beta")
    assert 0 <= alpha_pos < beta_pos
    # error rate column shows a non-zero value for alpha (1 of 3 events).
    assert "33.3%" in out or "33.3" in out


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_json_format_emits_valid_json(mock_get_client, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [
        {"agent": "alpha", "duration_ms": 100},
        {"agent": "beta", "duration_ms": 400},
    ]}
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args(format="json"))
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert isinstance(payload, list)
    keys = {row["key"] for row in payload}
    assert keys == {"alpha", "beta"}
    # beta has 80% of total latency (400 / 500)
    beta = next(r for r in payload if r["key"] == "beta")
    assert beta["pct_contribution"] == 80.0
    assert beta["sessions"] == 1
    assert beta["events"] == 1


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_group_by_model_uses_meta_fallback(mock_get_client, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [
        # Top-level model wins when present.
        {"model": "gpt-4", "duration_ms": 100},
        # Falls back to meta.model when top-level missing.
        {"meta": {"model": "claude"}, "duration_ms": 300},
        # Falls back to "unknown" when neither present.
        {"duration_ms": 50},
    ]}
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args(by="model", format="json"))
    payload = json.loads(capsys.readouterr().out)
    keys = {row["key"] for row in payload}
    assert keys == {"gpt-4", "claude", "unknown"}


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_metric_cost_ranks_by_dollars(mock_get_client, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [
        {"agent": "cheap", "duration_ms": 10000, "cost": 0.01},  # high latency, tiny $
        {"agent": "pricey", "duration_ms": 10, "cost_usd": 9.99},  # tiny latency, big $
    ]}
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args(metric="cost", format="json"))
    payload = json.loads(capsys.readouterr().out)
    # "pricey" should outrank "cheap" when ranked by cost.
    assert payload[0]["key"] == "pricey"
    assert payload[0]["pct_contribution"] > 99.0


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_metric_errors_counts_error_field_and_level(mock_get_client, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [
        {"agent": "a", "error": "boom"},
        {"agent": "a", "level": "error"},
        {"agent": "a"},
        {"agent": "b"},
    ]}
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args(metric="errors", format="json"))
    payload = json.loads(capsys.readouterr().out)
    a = next(r for r in payload if r["key"] == "a")
    assert a["error_count"] == 2
    assert a["pct_contribution"] == 100.0


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_min_sessions_filter(mock_get_client, capsys):
    sessions = [{"id": "s1"}, {"id": "s2"}]
    events = {
        "s1": [{"agent": "alpha", "duration_ms": 100}],
        "s2": [{"agent": "alpha", "duration_ms": 100}, {"agent": "beta", "duration_ms": 100}],
    }
    mock_get_client.return_value = _stub_client(sessions, events)

    # beta only appears in 1 session; min_sessions=2 should drop it.
    cmd_bottleneck(_make_args(min_sessions=2, format="json"))
    payload = json.loads(capsys.readouterr().out)
    keys = {row["key"] for row in payload}
    assert keys == {"alpha"}


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_handles_envelope_response_shape(mock_get_client, capsys):
    """Backend may return {"sessions": [...]} or {"events": [...]} envelopes."""
    client = MagicMock()

    def _get(path, params=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if path == "/api/sessions":
            resp.json.return_value = {"sessions": [{"session_id": "s1"}]}
        else:
            resp.json.return_value = {"events": [
                {"agent": "alpha", "duration_ms": 42},
            ]}
        return resp

    client.get.side_effect = _get
    mock_get_client.return_value = client

    cmd_bottleneck(_make_args(format="json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["key"] == "alpha"
    assert payload[0]["events"] == 1


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_no_data_exits_zero(mock_get_client, capsys):
    """Empty bucket after filter is a normal/no-op exit, not an error."""
    sessions = [{"id": "s1"}]
    events = {"s1": [{"agent": "alpha", "duration_ms": 1}]}
    mock_get_client.return_value = _stub_client(sessions, events)

    with pytest.raises(SystemExit) as exc_info:
        cmd_bottleneck(_make_args(min_sessions=99))
    assert exc_info.value.code == 0
    assert "No bottleneck data" in capsys.readouterr().err


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_sessions_http_error_exits_one(mock_get_client, capsys):
    import httpx

    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("boom")
    mock_get_client.return_value = client

    with pytest.raises(SystemExit) as exc_info:
        cmd_bottleneck(_make_args())
    assert exc_info.value.code == 1
    assert "Error fetching sessions" in capsys.readouterr().err


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_event_fetch_error_skipped_silently(mock_get_client, capsys):
    """Per-session event fetch failures must not abort the whole run."""
    import httpx

    client = MagicMock()

    def _get(path, params=None):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if path == "/api/sessions":
            resp.json.return_value = [{"id": "good"}, {"id": "bad"}]
        else:
            sid = (params or {}).get("session", "")
            if sid == "bad":
                raise httpx.ConnectError("per-session failure")
            resp.json.return_value = [{"agent": "alpha", "duration_ms": 5}]
        return resp

    client.get.side_effect = _get
    mock_get_client.return_value = client

    cmd_bottleneck(_make_args(format="json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["key"] == "alpha"


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_writes_to_output_file(mock_get_client, tmp_path, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [{"agent": "alpha", "duration_ms": 10}]}
    mock_get_client.return_value = _stub_client(sessions, events)

    out_file = tmp_path / "bottleneck.json"
    cmd_bottleneck(_make_args(format="json", output=str(out_file)))

    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload[0]["key"] == "alpha"
    # stdout should mention the write path, not the JSON body.
    assert str(out_file) in capsys.readouterr().out


@patch("agentlens.cli_bottleneck.get_client")
def test_cmd_bottleneck_respects_limit(mock_get_client, capsys):
    sessions = [{"id": "s1"}]
    events = {"s1": [
        {"agent": f"a{i}", "duration_ms": (i + 1) * 10} for i in range(5)
    ]}
    mock_get_client.return_value = _stub_client(sessions, events)

    cmd_bottleneck(_make_args(limit=2, format="json"))
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    # Largest two latencies are a4 (50ms) and a3 (40ms).
    assert payload[0]["key"] == "a4"
    assert payload[1]["key"] == "a3"
