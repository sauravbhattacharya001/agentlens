"""Tests for ``agentlens._utils`` — the shared internal helpers.

These helpers are imported by several core modules (``models``, ``span``,
``timeline``, ``flamegraph``, ``exporter``).  Bugs here ripple widely, so
they get thorough coverage.

Covers:
    - new_id(): default length, custom length, uniqueness, character set
    - utcnow(): timezone-awareness, monotonic-ish ordering
    - parse_iso(): Z suffix, +offset, naive, empty/None, garbage, non-strings
    - percentile(): empty, single value, exact ranks, interpolation,
      p=0 / p=100 endpoints
    - format_duration(): None, sub-second, seconds, minutes, hours,
      negative-ish edge values, string-coercible numerics
    - format_duration_seconds(): zero, sub-minute, minute+seconds, hour+minutes,
      negative clamp, boundary values, truncation of fractional seconds
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from agentlens import _utils


# ---------------------------------------------------------------------------
# new_id
# ---------------------------------------------------------------------------

class TestNewId:
    def test_default_length(self):
        assert len(_utils.new_id()) == 12

    @pytest.mark.parametrize("length", [1, 4, 8, 16, 32])
    def test_custom_length(self, length):
        assert len(_utils.new_id(length)) == length

    def test_hex_characters_only(self):
        assert re.fullmatch(r"[0-9a-f]+", _utils.new_id(32))

    def test_uniqueness(self):
        # 1k samples — collision probability is astronomically low
        ids = {_utils.new_id() for _ in range(1000)}
        assert len(ids) == 1000


# ---------------------------------------------------------------------------
# utcnow
# ---------------------------------------------------------------------------

class TestUtcNow:
    def test_returns_timezone_aware(self):
        now = _utils.utcnow()
        assert now.tzinfo is not None
        assert now.utcoffset() == timezone.utc.utcoffset(now)

    def test_close_to_system_clock(self):
        a = _utils.utcnow()
        b = datetime.now(timezone.utc)
        # Should be within a second of each other.
        assert abs((b - a).total_seconds()) < 1.0

    def test_monotonic_within_call(self):
        a = _utils.utcnow()
        b = _utils.utcnow()
        assert b >= a


# ---------------------------------------------------------------------------
# parse_iso
# ---------------------------------------------------------------------------

class TestParseIso:
    def test_z_suffix(self):
        dt = _utils.parse_iso("2025-06-15T12:34:56Z")
        assert dt == datetime(2025, 6, 15, 12, 34, 56, tzinfo=timezone.utc)

    def test_explicit_offset(self):
        dt = _utils.parse_iso("2025-06-15T12:34:56+00:00")
        assert dt == datetime(2025, 6, 15, 12, 34, 56, tzinfo=timezone.utc)

    def test_naive_iso(self):
        dt = _utils.parse_iso("2025-06-15T12:34:56")
        assert dt is not None
        assert dt.tzinfo is None

    def test_microseconds(self):
        dt = _utils.parse_iso("2025-06-15T12:34:56.123456Z")
        assert dt is not None and dt.microsecond == 123456

    @pytest.mark.parametrize("value", [None, "", "not a date", "2025-13-99", 12345])
    def test_unparseable_returns_none(self, value):
        # Pure-numeric input is not ISO; parse_iso should reject it.
        assert _utils.parse_iso(value) is None


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_empty(self):
        assert _utils.percentile([], 50) == 0.0

    def test_single_value(self):
        assert _utils.percentile([42.0], 99) == 42.0

    def test_p0_returns_min(self):
        assert _utils.percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0

    def test_p100_returns_max(self):
        assert _utils.percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0

    def test_median(self):
        # 5 elements, p50 → middle (index 2) by linear interpolation
        assert _utils.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0

    def test_interpolation(self):
        # p=25 on [0,1,2,3,4]: k = 0.25*4 = 1.0 → exactly index 1 → 1.0
        assert _utils.percentile([0.0, 1.0, 2.0, 3.0, 4.0], 25) == 1.0
        # p=37.5: k = 0.375*4 = 1.5 → halfway between 1.0 and 2.0
        assert _utils.percentile([0.0, 1.0, 2.0, 3.0, 4.0], 37.5) == 1.5

    def test_p_above_100_clamps_to_max(self):
        # p > 100 would send k past the last rank and raise IndexError; it is
        # clamped to p=100 → the maximum, identical to an in-range p100 call.
        data = [1.0, 2.0, 3.0, 4.0]
        assert _utils.percentile(data, 150) == _utils.percentile(data, 100) == 4.0

    def test_p_below_0_clamps_to_min(self):
        # p < 0 would make k negative and silently wrap to a negative index
        # (the wrong tail); it is clamped to p=0 → the minimum.
        data = [1.0, 2.0, 3.0, 4.0]
        assert _utils.percentile(data, -10) == _utils.percentile(data, 0) == 1.0

    def test_non_finite_p_treated_as_zero(self):
        # A NaN/inf rank must not poison the index math; treated as p=0.
        data = [1.0, 2.0, 3.0, 4.0]
        assert _utils.percentile(data, float("nan")) == 1.0
        assert _utils.percentile(data, float("inf")) == 1.0  # non-finite → p=0 → min
        assert _utils.percentile(data, float("-inf")) == 1.0

    def test_out_of_range_p_on_single_and_empty(self):
        # Empty/single-element short-circuits are unaffected by the clamp.
        assert _utils.percentile([], 150) == 0.0
        assert _utils.percentile([42.0], -5) == 42.0


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_none(self):
        assert _utils.format_duration(None) == "\u2014"

    def test_milliseconds(self):
        assert _utils.format_duration(42) == "42ms"
        assert _utils.format_duration(999) == "999ms"

    def test_seconds(self):
        assert _utils.format_duration(1500) == "1.5s"
        assert _utils.format_duration(59_999).endswith("s")

    def test_minutes(self):
        assert _utils.format_duration(90_000) == "1.5m"

    def test_hours(self):
        assert _utils.format_duration(7_200_000) == "2.0h"
        # Anything > 1h falls into the hours bucket.
        out = _utils.format_duration(25 * 3_600_000)
        assert out.endswith("h")

    def test_zero(self):
        assert _utils.format_duration(0) == "0ms"

    def test_string_numeric(self):
        # The helper coerces with float(), so numeric strings work.
        assert _utils.format_duration("250") == "250ms"

    def test_non_finite_returns_sentinel(self):
        # Durations come from span deltas; a malformed/absent span can yield
        # NaN or inf. These render as the same em-dash sentinel as None,
        # never a garbage "nanh"/"infh" string.
        assert _utils.format_duration(float("nan")) == "\u2014"
        assert _utils.format_duration(float("inf")) == "\u2014"
        assert _utils.format_duration(float("-inf")) == "\u2014"

    def test_negative_clamps_to_zero(self):
        # Consistent with format_duration_seconds: negatives clamp to zero
        # rather than emitting a "-5ms" string.
        assert _utils.format_duration(-5) == "0ms"
        assert _utils.format_duration(-90_000) == "0ms"

    def test_unparseable_input_returns_sentinel(self):
        # `ms` is typed `Any` and durations come from possibly-malformed span
        # data, so a non-numeric string or an unrelated object must not crash
        # the caller with the `ValueError`/`TypeError` that `float()` raises.
        # It returns the same em-dash sentinel as None (mirroring parse_iso's
        # tolerance of malformed input).
        assert _utils.format_duration("abc") == "\u2014"
        assert _utils.format_duration("") == "\u2014"
        assert _utils.format_duration(object()) == "\u2014"
        assert _utils.format_duration([1, 2]) == "\u2014"
        # A numeric string still parses (regression guard for the float() path).
        assert _utils.format_duration("250") == "250ms"


# ---------------------------------------------------------------------------
# format_duration_seconds (coarse Ns / Nm Ns / Nh Nm vocabulary)
# ---------------------------------------------------------------------------

class TestFormatDurationSeconds:
    def test_zero(self):
        # Unlike the empty-string guard in narrative_render.fmt_dur, the bare
        # helper renders zero as "0s"; callers add their own guard if needed.
        assert _utils.format_duration_seconds(0) == "0s"

    def test_sub_minute(self):
        assert _utils.format_duration_seconds(45) == "45s"
        assert _utils.format_duration_seconds(59) == "59s"

    def test_minute_boundary(self):
        assert _utils.format_duration_seconds(60) == "1m 0s"
        assert _utils.format_duration_seconds(125) == "2m 5s"

    def test_hour_boundary(self):
        assert _utils.format_duration_seconds(3600) == "1h 0m"
        assert _utils.format_duration_seconds(3725) == "1h 2m"

    def test_negative_clamps_to_zero(self):
        # Negative input is clamped rather than producing a "-1s" string.
        assert _utils.format_duration_seconds(-5) == "0s"

    def test_unparseable_input_clamps_to_zero(self):
        # `seconds` is derived from span deltas that may be malformed, so a
        # non-numeric string or an unrelated object must clamp to "0s" rather
        # than propagating the `ValueError`/`TypeError` that `float()` raises
        # (mirroring format_duration's sentinel and parse_iso's tolerance).
        assert _utils.format_duration_seconds("abc") == "0s"
        assert _utils.format_duration_seconds("") == "0s"
        assert _utils.format_duration_seconds(object()) == "0s"
        assert _utils.format_duration_seconds([1, 2]) == "0s"
        # A numeric string still parses (regression guard for the float() path).
        assert _utils.format_duration_seconds("45") == "45s"

    def test_truncates_fractional_seconds(self):
        # Whole-second granularity: fractional input is truncated, not rounded.
        assert _utils.format_duration_seconds(45.9) == "45s"
        assert _utils.format_duration_seconds(125.4) == "2m 5s"

    def test_non_finite_clamps_to_zero(self):
        # A malformed/absent span delta can yield a non-finite duration.
        # `int(float('nan'))` raises ValueError and `int(float('inf'))`
        # raises OverflowError, so the helper must clamp non-finite input to
        # `"0s"` (mirroring format_duration's non-finite handling) rather
        # than propagating the exception up through the narrative renderers.
        assert _utils.format_duration_seconds(float("nan")) == "0s"
        assert _utils.format_duration_seconds(float("inf")) == "0s"
        assert _utils.format_duration_seconds(float("-inf")) == "0s"