"""Tests for the transcript exporter (AgentLens -> agent-eval bridge).

These assert the exported markdown conforms structurally to
``transcript-contract@v1`` and that each section is derived from captured
session/event data rather than self-reported prose.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from agentlens.models import AgentEvent, Session, ToolCall, DecisionTrace
from agentlens.transcript import (
    TranscriptExporter,
    export_transcript,
    TRANSCRIPT_CONTRACT_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)

# The required `##` sections, in contract order.
REQUIRED_SECTIONS = [
    "## Task",
    "## Actions Taken",
    "## Key Outputs",
    "## Outcome",
    "## Errors & Retries",
    "## Duration",
]


def _make_session(status: str = "completed", *, with_error: bool = False) -> Session:
    session = Session(
        agent_name="builder",
        started_at=BASE,
        metadata={"task": "Add a small feature to the everything repo"},
    )
    session.add_event(
        AgentEvent(
            event_type="tool_call",
            tool_call=ToolCall(
                tool_name="git_clone",
                tool_input={"repo": "everything"},
                tool_output={"path": "/tmp/everything"},
            ),
        )
    )
    session.add_event(
        AgentEvent(
            event_type="tool_call",
            tool_call=ToolCall(
                tool_name="run_tests",
                tool_input={"cmd": "npm test"},
                tool_output={"passed": 42, "failed": 0},
            ),
        )
    )
    if with_error:
        session.add_event(
            AgentEvent(
                event_type="error",
                output_data={"message": "transient network error, retried"},
            )
        )
    session.add_event(
        AgentEvent(
            event_type="llm_call",
            output_data={"summary": "feature implemented and pushed (commit a1b2c3d)"},
        )
    )
    session.ended_at = BASE + timedelta(minutes=14)
    session.status = status
    return session


def _section(md: str, heading: str) -> str:
    """Extract the body text under a `##` heading."""
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Structural compliance
# ---------------------------------------------------------------------------

def test_has_title_and_all_required_sections_in_order():
    md = export_transcript(_make_session())
    # Title is a level-1 heading.
    assert md.lstrip().startswith("# builder Run - ")
    # All required sections present, in order.
    positions = [md.find(h) for h in REQUIRED_SECTIONS]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), "sections must appear in contract order"


def test_no_required_section_is_empty():
    md = export_transcript(_make_session())
    for heading in REQUIRED_SECTIONS:
        if heading == "## Errors & Retries":
            continue  # optional; may be "(none)"
        assert _section(md, heading), f"{heading} should not be empty"


# ---------------------------------------------------------------------------
# Outcome mapping (trusted status, not self-report)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected_token",
    [
        ("completed", "pass"),
        ("error", "fail"),
        ("failed", "fail"),
    ],
)
def test_outcome_maps_status_to_token(status, expected_token):
    md = export_transcript(_make_session(status=status))
    outcome = _section(md, "## Outcome")
    # Outcome line must START with the bare token (contract rule).
    assert outcome.split()[0].lower() == expected_token


def test_active_session_is_in_progress():
    md = export_transcript(_make_session(status="active"))
    assert _section(md, "## Outcome") == "IN-PROGRESS"


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------

def test_actions_come_from_tool_calls():
    md = export_transcript(_make_session())
    actions = _section(md, "## Actions Taken")
    # Real tool names appear as numbered list items.
    assert "1. `git_clone`" in actions
    assert "2. `run_tests`" in actions


def test_key_outputs_include_tool_outputs_and_final():
    md = export_transcript(_make_session())
    outputs = _section(md, "## Key Outputs")
    assert "git_clone" in outputs or "run_tests" in outputs
    assert "commit a1b2c3d" in outputs  # final output summary


def test_task_comes_from_metadata():
    md = export_transcript(_make_session())
    assert "Add a small feature to the everything repo" in _section(md, "## Task")


def test_errors_section_lists_error_events():
    md = export_transcript(_make_session(status="error", with_error=True))
    errors = _section(md, "## Errors & Retries")
    assert "transient network error" in errors


def test_errors_section_none_when_clean():
    md = export_transcript(_make_session())
    assert _section(md, "## Errors & Retries") == "(none)"


def test_duration_is_parseable_arrow_form():
    md = export_transcript(_make_session())
    duration = _section(md, "## Duration")
    assert "->" in duration
    assert "14 minutes" in duration


# ---------------------------------------------------------------------------
# Input shapes
# ---------------------------------------------------------------------------

def test_accepts_session_dict_from_backend():
    """export_transcript should accept a session-shaped dict (export_session output)."""
    session_dict = {
        "agent_name": "gardener",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE + timedelta(minutes=5)).isoformat(),
        "status": "completed",
        "metadata": {"task": "prune stale branches"},
        "events": [
            {
                "event_type": "tool_call",
                "tool_call": {
                    "tool_name": "git_branch_delete",
                    "tool_input": {"branch": "old/feature"},
                    "tool_output": {"deleted": True},
                },
            },
        ],
    }
    md = export_transcript(session_dict)
    assert md.lstrip().startswith("# gardener Run - ")
    assert "git_branch_delete" in _section(md, "## Actions Taken")
    assert _section(md, "## Outcome").split()[0] == "pass"


def test_long_values_are_truncated():
    session = _make_session()
    session.events[0].tool_call.tool_input = {"blob": "x" * 5000}
    md = export_transcript(session)
    # Should not dump 5000 chars verbatim into a list item.
    assert "x" * 500 not in md


def test_decision_events_become_actions():
    session = Session(agent_name="thinker", started_at=BASE, metadata={"task": "decide"})
    session.add_event(
        AgentEvent(
            event_type="decision",
            decision_trace=DecisionTrace(reasoning="chose approach B for safety"),
        )
    )
    session.ended_at = BASE + timedelta(minutes=1)
    session.status = "completed"
    md = export_transcript(session)
    assert "chose approach B" in _section(md, "## Actions Taken")


def test_contract_version_constant():
    assert TRANSCRIPT_CONTRACT_VERSION == "transcript-contract@v1"


def test_exporter_class_matches_module_function():
    session = _make_session()
    assert TranscriptExporter().render(session) == export_transcript(session)
