"""Tests for agentlens.correlation — Session Correlation Engine."""

import unittest
from datetime import datetime, timedelta, timezone

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.correlation import (
    ContentionSeverity,
    CorrelationKind,
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


# ── Helpers ──────────────────────────────────────────────────────────

def _utc(minutes: float = 0, seconds: float = 0) -> datetime:
    """Fixed base time + offset."""
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes, seconds=seconds)


def _event(
    event_type: str = "llm_call",
    model: str = None,
    tool_name: str = None,
    ts_minutes: float = 0,
    ts_seconds: float = 0,
    duration_ms: float = 100,
) -> AgentEvent:
    """Create a test event."""
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
    """Create a test session."""
    s = Session(
        session_id=sid,
        agent_name=agent,
        started_at=_utc(start_min),
        ended_at=_utc(end_min),
        events=events or [],
    )
    return s


# ── SessionWindow ────────────────────────────────────────────────────

class TestSessionWindow(unittest.TestCase):
    def test_build_window(self):
        events = [
            _event(model="gpt-4", ts_minutes=1),
            _event(tool_name="search", ts_minutes=2),
            _event(event_type="agent_error", ts_minutes=3),
        ]
        session = _session("s1", events=events)
        window = SessionCorrelator._build_window(session)
        self.assertEqual(window.session_id, "s1")
        self.assertEqual(window.event_count, 3)
        self.assertEqual(window.error_count, 1)
        self.assertIn("gpt-4", window.models_used)
        self.assertIn("search", window.tools_used)

    def test_window_no_end(self):
        session = _session("s1", events=[_event(ts_minutes=5)])
        session.ended_at = None
        window = SessionCorrelator._build_window(session)
        self.assertIsNotNone(window.end)


# ── Temporal Overlaps ────────────────────────────────────────────────

class TestOverlaps(unittest.TestCase):
    def test_full_overlap(self):
        s1 = _session("a", start_min=0, end_min=10)
        s2 = _session("b", start_min=0, end_min=10)
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 1)
        self.assertAlmostEqual(overlaps[0].overlap_pct_a, 100.0, places=0)

    def test_partial_overlap(self):
        s1 = _session("a", start_min=0, end_min=10)
        s2 = _session("b", start_min=5, end_min=15)
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 1)
        self.assertAlmostEqual(overlaps[0].overlap_pct_a, 50.0, places=0)

    def test_no_overlap(self):
        s1 = _session("a", start_min=0, end_min=5)
        s2 = _session("b", start_min=6, end_min=10)
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 0)

    def test_touching_no_overlap(self):
        s1 = _session("a", start_min=0, end_min=5)
        s2 = _session("b", start_min=5, end_min=10)
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        overlaps = c.find_overlaps()
        self.assertEqual(len(overlaps), 0)

    def test_multiple_sessions(self):
        s1 = _session("a", start_min=0, end_min=10)
        s2 = _session("b", start_min=3, end_min=8)
        s3 = _session("c", start_min=7, end_min=15)
        c = SessionCorrelator()
        c.add_sessions([s1, s2, s3])
        overlaps = c.find_overlaps()
        # a↔b, a↔c, b↔c
        self.assertEqual(len(overlaps), 3)

    def test_overlap_to_dict(self):
        overlap = TemporalOverlap(
            session_a="a", session_b="b",
            overlap_start=_utc(), overlap_end=_utc(5),
            overlap_ms=300000, overlap_pct_a=50, overlap_pct_b=100,
        )
        d = overlap.to_dict()
        self.assertEqual(d["session_a"], "a")
        self.assertIn("overlap_start", d)


# ── Shared Resources ────────────────────────────────────────────────

class TestSharedResources(unittest.TestCase):
    def test_shared_model(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=1)])
        s2 = _session("b", events=[_event(model="gpt-4", ts_minutes=2)])
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        shared = c.find_shared_resources()
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].resource_name, "gpt-4")
        self.assertEqual(shared[0].resource_type, "model")

    def test_shared_tool(self):
        s1 = _session("a", events=[_event(tool_name="search", ts_minutes=1)])
        s2 = _session("b", events=[_event(tool_name="search", ts_minutes=2)])
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        shared = c.find_shared_resources()
        tools = [r for r in shared if r.resource_type == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].resource_name, "search")

    def test_no_shared(self):
        s1 = _session("a", events=[_event(model="gpt-4")])
        s2 = _session("b", events=[_event(model="claude")])
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        shared = c.find_shared_resources()
        self.assertEqual(len(shared), 0)

    def test_shared_to_dict(self):
        sr = SharedResource(
            resource_name="search", resource_type="tool",
            session_ids=["a", "b"], total_uses=5, concurrent_uses=2,
        )
        d = sr.to_dict()
        self.assertEqual(d["session_count"], 2)
        self.assertEqual(d["total_uses"], 5)


# ── Error Propagation ───────────────────────────────────────────────

class TestErrorPropagation(unittest.TestCase):
    def test_forward_propagation(self):
        s1 = _session("a", events=[
            _event(model="gpt-4", ts_minutes=1),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=2),
        ])
        s2 = _session("b", events=[
            _event(model="gpt-4", ts_minutes=1),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=2, ts_seconds=3),
        ])
        c = SessionCorrelator(error_propagation_window_ms=10000)
        c.add_sessions([s1, s2])
        props = c.trace_error_propagation()
        self.assertGreaterEqual(len(props), 1)

    def test_no_propagation_no_shared_resource(self):
        s1 = _session("a", events=[
            _event(event_type="agent_error", model="gpt-4", ts_minutes=2),
        ])
        s2 = _session("b", events=[
            _event(event_type="agent_error", model="claude", ts_minutes=2, ts_seconds=1),
        ])
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        props = c.trace_error_propagation()
        self.assertEqual(len(props), 0)

    def test_no_propagation_outside_window(self):
        s1 = _session("a", events=[
            _event(event_type="agent_error", model="gpt-4", ts_minutes=0),
        ])
        s2 = _session("b", events=[
            _event(event_type="agent_error", model="gpt-4", ts_minutes=10),
        ])
        c = SessionCorrelator(error_propagation_window_ms=5000)
        c.add_sessions([s1, s2])
        props = c.trace_error_propagation()
        self.assertEqual(len(props), 0)

    def test_mutual_propagation(self):
        s1 = _session("a", events=[
            _event(model="gpt-4", ts_minutes=1),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=2),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=4),
        ])
        s2 = _session("b", events=[
            _event(model="gpt-4", ts_minutes=1),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=3),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=3, ts_seconds=30),
        ])
        c = SessionCorrelator(error_propagation_window_ms=120000)
        c.add_sessions([s1, s2])
        props = c.trace_error_propagation()
        mutual = [p for p in props if p.direction == PropagationDirection.MUTUAL]
        self.assertGreaterEqual(len(mutual), 1)

    def test_propagation_confidence(self):
        s1 = _session("a", events=[
            _event(event_type="agent_error", model="gpt-4", ts_minutes=0),
        ])
        s2 = _session("b", events=[
            _event(model="gpt-4", ts_minutes=0),
            _event(event_type="agent_error", model="gpt-4", ts_seconds=1),
        ])
        c = SessionCorrelator(error_propagation_window_ms=5000)
        c.add_sessions([s1, s2])
        props = c.trace_error_propagation()
        if props:
            self.assertGreater(props[0].confidence, 0.5)

    def test_propagation_to_dict(self):
        prop = ErrorPropagation(
            source_session="a", target_session="b",
            source_error_time=_utc(), target_error_time=_utc(0, 1),
            delay_ms=1000, direction=PropagationDirection.FORWARD,
            shared_resources=["gpt-4"], confidence=0.8,
        )
        d = prop.to_dict()
        self.assertEqual(d["direction"], "forward")
        self.assertEqual(d["confidence"], 0.8)


# ── Sync Points ──────────────────────────────────────────────────────

class TestSyncPoints(unittest.TestCase):
    def test_sync_point(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=5)])
        s2 = _session("b", events=[_event(model="gpt-4", ts_minutes=5, ts_seconds=0.1)])
        c = SessionCorrelator(sync_window_ms=500)
        c.add_sessions([s1, s2])
        syncs = c.find_sync_points()
        self.assertGreaterEqual(len(syncs), 1)
        self.assertEqual(syncs[0].resource_name, "gpt-4")

    def test_no_sync_far_apart(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=0)])
        s2 = _session("b", events=[_event(model="gpt-4", ts_minutes=5)])
        c = SessionCorrelator(sync_window_ms=500)
        c.add_sessions([s1, s2])
        syncs = c.find_sync_points()
        self.assertEqual(len(syncs), 0)

    def test_sync_multiple_sessions(self):
        events = []
        sessions = []
        for i in range(3):
            s = _session(f"s{i}", events=[
                _event(model="gpt-4", ts_minutes=5, ts_seconds=i * 0.1)
            ])
            sessions.append(s)
        c = SessionCorrelator(sync_window_ms=1000)
        c.add_sessions(sessions)
        syncs = c.find_sync_points()
        self.assertGreaterEqual(len(syncs), 1)
        self.assertGreaterEqual(len(syncs[0].session_ids), 2)

    def test_sync_to_dict(self):
        sp = SyncPoint(
            timestamp=_utc(), resource_name="search",
            session_ids=["a", "b"], window_ms=200,
        )
        d = sp.to_dict()
        self.assertIn("timestamp", d)
        self.assertEqual(d["window_ms"], 200)


# ── Resource Contention ──────────────────────────────────────────────

class TestResourceContention(unittest.TestCase):
    def test_detect_contention(self):
        # 3 sessions all using gpt-4 at the same time
        sessions = []
        for i in range(3):
            s = _session(f"s{i}", events=[
                _event(model="gpt-4", ts_minutes=5, duration_ms=5000),
            ])
            sessions.append(s)
        c = SessionCorrelator(contention_threshold=3)
        c.add_sessions(sessions)
        contentions = c.detect_contention()
        self.assertGreaterEqual(len(contentions), 1)
        self.assertEqual(contentions[0].resource_name, "gpt-4")
        self.assertGreaterEqual(contentions[0].concurrent_count, 3)

    def test_no_contention_below_threshold(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=1, duration_ms=100)])
        s2 = _session("b", events=[_event(model="gpt-4", ts_minutes=1, duration_ms=100)])
        c = SessionCorrelator(contention_threshold=3)
        c.add_sessions([s1, s2])
        contentions = c.detect_contention()
        self.assertEqual(len(contentions), 0)

    def test_contention_severity_levels(self):
        self.assertEqual(
            SessionCorrelator._contention_severity(3), ContentionSeverity.MEDIUM
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(5), ContentionSeverity.HIGH
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(8), ContentionSeverity.CRITICAL
        )
        self.assertEqual(
            SessionCorrelator._contention_severity(2), ContentionSeverity.LOW
        )

    def test_contention_to_dict(self):
        rc = ResourceContention(
            resource_name="gpt-4", resource_type="model",
            sessions_involved=["a", "b", "c"],
            contention_window_start=_utc(), contention_window_end=_utc(0, 5),
            concurrent_count=3, severity=ContentionSeverity.MEDIUM,
            estimated_delay_ms=150,
        )
        d = rc.to_dict()
        self.assertEqual(d["severity"], "medium")
        self.assertEqual(d["concurrent_count"], 3)


# ── Model Hotspots ───────────────────────────────────────────────────

class TestModelHotspots(unittest.TestCase):
    def test_find_hotspot(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=5, duration_ms=5000)])
        s2 = _session("b", events=[_event(model="gpt-4", ts_minutes=5, duration_ms=5000)])
        c = SessionCorrelator()
        c.add_sessions([s1, s2])
        hotspots = c.find_model_hotspots()
        self.assertIn("gpt-4", hotspots)
        self.assertEqual(hotspots["gpt-4"], 2)

    def test_no_hotspot_single_use(self):
        s1 = _session("a", events=[_event(model="gpt-4", ts_minutes=0)])
        c = SessionCorrelator()
        c.add_session(s1)
        hotspots = c.find_model_hotspots()
        self.assertEqual(len(hotspots), 0)


# ── Full Correlation ─────────────────────────────────────────────────

class TestCorrelate(unittest.TestCase):
    def test_full_correlation(self):
        s1 = _session("a", start_min=0, end_min=10, events=[
            _event(model="gpt-4", ts_minutes=1, duration_ms=5000),
            _event(tool_name="search", ts_minutes=3),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=5),
        ])
        s2 = _session("b", start_min=2, end_min=12, events=[
            _event(model="gpt-4", ts_minutes=3, duration_ms=5000),
            _event(tool_name="search", ts_minutes=3, ts_seconds=0.1),
            _event(event_type="agent_error", model="gpt-4", ts_minutes=5, ts_seconds=2),
        ])
        c = SessionCorrelator(sync_window_ms=1000, error_propagation_window_ms=10000)
        c.add_sessions([s1, s2])
        report = c.correlate()

        self.assertEqual(report.session_count, 2)
        self.assertEqual(report.total_events, 6)
        self.assertGreater(len(report.overlaps), 0)
        self.assertGreater(len(report.shared_resources), 0)
        self.assertGreater(report.total_correlations, 0)

    def test_static_compare(self):
        s1 = _session("a", events=[_event(model="gpt-4")])
        s2 = _session("b", events=[_event(model="gpt-4")])
        report = SessionCorrelator.compare([s1, s2])
        self.assertEqual(report.session_count, 2)

    def test_empty_correlator(self):
        c = SessionCorrelator()
        report = c.correlate()
        self.assertEqual(report.session_count, 0)
        self.assertEqual(report.total_correlations, 0)

    def test_clear(self):
        c = SessionCorrelator()
        c.add_session(_session("a"))
        self.assertEqual(len(c._sessions), 1)
        c.clear()
        self.assertEqual(len(c._sessions), 0)


# ── Report ───────────────────────────────────────────────────────────

class TestReport(unittest.TestCase):
    def test_summary(self):
        report = CorrelationReport(session_count=3, total_events=15)
        summary = report.summary()
        self.assertEqual(summary["session_count"], 3)
        self.assertFalse(summary["has_contention"])
        self.assertFalse(summary["has_error_propagation"])

    def test_render(self):
        report = CorrelationReport(session_count=2, total_events=10)
        report.overlaps = [TemporalOverlap(
            session_a="a", session_b="b",
            overlap_start=_utc(), overlap_end=_utc(5),
            overlap_ms=300000, overlap_pct_a=50, overlap_pct_b=100,
        )]
        text = report.render()
        self.assertIn("Session Correlation Report", text)
        self.assertIn("Temporal Overlaps", text)

    def test_to_dict(self):
        report = CorrelationReport(session_count=1, total_events=5)
        d = report.to_dict()
        self.assertIn("summary", d)
        self.assertIn("overlaps", d)
        self.assertIn("contentions", d)

    def test_max_contention_severity(self):
        report = CorrelationReport()
        self.assertIsNone(report.max_contention_severity)

        report.contentions = [
            ResourceContention(
                resource_name="x", resource_type="model",
                sessions_involved=["a"], contention_window_start=_utc(),
                contention_window_end=_utc(1), concurrent_count=3,
                severity=ContentionSeverity.MEDIUM, estimated_delay_ms=100,
            ),
            ResourceContention(
                resource_name="y", resource_type="tool",
                sessions_involved=["a"], contention_window_start=_utc(),
                contention_window_end=_utc(1), concurrent_count=8,
                severity=ContentionSeverity.CRITICAL, estimated_delay_ms=400,
            ),
        ]
        self.assertEqual(report.max_contention_severity, ContentionSeverity.CRITICAL)

    def test_avg_overlap_pct(self):
        report = CorrelationReport()
        self.assertEqual(report.avg_overlap_pct, 0.0)
        report.overlaps = [
            TemporalOverlap("a", "b", _utc(), _utc(5), 1000, 50.0, 100.0),
        ]
        self.assertAlmostEqual(report.avg_overlap_pct, 75.0)

    def test_render_all_sections(self):
        report = CorrelationReport(session_count=2, total_events=10)
        report.analysis_window_start = _utc()
        report.analysis_window_end = _utc(10)
        report.overlaps = [TemporalOverlap("a", "b", _utc(), _utc(5), 1000, 50, 100)]
        report.shared_resources = [SharedResource("gpt-4", "model", ["a", "b"], 5, 2)]
        report.error_propagations = [ErrorPropagation(
            "a", "b", _utc(), _utc(0, 1), 1000,
            PropagationDirection.FORWARD, ["gpt-4"], 0.8,
        )]
        report.sync_points = [SyncPoint(_utc(), "gpt-4", ["a", "b"], 100)]
        report.contentions = [ResourceContention(
            "gpt-4", "model", ["a", "b", "c"], _utc(), _utc(0, 5),
            3, ContentionSeverity.MEDIUM, 150,
        )]
        report.model_hotspots = {"gpt-4": 3}
        text = report.render()
        for section in ["Temporal Overlaps", "Shared Resources", "Error Propagation",
                       "Sync Points", "Resource Contention", "Model Hotspots"]:
            self.assertIn(section, text)


# ── Edge Cases ───────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):
    def test_single_session(self):
        c = SessionCorrelator()
        c.add_session(_session("a", events=[_event(model="gpt-4")]))
        report = c.correlate()
        self.assertEqual(report.session_count, 1)
        self.assertEqual(len(report.overlaps), 0)

    def test_session_no_events(self):
        c = SessionCorrelator()
        c.add_sessions([_session("a"), _session("b")])
        report = c.correlate()
        self.assertEqual(report.total_events, 0)

    def test_many_sessions(self):
        sessions = [
            _session(f"s{i}", start_min=i, end_min=i + 5,
                     events=[_event(model="gpt-4", ts_minutes=i + 1)])
            for i in range(10)
        ]
        c = SessionCorrelator()
        c.add_sessions(sessions)
        report = c.correlate()
        self.assertEqual(report.session_count, 10)
        self.assertGreater(len(report.overlaps), 0)


# ── Enum coverage ────────────────────────────────────────────────────

class TestEnums(unittest.TestCase):
    def test_correlation_kind_values(self):
        self.assertEqual(CorrelationKind.TEMPORAL_OVERLAP.value, "temporal_overlap")
        self.assertEqual(CorrelationKind.CASCADING_FAILURE.value, "cascading_failure")

    def test_contention_severity_values(self):
        self.assertEqual(ContentionSeverity.LOW.value, "low")
        self.assertEqual(ContentionSeverity.CRITICAL.value, "critical")

    def test_propagation_direction_values(self):
        self.assertEqual(PropagationDirection.FORWARD.value, "forward")
        self.assertEqual(PropagationDirection.MUTUAL.value, "mutual")


if __name__ == "__main__":
    unittest.main()
