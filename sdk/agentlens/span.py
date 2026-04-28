"""Spans — context managers for grouping agent events into logical units.

Spans provide structured, hierarchical grouping of events with automatic
timing.  They work as context managers and support nesting::

    with tracker.span("planning") as s:
        tracker.track(event_type="llm_call", model="gpt-4o", ...)
        with tracker.span("tool-execution") as child:
            tracker.track_tool("search", ...)
        s.set_attribute("result", "success")

Each span automatically records its start time, end time, duration, child
spans, event count, and any custom attributes.  Spans are sent to the
backend as ``span_start`` / ``span_end`` events so they appear in the
session timeline and can be queried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

from agentlens._utils import new_id, utcnow as _utcnow

_new_id = partial(new_id, 16)


@dataclass
class Span:
    """A logical grouping of agent events with timing and metadata.

    Spans are created via :meth:`AgentTracker.span` and should be used
    as context managers.  They automatically record start/end times and
    emit events to the backend.

    Attributes:
        span_id: Unique identifier for this span.
        name: Human-readable span name (e.g. ``"planning"``, ``"tool-loop"``).
        parent_id: ID of the parent span, or ``None`` for root spans.
        session_id: Session this span belongs to.
        started_at: UTC timestamp when the span started.
        ended_at: UTC timestamp when the span ended (set on exit).
        duration_ms: Wall-clock duration in milliseconds (set on exit).
        attributes: Arbitrary key-value metadata attached to this span.
        status: ``"active"``, ``"completed"``, or ``"error"``.
        error: Error message if the span exited with an exception.
        event_count: Number of events tracked while this span was active.
        children: List of child span IDs.
    """

    span_id: str = field(default_factory=_new_id)
    name: str = ""
    parent_id: str | None = None
    session_id: str = ""
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    error: str | None = None
    event_count: int = 0
    children: list[str] = field(default_factory=list)

    # Internal: monotonic clock reference for accurate duration
    _mono_start: float = field(default=0.0, repr=False)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a custom attribute on this span.

        Args:
            key: Attribute name.
            value: Attribute value (must be JSON-serializable).
        """
        self.attributes[key] = value

    def set_status(self, status: str, error: str | None = None) -> None:
        """Override the span status.

        Args:
            status: ``"completed"`` or ``"error"``.
            error: Optional error message when status is ``"error"``.
        """
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "name": self.name,
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "status": self.status,
            "event_count": self.event_count,
            "children": self.children,
        }
        if self.parent_id:
            d["parent_id"] = self.parent_id
        if self.ended_at:
            d["ended_at"] = self.ended_at.isoformat()
        if self.duration_ms is not None:
            d["duration_ms"] = round(self.duration_ms, 2)
        if self.attributes:
            d["attributes"] = self.attributes
        if self.error:
            d["error"] = self.error
        return d
