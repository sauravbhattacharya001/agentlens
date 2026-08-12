"""Markdown-table cell escaping in the narrative renderers.

Tool names, agent names, and session ids flow in from arbitrary
agent/user-authored data.  A raw ``|`` or embedded newline in any of those
would silently corrupt the Markdown tables emitted by ``Narrative.to_markdown``
(Tool Usage table) and ``NarrativeGenerator.generate_comparison`` (Session
Comparison + Tool Usage Comparison tables).  These tests pin that such content
stays inside its own cell via the shared ``_utils.md_cell`` helper.
"""

from datetime import datetime, timezone

from agentlens._utils import md_cell
from agentlens.narrative import NarrativeGenerator
from agentlens.narrative_types import Narrative, ToolSummary
from agentlens.models import AgentEvent, Session, ToolCall


def _table_rows(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if ln.strip().startswith("|")]


def _fences(row: str) -> int:
    """Count column-delimiting pipes, ignoring escaped ``\\|`` content."""
    return row.replace("\\|", "").count("|")


def test_md_cell_escapes_pipe_and_folds_newlines():
    assert md_cell("a|b") == "a\\|b"
    assert md_cell("a\nb\r\nc") == "a b  c"
    assert md_cell("a\\b") == "a\\\\b"
    # content-preserving for well-behaved text
    assert md_cell("search") == "search"


def test_tool_usage_table_escapes_pipe_in_tool_name():
    n = Narrative(
        session_id="s1",
        agent_name="agent",
        summary="s",
        body="b",
        tool_summaries=[
            ToolSummary(
                tool_name="weird|tool\nname",
                call_count=3,
                success_count=2,
                failure_count=1,
                total_duration_ms=30.0,
                avg_duration_ms=10.0,
            )
        ],
        generated_at=datetime(2026, 3, 16, tzinfo=timezone.utc),
    )
    md = n.to_markdown()
    assert "weird\\|tool name" in md
    # the tool row keeps exactly the 5 columns it declares (6 fences)
    row = next(ln for ln in _table_rows(md) if "weird" in ln)
    assert _fences(row) == 6


def _sess(sid: str, agent: str) -> Session:
    s = Session(
        session_id=sid,
        agent_name=agent,
        started_at=datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 3, 16, 12, 1, 0, tzinfo=timezone.utc),
        status="completed",
    )
    s.add_event(
        AgentEvent(
            event_type="tool_call",
            tool_call=ToolCall(tool_name="a|b", duration_ms=10.0),
            output_data={"result": "ok"},
            timestamp=datetime(2026, 3, 16, 12, 0, 1, tzinfo=timezone.utc),
        )
    )
    return s


def test_comparison_table_escapes_pipe_in_agent_and_tool_names():
    gen = NarrativeGenerator()
    md = gen.compare(_sess("id|1", "agent|A"), _sess("id|2", "agent|B"))
    # agent + id + tool cells all escaped
    assert "agent\\|A" in md and "agent\\|B" in md
    assert "id\\|1" in md and "id\\|2" in md
    assert "a\\|b" in md
    # every table row keeps its intended fence count (no early column break)
    for row in _table_rows(md):
        # comparison metric rows have 3 columns (4 fences); tool rows have 5 (6)
        assert _fences(row) in (4, 6)
