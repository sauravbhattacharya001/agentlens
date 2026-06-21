"""Pin the narrative_render.py seam.

The stateless prose-building engine (event classification, per-tool / per-model
aggregation, the section/summary/body line builders, and the duration
formatter) lives in ``agentlens.narrative_render`` so ``narrative.py`` keeps
only the :class:`NarrativeGenerator` orchestration.  These tests guard that
boundary: the engine functions must remain importable from the sibling module,
``NarrativeGenerator`` must delegate to them rather than re-implementing the
logic, the engine must stay free of any generator/value-type state, and the
moved functions must keep producing the same prose the public ``generate``
path already returns.
"""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone

from agentlens import narrative_render
from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.narrative import NarrativeGenerator
from agentlens.narrative_types import NarrativeConfig, NarrativeStyle, ToolSummary

_ENGINE_FUNCS = (
    "classify_events",
    "build_tool_summaries",
    "aggregate_models",
    "build_summary",
    "build_body",
    "build_timeline",
    "build_decisions",
    "build_errors",
    "fmt_dur",
)


def _ts(offset_s: int = 0) -> datetime:
    return datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc).replace(
        second=offset_s % 60
    )


def _sample_events() -> list[AgentEvent]:
    return [
        AgentEvent(event_type="llm_call", timestamp=_ts(1), model="gpt-x",
                   tokens_in=10, tokens_out=20),
        AgentEvent(event_type="tool_call", timestamp=_ts(2),
                   tool_call=ToolCall(tool_name="search", duration_ms=12.0)),
        AgentEvent(event_type="error", timestamp=_ts(3),
                   output_data={"error": "boom"}),
    ]


class TestNarrativeRenderSeam(unittest.TestCase):
    def test_engine_functions_importable_from_sibling_module(self):
        for name in _ENGINE_FUNCS:
            self.assertTrue(
                hasattr(narrative_render, name),
                f"{name} should live in narrative_render",
            )
            self.assertTrue(callable(getattr(narrative_render, name)))

    def test_engine_functions_are_module_level_not_generator_methods(self):
        # The prose builders moved OFF the class; NarrativeGenerator must no
        # longer carry the private _build_*/_classify_*/_fmt_dur methods, so the
        # engine and the orchestrator cannot silently re-entangle.
        for legacy in (
            "_classify_events",
            "_build_tool_summaries",
            "_aggregate_models",
            "_build_summary",
            "_build_body",
            "_build_timeline",
            "_build_decisions",
            "_build_errors",
            "_fmt_dur",
        ):
            self.assertFalse(
                hasattr(NarrativeGenerator, legacy),
                f"{legacy} should have moved to narrative_render",
            )

    def test_generator_delegates_to_render_module(self):
        # generate() should call into narrative_render, not re-implement the
        # builders. Guard by asserting the engine module is referenced from the
        # orchestrator's source.
        src = inspect.getsource(NarrativeGenerator.generate)
        self.assertIn("_render.", src)
        self.assertIn("build_summary", src)
        self.assertIn("build_body", src)

    def test_classify_events_single_pass_buckets(self):
        llm, tool, dec, err = narrative_render.classify_events(_sample_events())
        self.assertEqual(len(llm), 1)
        self.assertEqual(len(tool), 1)
        self.assertEqual(len(dec), 0)
        self.assertEqual(len(err), 1)

    def test_build_tool_summaries_aggregates(self):
        summaries = narrative_render.build_tool_summaries([
            AgentEvent(event_type="tool_call", timestamp=_ts(1),
                       tool_call=ToolCall(tool_name="grep", duration_ms=10.0)),
            AgentEvent(event_type="tool_call", timestamp=_ts(2),
                       tool_call=ToolCall(tool_name="grep", duration_ms=30.0),
                       output_data={"error": "nope"}),
        ])
        self.assertIn("grep", summaries)
        g = summaries["grep"]
        self.assertIsInstance(g, ToolSummary)
        self.assertEqual(g.call_count, 2)
        self.assertEqual(g.failure_count, 1)
        self.assertEqual(g.success_count, 1)
        self.assertEqual(g.avg_duration_ms, 20.0)

    def test_fmt_dur_formats(self):
        self.assertEqual(narrative_render.fmt_dur(0), "")
        self.assertEqual(narrative_render.fmt_dur(-5), "")
        self.assertEqual(narrative_render.fmt_dur(45), "45s")
        self.assertEqual(narrative_render.fmt_dur(125), "2m 5s")
        self.assertEqual(narrative_render.fmt_dur(3725), "1h 2m")

    def test_render_module_has_no_generator_dependency(self):
        # The engine consumes events/config but must not import the orchestrator
        # (that would invert the dependency the split establishes).
        self.assertFalse(hasattr(narrative_render, "NarrativeGenerator"))

    def test_moved_builders_match_public_generate_output(self):
        # End-to-end equivalence: composing the body directly via the engine
        # must equal what the public generate() path emits, proving the move was
        # behavior-preserving rather than a divergent reimplementation.
        session = Session(
            session_id="render-1",
            agent_name="render-agent",
            status="completed",
            started_at=_ts(0),
            ended_at=_ts(5),
        )
        session.events = _sample_events()

        cfg = NarrativeConfig(style=NarrativeStyle.TECHNICAL)
        narrative = NarrativeGenerator().generate(session, cfg)

        llm, tool, dec, err = narrative_render.classify_events(session.events)
        tool_map = narrative_render.build_tool_summaries(tool)
        expected_body = narrative_render.build_body(
            session, session.events, llm, tool, dec, err, tool_map,
            narrative.total_tokens, narrative.total_cost_usd,
            narrative.duration_seconds, cfg.style,
        )
        self.assertEqual(narrative.body, expected_body)


if __name__ == "__main__":
    unittest.main()
