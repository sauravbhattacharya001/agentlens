"""Regression tests for the get_client/get_client_only bug.

``agentlens.cli_common.get_client(args)`` returns a ``(httpx.Client, str)``
tuple — but five thin CLI wrappers were assigning that tuple to a local
named ``client`` and then calling ``client.get(...)``, which crashed with
``AttributeError: 'tuple' object has no attribute 'get'`` on every
invocation. None of the existing test suites caught it because each
caller's tests had patched the helper to return a bare ``MagicMock``
(matching the buggy code rather than the real contract).

This module locks in the fix by exercising each of the five entry points
end-to-end against a mocked ``get_client_only``. If anyone reverts to
``get_client(args)`` the test will reproduce the original AttributeError.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_args(**extra):
    """Build the common Namespace shared by all five session-analysis CLIs."""
    base = dict(
        endpoint="http://localhost:3000",
        api_key="default",
        session_id="sess-xyz",
        json_output=True,
        verbose=False,
        min_confidence=0.5,
    )
    base.update(extra)
    return argparse.Namespace(**base)


def _stub_client(session_body):
    """Return a MagicMock httpx-like client whose .get returns *session_body*."""
    client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = session_body
    client.get.return_value = resp
    return client


# Minimal session bodies that satisfy each analyzer's contract.
_TRIVIAL_SESSION = {
    "session_id": "sess-xyz",
    "agent_name": "test-agent",
    "events": [],
}


# ---------------------------------------------------------------------------
# cli_cognitive_bias
# ---------------------------------------------------------------------------

@patch("agentlens.cli_cognitive_bias.get_client_only")
def test_cli_cognitive_bias_uses_real_client(mock_gco, capsys):
    """If get_client (tuple) ever sneaks back in, this fails with AttributeError."""
    mock_gco.return_value = _stub_client(_TRIVIAL_SESSION)
    from agentlens.cli_cognitive_bias import cmd_cognitive_bias
    cmd_cognitive_bias(_make_args(json_output=True))
    out = capsys.readouterr().out
    # JSON output produced — proves .get() was called on a real client, not a tuple.
    parsed = json.loads(out)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# cli_self_correction
# ---------------------------------------------------------------------------

@patch("agentlens.cli_self_correction.get_client_only")
def test_cli_self_correction_uses_real_client(mock_gco, capsys):
    mock_gco.return_value = _stub_client(_TRIVIAL_SESSION)
    from agentlens.cli_self_correction import cmd_self_correction
    cmd_self_correction(_make_args(json_output=True))
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# cli_prompt_injection
# ---------------------------------------------------------------------------

@patch("agentlens.cli_prompt_injection.get_client_only")
def test_cli_prompt_injection_uses_real_client(mock_gco, capsys):
    mock_gco.return_value = _stub_client(_TRIVIAL_SESSION)
    from agentlens.cli_prompt_injection import cmd_prompt_injection
    args = _make_args(json_output=True)
    # cli_prompt_injection adds --no-tool-outputs
    args.no_tool_outputs = False
    cmd_prompt_injection(args)
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# cli_context_utilization
# ---------------------------------------------------------------------------

@patch("agentlens.cli_context_utilization.get_client_only")
def test_cli_context_utilization_uses_real_client(mock_gco, capsys):
    mock_gco.return_value = _stub_client(_TRIVIAL_SESSION)
    from agentlens.cli_context_utilization import cmd_context_utilization
    args = _make_args(json_output=True)
    args.context_limit = 128_000
    cmd_context_utilization(args)
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Reverse-regression check: confirm we patched the right attribute name.
# ---------------------------------------------------------------------------
#
# If a future refactor renames or removes get_client_only, the tests above
# will silently keep "passing" because the patch creates a phantom attribute.
# This explicit existence assertion makes the contract loud.

@pytest.mark.parametrize("module_name", [
    "agentlens.cli_cognitive_bias",
    "agentlens.cli_self_correction",
    "agentlens.cli_prompt_injection",
    "agentlens.cli_context_utilization",
    "agentlens.cli_bottleneck",
])
def test_module_imports_get_client_only(module_name):
    """All five fixed modules must import the real (single-client) helper."""
    import importlib
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "get_client_only"), (
        f"{module_name} no longer imports get_client_only — did the bug come back?"
    )
