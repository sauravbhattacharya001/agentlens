"""Branch-depth coverage for the ``timeline_render`` engine.

``test_timeline_render_seam.py`` pins the module boundary and the common
render paths.  These tests target the conditional branches inside the three
render functions that the seam suite exercises only indirectly (if at all):
the ``session_end`` total-tokens footer, the tool-label fallback when an event
has no model, the LLM-average line in the text summary, the Markdown
``Models:`` line and column dropping, the HTML duration-bar scaling and
dark-mode palette, and the header when no agent/duration is present.

All events carry ``_offset_ms`` (the engine expects offsets already computed),
so we build them through :class:`TimelineRenderer` and render directly through
the stateless engine.
"""

from __future__ import annotations

import unittest

from agentlens import timeline_render
from agentlens.timeline import TimelineRenderer


def _events() -> list[dict]:
    """start -> llm(with model+tokens+duration) -> ok tool(no model) -> end(tokens)."""
    return [
        {"event_type": "session_start", "timestamp": "2026-03-16T12:00:00+00:00"},
        {
            "event_type": "llm_call",
            "timestamp": "2026-03-16T12:00:01+00:00",
            "model": "gpt-x",
            "tokens_in": 10,
            "tokens_out": 20,
            "duration_ms": 1500.0,
        },
        {
            "event_type": "tool_call",
            "timestamp": "2026-03-16T12:00:03+00:00",
            "tool_call": {"tool_name": "search", "tool_output": {"ok": True}},
            "duration_ms": 12.0,
        },
        {
            "event_type": "session_end",
            "timestamp": "2026-03-16T12:00:06+00:00",
            "tokens_in": 5,
            "tokens_out": 5,
        },
    ]


def _rendered(fmt: str, events: list[dict] | None = None, session: dict | None = None, **kw) -> str:
    r = TimelineRenderer(events or _events(), session or {"session_id": "s", "agent_name": "a"})
    engine = getattr(timeline_render, f"render_{fmt}")
    return engine(r.events, r.session, r.get_summary(), **kw)


class TestRenderTextBranches(unittest.TestCase):
    def test_session_end_total_tokens_footer_line(self):
        # etype == "session_end" branch emits a per-event total_tokens meta line.
        out = _rendered("text")
        self.assertIn("total_tokens: 40", out)

    def test_tool_label_uses_tool_name_when_no_model(self):
        # The `elif tool_name` label branch: a tool_call event has no model, so
        # the label suffix must be the tool name, not a model.
        out = _rendered("text")
        self.assertIn("TOOL_CALL [search]", out)

    def test_summary_has_llm_average_duration(self):
        # line2_parts LLM branch fires only when there is >=1 llm_call event.
        out = _rendered("text")
        self.assertIn("LLM calls: 1 (avg 1.5s)", out)
        self.assertIn("Tools: 1", out)
        self.assertIn("Errors: 0", out)

    def test_summary_omits_llm_line_when_no_llm_calls(self):
        events = [
            {"event_type": "tool_call", "timestamp": "2026-03-16T12:00:00+00:00",
             "tool_call": {"tool_name": "t", "tool_output": {"ok": True}}},
        ]
        out = _rendered("text", events=events)
        self.assertNotIn("LLM calls:", out)
        self.assertIn("Tools: 1", out)

    def test_header_without_agent_or_duration(self):
        events = [{"event_type": "generic", "timestamp": "2026-03-16T12:00:00+00:00"}]
        out = _rendered("text", events=events, session={"session_id": "z"})
        # No agent_name -> no "Agent:" part; zero span -> "0ms" duration is dropped.
        self.assertIn("Session Timeline: z", out)
        self.assertIn("Events: 1", out)
        self.assertNotIn("Agent:", out)

    def test_max_width_floored_at_40(self):
        # max_width below 40 is clamped; the header rule uses the effective width.
        out = _rendered("text", max_width=5)
        header = out.splitlines()[0]
        self.assertEqual(len(header), 40)

    def test_status_success_for_ok_tool(self):
        out = _rendered("text")
        self.assertIn("status: success", out)


class TestRenderMarkdownBranches(unittest.TestCase):
    def test_models_used_summary_line(self):
        out = _rendered("markdown")
        self.assertIn("- **Models:** gpt-x", out)

    def test_tool_detail_column(self):
        out = _rendered("markdown")
        self.assertIn("tool: search", out)
        self.assertIn("model: gpt-x", out)

    def test_columns_dropped_when_flags_off(self):
        out = _rendered("markdown", show_duration=False, show_tokens=False)
        # Header collapses to the three base columns.
        self.assertIn("| Time | Type | Details |", out)
        self.assertNotIn("| Duration |", out)

    def test_detail_dash_when_no_model_tool_or_error(self):
        events = [{"event_type": "generic", "timestamp": "2026-03-16T12:00:00+00:00"}]
        out = _rendered("markdown", events=events)
        # The generic row has no details -> "-" placeholder in the Details column.
        row = [ln for ln in out.splitlines() if "GENERIC" in ln][0]
        self.assertIn("| - |", row)

    def test_models_line_omitted_when_no_models(self):
        events = [{"event_type": "generic", "timestamp": "2026-03-16T12:00:00+00:00"}]
        out = _rendered("markdown", events=events)
        self.assertNotIn("**Models:**", out)


class TestRenderHtmlBranches(unittest.TestCase):
    def test_dark_mode_palette(self):
        light = _rendered("html", dark_mode=False)
        dark = _rendered("html", dark_mode=True)
        self.assertIn("#1a1a2e", dark)      # dark background
        self.assertNotIn("#1a1a2e", light)

    def test_duration_bar_scales_to_longest_event(self):
        out = _rendered("html")
        # The longest event (1500ms) should get the full ~80px bar; a shorter
        # one gets a proportionally narrower bar. Both duration-bar spans exist.
        self.assertIn("duration-bar", out)
        self.assertIn("width:80px", out)

    def test_tool_name_span_when_no_model(self):
        out = _rendered("html")
        self.assertIn("[search]", out)
        self.assertIn("[gpt-x]", out)

    def test_models_used_line(self):
        out = _rendered("html")
        self.assertIn("Models: gpt-x", out)

    def test_models_line_omitted_when_no_models(self):
        events = [{"event_type": "generic", "timestamp": "2026-03-16T12:00:00+00:00"}]
        out = _rendered("html", events=events)
        self.assertNotIn("<p>Models:", out)

    def test_custom_title_used_in_head_and_heading(self):
        out = _rendered("html", title="My Run")
        self.assertIn("<title>My Run</title>", out)
        self.assertIn("<h1>My Run</h1>", out)


if __name__ == "__main__":
    unittest.main()
