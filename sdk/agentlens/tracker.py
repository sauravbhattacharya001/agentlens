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

    # ── Alert Rules ──────────────────────────────────────────────────

    def list_alert_rules(self, enabled: bool | None = None) -> dict:
        """List all alert rules, optionally filtered by enabled status."""
        params = {}
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        response = self.transport._client.get(
            f"{self.transport.endpoint}/alerts/rules",
            params=params,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        response = self.transport._client.post(
            f"{self.transport.endpoint}/alerts/rules",
            json=payload,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def update_alert_rule(self, rule_id: str, **kwargs) -> dict:
        """Update an existing alert rule. Pass any field to update."""
        response = self.transport._client.put(
            f"{self.transport.endpoint}/alerts/rules/{rule_id}",
            json=kwargs,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def delete_alert_rule(self, rule_id: str) -> dict:
        """Delete an alert rule."""
        response = self.transport._client.delete(
            f"{self.transport.endpoint}/alerts/rules/{rule_id}",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def evaluate_alerts(self) -> dict:
        """Evaluate all enabled alert rules against current data."""
        response = self.transport._client.post(
            f"{self.transport.endpoint}/alerts/evaluate",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        response = self.transport._client.get(
            f"{self.transport.endpoint}/alerts/events",
            params=params,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def acknowledge_alert(self, alert_id: str) -> dict:
        """Acknowledge a triggered alert event."""
        response = self.transport._client.put(
            f"{self.transport.endpoint}/alerts/events/{alert_id}/acknowledge",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

    def get_alert_metrics(self) -> dict:
        """Get list of available metrics for alert rules."""
        response = self.transport._client.get(
            f"{self.transport.endpoint}/alerts/metrics",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(
                "No session to tag. Specify session_id or start a session first."
            )
        if not tags or not isinstance(tags, list):
            raise ValueError("tags must be a non-empty list of strings.")

        response = self.transport._client.post(
            f"{self.transport.endpoint}/sessions/{sid}/tags",
            json={"tags": tags},
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(
                "No session to untag. Specify session_id or start a session first."
            )

        body = {"tags": tags} if tags else {}
        response = self.transport._client.request(
            "DELETE",
            f"{self.transport.endpoint}/sessions/{sid}/tags",
            json=body,
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()

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
        sid = session_id or self._current_session_id
        if not sid:
            raise RuntimeError(
                "No session to query. Specify session_id or start a session first."
            )

        response = self.transport._client.get(
            f"{self.transport.endpoint}/sessions/{sid}/tags",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json().get("tags", [])

    def list_all_tags(self) -> list[dict[str, Any]]:
        """List all tags across all sessions with session counts.

        Returns:
            A list of dicts with ``tag`` and ``session_count`` keys,
            ordered by session count descending.

        Example::

            tags = tracker.list_all_tags()
            # [{"tag": "production", "session_count": 42}, ...]
        """
        response = self.transport._client.get(
            f"{self.transport.endpoint}/sessions/tags",
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json().get("tags", [])

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

        response = self.transport._client.get(
            f"{self.transport.endpoint}/sessions/by-tag/{tag}",
            params={"limit": limit, "offset": offset},
            headers={"X-API-Key": self.transport.api_key},
        )
        response.raise_for_status()
        return response.json()
