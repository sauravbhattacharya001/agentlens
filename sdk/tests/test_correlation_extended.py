"""Extended tests for agentlens.correlation — edge cases and deeper coverage."""

import unittest
from datetime import datetime, timedelta, timezone

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.correlation import (
    ContentionSeverity,
    CorrelationReport,
    ErrorPropagation,
    PropagationDirection,
    ResourceContention,
    SessionCorrelator,
    SessionWindow,
    SharedResource,
    SyncPoint,
    TemporalOverlap,
)


def _utc(minutes: float = 0, seconds: float = 0) -> datetime:
    base = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes, seconds=seconds)


def _event(
    event_type: str = "llm_call",
    model: str = None,
    tool_name: str = None,
    ts_minutes: float = 0,
    ts_seconds: float = 0,
    duration_ms: float = 100,
) -> AgentEvent:
    tc = None
    if tool_name:
        tc = ToolCall(tool_name=tool_name)
    return AgentEvent(
        event_type=event_type,
        model=model,
        tool_call=tc,
        timestamp=_utc(ts_minutes, ts_seconds),
        duration_ms=duration_ms,
    )


def _session(
    sid: str,
    agent: str = "agent-1",
    start_min: float = 0,
    end_min: float = 10,
    events: list = None,
) -> Session:
    return Session(
        session_id=sid,
        agent_name=agent,
        started_at=_utc(start_min),
        ended_at=_utc(end_min),
        events=events or [],
    )


# ── SessionWindow edge cases ────────────────────────────────────────


class TestBuildWindowEdgeCases(unittest.TestCase):
    """Edge cases for _build_window."""

    def test_no_events(self):
        """Session with zero events."""
        s = _session("s1", events=[])
        w = SessionCorrelator._build_window(s)
        self.assertEqual(w.event_count, 0)
        self.assertEqual(w.error_count, 0)
        self.assertEqual(len(w.models_used), 0)
        self.assertEqual(len(w.tools_used), 0)

    def test_multiple_models_and_tools(self):
        """Window collects unique models and tools."""
        events = [
            _event(model="gpt-4", tool_name="search", ts_minutes=1),
            _event(model="gpt-4", tool_name="search", ts_minutes=2),
            _event(model="claude", tool_name="browser", ts_minutes=3),
            _event(model="gpt-4", ts_minutes=4),
        ]
        s = _session("s1", events=events)
        w = SessionCorrelator._build_window(s)
        self.assertEqual(w.models_used, {"gpt-4", "claude"})
        self.assertEqual(w.tools_used, {"search", "browser"})
        self.assertEqual(w.event_count, 4)

    def test_multiple_errors(self):
        """Count multiple errors."""
        events = [
            _event(event_type="agent_error", ts_minutes=1),
            _event(event_type="llm_error", ts_minutes=2),
            _event(event_type="tool_error", ts_minutes=3),
            _event(event_type="llm_call", ts_minutes=4),
        ]
        s = _session("s1", events=events)
        w = SessionCorrelator._build_window(s)
        self.assertEqual(w.error_count, 3)

    def test_end_estimated_from_events(self):
        """When ended_at is None, estimate from last event."""
        events = [
            _event(ts_minutes=5, duration_ms=200),
            _event(ts_minutes=8, duration_ms=300),
        ]
        s = Session(
            session_id="s1",
            agent_name="a",
            started_at=_utc(0),
            ended_at=None,
            events=events,
        )
        w = SessionCorrelator._build_window(s)
        # End should be ~8min + 300ms from base
        self.assertIsNotNone(w.end)
        self.assertGreater(w.end, _utc(8))


# ── TemporalOverlap edge cases ──────────────────────────────────────


class TestOverlapEdgeCases(unittest.TestCase):

    def test_contained_session(self):
        """Session B entirely within session A."""
        c = SessionCorrelator()
        c.add_session(_session("a", start_min=0, end_min=20))
        c.add_session(_session("b", start_min=5, end_min=10))
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 1)
        # B is 5 minutes long, overlap should be 5 minutes
        self.assertAlmostEqual(overlaps[0].overlap_pct_b, 100.0)
        # A is 20 minutes, overlap 5 → 25%
        self.assertAlmostEqual(overlaps[0].overlap_pct_a, 25.0)

    def test_three_sessions_pairwise(self):
        """Three overlapping sessions produce 3 overlap pairs."""
        c = SessionCorrelator()
        c.add_session(_session("a", start_min=0, end_min=10))
        c.add_session(_session("b", start_min=5, end_min=15))
        c.add_session(_session("c", start_min=8, end_min=20))
        overlaps = c.find_overlaps()
        # (a,b), (a,c), (b,c) all overlap
        self.assertEqual(len(overlaps), 3)
        pairs = {(o.session_a, o.session_b) for o in overlaps}
        self.assertIn(("a", "b"), pairs)
        self.assertIn(("a", "c"), pairs)
        self.assertIn(("b", "c"), pairs)

    def test_identical_windows(self):
        """Identical start/end → 100% overlap both ways."""
        c = SessionCorrelator()
        c.add_session(_session("a", start_min=0, end_min=10))
        c.add_session(_session("b", start_min=0, end_min=10))
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 1)
        self.assertAlmostEqual(overlaps[0].overlap_pct_a, 100.0)
        self.assertAlmostEqual(overlaps[0].overlap_pct_b, 100.0)

    def test_tiny_overlap(self):
        """1-second overlap between two 10-minute sessions."""
        c = SessionCorrelator()
        c.add_session(_session("a", start_min=0, end_min=10))
        # b starts at 9:59
        s2 = Session(
            session_id="b", agent_name="a",
            started_at=_utc(9, 59), ended_at=_utc(20),
            events=[],
        )
        c.add_session(s2)
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 1)
        self.assertGreater(overlaps[0].overlap_ms, 0)
        self.assertLess(overlaps[0].overlap_pct_a, 1.0)


# ── SharedResource edge cases ───────────────────────────────────────


class TestSharedResourceEdgeCases(unittest.TestCase):

    def test_multiple_tools_shared(self):
        """Two sessions share two different tools."""
        events_a = [
            _event(tool_name="search", ts_minutes=1),
            _event(tool_name="code", ts_minutes=2),
        ]
        events_b = [
            _event(tool_name="search", ts_minutes=3),
            _event(tool_name="code", ts_minutes=4),
        ]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        shared = c.find_shared_resources()
        names = {r.resource_name for r in shared}
        self.assertIn("search", names)
        self.assertIn("code", names)

    def test_tool_and_model_shared(self):
        """Shared tool AND shared model produce separate entries."""
        events_a = [
            _event(model="gpt-4", tool_name="search", ts_minutes=1),
        ]
        events_b = [
            _event(model="gpt-4", tool_name="search", ts_minutes=3),
        ]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        shared = c.find_shared_resources()
        types = {r.resource_type for r in shared}
        self.assertIn("tool", types)
        self.assertIn("model", types)

    def test_single_session_no_shared(self):
        """One session cannot have shared resources."""
        c = SessionCorrelator()
        c.add_session(_session("a", events=[_event(tool_name="x")]))
        self.assertEqual(len(c.find_shared_resources()), 0)

    def test_concurrent_uses_counted(self):
        """Overlapping tool usage increments concurrent count."""
        # Two sessions use "search" at the same time (ts=1min, 100ms duration)
        events_a = [_event(tool_name="search", ts_minutes=1, duration_ms=500)]
        events_b = [_event(tool_name="search", ts_minutes=1, ts_seconds=0.1, duration_ms=500)]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        shared = c.find_shared_resources()
        search = [r for r in shared if r.resource_name == "search"]
        self.assertEqual(len(search), 1)
        self.assertGreaterEqual(search[0].concurrent_uses, 2)


# ── Error propagation edge cases ────────────────────────────────────


class TestErrorPropagationEdgeCases(unittest.TestCase):

    def test_no_shared_resource_no_propagation(self):
        """Errors in sessions without shared resources → no propagation."""
        events_a = [
            _event(event_type="agent_error", tool_name="search", ts_minutes=1),
        ]
        events_b = [
            _event(event_type="agent_error", tool_name="browser", ts_minutes=1, ts_seconds=1),
        ]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        props = c.trace_error_propagation()
        self.assertEqual(len(props), 0)

    def test_error_outside_window_no_propagation(self):
        """Errors too far apart → no propagation."""
        events_a = [
            _event(event_type="agent_error", tool_name="db", ts_minutes=0),
        ]
        events_b = [
            _event(event_type="agent_error", tool_name="db", ts_minutes=10),
        ]
        c = SessionCorrelator(error_propagation_window_ms=5000)
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        props = c.trace_error_propagation()
        self.assertEqual(len(props), 0)

    def test_confidence_higher_for_shorter_delay(self):
        """Shorter delay → higher confidence."""
        events_a = [
            _event(event_type="agent_error", tool_name="db", ts_minutes=0),
        ]
        events_b_fast = [
            _event(event_type="agent_error", tool_name="db", ts_seconds=0.5),
        ]
        events_b_slow = [
            _event(event_type="agent_error", tool_name="db", ts_seconds=4),
        ]
        c_fast = SessionCorrelator(error_propagation_window_ms=5000)
        c_fast.add_session(_session("a", events=events_a))
        c_fast.add_session(_session("b", events=events_b_fast))
        p_fast = c_fast.trace_error_propagation()

        c_slow = SessionCorrelator(error_propagation_window_ms=5000)
        c_slow.add_session(_session("a", events=events_a))
        c_slow.add_session(_session("b", events=events_b_slow))
        p_slow = c_slow.trace_error_propagation()

        self.assertGreater(len(p_fast), 0)
        self.assertGreater(len(p_slow), 0)
        self.assertGreater(p_fast[0].confidence, p_slow[0].confidence)

    def test_backward_error_no_forward(self):
        """Error in B before A → no forward propagation from A to B."""
        events_a = [
            _event(event_type="agent_error", tool_name="cache", ts_minutes=5),
        ]
        events_b = [
            _event(event_type="agent_error", tool_name="cache", ts_minutes=2),
        ]
        c = SessionCorrelator(error_propagation_window_ms=5000)
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        props = c.trace_error_propagation()
        # Should find B→A propagation, not A→B
        for p in props:
            if p.direction == PropagationDirection.FORWARD:
                self.assertNotEqual(p.source_session, "a")


# ── Sync points edge cases ──────────────────────────────────────────


class TestSyncPointEdgeCases(unittest.TestCase):

    def test_three_sessions_same_resource(self):
        """Three sessions hitting same resource within window → single sync point."""
        c = SessionCorrelator(sync_window_ms=1000)
        events_a = [_event(tool_name="db", ts_minutes=5, ts_seconds=0)]
        events_b = [_event(tool_name="db", ts_minutes=5, ts_seconds=0.3)]
        events_c = [_event(tool_name="db", ts_minutes=5, ts_seconds=0.6)]
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        c.add_session(_session("c", events=events_c))
        syncs = c.find_sync_points()
        self.assertGreater(len(syncs), 0)
        # All three sessions should be in at least one sync point
        all_sids = set()
        for s in syncs:
            all_sids.update(s.session_ids)
        self.assertIn("a", all_sids)
        self.assertIn("b", all_sids)
        self.assertIn("c", all_sids)

    def test_different_resources_no_sync(self):
        """Two sessions using different tools at same time → no sync."""
        c = SessionCorrelator(sync_window_ms=500)
        events_a = [_event(tool_name="search", ts_minutes=5)]
        events_b = [_event(tool_name="browser", ts_minutes=5)]
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        syncs = c.find_sync_points()
        # No "search" or "browser" sync (each used by only 1 session)
        self.assertEqual(len(syncs), 0)

    def test_same_session_no_sync(self):
        """Single session multiple uses → no sync point (need 2 sessions)."""
        c = SessionCorrelator(sync_window_ms=500)
        events = [
            _event(tool_name="db", ts_minutes=1),
            _event(tool_name="db", ts_minutes=1, ts_seconds=0.1),
        ]
        c.add_session(_session("a", events=events))
        syncs = c.find_sync_points()
        self.assertEqual(len(syncs), 0)


# ── Contention edge cases ───────────────────────────────────────────


class TestContentionEdgeCases(unittest.TestCase):

    def test_contention_severity_scales(self):
        """More concurrent uses → higher severity."""
        self.assertEqual(
            SessionCorrelator._contention_severity(3),
            ContentionSeverity.MEDIUM,
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(5),
            ContentionSeverity.HIGH,
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(8),
            ContentionSeverity.CRITICAL,
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(2),
            ContentionSeverity.LOW,
        )

    def test_custom_threshold(self):
        """Custom contention_threshold controls detection."""
        events = [_event(tool_name="db", ts_minutes=1, duration_ms=500)]
        c_low = SessionCorrelator(contention_threshold=2)
        c_high = SessionCorrelator(contention_threshold=5)
        for corr in [c_low, c_high]:
            corr.add_session(_session("a", events=events[:]))
            corr.add_session(_session("b", events=[
                _event(tool_name="db", ts_minutes=1, ts_seconds=0.1, duration_ms=500)
            ]))
        # threshold=2 should detect, threshold=5 should not
        self.assertGreaterEqual(len(c_low.detect_contention()), 0)
        self.assertEqual(len(c_high.detect_contention()), 0)

    def test_event_resources_extraction(self):
        """_event_resources extracts model and tool."""
        e = _event(model="gpt-4", tool_name="search")
        resources = SessionCorrelator._event_resources(e)
        names = {r[0] for r in resources}
        types = {r[1] for r in resources}
        self.assertIn("gpt-4", names)
        self.assertIn("search", names)
        self.assertIn("model", types)
        self.assertIn("tool", types)

    def test_event_resources_no_model_no_tool(self):
        """Event without model or tool → empty resources."""
        e = _event(event_type="system")
        resources = SessionCorrelator._event_resources(e)
        self.assertEqual(len(resources), 0)


# ── Model hotspots edge cases ───────────────────────────────────────


class TestModelHotspotEdgeCases(unittest.TestCase):

    def test_multiple_models(self):
        """Each model tracked independently."""
        events_a = [
            _event(model="gpt-4", ts_minutes=1, duration_ms=500),
            _event(model="claude", ts_minutes=1, duration_ms=500),
        ]
        events_b = [
            _event(model="gpt-4", ts_minutes=1, ts_seconds=0.1, duration_ms=500),
            _event(model="claude", ts_minutes=1, ts_seconds=0.1, duration_ms=500),
        ]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        hotspots = c.find_model_hotspots()
        self.assertIn("gpt-4", hotspots)
        self.assertIn("claude", hotspots)
        self.assertEqual(hotspots["gpt-4"], 2)
        self.assertEqual(hotspots["claude"], 2)

    def test_non_overlapping_uses_no_hotspot(self):
        """Sequential (non-overlapping) uses → no hotspot."""
        events_a = [_event(model="gpt-4", ts_minutes=1, duration_ms=100)]
        events_b = [_event(model="gpt-4", ts_minutes=5, duration_ms=100)]
        c = SessionCorrelator()
        c.add_session(_session("a", events=events_a))
        c.add_session(_session("b", events=events_b))
        hotspots = c.find_model_hotspots()
        # Uses don't overlap → no concurrent peak ≥ 2
        self.assertNotIn("gpt-4", hotspots)


# ── CorrelationReport edge cases ────────────────────────────────────


class TestCorrelationReportEdgeCases(unittest.TestCase):

    def test_total_correlations_sum(self):
        """total_correlations sums all correlation types."""
        r = CorrelationReport(
            overlaps=[TemporalOverlap("a", "b", _utc(), _utc(1), 1000, 50, 50)],
            shared_resources=[SharedResource("db", "tool", ["a", "b"], 5, 2)],
            sync_points=[SyncPoint(_utc(), "db", ["a", "b"], 100)],
        )
        self.assertEqual(r.total_correlations, 3)

    def test_max_contention_severity_none(self):
        """No contentions → None severity."""
        r = CorrelationReport()
        self.assertIsNone(r.max_contention_severity)

    def test_max_contention_severity_picks_highest(self):
        """Picks the highest severity from contentions."""
        r = CorrelationReport(contentions=[
            ResourceContention("a", "tool", ["s1"], _utc(), _utc(1), 3, ContentionSeverity.LOW, 50),
            ResourceContention("b", "tool", ["s1"], _utc(), _utc(1), 8, ContentionSeverity.CRITICAL, 400),
        ])
        self.assertEqual(r.max_contention_severity, ContentionSeverity.CRITICAL)

    def test_avg_overlap_pct_zero_no_overlaps(self):
        """No overlaps → 0% average."""
        r = CorrelationReport()
        self.assertEqual(r.avg_overlap_pct, 0.0)

    def test_avg_overlap_pct_calculation(self):
        """Average overlap uses mean of (pct_a + pct_b) / 2."""
        r = CorrelationReport(overlaps=[
            TemporalOverlap("a", "b", _utc(), _utc(1), 1000, 40, 60),
            TemporalOverlap("c", "d", _utc(), _utc(1), 1000, 80, 80),
        ])
        # (40+60)/2 = 50, (80+80)/2 = 80, mean = 65
        self.assertAlmostEqual(r.avg_overlap_pct, 65.0)

    def test_has_error_propagation_false(self):
        r = CorrelationReport()
        self.assertFalse(r.has_error_propagation)

    def test_has_contention_false(self):
        r = CorrelationReport()
        self.assertFalse(r.has_contention)

    def test_render_empty_report(self):
        """Render works on empty report."""
        r = CorrelationReport()
        text = r.render()
        self.assertIn("Session Correlation Report", text)
        self.assertIn("Total correlations found: 0", text)

    def test_summary_fields(self):
        """Summary dict has expected keys."""
        r = CorrelationReport(session_count=5, total_events=100)
        s = r.summary()
        self.assertEqual(s["session_count"], 5)
        self.assertEqual(s["total_events"], 100)
        self.assertIn("temporal_overlaps", s)
        self.assertIn("avg_overlap_pct", s)

    def test_to_dict_structure(self):
        """to_dict includes all sections."""
        r = CorrelationReport()
        d = r.to_dict()
        for key in ["summary", "overlaps", "shared_resources",
                     "error_propagations", "sync_points", "contentions",
                     "model_hotspots"]:
            self.assertIn(key, d)


# ── Dataclass to_dict methods ───────────────────────────────────────


class TestDataclassSerialization(unittest.TestCase):

    def test_temporal_overlap_to_dict(self):
        o = TemporalOverlap("a", "b", _utc(), _utc(1), 60000, 50.123, 75.456)
        d = o.to_dict()
        self.assertEqual(d["session_a"], "a")
        self.assertAlmostEqual(d["overlap_pct_a"], 50.1, places=0)
        self.assertIn("overlap_start", d)

    def test_shared_resource_to_dict(self):
        r = SharedResource("db", "tool", ["s1", "s2"], 10, 3)
        d = r.to_dict()
        self.assertEqual(d["session_count"], 2)
        self.assertEqual(d["total_uses"], 10)

    def test_error_propagation_to_dict(self):
        p = ErrorPropagation("a", "b", _utc(), _utc(0, 1), 1000, PropagationDirection.FORWARD, ["db"], 0.85)
        d = p.to_dict()
        self.assertEqual(d["direction"], "forward")
        self.assertAlmostEqual(d["confidence"], 0.85)

    def test_sync_point_to_dict(self):
        s = SyncPoint(_utc(), "cache", ["a", "b", "c"], 250)
        d = s.to_dict()
        self.assertEqual(len(d["session_ids"]), 3)
        self.assertAlmostEqual(d["window_ms"], 250.0)

    def test_resource_contention_to_dict(self):
        rc = ResourceContention("db", "tool", ["a", "b", "c"], _utc(), _utc(1), 3, ContentionSeverity.MEDIUM, 150)
        d = rc.to_dict()
        self.assertEqual(d["severity"], "medium")
        self.assertEqual(d["concurrent_count"], 3)


# ── Full correlate flow ─────────────────────────────────────────────


class TestFullCorrelateFlow(unittest.TestCase):

    def test_correlate_returns_all_sections(self):
        """Full correlate() populates report structure."""
        events_a = [
            _event(model="gpt-4", tool_name="search", ts_minutes=1, duration_ms=500),
            _event(event_type="agent_error", tool_name="search", ts_minutes=2),
        ]
        events_b = [
            _event(model="gpt-4", tool_name="search", ts_minutes=1, ts_seconds=0.1, duration_ms=500),
            _event(event_type="agent_error", tool_name="search", ts_minutes=2, ts_seconds=1),
        ]
        c = SessionCorrelator(sync_window_ms=1000, contention_threshold=2)
        c.add_session(_session("a", start_min=0, end_min=10, events=events_a))
        c.add_session(_session("b", start_min=0, end_min=10, events=events_b))
        report = c.correlate()
        self.assertEqual(report.session_count, 2)
        self.assertGreater(report.total_events, 0)
        self.assertIsNotNone(report.analysis_window_start)
        self.assertIsNotNone(report.analysis_window_end)
        self.assertGreater(len(report.overlaps), 0)
        self.assertGreater(len(report.shared_resources), 0)

    def test_static_compare(self):
        """SessionCorrelator.compare() works."""
        sessions = [
            _session("a", start_min=0, end_min=10, events=[_event(model="gpt-4")]),
            _session("b", start_min=5, end_min=15, events=[_event(model="gpt-4")]),
        ]
        report = SessionCorrelator.compare(sessions)
        self.assertEqual(report.session_count, 2)
        self.assertGreater(report.total_correlations, 0)

    def test_add_and_clear(self):
        """add_session then clear resets state."""
        c = SessionCorrelator()
        c.add_session(_session("a"))
        c.add_session(_session("b"))
        self.assertEqual(len(c._sessions), 2)
        c.clear()
        self.assertEqual(len(c._sessions), 0)
        self.assertEqual(len(c._windows), 0)

    def test_render_with_all_sections(self):
        """Render a report that has data in every section."""
        events_a = [
            _event(model="gpt-4", tool_name="db", ts_minutes=1, duration_ms=500),
            _event(event_type="agent_error", tool_name="db", ts_minutes=2),
        ]
        events_b = [
            _event(model="gpt-4", tool_name="db", ts_minutes=1, ts_seconds=0.1, duration_ms=500),
            _event(event_type="agent_error", tool_name="db", ts_minutes=2, ts_seconds=0.5),
        ]
        events_c = [
            _event(model="gpt-4", tool_name="db", ts_minutes=1, ts_seconds=0.2, duration_ms=500),
        ]
        c = SessionCorrelator(
            sync_window_ms=2000,
            contention_threshold=2,
            error_propagation_window_ms=5000,
        )
        c.add_sessions([
            _session("a", start_min=0, end_min=10, events=events_a),
            _session("b", start_min=0, end_min=10, events=events_b),
            _session("c", start_min=0, end_min=10, events=events_c),
        ])
        report = c.correlate()
        text = report.render()
        self.assertIn("Temporal Overlaps", text)
        self.assertIn("Shared Resources", text)


if __name__ == "__main__":
    unittest.main()
