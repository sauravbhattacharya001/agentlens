"""Shared internal utilities for the AgentLens SDK.

Small helpers that are used by multiple modules (e.g. ``exporter``,
``cli_common``) live here to avoid copy-paste duplication.
"""

from __future__ import annotations

from typing import Any

__all__ = ["format_duration"]


def format_duration(ms: Any) -> str:
    """Format milliseconds into a human-readable duration string.

    Returns ``"—"`` for ``None`` values.  Handles the full range from
    sub-second to hours::

        >>> format_duration(42)
        '42ms'
        >>> format_duration(1500)
        '1.5s'
        >>> format_duration(90_000)
        '1.5m'
        >>> format_duration(7_200_000)
        '2.0h'
        >>> format_duration(None)
        '—'
    """
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
