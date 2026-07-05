"""Direct tests for the pure timestamp helpers in ``transcript_format``.

These cover the ISO-parsing / timestamp-formatting primitives the
``TranscriptExporter`` consumes (:func:`_parse_iso`, :func:`_fmt_ts`,
:func:`_fmt_duration`).  They previously had no direct coverage - only the
transitive exercise through ``export_transcript`` with ``.isoformat()`` inputs
- so the ``Z``-suffix path and the unparseable-string fallback were untested.

The regression of note: parsing now delegates to the shared
``agentlens._utils.parse_iso``, which normalises a trailing ``Z`` to
``+00:00`` before ``fromisoformat``.  The old local copy called bare
``datetime.fromisoformat``, which rejects ``Z`` on Python 3.9/3.10 and so
silently dropped common UTC timestamps to ``None`` / the raw string.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agentlens.transcript_format import _fmt_duration, _fmt_ts, _parse_iso


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


def test_parse_iso_accepts_z_suffix():
    # The bug fix: a trailing "Z" must parse to an aware UTC datetime on every
    # supported Python (3.9/3.10 bare fromisoformat would reject this).
    dt = _parse_iso("2025-01-01T00:00:00Z")
    assert dt == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_parse_iso_accepts_z_suffix_with_microseconds():
    dt = _parse_iso("2025-06-15T12:34:56.123456Z")
    assert dt == datetime(2025, 6, 15, 12, 34, 56, 123456, tzinfo=timezone.utc)


def test_parse_iso_accepts_explicit_offset():
    dt = _parse_iso("2025-01-01T00:00:00+00:00")
    assert dt == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_passes_datetime_through_by_identity():
    # An existing datetime must be returned unchanged (same object), so the
    # unguarded call sites in transcript.py keep exact tz/precision.
    d = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    assert _parse_iso(d) is d


def test_parse_iso_returns_none_for_none_and_empty():
    assert _parse_iso(None) is None
    assert _parse_iso("") is None


def test_parse_iso_returns_none_for_unparseable_and_non_string():
    assert _parse_iso("not-a-timestamp") is None
    assert _parse_iso(12345) is None
    assert _parse_iso(["2025-01-01T00:00:00Z"]) is None


# ---------------------------------------------------------------------------
# _fmt_ts
# ---------------------------------------------------------------------------


def test_fmt_ts_none_is_unknown():
    assert _fmt_ts(None) == "unknown"


def test_fmt_ts_formats_datetime():
    assert _fmt_ts(datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)) == "2026-06-05 10:00 UTC"


def test_fmt_ts_formats_z_suffixed_string():
    # Was degrading to the raw string on 3.9/3.10 before the shared-parser swap.
    assert _fmt_ts("2025-01-01T00:00:00Z") == "2025-01-01 00:00 UTC"


def test_fmt_ts_returns_raw_string_when_unparseable():
    # Fallback behaviour preserved: an unparseable string comes back verbatim.
    assert _fmt_ts("garbage") == "garbage"


# ---------------------------------------------------------------------------
# _fmt_duration (exercises _fmt_ts + the seconds/minutes rounding)
# ---------------------------------------------------------------------------


def test_fmt_duration_unknown_start():
    assert _fmt_duration(None, datetime(2026, 1, 1, tzinfo=timezone.utc)) == "unknown"


def test_fmt_duration_in_progress_when_no_end():
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert _fmt_duration(start, None) == "2026-01-01 00:00 UTC -> (in progress)"


def test_fmt_duration_seconds_under_ninety():
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
    assert _fmt_duration(start, end) == "2026-01-01 00:00 UTC -> 2026-01-01 00:00 UTC (30 seconds)"


def test_fmt_duration_minutes_at_or_above_ninety():
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 5, 0, tzinfo=timezone.utc)
    assert _fmt_duration(start, end).endswith("(5 minutes)")
