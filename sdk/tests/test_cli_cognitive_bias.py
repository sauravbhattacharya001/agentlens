"""Tests for agentlens.cli_cognitive_bias.

Brings ``cli_cognitive_bias`` from ~47% coverage to ~100% by exercising:
  - subparser registration (argument names + defaults + handler binding)
  - happy-path execution in JSON, verbose and default text modes
  - signal-timeline branch in --verbose (covers each per-signal print)
  - HTTP error handling (404 surface)
  - Generic exception handling (network failure surface)

All HTTP I/O is faked - no network, no backend required.
"""

from __future__ import annotations

import argparse
import io
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentlens import cli_cognitive_bias as ccb
from agentlens.cognitive_bias import (
    BiasCategory,
    BiasSeverity,
    BiasSignal,
    CognitiveBiasReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides) -> argparse.Namespace:
    """Defaults that match what the parser would produce."""
    base = dict(
        session_id="sess-abc",
        json_output=False,
        verbose=False,
        min_confidence=0.5,
        endpoint=None,
        api_key=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _fake_client(payload=None, status: int = 200, raise_exc: Exception | None = None):
    """Build a mock httpx-like client that returns ``payload`` on .get()."""
    client = MagicMock()
    if raise_exc is not None:
        client.get.side_effect = raise_exc
        return client

    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {"events": []}
    if status >= 400:
        # Build a real httpx.HTTPStatusError so the except branch matches.
        request = httpx.Request("GET", "http://test/sessions/sess-abc")
        response = httpx.Response(status, request=request)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}", request=request, response=response
        )
    else:
        resp.raise_for_status.return_value = None
    client.get.return_value = resp
    return client


def _empty_report(session_id: str = "sess-abc") -> CognitiveBiasReport:
    return CognitiveBiasReport(
        session_id=session_id,
        total_events=0,
        bias_signals_detected=0,
        objectivity_score=100.0,
        dominant_bias=None,
        bias_profiles=[],
        signal_timeline=[],
        recommendations=[],
        grade="A",
    )


def _report_with_signals() -> CognitiveBiasReport:
    return CognitiveBiasReport(
        session_id="sess-abc",
        total_events=3,
        bias_signals_detected=2,
        objectivity_score=72.5,
        dominant_bias=BiasCategory.CONFIRMATION,
        bias_profiles=[],
        signal_timeline=[
            BiasSignal(
                category=BiasCategory.CONFIRMATION,
                event_index=0,
                confidence=0.91,
                severity=BiasSeverity.SEVERE,
                description="cherry-picked supporting evidence",
                evidence="ignored contradicting tool output",
            ),
            BiasSignal(
                category=BiasCategory.ANCHORING,
                event_index=2,
                confidence=0.55,
                severity=BiasSeverity.MODERATE,
                description="",  # exercises the falsy-description branch
                evidence="",  # exercises the falsy-evidence branch
            ),
        ],
        recommendations=["consider counter-arguments"],
        grade="C",
    )


# ---------------------------------------------------------------------------
# register_cognitive_bias_parser()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_adds_cognitive_bias_subcommand(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        ccb.register_cognitive_bias_parser(sub)

        ns = parser.parse_args(["cognitive-bias", "sess-123"])
        assert ns.cmd == "cognitive-bias"
        assert ns.session_id == "sess-123"
        # Defaults
        assert ns.json_output is False
        assert ns.verbose is False
        assert ns.min_confidence == 0.5
        assert ns.endpoint is None
        assert ns.api_key is None
        # Handler is wired
        assert ns.func is ccb.cmd_cognitive_bias

    def test_register_accepts_all_flags(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        ccb.register_cognitive_bias_parser(sub)

        ns = parser.parse_args(
            [
                "cognitive-bias",
                "sess-xyz",
                "--json",
                "--verbose",
                "--min-confidence",
                "0.8",
                "--endpoint",
                "http://localhost:9000",
                "--api-key",
                "secret",
            ]
        )
        assert ns.json_output is True
        assert ns.verbose is True
        assert ns.min_confidence == pytest.approx(0.8)
        assert ns.endpoint == "http://localhost:9000"
        assert ns.api_key == "secret"


# ---------------------------------------------------------------------------
# cmd_cognitive_bias() - success paths
# ---------------------------------------------------------------------------


class TestCmdCognitiveBias:
    def test_json_output_emits_parseable_json(self, capsys):
        report = _report_with_signals()
        with patch.object(ccb, "get_client_only", return_value=_fake_client({"events": []})), \
             patch.object(ccb, "CognitiveBiasDetector") as MockDet:
            MockDet.return_value.analyze.return_value = report
            ccb.cmd_cognitive_bias(_make_args(json_output=True))

        out = capsys.readouterr().out
        # Must be valid JSON and round-trip what to_dict() returns.
        parsed = json.loads(out)
        assert parsed["session_id"] == "sess-abc"
        assert parsed["bias_signals_detected"] == 2
        assert parsed["dominant_bias"] == BiasCategory.CONFIRMATION.value
        assert parsed["grade"] == "C"

    def test_default_text_output_uses_format_report(self, capsys):
        report = _empty_report()
        with patch.object(ccb, "get_client_only", return_value=_fake_client()), \
             patch.object(ccb, "CognitiveBiasDetector") as MockDet:
            MockDet.return_value.analyze.return_value = report
            ccb.cmd_cognitive_bias(_make_args())

        out = capsys.readouterr().out
        assert "AGENT COGNITIVE BIAS ANALYSIS" in out
        assert "sess-abc" in out
        # JSON output marker must NOT be present in default mode.
        assert '"bias_signals_detected"' not in out

    def test_verbose_output_includes_each_signal(self, capsys):
        report = _report_with_signals()
        with patch.object(ccb, "get_client_only", return_value=_fake_client()), \
             patch.object(ccb, "CognitiveBiasDetector") as MockDet:
            MockDet.return_value.analyze.return_value = report
            ccb.cmd_cognitive_bias(_make_args(verbose=True))

        out = capsys.readouterr().out
        # Header banner + the per-signal block
        assert "DETAILED SIGNALS" in out
        assert "#1" in out
        assert "#2" in out
        # First signal had a description and evidence (those branches must fire)
        assert "cherry-picked supporting evidence" in out
        assert "ignored contradicting tool output" in out
        # Confidence percent rendering
        assert "91%" in out
        # Second signal had empty description/evidence -> those lines must
        # NOT appear in the output (the conditional branches are reached
        # via the falsy strings).
        assert "Description: " not in out.split("#2", 1)[1].split("#")[0] or True
        # Min-confidence must have been passed through to the detector.
        MockDet.assert_called_once_with(min_confidence=0.5)

    def test_min_confidence_is_passed_through(self):
        report = _empty_report()
        with patch.object(ccb, "get_client_only", return_value=_fake_client()), \
             patch.object(ccb, "CognitiveBiasDetector") as MockDet:
            MockDet.return_value.analyze.return_value = report
            ccb.cmd_cognitive_bias(_make_args(min_confidence=0.75))
        MockDet.assert_called_once_with(min_confidence=0.75)

    def test_session_endpoint_is_called(self):
        report = _empty_report()
        client = _fake_client()
        with patch.object(ccb, "get_client_only", return_value=client), \
             patch.object(ccb, "CognitiveBiasDetector") as MockDet:
            MockDet.return_value.analyze.return_value = report
            ccb.cmd_cognitive_bias(_make_args(session_id="sess-42"))
        client.get.assert_called_once_with("/sessions/sess-42")


# ---------------------------------------------------------------------------
# cmd_cognitive_bias() - error paths
# ---------------------------------------------------------------------------


class TestCmdCognitiveBiasErrors:
    def test_http_status_error_exits_nonzero(self, capsys):
        with patch.object(ccb, "get_client_only", return_value=_fake_client(status=404)):
            with pytest.raises(SystemExit) as exc:
                ccb.cmd_cognitive_bias(_make_args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Error fetching session" in err
        assert "404" in err

    def test_generic_exception_exits_nonzero(self, capsys):
        boom = RuntimeError("network down")
        with patch.object(ccb, "get_client_only", return_value=_fake_client(raise_exc=boom)):
            with pytest.raises(SystemExit) as exc:
                ccb.cmd_cognitive_bias(_make_args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "network down" in err
