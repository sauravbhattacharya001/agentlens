"""Branch-depth coverage for the narrative_render prose engine.

``test_narrative_render_seam`` pins the module boundary and end-to-end
equivalence; this file exercises the individual line/summary builders
*directly* across every :class:`NarrativeStyle` branch and their notable
edge cases (empty inputs, missing optional fields, cost/status gating,
per-model aggregation).  These builders are otherwise only reached
indirectly through ``NarrativeGenerator.generate``, so a regression in a
single style branch could ship silently -- these tests make each branch a
first-class contract.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agentlens import narrative_render as nr
from agentlens.models import AgentEvent, DecisionTrace, Session, ToolCall
from agentlens.narrative_types import NarrativeStyle


def _ts(offset_s: int = 0) -> datetime:
    return datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc).replace(
        second=offset_s % 60
    )


def _session(status: str = "completed") -> Session:
    return Session(
        session_id="brd-1",
        agent_name="brancher",
        status=status,
        started_at=_ts(0),
        ended_at=_ts(5),
    )


class TestAggregateModels(unittest.TestCase):
    def test_buckets_calls_and_tokens_per_model(self):
        llm = [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model="m-a",
                       tokens_in=10, tokens_out=5),
            AgentEvent(event_type="llm_call", timestamp=_ts(2), model="m-a",
                       tokens_in=1, tokens_out=4),
            AgentEvent(event_type="llm_call", timestamp=_ts(3), model="m-b",
                       tokens_in=7, tokens_out=3),
        ]
        agg = nr.aggregate_models(llm)
        self.assertEqual(agg["m-a"], [2, 20])
        self.assertEqual(agg["m-b"], [1, 10])

    def test_skips_events_without_model(self):
        llm = [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model=None,
                       tokens_in=9, tokens_out=9),
        ]
        self.assertEqual(nr.aggregate_models(llm), {})

    def test_empty(self):
        self.assertEqual(nr.aggregate_models([]), {})


class TestBuildSummary(unittest.TestCase):
    def test_executive_includes_duration_cost_errors(self):
        out = nr.build_summary(
            _session(), event_count=4, total_tokens=1234, cost=0.5,
            error_count=2, decision_count=1, tools=["t"], duration_s=125,
            style=NarrativeStyle.EXECUTIVE,
        )
        self.assertIn("'brancher' completed in 2m 5s", out)
        self.assertIn("1,234 tokens", out)
        self.assertIn("$0.5000", out)
        self.assertIn("2 error(s)", out)
        self.assertTrue(out.endswith("."))

    def test_executive_omits_zero_cost_and_no_duration(self):
        out = nr.build_summary(
            _session(), event_count=1, total_tokens=5, cost=0.0,
            error_count=0, decision_count=0, tools=[], duration_s=0,
            style=NarrativeStyle.EXECUTIVE,
        )
        self.assertNotIn("cost", out)
        self.assertNotIn(" in ", out)
        self.assertNotIn("error", out)

    def test_casual_mentions_tools_and_errors(self):
        out = nr.build_summary(
            _session(), event_count=3, total_tokens=42, cost=0.0,
            error_count=1, decision_count=0, tools=["a", "b"], duration_s=45,
            style=NarrativeStyle.CASUAL,
        )
        self.assertIn("ran for 45s", out)
        self.assertIn("used 2 tool(s)", out)
        self.assertIn("hit 1 error(s)", out)

    def test_technical_pipe_delimited_fields(self):
        out = nr.build_summary(
            _session(), event_count=3, total_tokens=42, cost=0.25,
            error_count=1, decision_count=2, tools=["a", "b"], duration_s=45,
            style=NarrativeStyle.TECHNICAL,
        )
        self.assertIn("session_id=brd-1", out)
        self.assertIn("agent=brancher", out)
        self.assertIn("status=completed", out)
        self.assertIn("events=3", out)
        self.assertIn("tokens=42", out)
        self.assertIn("duration=45s", out)
        self.assertIn("cost=$0.2500", out)
        self.assertIn("errors=1", out)
        self.assertIn("decisions=2", out)
        self.assertIn("tools=[a,b]", out)
        self.assertIn(" | ", out)

    def test_non_completed_status_word_passthrough(self):
        out = nr.build_summary(
            _session(status="failed"), event_count=1, total_tokens=1, cost=0.0,
            error_count=0, decision_count=0, tools=[], duration_s=0,
            style=NarrativeStyle.EXECUTIVE,
        )
        self.assertIn("failed", out)


class TestBuildBody(unittest.TestCase):
    def _events(self):
        return [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model="gpt-x",
                       tokens_in=10, tokens_out=20),
            AgentEvent(event_type="tool_call", timestamp=_ts(2),
                       tool_call=ToolCall(tool_name="search", duration_ms=12.0)),
            AgentEvent(event_type="tool_call", timestamp=_ts(3),
                       tool_call=ToolCall(tool_name="search", duration_ms=30.0),
                       output_data={"error": "nope"}),
            AgentEvent(event_type="decision", timestamp=_ts(4),
                       decision_trace=DecisionTrace(step=1, reasoning="picked A")),
            AgentEvent(event_type="error", timestamp=_ts(5),
                       output_data={"error": "boom"}),
        ]

    def _split(self, style):
        events = self._events()
        llm, tool, dec, err = nr.classify_events(events)
        tool_map = nr.build_tool_summaries(tool)
        return nr.build_body(
            _session(), events, llm, tool, dec, err, tool_map,
            total_tokens=30, cost=0.75, duration_s=125, style=style,
        )

    def test_technical_body_has_all_sections(self):
        body = self._split(NarrativeStyle.TECHNICAL)
        self.assertIn("Session brd-1 (brancher) processed 5 events in 2m 5s", body)
        self.assertIn("Total tokens: 30.", body)
        self.assertIn("LLM interactions: 1 call(s)", body)
        self.assertIn("Tool usage: 2 call(s) across 1 tool(s) [search]", body)
        self.assertIn("Failures: 1.", body)
        self.assertIn("Decision points: 1.", body)
        self.assertIn("Errors encountered: 1.", body)
        self.assertIn("Estimated cost: $0.7500.", body)
        self.assertIn("Final status: completed.", body)

    def test_casual_body_phrasing(self):
        body = self._split(NarrativeStyle.CASUAL)
        self.assertIn("Here's what happened", body)
        self.assertIn("Made 1 LLM call(s)", body)
        self.assertIn("Used 1 tool(s) (search)", body)
        self.assertIn("with 1 failure(s)", body)
        self.assertIn("made 1 notable decision(s)", body)
        self.assertIn("Ran into 1 error(s)", body)
        self.assertIn("Session ended with status: completed.", body)

    def test_executive_body_opening(self):
        body = self._split(NarrativeStyle.EXECUTIVE)
        self.assertIn("The agent 'brancher' executed a session", body)
        self.assertIn("over 2m 5s", body)

    def test_active_status_omits_status_paragraph(self):
        events = self._events()
        llm, tool, dec, err = nr.classify_events(events)
        tool_map = nr.build_tool_summaries(tool)
        body = nr.build_body(
            _session(status="active"), events, llm, tool, dec, err, tool_map,
            total_tokens=30, cost=0.0, duration_s=0, style=NarrativeStyle.TECHNICAL,
        )
        self.assertNotIn("Final status", body)
        self.assertNotIn("Estimated cost", body)

    def test_empty_events_minimal_body(self):
        body = nr.build_body(
            _session(), [], [], [], [], [], {},
            total_tokens=0, cost=0.0, duration_s=0, style=NarrativeStyle.TECHNICAL,
        )
        self.assertIn("processed 0 events", body)
        self.assertNotIn("LLM interactions", body)
        self.assertNotIn("Tool usage", body)


class TestBuildTimeline(unittest.TestCase):
    def test_technical_markup_per_event_type(self):
        events = [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model="gpt-x",
                       tokens_in=10, tokens_out=20),
            AgentEvent(event_type="tool_call", timestamp=_ts(2),
                       tool_call=ToolCall(tool_name="search", duration_ms=12.0)),
            AgentEvent(event_type="decision", timestamp=_ts(3),
                       decision_trace=DecisionTrace(step=1, reasoning="chose the fast path")),
            AgentEvent(event_type="error", timestamp=_ts(4),
                       output_data={"error": "kaboom"}),
            AgentEvent(event_type="custom", timestamp=_ts(5)),
        ]
        lines = nr.build_timeline(events, NarrativeStyle.TECHNICAL)
        self.assertTrue(lines[0].startswith("- `") and "**LLM call**" in lines[0])
        self.assertIn("(gpt-x)", lines[0])
        self.assertIn("**Tool: search**", lines[1])
        self.assertIn("(12ms)", lines[1])
        self.assertIn("**Decision**", lines[2])
        self.assertIn("chose the fast path", lines[2])
        self.assertIn("⚠️ **Error**", lines[3])
        self.assertIn("kaboom", lines[3])
        self.assertTrue(lines[4].endswith("custom"))

    def test_casual_llm_line_variant(self):
        events = [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model="gpt-x",
                       tokens_in=1, tokens_out=2),
        ]
        lines = nr.build_timeline(events, NarrativeStyle.CASUAL)
        self.assertIn("— LLM call (gpt-x), 3 tokens", lines[0])

    def test_llm_without_model_omits_model_info(self):
        events = [AgentEvent(event_type="llm_call", timestamp=_ts(1),
                             tokens_in=0, tokens_out=0)]
        lines = nr.build_timeline(events, NarrativeStyle.TECHNICAL)
        self.assertNotIn("(", lines[0].split("tokens")[0].replace("`", ""))

    def test_tool_without_duration_and_unknown_name(self):
        events = [AgentEvent(event_type="tool_call", timestamp=_ts(1))]
        lines = nr.build_timeline(events, NarrativeStyle.TECHNICAL)
        self.assertIn("**Tool: unknown**", lines[0])
        self.assertNotIn("ms)", lines[0])

    def test_empty_events_placeholder(self):
        self.assertEqual(
            nr.build_timeline([], NarrativeStyle.TECHNICAL),
            ["No events recorded."],
        )


class TestBuildDecisions(unittest.TestCase):
    def test_numbered_with_confidence_and_alternatives(self):
        events = [
            AgentEvent(event_type="decision", timestamp=_ts(1),
                       decision_trace=DecisionTrace(
                           step=1, reasoning="use cache", confidence=0.9,
                           alternatives_considered=["recompute", "skip"])),
        ]
        lines = nr.build_decisions(events, NarrativeStyle.TECHNICAL)
        self.assertIn("1. **use cache**", lines[0])
        self.assertIn("(confidence: 90%)", lines[0])
        self.assertIn("Alternatives considered: recompute, skip", lines[1])

    def test_no_confidence_no_alternatives(self):
        events = [
            AgentEvent(event_type="decision", timestamp=_ts(1),
                       decision_trace=DecisionTrace(step=2, reasoning="proceed")),
        ]
        lines = nr.build_decisions(events, NarrativeStyle.TECHNICAL)
        self.assertEqual(lines, ["1. **proceed**"])

    def test_missing_decision_trace_fallback(self):
        events = [AgentEvent(event_type="decision", timestamp=_ts(1))]
        lines = nr.build_decisions(events, NarrativeStyle.TECHNICAL)
        self.assertEqual(lines, ["1. Decision at step ?"])


class TestBuildErrors(unittest.TestCase):
    def test_error_field_preferred(self):
        events = [AgentEvent(event_type="error", timestamp=_ts(1),
                             output_data={"error": "primary"})]
        lines = nr.build_errors(events, NarrativeStyle.TECHNICAL)
        self.assertIn("— primary", lines[0])

    def test_message_field_fallback(self):
        events = [AgentEvent(event_type="error", timestamp=_ts(1),
                             output_data={"message": "secondary"})]
        lines = nr.build_errors(events, NarrativeStyle.TECHNICAL)
        self.assertIn("— secondary", lines[0])

    def test_no_output_data_unknown(self):
        events = [AgentEvent(event_type="error", timestamp=_ts(1))]
        lines = nr.build_errors(events, NarrativeStyle.TECHNICAL)
        self.assertIn("— Unknown error", lines[0])


if __name__ == "__main__":
    unittest.main()
