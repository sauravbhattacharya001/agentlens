"""Tests for the span context manager feature."""

import time
from unittest.mock import MagicMock

import pytest

from agentlens.span import Span
from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


def _make_tracker() -> AgentTracker:
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test"
    transport.send_events = MagicMock()
    transport.flush = MagicMock()
    tracker = AgentTracker(transport=transport)
    return tracker


class TestSpanModel:
    def test_defaults(self):
        s = Span(name="test")
        assert s.name == "test"
        assert s.status == "active"
        assert s.parent_id is None
        assert s.event_count == 0
        assert s.children == []

    def test_set_attribute(self):
        s = Span(name="x")
        s.set_attribute("foo", 42)
        assert s.attributes["foo"] == 42

    def test_set_status(self):
        s = Span(name="x")
        s.set_status("error", "boom")
        assert s.status == "error"
        assert s.error == "boom"

    def test_to_dict(self):
        s = Span(name="test", session_id="s1")
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["session_id"] == "s1"
        assert "parent_id" not in d  # None is excluded


class TestTrackerSpan:
    def test_basic_span(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("planning") as s:
            assert s.status == "active"
            assert tracker.current_span is s
            tracker.track(event_type="llm_call", model="gpt-4o", tokens_in=100)

        assert s.status == "completed"
        assert s.duration_ms is not None
        assert s.duration_ms >= 0
        assert s.event_count == 1
        assert s.ended_at is not None
        assert tracker.current_span is None

    def test_nested_spans(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("outer") as outer:
            tracker.track(event_type="llm_call")
            with tracker.span("inner") as inner:
                tracker.track(event_type="tool_call")
                assert tracker.current_span is inner
                assert inner.parent_id == outer.span_id

            assert tracker.current_span is outer
            assert inner.span_id in outer.children

        assert outer.event_count == 2  # both events
        assert inner.event_count == 1  # only inner event
        assert tracker.current_span is None

    def test_span_error(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with pytest.raises(ValueError, match="oops"):
            with tracker.span("failing") as s:
                raise ValueError("oops")

        assert s.status == "error"
        assert s.error == "oops"
        assert s.duration_ms is not None
        assert tracker.current_span is None

    def test_span_attributes(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("work", attributes={"initial": True}) as s:
            s.set_attribute("result", "ok")

        assert s.attributes == {"initial": True, "result": "ok"}

    def test_span_sends_events_to_transport(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("test") as s:
            pass

        calls = tracker.transport.send_events.call_args_list
        # session_start + span_start + span_end = at least 3 calls
        event_types = []
        for call in calls:
            events = call[0][0]
            for ev in events:
                if "event_type" in ev:
                    event_types.append(ev["event_type"])

        assert "span_start" in event_types
        assert "span_end" in event_types

    def test_span_id_attached_to_tracked_events(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("work") as s:
            tracker.track(event_type="llm_call")

        # Find the llm_call event sent to transport
        for call in tracker.transport.send_events.call_args_list:
            events = call[0][0]
            for ev in events:
                if ev.get("event_type") == "llm_call":
                    assert ev.get("span_id") == s.span_id
                    return

        pytest.fail("llm_call event not found in transport calls")

    def test_no_span_no_crash(self):
        tracker = _make_tracker()
        tracker.start_session("agent")
        # Tracking without a span should work fine
        tracker.track(event_type="llm_call")
        assert tracker.current_span is None

    def test_manual_status_override(self):
        tracker = _make_tracker()
        tracker.start_session("agent")

        with tracker.span("work") as s:
            s.set_status("error", "manual error")

        # Manual status should stick (not overridden to "completed")
        assert s.status == "error"
        assert s.error == "manual error"
