"""Pydantic models for AgentOps events, sessions, and traces."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class ToolCall(BaseModel):
    """Represents a single tool/function call made by an agent."""
    tool_call_id: str = Field(default_factory=_new_id)
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_output: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    duration_ms: float | None = None


class DecisionTrace(BaseModel):
    """Captures the reasoning behind an agent decision."""
    trace_id: str = Field(default_factory=_new_id)
    step: int = 0
    reasoning: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)
    confidence: float | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class AgentEvent(BaseModel):
    """A single observable event in an agent's execution."""
    event_id: str = Field(default_factory=_new_id)
    session_id: str = ""
    event_type: str = "generic"  # llm_call, tool_call, decision, error, etc.
    timestamp: datetime = Field(default_factory=_utcnow)
    
    # I/O
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    
    # LLM specifics
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    
    # Tool call (optional)
    tool_call: ToolCall | None = None
    
    # Decision trace (optional)
    decision_trace: DecisionTrace | None = None
    
    # Timing
    duration_ms: float | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for the API."""
        d = self.model_dump(mode="json", exclude_none=True)
        # Ensure timestamp is ISO string
        if isinstance(d.get("timestamp"), str):
            pass
        return d


class Session(BaseModel):
    """Represents an agent tracking session."""
    session_id: str = Field(default_factory=_new_id)
    agent_name: str = "default-agent"
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[AgentEvent] = Field(default_factory=list)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    status: str = "active"  # active, completed, error

    def add_event(self, event: AgentEvent) -> None:
        """Add an event to this session."""
        event.session_id = self.session_id
        self.events.append(event)
        self.total_tokens_in += event.tokens_in
        self.total_tokens_out += event.tokens_out

    def end(self) -> None:
        """Mark session as completed."""
        self.ended_at = _utcnow()
        self.status = "completed"

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for the API (without events)."""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "metadata": self.metadata,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "status": self.status,
        }
