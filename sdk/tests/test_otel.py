"""Tests for the OpenTelemetry GenAI-semconv bridge (agentlens.otel)."""

from __future__ import annotations

import pytest

from agentlens.models import AgentEvent, Session, ToolCall
from agentlens import otel


# ── Pure attribute mappers (no opentelemetry required) ───────────────────


def test_llm_event_attributes_use_genai_semconv():
    ev = AgentEvent(
        session_id="sess1",
        event_type="llm_call",
        model="gpt-4o",
        tokens_in=100,
        tokens_out=50,
    )
    attrs = otel.event_attributes(ev, system="openai")
    assert attrs[otel.GEN_AI_OPERATION_NAME] == otel.OP_CHAT
    assert attrs[otel.GEN_AI_SYSTEM] == "openai"
    assert attrs[otel.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert attrs[otel.GEN_AI_RESPONSE_MODEL] == "gpt-4o"
    assert attrs[otel.GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert attrs[otel.GEN_AI_USAGE_OUTPUT_TOKENS] == 50
    assert attrs[otel.GEN_AI_CONVERSATION_ID] == "sess1"


def test_zero_tokens_are_omitted():
    ev = AgentEvent(event_type="llm_call", model="m", tokens_in=0, tokens_out=0)
    attrs = otel.event_attributes(ev)
    assert otel.GEN_AI_USAGE_INPUT_TOKENS not in attrs
    assert otel.GEN_AI_USAGE_OUTPUT_TOKENS not in attrs


def test_tool_event_attributes():
    tc = ToolCall(tool_name="search", tool_call_id="call-123")
    ev = AgentEvent(event_type="tool_call", tool_call=tc)
    attrs = otel.event_attributes(ev)
    assert attrs[otel.GEN_AI_OPERATION_NAME] == otel.OP_EXECUTE_TOOL
    assert attrs[otel.GEN_AI_TOOL_NAME] == "search"
    assert attrs[otel.GEN_AI_TOOL_CALL_ID] == "call-123"


def test_span_names_follow_genai_convention():
    llm = AgentEvent(event_type="llm_call", model="gpt-4o")
    tool = AgentEvent(event_type="tool_call", tool_call=ToolCall(tool_name="search"))
    generic = AgentEvent(event_type="custom_thing")
    assert otel.span_name_for_event(llm) == "chat gpt-4o"
    assert otel.span_name_for_event(tool) == "execute_tool search"
    assert otel.span_name_for_event(generic) == "custom_thing"


def test_session_attributes():
    s = Session(agent_name="planner", total_tokens_in=10, total_tokens_out=5)
    attrs = otel.session_attributes(s, system="anthropic")
    assert attrs[otel.GEN_AI_OPERATION_NAME] == otel.OP_INVOKE_AGENT
    assert attrs[otel.GEN_AI_AGENT_NAME] == "planner"
    assert attrs[otel.GEN_AI_SYSTEM] == "anthropic"
    assert attrs[otel.GEN_AI_USAGE_INPUT_TOKENS] == 10


def test_none_attributes_are_stripped():
    ev = AgentEvent(event_type="generic")  # no model, no tokens
    attrs = otel.event_attributes(ev)
    assert None not in attrs.values()


# ── Exporter (requires opentelemetry-sdk) ────────────────────────────────

otel_sdk = pytest.importorskip("opentelemetry.sdk.trace")


@pytest.fixture()
def span_capture(monkeypatch):
    """Install an in-memory tracer provider and return the captured spans."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Bypass the "provider already set" one-shot guard so each test is isolated.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return exporter


def _build_session() -> Session:
    s = Session(agent_name="researcher")
    s.add_event(AgentEvent(event_type="llm_call", model="gpt-4o", tokens_in=120, tokens_out=40))
    s.add_event(AgentEvent(event_type="tool_call", tool_call=ToolCall(tool_name="web_search", tool_call_id="c1")))
    s.add_event(AgentEvent(event_type="error"))
    s.end()
    return s


def test_exporter_emits_root_and_child_spans(span_capture):
    otel.OTelExporter(system="openai").export(_build_session())
    spans = span_capture.get_finished_spans()
    names = sorted(sp.name for sp in spans)
    assert "invoke_agent researcher" in names
    assert "chat gpt-4o" in names
    assert "execute_tool web_search" in names
    # 1 root + 3 events
    assert len(spans) == 4


def test_exporter_sets_genai_attributes(span_capture):
    otel.OTelExporter(system="openai").export(_build_session())
    spans = {sp.name: sp for sp in span_capture.get_finished_spans()}
    chat = spans["chat gpt-4o"]
    assert chat.attributes[otel.GEN_AI_OPERATION_NAME] == "chat"
    assert chat.attributes[otel.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert chat.attributes[otel.GEN_AI_USAGE_INPUT_TOKENS] == 120


def test_error_event_marks_span_error(span_capture):
    from opentelemetry.trace import StatusCode

    otel.OTelExporter().export(_build_session())
    spans = {sp.name: sp for sp in span_capture.get_finished_spans()}
    assert spans["error"].status.status_code == StatusCode.ERROR


def test_child_spans_share_root_trace(span_capture):
    otel.OTelExporter().export(_build_session())
    spans = span_capture.get_finished_spans()
    trace_ids = {sp.context.trace_id for sp in spans}
    assert len(trace_ids) == 1  # everything under one trace


def test_missing_otel_raises_helpful_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("no otel")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"agentlens\[otel\]"):
        otel.OTelExporter()
