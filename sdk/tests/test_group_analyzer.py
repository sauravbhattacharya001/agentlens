"""Tests for SessionGroupAnalyzer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.models import AgentEvent, Session
from agentlens.group_analyzer import (
    ComparisonReport,
    GroupStats,
    SessionGroupAnalyzer,
)


def _utc(year=2026, month=1, day=1, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _make_session(
    agent="agent-a",
    status="completed",
    tokens_in=100,
    tokens_out=50,
    started=None,
    ended=None,
    model=None,
    metadata=None,
    n_events=3,
):
    s = Session(
        agent_name=agent,
        status=status,
        started_at=started or _utc(),
        ended_at=ended,
        total_tokens_in=tokens_in,
        total_tokens_out=tokens_out,
        metadata=metadata or {},
    )
    for i in range(n_events):
        e = AgentEvent(event_type="llm_call", model=model or "gpt-4", tokens_in=10, tokens_out=5)
        s.events.append(e)
    return s


# ---- GroupStats -----------------------------------------------------------

class TestGroupStats:
    def test_empty(self):
        gs = GroupStats("empty", [])
        assert gs.count == 0
        assert gs.avg_tokens_in == 0.0
        assert gs.completion_rate == 0.0

    def test_basic_stats(self):
        sessions = [
            _make_session(tokens_in=100, tokens_out=50, started=_utc(hour=0), ended=_utc(hour=1)),
            _make_session(tokens_in=200, tokens_out=80, started=_utc(hour=2), ended=_utc(hour=3)),
        ]
        gs = GroupStats("test", sessions)
        assert gs.count == 2
        assert gs.total_tokens_in == 300
        assert gs.total_tokens_out == 130
        assert gs.avg_tokens_in == 150.0
        assert gs.avg_events == 3.0
        assert len(gs.durations) == 2

    def test_duration_percentiles(self):
        sessions = []
        for i in range(20):
            sessions.append(_make_session(
                started=_utc(hour=0),
                ended=_utc(hour=0, minute=i + 1),
            ))
        gs = GroupStats("dur", sessions)
        assert gs.min_duration_ms == 60_000
        assert gs.max_duration_ms == 20 * 60_000
        assert gs.p95_duration_ms >= gs.median_duration_ms

    def test_status_counts(self):
        sessions = [
            _make_session(status="completed"),
            _make_session(status="completed"),
            _make_session(status="error"),
            _make_session(status="active"),
        ]
        gs = GroupStats("mix", sessions)
        assert gs.completed_count == 2
        assert gs.error_count == 1
        assert gs.active_count == 1
        assert gs.completion_rate == 0.5
        assert gs.error_rate == 0.25

    def test_models_used(self):
        s1 = _make_session(model="gpt-4")
        s2 = _make_session(model="claude-3")
        gs = GroupStats("m", [s1, s2])
        assert "gpt-4" in gs.models_used
        assert "claude-3" in gs.models_used

    def test_to_dict(self):
        gs = GroupStats("x", [_make_session()])
        d = gs.to_dict()
        assert d["name"] == "x"
        assert "count" in d
        assert "models_used" in d

    def test_no_ended_sessions(self):
        s = _make_session(status="active")
        s.ended_at = None
        gs = GroupStats("no-dur", [s])
        assert gs.durations == []
        assert gs.avg_duration_ms == 0.0


# ---- SessionGroupAnalyzer ------------------------------------------------

class TestGroupByAgent:
    def test_single_agent(self):
        a = SessionGroupAnalyzer([_make_session(agent="bot")])
        groups = a.group_by_agent()
        assert "bot" in groups
        assert len(groups["bot"]) == 1

    def test_multiple_agents(self):
        a = SessionGroupAnalyzer([
            _make_session(agent="a"),
            _make_session(agent="b"),
            _make_session(agent="a"),
        ])
        groups = a.group_by_agent()
        assert len(groups["a"]) == 2
        assert len(groups["b"]) == 1


class TestGroupByStatus:
    def test_groups(self):
        a = SessionGroupAnalyzer([
            _make_session(status="completed"),
            _make_session(status="error"),
        ])
        groups = a.group_by_status()
        assert "completed" in groups
        assert "error" in groups


class TestGroupByModel:
    def test_primary_model(self):
        a = SessionGroupAnalyzer([
            _make_session(model="gpt-4"),
            _make_session(model="claude-3"),
        ])
        groups = a.group_by_model()
        assert "gpt-4" in groups
        assert "claude-3" in groups

    def test_no_model(self):
        s = _make_session(n_events=0)
        a = SessionGroupAnalyzer([s])
        groups = a.group_by_model()
        assert "(no model)" in groups


class TestGroupByMetadata:
    def test_metadata_key(self):
        a = SessionGroupAnalyzer([
            _make_session(metadata={"env": "prod"}),
            _make_session(metadata={"env": "staging"}),
            _make_session(metadata={}),
        ])
        groups = a.group_by_metadata("env")
        assert "prod" in groups
        assert "staging" in groups
        assert "(missing)" in groups


class TestGroupByTimeWindow:
    def test_hourly(self):
        sessions = [
            _make_session(started=_utc(hour=0)),
            _make_session(started=_utc(hour=0, minute=30)),
            _make_session(started=_utc(hour=1, minute=15)),
        ]
        a = SessionGroupAnalyzer(sessions)
        groups = a.group_by_time_window(timedelta(hours=1))
        assert len(groups) == 2

    def test_empty(self):
        a = SessionGroupAnalyzer([])
        assert a.group_by_time_window(timedelta(hours=1)) == {}

    def test_custom_origin(self):
        s = _make_session(started=_utc(hour=5))
        a = SessionGroupAnalyzer([s])
        groups = a.group_by_time_window(timedelta(hours=2), origin=_utc(hour=4))
        assert len(groups) == 1


class TestGroupByCustom:
    def test_custom_fn(self):
        a = SessionGroupAnalyzer([
            _make_session(tokens_in=10),
            _make_session(tokens_in=500),
        ])
        groups = a.group_by_custom(lambda s: "heavy" if s.total_tokens_in > 100 else "light")
        assert "heavy" in groups
        assert "light" in groups


# ---- Compare & Report ----------------------------------------------------

class TestCompare:
    def test_compare(self):
        a = SessionGroupAnalyzer([
            _make_session(agent="fast", started=_utc(hour=0), ended=_utc(hour=0, minute=1)),
            _make_session(agent="slow", started=_utc(hour=0), ended=_utc(hour=2)),
        ])
        report = a.compare(a.group_by_agent())
        assert report.fastest_median == "fast"
        assert isinstance(report.groups, dict)

    def test_compare_empty(self):
        a = SessionGroupAnalyzer([])
        report = a.compare({})
        assert report.best_completion_rate is None

    def test_highlights(self):
        sessions = [
            _make_session(agent="good", status="completed", tokens_in=10, tokens_out=5, started=_utc(hour=0), ended=_utc(hour=0, minute=1), n_events=5),
            _make_session(agent="bad", status="error", tokens_in=1000, tokens_out=500, started=_utc(hour=0), ended=_utc(hour=3), n_events=2),
        ]
        a = SessionGroupAnalyzer(sessions)
        report = a.compare(a.group_by_agent())
        assert report.best_completion_rate == "good"
        assert report.lowest_error_rate == "good"
        assert report.most_efficient == "good"

    def test_to_dict(self):
        a = SessionGroupAnalyzer([_make_session()])
        report = a.compare(a.group_by_agent())
        d = report.to_dict()
        assert "groups" in d
        assert "highlights" in d


class TestTextReport:
    def test_text_output(self):
        a = SessionGroupAnalyzer([
            _make_session(agent="a", started=_utc(hour=0), ended=_utc(hour=1)),
            _make_session(agent="b", started=_utc(hour=0), ended=_utc(hour=2)),
        ])
        report = a.compare(a.group_by_agent())
        text = a.text_report(report)
        assert "SESSION GROUP COMPARISON REPORT" in text
        assert "Group: a" in text
        assert "HIGHLIGHTS" in text


class TestJsonExport:
    def test_json_valid(self):
        a = SessionGroupAnalyzer([_make_session()])
        report = a.compare(a.group_by_agent())
        j = a.json_export(report)
        parsed = json.loads(j)
        assert "groups" in parsed


# ---- Add sessions ---------------------------------------------------------

class TestAddSessions:
    def test_add_session(self):
        a = SessionGroupAnalyzer()
        a.add_session(_make_session())
        assert len(a.sessions) == 1

    def test_add_sessions(self):
        a = SessionGroupAnalyzer()
        a.add_sessions([_make_session(), _make_session()])
        assert len(a.sessions) == 2

    def test_sessions_property_copy(self):
        a = SessionGroupAnalyzer([_make_session()])
        s = a.sessions
        s.clear()
        assert len(a.sessions) == 1  # original unmodified
