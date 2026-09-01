"""OpenTelemetry GenAI semantic-convention bridge.

AgentLens historically emitted its own event schema (``event_type``,
``tokens_in`` / ``tokens_out``, ``tool_call`` …). This module lets AgentLens
speak the **OpenTelemetry GenAI semantic conventions** instead, so sessions
can be shipped to any OTLP-compatible backend (Jaeger, Tempo, Honeycomb,
Grafana, Arize, Langfuse, …) and correlate with the rest of your traces.

It maps AgentLens primitives onto OTel spans:

===================  ===========================================
AgentLens            OpenTelemetry GenAI semconv
===================  ===========================================
``Session``          root span, ``gen_ai.operation.name=invoke_agent``
``Span``             internal span (grouping)
``event_type=llm_call``  ``gen_ai.operation.name=chat``, span name ``chat {model}``
``event_type=tool_call`` ``gen_ai.operation.name=execute_tool``, span name ``execute_tool {name}``
``event_type=error``     span status ``ERROR``
``tokens_in/out``    ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``
``model``            ``gen_ai.request.model`` / ``gen_ai.response.model``
``tool_call``        ``gen_ai.tool.name`` / ``gen_ai.tool.call.id``
===================  ===========================================

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/

The ``opentelemetry-sdk`` / ``opentelemetry-api`` packages are an *optional*
dependency (``pip install "agentlens[otel]"``). Importing this module without
them raises a clear, actionable error rather than an opaque ``ModuleNotFound``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentlens.models import AgentEvent, Session, ToolCall
    from agentlens.span import Span

# ── GenAI semantic-convention attribute keys (stable subset) ─────────────
# https://opentelemetry.io/docs/specs/semconv/attributes-registry/gen-ai/
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

# Operation-name enum values.
OP_INVOKE_AGENT = "invoke_agent"
OP_CHAT = "chat"
OP_EXECUTE_TOOL = "execute_tool"

# Map AgentLens event_type -> GenAI operation name / span-name prefix.
_EVENT_OPERATION = {
    "llm_call": OP_CHAT,
    "tool_call": OP_EXECUTE_TOOL,
}

_MISSING_OTEL_MSG = (
    "OpenTelemetry export requires the optional 'otel' extra. Install it with:\n"
    '    pip install "agentlens[otel]"\n'
    "(this pulls in opentelemetry-api, opentelemetry-sdk and the OTLP exporter)."
)


def _require_otel():  # noqa: ANN202 - returns the otel trace module
    """Import ``opentelemetry.trace`` or raise a helpful error."""
    try:
        from opentelemetry import trace  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_MISSING_OTEL_MSG) from exc
    return trace


def _epoch_ns(dt: datetime | None) -> int | None:
    """Convert a datetime to an integer nanosecond epoch, OTel's timestamp unit."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values — OTel attributes must not be null."""
    return {k: v for k, v in attrs.items() if v is not None}


# ── Attribute mappers (pure functions, importable without opentelemetry) ──


def event_attributes(event: "AgentEvent", system: str | None = None) -> dict[str, Any]:
    """Map an :class:`AgentEvent` to GenAI semconv span attributes."""
    operation = _EVENT_OPERATION.get(event.event_type)
    attrs: dict[str, Any] = {
        GEN_AI_OPERATION_NAME: operation,
        GEN_AI_SYSTEM: system,
        GEN_AI_CONVERSATION_ID: event.session_id or None,
    }
    if event.model:
        attrs[GEN_AI_REQUEST_MODEL] = event.model
        attrs[GEN_AI_RESPONSE_MODEL] = event.model
    # Token usage — only emit when non-zero to avoid noisy 0-valued attributes.
    if event.tokens_in:
        attrs[GEN_AI_USAGE_INPUT_TOKENS] = event.tokens_in
    if event.tokens_out:
        attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = event.tokens_out
    if event.tool_call is not None:
        attrs.update(tool_attributes(event.tool_call))
    return _clean(attrs)


def tool_attributes(tool: "ToolCall") -> dict[str, Any]:
    """Map a :class:`ToolCall` to GenAI semconv tool attributes."""
    return _clean(
        {
            GEN_AI_OPERATION_NAME: OP_EXECUTE_TOOL,
            GEN_AI_TOOL_NAME: tool.tool_name,
            GEN_AI_TOOL_CALL_ID: tool.tool_call_id,
        }
    )


def span_name_for_event(event: "AgentEvent") -> str:
    """Return the OTel span name following GenAI naming: ``{operation} {target}``.

    e.g. ``chat gpt-4o`` or ``execute_tool search``. Falls back to the raw
    ``event_type`` for non-GenAI events.
    """
    operation = _EVENT_OPERATION.get(event.event_type)
    if operation == OP_CHAT and event.model:
        return f"{OP_CHAT} {event.model}"
    if operation == OP_EXECUTE_TOOL and event.tool_call is not None:
        return f"{OP_EXECUTE_TOOL} {event.tool_call.tool_name}"
    if operation:
        return operation
    return event.event_type


def session_attributes(session: "Session", system: str | None = None) -> dict[str, Any]:
    """Map a :class:`Session` to GenAI semconv agent-invocation attributes."""
    return _clean(
        {
            GEN_AI_OPERATION_NAME: OP_INVOKE_AGENT,
            GEN_AI_SYSTEM: system,
            GEN_AI_AGENT_NAME: session.agent_name,
            GEN_AI_CONVERSATION_ID: session.session_id,
            GEN_AI_USAGE_INPUT_TOKENS: session.total_tokens_in or None,
            GEN_AI_USAGE_OUTPUT_TOKENS: session.total_tokens_out or None,
        }
    )


class OTelExporter:
    """Emit an AgentLens :class:`Session` as OpenTelemetry GenAI spans.

    The session becomes a root ``invoke_agent`` span; every event becomes a
    child span using the GenAI ``chat`` / ``execute_tool`` conventions.
    Spans are emitted through the OpenTelemetry SDK, so wherever your global
    ``TracerProvider`` points (OTLP/Jaeger/Tempo/console) is where they land.

    Args:
        system: Value for ``gen_ai.system`` (e.g. ``"openai"``, ``"anthropic"``).
        tracer_name: Instrumentation scope name for the tracer.

    Example::

        from agentlens.otel import OTelExporter
        OTelExporter(system="openai").export(session)
    """

    def __init__(self, system: str | None = None, tracer_name: str = "agentlens") -> None:
        self._system = system
        trace = _require_otel()
        self._trace = trace
        self._tracer = trace.get_tracer(tracer_name)

    def export(self, session: "Session") -> None:
        """Export a whole session as a tree of GenAI spans."""
        trace = self._trace
        start_ns = _epoch_ns(session.started_at)
        root = self._tracer.start_span(
            name=f"{OP_INVOKE_AGENT} {session.agent_name}",
            start_time=start_ns,
            attributes=session_attributes(session, self._system),
        )
        try:
            ctx = trace.set_span_in_context(root)
            for event in session.events:
                self._export_event(event, ctx)
            if session.status == "error":
                root.set_status(trace.Status(trace.StatusCode.ERROR))
        finally:
            root.end(end_time=_epoch_ns(session.ended_at))

    def _export_event(self, event: "AgentEvent", ctx: Any) -> None:
        trace = self._trace
        span = self._tracer.start_span(
            name=span_name_for_event(event),
            context=ctx,
            start_time=_epoch_ns(event.timestamp),
            attributes=event_attributes(event, self._system),
        )
        try:
            if event.event_type == "error":
                span.set_status(trace.Status(trace.StatusCode.ERROR))
        finally:
            end = event.timestamp
            if event.duration_ms is not None:
                from datetime import timedelta

                end = event.timestamp + timedelta(milliseconds=event.duration_ms)
            span.end(end_time=_epoch_ns(end))


__all__ = [
    "OTelExporter",
    "event_attributes",
    "tool_attributes",
    "session_attributes",
    "span_name_for_event",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_SYSTEM",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_CALL_ID",
    "OP_INVOKE_AGENT",
    "OP_CHAT",
    "OP_EXECUTE_TOOL",
]
