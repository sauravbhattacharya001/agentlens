"""Session tagging and search mixin for AgentTracker."""

from __future__ import annotations

from typing import Any


class TagMixin:
    """Mixin providing session tag management and session search.

    Requires ``self.transport`` and ``self._resolve_session()``.
    """

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
