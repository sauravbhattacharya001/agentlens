"""Tests for SessionReplayer — step-by-step session replay and export."""

import json
import pytest
from datetime import datetime, timedelta, timezone

from agentlens.models import AgentEvent, Session, ToolCall, DecisionTrace
from agentlens.replayer import (
    SessionReplayer,
    ReplayFrame,
    ReplayStats,
)


def _ts(offset_s=0):
    """Create a UTC timestamp with offset in seconds."""
    return datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _make_session(n_events=5, agent_name="test-agent"):
    """Create a session with n events spaced 1 second apart."""
    session = Session(session_id="sess-test", agent_name=agent_name)
    for i in range(n_events):
        event = AgentEvent(
            event_id=f"evt-{i}",
            event_type="llm_call" if i % 2 == 0 else "tool_call",
            model="gpt-4o" if i % 2 == 0 else None,
            tokens_in=100 * (i + 1),
            tokens_out=50 * (i + 1),
            timestamp=_ts(i),
            duration_ms=float(100 + i * 50),
        )
        if i % 2 == 1:
            event.tool_call = ToolCall(tool_name=f"tool_{i}")
        session.add_event(event)
    return session


# ── ReplayFrame ──────────────────────────────────────────────────────


class TestReplayFrame:
    def test_progress_calculation(self):
        event = AgentEvent(event_id="e1", timestamp=_ts())
        frame = ReplayFrame(index=2, total=10, event=event, wall_delay_ms=0, elapsed_ms=0)
        assert frame.progress == 0.3
        assert frame.progress_pct == 30.0

    def test_progress_zero_total(self):
        event = AgentEvent(event_id="e1", timestamp=_ts())
        frame = ReplayFrame(index=0, total=0, event=event, wall_delay_ms=0, elapsed_ms=0)
        assert frame.progress == 0.0

    def test_to_dict(self):
        event = AgentEvent(
            event_id="e1",
            event_type="llm_call",
            model="gpt-4o",
            tokens_in=100,
            tokens_out=50,
            timestamp=_ts(),
            duration_ms=200.0,
        )
        frame = ReplayFrame(
            index=0, total=5, event=event,
            wall_delay_ms=100.5, elapsed_ms=0,
            is_breakpoint=True, annotations=["note1"],
        )
        d = frame.to_dict()
        assert d["event_id"] == "e1"
        assert d["event_type"] == "llm_call"
        assert d["model"] == "gpt-4o"
        assert d["is_breakpoint"] is True
        assert d["annotations"] == ["note1"]
        assert d["progress_pct"] == 20.0
        assert d["tool_name"] is None

    def test_to_dict_with_tool(self):
        event = AgentEvent(
            event_id="e1",
            event_type="tool_call",
            timestamp=_ts(),
            tool_call=ToolCall(tool_name="search"),
        )
        frame = ReplayFrame(index=0, total=1, event=event, wall_delay_ms=0, elapsed_ms=0)
        d = frame.to_dict()
        assert d["tool_name"] == "search"

    def test_to_text(self):
        event = AgentEvent(
            event_id="e1",
            event_type="llm_call",
            model="gpt-4o",
            tokens_in=100,
            tokens_out=50,
            timestamp=_ts(),
            duration_ms=200.0,
        )
        frame = ReplayFrame(
            index=0, total=5, event=event,
            wall_delay_ms=50, elapsed_ms=0,
        )
        text = frame.to_text()
        assert "[1/5]" in text
        assert "llm_call" in text
        assert "gpt-4o" in text
        assert "tok=100→50" in text
        assert "dur=200ms" in text

    def test_to_text_breakpoint(self):
        event = AgentEvent(event_id="e1", timestamp=_ts())
        frame = ReplayFrame(
            index=0, total=1, event=event,
            wall_delay_ms=0, elapsed_ms=0,
            is_breakpoint=True,
        )
        text = frame.to_text()
        assert "BREAKPOINT" in text

    def test_to_text_annotations(self):
        event = AgentEvent(event_id="e1", timestamp=_ts())
        frame = ReplayFrame(
            index=0, total=1, event=event,
            wall_delay_ms=0, elapsed_ms=0,
            annotations=["important", "review"],
        )
        text = frame.to_text()
        assert "important" in text
        assert "review" in text


# ── ReplayStats ──────────────────────────────────────────────────────


class TestReplayStats:
    def test_to_dict(self):
        stats = ReplayStats(
            total_events=10,
            played_events=8,
            filtered_events=2,
            breakpoints_hit=1,
            models_used={"gpt-4o", "gpt-4o-mini"},
            tools_used={"search"},
        )
        d = stats.to_dict()
        assert d["total_events"] == 10
        assert d["played_events"] == 8
        assert "gpt-4o" in d["models_used"]
        assert "search" in d["tools_used"]

    def test_summary(self):
        stats = ReplayStats(
            total_events=5,
            played_events=5,
            original_duration_ms=5000,
            replay_duration_ms=2500,
            speed=2.0,
            total_tokens_in=500,
            total_tokens_out=250,
            models_used={"gpt-4o"},
            event_type_counts={"llm_call": 3, "tool_call": 2},
        )
        summary = stats.summary()
        assert "Replay Summary" in summary
        assert "5/5 played" in summary
        assert "gpt-4o" in summary
        assert "llm_call: 3" in summary

    def test_summary_with_filtered(self):
        stats = ReplayStats(
            total_events=10,
            played_events=7,
            filtered_events=3,
        )
        summary = stats.summary()
        assert "3 filtered" in summary


# ── SessionReplayer ──────────────────────────────────────────────────


class TestSessionReplayer:
    def test_basic_play(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        frames = list(replayer.play())
        assert len(frames) == 3
        assert frames[0].index == 0
        assert frames[-1].index == 2

    def test_events_sorted_by_timestamp(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        events = replayer.events
        for i in range(len(events) - 1):
            assert events[i].timestamp <= events[i + 1].timestamp

    def test_speed_affects_wall_delay(self):
        session = _make_session(3)
        slow = SessionReplayer(session, speed=1.0)
        fast = SessionReplayer(session, speed=2.0)

        slow_frames = list(slow.play())
        fast_frames = list(fast.play())

        # Second frame should have half the delay at 2x speed
        if len(slow_frames) > 1 and slow_frames[1].wall_delay_ms > 0:
            ratio = fast_frames[1].wall_delay_ms / slow_frames[1].wall_delay_ms
            assert abs(ratio - 0.5) < 0.01

    def test_invalid_speed_raises(self):
        session = _make_session(1)
        with pytest.raises(ValueError):
            SessionReplayer(session, speed=0)
        with pytest.raises(ValueError):
            SessionReplayer(session, speed=-1)

    def test_set_speed(self):
        session = _make_session(1)
        replayer = SessionReplayer(session)
        result = replayer.set_speed(3.0)
        assert result is replayer  # chainable

    def test_set_speed_invalid(self):
        session = _make_session(1)
        replayer = SessionReplayer(session)
        with pytest.raises(ValueError):
            replayer.set_speed(0)

    def test_filter_include(self):
        session = _make_session(6)  # alternates llm_call/tool_call
        replayer = SessionReplayer(session)
        replayer.add_filter("llm_call")

        frames = list(replayer.play())
        assert all(f.event.event_type == "llm_call" for f in frames)
        assert len(frames) == 3  # indices 0,2,4

    def test_exclude_filter(self):
        session = _make_session(6)
        replayer = SessionReplayer(session)
        replayer.exclude("tool_call")

        frames = list(replayer.play())
        assert all(f.event.event_type != "tool_call" for f in frames)

    def test_clear_filters(self):
        session = _make_session(4)
        replayer = SessionReplayer(session)
        replayer.add_filter("llm_call")
        replayer.exclude("tool_call")
        replayer.clear_filters()

        frames = list(replayer.play())
        assert len(frames) == 4

    def test_remove_filter(self):
        session = _make_session(4)
        replayer = SessionReplayer(session)
        replayer.add_filter("llm_call", "tool_call")
        replayer.remove_filter("tool_call")

        frames = list(replayer.play())
        assert all(f.event.event_type == "llm_call" for f in frames)

    def test_breakpoint(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        replayer.add_breakpoint(lambda e: e.event_type == "tool_call")

        frames = list(replayer.play())
        bp_frames = [f for f in frames if f.is_breakpoint]
        assert len(bp_frames) > 0
        assert all(f.event.event_type == "tool_call" for f in bp_frames)

    def test_clear_breakpoints(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        replayer.add_breakpoint(lambda e: True)
        replayer.clear_breakpoints()

        frames = list(replayer.play())
        assert all(not f.is_breakpoint for f in frames)

    def test_on_frame_callback(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        captured = []
        replayer.on_frame(lambda f: captured.append(f.index))

        list(replayer.play())
        assert captured == [0, 1, 2]

    def test_annotate(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        replayer.annotate("evt-1", "interesting event")

        frames = list(replayer.play())
        annotated = [f for f in frames if f.annotations]
        assert len(annotated) == 1
        assert "interesting event" in annotated[0].annotations

    def test_empty_session(self):
        session = Session(session_id="empty")
        replayer = SessionReplayer(session)
        frames = list(replayer.play())
        assert len(frames) == 0

    def test_filtered_events_property(self):
        session = _make_session(6)
        replayer = SessionReplayer(session)
        replayer.add_filter("llm_call")
        assert len(replayer.filtered_events) == 3

    def test_stats_populated_after_play(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        list(replayer.play())

        stats = replayer.stats
        assert stats.total_events == 5
        assert stats.played_events == 5
        assert stats.total_tokens_in > 0
        assert stats.total_tokens_out > 0
        assert len(stats.models_used) > 0

    def test_stats_breakpoints_counted(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        replayer.add_breakpoint(lambda e: e.event_type == "tool_call")
        list(replayer.play())

        assert replayer.stats.breakpoints_hit > 0

    def test_stats_filtered_counted(self):
        session = _make_session(6)
        replayer = SessionReplayer(session)
        replayer.add_filter("llm_call")
        list(replayer.play())

        assert replayer.stats.filtered_events == 3

    def test_play_range(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        frames = list(replayer.play_range(start=1, end=3))
        assert len(frames) == 2
        assert frames[0].index == 1
        assert frames[1].index == 2

    def test_step_through(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)

        f1 = replayer.step()
        assert f1 is not None
        assert f1.index == 0

        f2 = replayer.step()
        assert f2 is not None
        assert f2.index == 1

        f3 = replayer.step()
        assert f3 is not None
        assert f3.index == 2

        f4 = replayer.step()
        assert f4 is None  # past end

    def test_reset(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        replayer.step()
        replayer.step()
        replayer.reset()
        f = replayer.step()
        assert f is not None
        assert f.index == 0

    def test_seek(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        replayer.seek(3)
        f = replayer.step()
        assert f is not None
        assert f.index == 3

    def test_seek_negative_clamped(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        replayer.seek(-5)
        f = replayer.step()
        assert f is not None
        assert f.index == 0

    # -- Export -----------------------------------------------------------

    def test_to_json(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        result = json.loads(replayer.to_json())
        assert result["session_id"] == "sess-test"
        assert result["agent_name"] == "test-agent"
        assert len(result["frames"]) == 3
        assert "stats" in result

    def test_to_text(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        text = replayer.to_text()
        assert "Replay: session=sess-test" in text
        assert "Replay Summary" in text

    def test_to_markdown(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        md = replayer.to_markdown()
        assert "# Session Replay:" in md
        assert "## Timeline" in md
        assert "| # | Type |" in md
        assert "## Stats" in md

    def test_to_markdown_with_breakpoint(self):
        session = _make_session(3)
        replayer = SessionReplayer(session)
        replayer.add_breakpoint(lambda e: e.event_type == "tool_call")
        md = replayer.to_markdown()
        assert "⏸" in md

    # -- Diff -----------------------------------------------------------

    def test_diff(self):
        session_a = _make_session(5, agent_name="agent-a")
        session_b = _make_session(3, agent_name="agent-b")

        result = SessionReplayer.diff(session_a, session_b)
        assert result["session_a"] == "sess-test"
        assert result["event_count"]["a"] == 5
        assert result["event_count"]["b"] == 3
        assert "duration_ms" in result
        assert "tokens" in result
        assert "event_types" in result

    def test_diff_empty_sessions(self):
        a = Session(session_id="a")
        b = Session(session_id="b")
        result = SessionReplayer.diff(a, b)
        assert result["event_count"]["a"] == 0
        assert result["event_count"]["b"] == 0
        assert result["duration_ms"]["a"] == 0.0

    def test_diff_tokens(self):
        session_a = _make_session(3)
        session_b = _make_session(2)
        result = SessionReplayer.diff(session_a, session_b)
        assert result["tokens"]["a"]["in"] > 0
        assert result["tokens"]["b"]["in"] > 0

    # -- Chaining ---------------------------------------------------------

    def test_method_chaining(self):
        session = _make_session(5)
        replayer = (
            SessionReplayer(session)
            .set_speed(2.0)
            .add_filter("llm_call")
            .exclude("error")
            .add_breakpoint(lambda e: False)
            .annotate("evt-0", "test")
        )
        frames = list(replayer.play())
        assert len(frames) > 0

    # -- Progress tracking ─────────────────────────────────────────────

    def test_progress_increases(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        frames = list(replayer.play())
        for i in range(len(frames) - 1):
            assert frames[i].progress < frames[i + 1].progress
        assert frames[-1].progress == 1.0

    def test_elapsed_ms_increases(self):
        session = _make_session(5)
        replayer = SessionReplayer(session)
        frames = list(replayer.play())
        for i in range(len(frames) - 1):
            assert frames[i].elapsed_ms <= frames[i + 1].elapsed_ms
