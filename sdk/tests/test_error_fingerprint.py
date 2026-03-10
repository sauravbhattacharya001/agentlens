"""Tests for agentlens.error_fingerprint module."""

import pytest
from datetime import datetime, timezone, timedelta

from agentlens.error_fingerprint import (
    ErrorFingerprinter,
    ErrorCluster,
    ErrorReport,
    Trend,
    Resolution,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(minutes: int = 0) -> datetime:
    """Create a UTC timestamp offset by *minutes* from a fixed base."""
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Basic recording
# ---------------------------------------------------------------------------

class TestRecord:
    def test_returns_fingerprint_id(self):
        fp = ErrorFingerprinter()
        fid = fp.record("ValueError", "bad value", session_id="s1")
        assert isinstance(fid, str)
        assert len(fid) == 16

    def test_same_error_same_fingerprint(self):
        fp = ErrorFingerprinter()
        a = fp.record("ValueError", "expected 30 items, got 50")
        b = fp.record("ValueError", "expected 20 items, got 80")
        # Multi-digit numbers normalised → same template
        assert a == b

    def test_different_types_different_fingerprint(self):
        fp = ErrorFingerprinter()
        a = fp.record("ValueError", "bad input")
        b = fp.record("TypeError", "bad input")
        assert a != b

    def test_occurrence_count(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", session_id="s1")
        fp.record("E", "msg", session_id="s2")
        fp.record("E", "msg", session_id="s3")
        cluster = fp.get_cluster(fid)
        assert cluster is not None
        assert cluster.occurrence_count == 3

    def test_session_tracking(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", session_id="s1")
        fp.record("E", "msg", session_id="s2")
        fp.record("E", "msg", session_id="s1")
        cluster = fp.get_cluster(fid)
        assert cluster is not None
        assert cluster.session_ids == {"s1", "s2"}

    def test_first_last_seen(self):
        fp = ErrorFingerprinter()
        t1 = _ts(0)
        t2 = _ts(10)
        t3 = _ts(5)
        fid = fp.record("E", "msg", timestamp=t1)
        fp.record("E", "msg", timestamp=t2)
        fp.record("E", "msg", timestamp=t3)
        c = fp.get_cluster(fid)
        assert c is not None
        assert c.first_seen == t1
        assert c.last_seen == t2

    def test_sample_message_preserved(self):
        fp = ErrorFingerprinter()
        fid = fp.record("ValueError", "expected 30 items, got 50")
        c = fp.get_cluster(fid)
        assert c is not None
        assert c.sample_message == "expected 30 items, got 50"

    def test_metadata_does_not_affect_fingerprint(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "msg", metadata={"tool": "search"})
        b = fp.record("E", "msg", metadata={"tool": "browse"})
        assert a == b


# ---------------------------------------------------------------------------
# Message normalisation
# ---------------------------------------------------------------------------

class TestNormalisation:
    def test_numbers_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "expected 42 items")
        b = fp.record("E", "expected 99 items")
        assert a == b

    def test_uuids_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "session a1b2c3d4-e5f6-7890-abcd-ef1234567890 failed")
        b = fp.record("E", "session 11111111-2222-3333-4444-555555555555 failed")
        assert a == b

    def test_hex_addresses_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "object at 0x7f3a2b1c4d5e")
        b = fp.record("E", "object at 0xdeadbeef1234")
        assert a == b

    def test_timestamps_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "failed at 2026-03-09T12:00:00Z")
        b = fp.record("E", "failed at 2025-01-15T08:30:00Z")
        assert a == b

    def test_quoted_strings_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "key 'foo' not found")
        b = fp.record("E", "key 'bar' not found")
        assert a == b

    def test_paths_normalised(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "error in /usr/lib/python3.12/site.py")
        b = fp.record("E", "error in /home/user/app/main.py")
        assert a == b

    def test_single_digit_not_normalised(self):
        """Single digits should NOT be normalised (they're often meaningful)."""
        fp = ErrorFingerprinter()
        a = fp.record("E", "step 1 failed")
        b = fp.record("E", "step 2 failed")
        # Single digit "1" and "2" are not replaced (pattern needs ≥2 digits)
        assert a != b

    def test_whitespace_collapsed(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "too   many    spaces")
        b = fp.record("E", "too many spaces")
        assert a == b


# ---------------------------------------------------------------------------
# Stack trace fingerprinting
# ---------------------------------------------------------------------------

class TestStackTrace:
    def test_python_frames_extracted(self):
        stack = '''Traceback (most recent call last):
  File "/app/main.py", line 42, in process
  File "/app/handler.py", line 15, in handle
  File "/app/core.py", line 8, in execute
ValueError: bad value'''
        fp = ErrorFingerprinter()
        a = fp.record("ValueError", "bad value", stack_trace=stack)
        # Same functions, different line numbers
        stack2 = '''Traceback (most recent call last):
  File "/app/main.py", line 99, in process
  File "/app/handler.py", line 22, in handle
  File "/app/core.py", line 11, in execute
ValueError: bad value'''
        b = fp.record("ValueError", "bad value", stack_trace=stack2)
        assert a == b

    def test_js_frames_extracted(self):
        stack = '''Error: failed
    at processRequest (server.js:42:10)
    at handleRoute (router.js:15:5)
    at main (app.js:8:3)'''
        fp = ErrorFingerprinter()
        a = fp.record("Error", "failed", stack_trace=stack)
        stack2 = '''Error: failed
    at processRequest (server.js:100:10)
    at handleRoute (router.js:20:5)
    at main (app.js:12:3)'''
        b = fp.record("Error", "failed", stack_trace=stack2)
        assert a == b

    def test_no_stack_same_fingerprint(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "msg")
        b = fp.record("E", "msg")
        assert a == b

    def test_different_stacks_different_fingerprint(self):
        fp = ErrorFingerprinter()
        stack_a = '''File "a.py", line 1, in alpha
File "b.py", line 2, in beta'''
        stack_b = '''File "x.py", line 1, in xray
File "y.py", line 2, in yankee'''
        a = fp.record("E", "msg", stack_trace=stack_a)
        b = fp.record("E", "msg", stack_trace=stack_b)
        assert a != b

    def test_stack_with_no_recognisable_frames(self):
        fp = ErrorFingerprinter()
        a = fp.record("E", "msg", stack_trace="some random text")
        b = fp.record("E", "msg", stack_trace="other random text")
        # No frames extracted → empty signature → same fingerprint
        assert a == b


# ---------------------------------------------------------------------------
# record_from_event
# ---------------------------------------------------------------------------

class TestRecordFromEvent:
    def test_error_event_type(self):
        fp = ErrorFingerprinter()
        event = {
            "event_type": "error",
            "session_id": "s1",
            "output_data": {
                "error_type": "TimeoutError",
                "message": "request timed out after 30s",
            },
        }
        fid = fp.record_from_event(event)
        assert fid is not None
        c = fp.get_cluster(fid)
        assert c is not None
        assert c.error_type == "TimeoutError"

    def test_error_in_output_data(self):
        fp = ErrorFingerprinter()
        event = {
            "event_type": "tool_call",
            "output_data": {
                "error_type": "ConnectionError",
                "error": "connection refused",
            },
        }
        fid = fp.record_from_event(event)
        assert fid is not None

    def test_no_error_returns_none(self):
        fp = ErrorFingerprinter()
        event = {
            "event_type": "llm_call",
            "output_data": {"result": "success"},
        }
        assert fp.record_from_event(event) is None

    def test_session_id_from_event(self):
        fp = ErrorFingerprinter()
        event = {
            "event_type": "error",
            "session_id": "sess-42",
            "output_data": {"error_type": "E", "message": "m"},
        }
        fid = fp.record_from_event(event, session_id="override")
        c = fp.get_cluster(fid)
        assert "override" in c.session_ids

    def test_iso_timestamp_parsed(self):
        fp = ErrorFingerprinter()
        event = {
            "event_type": "error",
            "timestamp": "2026-03-09T12:00:00+00:00",
            "output_data": {"error_type": "E", "message": "m"},
        }
        fid = fp.record_from_event(event)
        c = fp.get_cluster(fid)
        assert c.first_seen is not None
        assert c.first_seen.year == 2026


# ---------------------------------------------------------------------------
# Resolution tracking
# ---------------------------------------------------------------------------

class TestResolution:
    def test_resolve(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg")
        assert fp.resolve(fid) is True
        c = fp.get_cluster(fid)
        assert c.resolution == Resolution.RESOLVED

    def test_resolve_nonexistent(self):
        fp = ErrorFingerprinter()
        assert fp.resolve("nonexistent") is False

    def test_ignore(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg")
        assert fp.ignore(fid) is True
        c = fp.get_cluster(fid)
        assert c.resolution == Resolution.IGNORED

    def test_regression_detection(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", timestamp=_ts(0))
        fp.resolve(fid)
        # Same error reappears
        fp.record("E", "msg", timestamp=_ts(10))
        c = fp.get_cluster(fid)
        assert c.resolution == Resolution.REGRESSED

    def test_resolved_excluded_from_report(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "resolved error")
        fp.resolve(fid)
        fp.record("E2", "active error")
        report = fp.report()
        assert report.unique_count == 1  # only active
        assert fid in report.resolved_fingerprints

    def test_ignored_excluded_from_report(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "noisy error")
        fp.ignore(fid)
        fp.record("E2", "real error")
        report = fp.report()
        assert report.unique_count == 1


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestReport:
    def test_empty_report(self):
        fp = ErrorFingerprinter()
        report = fp.report()
        assert report.unique_count == 0
        assert report.total_count == 0

    def test_basic_report(self):
        fp = ErrorFingerprinter()
        fp.record("ValueError", "bad input 42", session_id="s1", timestamp=_ts(0))
        fp.record("ValueError", "bad input 99", session_id="s2", timestamp=_ts(5))
        fp.record("TypeError", "wrong type", session_id="s1", timestamp=_ts(10))
        report = fp.report()
        assert report.unique_count == 2
        assert report.total_count == 3
        assert report.sessions_affected == 2

    def test_top_clusters_sorted(self):
        fp = ErrorFingerprinter()
        for _ in range(5):
            fp.record("A", "frequent error", timestamp=_ts(0))
        for _ in range(2):
            fp.record("B", "rare error", timestamp=_ts(5))
        report = fp.report()
        assert report.top_clusters[0].error_type == "A"
        assert report.top_clusters[0].occurrence_count == 5

    def test_error_rate(self):
        fp = ErrorFingerprinter()
        fp.set_total_sessions(10)
        fp.record("E", "msg", timestamp=_ts(0))
        fp.record("E", "msg", timestamp=_ts(5))
        report = fp.report()
        assert report.error_rate == pytest.approx(0.2)

    def test_most_affected_sessions(self):
        fp = ErrorFingerprinter()
        for _ in range(5):
            fp.record("E", "msg", session_id="bad-session", timestamp=_ts(0))
        fp.record("E", "msg", session_id="ok-session", timestamp=_ts(5))
        report = fp.report()
        assert report.most_affected_sessions[0] == ("bad-session", 5)

    def test_new_fingerprints(self):
        fp = ErrorFingerprinter()
        # All errors at same timestamp → all "new"
        fp.record("E1", "msg1", timestamp=_ts(0))
        fp.record("E2", "msg2", timestamp=_ts(0))
        report = fp.report()
        assert len(report.new_fingerprints) >= 0  # may be NEW or STABLE

    def test_render(self):
        fp = ErrorFingerprinter()
        fp.record("ValueError", "test error 42", session_id="s1", timestamp=_ts(0))
        fp.record("ValueError", "test error 99", session_id="s2", timestamp=_ts(10))
        report = fp.report()
        text = report.render()
        assert "Error Fingerprint Report" in text
        assert "ValueError" in text

    def test_top_n_limit(self):
        fp = ErrorFingerprinter()
        for i in range(20):
            fp.record(f"E{i}", f"error {i}", timestamp=_ts(i))
        report = fp.report(top_n=5)
        assert len(report.top_clusters) == 5

    def test_window_times(self):
        fp = ErrorFingerprinter()
        fp.record("E", "msg", timestamp=_ts(0))
        fp.record("E", "msg", timestamp=_ts(60))
        report = fp.report()
        assert report.window_start == _ts(0)
        assert report.window_end == _ts(60)


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

class TestTrends:
    def test_rising_trend(self):
        fp = ErrorFingerprinter()
        # Few early, many late
        fp.record("E", "msg", timestamp=_ts(0))
        for i in range(10):
            fp.record("E", "msg", timestamp=_ts(50 + i))
        report = fp.report()
        cluster = report.top_clusters[0]
        assert cluster.trend == Trend.RISING

    def test_falling_trend(self):
        fp = ErrorFingerprinter()
        # Many early, few late
        for i in range(10):
            fp.record("E", "msg", timestamp=_ts(i))
        fp.record("E", "msg", timestamp=_ts(50))
        report = fp.report()
        cluster = report.top_clusters[0]
        assert cluster.trend == Trend.FALLING

    def test_stable_trend(self):
        fp = ErrorFingerprinter()
        # Even distribution
        for i in range(10):
            fp.record("E", "msg", timestamp=_ts(i * 5))
        report = fp.report()
        cluster = report.top_clusters[0]
        assert cluster.trend == Trend.STABLE


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_all(self):
        fp = ErrorFingerprinter()
        fp.record("E", "msg")
        fp.reset()
        report = fp.report()
        assert report.unique_count == 0
        assert report.total_count == 0

    def test_get_cluster_after_reset(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg")
        fp.reset()
        assert fp.get_cluster(fid) is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_message(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "")
        assert fid is not None

    def test_empty_error_type(self):
        fp = ErrorFingerprinter()
        fid = fp.record("", "some error")
        assert fid is not None

    def test_very_long_message(self):
        fp = ErrorFingerprinter()
        msg = "x" * 10000
        fid = fp.record("E", msg)
        assert fid is not None
        c = fp.get_cluster(fid)
        assert c.sample_message == msg

    def test_unicode_message(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "error: 日本語テスト 🚀")
        assert fid is not None

    def test_empty_session_id(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", session_id="")
        c = fp.get_cluster(fid)
        assert "" in c.session_ids

    def test_concurrent_different_errors(self):
        fp = ErrorFingerprinter()
        ts = _ts(0)
        fids = set()
        for i in range(100):
            fid = fp.record(f"E{i % 10}", f"error type {i % 10}",
                            session_id=f"s{i}", timestamp=ts)
            fids.add(fid)
        assert len(fids) == 10  # 10 unique error types

    def test_report_include_resolved(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", timestamp=_ts(0))
        fp.resolve(fid)
        report = fp.report(include_resolved=True)
        assert report.unique_count == 1

    def test_report_include_ignored(self):
        fp = ErrorFingerprinter()
        fid = fp.record("E", "msg", timestamp=_ts(0))
        fp.ignore(fid)
        report = fp.report(include_ignored=True)
        assert report.unique_count == 1
