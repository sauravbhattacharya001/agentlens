"""Comprehensive tests for TimelineRenderer."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from agentlens.timeline import TimelineRenderer, _format_duration, _format_timestamp_offset


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _ts(offset_s: float = 0) -> str:
    """ISO timestamp with offset seconds from a base time."""
    base = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_s)).isoformat()


def _make_events() -> list[dict]:
    """Standard set of test events."""
    return [
        {"event_type": "session_start", "timestamp": _ts(0)},
        {
            "event_type": "llm_call",
            "timestamp": _ts(0.123),
            "model": "gpt-4o",
            "tokens_in": 150,
            "tokens_out": 89,
            "duration_ms": 2100,
        },
        {
            "event_type": "tool_call",
            "timestamp": _ts(2.234),
            "tool_call": {"tool_name": "web_search", "tool_output": {"result": "ok"}},
            "duration_ms": 1500,
        },
        {
            "event_type": "llm_call",
            "timestamp": _ts(3.734),
            "model": "gpt-4o",
            "tokens_in": 312,
            "tokens_out": 156,
            "duration_ms": 3200,
            "decision_trace": {"reasoning": "Chose to summarize"},
        },
        {
            "event_type": "error",
            "timestamp": _ts(6.934),
            "output_data": {"error": "Rate limit exceeded"},
        },
        {"event_type": "session_end", "timestamp": _ts(12.3)},
    ]


def _make_session() -> dict:
    return {
        "session_id": "abc-123",
        "agent_name": "my-agent",
        "started_at": _ts(0),
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_events(self):
        r = TimelineRenderer([], None)
        assert r.events == []
        assert r.session == {}

    def test_with_session(self):
        r = TimelineRenderer([], {"session_id": "s1"})
        assert r.session["session_id"] == "s1"

    def test_events_copied(self):
        orig = [{"event_type": "generic"}]
        r = TimelineRenderer(orig)
        r.events.append({"event_type": "other"})
        assert len(orig) == 1

    def test_offset_computation(self):
        events = _make_events()
        r = TimelineRenderer(events)
        assert r.events[0]["_offset_ms"] == 0.0
        assert r.events[1]["_offset_ms"] == pytest.approx(123, abs=1)

    def test_offset_no_timestamps(self):
        events = [{"event_type": "generic"}, {"event_type": "generic"}]
        r = TimelineRenderer(events)
        assert r.events[0]["_offset_ms"] == 0.0
        assert r.events[1]["_offset_ms"] == 0.0

    def test_single_event(self):
        r = TimelineRenderer([{"event_type": "llm_call", "timestamp": _ts(0)}])
        assert len(r.events) == 1


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


class TestGetSummary:
    def test_empty(self):
        s = TimelineRenderer([]).get_summary()
        assert s["total_events"] == 0
        assert s["total_tokens"] == 0
        assert s["error_count"] == 0
        assert s["models_used"] == []
        assert s["total_duration_ms"] == 0.0

    def test_full(self):
        r = TimelineRenderer(_make_events(), _make_session())
        s = r.get_summary()
        assert s["total_events"] == 6
        assert s["total_tokens"] == 150 + 89 + 312 + 156
        assert s["error_count"] == 1
        assert s["models_used"] == ["gpt-4o"]

    def test_duration_calculation(self):
        r = TimelineRenderer(_make_events(), _make_session())
        s = r.get_summary()
        # Last event at 12.3s offset, no duration → total ≈ 12300ms
        assert s["total_duration_ms"] == pytest.approx(12300, abs=5)

    def test_multiple_models(self):
        events = [
            {"event_type": "llm_call", "model": "gpt-4o", "timestamp": _ts(0)},
            {"event_type": "llm_call", "model": "claude-3", "timestamp": _ts(1)},
        ]
        s = TimelineRenderer(events).get_summary()
        assert sorted(s["models_used"]) == ["claude-3", "gpt-4o"]

    def test_token_counting(self):
        events = [
            {"event_type": "llm_call", "tokens_in": 100, "tokens_out": 50, "timestamp": _ts(0)},
            {"event_type": "llm_call", "tokens_in": 200, "tokens_out": 100, "timestamp": _ts(1)},
        ]
        s = TimelineRenderer(events).get_summary()
        assert s["total_tokens"] == 450

    def test_no_tokens(self):
        events = [{"event_type": "generic", "timestamp": _ts(0)}]
        s = TimelineRenderer(events).get_summary()
        assert s["total_tokens"] == 0

    def test_error_count_tool_error(self):
        events = [
            {
                "event_type": "tool_call",
                "timestamp": _ts(0),
                "tool_call": {"tool_name": "x", "tool_output": {"error": "fail"}},
            }
        ]
        s = TimelineRenderer(events).get_summary()
        assert s["error_count"] == 1


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFilter:
    def test_by_event_type(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(event_types=["llm_call"])
        assert len(filtered.events) == 2
        assert all(e["event_type"] == "llm_call" for e in filtered.events)

    def test_by_event_type_case_insensitive(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(event_types=["LLM_CALL"])
        assert len(filtered.events) == 2

    def test_by_min_duration(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(min_duration_ms=2000)
        assert all(e.get("duration_ms", 0) >= 2000 for e in filtered.events)

    def test_by_has_error_true(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(has_error=True)
        assert len(filtered.events) == 1
        assert filtered.events[0]["event_type"] == "error"

    def test_by_has_error_false(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(has_error=False)
        assert all(e["event_type"] != "error" for e in filtered.events)

    def test_by_model(self):
        events = [
            {"event_type": "llm_call", "model": "gpt-4o", "timestamp": _ts(0)},
            {"event_type": "llm_call", "model": "claude-3", "timestamp": _ts(1)},
        ]
        filtered = TimelineRenderer(events).filter(model="gpt-4o")
        assert len(filtered.events) == 1

    def test_by_model_case_insensitive(self):
        events = [{"event_type": "llm_call", "model": "GPT-4o", "timestamp": _ts(0)}]
        filtered = TimelineRenderer(events).filter(model="gpt-4o")
        assert len(filtered.events) == 1

    def test_chained_filter(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(event_types=["llm_call"]).filter(min_duration_ms=3000)
        assert len(filtered.events) == 1
        assert filtered.events[0]["duration_ms"] == 3200

    def test_filter_preserves_session(self):
        session = _make_session()
        r = TimelineRenderer(_make_events(), session)
        filtered = r.filter(event_types=["error"])
        assert filtered.session == session

    def test_filter_empty_result(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(event_types=["nonexistent"])
        assert len(filtered.events) == 0

    def test_multiple_event_types(self):
        r = TimelineRenderer(_make_events())
        filtered = r.filter(event_types=["llm_call", "tool_call"])
        assert len(filtered.events) == 3


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


class TestAnalysis:
    def test_get_error_events(self):
        r = TimelineRenderer(_make_events())
        errors = r.get_error_events()
        assert len(errors) == 1
        assert errors[0]["event_type"] == "error"

    def test_get_error_events_empty(self):
        events = [{"event_type": "llm_call", "timestamp": _ts(0)}]
        assert TimelineRenderer(events).get_error_events() == []

    def test_get_slowest_events(self):
        r = TimelineRenderer(_make_events())
        slowest = r.get_slowest_events(2)
        assert len(slowest) == 2
        assert slowest[0]["duration_ms"] >= slowest[1]["duration_ms"]

    def test_get_slowest_events_default_n(self):
        r = TimelineRenderer(_make_events())
        slowest = r.get_slowest_events()
        assert len(slowest) <= 5

    def test_get_slowest_events_ordering(self):
        events = [
            {"event_type": "llm_call", "duration_ms": 100, "timestamp": _ts(0)},
            {"event_type": "llm_call", "duration_ms": 500, "timestamp": _ts(1)},
            {"event_type": "llm_call", "duration_ms": 300, "timestamp": _ts(2)},
        ]
        slowest = TimelineRenderer(events).get_slowest_events(3)
        assert [e["duration_ms"] for e in slowest] == [500, 300, 100]

    def test_get_slowest_no_duration(self):
        events = [{"event_type": "generic", "timestamp": _ts(0)}]
        assert TimelineRenderer(events).get_slowest_events() == []

    def test_get_critical_path(self):
        r = TimelineRenderer(_make_events())
        path = r.get_critical_path()
        assert len(path) > 0
        # Should be sorted by offset
        offsets = [e.get("_offset_ms", 0) for e in path]
        assert offsets == sorted(offsets)

    def test_get_critical_path_empty(self):
        assert TimelineRenderer([]).get_critical_path() == []

    def test_get_critical_path_no_durations(self):
        events = [{"event_type": "generic", "timestamp": _ts(0)}]
        path = TimelineRenderer(events).get_critical_path()
        assert len(path) == 1


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------


class TestRenderText:
    def test_basic_structure(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "Session Timeline: abc-123" in text
        assert "Agent: my-agent" in text
        assert "═" in text

    def test_contains_events(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "SESSION_START" in text
        assert "LLM_CALL" in text
        assert "TOOL_CALL" in text
        assert "ERROR" in text
        assert "SESSION_END" in text

    def test_contains_summary(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "Summary" in text
        assert "Total:" in text

    def test_show_tokens(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(show_tokens=True)
        assert "tokens:" in text

    def test_hide_tokens(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(show_tokens=False)
        assert "tokens: 150" not in text

    def test_show_duration(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(show_duration=True)
        assert "──── 2.1s" in text

    def test_hide_duration(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(show_duration=False)
        assert "──── 2.1s" not in text

    def test_hide_metadata(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(show_metadata=False)
        assert "agent: my-agent" not in text

    def test_max_width_clamped(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text(max_width=10)
        # Should clamp to 40 minimum
        lines = text.split("\n")
        header_line = lines[0]
        assert len(header_line) == 40

    def test_empty_events(self):
        text = TimelineRenderer([], _make_session()).render_text()
        assert "Events: 0" in text

    def test_model_in_label(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "[gpt-4o]" in text

    def test_tool_name_in_label(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "[web_search]" in text

    def test_error_message(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "Rate limit exceeded" in text

    def test_has_reasoning_marker(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "has_reasoning" in text

    def test_timestamp_format(self):
        text = TimelineRenderer(_make_events(), _make_session()).render_text()
        assert "00:00.000" in text


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_basic_structure(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown()
        assert "# Session Timeline: abc-123" in md
        assert "## Events" in md
        assert "## Summary" in md

    def test_table_present(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown()
        assert "| Time |" in md
        assert "| --- |" in md

    def test_toc(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown(include_toc=True)
        assert "## Table of Contents" in md

    def test_no_toc(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown(include_toc=False)
        assert "Table of Contents" not in md

    def test_hide_tokens(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown(show_tokens=False)
        assert "Tokens" not in md.split("## Events")[1].split("## Summary")[0]

    def test_hide_duration(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown(show_duration=False)
        assert "Duration" not in md.split("## Events")[1].split("## Summary")[0]

    def test_hide_metadata(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown(show_metadata=False)
        assert "**Agent:**" not in md

    def test_summary_section(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown()
        assert "**Total events:**" in md
        assert "**Errors:**" in md

    def test_models_in_summary(self):
        md = TimelineRenderer(_make_events(), _make_session()).render_markdown()
        assert "gpt-4o" in md

    def test_empty_events(self):
        md = TimelineRenderer([], _make_session()).render_markdown()
        assert "**Total events:** 0" in md


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


class TestRenderHTML:
    def test_basic_structure(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        assert "<!DOCTYPE html>" in html
        assert "<html>" in html
        assert "</html>" in html

    def test_contains_style(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        assert "<style>" in html

    def test_event_cards(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        assert "event-card" in html

    def test_title(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(title="My Timeline")
        assert "<title>My Timeline</title>" in html
        assert "My Timeline" in html

    def test_dark_mode(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(dark_mode=True)
        assert "#1a1a2e" in html  # dark bg

    def test_light_mode(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(dark_mode=False)
        assert "#ffffff" in html

    def test_token_badges(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(show_tokens=True)
        assert "token-badge" in html

    def test_hide_tokens(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(show_tokens=False)
        # token-badge class in CSS is fine, but no span with it should be rendered
        body = html.split("</style>")[1]
        assert "token-badge" not in body

    def test_duration_bars(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(show_duration=True)
        assert "duration-bar" in html

    def test_hide_duration(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(show_duration=False)
        # duration-bar class should not appear as inline element (only in CSS)
        # Check that no duration-bar span is rendered
        assert "duration-bar'" not in html.split("</style>")[1]

    def test_error_details(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        assert "Rate limit exceeded" in html
        assert "error-details" in html

    def test_summary_section(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        assert "Summary" in html

    def test_hide_metadata(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html(show_metadata=False)
        body = html.split("</style>")[1]
        assert "Agent:" not in body.split("</div>")[0]  # Not in header meta

    def test_self_contained(self):
        html = TimelineRenderer(_make_events(), _make_session()).render_html()
        # No external resources
        assert "http" not in html.split("<style>")[0].split("<head>")[-1]
        assert "<link" not in html
        assert "<script src=" not in html

    def test_empty_events(self):
        html = TimelineRenderer([], _make_session()).render_html()
        assert "<!DOCTYPE html>" in html


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_text(self, tmp_path):
        path = str(tmp_path / "out.txt")
        TimelineRenderer(_make_events(), _make_session()).save(path)
        content = open(path, encoding="utf-8").read()
        assert "Session Timeline" in content

    def test_save_md(self, tmp_path):
        path = str(tmp_path / "out.md")
        TimelineRenderer(_make_events(), _make_session()).save(path)
        content = open(path, encoding="utf-8").read()
        assert "# Session Timeline" in content

    def test_save_html(self, tmp_path):
        path = str(tmp_path / "out.html")
        TimelineRenderer(_make_events(), _make_session()).save(path)
        content = open(path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in content

    def test_save_htm(self, tmp_path):
        path = str(tmp_path / "out.htm")
        TimelineRenderer(_make_events(), _make_session()).save(path)
        content = open(path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in content

    def test_save_explicit_format(self, tmp_path):
        path = str(tmp_path / "out.xyz")
        TimelineRenderer(_make_events(), _make_session()).save(path, format="html")
        content = open(path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in content

    def test_save_auto_unknown_ext(self, tmp_path):
        path = str(tmp_path / "out.xyz")
        TimelineRenderer(_make_events(), _make_session()).save(path)
        content = open(path, encoding="utf-8").read()
        # Falls back to text
        assert "Session Timeline" in content

    def test_save_creates_file(self, tmp_path):
        path = str(tmp_path / "new_file.txt")
        assert not os.path.exists(path)
        TimelineRenderer([], _make_session()).save(path)
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_duration_ms(self):
        assert _format_duration(500) == "500ms"

    def test_format_duration_seconds(self):
        assert _format_duration(2100) == "2.1s"

    def test_format_duration_none(self):
        assert _format_duration(None) == ""

    def test_format_timestamp_offset(self):
        assert _format_timestamp_offset(0) == "00:00.000"
        assert _format_timestamp_offset(123) == "00:00.123"
        assert _format_timestamp_offset(61000) == "01:01.000"

    def test_is_error_event_type(self):
        assert TimelineRenderer._is_error({"event_type": "error"}) is True

    def test_is_error_tool_output(self):
        e = {
            "event_type": "tool_call",
            "tool_call": {"tool_name": "x", "tool_output": {"error": "fail"}},
        }
        assert TimelineRenderer._is_error(e) is True

    def test_is_not_error(self):
        assert TimelineRenderer._is_error({"event_type": "llm_call"}) is False


# ---------------------------------------------------------------------------
# Integration with AgentTracker
# ---------------------------------------------------------------------------


class TestTrackerIntegration:
    def test_timeline_method_exists(self):
        from agentlens.tracker import AgentTracker
        assert hasattr(AgentTracker, "timeline")

    def test_timeline_import(self):
        from agentlens import TimelineRenderer
        assert TimelineRenderer is not None

    def test_timeline_no_session_raises(self):
        from agentlens.tracker import AgentTracker
        from unittest.mock import MagicMock
        tracker = AgentTracker(transport=MagicMock())
        with pytest.raises(RuntimeError, match="Session not found"):
            tracker.timeline()

    def test_timeline_with_session(self):
        from agentlens.tracker import AgentTracker
        from unittest.mock import MagicMock
        tracker = AgentTracker(transport=MagicMock())
        # Mock transport send_events
        tracker.transport.send_events = MagicMock()
        session = tracker.start_session(agent_name="test-agent")
        tracker.track(event_type="llm_call", model="gpt-4o", tokens_in=100, tokens_out=50, duration_ms=1000)
        renderer = tracker.timeline()
        assert isinstance(renderer, TimelineRenderer)
        assert len(renderer.events) == 1
        assert renderer.session["agent_name"] == "test-agent"

    def test_timeline_with_filter_kwargs(self):
        from agentlens.tracker import AgentTracker
        from unittest.mock import MagicMock
        tracker = AgentTracker(transport=MagicMock())
        tracker.transport.send_events = MagicMock()
        tracker.start_session()
        tracker.track(event_type="llm_call", model="gpt-4o", duration_ms=100)
        tracker.track(event_type="tool_call", tool_name="search", duration_ms=200)
        renderer = tracker.timeline(event_types=["llm_call"])
        assert len(renderer.events) == 1
