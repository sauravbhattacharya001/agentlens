"""AgentOps — Observability and Explainability for AI Agents."""

from agentops.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentops.tracker import AgentTracker
from agentops.decorators import track_agent, track_tool_call
from agentops.transport import Transport

__version__ = "0.1.0"
__all__ = [
    "init",
    "start_session",
    "end_session",
    "track",
    "explain",
    "track_agent",
    "track_tool_call",
    "AgentEvent",
    "ToolCall",
    "DecisionTrace",
    "Session",
]

_tracker: AgentTracker | None = None


def init(api_key: str = "default", endpoint: str = "http://localhost:3000") -> AgentTracker:
    """Initialize the AgentOps SDK.
    
    Args:
        api_key: Your AgentOps API key.
        endpoint: The AgentOps backend URL.
    
    Returns:
        The global AgentTracker instance.
    """
    global _tracker
    transport = Transport(endpoint=endpoint, api_key=api_key)
    _tracker = AgentTracker(transport=transport)
    return _tracker


def start_session(agent_name: str = "default-agent", metadata: dict | None = None) -> Session:
    """Start a new tracking session.
    
    Args:
        agent_name: Name of the agent being tracked.
        metadata: Optional metadata dict.
    
    Returns:
        A Session object.
    """
    if _tracker is None:
        raise RuntimeError("Call agentops.init() before start_session()")
    return _tracker.start_session(agent_name=agent_name, metadata=metadata)


def end_session(session_id: str | None = None) -> None:
    """End the current or specified session and flush pending events."""
    if _tracker is None:
        raise RuntimeError("Call agentops.init() before end_session()")
    _tracker.end_session(session_id=session_id)


def track(
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
    """Track an agent event manually.
    
    Returns:
        The created AgentEvent.
    """
    if _tracker is None:
        raise RuntimeError("Call agentops.init() before track()")
    return _tracker.track(
        event_type=event_type,
        input_data=input_data,
        output_data=output_data,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning=reasoning,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        duration_ms=duration_ms,
    )


def explain(session_id: str | None = None) -> str:
    """Get a human-readable explanation of the agent's behavior in the current/specified session.
    
    Returns:
        A string explanation.
    """
    if _tracker is None:
        raise RuntimeError("Call agentops.init() before explain()")
    return _tracker.explain(session_id=session_id)
