"""Backend query mixin for AgentTracker.

These methods are thin wrappers over the AgentLens collector's HTTP API:
they fetch comparison, export, pricing/cost, event-search, and activity
data for sessions that have already been shipped to the backend. They hold
no capture state of their own.
"""

from __future__ import annotations

from typing import Any


class QueryMixin:
    """Mixin providing backend-backed session query/export methods.

    Requires ``self.transport`` and ``self._resolve_session()``.
    """

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
