"""Shared CLI utilities — DRY helpers used by all agentlens-cli sub-commands.

This module eliminates the duplicated ``_get_client`` / ``_print_json`` /
``_fetch_sessions`` helpers that were copy-pasted across every ``cli_*.py``
module.  Import from here instead of re-defining them locally.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx

__all__ = [
    "get_client",
    "get_client_only",
    "print_json",
    "fetch_sessions",
    "format_duration",
    "percentile",
]


def percentile(values: list[float], p: float) -> float:
    """Compute the *p*-th percentile (0–100) of *values* without numpy.

    Returns ``0.0`` for empty input.  Uses linear interpolation between
    the two nearest ranks.
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def get_client(args: argparse.Namespace) -> tuple[httpx.Client, str]:
    """Build an ``httpx.Client`` from parsed CLI args and return (client, endpoint).

    Resolution order for endpoint / api-key:
      1. Explicit CLI flag (``--endpoint`` / ``--api-key``)
      2. Environment variable (``AGENTLENS_ENDPOINT`` / ``AGENTLENS_API_KEY``)
      3. Built-in default (``http://localhost:3000`` / ``"default"``)
    """
    endpoint = (
        getattr(args, "endpoint", None)
        or os.environ.get("AGENTLENS_ENDPOINT", "http://localhost:3000")
    ).rstrip("/")
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("AGENTLENS_API_KEY", "default")
    )
    client = httpx.Client(
        base_url=endpoint,
        headers={"x-api-key": api_key},
        timeout=15.0,
    )
    return client, endpoint


def get_client_only(args: argparse.Namespace) -> httpx.Client:
    """Convenience wrapper that returns only the ``httpx.Client``."""
    client, _ = get_client(args)
    return client


def print_json(data: Any) -> None:
    """Pretty-print *data* as indented JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def format_duration(ms: Any) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms is None:
        return "\u2014"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    hours = mins / 60
    return f"{hours:.1f}h"


def fetch_sessions(client: httpx.Client, limit: int = 200) -> list[dict]:
    """Fetch sessions from the backend, handling both list and dict responses."""
    resp = client.get("/api/sessions", params={"limit": limit})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("sessions", [])
