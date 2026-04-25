"""Shared internal utilities for the AgentLens SDK.

Small helpers that are used by multiple modules (e.g. ``exporter``,
``cli_common``) live here to avoid copy-paste duplication.
"""

from __future__ import annotations

import re
import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Pattern

__all__ = ["format_duration", "new_id", "parse_iso", "parse_iso_or_epoch", "safe_compile", "safe_search", "percentile", "utcnow"]


def new_id(length: int = 12) -> str:
    """Return a random hex identifier of the given *length*.

    Consolidates the previously duplicated ``_new_id`` helpers from
    ``budget``, ``cost_optimizer``, ``latency``, ``models``, and ``span``.
    """
    return uuid.uuid4().hex[:length]


def utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Centralised helper so every module uses the same clock source.
    This also makes it easy to monkey-patch in tests for deterministic
    timestamps.
    """
    return datetime.now(timezone.utc)


def parse_iso(value: "str | Any") -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Handles the common ``"Z"`` suffix (replacing it with ``"+00:00"``) and
    gracefully returns ``None`` for ``None``, empty strings, or unparseable
    values.  Consolidates the duplicated ``datetime.fromisoformat(…
    .replace("Z", "+00:00"))`` pattern previously copy-pasted across
    nine CLI / analytics modules.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

def parse_iso_or_epoch(value: "str | int | float | datetime | Any") -> Optional[datetime]:
    """Parse an ISO-8601 string *or* numeric epoch timestamp into a datetime.

    Handles:
    - ISO-8601 strings (with or without ``Z`` suffix)
    - ``datetime`` objects (returned as-is)
    - Numeric timestamps: seconds since epoch (< 1e12) or
      milliseconds since epoch (>= 1e12)

    Returns ``None`` for ``None``, empty strings, or unparseable values.

    This consolidates the duplicated ``_parse_ts`` helpers previously
    copy-pasted across ``cli_digest``, ``cli_retention``, ``cli_replay``,
    ``flamegraph``, and ``postmortem``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            epoch = value / 1000 if value > 1e12 else value
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    return parse_iso(value)


# ---------------------------------------------------------------------------
# ReDoS-safe regex helpers (CWE-1333)
# ---------------------------------------------------------------------------

_REGEX_TIMEOUT_S = 2  # max seconds for a single regex operation


def safe_compile(pattern: str, flags: int = 0) -> Optional[Pattern[str]]:
    """Compile a regex pattern, returning *None* on invalid syntax.

    This is a thin wrapper around :func:`re.compile` that swallows
    :class:`re.error` so callers don't need their own try/except.
    """
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def safe_search(
    pattern: "Pattern[str] | str",
    text: str,
    flags: int = 0,
    timeout: float = _REGEX_TIMEOUT_S,
) -> Optional[re.Match[str]]:
    """Run :func:`re.search` with a wall-clock timeout guard.

    On POSIX systems (Linux / macOS) this uses ``SIGALRM`` to abort
    catastrophic backtracking.  On Windows (no ``SIGALRM``), the
    function still executes the search but caps *text* length to
    100 000 characters as a heuristic safeguard — enough for any
    reasonable input while preventing multi-second stalls on
    pathological data.

    Returns the match object or *None* (no match **or** timed out).
    """
    compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, flags)

    if sys.platform != "win32" and hasattr(signal, "SIGALRM"):
        def _alarm_handler(signum: int, frame: Any) -> None:  # pragma: no cover
            raise TimeoutError("regex search timed out")

        old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(int(timeout) or 1)
        try:
            return compiled.search(text)
        except TimeoutError:
            return None
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows fallback — cap input length to prevent worst-case backtracking.
        truncated = text[:100_000]
        try:
            return compiled.search(truncated)
        except (RecursionError, MemoryError):
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
