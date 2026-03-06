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


class AgentTracker:
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
    ) -> str:
        """Resolve a session ID, falling back to the current session.

        Args:
            session_id: Explicit session ID, or None to use current.
            error_msg: Error message if no session can be resolved.

        Returns:
            The resolved session ID.

        Raises:
            RuntimeError: If no session can be resolved.
        """
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(error_msg)
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
        sid = self._resolve_session(session_id, "Session not found")
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
        sid = self._resolve_session(session_id, "Session not found")

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

    # ── Alert Rules ──────────────────────────────────────────────────

    def list_alert_rules(self, enabled: bool | None = None) -> dict:
        """List all alert rules, optionally filtered by enabled status."""
        params = {}
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        return self.transport.get("/alerts/rules", params=params).json()

    def create_alert_rule(
        self,
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        window_minutes: int = 60,
        agent_filter: str | None = None,
        cooldown_minutes: int = 15,
    ) -> dict:
        """Create a new alert rule.

        Args:
            name: Human-readable rule name
            metric: Metric to monitor (total_tokens, error_rate, avg_duration_ms, etc.)
            operator: Comparison operator (<, >, <=, >=, ==, !=)
            threshold: Threshold value to compare against
            window_minutes: Time window to evaluate (default 60)
            agent_filter: Optional agent name filter
            cooldown_minutes: Min minutes between alerts for same rule (default 15)
        """
        payload = {
            "name": name,
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "window_minutes": window_minutes,
            "cooldown_minutes": cooldown_minutes,
        }
        if agent_filter:
            payload["agent_filter"] = agent_filter
        return self.transport.post("/alerts/rules", json=payload).json()

    def update_alert_rule(self, rule_id: str, **kwargs) -> dict:
        """Update an existing alert rule. Pass any field to update."""
        return self.transport.put(
            f"/alerts/rules/{rule_id}", json=kwargs,
        ).json()

    def delete_alert_rule(self, rule_id: str) -> dict:
        """Delete an alert rule."""
        return self.transport.delete(f"/alerts/rules/{rule_id}").json()

    def evaluate_alerts(self) -> dict:
        """Evaluate all enabled alert rules against current data."""
        return self.transport.post("/alerts/evaluate").json()

    def get_alert_events(
        self,
        rule_id: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 50,
    ) -> dict:
        """Get triggered alert events."""
        params: dict[str, Any] = {"limit": limit}
        if rule_id:
            params["rule_id"] = rule_id
        if acknowledged is not None:
            params["acknowledged"] = "true" if acknowledged else "false"
        return self.transport.get("/alerts/events", params=params).json()

    def acknowledge_alert(self, alert_id: str) -> dict:
        """Acknowledge a triggered alert event."""
        return self.transport.put(
            f"/alerts/events/{alert_id}/acknowledge",
        ).json()

    def get_alert_metrics(self) -> dict:
        """Get list of available metrics for alert rules."""
        return self.transport.get("/alerts/metrics").json()

    # -- Session Tags ---------------------------------------------------------

    def add_tags(
        self,
        tags: list[str],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Add tags to a session for filtering and organization.

        Tags are short labels (alphanumeric + ``_-.:/ ``, max 64 chars)
        that help organize and filter sessions. Each session can have
        up to 20 tags.

        Args:
            tags: List of tag strings to add.
            session_id: Session to tag. Defaults to the current session.

        Returns:
            A dict with ``session_id``, ``added`` count, and ``tags``
            (all current tags on the session).

        Example::

            tracker.add_tags(["production", "v2.1", "regression-test"])
        """
        sid = self._resolve_session(
            session_id,
            "No session to tag. Specify session_id or start a session first.",
        )
        if not tags or not isinstance(tags, list):
            raise ValueError("tags must be a non-empty list of strings.")

        return self.transport.post(
            f"/sessions/{sid}/tags", json={"tags": tags},
        ).json()

    def remove_tags(
        self,
        tags: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove tags from a session.

        If no tags are specified, removes all tags from the session.

        Args:
            tags: List of tag strings to remove. If None or empty,
                removes all tags.
            session_id: Session to untag. Defaults to the current session.

        Returns:
            A dict with ``session_id``, ``removed`` count, and ``tags``
            (remaining tags on the session).

        Example::

            tracker.remove_tags(["debug"])
            tracker.remove_tags()  # removes all tags
        """
        sid = self._resolve_session(
            session_id,
            "No session to untag. Specify session_id or start a session first.",
        )

        body = {"tags": tags} if tags else {}
        return self.transport.delete(
            f"/sessions/{sid}/tags", json=body,
        ).json()

    def get_tags(
        self,
        session_id: str | None = None,
    ) -> list[str]:
        """Get all tags for a session.

        Args:
            session_id: Session to query. Defaults to the current session.

        Returns:
            A list of tag strings.
        """
        sid = self._resolve_session(
            session_id,
            "No session to query. Specify session_id or start a session first.",
        )

        return self.transport.get(f"/sessions/{sid}/tags").json().get("tags", [])

    def list_all_tags(self) -> list[dict[str, Any]]:
        """List all tags across all sessions with session counts.

        Returns:
            A list of dicts with ``tag`` and ``session_count`` keys,
            ordered by session count descending.

        Example::

            tags = tracker.list_all_tags()
            # [{"tag": "production", "session_count": 42}, ...]
        """
        return self.transport.get("/sessions/tags").json().get("tags", [])

    def list_sessions_by_tag(
        self,
        tag: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List sessions that have a specific tag.

        Args:
            tag: Tag to filter by.
            limit: Max sessions to return (default 50, max 200).
            offset: Pagination offset.

        Returns:
            A dict with ``sessions``, ``total``, ``limit``, ``offset``,
            and ``tag``.

        Example::

            result = tracker.list_sessions_by_tag("production")
            for session in result["sessions"]:
                print(session["session_id"], session["tags"])
        """
        if not tag or not isinstance(tag, str):
            raise ValueError("tag must be a non-empty string.")

        return self.transport.get(
            f"/sessions/by-tag/{tag}",
            params={"limit": limit, "offset": offset},
        ).json()

    # -- Session Search ---------------------------------------------------------

    def search_sessions(
        self,
        *,
        q: str | None = None,
        agent: str | None = None,
        status: str | None = None,
        after: str | None = None,
        before: str | None = None,
        min_tokens: int | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search and filter sessions across the AgentLens backend.

        Provides full-text search across agent names and metadata, with
        filtering by status, date range, token thresholds, and tags.

        Args:
            q: Full-text search query (searches agent name and metadata).
            agent: Filter by agent name (substring match).
            status: Filter by session status (active, completed, error).
            after: ISO timestamp — only sessions started at or after this.
            before: ISO timestamp — only sessions started at or before this.
            min_tokens: Minimum total tokens threshold.
            max_tokens: Maximum total tokens threshold.
            tags: List of tags — sessions must have ALL specified tags.
            sort: Sort field (started_at, total_tokens, agent_name, status).
            order: Sort order (asc, desc). Default desc.
            limit: Max sessions to return (default 50, max 200).
            offset: Pagination offset.

        Returns:
            A dict containing ``sessions``, ``total``, ``limit``, ``offset``,
            ``sort``, ``order``, and ``filters``.

        Example::

            results = tracker.search_sessions(agent="planner", min_tokens=1000)
            for s in results["sessions"]:
                print(s["agent_name"], s["total_tokens_in"] + s["total_tokens_out"])
        """
        params: dict[str, str | int] = {
            "limit": min(max(1, limit), 200),
            "offset": max(0, offset),
            "sort": sort,
            "order": order,
        }
        if q:
            params["q"] = q
        if agent:
            params["agent"] = agent
        if status:
            params["status"] = status
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if min_tokens is not None and min_tokens > 0:
            params["min_tokens"] = min_tokens
        if max_tokens is not None and max_tokens > 0:
            params["max_tokens"] = max_tokens
        if tags:
            params["tags"] = ",".join(tags)

        return self.transport.get("/sessions/search", params=params).json()

    # -- Session Annotations --------------------------------------------------

    def annotate(
        self,
        text: str,
        *,
        session_id: str | None = None,
        author: str = "sdk",
        annotation_type: str = "note",
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Add an annotation (note, bug, insight, warning, milestone) to a session.

        Annotations are freeform text notes attached to sessions, useful
        for marking important observations, bugs, insights, or milestones
        during agent operation.

        Args:
            text: Annotation text (max 4000 characters).
            session_id: Session to annotate. Defaults to the current session.
            author: Who is creating the annotation (default ``"sdk"``).
            annotation_type: One of ``"note"``, ``"bug"``, ``"insight"``,
                ``"warning"``, ``"milestone"`` (default ``"note"``).
            event_id: Optional event ID to attach the annotation to a
                specific event in the session timeline.

        Returns:
            A dict with ``annotation_id``, ``session_id``, ``text``,
            ``author``, ``event_id``, ``type``, ``created_at``, ``updated_at``.

        Example::

            tracker.annotate("Bug: model hallucinated tool name")
            tracker.annotate("Latency spike at step 5", annotation_type="warning")
            tracker.annotate("Reached goal state", annotation_type="milestone")
        """
        sid = self._resolve_session(
            session_id,
            "No session to annotate. Specify session_id or start a session first.",
        )
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string.")

        payload: dict[str, Any] = {
            "text": text,
            "author": author,
            "type": annotation_type,
        }
        if event_id:
            payload["event_id"] = event_id

        return self.transport.post(
            f"/sessions/{sid}/annotations", json=payload,
        ).json()

    def get_annotations(
        self,
        session_id: str | None = None,
        *,
        annotation_type: str | None = None,
        author: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get annotations for a session.

        Args:
            session_id: Session to query. Defaults to the current session.
            annotation_type: Filter by type (comma-separated for multiple).
            author: Filter by author.
            limit: Max annotations to return (default 100, max 500).
            offset: Pagination offset.

        Returns:
            A dict with ``session_id``, ``total``, ``returned``,
            ``type_breakdown``, and ``annotations`` list.

        Example::

            result = tracker.get_annotations()
            for ann in result["annotations"]:
                print(f"[{ann['type']}] {ann['text']}")
        """
        sid = self._resolve_session(
            session_id,
            "No session to query. Specify session_id or start a session first.",
        )

        params: dict[str, str | int] = {
            "limit": min(max(1, limit), 500),
            "offset": max(0, offset),
        }
        if annotation_type:
            params["type"] = annotation_type
        if author:
            params["author"] = author

        return self.transport.get(
            f"/sessions/{sid}/annotations", params=params,
        ).json()

    def update_annotation(
        self,
        annotation_id: str,
        *,
        session_id: str | None = None,
        text: str | None = None,
        annotation_type: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing annotation.

        Args:
            annotation_id: ID of the annotation to update.
            session_id: Session containing the annotation. Defaults to current.
            text: New annotation text.
            annotation_type: New type.
            author: New author.

        Returns:
            Updated annotation dict.
        """
        sid = self._resolve_session(session_id)
        if not annotation_id:
            raise ValueError("annotation_id is required.")

        payload: dict[str, Any] = {}
        if text is not None:
            payload["text"] = text
        if annotation_type is not None:
            payload["type"] = annotation_type
        if author is not None:
            payload["author"] = author

        if not payload:
            raise ValueError("At least one field (text, annotation_type, author) must be provided.")

        return self.transport.put(
            f"/sessions/{sid}/annotations/{annotation_id}", json=payload,
        ).json()

    def delete_annotation(
        self,
        annotation_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete an annotation.

        Args:
            annotation_id: ID of the annotation to delete.
            session_id: Session containing the annotation. Defaults to current.

        Returns:
            A dict with ``deleted`` (bool) and ``annotation_id``.
        """
        sid = self._resolve_session(session_id)
        if not annotation_id:
            raise ValueError("annotation_id is required.")

        return self.transport.delete(
            f"/sessions/{sid}/annotations/{annotation_id}",
        ).json()

    def list_recent_annotations(
        self,
        *,
        annotation_type: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List recent annotations across all sessions.

        Args:
            annotation_type: Filter by type (comma-separated for multiple).
            limit: Max annotations to return (default 50, max 200).

        Returns:
            A dict with ``total`` and ``annotations`` list (includes
            ``agent_name`` from the session).

        Example::

            result = tracker.list_recent_annotations(annotation_type="bug")
            for ann in result["annotations"]:
                print(f"[{ann['agent_name']}] {ann['text']}")
        """
        params: dict[str, str | int] = {"limit": min(max(1, limit), 200)}
        if annotation_type:
            params["type"] = annotation_type

        return self.transport.get("/annotations", params=params).json()

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

    # -- Data Retention & Cleanup ---------------------------------------------

    def get_retention_config(self) -> dict[str, Any]:
        """Get current data retention configuration.

        Returns:
            A dict with ``config`` containing:
            - ``max_age_days``: Sessions older than this are eligible
              for purge (0 = disabled, default 90).
            - ``max_sessions``: Maximum sessions to keep (0 = unlimited).
            - ``exempt_tags``: Tags that protect sessions from purging.
            - ``auto_purge``: Whether automatic cleanup is enabled.

        Example::

            config = tracker.get_retention_config()
            print(f"Max age: {config['config']['max_age_days']} days")
        """
        return self.transport.get("/retention/config").json()

    def set_retention_config(
        self,
        *,
        max_age_days: int | None = None,
        max_sessions: int | None = None,
        exempt_tags: list[str] | None = None,
        auto_purge: bool | None = None,
    ) -> dict[str, Any]:
        """Update data retention configuration.

        Only specified fields are updated; others retain their current
        values. Changes take effect on the next purge (manual or auto).

        Args:
            max_age_days: Sessions older than this (in days) are eligible
                for purge. 0 disables age-based cleanup. Max 3650 (~10 years).
            max_sessions: Maximum sessions to keep. 0 = unlimited.
                When exceeded, oldest sessions are purged first.
            exempt_tags: List of tags that protect sessions from purging.
                Sessions with any exempt tag are never purged, regardless
                of age or count limits.
            auto_purge: Enable or disable automatic cleanup.

        Returns:
            A dict with ``config`` (updated settings) and ``updated`` count.

        Example::

            tracker.set_retention_config(max_age_days=30, exempt_tags=["production", "important"])
        """
        payload: dict[str, Any] = {}
        if max_age_days is not None:
            payload["max_age_days"] = max_age_days
        if max_sessions is not None:
            payload["max_sessions"] = max_sessions
        if exempt_tags is not None:
            payload["exempt_tags"] = exempt_tags
        if auto_purge is not None:
            payload["auto_purge"] = auto_purge

        if not payload:
            raise ValueError("At least one config field must be specified.")

        return self.transport.put("/retention/config", json=payload).json()

    def get_retention_stats(self) -> dict[str, Any]:
        """Get database statistics and retention status.

        Returns a comprehensive overview of database size, session ages,
        and how many sessions are currently eligible for purging under
        the active retention policy.

        Returns:
            A dict containing:
            - ``sessions``: Total session count.
            - ``events``: Total event count.
            - ``avg_events_per_session``: Average events per session.
            - ``oldest_session``: ISO timestamp of oldest session.
            - ``newest_session``: ISO timestamp of newest session.
            - ``age_breakdown``: Session counts by age bucket
              (last_24h, last_7d, last_30d, last_90d, older).
            - ``status_breakdown``: Session counts by status.
            - ``eligible_for_purge``: Number of sessions matching
              current retention policy for deletion.
            - ``config``: Current retention configuration.

        Example::

            stats = tracker.get_retention_stats()
            print(f"{stats['sessions']} sessions, {stats['events']} events")
            print(f"{stats['eligible_for_purge']} eligible for purge")
        """
        return self.transport.get("/retention/stats").json()

    def purge(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Manually purge sessions matching the retention policy.

        Deletes sessions (and their events, tags, annotations) that are
        eligible under the current retention configuration. Sessions
        with exempt tags are always preserved.

        Args:
            dry_run: If True, returns what *would* be purged without
                actually deleting anything. Useful for previewing the
                impact before committing.

        Returns:
            A dict containing:
            - ``dry_run``: Whether this was a dry run.
            - ``purged_sessions`` / ``would_purge_sessions``: Count.
            - ``purged_events`` / ``would_purge_events``: Count.
            - ``details``: Per-session breakdown with session ID, reason,
              and event count.
            - ``message``: Human-readable summary.

        Example::

            # Preview first
            preview = tracker.purge(dry_run=True)
            print(preview["message"])

            # Then actually purge
            result = tracker.purge()
            print(f"Purged {result['purged_sessions']} sessions")
        """
        params = {}
        if dry_run:
            params["dry_run"] = "true"

        return self.transport.post(
            "/retention/purge", params=params, json={},
        ).json()
