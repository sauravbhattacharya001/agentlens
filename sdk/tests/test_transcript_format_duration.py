"""Direct branch tests for ``transcript_format._finite_duration_ms``.

This coercion guards the ``RunMetadata.durationMs`` that
:meth:`agentlens.transcript.TranscriptExporter.export_metadata` emits.  A raw
``duration_ms`` comes straight from instrumentation, so it can be non-numeric,
non-finite (``NaN``/``inf``), or negative.  Passing any of those through would
either raise (non-numeric) or poison ``json.dumps`` (which serialises
``NaN``/``Infinity`` as bare tokens - invalid JSON that breaks any RunMetadata
consumer such as agent-eval's verification check).

These branches were only ever exercised transitively through
``export_metadata``; none of them were pinned directly, so the ``None``
fall-through contract (return ``None`` so the caller can derive from start/end)
and the negative-clamp / bool-coercion edges were untested.
"""

from __future__ import annotations

from agentlens.transcript import export_run_metadata
from agentlens.transcript_format import _finite_duration_ms


# ---------------------------------------------------------------------------
# Values that must yield None (so the caller falls back to start/end)
# ---------------------------------------------------------------------------


def test_none_returns_none():
    assert _finite_duration_ms(None) is None


def test_non_numeric_string_returns_none():
    # float("abc") raises ValueError -> caught -> None (no crash).
    assert _finite_duration_ms("abc") is None


def test_non_numeric_object_returns_none():
    # float(object()) raises TypeError -> caught -> None.
    assert _finite_duration_ms(object()) is None


def test_nan_returns_none():
    # NaN would serialise as the bare token ``NaN`` (invalid JSON).
    assert _finite_duration_ms(float("nan")) is None


def test_positive_infinity_returns_none():
    assert _finite_duration_ms(float("inf")) is None


def test_negative_infinity_returns_none():
    assert _finite_duration_ms(float("-inf")) is None


# ---------------------------------------------------------------------------
# Values that must yield a safe, non-negative, finite float
# ---------------------------------------------------------------------------


def test_negative_is_clamped_to_zero():
    # A negative wall-clock is meaningless; clamp to 0.0 rather than drop.
    assert _finite_duration_ms(-5.0) == 0.0


def test_zero_stays_zero():
    assert _finite_duration_ms(0) == 0.0


def test_positive_float_passes_through():
    assert _finite_duration_ms(1234.5) == 1234.5


def test_numeric_string_is_parsed():
    assert _finite_duration_ms("42.5") == 42.5


def test_bool_true_coerces_to_one():
    # bool is an int subclass; float(True) == 1.0. Documented edge, not a crash.
    assert _finite_duration_ms(True) == 1.0


# ---------------------------------------------------------------------------
# End-to-end: the caller's fall-through when the coercion returns None
# ---------------------------------------------------------------------------


def test_run_metadata_uses_finite_duration_ms_when_valid():
    # An explicit, valid duration_ms is preferred over the start/end derive.
    meta = export_run_metadata(
        {
            "status": "completed",
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": "2026-01-01T00:05:00Z",
            "duration_ms": 4200.0,
        }
    )
    assert meta["durationMs"] == 4200.0


def test_run_metadata_falls_back_to_start_end_when_duration_non_finite():
    # A non-finite explicit duration must be ignored and the trusted
    # start/end-derived value used instead (5 minutes -> 300000 ms).
    meta = export_run_metadata(
        {
            "status": "completed",
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": "2026-01-01T00:05:00Z",
            "duration_ms": float("inf"),
        }
    )
    assert meta["durationMs"] == 300000.0
