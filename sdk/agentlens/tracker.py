"""Core tracker that manages sessions and events."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any
from collections.abc import Generator

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.transport import Transport
from agentlens.health import HealthScorer, HealthReport, HealthThresholds
from agentlens.timeline import TimelineRenderer
from agentlens.span import Span
from agentlens.tracker_alerts import AlertMixin
from agentlens.tracker_tags import TagMixin
from agentlens.tracker_annotations import AnnotationMixin
from agentlens.tracker_retention import RetentionMixin
from agentlens.tracker_queries import QueryMixin

class AgentTracker(
    AlertMixin,
    TagMixin,
    AnnotationMixin,
    RetentionMixin,
    QueryMixin,
):
    """Central tracker for agent observability."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.sessions: dict[str, Session] = {}
        self._current_session_id: str | None = None
        self._active_spans: list[Span] = []

    def __repr__(self) -> str:
        return (
            f"AgentTracker(endpoint={self.transport.endpoint!r}, "
            f"sessions={len(self.sessions)}, "
            f"current={self._current_session_id!r})"
        )

    def _emit(self, event_type: str, **fields: Any) -> None:
        """Send a single event dict to the transport."""
        payload = {"event_type": event_type, **fields}
        self.transport.send_events([payload])

    @property
    def current_session(self) -> Session | None:
        if self._current_session_id and self._current_session_id in self.sessions:
            return self.sessions[self._current_session_id]
        return None

    @property
    def current_span(self) -> Span | None:
        """Return the innermost active span, or None."""
        return self._active_spans[-1] if self._active_spans else None

    def _resolve_session(
        self,
        session_id: str | None,
        error_msg: str = "No session specified. Specify session_id or start a session first.",
        *,
        require_local: bool = False,
    ) -> str:
        """Resolve a session ID, falling back to the current session.

        Args:
            session_id: Explicit session ID, or None to use current.
            error_msg: Error message if no session can be resolved.
            require_local: If True, also verify the session exists in
                ``self.sessions``. Methods that need to access session
                data locally (e.g. ``health_score``, ``timeline``,
                ``explain``) should set this to True.

        Returns:
            The resolved session ID.

        Raises:
            RuntimeError: If no session can be resolved or (when
                *require_local* is True) the session is not tracked locally.
        """
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(error_msg)
        if require_local and sid not in self.sessions:
            raise RuntimeError(
                f"Session '{sid}' not found locally. "
                "It may have been ended or was never started by this tracker."
            )
        return sid

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Create a span that groups events into a logical unit.

        Spans record start/end times, duration, nested children, and
        custom attributes.  They emit ``span_start`` and ``span_end``
        events to the backend for timeline visualization.

        Spans can be nested — inner spans automatically become children
        of the outer span.

        Args:
            name: Human-readable name for this span (e.g. ``"planning"``).
            attributes: Optional initial attributes dict.

        Yields:
            A :class:`Span` instance.  Use :meth:`Span.set_attribute` to
            attach metadata during execution.

        Example::

            with tracker.span("research") as s:
                tracker.track(event_type="llm_call", model="gpt-4o", ...)
                with tracker.span("web-search"):
                    tracker.track_tool("search", tool_input={"q": "test"})
                s.set_attribute("sources_found", 3)
        """
        sid = self._current_session_id or ""
        parent = self.current_span

        sp = Span(
            name=name,
            session_id=sid,
            parent_id=parent.span_id if parent else None,
            attributes=attributes or {},
            _mono_start=time.monotonic(),
        )

        # Register as child of parent span
        if parent:
            parent.children.append(sp.span_id)

        # Emit span_start event
        self._emit(
            "span_start",
            session_id=sid,
            span_id=sp.span_id,
            span_name=name,
            parent_span_id=sp.parent_id,
            timestamp=sp.started_at.isoformat(),
            attributes=sp.attributes,
        )

        self._active_spans.append(sp)
        try:
            yield sp
            if sp.status == "active":
                sp.status = "completed"
        except Exception as exc:
            sp.status = "error"
            sp.error = str(exc)
            raise
        finally:
            from agentlens.span import _utcnow
            sp.ended_at = _utcnow()
            sp.duration_ms = (time.monotonic() - sp._mono_start) * 1000
            self._active_spans.pop()

            # Emit span_end event
            self._emit(
                "span_end",
                session_id=sid,
                span_id=sp.span_id,
                span_name=name,
                parent_span_id=sp.parent_id,
                timestamp=sp.ended_at.isoformat(),
                duration_ms=round(sp.duration_ms, 2),
                status=sp.status,
                error=sp.error,
                event_count=sp.event_count,
                children=sp.children,
                attributes=sp.attributes,
            )

    def start_session(self, agent_name: str = "default-agent", metadata: dict | None = None) -> Session:
        """Create and register a new tracking session."""
        session = Session(agent_name=agent_name, metadata=metadata or {})
        self.sessions[session.session_id] = session
        self._current_session_id = session.session_id

        # Send session start event
        self._emit(
            "session_start",
            session_id=session.session_id,
            agent_name=agent_name,
            metadata=metadata or {},
            timestamp=session.started_at.isoformat(),
        )

        return session

    def end_session(self, session_id: str | None = None) -> None:
        """End a session and flush all pending events."""
        sid = session_id or self._current_session_id
        if sid and sid in self.sessions:
            session = self.sessions[sid]
            session.end()
            self._emit(
                "session_end",
                session_id=sid,
                ended_at=session.ended_at.isoformat() if session.ended_at else None,
                total_tokens_in=session.total_tokens_in,
                total_tokens_out=session.total_tokens_out,
                status="completed",
            )
            self.transport.flush()
            if sid == self._current_session_id:
                self._current_session_id = None

    def health_score(
        self,
        session_id: str | None = None,
        thresholds: HealthThresholds | None = None,
    ) -> HealthReport:
        """Score the health of a session.

        Args:
            session_id: Session to score. Defaults to the current session.
            thresholds: Optional custom thresholds for scoring.

        Returns:
            A :class:`HealthReport` with overall score, grade, per-metric
            breakdown, and recommendations.

        Raises:
            RuntimeError: If the session is not found.
        """
        sid = self._resolve_session(session_id, "Session not found", require_local=True)
        session = self.sessions[sid]
        scorer = HealthScorer(thresholds)
        return scorer.score_session(session)

    def timeline(
        self,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> TimelineRenderer:
        """Get a TimelineRenderer for a session.

        Constructs a :class:`TimelineRenderer` from the session's events.
        Any keyword arguments are forwarded to :meth:`TimelineRenderer.filter`.

        Args:
            session_id: Session to render. Defaults to the current session.

        Returns:
            A :class:`TimelineRenderer` instance.

        Raises:
            RuntimeError: If the session is not found.
        """
        sid = self._resolve_session(session_id, "Session not found", require_local=True)

        session = self.sessions[sid]

        # Convert events to raw dicts
        raw_events: list[dict[str, Any]] = []
        for ev in session.events:
            d = ev.to_api_dict()
            raw_events.append(d)

        session_dict = session.to_api_dict()
        renderer = TimelineRenderer(raw_events, session_dict)

        if kwargs:
            renderer = renderer.filter(**kwargs)

        return renderer

    def track(
        self,
        event_type: str = "generic",
        input_data: dict | None = None,
        output_data: dict | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        reasoning: str | None = None,
        tool_name: str | None = None,
        tool_input: dict | None = None,
        tool_output: dict | None = None,
        duration_ms: float | None = None,
    ) -> AgentEvent:
        """Track a single agent event."""
        # Build tool call if provided
        tool_call = None
        if tool_name:
            tool_call = ToolCall(
                tool_name=tool_name,
                tool_input=tool_input or {},
                tool_output=tool_output,
                duration_ms=duration_ms,
            )

        # Build decision trace if reasoning provided
        decision_trace = None
        if reasoning:
            decision_trace = DecisionTrace(
                reasoning=reasoning,
                step=len(self.current_session.events) + 1 if self.current_session else 0,
            )

        event = AgentEvent(
            session_id=self._current_session_id or "",
            event_type=event_type,
            input_data=input_data,
            output_data=output_data,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tool_call=tool_call,
            decision_trace=decision_trace,
            duration_ms=duration_ms,
        )

        # Add to current session
        if self.current_session:
            self.current_session.add_event(event)

        # Increment event count on active span(s)
        for sp in self._active_spans:
            sp.event_count += 1

        # Send to backend — use send_event() directly to skip the
        # single-element list allocation and unpacking in send_events().
        api_dict = event.to_api_dict()
        # Attach span context so the backend can associate events with spans
        if self._active_spans:
            api_dict["span_id"] = self._active_spans[-1].span_id
        self.transport.send_event(api_dict)

        return event

    def track_tool(
        self,
        tool_name: str,
        tool_input: dict | None = None,
        tool_output: dict | None = None,
        duration_ms: float | None = None,
    ) -> AgentEvent:
        """Convenience method to track a tool call."""
        return self.track(
            event_type="tool_call",
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            duration_ms=duration_ms,
        )

    def explain(self, session_id: str | None = None) -> str:
        """Generate a human-readable explanation of the agent's behavior."""
        try:
            sid = self._resolve_session(session_id, require_local=True)
        except RuntimeError:
            if session_id:
                return f"Session {session_id} not found."
            return "No active session."

        session = self.sessions[sid]

        lines = [
            f"## Session Explanation: {session.agent_name}",
            f"**Session ID:** {session.session_id}",
            f"**Started:** {session.started_at.isoformat()}",
            f"**Status:** {session.status}",
            f"**Total tokens:** {session.total_tokens_in} in / {session.total_tokens_out} out",
            "",
            "### Event Timeline:",
        ]

        for i, event in enumerate(session.events, 1):
            ts = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
            line = f"{i}. [{ts}] **{event.event_type}**"
            if event.model:
                line += f" (model: {event.model})"
            if event.tool_call:
                line += f" → tool: {event.tool_call.tool_name}"
            if event.decision_trace and event.decision_trace.reasoning:
                line += f"\n   💡 Reasoning: {event.decision_trace.reasoning}"
            if event.tokens_in or event.tokens_out:
                line += f"\n   📊 Tokens: {event.tokens_in} in / {event.tokens_out} out"
            lines.append(line)

        return "\n".join(lines)
