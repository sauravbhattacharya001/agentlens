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
``event_type=error``     span status ``ERROR`` + ``error.type``
``tokens_in/out``    ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``
``model``            ``gen_ai.request.model`` (response model only when captured)
``tool_call``        ``gen_ai.tool.name`` / ``gen_ai.tool.call.id``
===================  ===========================================

Conventions target the GenAI semconv as of v1.37.0 (the conventions now live in
https://github.com/open-telemetry/semantic-conventions-genai). Note the whole
``gen_ai.*`` namespace is still **Development** stability, not Stable; the chat
attributes are mature, the agent/tool-orchestration ones are still settling.

Provider identity uses ``gen_ai.provider.name`` (the v1.37.0 rename of the
now-deprecated ``gen_ai.system``). For interop with backends still on the old
name during migration, pass ``emit_legacy_system=True`` to also emit
``gen_ai.system``.

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

# ── GenAI semantic-convention attribute keys ─────────────────────────────
# Registry: https://github.com/open-telemetry/semantic-conventions-genai
# NOTE: every gen_ai.* attribute is Development stability, not Stable.
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"  # v1.37.0 rename of gen_ai.system
GEN_AI_SYSTEM = "gen_ai.system"  # deprecated; emitted only for legacy interop
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
ERROR_TYPE = "error.type"  # conditionally required on error (general semconv)

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


def _provider_attrs(provider: str | None, emit_legacy_system: bool) -> dict[str, Any]:
    """Provider identity: current ``gen_ai.provider.name`` (+ optional legacy)."""
    if not provider:
        return {}
    attrs = {GEN_AI_PROVIDER_NAME: provider}
    if emit_legacy_system:
        attrs[GEN_AI_SYSTEM] = provider
    return attrs


def _response_model(event: "AgentEvent") -> str | None:
    """Return the *served* response model if the event actually captured one.

    We never fabricate it from the request model — equating them would mask
    silent model substitution (e.g. ``gpt-4o`` resolving to a dated snapshot),
    which is exactly the signal worth preserving. Only emit if the SDK recorded
    a distinct response model under ``output_data``.
    """
    out = getattr(event, "output_data", None)
    if isinstance(out, dict):
        rm = out.get("response_model") or out.get("model")
        if isinstance(rm, str) and rm:
            return rm
    return None


def _error_type(event: "AgentEvent") -> str:
    """Best-effort ``error.type`` for an error event.

    Prefers an explicit type recorded in ``output_data``/``input_data``; falls
    back to the spec's catch-all ``"_OTHER"`` when none was captured.
    """
    for container in (getattr(event, "output_data", None), getattr(event, "input_data", None)):
        if isinstance(container, dict):
            et = container.get("error_type") or container.get("error") or container.get("type")
            if isinstance(et, str) and et:
                return et
    return "_OTHER"


# ── Attribute mappers (pure functions, importable without opentelemetry) ──


def event_attributes(
    event: "AgentEvent",
    provider: str | None = None,
    *,
    emit_legacy_system: bool = False,
) -> dict[str, Any]:
    """Map an :class:`AgentEvent` to GenAI semconv span attributes."""
    operation = _EVENT_OPERATION.get(event.event_type)
    attrs: dict[str, Any] = {
        GEN_AI_OPERATION_NAME: operation,
        GEN_AI_CONVERSATION_ID: event.session_id or None,
    }
    attrs.update(_provider_attrs(provider, emit_legacy_system))
    if event.model:
        attrs[GEN_AI_REQUEST_MODEL] = event.model
    # Response model only when the provider actually served a distinct one —
    # never mirrored from the request (see _response_model).
    resp_model = _response_model(event)
    if resp_model is not None:
        attrs[GEN_AI_RESPONSE_MODEL] = resp_model
    # Token usage lives on the child chat spans (the operations that consume
    # tokens). It is deliberately NOT duplicated onto the root invoke_agent
    # span, so trace-wide sums don't double-count.
    if event.tokens_in:
        attrs[GEN_AI_USAGE_INPUT_TOKENS] = event.tokens_in
    if event.tokens_out:
        attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = event.tokens_out
    if event.event_type == "error":
        attrs[ERROR_TYPE] = _error_type(event)
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


def session_attributes(
    session: "Session",
    provider: str | None = None,
    *,
    emit_legacy_system: bool = False,
) -> dict[str, Any]:
    """Map a :class:`Session` to GenAI semconv agent-invocation attributes.

    Token usage is intentionally omitted here: it belongs on the child ``chat``
    spans that actually consumed the tokens. Placing it on the root as well
    would make any backend that sums ``gen_ai.usage.*`` across a trace report
    roughly double.
    """
    attrs: dict[str, Any] = {
        GEN_AI_OPERATION_NAME: OP_INVOKE_AGENT,
        GEN_AI_AGENT_NAME: session.agent_name,
        GEN_AI_CONVERSATION_ID: session.session_id,
    }
    attrs.update(_provider_attrs(provider, emit_legacy_system))
    return _clean(attrs)


class OTelExporter:
    """Emit an AgentLens :class:`Session` as OpenTelemetry GenAI spans.

    The session becomes a root ``invoke_agent`` span; every event becomes a
    child span using the GenAI ``chat`` / ``execute_tool`` conventions.
    Spans are emitted through the OpenTelemetry SDK, so wherever your global
    ``TracerProvider`` points (OTLP/Jaeger/Tempo/console) is where they land.

    Args:
        provider: Value for ``gen_ai.provider.name`` (e.g. ``"openai"``,
            ``"anthropic"``). Superseded kwarg name ``system`` is still accepted
            for backward compatibility.
        emit_legacy_system: Also emit the deprecated ``gen_ai.system`` attribute
            for backends that have not migrated to ``gen_ai.provider.name``.
        tracer_name: Instrumentation scope name for the tracer.

    Example::

        from agentlens.otel import OTelExporter
        OTelExporter(provider="openai").export(session)
    """

    def __init__(
        self,
        provider: str | None = None,
        *,
        emit_legacy_system: bool = False,
        tracer_name: str = "agentlens",
        system: str | None = None,  # deprecated alias for provider
    ) -> None:
        self._provider = provider if provider is not None else system
        self._emit_legacy_system = emit_legacy_system
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
            attributes=session_attributes(
                session, self._provider, emit_legacy_system=self._emit_legacy_system
            ),
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
            attributes=event_attributes(
                event, self._provider, emit_legacy_system=self._emit_legacy_system
            ),
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
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_SYSTEM",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_CALL_ID",
    "ERROR_TYPE",
    "OP_INVOKE_AGENT",
    "OP_CHAT",
    "OP_EXECUTE_TOOL",
]
