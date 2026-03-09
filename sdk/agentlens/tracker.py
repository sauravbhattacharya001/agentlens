"""Core tracker that manages sessions and events."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.transport import Transport
from agentlens.health import HealthScorer, HealthReport, HealthThresholds
from agentlens.timeline import TimelineRenderer
from agentlens.span import Span
from agentlens.tracker_alerts import AlertMixin
from agentlens.tracker_tags import TagMixin
from agentlens.tracker_annotations import AnnotationMixin
from agentlens.tracker_retention import RetentionMixin


class AgentTracker(AlertMixin, TagMixin, AnnotationMixin, RetentionMixin):
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
        self.transport.send_events([{
            "event_type": "span_start",
            "session_id": sid,
            "span_id": sp.span_id,
            "span_name": name,
            "parent_span_id": sp.parent_id,
            "timestamp": sp.started_at.isoformat(),
            "attributes": sp.attributes,
        }])

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
            self.transport.send_events([{
                "event_type": "span_end",
                "session_id": sid,
                "span_id": sp.span_id,
                "span_name": name,
                "parent_span_id": sp.parent_id,
                "timestamp": sp.ended_at.isoformat(),
                "duration_ms": round(sp.duration_ms, 2),
                "status": sp.status,
                "error": sp.error,
                "event_count": sp.event_count,
                "children": sp.children,
                "attributes": sp.attributes,
            }])

    def start_session(self, agent_name: str = "default-agent", metadata: dict | None = None) -> Session:
        """Create and register a new tracking session."""
        session = Session(agent_name=agent_name, metadata=metadata or {})
        self.sessions[session.session_id] = session
        self._current_session_id = session.session_id

        # Send session start event
        self.transport.send_events([{
            "event_type": "session_start",
            "session_id": session.session_id,
            "agent_name": agent_name,
            "metadata": metadata or {},
            "timestamp": session.started_at.isoformat(),
        }])

        return session

    def end_session(self, session_id: str | None = None) -> None:
        """End a session and flush all pending events."""
        sid = session_id or self._current_session_id
        if sid and sid in self.sessions:
            session = self.sessions[sid]
            session.end()
            self.transport.send_events([{
                "event_type": "session_end",
                "session_id": sid,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "total_tokens_in": session.total_tokens_in,
                "total_tokens_out": session.total_tokens_out,
                "status": "completed",
            }])
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

        # Send to backend
        api_dict = event.to_api_dict()
        # Attach span context so the backend can associate events with spans
        if self._active_spans:
            api_dict["span_id"] = self._active_spans[-1].span_id
        self.transport.send_events([api_dict])

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

    def compare_sessions(
        self,
        session_a: str,
        session_b: str,
    ) -> dict[str, Any]:
        """Compare two sessions side-by-side.

        Fetches comparison metrics from the AgentLens backend including
        token usage, event counts, tool usage, timing, and deltas between
        the two sessions.

        Args:
            session_a: First session ID to compare.
            session_b: Second session ID to compare.

        Returns:
            A dict containing ``session_a`` metrics, ``session_b`` metrics,
            ``deltas`` (percentage and absolute differences), and ``shared``
            (common event types, tools, and models).

        Raises:
            ValueError: If either session ID is empty or they are the same.
            httpx.HTTPStatusError: If the backend returns an error.
        """
        if not session_a or not session_b:
            raise ValueError("Both session_a and session_b are required.")
        if session_a == session_b:
            raise ValueError("Cannot compare a session with itself.")

        return self.transport.post(
            "/sessions/compare",
            json={"session_a": session_a, "session_b": session_b},
        ).json()

    def export_session(
        self,
        session_id: str | None = None,
        format: str = "json",
    ) -> dict[str, Any] | str:
        """Export session data from the backend.

        Fetches the full session data (including all events) from the
        AgentLens backend and returns it in the requested format.

        Args:
            session_id: Session to export. Defaults to the current session.
            format: Export format — ``"json"`` returns a dict, ``"csv"``
                returns a CSV string.

        Returns:
            A dict (for JSON) or a string (for CSV) containing the full
            session data with events, token usage, and summary statistics.

        Raises:
            RuntimeError: If no session is specified and there is no current
                session.
            ValueError: If the format is not ``"json"`` or ``"csv"``.
            httpx.HTTPStatusError: If the backend returns an error.
        """
        sid = self._resolve_session(
            session_id,
            "No session to export. Specify session_id or start a session first.",
        )

        if format not in ("json", "csv"):
            raise ValueError(f"Invalid format '{format}'. Use 'json' or 'csv'.")

        response = self.transport.get(
            f"/sessions/{sid}/export",
            params={"format": format},
        )

        if format == "json":
            return response.json()
        return response.text

    def explain(self, session_id: str | None = None) -> str:
        """Generate a human-readable explanation of the agent's behavior."""
        try:
            sid = self._resolve_session(session_id)
        except RuntimeError:
            if session_id:
                return f"Session {session_id} not found."
            return "No active session."

        session = self.sessions.get(sid)
        if not session:
            return f"Session {sid} not found."

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

    def get_costs(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Get cost breakdown for a session.

        Fetches cost data from the AgentLens backend, calculated using
        the configured model pricing (per 1M tokens).

        Args:
            session_id: Session to get costs for. Defaults to the current session.

        Returns:
            A dict containing ``total_cost``, ``total_input_cost``,
            ``total_output_cost``, ``model_costs`` (per-model breakdown),
            ``event_costs`` (per-event costs), ``currency``, and
            ``unmatched_models`` (models without pricing).

        Raises:
            RuntimeError: If no session is specified and there is no current
                session.
            httpx.HTTPStatusError: If the backend returns an error.
        """
        sid = self._resolve_session(
            session_id,
            "No session to get costs for. Specify session_id or start a session first.",
        )

        return self.transport.get(f"/pricing/costs/{sid}").json()

    def get_pricing(self) -> dict[str, Any]:
        """Get the current model pricing configuration.

        Returns:
            A dict with ``pricing`` (current model prices) and ``defaults``
            (built-in default prices).
        """
        return self.transport.get("/pricing").json()

    def set_pricing(self, pricing: dict[str, dict[str, float]]) -> dict[str, Any]:
        """Update model pricing configuration.

        Args:
            pricing: A dict mapping model names to pricing dicts with
                ``input_cost_per_1m`` and ``output_cost_per_1m`` keys.

        Returns:
            A dict with ``status`` and ``updated`` count.
        """
        return self.transport.put(
            "/pricing", json={"pricing": pricing},
        ).json()

    def search_events(
        self,
        session_id: str | None = None,
        *,
        q: str | None = None,
        event_type: str | None = None,
        model: str | None = None,
        min_tokens: int | None = None,
        max_tokens: int | None = None,
        min_duration_ms: float | None = None,
        has_tools: bool = False,
        has_reasoning: bool = False,
        errors: bool = False,
        after: str | None = None,
        before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter events within a session.

        Provides full-text search across event data (input, output, tool
        calls, reasoning) and filtering by event type, model, token count,
        duration, and more.

        Args:
            session_id: Session to search in. Defaults to the current session.
            q: Full-text search query (searches input, output, tool data,
                reasoning). Multiple space-separated terms are AND-matched.
            event_type: Filter by event type(s). Comma-separated for
                multiple types (e.g., ``"llm_call,tool_call"``).
            model: Filter by model name(s). Comma-separated, case-insensitive
                substring match.
            min_tokens: Minimum total tokens (input + output) threshold.
            max_tokens: Maximum total tokens (input + output) threshold.
            min_duration_ms: Minimum event duration in milliseconds.
            has_tools: If True, only return events with tool calls.
            has_reasoning: If True, only return events with decision reasoning.
            errors: If True, only return error events.
            after: ISO timestamp — only events at or after this time.
            before: ISO timestamp — only events at or before this time.
            limit: Max events to return (default 100, max 500).
            offset: Pagination offset.

        Returns:
            A dict containing:
            - ``session_id``: The session searched.
            - ``total_events``: Total events in the session.
            - ``matched``: Number of events matching the filters.
            - ``returned``: Number of events in this page.
            - ``offset``: Current offset.
            - ``limit``: Current limit.
            - ``summary``: Aggregate stats for matched events (tokens,
              duration, event type breakdown, model breakdown).
            - ``events``: List of matched event dicts.

        Raises:
            RuntimeError: If no session is specified and there is no current
                session.
            httpx.HTTPStatusError: If the backend returns an error.
        """
        sid = self._resolve_session(
            session_id,
            "No session to search. Specify session_id or start a session first.",
        )

        params: dict[str, str | int | float] = {
            "limit": min(max(1, limit), 500),
            "offset": max(0, offset),
        }

        if q:
            params["q"] = q
        if event_type:
            params["type"] = event_type
        if model:
            params["model"] = model
        if min_tokens is not None and min_tokens > 0:
            params["min_tokens"] = min_tokens
        if max_tokens is not None and max_tokens > 0:
            params["max_tokens"] = max_tokens
        if min_duration_ms is not None and min_duration_ms > 0:
            params["min_duration_ms"] = min_duration_ms
        if has_tools:
            params["has_tools"] = "true"
        if has_reasoning:
            params["has_reasoning"] = "true"
        if errors:
            params["errors"] = "true"
        if after:
            params["after"] = after
        if before:
            params["before"] = before

        return self.transport.get(
            f"/sessions/{sid}/events/search", params=params,
        ).json()

    # -- Activity Heatmap -----------------------------------------------------

    def heatmap(
        self,
        *,
        metric: str = "events",
        days: int = 30,
    ) -> dict[str, Any]:
        """Get a day-of-week × hour-of-day activity heatmap.

        Returns a 7×24 matrix showing activity intensity across the week,
        useful for identifying peak usage patterns.

        Args:
            metric: What to measure — ``"events"`` (default), ``"tokens"``,
                or ``"sessions"``.
            days: Number of days to look back (default 30, max 365).

        Returns:
            A dict containing:
            - ``matrix``: 7×24 list of lists (Sun=0 … Sat=6, hours 0–23).
            - ``peak``: The single busiest slot (day, hour, value).
            - ``day_totals``: Per-day totals.
            - ``hour_totals``: Per-hour totals.
            - ``cells``: Non-zero cells with intensity (0–1).
            - ``max_value``: Maximum cell value for normalization.

        Example::

            hm = tracker.heatmap(metric="tokens", days=7)
            print(f"Peak: {hm['peak']['day_name']} at {hm['peak']['hour']}:00")
        """
        if metric not in ("events", "tokens", "sessions"):
            raise ValueError(f"Invalid metric '{metric}'. Use 'events', 'tokens', or 'sessions'.")

        return self.transport.get(
            "/analytics/heatmap",
            params={"metric": metric, "days": min(max(1, days), 365)},
        ).json()
