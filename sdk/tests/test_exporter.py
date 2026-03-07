"""Tests for the SessionExporter module."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from agentlens.models import Session, AgentEvent, ToolCall, DecisionTrace
from agentlens.exporter import SessionExporter, _session_stats, _duration_human, _escape


# ── Fixtures ────────────────────────────────────────────────────────

def _make_session(n_events: int = 3, with_tools: bool = True, with_decisions: bool = True) -> Session:
    started = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
    s = Session(
        session_id="test-session-001",
        agent_name="test-agent",
        started_at=started,
        ended_at=started + timedelta(minutes=5),
        status="completed",
        metadata={"env": "test"},
    )
    for i in range(n_events):
        ev = AgentEvent(
            event_id=f"ev-{i:03d}",
            event_type="llm_call",
            model="gpt-4",
            tokens_in=100 * (i + 1),
            tokens_out=50 * (i + 1),
            duration_ms=200.0 * (i + 1),
            timestamp=started + timedelta(seconds=30 * i),
        )
        if with_tools and i == 1:
            ev.event_type = "tool_call"
            ev.tool_call = ToolCall(tool_name="web_search", tool_input={"q": "test"})
        if with_decisions and i == 2:
            ev.event_type = "decision"
            ev.decision_trace = DecisionTrace(reasoning="Chose path A over B", confidence=0.85)
        s.add_event(ev)
    return s


def _empty_session() -> Session:
    return Session(session_id="empty-001", agent_name="idle-agent")


# ── Unit tests ──────────────────────────────────────────────────────

class TestDurationHuman:
    def test_none(self):
        assert _duration_human(None) == "—"

    def test_milliseconds(self):
        assert _duration_human(450) == "450ms"

    def test_seconds(self):
        assert _duration_human(3500) == "3.5s"

    def test_minutes(self):
        assert _duration_human(90_000) == "1.5m"


class TestEscape:
    def test_basic(self):
        assert _escape("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"

    def test_ampersand(self):
        assert _escape("a & b") == "a &amp; b"

    def test_quotes(self):
        assert _escape('"hello"') == "&quot;hello&quot;"


class TestSessionStats:
    def test_basic_stats(self):
        s = _make_session()
        stats = _session_stats(s)
        assert stats["event_count"] == 3
        assert stats["total_tokens"] == stats["total_tokens_in"] + stats["total_tokens_out"]
        assert stats["tool_calls"] == 1
        assert "web_search" in stats["unique_tools"]
        assert stats["models_used"]["gpt-4"] == 3
        assert stats["error_count"] == 0
        assert stats["session_duration_ms"] == 300_000.0  # 5 min

    def test_empty_session(self):
        s = _empty_session()
        stats = _session_stats(s)
        assert stats["event_count"] == 0
        assert stats["tool_calls"] == 0
        assert stats["session_duration_ms"] is None


# ── JSON export ─────────────────────────────────────────────────────

class TestJsonExport:
    def test_valid_json(self):
        exp = SessionExporter(_make_session())
        raw = exp.as_json()
        data = json.loads(raw)
        assert data["session"]["session_id"] == "test-session-001"
        assert data["session"]["agent_name"] == "test-agent"
        assert len(data["events"]) == 3
        assert data["stats"]["event_count"] == 3

    def test_to_file(self):
        exp = SessionExporter(_make_session())
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            exp.to_json(path)
            data = json.loads(open(path, encoding="utf-8").read())
            assert data["session"]["status"] == "completed"
        finally:
            os.unlink(path)

    def test_empty_session_json(self):
        exp = SessionExporter(_empty_session())
        data = json.loads(exp.as_json())
        assert data["events"] == []
        assert data["stats"]["event_count"] == 0


# ── CSV export ──────────────────────────────────────────────────────

class TestCsvExport:
    def test_valid_csv(self):
        exp = SessionExporter(_make_session())
        raw = exp.as_csv()
        reader = csv.DictReader(io.StringIO(raw))
        rows = list(reader)
        assert len(rows) == 3
        assert rows[0]["event_type"] == "llm_call"
        assert rows[1]["tool_name"] == "web_search"
        assert rows[2]["reasoning"] == "Chose path A over B"

    def test_csv_columns(self):
        exp = SessionExporter(_make_session())
        raw = exp.as_csv()
        header = raw.split("\n")[0]
        assert "event_id" in header
        assert "tokens_in" in header
        assert "confidence" in header

    def test_to_file(self):
        exp = SessionExporter(_make_session())
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            exp.to_csv(path)
            content = open(path, encoding="utf-8").read()
            assert "web_search" in content
        finally:
            os.unlink(path)

    def test_empty_csv(self):
        exp = SessionExporter(_empty_session())
        raw = exp.as_csv()
        lines = raw.strip().split("\n")
        assert len(lines) == 1  # header only


# ── HTML export ─────────────────────────────────────────────────────

class TestHtmlExport:
    def test_contains_key_elements(self):
        exp = SessionExporter(_make_session())
        html = exp.as_html()
        assert "<!DOCTYPE html>" in html
        assert "test-agent" in html
        assert "test-session-001" in html
        assert "gpt-4" in html
        assert "web_search" in html
        assert "AgentLens SessionExporter" in html

    def test_self_contained(self):
        """HTML should not reference external CSS/JS."""
        exp = SessionExporter(_make_session())
        html = exp.as_html()
        assert "<link" not in html
        assert "<script src" not in html

    def test_event_badges(self):
        exp = SessionExporter(_make_session())
        html = exp.as_html()
        assert "badge-llm" in html
        assert "badge-tool" in html
        assert "badge-decision" in html

    def test_empty_session_html(self):
        exp = SessionExporter(_empty_session())
        html = exp.as_html()
        assert "No events recorded" in html

    def test_to_file(self):
        exp = SessionExporter(_make_session())
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            exp.to_html(path)
            content = open(path, encoding="utf-8").read()
            assert "<!DOCTYPE html>" in content
        finally:
            os.unlink(path)

    def test_xss_safe(self):
        """Agent names with HTML should be escaped."""
        s = _make_session()
        s.agent_name = '<script>alert("xss")</script>'
        exp = SessionExporter(s)
        html = exp.as_html()
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


# ── Round-trip ──────────────────────────────────────────────────────

class TestRoundTrip:
    def test_json_preserves_data(self):
        original = _make_session()
        exp = SessionExporter(original)
        data = json.loads(exp.as_json())
        assert data["session"]["session_id"] == original.session_id
        assert len(data["events"]) == len(original.events)
        for i, ev_dict in enumerate(data["events"]):
            assert ev_dict["event_id"] == original.events[i].event_id
