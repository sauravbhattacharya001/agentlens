"""Tests for agentlens.replayer -- SessionReplayer."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.replayer import ReplayFrame, ReplayStats, SessionReplayer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_ms: int = 0) -> datetime:
    return datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc) + timedelta(
        milliseconds=offset_ms
    )


def _event(
    etype: str = "generic",
    offset_ms: int = 0,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    duration_ms: float | None = None,
    tool_name: str | None = None,
) -> AgentEvent:
    tc = ToolCall(tool_name=tool_name) if tool_name else None
    return AgentEvent(
        event_type=etype,
        timestamp=_ts(offset_ms),
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        tool_call=tc,
    )


def _session(events: list[AgentEvent] | None = None) -> Session:
    s = Session(agent_name="test-agent")
    for e in events or []:
        s.add_event(e)
    return s


def _default_session() -> Session:
    return _session([
        _event("llm_call", 0, model="gpt-4", tokens_in=100, tokens_out=50, duration_ms=200),
        _event("tool_call", 500, tool_name="search", duration_ms=150),
        _event("decision", 1000, duration_ms=10),
        _event("llm_call", 1500, model="gpt-4", tokens_in=80, tokens_out=40, duration_ms=180),
        _event("error", 2000, duration_ms=5),
    ])


# ---------------------------------------------------------------------------
# Basic replay
# ---------------------------------------------------------------------------

class TestBasicReplay:
    def test_play_yields_all_events(self):
        s = _default_session()
        r = SessionReplayer(s)
        frames = list(r.play())
        assert len(frames) == 5

    def test_play_order_is_chronological(self):
        s = _default_session()
        frames = list(SessionReplayer(s).play())
        for i in range(1, len(frames)):
            assert frames[i].event.timestamp >= frames[i - 1].event.timestamp

    def test_first_frame_has_zero_delay(self):
        frames = list(SessionReplayer(_default_session()).play())
        assert frames[0].wall_delay_ms == 0.0

    def test_progress_increases(self):
        frames = list(SessionReplayer(_default_session()).play())
        for i in range(1, len(frames)):
            assert frames[i].progress > frames[i - 1].progress
        assert frames[-1].progress == 1.0

    def test_empty_session(self):
        s = _session([])
        frames = list(SessionReplayer(s).play())
        assert frames == []

    def test_single_event(self):
        s = _session([_event("llm_call", 0)])
        frames = list(SessionReplayer(s).play())
        assert len(frames) == 1
        assert frames[0].progress == 1.0
        assert frames[0].wall_delay_ms == 0.0


# ---------------------------------------------------------------------------
# Speed control
# ---------------------------------------------------------------------------

class TestSpeed:
    def test_speed_2x_halves_delays(self):
        s = _default_session()
        normal = list(SessionReplayer(s, speed=1.0).play())
        fast = list(SessionReplayer(s, speed=2.0).play())
        for n, f in zip(normal[1:], fast[1:]):
            assert abs(f.wall_delay_ms - n.wall_delay_ms / 2) < 0.01

    def test_speed_half_doubles_delays(self):
        s = _default_session()
        normal = list(SessionReplayer(s, speed=1.0).play())
        slow = list(SessionReplayer(s, speed=0.5).play())
        for n, sl in zip(normal[1:], slow[1:]):
            assert abs(sl.wall_delay_ms - n.wall_delay_ms * 2) < 0.01

    def test_set_speed(self):
        r = SessionReplayer(_default_session())
        r.set_speed(3.0)
        frames = list(r.play())
        assert frames[1].wall_delay_ms == pytest.approx(500 / 3.0, abs=0.1)

    def test_invalid_speed_raises(self):
        with pytest.raises(ValueError):
            SessionReplayer(_default_session(), speed=0)
        with pytest.raises(ValueError):
            SessionReplayer(_default_session(), speed=-1)

    def test_set_speed_invalid(self):
        r = SessionReplayer(_default_session())
        with pytest.raises(ValueError):
            r.set_speed(0)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestFilters:
    def test_include_filter(self):
        r = SessionReplayer(_default_session())
        r.add_filter("llm_call")
        frames = list(r.play())
        assert len(frames) == 2
        assert all(f.event.event_type == "llm_call" for f in frames)

    def test_multi_include_filter(self):
        r = SessionReplayer(_default_session())
        r.add_filter("llm_call", "error")
        frames = list(r.play())
        assert len(frames) == 3

    def test_exclude_filter(self):
        r = SessionReplayer(_default_session())
        r.exclude("error")
        frames = list(r.play())
        assert len(frames) == 4
        assert all(f.event.event_type != "error" for f in frames)

    def test_clear_filters(self):
        r = SessionReplayer(_default_session())
        r.add_filter("llm_call")
        r.clear_filters()
        frames = list(r.play())
        assert len(frames) == 5

    def test_exclude_overrides_include(self):
        r = SessionReplayer(_default_session())
        r.add_filter("llm_call", "error")
        r.exclude("error")
        frames = list(r.play())
        assert len(frames) == 2
        assert all(f.event.event_type == "llm_call" for f in frames)

    def test_filter_nonexistent_type(self):
        r = SessionReplayer(_default_session())
        r.add_filter("nonexistent")
        assert list(r.play()) == []


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

class TestBreakpoints:
    def test_breakpoint_on_error(self):
        r = SessionReplayer(_default_session())
        r.add_breakpoint(lambda e: e.event_type == "error")
        frames = list(r.play())
        bp_frames = [f for f in frames if f.is_breakpoint]
        assert len(bp_frames) == 1
        assert bp_frames[0].event.event_type == "error"

    def test_multiple_breakpoints(self):
        r = SessionReplayer(_default_session())
        r.add_breakpoint(lambda e: e.event_type == "error")
        r.add_breakpoint(lambda e: e.event_type == "decision")
        frames = list(r.play())
        bp = [f for f in frames if f.is_breakpoint]
        assert len(bp) == 2

    def test_clear_breakpoints(self):
        r = SessionReplayer(_default_session())
        r.add_breakpoint(lambda e: True)
        r.clear_breakpoints()
        frames = list(r.play())
        assert not any(f.is_breakpoint for f in frames)

    def test_breakpoint_counted_in_stats(self):
        r = SessionReplayer(_default_session())
        r.add_breakpoint(lambda e: e.event_type == "error")
        list(r.play())
        assert r.stats.breakpoints_hit == 1


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_on_frame_called(self):
        captured = []
        r = SessionReplayer(_default_session())
        r.on_frame(lambda f: captured.append(f.index))
        list(r.play())
        assert captured == [0, 1, 2, 3, 4]

    def test_multiple_callbacks(self):
        a, b = [], []
        r = SessionReplayer(_default_session())
        r.on_frame(lambda f: a.append(1))
        r.on_frame(lambda f: b.append(1))
        list(r.play())
        assert len(a) == 5
        assert len(b) == 5


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

class TestAnnotations:
    def test_annotate_event(self):
        s = _default_session()
        eid = s.events[2].event_id
        r = SessionReplayer(s)
        r.annotate(eid, "interesting decision")
        frames = list(r.play())
        annotated = [f for f in frames if f.annotations]
        assert len(annotated) == 1
        assert "interesting decision" in annotated[0].annotations

    def test_multiple_annotations(self):
        s = _default_session()
        eid = s.events[0].event_id
        r = SessionReplayer(s)
        r.annotate(eid, "note1")
        r.annotate(eid, "note2")
        frames = list(r.play())
        assert len(frames[0].annotations) == 2


# ---------------------------------------------------------------------------
# Step-through
# ---------------------------------------------------------------------------

class TestStepThrough:
    def test_step_advances(self):
        r = SessionReplayer(_default_session())
        f1 = r.step()
        f2 = r.step()
        assert f1 is not None and f1.index == 0
        assert f2 is not None and f2.index == 1

    def test_step_past_end_returns_none(self):
        s = _session([_event("llm_call", 0)])
        r = SessionReplayer(s)
        assert r.step() is not None
        assert r.step() is None

    def test_reset_resets_position(self):
        r = SessionReplayer(_default_session())
        r.step()
        r.step()
        r.reset()
        f = r.step()
        assert f is not None and f.index == 0

    def test_seek(self):
        r = SessionReplayer(_default_session())
        r.seek(3)
        f = r.step()
        assert f is not None and f.index == 3


# ---------------------------------------------------------------------------
# play_range
# ---------------------------------------------------------------------------

class TestPlayRange:
    def test_range_slice(self):
        r = SessionReplayer(_default_session())
        frames = list(r.play_range(1, 3))
        assert len(frames) == 2
        assert frames[0].index == 1
        assert frames[1].index == 2

    def test_range_from_start(self):
        r = SessionReplayer(_default_session())
        frames = list(r.play_range(0, 2))
        assert len(frames) == 2

    def test_range_to_end(self):
        r = SessionReplayer(_default_session())
        frames = list(r.play_range(3))
        assert len(frames) == 2


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_after_play(self):
        r = SessionReplayer(_default_session())
        list(r.play())
        s = r.stats
        assert s.played_events == 5
        assert s.total_events == 5
        assert s.filtered_events == 0
        assert s.total_tokens_in == 180
        assert s.total_tokens_out == 90
        assert "gpt-4" in s.models_used
        assert "search" in s.tools_used

    def test_stats_with_filter(self):
        r = SessionReplayer(_default_session())
        r.add_filter("llm_call")
        list(r.play())
        assert r.stats.played_events == 2
        assert r.stats.filtered_events == 3

    def test_event_type_counts(self):
        r = SessionReplayer(_default_session())
        list(r.play())
        assert r.stats.event_type_counts["llm_call"] == 2
        assert r.stats.event_type_counts["error"] == 1

    def test_stats_summary_is_string(self):
        r = SessionReplayer(_default_session())
        list(r.play())
        summary = r.stats.summary()
        assert "Replay Summary" in summary
        assert "gpt-4" in summary

    def test_stats_to_dict(self):
        r = SessionReplayer(_default_session())
        list(r.play())
        d = r.stats.to_dict()
        assert d["played_events"] == 5
        assert isinstance(d["models_used"], list)

    def test_original_duration(self):
        r = SessionReplayer(_default_session())
        list(r.play())
        assert r.stats.original_duration_ms == 2000.0


# ---------------------------------------------------------------------------
# Export: JSON
# ---------------------------------------------------------------------------

class TestExportJSON:
    def test_valid_json(self):
        r = SessionReplayer(_default_session())
        data = json.loads(r.to_json())
        assert "frames" in data
        assert "stats" in data
        assert len(data["frames"]) == 5

    def test_json_includes_session_info(self):
        s = _default_session()
        data = json.loads(SessionReplayer(s).to_json())
        assert data["session_id"] == s.session_id
        assert data["agent_name"] == "test-agent"

    def test_json_frame_fields(self):
        data = json.loads(SessionReplayer(_default_session()).to_json())
        f = data["frames"][0]
        assert "event_id" in f
        assert "event_type" in f
        assert "progress_pct" in f


# ---------------------------------------------------------------------------
# Export: Text
# ---------------------------------------------------------------------------

class TestExportText:
    def test_text_output(self):
        text = SessionReplayer(_default_session()).to_text()
        assert "Replay:" in text
        assert "llm_call" in text
        assert "Replay Summary" in text

    def test_text_includes_tool(self):
        text = SessionReplayer(_default_session()).to_text()
        assert "tool=search" in text


# ---------------------------------------------------------------------------
# Export: Markdown
# ---------------------------------------------------------------------------

class TestExportMarkdown:
    def test_markdown_has_table(self):
        md = SessionReplayer(_default_session()).to_markdown()
        assert "| # |" in md
        assert "## Timeline" in md

    def test_markdown_has_stats(self):
        md = SessionReplayer(_default_session()).to_markdown()
        assert "## Stats" in md


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

class TestDiff:
    def test_diff_structure(self):
        sa = _default_session()
        sb = _session([
            _event("llm_call", 0, model="claude-3", tokens_in=200, tokens_out=100),
            _event("tool_call", 1000, tool_name="code_exec"),
        ])
        d = SessionReplayer.diff(sa, sb)
        assert d["event_count"]["a"] == 5
        assert d["event_count"]["b"] == 2
        assert "llm_call" in d["event_types"]

    def test_diff_tokens(self):
        sa = _default_session()
        sb = _session([_event("llm_call", 0, tokens_in=50, tokens_out=25)])
        d = SessionReplayer.diff(sa, sb)
        assert d["tokens"]["a"]["in"] == 180
        assert d["tokens"]["b"]["in"] == 50

    def test_diff_same_session(self):
        s = _default_session()
        d = SessionReplayer.diff(s, s)
        assert d["event_count"]["a"] == d["event_count"]["b"]


# ---------------------------------------------------------------------------
# ReplayFrame
# ---------------------------------------------------------------------------

class TestReplayFrame:
    def test_to_dict(self):
        s = _default_session()
        frames = list(SessionReplayer(s).play())
        d = frames[0].to_dict()
        assert d["index"] == 0
        assert d["event_type"] == "llm_call"
        assert d["model"] == "gpt-4"

    def test_to_text(self):
        frames = list(SessionReplayer(_default_session()).play())
        t = frames[1].to_text()
        assert "tool_call" in t
        assert "tool=search" in t

    def test_progress_pct(self):
        frames = list(SessionReplayer(_default_session()).play())
        assert frames[-1].progress_pct == 100.0
        assert frames[0].progress_pct == 20.0


# ---------------------------------------------------------------------------
# Chaining
# ---------------------------------------------------------------------------

class TestChaining:
    def test_fluent_api(self):
        r = (
            SessionReplayer(_default_session())
            .set_speed(2.0)
            .add_filter("llm_call", "tool_call")
            .add_breakpoint(lambda e: e.event_type == "tool_call")
        )
        frames = list(r.play())
        assert len(frames) == 3
        assert any(f.is_breakpoint for f in frames)

    def test_chained_exclude(self):
        r = SessionReplayer(_default_session()).exclude("error", "decision")
        frames = list(r.play())
        assert len(frames) == 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_events_out_of_order_sorted(self):
        """Events added out of order should still replay chronologically."""
        s = _session([
            _event("b", 1000),
            _event("a", 0),
            _event("c", 2000),
        ])
        frames = list(SessionReplayer(s).play())
        types = [f.event.event_type for f in frames]
        assert types == ["a", "b", "c"]

    def test_simultaneous_events(self):
        s = _session([
            _event("a", 0),
            _event("b", 0),
        ])
        frames = list(SessionReplayer(s).play())
        assert len(frames) == 2
        assert frames[1].wall_delay_ms == 0.0

    def test_replay_is_repeatable(self):
        r = SessionReplayer(_default_session())
        first = [f.to_dict() for f in r.play()]
        second = [f.to_dict() for f in r.play()]
        assert first == second
