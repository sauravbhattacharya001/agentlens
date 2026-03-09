"""Tests for the RetryTracker module."""

import pytest
from agentlens.retry_tracker import (
    RetryChain,
    RetryOutcome,
    RetryReport,
    RetryStorm,
    RetryTracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_eid = 0

def _next_id() -> str:
    global _eid
    _eid += 1
    return f"ev-{_eid:04d}"


def _event(
    event_type: str = "llm_call",
    duration_ms: float = 100.0,
    tokens_in: int = 50,
    tokens_out: int = 50,
    model: str | None = "gpt-4o",
    tool_name: str | None = None,
    retry_of: str | None = None,
    error_type: str | None = None,
    timestamp: str = "2026-01-15T10:00:00Z",
    event_id: str | None = None,
) -> dict:
    ev = {
        "event_id": event_id or _next_id(),
        "event_type": event_type,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    if model:
        ev["model"] = model
    if tool_name:
        ev["tool_call"] = {"tool_name": tool_name}
    if retry_of:
        ev["retry_of"] = retry_of
    if error_type:
        ev["error_type"] = error_type
    return ev


def _session(sid: str, events: list[dict]) -> dict:
    return {"session_id": sid, "events": events}


# ---------------------------------------------------------------------------
# Construction & Config
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_construction(self):
        t = RetryTracker()
        report = t.report()
        assert report.total_events == 0
        assert report.total_retries == 0

    def test_custom_storm_params(self):
        t = RetryTracker(storm_window_ms=30_000, storm_threshold=3)
        assert t._storm_window_ms == 30_000
        assert t._storm_threshold == 3

    def test_invalid_storm_window(self):
        with pytest.raises(ValueError, match="storm_window_ms"):
            RetryTracker(storm_window_ms=0)

    def test_invalid_storm_threshold(self):
        with pytest.raises(ValueError, match="storm_threshold"):
            RetryTracker(storm_threshold=1)


# ---------------------------------------------------------------------------
# Chain building
# ---------------------------------------------------------------------------

class TestChainBuilding:
    def test_no_retries_no_chains(self):
        e1 = _event(event_id="a1")
        e2 = _event(event_id="a2")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert len(report.chains) == 0
        assert report.total_retries == 0

    def test_single_retry_chain(self):
        e1 = _event(event_id="a1", error_type="timeout")
        e2 = _event(event_id="a2", retry_of="a1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert len(report.chains) == 1
        chain = report.chains[0]
        assert chain.attempt_count == 2
        assert chain.outcome == RetryOutcome.SUCCEEDED

    def test_multi_retry_chain(self):
        e1 = _event(event_id="b1", error_type="timeout")
        e2 = _event(event_id="b2", retry_of="b1", error_type="timeout")
        e3 = _event(event_id="b3", retry_of="b2")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3]))
        report = t.report()
        assert len(report.chains) == 1
        assert report.chains[0].attempt_count == 3

    def test_failed_chain(self):
        e1 = _event(event_id="c1", error_type="timeout")
        e2 = _event(event_id="c2", retry_of="c1", error_type="timeout")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.chains[0].outcome == RetryOutcome.FAILED

    def test_multiple_independent_chains(self):
        # Two separate retry chains in one session
        e1 = _event(event_id="d1", error_type="err")
        e2 = _event(event_id="d2", retry_of="d1")
        e3 = _event(event_id="d3", error_type="err")
        e4 = _event(event_id="d4", retry_of="d3")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3, e4]))
        report = t.report()
        assert len(report.chains) == 2

    def test_chain_across_multiple_add_session(self):
        e1 = _event(event_id="e1", error_type="err")
        e2 = _event(event_id="e2", retry_of="e1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1]))
        t.add_session(_session("s2", [e2]))
        # Different sessions but linked by retry_of
        report = t.report()
        assert len(report.chains) == 1

    def test_cycle_prevention(self):
        # Prevent infinite loop on cyclical retry_of
        e1 = _event(event_id="f1", retry_of="f2", error_type="err")
        e2 = _event(event_id="f2", retry_of="f1", error_type="err")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        # Should not hang — just produces partial chains
        report = t.report()
        assert report.total_events == 2


# ---------------------------------------------------------------------------
# Retry Tax
# ---------------------------------------------------------------------------

class TestRetryTax:
    def test_token_tax(self):
        e1 = _event(event_id="g1", tokens_in=100, tokens_out=200, error_type="err")
        e2 = _event(event_id="g2", retry_of="g1", tokens_in=100, tokens_out=200)
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.retry_tax_tokens == 300  # e2's tokens

    def test_duration_tax(self):
        e1 = _event(event_id="h1", duration_ms=500, error_type="err")
        e2 = _event(event_id="h2", retry_of="h1", duration_ms=300)
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.retry_tax_duration_ms == 300.0


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

class TestBreakdowns:
    def test_retries_by_type(self):
        e1 = _event(event_id="i1", event_type="tool_call", error_type="err")
        e2 = _event(event_id="i2", retry_of="i1", event_type="tool_call")
        e3 = _event(event_id="i3", event_type="llm_call", error_type="err")
        e4 = _event(event_id="i4", retry_of="i3", event_type="llm_call")
        e5 = _event(event_id="i5", retry_of="i4", event_type="llm_call")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3, e4, e5]))
        report = t.report()
        assert report.retries_by_type.get("tool_call") == 1
        assert report.retries_by_type.get("llm_call") == 2

    def test_retries_by_tool(self):
        e1 = _event(event_id="j1", event_type="tool_call",
                     tool_name="search", error_type="err")
        e2 = _event(event_id="j2", retry_of="j1", event_type="tool_call",
                     tool_name="search")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.retries_by_tool.get("search") == 1

    def test_retries_by_model(self):
        e1 = _event(event_id="k1", model="gpt-4o", error_type="err")
        e2 = _event(event_id="k2", retry_of="k1", model="gpt-4o")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.retries_by_model.get("gpt-4o") == 1

    def test_retries_by_error(self):
        e1 = _event(event_id="l1", error_type="rate_limit")
        e2 = _event(event_id="l2", retry_of="l1", error_type="rate_limit")
        e3 = _event(event_id="l3", retry_of="l2")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3]))
        report = t.report()
        assert report.retries_by_error.get("rate_limit") == 2


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_retry_rate(self):
        e1 = _event(event_id="m1", error_type="err")
        e2 = _event(event_id="m2", retry_of="m1")
        e3 = _event(event_id="m3")  # no retry
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3]))
        report = t.report()
        # 1 retry out of 3 events = 33%
        assert abs(report.retry_rate - 1/3) < 0.01

    def test_success_rate_all_succeed(self):
        e1 = _event(event_id="n1", error_type="err")
        e2 = _event(event_id="n2", retry_of="n1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.success_rate == 1.0

    def test_success_rate_all_fail(self):
        e1 = _event(event_id="o1", error_type="err")
        e2 = _event(event_id="o2", retry_of="o1", error_type="err")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert report.success_rate == 0.0

    def test_avg_attempts(self):
        # Chain 1: 2 attempts, Chain 2: 3 attempts → avg 2.5
        e1 = _event(event_id="p1", error_type="err")
        e2 = _event(event_id="p2", retry_of="p1")
        e3 = _event(event_id="p3", error_type="err")
        e4 = _event(event_id="p4", retry_of="p3", error_type="err")
        e5 = _event(event_id="p5", retry_of="p4")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3, e4, e5]))
        report = t.report()
        assert report.avg_attempts == 2.5

    def test_max_attempts(self):
        e1 = _event(event_id="q1", error_type="err")
        e2 = _event(event_id="q2", retry_of="q1", error_type="err")
        e3 = _event(event_id="q3", retry_of="q2", error_type="err")
        e4 = _event(event_id="q4", retry_of="q3")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2, e3, e4]))
        report = t.report()
        assert report.max_attempts == 4

    def test_empty_report(self):
        t = RetryTracker()
        report = t.report()
        assert report.retry_rate == 0
        assert report.success_rate == 0
        assert report.avg_attempts == 0


# ---------------------------------------------------------------------------
# Storm detection
# ---------------------------------------------------------------------------

class TestStormDetection:
    def test_storm_detected(self):
        base = "2026-01-15T10:00:"
        e0 = _event(event_id="s0", error_type="err", timestamp=f"{base}00Z")
        # 5 retries in quick succession
        events = [e0]
        for i in range(1, 6):
            events.append(_event(
                event_id=f"s{i}", retry_of=f"s{i-1}",
                error_type="rate_limit" if i < 5 else None,
                timestamp=f"{base}{i:02d}Z",
            ))
        t = RetryTracker(storm_threshold=5)
        t.add_session(_session("sess1", events))
        report = t.report()
        assert len(report.storms) >= 1

    def test_no_storm_under_threshold(self):
        e1 = _event(event_id="t1", error_type="err", timestamp="2026-01-15T10:00:00Z")
        e2 = _event(event_id="t2", retry_of="t1", timestamp="2026-01-15T10:00:01Z")
        t = RetryTracker(storm_threshold=5)
        t.add_session(_session("s1", [e1, e2]))
        report = t.report()
        assert len(report.storms) == 0

    def test_storm_captures_metadata(self):
        base = "2026-01-15T10:00:"
        events = []
        for i in range(7):
            eid = f"u{i}"
            events.append(_event(
                event_id=eid,
                retry_of=f"u{i-1}" if i > 0 else None,
                error_type="timeout",
                model="gpt-4o",
                tool_name="web_search",
                timestamp=f"{base}{i:02d}Z",
            ))
        t = RetryTracker(storm_threshold=5)
        t.add_session(_session("s1", events))
        report = t.report()
        if report.storms:
            storm = report.storms[0]
            assert storm.session_id == "s1"
            assert storm.dominant_error == "timeout"
            assert "web_search" in storm.affected_tools


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_no_recs_without_retries(self):
        e1 = _event(event_id="v1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1]))
        report = t.report()
        assert len(report.recommendations) == 0

    def test_circuit_breaker_rec_on_storm(self):
        base = "2026-01-15T10:00:"
        events = []
        for i in range(7):
            events.append(_event(
                event_id=f"w{i}",
                retry_of=f"w{i-1}" if i > 0 else None,
                error_type="timeout",
                timestamp=f"{base}{i:02d}Z",
            ))
        t = RetryTracker(storm_threshold=5)
        t.add_session(_session("s1", events))
        report = t.report()
        cats = [r.category for r in report.recommendations]
        assert "circuit-breaker" in cats

    def test_retry_limit_rec_for_long_chains(self):
        # Chain of 5 attempts
        events = []
        for i in range(5):
            events.append(_event(
                event_id=f"x{i}",
                retry_of=f"x{i-1}" if i > 0 else None,
                error_type="err" if i < 4 else None,
            ))
        t = RetryTracker()
        t.add_session(_session("s1", events))
        report = t.report()
        cats = [r.category for r in report.recommendations]
        assert "retry-limit" in cats

    def test_caching_rec_for_tool_retries(self):
        # 4 retries on same tool
        events = []
        for i in range(4):
            events.append(_event(
                event_id=f"y{i}",
                retry_of=f"y{i-1}" if i > 0 else None,
                event_type="tool_call",
                tool_name="database_query",
                error_type="err" if i < 3 else None,
            ))
        t = RetryTracker()
        t.add_session(_session("s1", events))
        report = t.report()
        cats = [r.category for r in report.recommendations]
        assert "caching" in cats

    def test_error_handling_rec(self):
        events = []
        for i in range(4):
            events.append(_event(
                event_id=f"z{i}",
                retry_of=f"z{i-1}" if i > 0 else None,
                error_type="rate_limit" if i < 3 else None,
            ))
        t = RetryTracker()
        t.add_session(_session("s1", events))
        report = t.report()
        cats = [r.category for r in report.recommendations]
        assert "error-handling" in cats

    def test_backoff_rec_high_retry_rate(self):
        # Make >10% of events be retries
        events = []
        for i in range(10):
            events.append(_event(event_id=f"aa{i}"))
        # Add a chain with 3 retries (3 out of 13 events = 23%)
        events.append(_event(event_id="bb0", error_type="err"))
        events.append(_event(event_id="bb1", retry_of="bb0", error_type="err"))
        events.append(_event(event_id="bb2", retry_of="bb1"))
        t = RetryTracker()
        t.add_session(_session("s1", events))
        report = t.report()
        cats = [r.category for r in report.recommendations]
        assert "backoff" in cats


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

class TestReportRendering:
    def test_render_empty(self):
        t = RetryTracker()
        report = t.report()
        text = report.render()
        assert "Retry Analysis Report" in text
        assert "0" in text

    def test_render_with_data(self):
        e1 = _event(event_id="cc1", error_type="err", model="gpt-4o")
        e2 = _event(event_id="cc2", retry_of="cc1", model="gpt-4o")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        text = t.report().render()
        assert "Retry rate:" in text
        assert "Extra tokens:" in text

    def test_to_dict(self):
        e1 = _event(event_id="dd1", error_type="err")
        e2 = _event(event_id="dd2", retry_of="dd1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        d = t.report().to_dict()
        assert "total_events" in d
        assert "retry_rate" in d
        assert "retry_tax_tokens" in d
        assert "recommendations" in d
        assert isinstance(d["recommendations"], list)


# ---------------------------------------------------------------------------
# Clear / add_sessions
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_clear(self):
        e1 = _event(event_id="ee1", error_type="err")
        e2 = _event(event_id="ee2", retry_of="ee1")
        t = RetryTracker()
        t.add_session(_session("s1", [e1, e2]))
        assert t.report().total_retries > 0
        t.clear()
        assert t.report().total_retries == 0

    def test_add_sessions_batch(self):
        s1 = _session("s1", [_event(event_id="ff1")])
        s2 = _session("s2", [_event(event_id="ff2")])
        t = RetryTracker()
        t.add_sessions([s1, s2])
        assert t.report().total_events == 2
