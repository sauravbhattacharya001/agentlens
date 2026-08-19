"""Tests for the SessionExporter module."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


from agentlens.models import Session, AgentEvent, ToolCall, DecisionTrace
from agentlens.exporter import (
    SessionExporter,
    _session_stats,
    _duration_human,
    _escape,
    _validate_output_path,
)
from agentlens.exporter_format import _finite_ms


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

    def test_single_quotes(self):
        assert _escape("it's") == "it&#39;s"

    def test_all_five_chars(self):
        assert _escape("""<>&"'""") == "&lt;&gt;&amp;&quot;&#39;"

    def test_xss_payload(self):
        payload = '<script>alert("xss")</script>'
        escaped = _escape(payload)
        assert "<script>" not in escaped
        assert "alert" in escaped  # content preserved, tags neutralised


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
        # ``Session.started_at`` has a default factory but ``ended_at`` defaults
        # to None, so this exercises the FIRST-operand-falsy arm of the
        # ``session.ended_at and session.started_at`` guard (ended_at is None).
        assert stats["session_duration_ms"] is None

    def test_started_at_none_ended_at_set(self):
        # The complementary asymmetry: a reconstructed/imported Session can
        # carry ``ended_at`` while ``started_at`` was cleared to None. This
        # exercises the SECOND-operand-falsy arm of the ``and`` guard, which
        # ``test_empty_session`` (ended_at None) never reaches. The duration
        # must stay None rather than attempting ``ended_at - None``.
        s = _empty_session()
        s.started_at = None
        s.ended_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        stats = _session_stats(s)
        assert stats["session_duration_ms"] is None

    def test_zero_duration_session_reports_zero_not_none(self):
        # Regression: a session whose ended_at == started_at has a KNOWN
        # duration of 0 ms. A naive truthiness guard (`x if x else None`)
        # would collapse that real zero into None ("unknown"), conflating a
        # bounded instantaneous session with one whose timestamps are missing.
        instant = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        s = Session(
            session_id="instant-001",
            agent_name="instant-agent",
            started_at=instant,
            ended_at=instant,
        )
        stats = _session_stats(s)
        assert stats["session_duration_ms"] == 0.0
        assert stats["session_duration_ms"] is not None

    def test_events_without_model_or_duration(self):
        # Exercises the false side of `if ev.model` and `if ev.duration_ms`:
        # a bare event contributes to the count but not to models_used or
        # total_event_duration_ms.
        s = Session(session_id="bare-001", agent_name="bare-agent")
        s.add_event(AgentEvent(event_id="e0", event_type="llm_call"))
        stats = _session_stats(s)
        assert stats["event_count"] == 1
        assert stats["models_used"] == {}
        assert stats["total_event_duration_ms"] == 0.0
        assert stats["error_count"] == 0

    def test_error_event_is_counted(self):
        # Exercises the `event_type == "error"` branch (error_count increment).
        s = Session(session_id="err-001", agent_name="err-agent")
        s.add_event(AgentEvent(event_id="e0", event_type="error"))
        s.add_event(AgentEvent(event_id="e1", event_type="llm_call"))
        stats = _session_stats(s)
        assert stats["error_count"] == 1
        assert stats["event_types"]["error"] == 1

    def test_non_finite_event_duration_does_not_poison_total(self):
        # Regression: bad instrumentation can hand an event a non-finite
        # duration_ms (inf/nan). A naive `total += ev.duration_ms` would make
        # total_event_duration_ms itself inf/nan, which then serialises via the
        # report/json.dumps as the bare tokens Infinity/NaN (invalid JSON, a
        # meaningless duration). The _finite_ms guard coerces such values to 0
        # so the reported total stays finite and additive.
        s = Session(session_id="badur-001", agent_name="badur-agent")
        s.add_event(AgentEvent(event_id="e0", event_type="llm_call", duration_ms=100.0))
        s.add_event(AgentEvent(event_id="e1", event_type="llm_call", duration_ms=float("inf")))
        s.add_event(AgentEvent(event_id="e2", event_type="llm_call", duration_ms=float("nan")))
        s.add_event(AgentEvent(event_id="e3", event_type="llm_call", duration_ms=-50.0))
        stats = _session_stats(s)
        # Only the single valid 100ms event contributes; inf/nan/negative -> 0.
        assert stats["total_event_duration_ms"] == 100.0
        assert math.isfinite(stats["total_event_duration_ms"])


class TestFiniteMs:
    def test_valid_positive_passes_through(self):
        assert _finite_ms(250.0) == 250.0

    def test_non_finite_coerced_to_zero(self):
        assert _finite_ms(float("inf")) == 0.0
        assert _finite_ms(float("-inf")) == 0.0
        assert _finite_ms(float("nan")) == 0.0

    def test_negative_and_zero_coerced_to_zero(self):
        assert _finite_ms(-5.0) == 0.0
        assert _finite_ms(0.0) == 0.0

    def test_non_numeric_coerced_to_zero(self):
        assert _finite_ms(None) == 0.0
        assert _finite_ms("not-a-number") == 0.0


class TestValidateOutputPath:
    def test_accepts_file_in_cwd(self):
        resolved = _validate_output_path("report.json")
        assert resolved == (Path.cwd() / "report.json").resolve()

    def test_accepts_file_in_temp(self):
        target = Path(tempfile.gettempdir()) / "al-export.json"
        resolved = _validate_output_path(str(target))
        assert resolved == target.resolve()

    def test_rejects_bare_cwd_directory(self):
        # Resolving to the cwd itself is a directory, not a file (line 58).
        with pytest.raises(ValueError, match="not a directory"):
            _validate_output_path(str(Path.cwd()))

    def test_rejects_bare_temp_directory(self):
        with pytest.raises(ValueError, match="not a directory"):
            _validate_output_path(tempfile.gettempdir())

    def test_rejects_path_outside_allowed_dirs(self):
        # A traversal that escapes both cwd and temp is rejected (line 69).
        escaped = Path.cwd().resolve()
        while escaped.parent != escaped:
            escaped = escaped.parent  # filesystem root
        target = escaped / "agentlens-not-allowed-xyz.json"
        with pytest.raises(ValueError, match="must be within"):
            _validate_output_path(str(target))


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

    def test_non_finite_duration_ms_coerced(self):
        """A non-finite ``duration_ms`` must not leak ``inf``/``nan`` into the CSV.

        Bad instrumentation can hand an event an ``inf``/``nan`` duration; without
        coercion ``csv`` would write the bare token ``inf``/``nan`` into the cell,
        producing an un-parseable / meaningless duration column.  It should be
        normalised to ``0`` (mirroring the ``_finite_ms`` guard used for the
        aggregate ``total_event_duration_ms``).
        """
        s = _empty_session()
        base = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        for i, bad in enumerate((float("inf"), float("-inf"), float("nan"), -5.0)):
            s.add_event(AgentEvent(
                event_id=f"bad-{i}",
                event_type="llm_call",
                duration_ms=bad,
                timestamp=base + timedelta(seconds=i),
            ))
        raw = SessionExporter(s).as_csv()
        assert "inf" not in raw.lower()
        assert "nan" not in raw.lower()
        rows = list(csv.DictReader(io.StringIO(raw)))
        assert len(rows) == 4
        for row in rows:
            assert row["duration_ms"] == "0.0"

    def test_none_duration_ms_stays_empty(self):
        """A ``None`` duration still renders as an empty cell (unchanged behavior)."""
        s = _empty_session()
        s.add_event(AgentEvent(
            event_id="no-dur",
            event_type="llm_call",
            duration_ms=None,
            timestamp=datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc),
        ))
        rows = list(csv.DictReader(io.StringIO(SessionExporter(s).as_csv())))
        assert rows[0]["duration_ms"] == ""


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

    def test_xss_safe_session_id(self):
        """Session IDs with HTML payloads must be escaped."""
        s = _make_session()
        s.session_id = '"><img src=x onerror=alert(1)>'
        exp = SessionExporter(s)
        html = exp.as_html()
        assert "<img src" not in html
        assert "onerror" not in html or "&lt;img" in html

    def test_xss_safe_status(self):
        """Status field with HTML payloads must be escaped."""
        s = _make_session()
        s.status = '<script>document.cookie</script>'
        exp = SessionExporter(s)
        html = exp.as_html()
        assert "<script>document" not in html
        assert "&lt;script&gt;" in html

    # ── Per-row escaping (events / models / tools tables) ───────────
    # The whole-session fields (agent_name / session_id / status) are
    # covered above; these guard the PER-ROW cells that render
    # agent-controlled content and were previously untested.

    def test_xss_safe_event_model(self):
        """An event's model name is escaped in the events table row."""
        s = _make_session()
        s.events[0].model = '<img src=x onerror=alert(1)>'
        html = SessionExporter(s).as_html()
        assert "<img src=x onerror" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_xss_safe_tool_name(self):
        """A tool_call's tool_name is escaped in the events table row."""
        s = _make_session()
        ev = s.events[1]
        ev.event_type = "tool_call"
        ev.tool_call = ToolCall(tool_name='<script>alert("t")</script>', tool_input={})
        html = SessionExporter(s).as_html()
        assert '<script>alert("t")' not in html
        assert "&lt;script&gt;alert(" in html

    def test_xss_safe_decision_reasoning(self):
        """A decision_trace's reasoning is escaped in the events table row."""
        s = _make_session()
        ev = s.events[2]
        ev.event_type = "decision"
        ev.decision_trace = DecisionTrace(reasoning='<b>x</b>&"quote"', confidence=0.5)
        html = SessionExporter(s).as_html()
        assert "<b>x</b>" not in html
        assert "&lt;b&gt;x&lt;/b&gt;" in html
        assert "&amp;" in html and "&quot;" in html

    def test_xss_safe_event_type(self):
        """An event_type is escaped in the badge cell (defensive)."""
        s = _make_session()
        s.events[0].event_type = '<i>evt</i>'
        html = SessionExporter(s).as_html()
        assert "<i>evt</i>" not in html
        assert "&lt;i&gt;evt&lt;/i&gt;" in html

    def test_xss_safe_model_table(self):
        """Model names are escaped in the Models summary table."""
        s = _make_session()
        s.events[0].model = '<u>m</u>'
        html = SessionExporter(s).as_html()
        # Models summary table renders each distinct model name.
        assert "<u>m</u>" not in html
        assert "&lt;u&gt;m&lt;/u&gt;" in html

    def test_xss_safe_tools_list(self):
        """Tool names are escaped in the Tools Used pill list."""
        s = _make_session()
        ev = s.events[1]
        ev.event_type = "tool_call"
        ev.tool_call = ToolCall(tool_name='<span onclick=x>t</span>', tool_input={})
        html = SessionExporter(s).as_html()
        assert "<span onclick=x>" not in html
        assert "&lt;span onclick=x&gt;t&lt;/span&gt;" in html


# ── Events-table detail cell (truncation + precedence) ──────────────
# The per-row ``detail`` cell in ``_render_events_table`` picks its content
# with a precedence chain (decision reasoning > tool name > model) and
# truncates long decision reasoning to 60 chars + "…". The XSS tests above
# use short reasoning, so only the *non*-truncated arm was exercised; these
# pin the truncation boundary and the precedence order directly.

class TestEventsTableDetail:
    def _decision_session(self, reasoning: str) -> Session:
        s = _empty_session()
        s.add_event(AgentEvent(
            event_type="decision",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            decision_trace=DecisionTrace(reasoning=reasoning),
        ))
        return s

    def test_long_reasoning_truncated_with_ellipsis(self):
        """Reasoning over 60 chars is sliced to 60 and gets a trailing '…'."""
        reasoning = "R" * 100
        html = SessionExporter(self._decision_session(reasoning)).as_html()
        assert ("R" * 60 + "\u2026") in html
        # The full (untruncated) string must NOT appear anywhere.
        assert ("R" * 61) not in html

    def test_reasoning_exactly_60_not_truncated(self):
        """Boundary: len == 60 uses the ``else r`` arm (no ellipsis added)."""
        reasoning = "B" * 60
        html = SessionExporter(self._decision_session(reasoning)).as_html()
        assert ("B" * 60) in html
        assert ("B" * 60 + "\u2026") not in html

    def test_detail_precedence_decision_over_tool_and_model(self):
        """A decision_trace wins the detail cell over tool_call/model."""
        s = _empty_session()
        s.add_event(AgentEvent(
            event_type="decision",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model="model-should-lose",
            tool_call=ToolCall(tool_name="tool-should-lose", tool_input={}),
            decision_trace=DecisionTrace(reasoning="decision-wins"),
        ))
        html = SessionExporter(s).as_html()
        assert "decision-wins" in html

    def test_detail_precedence_tool_over_model(self):
        """With no decision_trace, tool_call name wins over the model detail."""
        s = _empty_session()
        s.add_event(AgentEvent(
            event_type="tool_call",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            model="model-should-lose",
            tool_call=ToolCall(tool_name="tool-wins", tool_input={}),
        ))
        html = SessionExporter(s).as_html()
        assert "tool-wins" in html


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
