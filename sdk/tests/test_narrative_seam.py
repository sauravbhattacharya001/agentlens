"""Pin the narrative_types.py seam.

The narrative value types (``NarrativeStyle``, ``NarrativeSection``,
``ToolSummary``, ``NarrativeConfig``, ``Narrative``) and their serialization
(``Narrative.to_markdown`` / ``to_dict``) live in their own module so
``narrative.py`` stays focused on the prose-building engine
(``NarrativeGenerator``).  These tests guard that boundary: the types must
remain importable from the sibling module, be re-exported by ``narrative`` (and
``agentlens``) as the *same* objects, the data model must carry no
session-traversal dependency, and a narrative produced by the engine must still
serialize through the moved methods unchanged.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.narrative import (
    Narrative,
    NarrativeConfig,
    NarrativeGenerator,
    NarrativeSection,
    NarrativeStyle,
    ToolSummary,
)

_MOVED_TYPES = (
    "Narrative",
    "NarrativeConfig",
    "NarrativeSection",
    "NarrativeStyle",
    "ToolSummary",
)


def _ts(offset_s: int = 0) -> datetime:
    return datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc).replace(
        second=offset_s % 60
    )


class TestNarrativeTypesSeam(unittest.TestCase):
    def test_types_importable_from_sibling_module(self):
        from agentlens import narrative_types

        for name in _MOVED_TYPES:
            self.assertTrue(
                hasattr(narrative_types, name),
                f"{name} should live in narrative_types",
            )

    def test_narrative_reexports_same_type_objects(self):
        # narrative.py re-exports the moved types; each must be the SAME object
        # as the one defined in narrative_types, not a divergent copy.
        from agentlens import narrative_types

        self.assertIs(Narrative, narrative_types.Narrative)
        self.assertIs(NarrativeConfig, narrative_types.NarrativeConfig)
        self.assertIs(NarrativeSection, narrative_types.NarrativeSection)
        self.assertIs(NarrativeStyle, narrative_types.NarrativeStyle)
        self.assertIs(ToolSummary, narrative_types.ToolSummary)

    def test_package_reexports_same_type_objects(self):
        # The public ``agentlens`` barrel must resolve the same objects through
        # the narrative module, so the import path stays stable for consumers.
        import agentlens
        from agentlens import narrative_types

        self.assertIs(agentlens.Narrative, narrative_types.Narrative)
        self.assertIs(agentlens.NarrativeConfig, narrative_types.NarrativeConfig)
        self.assertIs(agentlens.NarrativeSection, narrative_types.NarrativeSection)
        self.assertIs(agentlens.NarrativeStyle, narrative_types.NarrativeStyle)
        self.assertIs(agentlens.ToolSummary, narrative_types.ToolSummary)
        self.assertIs(agentlens.NarrativeGenerator, NarrativeGenerator)

    def test_types_module_defines_only_the_data_model(self):
        # The types module should own exactly the five value types and nothing
        # else of its own (no generator logic leaking back in), keeping the data
        # model and the prose engine from re-entangling.
        from agentlens import narrative_types

        own = sorted(
            name
            for name in vars(narrative_types)
            if not name.startswith("__")
            and getattr(
                getattr(narrative_types, name), "__module__", None
            )
            == narrative_types.__name__
        )
        self.assertEqual(own, sorted(_MOVED_TYPES))

    def test_types_module_has_no_models_dependency(self):
        # The data model serializes only its own fields, so it must not pull in
        # agentlens.models. Keeping the seam dependency-light is the point of the
        # split; a stray import here would re-couple it to the event schema.
        from agentlens import narrative_types

        self.assertFalse(hasattr(narrative_types, "AgentEvent"))
        self.assertFalse(hasattr(narrative_types, "Session"))

    def test_engine_output_serializes_through_moved_methods(self):
        # End-to-end guard: a narrative built by the engine must still round-trip
        # through the moved to_markdown()/to_dict() with the expected shape.
        session = Session(
            session_id="seam-1",
            agent_name="seam-agent",
            status="completed",
            started_at=_ts(0),
            ended_at=_ts(5),
        )
        session.events = [
            AgentEvent(event_type="llm_call", timestamp=_ts(1), model="gpt-x",
                       tokens_in=10, tokens_out=20),
            AgentEvent(event_type="tool_call", timestamp=_ts(2),
                       tool_call=ToolCall(tool_name="search", duration_ms=12.0)),
        ]

        narrative = NarrativeGenerator().generate(session)
        self.assertIsInstance(narrative, Narrative)

        md = narrative.to_markdown()
        self.assertIn("# Session Narrative: seam-1", md)
        self.assertIn("seam-agent", md)

        data = narrative.to_dict()
        self.assertEqual(data["session_id"], "seam-1")
        self.assertEqual(data["agent_name"], "seam-agent")
        self.assertEqual(data["style"], NarrativeStyle.TECHNICAL.value)
        self.assertEqual(len(data["tool_summaries"]), 1)
        self.assertEqual(data["tool_summaries"][0]["tool_name"], "search")

    def test_config_post_init_coerces_string_style(self):
        # NarrativeConfig.__post_init__ moved with the type; confirm the string
        # -> enum coercion still works through the public import path.
        cfg = NarrativeConfig(style="executive")
        self.assertIs(cfg.style, NarrativeStyle.EXECUTIVE)

    def test_generated_at_uses_shared_utcnow_clock(self):
        # generated_at defaults through the shared _utils.utcnow clock (like every
        # other timestamp in the SDK) rather than a local
        # ``datetime.now(timezone.utc)`` copy. Guard both halves of that contract:
        #   1) the field's default_factory IS the shared helper object (the same
        #      symbol models/span defer to), and
        #   2) the default it mints is timezone-aware UTC.
        from agentlens import _utils, narrative_types

        self.assertIs(
            narrative_types.Narrative.__dataclass_fields__["generated_at"].default_factory,
            _utils.utcnow,
            "generated_at should default via the shared _utils.utcnow helper",
        )

        n = Narrative(session_id="s", agent_name="a", summary="", body="")
        self.assertEqual(n.generated_at.tzinfo, timezone.utc)

    def test_types_module_carries_no_local_clock_import(self):
        # The fold onto the shared clock also drops the now-unused ``timezone``
        # import from the data-model module; a stray one would signal a local
        # datetime.now(timezone.utc) copy creeping back in.
        from agentlens import narrative_types

        self.assertFalse(
            hasattr(narrative_types, "timezone"),
            "narrative_types should not import timezone once it defers to "
            "_utils.utcnow for the generated_at default",
        )


if __name__ == "__main__":
    unittest.main()
