"""Pin the timeline_render.py seam.

The stateless multi-format render engine (:func:`render_text`,
:func:`render_markdown`, :func:`render_html` plus the small error-extraction
helpers) lives in ``agentlens.timeline_render`` so ``timeline.py`` keeps only
the :class:`TimelineRenderer` orchestration (offset computation, filtering,
analysis, IO).  These tests guard that boundary: the render functions must stay
importable from the sibling module, ``TimelineRenderer`` must delegate to them
rather than re-implement the formatting, the engine must not depend back on the
orchestrator, and rendering directly through the engine must produce byte-for-
byte the same output the public ``render_*`` methods already return.
"""

from __future__ import annotations

import inspect
import unittest

from agentlens import timeline_render
from agentlens.timeline import TimelineRenderer

_RENDER_FUNCS = (
    "render_text",
    "render_markdown",
    "render_html",
    "_is_error_event",
    "_error_message",
)


def _make_events() -> list[dict]:
    """A representative mix: start, llm, ok tool, failed tool, error, end."""
    return [
        {"event_type": "session_start", "timestamp": "2026-03-16T12:00:00+00:00"},
        {
            "event_type": "llm_call",
            "timestamp": "2026-03-16T12:00:01+00:00",
            "model": "gpt-x",
            "tokens_in": 10,
            "tokens_out": 20,
            "duration_ms": 1500.0,
            "decision_trace": {"reasoning": "because"},
        },
        {
            "event_type": "tool_call",
            "timestamp": "2026-03-16T12:00:03+00:00",
            "tool_call": {"tool_name": "search", "tool_output": {"ok": True}},
            "duration_ms": 12.0,
        },
        {
            "event_type": "tool_call",
            "timestamp": "2026-03-16T12:00:04+00:00",
            "tool_call": {"tool_name": "grep", "tool_output": {"error": "nope"}},
            "duration_ms": 8.0,
        },
        {
            "event_type": "error",
            "timestamp": "2026-03-16T12:00:05+00:00",
            "output_data": {"error": "boom"},
        },
        {"event_type": "session_end", "timestamp": "2026-03-16T12:00:06+00:00"},
    ]


def _session() -> dict:
    return {"session_id": "render-1", "agent_name": "render-agent"}


class TestTimelineRenderSeam(unittest.TestCase):
    # -- boundary shape ------------------------------------------------------

    def test_render_functions_importable_from_sibling_module(self):
        for name in _RENDER_FUNCS:
            self.assertTrue(
                hasattr(timeline_render, name),
                f"{name} should live in timeline_render",
            )
            self.assertTrue(callable(getattr(timeline_render, name)))

    def test_render_module_has_no_orchestrator_dependency(self):
        # The engine consumes (events, session, summary) but must not import the
        # orchestrator, or the split's dependency direction would invert.
        self.assertFalse(hasattr(timeline_render, "TimelineRenderer"))

    def test_renderer_delegates_to_render_module(self):
        # Each render_* method should call into timeline_render, not re-implement
        # the formatting. Guard by asserting the engine functions are referenced
        # from the orchestrator method source.
        for method, fn in (
            (TimelineRenderer.render_text, "_render_text"),
            (TimelineRenderer.render_markdown, "_render_markdown"),
            (TimelineRenderer.render_html, "_render_html"),
        ):
            src = inspect.getsource(method)
            self.assertIn(fn, src, f"{method.__name__} should delegate to {fn}")

    # -- equivalence: engine output == public method output ------------------

    def _assert_method_matches_engine(self, fmt: str, **kwargs) -> None:
        r = TimelineRenderer(_make_events(), _session())
        method = getattr(r, f"render_{fmt}")
        engine = getattr(timeline_render, f"render_{fmt}")
        expected = engine(r.events, r.session, r.get_summary(), **kwargs)
        self.assertEqual(method(**kwargs), expected)

    def test_text_method_matches_engine(self):
        self._assert_method_matches_engine("text")
        self._assert_method_matches_engine(
            "text", show_metadata=False, show_tokens=False, show_duration=False,
            max_width=60,
        )

    def test_markdown_method_matches_engine(self):
        self._assert_method_matches_engine("markdown")
        self._assert_method_matches_engine(
            "markdown", include_toc=False, show_tokens=False,
        )

    def test_html_method_matches_engine(self):
        self._assert_method_matches_engine("html")
        self._assert_method_matches_engine(
            "html", dark_mode=True, title="Custom Title",
        )

    # -- direct unit coverage of the moved functions -------------------------

    def test_render_text_contents(self):
        out = timeline_render.render_text(
            _events_with_offsets(), _session(), _summary(),
        )
        self.assertIn("Session Timeline: render-1", out)
        self.assertIn("Agent: render-agent", out)
        self.assertIn("LLM_CALL [gpt-x]", out)
        self.assertIn("status: error", out)   # failed tool footprint
        self.assertIn("status: success", out)  # ok tool footprint
        self.assertIn("has_reasoning", out)
        self.assertIn("Summary", out)

    def test_render_markdown_is_a_table(self):
        out = timeline_render.render_markdown(
            _events_with_offsets(), _session(), _summary(),
        )
        self.assertIn("# Session Timeline: render-1", out)
        self.assertIn("## Table of Contents", out)
        self.assertIn("| Time | Type | Details | Duration | Tokens |", out)
        self.assertIn("boom", out)  # error detail surfaced in the table

    def test_render_markdown_without_toc(self):
        out = timeline_render.render_markdown(
            _events_with_offsets(), _session(), _summary(), include_toc=False,
        )
        self.assertNotIn("## Table of Contents", out)

    def test_render_html_is_self_contained(self):
        out = timeline_render.render_html(
            _events_with_offsets(), _session(), _summary(),
        )
        self.assertTrue(out.startswith("<!DOCTYPE html>"))
        self.assertIn("<style>", out)
        self.assertIn("error-details", out)
        self.assertNotIn("http://", out)  # no external resources

    def test_render_html_escapes_markup(self):
        events = [{
            "event_type": "error",
            "timestamp": "2026-03-16T12:00:00+00:00",
            "_offset_ms": 0.0,
            "output_data": {"error": "<script>alert(1)</script>"},
        }]
        summary = {
            "total_events": 1, "total_duration_ms": 0.0, "total_tokens": 0,
            "error_count": 1, "models_used": [],
        }
        out = timeline_render.render_html(events, {"session_id": "x"}, summary)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    # -- shared error helpers ------------------------------------------------

    def test_is_error_event(self):
        self.assertTrue(timeline_render._is_error_event({"event_type": "error"}))
        self.assertTrue(timeline_render._is_error_event(
            {"event_type": "tool_call",
             "tool_call": {"tool_output": {"error": "x"}}}
        ))
        self.assertFalse(timeline_render._is_error_event(
            {"event_type": "tool_call",
             "tool_call": {"tool_output": {"ok": True}}}
        ))
        self.assertFalse(timeline_render._is_error_event({"event_type": "llm_call"}))

    def test_error_message_prefers_error_then_message(self):
        self.assertEqual(
            timeline_render._error_message({"output_data": {"error": "E"}}), "E"
        )
        self.assertEqual(
            timeline_render._error_message({"output_data": {"message": "M"}}), "M"
        )
        self.assertEqual(timeline_render._error_message({}), "")
        self.assertEqual(
            timeline_render._error_message({"output_data": "not-a-dict"}),
            "not-a-dict",
        )


# -- helpers: events with offsets already computed (engine expects them) -----


def _events_with_offsets() -> list[dict]:
    r = TimelineRenderer(_make_events(), _session())
    return r.events


def _summary() -> dict:
    return TimelineRenderer(_make_events(), _session()).get_summary()


if __name__ == "__main__":
    unittest.main()
