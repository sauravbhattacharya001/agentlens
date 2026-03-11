"""Tests for session_diff module."""

from datetime import datetime, timezone, timedelta

import pytest

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.session_diff import (
    AlignmentStatus,
    DiffReport,
    EventPair,
    SessionDiff,
    ToolCallDelta,
    _align_events,
    _model_counts,
    _session_duration_ms,
    _tool_counts,
)


def _ts(offset_s: int = 0) -> datetime:
    return datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _event(event_type: str = "llm_call", model: str | None = "gpt-4",
           tokens_in: int = 100, tokens_out: int = 50,
           duration_ms: float | None = 500.0,
           tool_name: str | None = None, offset: int = 0) -> AgentEvent:
    tc = ToolCall(tool_name=tool_name, tool_input={}) if tool_name else None
    return AgentEvent(
        event_type=event_type, model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        duration_ms=duration_ms, tool_call=tc,
        timestamp=_ts(offset),
    )


def _session(agent: str = "test-agent", events: list[AgentEvent] | None = None,
             ended: bool = True) -> Session:
    s = Session(agent_name=agent, started_at=_ts(0))
    for e in (events or []):
        s.add_event(e)
    if ended:
        s.ended_at = _ts(60)
        s.status = "completed"
    return s


# ── Helper functions ──

class TestToolCounts:
    def test_empty(self):
        assert _tool_counts([]) == {}

    def test_counts(self):
        events = [_event(tool_name="search"), _event(tool_name="search"), _event(tool_name="calc")]
        assert _tool_counts(events) == {"search": 2, "calc": 1}

    def test_no_tool_calls(self):
        assert _tool_counts([_event()]) == {}


class TestModelCounts:
    def test_counts(self):
        events = [_event(model="gpt-4"), _event(model="gpt-4"), _event(model="claude")]
        assert _model_counts(events) == {"gpt-4": 2, "claude": 1}

    def test_none_model(self):
        assert _model_counts([_event(model=None)]) == {}


class TestSessionDuration:
    def test_with_ended_at(self):
        s = _session()
        assert _session_duration_ms(s) == 60_000.0

    def test_fallback_sum(self):
        s = _session(ended=False, events=[_event(duration_ms=100), _event(duration_ms=200)])
        assert _session_duration_ms(s) == 300.0

    def test_no_duration(self):
        s = _session(ended=False, events=[_event(duration_ms=None)])
        assert _session_duration_ms(s) is None


# ── Alignment ──

class TestAlignEvents:
    def test_identical(self):
        evts = [_event("llm_call"), _event("tool_call", tool_name="search")]
        pairs = _align_events(evts, list(evts))
        assert len(pairs) == 2
        assert all(p.status in (AlignmentStatus.MATCHED, AlignmentStatus.MODIFIED) for p in pairs)

    def test_added(self):
        base = [_event("llm_call")]
        cand = [_event("llm_call"), _event("tool_call", tool_name="search")]
        pairs = _align_events(base, cand)
        statuses = [p.status for p in pairs]
        assert AlignmentStatus.ADDED in statuses

    def test_removed(self):
        base = [_event("llm_call"), _event("tool_call", tool_name="calc")]
        cand = [_event("llm_call")]
        pairs = _align_events(base, cand)
        statuses = [p.status for p in pairs]
        assert AlignmentStatus.REMOVED in statuses

    def test_modified_tokens(self):
        base = [_event("llm_call", tokens_in=100)]
        cand = [_event("llm_call", tokens_in=200)]
        pairs = _align_events(base, cand)
        assert pairs[0].status == AlignmentStatus.MODIFIED
        assert "tokens_in" in pairs[0].changes

    def test_empty(self):
        assert _align_events([], []) == []

    def test_all_removed(self):
        pairs = _align_events([_event("a"), _event("b")], [])
        assert all(p.status == AlignmentStatus.REMOVED for p in pairs)

    def test_all_added(self):
        pairs = _align_events([], [_event("a"), _event("b")])
        assert all(p.status == AlignmentStatus.ADDED for p in pairs)


# ── EventPair ──

class TestEventPair:
    def test_label_plain(self):
        p = EventPair(baseline=_event("llm_call"), candidate=None, status=AlignmentStatus.REMOVED)
        assert p.label == "llm_call"

    def test_label_tool(self):
        p = EventPair(baseline=_event(tool_name="search"), candidate=None, status=AlignmentStatus.REMOVED)
        assert "search" in p.label


# ── SessionDiff ──

class TestSessionDiff:
    def test_identical_sessions(self):
        evts = [_event("llm_call"), _event("tool_call", tool_name="search")]
        b = _session(events=evts)
        c = _session(events=list(evts))
        report = SessionDiff(b, c).compare()
        assert report.token_delta == 0
        assert report.similarity_score == 1.0
        assert report.baseline_event_count == 2
        assert report.candidate_event_count == 2

    def test_token_delta(self):
        b = _session(events=[_event(tokens_in=100, tokens_out=50)])
        c = _session(events=[_event(tokens_in=300, tokens_out=150)])
        report = SessionDiff(b, c).compare()
        assert report.tokens_in_delta == 200
        assert report.tokens_out_delta == 100
        assert report.token_delta == 300

    def test_tool_delta(self):
        b = _session(events=[_event(tool_name="calc"), _event(tool_name="search")])
        c = _session(events=[_event(tool_name="search"), _event(tool_name="browse")])
        report = SessionDiff(b, c).compare()
        assert "calc" in report.tool_delta.removed
        assert "browse" in report.tool_delta.added
        assert "search" in report.tool_delta.common

    def test_model_delta(self):
        b = _session(events=[_event(model="gpt-4")])
        c = _session(events=[_event(model="claude")])
        report = SessionDiff(b, c).compare()
        assert "gpt-4" in report.baseline_models
        assert "claude" in report.candidate_models

    def test_event_type_delta(self):
        b = _session(events=[_event("llm_call")])
        c = _session(events=[_event("llm_call"), _event("error")])
        report = SessionDiff(b, c).compare()
        assert "error" in report.added_event_types

    def test_duration_delta(self):
        b = _session()
        c = _session()
        c.ended_at = _ts(120)  # double duration
        report = SessionDiff(b, c).compare()
        assert report.duration_delta_ms == 60_000.0

    def test_similarity_partial(self):
        b = _session(events=[_event("a"), _event("b"), _event("c")])
        c = _session(events=[_event("a"), _event("d"), _event("c")])
        report = SessionDiff(b, c).compare()
        assert 0 < report.similarity_score < 1

    def test_empty_sessions(self):
        report = SessionDiff(_session(), _session()).compare()
        assert report.token_delta == 0
        assert report.similarity_score == 0.0  # 0/1 edge case


# ── Report rendering ──

class TestDiffReport:
    def _make_report(self) -> DiffReport:
        b = _session(events=[_event("llm_call"), _event(tool_name="search")])
        c = _session(events=[_event("llm_call", tokens_in=200), _event(tool_name="browse")])
        return SessionDiff(b, c).compare()

    def test_summary(self):
        r = self._make_report()
        s = r.summary()
        assert "tokens" in s
        assert "similarity" in s

    def test_render_text(self):
        r = self._make_report()
        text = r.render_text()
        assert "SESSION DIFF REPORT" in text
        assert "Tokens" in text
        assert "Tool Calls" in text

    def test_to_dict(self):
        r = self._make_report()
        d = r.to_dict()
        assert "baseline_id" in d
        assert "events" in d
        assert isinstance(d["events"], list)

    def test_to_json(self, tmp_path):
        r = self._make_report()
        path = str(tmp_path / "diff.json")
        r.to_json(path)
        import json
        with open(path) as f:
            data = json.load(f)
        assert data["baseline_id"] == r.baseline_id


# ── Edge cases ──

class TestEdgeCases:
    def test_one_empty_one_full(self):
        b = _session()
        c = _session(events=[_event("a"), _event("b"), _event("c")])
        report = SessionDiff(b, c).compare()
        assert report.candidate_event_count == 3
        assert report.similarity_score == 0.0

    def test_different_agents(self):
        b = _session(agent="agent-v1")
        c = _session(agent="agent-v2")
        report = SessionDiff(b, c).compare()
        assert report.baseline_agent == "agent-v1"
        assert report.candidate_agent == "agent-v2"

    def test_modified_duration(self):
        b_events = [_event(duration_ms=100)]
        c_events = [_event(duration_ms=500)]
        pairs = _align_events(b_events, c_events)
        assert pairs[0].status == AlignmentStatus.MODIFIED
        assert "duration_ms" in pairs[0].changes

    def test_model_change_detected(self):
        pairs = _align_events([_event(model="gpt-4")], [_event(model="claude")])
        assert pairs[0].status == AlignmentStatus.MODIFIED
        assert "model" in pairs[0].changes
