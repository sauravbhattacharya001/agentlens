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
    export_run_metadata,
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


# ---------------------------------------------------------------------------
# Run metadata (ground-truth side-channel for agent-eval verification)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        ("completed", "ok"),
        ("error", "error"),
        ("failed", "error"),
        ("active", "running"),
    ],
)
def test_run_metadata_maps_status_to_exit_status(status, expected):
    meta = export_run_metadata(_make_session(status=status))
    assert meta["exitStatus"] == expected


def test_run_metadata_includes_timing():
    meta = export_run_metadata(_make_session())
    assert "startedAt" in meta and isinstance(meta["startedAt"], str)
    assert "endedAt" in meta and isinstance(meta["endedAt"], str)
    # 14-minute run -> 840000 ms
    assert meta["durationMs"] == pytest.approx(14 * 60_000)


def test_run_metadata_omits_end_for_active_session():
    session = _make_session(status="active")
    session.ended_at = None
    meta = export_run_metadata(session)
    assert meta["exitStatus"] == "running"
    assert "endedAt" not in meta


def test_run_metadata_accepts_session_dict():
    session_dict = {
        "agent_name": "gardener",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE + timedelta(minutes=5)).isoformat(),
        "status": "completed",
        "events": [],
    }
    meta = export_run_metadata(session_dict)
    assert meta["exitStatus"] == "ok"
    assert meta["durationMs"] == pytest.approx(5 * 60_000)


def test_run_metadata_is_json_serializable():
    import json

    meta = export_run_metadata(_make_session())
    # Must round-trip cleanly so it can be handed to agent-eval.
    assert json.loads(json.dumps(meta)) == meta


# ---------------------------------------------------------------------------
# Fallback / edge paths (fill uncovered branches)
# ---------------------------------------------------------------------------

def test_task_falls_back_to_first_event_input_when_no_metadata_task():
    """With no task/prompt/goal/description in metadata, the Task section is
    derived from the first event that carries an input_data summary."""
    session = Session(agent_name="builder", started_at=BASE, metadata={})
    # First event has no input_data (skipped), second supplies it.
    session.add_event(AgentEvent(event_type="llm_call"))
    session.add_event(
        AgentEvent(event_type="llm_call", input_data={"prompt": "summarize the diff"})
    )
    session.ended_at = BASE + timedelta(minutes=1)
    session.status = "completed"
    task = _section(export_transcript(session), "## Task")
    assert "summarize the diff" in task


def test_task_placeholder_when_nothing_recorded():
    """No metadata task and no event input -> explicit placeholder."""
    session = Session(agent_name="builder", started_at=BASE, metadata={})
    session.add_event(AgentEvent(event_type="llm_call"))
    session.ended_at = BASE + timedelta(minutes=1)
    session.status = "completed"
    assert _section(export_transcript(session), "## Task") == "(no task recorded)"


def test_actions_placeholder_when_no_actions():
    session = Session(agent_name="idle", started_at=BASE, metadata={"task": "nap"})
    session.add_event(AgentEvent(event_type="llm_call"))
    session.ended_at = BASE + timedelta(minutes=1)
    session.status = "completed"
    assert _section(export_transcript(session), "## Actions Taken") == "(no actions recorded)"


def test_decision_reasoning_from_top_level_field():
    """A decision event whose reasoning lives at the top level (no
    decision_trace) still becomes an action via the ev.get('reasoning')
    fallback."""
    session_dict = {
        "agent_name": "thinker",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE + timedelta(minutes=1)).isoformat(),
        "status": "completed",
        "metadata": {"task": "decide"},
        "events": [
            {"event_type": "decision", "reasoning": "picked path A after weighing cost"},
        ],
    }
    actions = _section(export_transcript(session_dict), "## Actions Taken")
    assert "picked path A" in actions


def test_outputs_skip_empty_summaries_and_use_later_final():
    """Tool outputs / final outputs that summarize to empty are skipped, so the
    exporter walks past them to the next candidate (covers the loop-continue
    branches)."""
    session_dict = {
        "agent_name": "builder",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE + timedelta(minutes=1)).isoformat(),
        "status": "completed",
        "metadata": {"task": "t"},
        "events": [
            # tool_call present but tool_output summarizes to empty (empty string).
            {
                "event_type": "tool_call",
                "tool_call": {"tool_name": "noop", "tool_input": {}, "tool_output": ""},
            },
            # last event's output_data is empty -> skipped; walk to prior real output.
            {"event_type": "llm_call", "output_data": {"result": "done"}},
            {"event_type": "llm_call", "output_data": ""},
        ],
    }
    outputs = _section(export_transcript(session_dict), "## Key Outputs")
    # The empty tool output is not listed; the real final output is.
    assert "noop" not in outputs
    assert "done" in outputs


def test_outputs_placeholder_when_nothing_recorded():
    session = Session(agent_name="quiet", started_at=BASE, metadata={"task": "t"})
    session.add_event(AgentEvent(event_type="llm_call"))
    session.ended_at = BASE + timedelta(minutes=1)
    session.status = "completed"
    assert _section(export_transcript(session), "## Key Outputs") == "(no outputs recorded)"


def test_duration_appends_timezone_label_when_not_utc():
    md = TranscriptExporter(timezone_label="PT").render(_make_session())
    duration = _section(md, "## Duration")
    assert "[PT]" in duration


def test_run_metadata_converts_datetime_timing_to_isoformat():
    """When started_at/ended_at are datetime objects (not strings), the run
    metadata serializes them to ISO strings."""
    # Pass a session-shaped dict carrying raw datetime objects (not the
    # already-stringified to_api_dict form), exercising the datetime->isoformat
    # branch in to_run_metadata.
    session_dict = {
        "agent_name": "builder",
        "started_at": BASE,
        "ended_at": BASE + timedelta(minutes=3),
        "status": "completed",
        "events": [],
    }
    meta = export_run_metadata(session_dict)
    assert isinstance(meta["startedAt"], str) and "2026-06-05" in meta["startedAt"]
    assert isinstance(meta["endedAt"], str) and "2026-06-05" in meta["endedAt"]
    assert meta["durationMs"] == pytest.approx(3 * 60_000)


def test_run_metadata_prefers_explicit_duration_ms():
    session_dict = {
        "agent_name": "builder",
        "started_at": BASE.isoformat(),
        "ended_at": (BASE + timedelta(minutes=5)).isoformat(),
        "status": "completed",
        "duration_ms": 12345,
        "events": [],
    }
    meta = export_run_metadata(session_dict)
    # Explicit duration_ms wins over the start/end-derived value.
    assert meta["durationMs"] == 12345.0


def test_run_metadata_omits_timing_when_absent():
    session_dict = {"agent_name": "x", "status": "completed", "events": []}
    meta = export_run_metadata(session_dict)
    assert "startedAt" not in meta and "endedAt" not in meta
    assert "durationMs" not in meta


def test_run_metadata_omits_exit_status_for_unknown_status():
    """An unmapped session status yields no exitStatus key (verification then
    has no ground-truth status to grade against)."""
    session_dict = {"agent_name": "x", "status": "paused", "events": []}
    meta = export_run_metadata(session_dict)
    assert "exitStatus" not in meta


def test_transcript_claim_and_metadata_truth_can_diverge():
    """The whole point: a session can have a self-reported outcome that differs
    from the ground-truth status. The transcript carries the claim; the
    metadata carries the truth the verification check will catch."""
    # An errored session: transcript Outcome -> fail, metadata exitStatus -> error.
    session = _make_session(status="error")
    md = export_transcript(session)
    meta = export_run_metadata(session)
    assert _section(md, "## Outcome").split()[0] == "fail"
    assert meta["exitStatus"] == "error"
