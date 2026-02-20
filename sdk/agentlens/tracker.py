"""Core tracker that manages sessions and events."""

from __future__ import annotations

from typing import Any

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.transport import Transport


class AgentTracker:
    """Central tracker for agent observability."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.sessions: dict[str, Session] = {}
        self._current_session_id: str | None = None

    @property
    def current_session(self) -> Session | None:
        if self._current_session_id and self._current_session_id in self.sessions:
            return self.sessions[self._current_session_id]
        return None

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

        # Send to backend
        self.transport.send_events([event.to_api_dict()])

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

        response = self.transport._client.post(
            f"{self.transport.endpoint}/sessions/compare",
            json={"session_a": session_a, "session_b": session_b},
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError("No session to export. Specify session_id or start a session first.")

        if format not in ("json", "csv"):
            raise ValueError(f"Invalid format '{format}'. Use 'json' or 'csv'.")

        response = self.transport._client.get(
            f"{self.transport.endpoint}/sessions/{sid}/export",
            params={"format": format},
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()

        if format == "json":
            return response.json()
        return response.text

    def explain(self, session_id: str | None = None) -> str:
        """Generate a human-readable explanation of the agent's behavior."""
        sid = session_id or self._current_session_id
        if not sid:
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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError("No session to get costs for. Specify session_id or start a session first.")

        response = self.transport._client.get(
            f"{self.transport.endpoint}/pricing/costs/{sid}",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def get_pricing(self) -> dict[str, Any]:
        """Get the current model pricing configuration.

        Returns:
            A dict with ``pricing`` (current model prices) and ``defaults``
            (built-in default prices).
        """
        response = self.transport._client.get(
            f"{self.transport.endpoint}/pricing",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def set_pricing(self, pricing: dict[str, dict[str, float]]) -> dict[str, Any]:
        """Update model pricing configuration.

        Args:
            pricing: A dict mapping model names to pricing dicts with
                ``input_cost_per_1m`` and ``output_cost_per_1m`` keys.

        Returns:
            A dict with ``status`` and ``updated`` count.
        """
        response = self.transport._client.put(
            f"{self.transport.endpoint}/pricing",
            json={"pricing": pricing},
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(
                "No session to search. Specify session_id or start a session first."
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

        response = self.transport._client.get(
            f"{self.transport.endpoint}/sessions/{sid}/events/search",
            params=params,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()
