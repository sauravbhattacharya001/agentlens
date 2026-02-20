"""AgentLens — Observability and Explainability for AI Agents."""

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.tracker import AgentTracker
from agentlens.decorators import track_agent, track_tool_call
from agentlens.transport import Transport

__version__ = "0.1.0"
__all__ = [
    "init",
    "start_session",
    "end_session",
    "track",
    "explain",
    "export_session",
    "compare_sessions",
    "get_costs",
    "get_pricing",
    "set_pricing",
    "track_agent",
    "track_tool_call",
    "AgentEvent",
    "ToolCall",
    "DecisionTrace",
    "Session",
]

_tracker: AgentTracker | None = None


def init(api_key: str = "default", endpoint: str = "http://localhost:3000") -> AgentTracker:
    """Initialize the AgentLens SDK.
    
    If the SDK was already initialized, the previous transport is closed
    (flushing any buffered events and stopping the background thread)
    before creating the new one.  This prevents resource leaks when
    ``init()`` is called multiple times (e.g. in tests or notebooks).
    
    Args:
        api_key: Your AgentLens API key.
        endpoint: The AgentLens backend URL.
    
    Returns:
        The global AgentTracker instance.
    """
    global _tracker
    # Clean up the previous tracker/transport to avoid leaking threads
    # and HTTP connections.
    if _tracker is not None:
        try:
            _tracker.transport.close()
        except Exception:
            pass
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
        raise RuntimeError("Call agentlens.init() before start_session()")
    return _tracker.start_session(agent_name=agent_name, metadata=metadata)


def end_session(session_id: str | None = None) -> None:
    """End the current or specified session and flush pending events."""
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before end_session()")
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
        raise RuntimeError("Call agentlens.init() before track()")
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
        raise RuntimeError("Call agentlens.init() before explain()")
    return _tracker.explain(session_id=session_id)


def export_session(session_id: str | None = None, format: str = "json"):
    """Export session data from the backend.

    Fetches the full session data (including all events) from the AgentLens
    backend and returns it in the requested format.

    Args:
        session_id: Session to export. Defaults to the current session.
        format: Export format — ``"json"`` returns a dict, ``"csv"`` returns
            a CSV string.

    Returns:
        A dict (for JSON) or a string (for CSV) with session data, events,
        and summary statistics.
    """
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before export_session()")
    return _tracker.export_session(session_id=session_id, format=format)


def compare_sessions(session_a: str, session_b: str) -> dict:
    """Compare two sessions side-by-side.

    Fetches comparison metrics from the AgentLens backend including
    token usage, event counts, tool usage, timing, and percentage deltas.

    Args:
        session_a: First session ID.
        session_b: Second session ID.

    Returns:
        A dict with ``session_a`` metrics, ``session_b`` metrics,
        ``deltas``, and ``shared`` breakdowns.
    """
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before compare_sessions()")
    return _tracker.compare_sessions(session_a=session_a, session_b=session_b)


def get_costs(session_id: str | None = None) -> dict:
    """Get cost breakdown for a session.

    Calculates costs using configured model pricing (per 1M tokens).

    Args:
        session_id: Session to get costs for. Defaults to the current session.

    Returns:
        A dict with ``total_cost``, ``total_input_cost``, ``total_output_cost``,
        ``model_costs``, ``event_costs``, ``currency``, and ``unmatched_models``.
    """
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before get_costs()")
    return _tracker.get_costs(session_id=session_id)


def get_pricing() -> dict:
    """Get the current model pricing configuration.

    Returns:
        A dict with ``pricing`` (current prices) and ``defaults`` (built-in defaults).
    """
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before get_pricing()")
    return _tracker.get_pricing()


def set_pricing(pricing: dict) -> dict:
    """Update model pricing configuration.

    Args:
        pricing: A dict mapping model names to pricing dicts with
            ``input_cost_per_1m`` and ``output_cost_per_1m`` keys.

    Returns:
        A dict with ``status`` and ``updated`` count.
    """
    if _tracker is None:
        raise RuntimeError("Call agentlens.init() before set_pricing()")
    return _tracker.set_pricing(pricing=pricing)
