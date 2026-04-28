"""Pydantic models for AgentLens events, sessions, and traces."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from pydantic import BaseModel, Field

from agentlens._utils import new_id, utcnow as _utcnow

_new_id = partial(new_id, 16)


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
        """Convert to a dict suitable for the API.

        Uses a fast manual path for the common case (no tool_call, no
        decision_trace) to avoid Pydantic's full ``model_dump``
        serialisation overhead.  Falls back to ``model_dump`` when
        nested models are present.
        """
        if self.tool_call is not None or self.decision_trace is not None:
            return self.model_dump(mode="json", exclude_none=True)

        # Fast path: build dict manually, ~3-5x faster than model_dump
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }
        if self.input_data is not None:
            d["input_data"] = self.input_data
        if self.output_data is not None:
            d["output_data"] = self.output_data
        if self.model is not None:
            d["model"] = self.model
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
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
