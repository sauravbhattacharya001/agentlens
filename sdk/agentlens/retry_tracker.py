"""Retry Tracker for AgentLens.

Specialised tracking for agent retry behaviour — when an agent retries
a tool call or LLM request, the tracker builds retry chains, measures
retry effectiveness, computes the "retry tax" (extra tokens, cost, and
latency), identifies retry storms, and generates reduction
recommendations.

Retries are a major source of invisible overhead in agent systems.
This module makes them visible and quantifiable.

Example::

    from agentlens.retry_tracker import RetryTracker

    tracker = RetryTracker()

    # Feed sessions (each event may carry a retry_of field)
    tracker.add_sessions(sessions)

    report = tracker.report()
    print(report.render())
    print(f"Retry rate:  {report.retry_rate:.1%}")
    print(f"Retry tax:   {report.retry_tax_tokens} extra tokens")
    print(f"Storms:      {len(report.storms)}")
    print(f"Savings tip: {report.recommendations[0]}")
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class RetryOutcome(Enum):
    """Final outcome of a retry chain."""
    SUCCEEDED = "succeeded"       # eventually succeeded
    FAILED = "failed"             # all retries failed
    PARTIAL = "partial"           # succeeded with degraded output


@dataclass
class RetryChain:
    """A chain of retries from original call through all retry attempts."""
    chain_id: str
    session_id: str
    original_event_id: str
    event_type: str                   # llm_call, tool_call, etc.
    tool_name: str | None             # for tool_call events
    model: str | None                 # for llm_call events
    attempt_count: int                # total attempts (1 = no retries)
    outcome: RetryOutcome
    total_duration_ms: float
    original_duration_ms: float
    retry_duration_ms: float          # extra time from retries
    tokens_original: int
    tokens_retry: int                 # extra tokens from retries
    error_types: list[str]            # errors encountered in chain
    timestamps: list[str]             # ISO timestamps of each attempt


@dataclass
class RetryStorm:
    """A burst of retries within a short window — indicates systemic issues."""
    session_id: str
    window_start: str                 # ISO timestamp
    window_end: str
    retry_count: int
    unique_chains: int
    dominant_error: str | None
    affected_tools: list[str]
    affected_models: list[str]


@dataclass
class RetryRecommendation:
    """A specific, actionable recommendation to reduce retries."""
    priority: int                     # 1 = highest
    category: str                     # "caching", "fallback", "timeout", etc.
    description: str
    estimated_savings_pct: float      # estimated retry reduction %


@dataclass
class RetryReport:
    """Full retry analysis report."""
    total_events: int
    total_retries: int
    retry_rate: float                 # retries / total events
    chains: list[RetryChain]
    success_rate: float               # % of chains that eventually succeeded
    avg_attempts: float               # average attempts per chain
    max_attempts: int
    retry_tax_tokens: int             # total extra tokens from retries
    retry_tax_duration_ms: float      # total extra time from retries
    retries_by_type: dict[str, int]   # event_type → count
    retries_by_tool: dict[str, int]   # tool_name → count
    retries_by_model: dict[str, int]  # model → count
    retries_by_error: dict[str, int]  # error_type → count
    storms: list[RetryStorm]
    recommendations: list[RetryRecommendation]

    def render(self) -> str:
        """Render a human-readable summary."""
        lines = [
            "═══ Retry Analysis Report ═══",
            f"Total events:       {self.total_events}",
            f"Total retries:      {self.total_retries}",
            f"Retry rate:         {self.retry_rate:.1%}",
            f"Success rate:       {self.success_rate:.1%}",
            f"Avg attempts/chain: {self.avg_attempts:.1f}",
            f"Max attempts:       {self.max_attempts}",
            "",
            "── Retry Tax ──",
            f"Extra tokens:       {self.retry_tax_tokens:,}",
            f"Extra latency:      {self.retry_tax_duration_ms:,.0f} ms",
        ]

        if self.retries_by_type:
            lines.append("")
            lines.append("── By Event Type ──")
            for k, v in sorted(self.retries_by_type.items(),
                               key=lambda x: -x[1]):
                lines.append(f"  {k}: {v}")

        if self.retries_by_tool:
            lines.append("")
            lines.append("── By Tool ──")
            for k, v in sorted(self.retries_by_tool.items(),
                               key=lambda x: -x[1]):
                lines.append(f"  {k}: {v}")

        if self.retries_by_error:
            lines.append("")
            lines.append("── By Error ──")
            for k, v in sorted(self.retries_by_error.items(),
                               key=lambda x: -x[1]):
                lines.append(f"  {k}: {v}")

        if self.storms:
            lines.append("")
            lines.append(f"── Retry Storms ({len(self.storms)}) ──")
            for s in self.storms:
                lines.append(
                    f"  {s.session_id}: {s.retry_count} retries in "
                    f"{s.window_start} – {s.window_end}"
                )
                if s.dominant_error:
                    lines.append(f"    Dominant error: {s.dominant_error}")

        if self.recommendations:
            lines.append("")
            lines.append("── Recommendations ──")
            for r in self.recommendations:
                lines.append(
                    f"  [{r.priority}] ({r.category}) {r.description} "
                    f"(~{r.estimated_savings_pct:.0f}% reduction)"
                )

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "total_events": self.total_events,
            "total_retries": self.total_retries,
            "retry_rate": round(self.retry_rate, 4),
            "success_rate": round(self.success_rate, 4),
            "avg_attempts": round(self.avg_attempts, 2),
            "max_attempts": self.max_attempts,
            "retry_tax_tokens": self.retry_tax_tokens,
            "retry_tax_duration_ms": round(self.retry_tax_duration_ms, 1),
            "chains": len(self.chains),
            "storms": len(self.storms),
            "retries_by_type": self.retries_by_type,
            "retries_by_tool": self.retries_by_tool,
            "retries_by_model": self.retries_by_model,
            "retries_by_error": self.retries_by_error,
            "recommendations": [
                {"priority": r.priority, "category": r.category,
                 "description": r.description,
                 "estimated_savings_pct": r.estimated_savings_pct}
                for r in self.recommendations
            ],
        }


# ---------------------------------------------------------------------------
# Core tracker
# ---------------------------------------------------------------------------

# Default: ≥5 retries within 60 seconds = storm
_DEFAULT_STORM_WINDOW_MS = 60_000
_DEFAULT_STORM_THRESHOLD = 5


class RetryTracker:
    """Analyse retry behaviour across agent sessions.

    Events are linked into retry chains by the ``retry_of`` field on each
    event dict.  If event B has ``retry_of = A.event_id``, then B is a
    retry of A.  Chains can be arbitrarily long (A → B → C → …).
    """

    def __init__(
        self,
        storm_window_ms: float = _DEFAULT_STORM_WINDOW_MS,
        storm_threshold: int = _DEFAULT_STORM_THRESHOLD,
    ) -> None:
        if storm_window_ms <= 0:
            raise ValueError("storm_window_ms must be positive")
        if storm_threshold < 2:
            raise ValueError("storm_threshold must be >= 2")

        self._storm_window_ms = storm_window_ms
        self._storm_threshold = storm_threshold
        self._sessions: list[dict[str, Any]] = []

    # -- Ingestion ------------------------------------------------------

    def add_session(self, session: dict[str, Any]) -> None:
        """Add a single session (dict with ``events`` list)."""
        self._sessions.append(session)

    def add_sessions(self, sessions: list[dict[str, Any]]) -> None:
        """Add multiple sessions."""
        self._sessions.extend(sessions)

    def clear(self) -> None:
        """Remove all ingested sessions."""
        self._sessions.clear()

    # -- Analysis -------------------------------------------------------

    def report(self) -> RetryReport:
        """Analyse all ingested sessions and produce a RetryReport."""
        all_events: list[dict[str, Any]] = []
        events_by_id: dict[str, dict[str, Any]] = {}
        events_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for sess in self._sessions:
            sid = sess.get("session_id", "unknown")
            for ev in sess.get("events", []):
                eid = ev.get("event_id", "")
                ev["_session_id"] = sid
                all_events.append(ev)
                if eid:
                    events_by_id[eid] = ev
                events_by_session[sid].append(ev)

        # Build retry chains
        chains = self._build_chains(all_events, events_by_id)

        # Aggregate stats
        total_events = len(all_events)
        total_retries = sum(c.attempt_count - 1 for c in chains)
        retry_rate = total_retries / max(total_events, 1)
        succeeded = sum(1 for c in chains if c.outcome == RetryOutcome.SUCCEEDED)
        success_rate = succeeded / max(len(chains), 1)
        avg_attempts = (
            sum(c.attempt_count for c in chains) / max(len(chains), 1)
        )
        max_attempts = max((c.attempt_count for c in chains), default=0)
        retry_tax_tokens = sum(c.tokens_retry for c in chains)
        retry_tax_duration = sum(c.retry_duration_ms for c in chains)

        # Breakdowns
        by_type: dict[str, int] = defaultdict(int)
        by_tool: dict[str, int] = defaultdict(int)
        by_model: dict[str, int] = defaultdict(int)
        by_error: dict[str, int] = defaultdict(int)

        for c in chains:
            retries = c.attempt_count - 1
            by_type[c.event_type] += retries
            if c.tool_name:
                by_tool[c.tool_name] += retries
            if c.model:
                by_model[c.model] += retries
            for err in c.error_types:
                by_error[err] += 1  # count each error occurrence once

        # Storm detection
        storms = self._detect_storms(events_by_session, events_by_id)

        # Recommendations
        recs = self._generate_recommendations(
            chains, dict(by_type), dict(by_tool), dict(by_error),
            storms, total_events, total_retries,
        )

        return RetryReport(
            total_events=total_events,
            total_retries=total_retries,
            retry_rate=retry_rate,
            chains=chains,
            success_rate=success_rate,
            avg_attempts=avg_attempts,
            max_attempts=max_attempts,
            retry_tax_tokens=retry_tax_tokens,
            retry_tax_duration_ms=retry_tax_duration,
            retries_by_type=dict(by_type),
            retries_by_tool=dict(by_tool),
            retries_by_model=dict(by_model),
            retries_by_error=dict(by_error),
            storms=storms,
            recommendations=recs,
        )

    # -- Chain building -------------------------------------------------

    @staticmethod
    def _build_chains(
        events: list[dict[str, Any]],
        by_id: dict[str, dict[str, Any]],
    ) -> list[RetryChain]:
        """Build retry chains from events linked by ``retry_of``."""
        # Find retry links: child → parent
        child_of: dict[str, str] = {}
        for ev in events:
            parent_id = ev.get("retry_of")
            if parent_id and parent_id in by_id:
                child_of[ev.get("event_id", "")] = parent_id

        # Find chain roots (events that are retried but are not retries themselves)
        all_children = set(child_of.keys())
        roots: set[str] = set()
        for eid in child_of.values():
            if eid not in child_of:
                roots.add(eid)

        # Build forward index: parent → [children]
        children_of: dict[str, list[str]] = defaultdict(list)
        for child_id, parent_id in child_of.items():
            children_of[parent_id].append(child_id)

        chains: list[RetryChain] = []
        for root_id in roots:
            root_ev = by_id.get(root_id)
            if not root_ev:
                continue

            # Walk the chain forward
            chain_events = [root_ev]
            current = root_id
            visited = {root_id}
            while current in children_of:
                kids = children_of[current]
                if not kids:
                    break
                next_id = kids[0]  # take first child
                if next_id in visited:
                    break  # prevent cycles
                visited.add(next_id)
                ev = by_id.get(next_id)
                if ev:
                    chain_events.append(ev)
                current = next_id

            chains.append(_chain_from_events(root_id, chain_events))

        return chains

    # -- Storm detection ------------------------------------------------

    def _detect_storms(
        self,
        events_by_session: dict[str, list[dict[str, Any]]],
        by_id: dict[str, dict[str, Any]],
    ) -> list[RetryStorm]:
        """Detect bursts of retries within the configured window."""
        storms: list[RetryStorm] = []

        for sid, events in events_by_session.items():
            # Collect retry events with timestamps
            retry_events = []
            for ev in events:
                if ev.get("retry_of") and ev.get("retry_of") in by_id:
                    ts = ev.get("timestamp", "")
                    retry_events.append((ts, ev))

            if len(retry_events) < self._storm_threshold:
                continue

            retry_events.sort(key=lambda x: x[0])

            # Sliding window
            i = 0
            while i < len(retry_events):
                window = [retry_events[i]]
                j = i + 1
                t0 = _parse_ts_ms(retry_events[i][0])
                while j < len(retry_events):
                    tj = _parse_ts_ms(retry_events[j][0])
                    if tj - t0 <= self._storm_window_ms:
                        window.append(retry_events[j])
                        j += 1
                    else:
                        break

                if len(window) >= self._storm_threshold:
                    # Collect metadata
                    errors: dict[str, int] = defaultdict(int)
                    tools: set[str] = set()
                    models: set[str] = set()
                    chain_ids: set[str] = set()

                    for _, ev in window:
                        err = ev.get("error_type") or ev.get("error", "")
                        if err:
                            errors[str(err)] += 1
                        tc = ev.get("tool_call")
                        if isinstance(tc, dict) and tc.get("tool_name"):
                            tools.add(tc["tool_name"])
                        if ev.get("model"):
                            models.add(ev["model"])
                        chain_ids.add(ev.get("retry_of", ""))

                    dominant = max(errors, key=errors.get) if errors else None

                    storms.append(RetryStorm(
                        session_id=sid,
                        window_start=window[0][0],
                        window_end=window[-1][0],
                        retry_count=len(window),
                        unique_chains=len(chain_ids),
                        dominant_error=dominant,
                        affected_tools=sorted(tools),
                        affected_models=sorted(models),
                    ))
                    i = j  # skip past this storm
                else:
                    i += 1

        return storms

    # -- Recommendations ------------------------------------------------

    @staticmethod
    def _generate_recommendations(
        chains: list[RetryChain],
        by_type: dict[str, int],
        by_tool: dict[str, int],
        by_error: dict[str, int],
        storms: list[RetryStorm],
        total_events: int,
        total_retries: int,
    ) -> list[RetryRecommendation]:
        recs: list[RetryRecommendation] = []
        priority = 1

        if not chains:
            return recs

        retry_rate = total_retries / max(total_events, 1)

        # 1. Retry storms → circuit breaker
        if storms:
            recs.append(RetryRecommendation(
                priority=priority,
                category="circuit-breaker",
                description=(
                    f"Detected {len(storms)} retry storm(s). Add a circuit "
                    f"breaker to halt retries after consecutive failures."
                ),
                estimated_savings_pct=min(
                    30.0, len(storms) * 10.0
                ),
            ))
            priority += 1

        # 2. High-retry tools → caching / fallback
        if by_tool:
            worst_tool = max(by_tool, key=by_tool.get)
            tool_retries = by_tool[worst_tool]
            if tool_retries >= 3:
                recs.append(RetryRecommendation(
                    priority=priority,
                    category="caching",
                    description=(
                        f"Tool '{worst_tool}' has {tool_retries} retries. "
                        f"Consider caching results or adding a fallback."
                    ),
                    estimated_savings_pct=min(
                        40.0, tool_retries * 5.0
                    ),
                ))
                priority += 1

        # 3. Dominant error type → targeted fix
        if by_error:
            worst_err = max(by_error, key=by_error.get)
            err_count = by_error[worst_err]
            if err_count >= 2:
                recs.append(RetryRecommendation(
                    priority=priority,
                    category="error-handling",
                    description=(
                        f"Error '{worst_err}' caused {err_count} retries. "
                        f"Add specific handling for this error type."
                    ),
                    estimated_savings_pct=min(
                        50.0, err_count * 8.0
                    ),
                ))
                priority += 1

        # 4. Long chains → max-retry cap
        long_chains = [c for c in chains if c.attempt_count > 3]
        if long_chains:
            max_att = max(c.attempt_count for c in long_chains)
            recs.append(RetryRecommendation(
                priority=priority,
                category="retry-limit",
                description=(
                    f"{len(long_chains)} chain(s) exceeded 3 attempts "
                    f"(max {max_att}). Set a retry cap of 3."
                ),
                estimated_savings_pct=min(
                    25.0, len(long_chains) * 5.0
                ),
            ))
            priority += 1

        # 5. High overall retry rate → exponential backoff
        if retry_rate > 0.1:
            recs.append(RetryRecommendation(
                priority=priority,
                category="backoff",
                description=(
                    f"Overall retry rate is {retry_rate:.0%}. Add "
                    f"exponential backoff to reduce load during failures."
                ),
                estimated_savings_pct=min(20.0, retry_rate * 100),
            ))
            priority += 1

        # 6. Failed chains → fallback model / tool
        failed = [c for c in chains if c.outcome == RetryOutcome.FAILED]
        if failed:
            recs.append(RetryRecommendation(
                priority=priority,
                category="fallback",
                description=(
                    f"{len(failed)} retry chain(s) failed completely. "
                    f"Add fallback models or alternative tools."
                ),
                estimated_savings_pct=min(
                    35.0, len(failed) * 10.0
                ),
            ))

        return recs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chain_from_events(
    chain_id: str,
    events: list[dict[str, Any]],
) -> RetryChain:
    """Build a RetryChain from an ordered list of attempt events."""
    first = events[0]
    last = events[-1]

    total_dur = sum(_dur(e) for e in events)
    orig_dur = _dur(first)
    orig_tokens = _tokens(first)
    retry_tokens = sum(_tokens(e) for e in events[1:])

    errors: list[str] = []
    for e in events:
        err = e.get("error_type") or e.get("error")
        if err:
            errors.append(str(err))

    # Determine outcome: last event in chain has no error → succeeded
    last_err = last.get("error_type") or last.get("error")
    if not last_err:
        outcome = RetryOutcome.SUCCEEDED
    elif len(events) > 1 and not (first.get("error_type") or first.get("error")):
        outcome = RetryOutcome.PARTIAL
    else:
        outcome = RetryOutcome.FAILED

    # Tool / model from first event
    tool_name = None
    tc = first.get("tool_call")
    if isinstance(tc, dict):
        tool_name = tc.get("tool_name")
    model = first.get("model")

    timestamps = [e.get("timestamp", "") for e in events]

    return RetryChain(
        chain_id=chain_id,
        session_id=first.get("_session_id", "unknown"),
        original_event_id=first.get("event_id", ""),
        event_type=first.get("event_type", "unknown"),
        tool_name=tool_name,
        model=model,
        attempt_count=len(events),
        outcome=outcome,
        total_duration_ms=total_dur,
        original_duration_ms=orig_dur,
        retry_duration_ms=total_dur - orig_dur,
        tokens_original=orig_tokens,
        tokens_retry=retry_tokens,
        error_types=errors,
        timestamps=timestamps,
    )


def _dur(ev: dict[str, Any]) -> float:
    return ev.get("duration_ms") or 0.0


def _tokens(ev: dict[str, Any]) -> int:
    return (ev.get("tokens_in") or 0) + (ev.get("tokens_out") or 0)


def _parse_ts_ms(ts: str) -> float:
    """Parse ISO timestamp to milliseconds since epoch (best-effort)."""
    if not ts:
        return 0.0
    try:
        from datetime import datetime
        # Handle Z suffix
        clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return 0.0
