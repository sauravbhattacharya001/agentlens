"""Session annotation mixin for AgentTracker."""

from __future__ import annotations

from typing import Any


class AnnotationMixin:
    """Mixin providing session annotation CRUD.

    Requires ``self.transport`` and ``self._resolve_session()``.
    """

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
