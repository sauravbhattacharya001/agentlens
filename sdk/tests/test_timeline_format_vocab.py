"""Vocabulary-parity tests for the timeline_format presentation module.

``timeline_format`` holds the pure event-styling vocabulary the multi-format
``TimelineRenderer`` consumes: the per-event icon map (``_ICONS``), the HTML
colour map (``_HTML_COLORS``) and the ``_icon`` lookup helper. Those symbols
were only exercised indirectly through full renderer runs, so the vocabulary's
own invariants were unpinned. This module locks them down directly:

* every icon has a matching HTML colour (and vice-versa) -- a renderer that
  looks up ``_icon(etype)`` and ``_HTML_COLORS[etype]`` must never get one
  without the other,
* both maps fall back to their ``"generic"`` entry for an unknown event type,
* ``_icon`` never raises and always returns a non-empty glyph.
"""

from __future__ import annotations

from agentlens import timeline_format as tf


KNOWN_EVENT_TYPES = [
    "session_start",
    "session_end",
    "llm_call",
    "tool_call",
    "error",
    "decision",
    "generic",
]


class TestIconLookup:
    def test_known_event_types_map_to_their_icon(self):
        for etype in KNOWN_EVENT_TYPES:
            assert tf._icon(etype) == tf._ICONS[etype]

    def test_unknown_event_type_falls_back_to_generic(self):
        assert tf._icon("does_not_exist") == tf._ICONS["generic"]
        assert tf._icon("") == tf._ICONS["generic"]

    def test_icon_always_returns_non_empty_glyph(self):
        for etype in KNOWN_EVENT_TYPES + ["totally_unknown", ""]:
            glyph = tf._icon(etype)
            assert isinstance(glyph, str)
            assert glyph != ""


class TestVocabularyParity:
    def test_icons_and_html_colors_cover_the_same_event_types(self):
        assert set(tf._ICONS) == set(tf._HTML_COLORS)

    def test_generic_fallback_key_exists_in_both_maps(self):
        assert "generic" in tf._ICONS
        assert "generic" in tf._HTML_COLORS

    def test_every_html_color_is_a_hex_string(self):
        for etype, color in tf._HTML_COLORS.items():
            assert isinstance(color, str)
            assert color.startswith("#")
            assert len(color) == 7, etype