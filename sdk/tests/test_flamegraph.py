"""Tests for agentlens.flamegraph module."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from agentlens.flamegraph import Flamegraph, flamegraph_html, _FGNode, _event_label, _parse_ts
from agentlens.models import AgentEvent, Session, ToolCall
from agentlens.span import Span


def _ts(offset_ms: int = 0) -> datetime:
    """Create a UTC timestamp with an offset from a fixed base."""
    base = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(milliseconds=offset_ms)


def _make_event(event_type: str = "llm_call", offset_ms: int = 0,
                duration_ms: float = 100, **kwargs) -> AgentEvent:
    return AgentEvent(
        event_type=event_type,
        timestamp=_ts(offset_ms),
        duration_ms=duration_ms,
        **kwargs,
    )


def _make_span(name: str, offset_ms: int = 0, duration_ms: float = 500,
               parent_id: str | None = None) -> Span:
    s = Span(
        name=name,
        started_at=_ts(offset_ms),
        ended_at=_ts(offset_ms + int(duration_ms)),
        duration_ms=duration_ms,
        parent_id=parent_id,
    )
    return s


class TestParseTs(unittest.TestCase):
    def test_datetime_input(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = _parse_ts(dt)
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, dt.timestamp() * 1000, places=0)

    def test_string_input(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = _parse_ts(dt.isoformat())
        self.assertIsNotNone(result)

    def test_none_input(self):
        self.assertIsNone(_parse_ts(None))


class TestEventLabel(unittest.TestCase):
    def test_llm_call(self):
        e = _make_event(event_type="llm_call", model="gpt-4o")
        self.assertEqual(_event_label(e), "llm: gpt-4o")

    def test_llm_no_model(self):
        e = _make_event(event_type="llm_call")
        self.assertEqual(_event_label(e), "llm: unknown")

    def test_tool_call(self):
        tc = ToolCall(tool_name="web_search")
        e = _make_event(event_type="tool_call", tool_call=tc)
        self.assertEqual(_event_label(e), "tool: web_search")

    def test_decision(self):
        e = _make_event(event_type="decision")
        self.assertEqual(_event_label(e), "decision")

    def test_error(self):
        e = _make_event(event_type="error")
        self.assertEqual(_event_label(e), "error")

    def test_generic(self):
        e = _make_event(event_type="custom_thing")
        self.assertEqual(_event_label(e), "custom_thing")


class TestFGNode(unittest.TestCase):
    def test_to_dict_basic(self):
        node = _FGNode(
            name="test", start_ms=10, duration_ms=50,
            depth=0, event_type="llm_call",
        )
        d = node.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["start"], 10)
        self.assertEqual(d["duration"], 50)
        self.assertEqual(d["type"], "llm_call")
        self.assertNotIn("model", d)

    def test_to_dict_with_model(self):
        node = _FGNode(
            name="test", start_ms=0, duration_ms=100,
            depth=0, event_type="llm_call", model="gpt-4",
        )
        d = node.to_dict()
        self.assertEqual(d["model"], "gpt-4")

    def test_to_dict_with_tokens(self):
        node = _FGNode(
            name="test", start_ms=0, duration_ms=100,
            depth=0, event_type="llm_call",
            tokens_in=500, tokens_out=200,
        )
        d = node.to_dict()
        self.assertEqual(d["tokensIn"], 500)
        self.assertEqual(d["tokensOut"], 200)

    def test_to_dict_with_children(self):
        child = _FGNode(name="child", start_ms=0, duration_ms=50, depth=1, event_type="tool_call")
        parent = _FGNode(name="parent", start_ms=0, duration_ms=100, depth=0, event_type="span", children=[child])
        d = parent.to_dict()
        self.assertEqual(len(d["children"]), 1)
        self.assertEqual(d["children"][0]["name"], "child")


class TestFlamegraphEmpty(unittest.TestCase):
    def test_empty_events(self):
        fg = Flamegraph(events=[], spans=[])
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 0)
        self.assertEqual(data["totalMs"], 0)

    def test_empty_html(self):
        fg = Flamegraph(events=[])
        html = fg.render_html()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Flamegraph", html)


class TestFlamegraphEvents(unittest.TestCase):
    def test_single_event(self):
        events = [_make_event(duration_ms=200)]
        fg = Flamegraph(events)
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 1)
        self.assertEqual(data["nodes"][0]["duration"], 200)

    def test_sequential_events(self):
        events = [
            _make_event(offset_ms=0, duration_ms=100),
            _make_event(offset_ms=100, duration_ms=150),
            _make_event(offset_ms=250, duration_ms=50),
        ]
        fg = Flamegraph(events)
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 3)
        # Sequential events should all be at depth 0
        for n in data["nodes"]:
            self.assertEqual(n["depth"], 0)

    def test_overlapping_events(self):
        events = [
            _make_event(offset_ms=0, duration_ms=300),
            _make_event(offset_ms=50, duration_ms=100),
        ]
        fg = Flamegraph(events)
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 2)
        # Second event overlaps first, should go deeper
        depths = {n["start"]: n["depth"] for n in data["nodes"]}
        self.assertTrue(any(d > 0 for d in depths.values()))

    def test_event_with_tool_call(self):
        tc = ToolCall(tool_name="calculator")
        events = [_make_event(event_type="tool_call", tool_call=tc, duration_ms=75)]
        fg = Flamegraph(events)
        data = fg.to_data()
        self.assertEqual(data["nodes"][0]["tool"], "calculator")

    def test_event_with_tokens(self):
        events = [_make_event(tokens_in=1000, tokens_out=500, model="gpt-4")]
        fg = Flamegraph(events)
        data = fg.to_data()
        self.assertEqual(data["nodes"][0]["tokensIn"], 1000)
        self.assertEqual(data["nodes"][0]["tokensOut"], 500)
        self.assertEqual(data["nodes"][0]["model"], "gpt-4")


class TestFlamegraphSpans(unittest.TestCase):
    def test_single_span(self):
        span = _make_span("planning", duration_ms=500)
        fg = Flamegraph(events=[], spans=[span])
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 1)
        self.assertEqual(data["nodes"][0]["name"], "span: planning")
        self.assertEqual(data["nodes"][0]["depth"], 0)

    def test_nested_spans(self):
        parent = _make_span("parent", duration_ms=1000)
        child = _make_span("child", offset_ms=100, duration_ms=300,
                           parent_id=parent.span_id)
        fg = Flamegraph(events=[], spans=[parent, child])
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 2)
        self.assertEqual(data["maxDepth"], 1)

    def test_events_nested_under_span(self):
        span = _make_span("planning", duration_ms=500)
        event = _make_event(offset_ms=100, duration_ms=50)
        fg = Flamegraph(events=[event], spans=[span])
        data = fg.to_data()
        # Event should be nested under span
        self.assertEqual(data["nodeCount"], 2)
        event_nodes = [n for n in data["nodes"] if n["type"] == "llm_call"]
        self.assertTrue(event_nodes[0]["depth"] > 0)


class TestFlamegraphFromSession(unittest.TestCase):
    def test_from_session(self):
        session = Session(agent_name="test-agent")
        session.add_event(_make_event(duration_ms=200))
        session.add_event(_make_event(offset_ms=200, duration_ms=100, event_type="tool_call"))

        fg = Flamegraph.from_session(session)
        self.assertEqual(fg.session_name, "test-agent")
        data = fg.to_data()
        self.assertEqual(data["session"], "test-agent")
        self.assertEqual(data["nodeCount"], 2)


class TestFlamegraphOutput(unittest.TestCase):
    def test_render_html_structure(self):
        events = [_make_event(duration_ms=100)]
        fg = Flamegraph(events, session_name="My Session")
        html = fg.render_html()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("const DATA =", html)
        self.assertIn("Flamegraph", html)
        self.assertIn("canvas", html)

    def test_data_is_valid_json(self):
        events = [_make_event(duration_ms=100)]
        fg = Flamegraph(events)
        html = fg.render_html()
        start = html.index("const DATA = ") + len("const DATA = ")
        end = html.index(";", start)
        data = json.loads(html[start:end])
        self.assertIn("session", data)
        self.assertIn("nodes", data)
        self.assertIn("totalMs", data)

    def test_save_to_file(self):
        events = [_make_event(duration_ms=100)]
        fg = Flamegraph(events)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.html")
            fg.save(path)
            self.assertTrue(os.path.exists(path))
            content = open(path, encoding="utf-8").read()
            self.assertIn("<!DOCTYPE html>", content)

    def test_get_stats(self):
        events = [
            _make_event(event_type="llm_call", duration_ms=200, tokens_in=500, tokens_out=100),
            _make_event(event_type="tool_call", offset_ms=200, duration_ms=50),
        ]
        fg = Flamegraph(events)
        stats = fg.get_stats()
        self.assertIn("total_ms", stats)
        self.assertIn("node_count", stats)
        self.assertEqual(stats["node_count"], 2)
        self.assertIn("time_by_type", stats)
        self.assertIn("llm_call", stats["time_by_type"])
        self.assertEqual(stats["total_tokens"], 600)
        self.assertEqual(len(stats["slowest_events"]), 2)


class TestFlamegraphHtmlConvenience(unittest.TestCase):
    def test_flamegraph_html_function(self):
        events = [_make_event(duration_ms=100)]
        html = flamegraph_html(events, session_name="Quick Test")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("const DATA =", html)


class TestFlamegraphMultipleEvents(unittest.TestCase):
    def test_realistic_session(self):
        """Simulate a realistic agent session with mixed event types."""
        events = [
            _make_event(event_type="llm_call", offset_ms=0, duration_ms=800,
                        model="gpt-4o", tokens_in=2000, tokens_out=500),
            _make_event(event_type="tool_call", offset_ms=850, duration_ms=200,
                        tool_call=ToolCall(tool_name="web_search")),
            _make_event(event_type="llm_call", offset_ms=1100, duration_ms=600,
                        model="gpt-4o", tokens_in=3000, tokens_out=800),
            _make_event(event_type="tool_call", offset_ms=1750, duration_ms=100,
                        tool_call=ToolCall(tool_name="calculator")),
            _make_event(event_type="decision", offset_ms=1900, duration_ms=50),
        ]
        fg = Flamegraph(events, session_name="RealisticAgent")
        data = fg.to_data()
        self.assertEqual(data["nodeCount"], 5)
        self.assertEqual(data["session"], "RealisticAgent")
        self.assertGreater(data["totalMs"], 1900)

        stats = fg.get_stats()
        self.assertEqual(stats["total_tokens"], 6300)
        self.assertIn("llm_call", stats["time_by_type"])
        self.assertIn("tool_call", stats["time_by_type"])

        html = fg.render_html()
        self.assertIn("web_search", html)
        self.assertIn("calculator", html)


if __name__ == "__main__":
    unittest.main()
