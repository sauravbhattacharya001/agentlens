"""Shared internal utilities for the AgentLens SDK.

Small helpers used by multiple modules (e.g. ``models``, ``span``,
``timeline``, ``flamegraph``, ``exporter``) live here to avoid copy-paste
duplication.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

__all__ = [
    "format_duration",
    "format_duration_seconds",
    "new_id",
    "parse_iso",
    "percentile",
    "utcnow",
]


def new_id(length: int = 12) -> str:
    """Return a random hex identifier of the given *length*.

    Single source for ID generation across the SDK so every module mints
    identifiers the same way (used by ``models`` and ``span``).
    """
    return uuid.uuid4().hex[:length]


def utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Centralised helper so every module uses the same clock source.
    This also makes it easy to monkey-patch in tests for deterministic
    timestamps.
    """
    return datetime.now(timezone.utc)


def parse_iso(value: str | Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Handles the common ``"Z"`` suffix (replacing it with ``"+00:00"``) and
    gracefully returns ``None`` for ``None``, empty strings, or unparseable
    values.  Single home for the ``datetime.fromisoformat(...
    .replace("Z", "+00:00"))`` pattern (used by ``timeline`` and
    ``flamegraph``).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def percentile(sorted_values: list[float], p: float) -> float:
    """Compute the *p*-th percentile (0–100) of pre-sorted *values*.

    Uses linear interpolation between the two nearest ranks.
    Returns ``0.0`` for empty input.

    .. note:: *sorted_values* must already be sorted in ascending order.
       For unsorted input, use ``percentile(sorted(data), p)``.
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]
    k = (p / 100.0) * (n - 1)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def format_duration_seconds(seconds: float) -> str:
    """Format a whole-second duration as ``Ns`` / ``Nm Ns`` / ``Nh Nm``.

    This is the seconds-granularity sibling of :func:`format_duration` (which
    takes milliseconds and renders fractional ``1.5s`` / ``1.5m`` style
    strings).  The narrative renderer family wants the coarser
    ``"1m 30s"`` / ``"1h 5m"`` vocabulary instead, so this is the single home
    for that pattern (used by ``narrative_render`` and ``narrative_types``).

    The input is truncated to whole seconds.  Negative values are clamped to
    zero (yielding ``"0s"``); callers that prefer an empty string for
    non-positive input should guard before calling::

        >>> format_duration_seconds(45)
        '45s'
        >>> format_duration_seconds(125)
        '2m 5s'
        >>> format_duration_seconds(3725)
        '1h 2m'
    """
    s = int(seconds)
    if s < 0:
        s = 0
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"


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
