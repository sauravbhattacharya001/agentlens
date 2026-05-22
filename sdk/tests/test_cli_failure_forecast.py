"""Tests for agentlens.cli_failure_forecast.

This module previously had 0% test coverage. Tests exercise:
  - subparser registration
  - JSON-line stdin parsing (valid + malformed rows)
  - predict path (single session + fleet)
  - fleet path
  - min-risk filtering
  - empty input early-exit
  - JSON vs text output formatting
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agentlens import cli_failure_forecast as cff
from agentlens.failure_forecast import (
    FailureForecaster,
    RiskLevel,
    SessionSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot_line(
    session_id: str = "sess-1",
    agent_id: str = "agent-a",
    ts: str = "2026-05-21T12:00:00+00:00",
    **kw,
) -> str:
    payload = {
        "session_id": session_id,
        "agent_id": agent_id,
        "timestamp": ts,
        "error_count": kw.get("error_count", 0),
        "total_events": kw.get("total_events", 10),
        "avg_latency_ms": kw.get("avg_latency_ms", 100.0),
        "retry_count": kw.get("retry_count", 0),
        "tool_failures": kw.get("tool_failures", 0),
        "tool_calls": kw.get("tool_calls", 5),
        "tokens_used": kw.get("tokens_used", 1000),
        "token_budget": kw.get("token_budget", 10000),
        "response_quality_score": kw.get("response_quality_score", 1.0),
        "consecutive_errors": kw.get("consecutive_errors", 0),
        "event_rate_per_min": kw.get("event_rate_per_min", 5.0),
    }
    return json.dumps(payload)


def _piped_stdin(lines):
    """Build a fake stdin object that is non-TTY and yields the given lines."""
    buf = io.StringIO("\n".join(lines) + "\n")
    # isatty defaults to False on StringIO, which is exactly what we want.
    return buf


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_adds_failure_forecast_command(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        cff.register(sub)

        # Parse the alias too
        args = parser.parse_args(["failure-forecast"])
        assert args.cmd == "failure-forecast"

        args = parser.parse_args(["ff"])
        assert args.cmd == "ff"

    def test_register_predict_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        cff.register(sub)

        args = parser.parse_args(
            [
                "failure-forecast", "predict",
                "--session", "sess-x",
                "--agent", "agent-y",
                "--min-risk", "high",
                "--json",
                "--min-snapshots", "5",
            ]
        )
        assert args.session == "sess-x"
        assert args.agent == "agent-y"
        assert args.min_risk == "high"
        assert args.json_output is True
        assert args.min_snapshots == 5

    def test_register_fleet_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        cff.register(sub)

        args = parser.parse_args(["failure-forecast", "fleet", "--json"])
        assert args.json_output is True

    def test_register_min_risk_invalid_choice_rejected(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        cff.register(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["failure-forecast", "predict", "--min-risk", "bogus"]
            )


# ---------------------------------------------------------------------------
# _load_snapshots()
# ---------------------------------------------------------------------------


class TestLoadSnapshots:
    def test_returns_empty_when_stdin_is_tty(self, monkeypatch):
        class _TTY:
            def isatty(self):
                return True

            def __iter__(self):
                return iter([])

        monkeypatch.setattr(sys, "stdin", _TTY())
        snaps = cff._load_snapshots(argparse.Namespace())
        assert snaps == []

    def test_parses_valid_jsonl(self, monkeypatch):
        lines = [
            _make_snapshot_line("s1"),
            _make_snapshot_line("s2", error_count=3),
        ]
        monkeypatch.setattr(sys, "stdin", _piped_stdin(lines))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert [s.session_id for s in snaps] == ["s1", "s2"]
        assert snaps[1].error_count == 3
        assert isinstance(snaps[0].timestamp, datetime)

    def test_skips_blank_lines(self, monkeypatch):
        lines = ["", _make_snapshot_line("s1"), "   ", _make_snapshot_line("s2")]
        monkeypatch.setattr(sys, "stdin", _piped_stdin(lines))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert len(snaps) == 2

    def test_skips_malformed_json(self, monkeypatch):
        lines = [
            "this is not json",
            _make_snapshot_line("s1"),
            "{broken",
        ]
        monkeypatch.setattr(sys, "stdin", _piped_stdin(lines))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert [s.session_id for s in snaps] == ["s1"]

    def test_skips_rows_missing_required_keys(self, monkeypatch):
        # No "timestamp" → KeyError → row dropped.
        lines = [
            json.dumps({"session_id": "x", "agent_id": "y"}),
            _make_snapshot_line("good"),
        ]
        monkeypatch.setattr(sys, "stdin", _piped_stdin(lines))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert [s.session_id for s in snaps] == ["good"]

    def test_skips_rows_with_invalid_timestamp(self, monkeypatch):
        lines = [
            json.dumps(
                {
                    "session_id": "x",
                    "agent_id": "y",
                    "timestamp": "not-a-date",
                }
            ),
            _make_snapshot_line("good"),
        ]
        monkeypatch.setattr(sys, "stdin", _piped_stdin(lines))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert [s.session_id for s in snaps] == ["good"]

    def test_defaults_applied_for_missing_optional_fields(self, monkeypatch):
        line = json.dumps(
            {
                "session_id": "s",
                "agent_id": "a",
                "timestamp": "2026-05-21T12:00:00+00:00",
            }
        )
        monkeypatch.setattr(sys, "stdin", _piped_stdin([line]))
        snaps = cff._load_snapshots(argparse.Namespace())
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.error_count == 0
        assert snap.tokens_used == 0
        assert snap.response_quality_score == 1.0


# ---------------------------------------------------------------------------
# _run_fleet() / _run_predict() / _run()
# ---------------------------------------------------------------------------


def _failing_snapshots():
    """Build a sequence of snapshots that should produce an elevated/high
    failure probability (lots of errors + retries + budget pressure)."""
    base = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(6):
        out.append(
            SessionSnapshot(
                session_id="sess-bad",
                agent_id="agent-z",
                timestamp=base.replace(minute=i * 5),
                error_count=10 + i,
                total_events=20,
                avg_latency_ms=5000.0,
                retry_count=4 + i,
                tool_failures=3 + i,
                tool_calls=5,
                tokens_used=9500 + i * 100,
                token_budget=10000,
                response_quality_score=0.2,
                consecutive_errors=4 + i,
                event_rate_per_min=1.0,
            )
        )
    return out


class TestRunFleet:
    def test_fleet_no_snapshots_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: [])
        args = argparse.Namespace(ff_action="fleet", json_output=False)
        with pytest.raises(SystemExit) as exc:
            cff._run(args)
        assert exc.value.code == 0
        assert "No session snapshots" in capsys.readouterr().out

    def test_fleet_text_output(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args = argparse.Namespace(ff_action="fleet", json_output=False)
        cff._run(args)
        out = capsys.readouterr().out
        assert "FAILURE FORECAST REPORT" in out

    def test_fleet_json_output_is_valid_json(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args = argparse.Namespace(ff_action="fleet", json_output=True)
        cff._run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "predictions" in data
        assert "fleet_health_score" in data

    def test_default_action_falls_back_to_fleet(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        # No ff_action attribute at all.
        args = argparse.Namespace(json_output=False)
        cff._run(args)
        out = capsys.readouterr().out
        assert "FAILURE FORECAST REPORT" in out


class TestRunPredict:
    def test_predict_no_snapshots_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: [])
        args = argparse.Namespace(
            ff_action="predict",
            session=None,
            min_risk="elevated",
            min_snapshots=3,
            json_output=False,
        )
        with pytest.raises(SystemExit) as exc:
            cff._run(args)
        assert exc.value.code == 0
        assert "No session snapshots" in capsys.readouterr().out

    def test_predict_specific_session_no_prediction(self, monkeypatch, capsys):
        # Forecaster returns None when the session is unknown.
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args = argparse.Namespace(
            ff_action="predict",
            session="does-not-exist",
            min_risk="elevated",
            min_snapshots=3,
            json_output=False,
        )
        with pytest.raises(SystemExit) as exc:
            cff._run(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "does-not-exist" in out
        assert "No prediction available" in out

    def test_predict_specific_session_with_prediction(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args = argparse.Namespace(
            ff_action="predict",
            session="sess-bad",
            min_risk="nominal",
            min_snapshots=3,
            json_output=True,
        )
        cff._run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["total_sessions_analyzed"] == 1
        assert isinstance(data["predictions"], list)

    def test_predict_fleet_with_min_risk_filter(self, monkeypatch, capsys):
        # Use very high min_risk so nothing matches the threshold and the
        # filter actually prunes predictions.
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args = argparse.Namespace(
            ff_action="predict",
            session=None,
            min_risk="imminent",
            min_snapshots=3,
            json_output=True,
        )
        cff._run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        threshold = RiskLevel("imminent").severity
        for p in data["predictions"]:
            assert RiskLevel(p["risk_level"]).severity >= threshold

    def test_predict_min_risk_nominal_keeps_everything(self, monkeypatch, capsys):
        monkeypatch.setattr(cff, "_load_snapshots", lambda args: _failing_snapshots())
        args_high = argparse.Namespace(
            ff_action="predict", session=None,
            min_risk="critical", min_snapshots=3, json_output=True,
        )
        cff._run(args_high)
        high_count = len(json.loads(capsys.readouterr().out)["predictions"])

        args_low = argparse.Namespace(
            ff_action="predict", session=None,
            min_risk="nominal", min_snapshots=3, json_output=True,
        )
        cff._run(args_low)
        low_count = len(json.loads(capsys.readouterr().out)["predictions"])

        # The relaxed filter should never drop *more* predictions than the
        # strict one.
        assert low_count >= high_count
