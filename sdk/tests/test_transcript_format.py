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


# ---------------------------------------------------------------------------
# _summarize (compact single-line rendering of an input/output value)
# ---------------------------------------------------------------------------


def test_summarize_none_is_empty():
    from agentlens.transcript_format import _summarize

    assert _summarize(None) == ""


def test_summarize_passes_short_string_through():
    from agentlens.transcript_format import _summarize

    assert _summarize("hello world") == "hello world"


def test_summarize_collapses_internal_whitespace_and_newlines():
    from agentlens.transcript_format import _summarize

    assert _summarize("a\tb\n\n  c   d") == "a b c d"


def test_summarize_serializes_non_string_json():
    from agentlens.transcript_format import _summarize

    # Falsy-but-not-None scalars must still render, not vanish.
    assert _summarize(0) == "0"
    assert _summarize(False) == "false"
    assert _summarize({"a": 1}) == '{"a": 1}'


def test_summarize_falls_back_to_str_for_unserializable():
    from agentlens.transcript_format import _summarize

    class NotJson:
        def __repr__(self):
            return "NOTJSON"

    assert _summarize(NotJson()) == "NOTJSON"


def test_summarize_truncates_long_text_to_200_chars_with_ellipsis():
    from agentlens.transcript_format import _summarize

    out = _summarize("x" * 500)
    assert len(out) == 200
    assert out.endswith("\u2026")
    assert out[:-1] == "x" * 199


def test_summarize_does_not_truncate_at_exactly_200_chars():
    from agentlens.transcript_format import _summarize

    text = "y" * 200
    assert _summarize(text) == text


# ---------------------------------------------------------------------------
# _get_tool (extract a tool_call dict from an event, incl. inline fields)
# ---------------------------------------------------------------------------


def test_get_tool_reads_nested_tool_call_dict():
    from agentlens.transcript_format import _get_tool

    tc = {"tool_name": "grep", "tool_input": {"q": "x"}}
    assert _get_tool({"tool_call": tc}) is tc


def test_get_tool_synthesizes_from_inline_fields():
    from agentlens.transcript_format import _get_tool

    got = _get_tool(
        {"tool_name": "bash", "tool_input": {"cmd": "ls"}, "tool_output": {"rc": 0}}
    )
    assert got == {
        "tool_name": "bash",
        "tool_input": {"cmd": "ls"},
        "tool_output": {"rc": 0},
    }


def test_get_tool_returns_none_when_no_tool_present():
    from agentlens.transcript_format import _get_tool

    assert _get_tool({"event_type": "llm_call"}) is None
    assert _get_tool({"tool_name": ""}) is None


def test_get_tool_ignores_non_dict_tool_call_and_uses_inline():
    from agentlens.transcript_format import _get_tool

    # A non-dict tool_call is skipped; inline tool_name still wins.
    got = _get_tool({"tool_call": "oops", "tool_name": "read"})
    assert got == {"tool_name": "read", "tool_input": None, "tool_output": None}


# ---------------------------------------------------------------------------
# _as_event_dict (normalize a model OR a plain dict into a dict)
# ---------------------------------------------------------------------------


def test_as_event_dict_passes_plain_dict_through():
    from agentlens.transcript_format import _as_event_dict

    d = {"event_type": "generic", "session_id": "s1"}
    assert _as_event_dict(d) is d


def test_as_event_dict_dumps_model_with_nested_tool_call():
    from agentlens.models import AgentEvent, ToolCall
    from agentlens.transcript_format import _as_event_dict, _get_tool

    ev = AgentEvent(
        event_type="tool_call",
        tool_call=ToolCall(tool_name="grep", tool_input={"q": "x"}),
    )
    out = _as_event_dict(ev)
    assert isinstance(out, dict)
    assert out["event_type"] == "tool_call"
    # The nested model dumped to a dict, so _get_tool can read it.
    tool = _get_tool(out)
    assert tool is not None
    assert tool["tool_name"] == "grep"
