"""AgentLens â€” Observability and Explainability for AI Agents."""

# PEP 563: defer annotation evaluation so module-level annotations using PEP 604
# union syntax (e.g. ``AgentTracker | None``) do not crash at import time on
# Python 3.9, where ``type | None`` raises ``TypeError`` at runtime.
from __future__ import annotations

from agentlens.models import AgentEvent, ToolCall, DecisionTrace, Session
from agentlens.tracker import AgentTracker
from agentlens.decorators import track_agent, track_tool_call
from agentlens.transport import Transport
from agentlens.health import HealthScorer, HealthReport, HealthGrade, HealthThresholds, MetricScore
from agentlens.timeline import TimelineRenderer
from agentlens.span import Span
from agentlens.exporter import SessionExporter
from agentlens.narrative import NarrativeGenerator, NarrativeConfig, NarrativeStyle, Narrative, NarrativeSection, ToolSummary
from agentlens.replayer import SessionReplayer, ReplayFrame, ReplayStats
from agentlens.flamegraph import Flamegraph, flamegraph_html
from agentlens.transcript import TranscriptExporter, export_transcript as _render_transcript, export_run_metadata as _extract_run_metadata, TRANSCRIPT_CONTRACT_VERSION

__version__ = "1.65.0"
__all__ = [
    "init",
    "start_session",
    "end_session",
    "track",
    "explain",
    "export_session",
    "export_transcript",
    "export_run_metadata",
    "TranscriptExporter",
    "TRANSCRIPT_CONTRACT_VERSION",
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
    "AgentTracker",
    "Transport",
    "HealthScorer",
    "HealthReport",
    "HealthGrade",
    "HealthThresholds",
    "MetricScore",
    "TimelineRenderer",
    "Span",
    "SessionExporter",
    "NarrativeGenerator",
    "NarrativeConfig",
    "NarrativeStyle",
    "Narrative",
    "NarrativeSection",
    "ToolSummary",
    "SessionReplayer",
    "ReplayFrame",
    "ReplayStats",
    "Flamegraph",
    "flamegraph_html",
]

_tracker: AgentTracker | None = None


def _get_tracker(operation: str = "this operation") -> AgentTracker:
    """Return the global tracker, raising if the SDK is not initialized.

    Centralises the guard clause that was previously copy-pasted in every
    module-level convenience function.

    Args:
        operation: Name shown in the error message (e.g. ``"track"``).

    Raises:
        RuntimeError: If :func:`init` has not been called yet.
    """
    if _tracker is None:
        raise RuntimeError(f"Call agentlens.init() before {operation}()")
    return _tracker


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
    return _get_tracker("start_session").start_session(agent_name=agent_name, metadata=metadata)


def end_session(session_id: str | None = None) -> None:
    """End the current or specified session and flush pending events."""
    _get_tracker("end_session").end_session(session_id=session_id)


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
    return _get_tracker("track").track(
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
    return _get_tracker("explain").explain(session_id=session_id)


def export_session(session_id: str | None = None, format: str = "json"):
    """Export session data from the backend.

    Fetches the full session data (including all events) from the AgentLens
    backend and returns it in the requested format.

    Args:
        session_id: Session to export. Defaults to the current session.
        format: Export format â€” ``"json"`` returns a dict, ``"csv"`` returns
            a CSV string.

    Returns:
        A dict (for JSON) or a string (for CSV) with session data, events,
        and summary statistics.
    """
    return _get_tracker("export_session").export_session(session_id=session_id, format=format)


def export_transcript(
    session=None,
    *,
    session_id: str | None = None,
    timezone_label: str = "UTC",
) -> str:
    """Render an AgentLens session as a contract-compliant transcript for agent-eval.

    The output conforms to ``transcript-contract@v1`` and is *evidence-backed*:
    every section is derived from captured trace data (real tool calls, timing,
    recorded status), not the agent's self-report. Validate it with
    ``agent-eval validate`` and score it with the agent-eval monitor.

    Args:
        session: An AgentLens :class:`~agentlens.models.Session` or a
            session-shaped dict (with an ``events`` list). If omitted, the
            session is fetched from the backend via ``export_session``.
        session_id: Session to fetch when ``session`` is not provided. Defaults
            to the current session.
        timezone_label: Cosmetic timezone label for printed times (UTC clock).

    Returns:
        A markdown string conforming to the agent-eval transcript contract.
    """
    if session is None:
        # Fetch the full session (incl. events) from the backend.
        session = export_session(session_id=session_id, format="json")
    return _render_transcript(session, timezone_label=timezone_label)


def export_run_metadata(session=None, *, session_id: str | None = None) -> dict:
    """Extract agent-eval ``RunMetadata`` (ground truth) from a session.

    Pairs with :func:`export_transcript`: the transcript is the agent's claim,
    this is the recorded status + wall-clock that agent-eval's ``verification``
    check grades the claim against. Together they make the AgentLens ->
    agent-eval path self-verifying.

    Args:
        session: An AgentLens :class:`~agentlens.models.Session` or a
            session-shaped dict. If omitted, fetched from the backend.
        session_id: Session to fetch when ``session`` is not provided. Defaults
            to the current session.

    Returns:
        A dict shaped like agent-eval's ``RunMetadata``.
    """
    if session is None:
        session = export_session(session_id=session_id, format="json")
    return _extract_run_metadata(session)


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
    return _get_tracker("compare_sessions").compare_sessions(session_a=session_a, session_b=session_b)


def get_costs(session_id: str | None = None) -> dict:
    """Get cost breakdown for a session.

    Calculates costs using configured model pricing (per 1M tokens).

    Args:
        session_id: Session to get costs for. Defaults to the current session.

    Returns:
        A dict with ``total_cost``, ``total_input_cost``, ``total_output_cost``,
        ``model_costs``, ``event_costs``, ``currency``, and ``unmatched_models``.
    """
    return _get_tracker("get_costs").get_costs(session_id=session_id)


def get_pricing() -> dict:
    """Get the current model pricing configuration.

    Returns:
        A dict with ``pricing`` (current prices) and ``defaults`` (built-in defaults).
    """
    return _get_tracker("get_pricing").get_pricing()


def set_pricing(pricing: dict) -> dict:
    """Update model pricing configuration.

    Args:
        pricing: A dict mapping model names to pricing dicts with
            ``input_cost_per_1m`` and ``output_cost_per_1m`` keys.

    Returns:
        A dict with ``status`` and ``updated`` count.
    """
    return _get_tracker("set_pricing").set_pricing(pricing=pricing)
