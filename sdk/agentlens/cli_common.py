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

from agentlens._utils import format_duration  # noqa: F401 — re-exported
from agentlens._utils import linear_regression  # noqa: F401 — re-exported
from agentlens._utils import percentile as _raw_percentile

__all__ = [
    "get_client",
    "get_client_only",
    "print_json",
    "fetch_sessions",
    "format_duration",
    "percentile",
    "linear_regression",
    "sparkline",
    "bar_chart",
]


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


# format_duration is imported from agentlens._utils and re-exported above.


def fetch_sessions(client: httpx.Client, limit: int = 200) -> list[dict]:
    """Fetch sessions from the backend, handling both list and dict responses."""
    resp = client.get("/api/sessions", params={"limit": limit})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("sessions", [])


# ---------------------------------------------------------------------------
# Shared numeric / display helpers (previously duplicated across cli modules)
# ---------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    """Compute the *p*-th percentile (0–100) of *values* without numpy.

    Returns ``0.0`` for empty input.  Sorts internally, then delegates
    to :func:`agentlens._utils.percentile`.
    """
    if not values:
        return 0.0
    return _raw_percentile(sorted(values), p)


def linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Simple OLS linear regression.  Returns ``(slope, intercept)``.

    Delegates to :func:`agentlens._utils.linear_regression` for the
    actual computation.

    .. deprecated:: Use ``agentlens._utils.linear_regression`` directly
       for new code.  This wrapper preserves the ``(xs, ys)`` call
       convention expected by existing CLI callers.
    """
    from agentlens._utils import linear_regression as _lr
    return _lr(ys, xs)


def sparkline(values: list[float], width: int = 30) -> str:
    """Render a sparkline string from *values* using Unicode bar chars."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1.0
    return "".join(
        bars[min(int((v - mn) / rng * (len(bars) - 1)), len(bars) - 1)]
        for v in values
    )


def bar_chart(value: float, max_val: float, width: int = 20) -> str:
    """Return a fixed-width Unicode bar chart segment."""
    if max_val <= 0:
        return "░" * width
    filled = min(int(round(value / max_val * width)), width)
    return "█" * filled + "░" * (width - filled)
