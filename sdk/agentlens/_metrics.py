"""Shared session metric extraction for AgentLens.

Centralizes the single-pass event metric extraction logic used by both
the anomaly detector and drift detector, eliminating the duplicated
iteration patterns across ``anomaly.py`` and ``drift.py``.
"""

from __future__ import annotations

from typing import Any


def extract_session_metrics(session: Any) -> dict[str, float]:
    """Extract numerical metrics from a session via single-pass event scan.

    Collects durations, token counts, error counts, tool invocations, and
    tool failures in one pass over all events.  Returns a dict of metric
    name → float suitable for both anomaly detection and drift analysis.

    Metrics produced:
        - ``event_count``: total events
        - ``avg_latency_ms``: mean event duration
        - ``p95_latency_ms``: 95th-percentile event duration (0 if no durations)
        - ``total_tokens``: sum of tokens_in + tokens_out
        - ``tokens_per_event``: average tokens per event
        - ``error_rate``: fraction of events with "error" in event_type
        - ``tool_call_rate``: fraction of events with a tool_call attribute
        - ``tool_failure_rate``: fraction of tool events that are also errors

    Args:
        session: Object with an ``events`` attribute (list of event objects).

    Returns:
        Dict mapping metric names to float values.
    """
    events = getattr(session, "events", None) or []
    event_count = len(events)

    metrics: dict[str, float] = {"event_count": float(event_count)}

    if event_count == 0:
        for k in ("avg_latency_ms", "p95_latency_ms", "total_tokens",
                   "tokens_per_event", "error_rate", "tool_call_rate",
                   "tool_failure_rate"):
            metrics[k] = 0.0
        return metrics

    durations: list[float] = []
    total_tokens = 0
    error_count = 0
    tool_count = 0
    tool_error_count = 0

    for e in events:
        # Latency
        dur = getattr(e, "duration_ms", None)
        if dur is not None:
            durations.append(dur)

        # Tokens
        total_tokens += (getattr(e, "tokens_in", 0) or 0) + \
                        (getattr(e, "tokens_out", 0) or 0)

        # Classify event type
        event_type = getattr(e, "event_type", None) or ""
        is_error = "error" in event_type.lower()
        # Detect tool events by either a tool_call attribute or "tool" in
        # the event_type string (covers event_type="tool_call"/"tool_error").
        has_tool = (getattr(e, "tool_call", None) is not None
                    or "tool" in event_type.lower())

        if is_error:
            error_count += 1
        if has_tool:
            tool_count += 1
            if is_error:
                tool_error_count += 1

    # Latency
    if durations:
        metrics["avg_latency_ms"] = sum(durations) / len(durations)
        sorted_d = sorted(durations)
        p95_idx = min(int(len(sorted_d) * 0.95), len(sorted_d) - 1)
        metrics["p95_latency_ms"] = sorted_d[p95_idx]
    else:
        metrics["avg_latency_ms"] = 0.0
        metrics["p95_latency_ms"] = 0.0

    # Tokens
    metrics["total_tokens"] = float(total_tokens)
    metrics["tokens_per_event"] = total_tokens / event_count

    # Rates
    metrics["error_rate"] = error_count / event_count
    metrics["tool_call_rate"] = tool_count / event_count
    metrics["tool_failure_rate"] = (
        tool_error_count / tool_count if tool_count > 0 else 0.0
    )

    return metrics
