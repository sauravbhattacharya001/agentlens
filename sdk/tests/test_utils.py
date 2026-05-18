"""Tests for ``agentlens._utils`` — the shared internal helpers.

These helpers are touched by ~20+ modules across the SDK (every CLI,
the analytics layer, the trackers).  Until now they had no dedicated
test file — bugs here ripple everywhere, so they get the most coverage
treatment.

Covers:
    - new_id(): default length, custom length, uniqueness, character set
    - utcnow(): timezone-awareness, monotonic-ish ordering
    - parse_iso(): Z suffix, +offset, naive, empty/None, garbage, non-strings
    - parse_iso_or_epoch(): seconds vs milliseconds heuristic, datetime
      passthrough, invalid numerics
    - safe_compile(): valid + invalid patterns
    - safe_search(): match, no-match, ReDoS guard on Windows-style
      truncation, accepts pre-compiled patterns
    - linear_regression(): empty, single-point, perfect line, constant y,
      explicit x's, ordering matches y = m*x + b
    - percentile(): empty, single value, exact ranks, interpolation,
      p=0 / p=100 endpoints
    - format_duration(): None, sub-second, seconds, minutes, hours,
      negative-ish edge values, string-coercible numerics
"""
from __future__ import annotations

import math
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
# parse_iso_or_epoch
# ---------------------------------------------------------------------------

class TestParseIsoOrEpoch:
    def test_iso_string(self):
        dt = _utils.parse_iso_or_epoch("2025-06-15T12:34:56Z")
        assert dt == datetime(2025, 6, 15, 12, 34, 56, tzinfo=timezone.utc)

    def test_datetime_passthrough(self):
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        assert _utils.parse_iso_or_epoch(dt) is dt

    def test_epoch_seconds(self):
        # 1_700_000_000 = 2023-11-14 22:13:20 UTC
        dt = _utils.parse_iso_or_epoch(1_700_000_000)
        assert dt is not None
        assert dt.year == 2023 and dt.month == 11

    def test_epoch_milliseconds(self):
        # > 1e12 → interpreted as ms
        dt_ms = _utils.parse_iso_or_epoch(1_700_000_000_000)
        dt_s = _utils.parse_iso_or_epoch(1_700_000_000)
        assert dt_ms == dt_s

    def test_float_epoch(self):
        dt = _utils.parse_iso_or_epoch(1_700_000_000.5)
        assert dt is not None
        assert dt.microsecond == 500_000

    @pytest.mark.parametrize("value", [None, "", "garbage"])
    def test_invalid_returns_none(self, value):
        assert _utils.parse_iso_or_epoch(value) is None

    def test_overflow_epoch_returns_none(self):
        # Way beyond datetime's range — should not raise.
        assert _utils.parse_iso_or_epoch(1e30) is None


# ---------------------------------------------------------------------------
# safe_compile / safe_search
# ---------------------------------------------------------------------------

class TestSafeCompile:
    def test_valid_pattern(self):
        pat = _utils.safe_compile(r"\d+")
        assert pat is not None and pat.search("abc123").group(0) == "123"

    def test_invalid_pattern_returns_none(self):
        assert _utils.safe_compile(r"(unclosed") is None

    def test_flags_respected(self):
        pat = _utils.safe_compile(r"hello", re.IGNORECASE)
        assert pat is not None and pat.search("HELLO") is not None


class TestSafeSearch:
    def test_match(self):
        m = _utils.safe_search(r"foo(\d+)", "foo42")
        assert m is not None and m.group(1) == "42"

    def test_no_match(self):
        assert _utils.safe_search(r"foo", "bar") is None

    def test_accepts_precompiled(self):
        pat = re.compile(r"x+")
        assert _utils.safe_search(pat, "xxx") is not None

    def test_handles_very_long_input(self):
        # On Windows the helper caps input at 100k chars but should still
        # produce a sensible result for a benign pattern.
        text = "a" * 200_000 + "needle"
        # 'needle' lives past the 100k cap on Windows, so the match may be
        # None; on POSIX it should match.  Either way: no exception, no hang.
        result = _utils.safe_search(r"needle", text)
        assert result is None or result.group(0) == "needle"


# ---------------------------------------------------------------------------
# linear_regression
# ---------------------------------------------------------------------------

class TestLinearRegression:
    def test_empty(self):
        slope, intercept = _utils.linear_regression([])
        assert slope == 0.0 and intercept == 0.0

    def test_single_point(self):
        slope, intercept = _utils.linear_regression([7.0])
        assert slope == 0.0 and intercept == 7.0

    def test_perfect_line_default_x(self):
        # y = 2x + 1 → at x=0..3 yields [1, 3, 5, 7]
        slope, intercept = _utils.linear_regression([1.0, 3.0, 5.0, 7.0])
        assert math.isclose(slope, 2.0)
        assert math.isclose(intercept, 1.0)

    def test_perfect_line_explicit_x(self):
        slope, intercept = _utils.linear_regression(
            ys=[10.0, 20.0, 30.0],
            xs=[1.0, 2.0, 3.0],
        )
        assert math.isclose(slope, 10.0)
        assert math.isclose(intercept, 0.0)

    def test_constant_y(self):
        slope, intercept = _utils.linear_regression([5.0, 5.0, 5.0, 5.0])
        assert math.isclose(slope, 0.0, abs_tol=1e-9)
        assert math.isclose(intercept, 5.0)

    def test_zero_x_variance(self):
        # All xs equal → denominator==0 branch
        slope, intercept = _utils.linear_regression(
            ys=[1.0, 2.0, 3.0],
            xs=[5.0, 5.0, 5.0],
        )
        assert slope == 0.0
        assert math.isclose(intercept, 2.0)  # y_mean

    def test_negative_slope(self):
        slope, _ = _utils.linear_regression([10.0, 8.0, 6.0, 4.0])
        assert slope < 0


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
