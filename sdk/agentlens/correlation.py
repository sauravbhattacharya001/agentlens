"""Session Correlation Engine — cross-session pattern detection.

Discovers relationships between concurrent agent sessions: temporal
overlap, shared resources, error propagation, synchronization points,
and resource contention.  Useful for multi-agent systems where agents
interact through shared tools, models, or data.

Usage::

    from agentlens.correlation import SessionCorrelator

    correlator = SessionCorrelator()
    correlator.add_sessions(sessions)      # list of Session objects

    report = correlator.correlate()
    print(report.render())

    # Specific analyses
    overlaps = correlator.find_overlaps()
    contention = correlator.detect_contention()
    propagation = correlator.trace_error_propagation()
    sync_points = correlator.find_sync_points()
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CorrelationKind(str, Enum):
    """Types of cross-session correlations."""
    TEMPORAL_OVERLAP = "temporal_overlap"
    SHARED_RESOURCE = "shared_resource"
    ERROR_PROPAGATION = "error_propagation"
    SYNC_POINT = "sync_point"
    RESOURCE_CONTENTION = "resource_contention"
    MODEL_HOTSPOT = "model_hotspot"
    CASCADING_FAILURE = "cascading_failure"


class ContentionSeverity(str, Enum):
    """Severity of resource contention."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PropagationDirection(str, Enum):
    """Direction of error propagation."""
    FORWARD = "forward"    # session A error → session B error
    BACKWARD = "backward"  # session B error → session A error
    MUTUAL = "mutual"      # both directions


@dataclass
class SessionWindow:
    """Time window for a session."""
    session_id: str
    agent_name: str
    start: datetime
    end: Optional[datetime]
    event_count: int = 0
    error_count: int = 0
    models_used: Set[str] = field(default_factory=set)
    tools_used: Set[str] = field(default_factory=set)


@dataclass
class TemporalOverlap:
    """Two sessions that overlap in time."""
    session_a: str
    session_b: str
    overlap_start: datetime
    overlap_end: datetime
    overlap_ms: float
    overlap_pct_a: float  # what % of session A's duration overlaps
    overlap_pct_b: float

    def to_dict(self) -> dict:
        return {
            "session_a": self.session_a,
            "session_b": self.session_b,
            "overlap_start": self.overlap_start.isoformat(),
            "overlap_end": self.overlap_end.isoformat(),
            "overlap_ms": round(self.overlap_ms, 1),
            "overlap_pct_a": round(self.overlap_pct_a, 1),
            "overlap_pct_b": round(self.overlap_pct_b, 1),
        }


@dataclass
class SharedResource:
    """A resource (tool or model) used by multiple sessions."""
    resource_name: str
    resource_type: str  # "tool" or "model"
    session_ids: List[str]
    total_uses: int
    concurrent_uses: int  # max simultaneous uses within overlap windows

    def to_dict(self) -> dict:
        return {
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "session_count": len(self.session_ids),
            "total_uses": self.total_uses,
            "concurrent_uses": self.concurrent_uses,
        }


@dataclass
class ErrorPropagation:
    """Detected error propagation between sessions."""
    source_session: str
    target_session: str
    source_error_time: datetime
    target_error_time: datetime
    delay_ms: float
    direction: PropagationDirection
    shared_resources: List[str]
    confidence: float  # 0.0-1.0

    def to_dict(self) -> dict:
        return {
            "source_session": self.source_session,
            "target_session": self.target_session,
            "delay_ms": round(self.delay_ms, 1),
            "direction": self.direction.value,
            "shared_resources": self.shared_resources,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class SyncPoint:
    """A moment where multiple sessions synchronize on the same resource."""
    timestamp: datetime
    resource_name: str
    session_ids: List[str]
    window_ms: float  # how tight the synchronization is

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "resource_name": self.resource_name,
            "session_ids": self.session_ids,
            "window_ms": round(self.window_ms, 1),
        }


@dataclass
class ResourceContention:
    """Detected resource contention between sessions."""
    resource_name: str
    resource_type: str
    sessions_involved: List[str]
    contention_window_start: datetime
    contention_window_end: datetime
    concurrent_count: int
    severity: ContentionSeverity
    estimated_delay_ms: float

    def to_dict(self) -> dict:
        return {
            "resource_name": self.resource_name,
            "resource_type": self.resource_type,
            "sessions_involved": self.sessions_involved,
            "window_start": self.contention_window_start.isoformat(),
            "window_end": self.contention_window_end.isoformat(),
            "concurrent_count": self.concurrent_count,
            "severity": self.severity.value,
            "estimated_delay_ms": round(self.estimated_delay_ms, 1),
        }


@dataclass
class CorrelationReport:
    """Complete cross-session correlation analysis."""
    session_count: int = 0
    total_events: int = 0
    analysis_window_start: Optional[datetime] = None
    analysis_window_end: Optional[datetime] = None
    overlaps: List[TemporalOverlap] = field(default_factory=list)
    shared_resources: List[SharedResource] = field(default_factory=list)
    error_propagations: List[ErrorPropagation] = field(default_factory=list)
    sync_points: List[SyncPoint] = field(default_factory=list)
    contentions: List[ResourceContention] = field(default_factory=list)
    model_hotspots: Dict[str, int] = field(default_factory=dict)

    @property
    def total_correlations(self) -> int:
        return (len(self.overlaps) + len(self.shared_resources) +
                len(self.error_propagations) + len(self.sync_points) +
                len(self.contentions))

    @property
    def has_contention(self) -> bool:
        return len(self.contentions) > 0

    @property
    def has_error_propagation(self) -> bool:
        return len(self.error_propagations) > 0

    @property
    def max_contention_severity(self) -> Optional[ContentionSeverity]:
        if not self.contentions:
            return None
        severity_order = [ContentionSeverity.LOW, ContentionSeverity.MEDIUM,
                          ContentionSeverity.HIGH, ContentionSeverity.CRITICAL]
        return max(self.contentions, key=lambda c: severity_order.index(c.severity)).severity

    @property
    def avg_overlap_pct(self) -> float:
        if not self.overlaps:
            return 0.0
        return statistics.mean(
            [(o.overlap_pct_a + o.overlap_pct_b) / 2 for o in self.overlaps]
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "session_count": self.session_count,
            "total_events": self.total_events,
            "temporal_overlaps": len(self.overlaps),
            "shared_resources": len(self.shared_resources),
            "error_propagations": len(self.error_propagations),
            "sync_points": len(self.sync_points),
            "resource_contentions": len(self.contentions),
            "model_hotspots": len(self.model_hotspots),
            "has_contention": self.has_contention,
            "has_error_propagation": self.has_error_propagation,
            "max_contention_severity": self.max_contention_severity.value if self.max_contention_severity else None,
            "avg_overlap_pct": round(self.avg_overlap_pct, 1),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "overlaps": [o.to_dict() for o in self.overlaps],
            "shared_resources": [r.to_dict() for r in self.shared_resources],
            "error_propagations": [p.to_dict() for p in self.error_propagations],
            "sync_points": [s.to_dict() for s in self.sync_points],
            "contentions": [c.to_dict() for c in self.contentions],
            "model_hotspots": self.model_hotspots,
        }

    def render(self) -> str:
        """Human-readable report."""
        lines = [
            "═══ Session Correlation Report ═══",
            f"Sessions analyzed: {self.session_count}",
            f"Total events: {self.total_events}",
        ]
        if self.analysis_window_start and self.analysis_window_end:
            dur = (self.analysis_window_end - self.analysis_window_start).total_seconds()
            lines.append(f"Analysis window: {dur:.0f}s")
        lines.append(f"Total correlations found: {self.total_correlations}")
        lines.append("")

        if self.overlaps:
            lines.append(f"── Temporal Overlaps ({len(self.overlaps)}) ──")
            for o in self.overlaps[:10]:
                lines.append(
                    f"  {o.session_a[:8]}…↔{o.session_b[:8]}…  "
                    f"{o.overlap_ms:.0f}ms  "
                    f"({o.overlap_pct_a:.0f}%/{o.overlap_pct_b:.0f}%)"
                )
            lines.append("")

        if self.shared_resources:
            lines.append(f"── Shared Resources ({len(self.shared_resources)}) ──")
            for r in self.shared_resources[:10]:
                lines.append(
                    f"  {r.resource_type}:{r.resource_name}  "
                    f"sessions={len(r.session_ids)}  "
                    f"uses={r.total_uses}  "
                    f"max_concurrent={r.concurrent_uses}"
                )
            lines.append("")

        if self.error_propagations:
            lines.append(f"── Error Propagation ({len(self.error_propagations)}) ──")
            for p in self.error_propagations[:10]:
                lines.append(
                    f"  {p.source_session[:8]}…→{p.target_session[:8]}…  "
                    f"delay={p.delay_ms:.0f}ms  "
                    f"confidence={p.confidence:.0%}  "
                    f"via={','.join(p.shared_resources[:3])}"
                )
            lines.append("")

        if self.sync_points:
            lines.append(f"── Sync Points ({len(self.sync_points)}) ──")
            for s in self.sync_points[:10]:
                lines.append(
                    f"  {s.resource_name}  "
                    f"sessions={len(s.session_ids)}  "
                    f"window={s.window_ms:.0f}ms"
                )
            lines.append("")

        if self.contentions:
            lines.append(f"── Resource Contention ({len(self.contentions)}) ──")
            for c in self.contentions[:10]:
                lines.append(
                    f"  [{c.severity.value.upper()}] {c.resource_type}:{c.resource_name}  "
                    f"concurrent={c.concurrent_count}  "
                    f"delay≈{c.estimated_delay_ms:.0f}ms"
                )
            lines.append("")

        if self.model_hotspots:
            lines.append(f"── Model Hotspots ({len(self.model_hotspots)}) ──")
            for model, count in sorted(self.model_hotspots.items(),
                                       key=lambda x: -x[1])[:5]:
                lines.append(f"  {model}: {count} concurrent uses")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Correlator
# ---------------------------------------------------------------------------


class SessionCorrelator:
    """Cross-session correlation engine.

    Parameters
    ----------
    sync_window_ms : float
        Maximum time gap (ms) between events on the same resource to be
        considered a sync point.  Default 500ms.
    contention_threshold : int
        Minimum concurrent resource uses to flag contention.  Default 3.
    error_propagation_window_ms : float
        Maximum delay (ms) between errors in different sessions to
        consider propagation.  Default 5000ms (5s).
    """

    def __init__(
        self,
        sync_window_ms: float = 500.0,
        contention_threshold: int = 3,
        error_propagation_window_ms: float = 5000.0,
    ) -> None:
        self.sync_window_ms = sync_window_ms
        self.contention_threshold = contention_threshold
        self.error_propagation_window_ms = error_propagation_window_ms
        self._sessions: list = []
        self._windows: List[SessionWindow] = []
        self._session_resources: Dict[str, Set[str]] = {}  # sid -> resource names
        self._event_index_cache: Optional[Dict[str, Any]] = None

    # -- Ingestion --------------------------------------------------------

    def add_session(self, session: Any) -> None:
        """Add a single session (agentlens.models.Session)."""
        self._sessions.append(session)
        window = self._build_window(session)
        self._windows.append(window)
        # Pre-build resource index for fast shared-resource lookups
        resources: Set[str] = set()
        for e in getattr(session, "events", []):
            for res, _ in self._event_resources(e):
                resources.add(res)
        self._session_resources[window.session_id] = resources
        # Invalidate cached event index when sessions change
        self._event_index_cache = None

    def add_sessions(self, sessions: list) -> None:
        """Add multiple sessions."""
        for s in sessions:
            self.add_session(s)

    def clear(self) -> None:
        """Remove all sessions."""
        self._sessions.clear()
        self._windows.clear()
        self._session_resources.clear()
        self._event_index_cache = None

    @staticmethod
    def _build_window(session: Any) -> SessionWindow:
        """Extract timing/resource metadata from a session."""
        events = getattr(session, "events", [])
        models: set = set()
        tools: set = set()
        errors = 0
        for e in events:
            m = getattr(e, "model", None)
            if m:
                models.add(m)
            tc = getattr(e, "tool_call", None)
            if tc:
                tools.add(getattr(tc, "tool_name", "unknown"))
            et = getattr(e, "event_type", "")
            if "error" in et.lower():
                errors += 1

        start = getattr(session, "started_at", datetime.now(timezone.utc))
        end = getattr(session, "ended_at", None)
        if end is None and events:
            # Estimate end from the event with the latest timestamp
            last_event = max(events, key=lambda e: getattr(e, "timestamp", start))
            last_ts = getattr(last_event, "timestamp", start)
            last_dur = getattr(last_event, "duration_ms", 0) or 0
            end = last_ts + timedelta(milliseconds=last_dur)

        return SessionWindow(
            session_id=getattr(session, "session_id", ""),
            agent_name=getattr(session, "agent_name", "unknown"),
            start=start,
            end=end,
            event_count=len(events),
            error_count=errors,
            models_used=models,
            tools_used=tools,
        )

    # -- Analysis ---------------------------------------------------------

    def find_overlaps(self) -> List[TemporalOverlap]:
        """Find all pairs of sessions that overlap in time.

        Uses a sweep-line approach: sort sessions by start time, then
        maintain a set of "active" sessions (those whose end > current
        start).  Only active sessions can overlap with the current one,
        pruning the O(n²) comparison to O(n·k) where k is the average
        number of concurrently active sessions.
        """
        if len(self._windows) < 2:
            return []

        now = datetime.now(timezone.utc)
        sorted_windows = sorted(self._windows, key=lambda w: w.start)
        overlaps: List[TemporalOverlap] = []

        # Active windows sorted by end time for efficient pruning
        active: List[SessionWindow] = []

        for win in sorted_windows:
            # Prune expired windows (end <= current start)
            active = [a for a in active if (a.end or now) > win.start]

            # All remaining active windows overlap with win
            for a in active:
                overlap = self._compute_overlap(a, win)
                if overlap:
                    overlaps.append(overlap)

            active.append(win)

        return overlaps

    def _compute_overlap(
        self, a: SessionWindow, b: SessionWindow
    ) -> Optional[TemporalOverlap]:
        """Compute temporal overlap between two session windows."""
        a_end = a.end or datetime.now(timezone.utc)
        b_end = b.end or datetime.now(timezone.utc)

        overlap_start = max(a.start, b.start)
        overlap_end = min(a_end, b_end)

        if overlap_start >= overlap_end:
            return None

        overlap_ms = (overlap_end - overlap_start).total_seconds() * 1000
        a_dur = max((a_end - a.start).total_seconds() * 1000, 1)
        b_dur = max((b_end - b.start).total_seconds() * 1000, 1)

        return TemporalOverlap(
            session_a=a.session_id,
            session_b=b.session_id,
            overlap_start=overlap_start,
            overlap_end=overlap_end,
            overlap_ms=overlap_ms,
            overlap_pct_a=min(100.0, overlap_ms / a_dur * 100),
            overlap_pct_b=min(100.0, overlap_ms / b_dur * 100),
        )

    def _build_event_index(
        self,
    ) -> Dict[
        str,
        Any,
    ]:
        """Build a shared index of resource usage across all sessions.

        Scans every event once and produces aggregated data structures
        used by find_shared_resources(), detect_contention(), and
        find_model_hotspots().  Cached and invalidated when sessions
        change, eliminating 3 redundant full-event scans in correlate().

        Returns a dict with keys:
        - tool_sessions: Dict[str, Set[str]]
        - model_sessions: Dict[str, Set[str]]
        - tool_counts: Counter
        - model_counts: Counter
        - resource_intervals: Dict[(resource, type), List[(start, end, sid)]]
        - model_uses: Dict[str, List[(timestamp, duration)]]
        """
        if self._event_index_cache is not None:
            return self._event_index_cache

        tool_sessions: Dict[str, Set[str]] = defaultdict(set)
        model_sessions: Dict[str, Set[str]] = defaultdict(set)
        tool_counts: Counter = Counter()
        model_counts: Counter = Counter()
        resource_intervals: Dict[
            Tuple[str, str], List[Tuple[datetime, datetime, str]]
        ] = defaultdict(list)
        model_uses: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

        now = datetime.now(timezone.utc)

        for session, window in zip(self._sessions, self._windows):
            sid = window.session_id
            for e in getattr(session, "events", []):
                ts = getattr(e, "timestamp", now)
                dur = getattr(e, "duration_ms", 100) or 100
                end = ts + timedelta(milliseconds=dur)

                m = getattr(e, "model", None)
                if m:
                    model_sessions[m].add(sid)
                    model_counts[m] += 1
                    resource_intervals[(m, "model")].append((ts, end, sid))
                    model_uses[m].append((ts, dur))

                tc = getattr(e, "tool_call", None)
                if tc:
                    name = getattr(tc, "tool_name", "unknown")
                    tool_sessions[name].add(sid)
                    tool_counts[name] += 1
                    resource_intervals[(name, "tool")].append((ts, end, sid))

        index = {
            "tool_sessions": tool_sessions,
            "model_sessions": model_sessions,
            "tool_counts": tool_counts,
            "model_counts": model_counts,
            "resource_intervals": resource_intervals,
            "model_uses": model_uses,
        }
        self._event_index_cache = index
        return index

    def find_shared_resources(self) -> List[SharedResource]:
        """Find tools and models shared across sessions.

        Uses the shared event index to avoid re-scanning all events.
        """
        idx = self._build_event_index()
        tool_sessions = idx["tool_sessions"]
        model_sessions = idx["model_sessions"]
        tool_counts = idx["tool_counts"]
        model_counts = idx["model_counts"]

        results: List[SharedResource] = []

        for tool, sids in tool_sessions.items():
            if len(sids) >= 2:
                concurrent = self._max_concurrent_usage(tool, "tool")
                results.append(SharedResource(
                    resource_name=tool,
                    resource_type="tool",
                    session_ids=sorted(sids),
                    total_uses=tool_counts[tool],
                    concurrent_uses=concurrent,
                ))

        for model, sids in model_sessions.items():
            if len(sids) >= 2:
                concurrent = self._max_concurrent_usage(model, "model")
                results.append(SharedResource(
                    resource_name=model,
                    resource_type="model",
                    session_ids=sorted(sids),
                    total_uses=model_counts[model],
                    concurrent_uses=concurrent,
                ))

        return sorted(results, key=lambda r: -r.total_uses)

    def _max_concurrent_usage(self, resource: str, rtype: str) -> int:
        """Estimate max concurrent uses of a resource across sessions.

        Reuses the shared event index (resource_intervals) built by
        _build_event_index() instead of re-scanning all events per
        resource.  This reduces find_shared_resources() from
        O(resources × total_events) to O(total_events).
        """
        idx = self._build_event_index()
        raw_intervals = idx["resource_intervals"].get((resource, rtype), [])
        if not raw_intervals:
            return 0

        # Build sweep-line events from pre-collected intervals
        sweep: List[Tuple[datetime, int]] = []
        for start, end, _sid in raw_intervals:
            sweep.append((start, 1))
            sweep.append((end, -1))

        sweep.sort(key=lambda x: x[0])
        max_concurrent = 0
        current = 0
        for _, delta in sweep:
            current += delta
            max_concurrent = max(max_concurrent, current)
        return max_concurrent

    def detect_contention(self) -> List[ResourceContention]:
        """Detect resource contention (multiple sessions competing).

        Uses a sweep-line algorithm per resource for O(n log n) performance
        instead of the previous O(n²) per-event approach.
        Reuses the shared event index to avoid re-scanning all events.
        """
        idx = self._build_event_index()
        resource_intervals = idx["resource_intervals"]

        contentions: List[ResourceContention] = []
        seen: set = set()

        for (resource, rtype), intervals in resource_intervals.items():
            if len(intervals) < self.contention_threshold:
                continue

            # Build sweep-line events: +1 at start, -1 at end
            sweep: List[Tuple[datetime, int, str]] = []
            for start, end, sid in intervals:
                sweep.append((start, 1, sid))
                sweep.append((end, -1, sid))
            sweep.sort(key=lambda x: (x[0], x[1]))

            current = 0
            active_sessions: Dict[str, int] = defaultdict(int)
            window_start: Optional[datetime] = None
            peak_sessions: Set[str] = set()

            for ts, delta, sid in sweep:
                if delta == 1:
                    active_sessions[sid] += 1
                    current += 1
                    if current >= self.contention_threshold and window_start is None:
                        window_start = ts
                    if current >= self.contention_threshold:
                        # Snapshot active sessions at each new peak or same level
                        peak_sessions = set(active_sessions.keys())
                else:
                    active_sessions[sid] -= 1
                    if active_sessions[sid] == 0:
                        del active_sessions[sid]

                    if current >= self.contention_threshold and (current + delta) < self.contention_threshold:
                        # Contention window closing — but we already decremented conceptually
                        pass

                    current -= 1

                    # Emit contention when we drop below threshold
                    if current < self.contention_threshold and window_start is not None:
                        key = (resource, round(window_start.timestamp(), 1))
                        if key not in seen:
                            seen.add(key)
                            window_end = ts
                            # Use the peak_sessions snapshot captured at peak
                            # instead of re-scanning all intervals O(n).
                            involved = peak_sessions.copy()
                            contentions.append(ResourceContention(
                                resource_name=resource,
                                resource_type=rtype,
                                sessions_involved=sorted(involved),
                                contention_window_start=window_start,
                                contention_window_end=window_end,
                                concurrent_count=len(involved),
                                severity=self._contention_severity(len(involved)),
                                estimated_delay_ms=len(involved) * 50.0,
                            ))
                        window_start = None

            # Handle case where contention persists to the end
            if window_start is not None:
                key = (resource, round(window_start.timestamp(), 1))
                if key not in seen:
                    seen.add(key)
                    last_ts = sweep[-1][0] if sweep else window_start
                    involved_final = peak_sessions.copy()
                    contentions.append(ResourceContention(
                        resource_name=resource,
                        resource_type=rtype,
                        sessions_involved=sorted(involved_final),
                        contention_window_start=window_start,
                        contention_window_end=last_ts,
                        concurrent_count=len(involved_final),
                        severity=self._contention_severity(len(involved_final)),
                        estimated_delay_ms=len(involved_final) * 50.0,
                    ))

        return sorted(contentions, key=lambda c: -c.concurrent_count)

    @staticmethod
    def _event_resources(event: Any) -> List[Tuple[str, str]]:
        """Extract (resource_name, type) pairs from an event."""
        resources = []
        m = getattr(event, "model", None)
        if m:
            resources.append((m, "model"))
        tc = getattr(event, "tool_call", None)
        if tc:
            resources.append((getattr(tc, "tool_name", "unknown"), "tool"))
        return resources

    @staticmethod
    def _contention_severity(concurrent: int) -> ContentionSeverity:
        if concurrent >= 8:
            return ContentionSeverity.CRITICAL
        elif concurrent >= 5:
            return ContentionSeverity.HIGH
        elif concurrent >= 3:
            return ContentionSeverity.MEDIUM
        return ContentionSeverity.LOW

    def trace_error_propagation(self) -> List[ErrorPropagation]:
        """Detect potential error propagation between sessions.

        Looks for sessions that share resources and where an error in one
        session is followed by an error in another within the propagation
        window.

        Uses sorted timestamps + bisect for O(E·log E) per session pair
        instead of the previous O(E_src × E_tgt) brute-force.
        """
        import bisect

        propagations: List[ErrorPropagation] = []
        # Collect error timestamps per session, pre-sorted
        error_timestamps: Dict[str, List[datetime]] = {}
        error_events_raw: Dict[str, list] = {}

        for session in self._sessions:
            sid = getattr(session, "session_id", "")
            errors = []
            for e in getattr(session, "events", []):
                if "error" in getattr(e, "event_type", "").lower():
                    errors.append(e)
            if errors:
                error_events_raw[sid] = errors
                timestamps = sorted(
                    getattr(e, "timestamp", datetime.now(timezone.utc))
                    for e in errors
                )
                error_timestamps[sid] = timestamps

        sids = list(error_timestamps.keys())
        window_ms = self.error_propagation_window_ms
        window_td = timedelta(milliseconds=window_ms)

        for i in range(len(sids)):
            for j in range(len(sids)):
                if i == j:
                    continue
                src = sids[i]
                tgt = sids[j]
                shared = self._shared_resources_between(src, tgt)
                if not shared:
                    continue

                tgt_ts_list = error_timestamps[tgt]

                for src_ts in error_timestamps[src]:
                    # Binary search for target errors in (src_ts, src_ts + window]
                    lo = bisect.bisect_right(tgt_ts_list, src_ts)
                    hi = bisect.bisect_right(tgt_ts_list, src_ts + window_td)

                    for k in range(lo, hi):
                        tgt_ts = tgt_ts_list[k]
                        delay_ms = (tgt_ts - src_ts).total_seconds() * 1000
                        confidence = max(0.1, 1.0 - (delay_ms / window_ms))
                        propagations.append(ErrorPropagation(
                            source_session=src,
                            target_session=tgt,
                            source_error_time=src_ts,
                            target_error_time=tgt_ts,
                            delay_ms=delay_ms,
                            direction=PropagationDirection.FORWARD,
                            shared_resources=shared,
                            confidence=confidence,
                        ))

        # Deduplicate: keep highest confidence per session pair
        best: Dict[Tuple[str, str], ErrorPropagation] = {}
        for p in propagations:
            key = (p.source_session, p.target_session)
            if key not in best or p.confidence > best[key].confidence:
                best[key] = p

        # Check for mutual propagation
        results: List[ErrorPropagation] = []
        seen_pairs: set = set()
        for key, prop in best.items():
            reverse_key = (key[1], key[0])
            if reverse_key in best and key not in seen_pairs:
                # Mutual propagation — merge into one with higher confidence
                reverse = best[reverse_key]
                merged = ErrorPropagation(
                    source_session=prop.source_session,
                    target_session=prop.target_session,
                    source_error_time=prop.source_error_time,
                    target_error_time=prop.target_error_time,
                    delay_ms=min(prop.delay_ms, reverse.delay_ms),
                    direction=PropagationDirection.MUTUAL,
                    shared_resources=prop.shared_resources,
                    confidence=max(prop.confidence, reverse.confidence),
                )
                results.append(merged)
                seen_pairs.add(key)
                seen_pairs.add(reverse_key)
            elif key not in seen_pairs:
                results.append(prop)
                seen_pairs.add(key)

        return sorted(results, key=lambda p: -p.confidence)

    def _shared_resources_between(self, sid_a: str, sid_b: str) -> List[str]:
        """Find resources shared between two sessions using pre-built index."""
        a_resources = self._session_resources.get(sid_a, set())
        b_resources = self._session_resources.get(sid_b, set())
        return sorted(a_resources & b_resources)

    def find_sync_points(self) -> List[SyncPoint]:
        """Find moments where multiple sessions use the same resource
        within a tight window (potential synchronization points)."""
        sync_points: List[SyncPoint] = []

        # Collect all resource usage timestamps
        resource_uses: Dict[str, List[Tuple[datetime, str]]] = defaultdict(list)
        for session in self._sessions:
            sid = getattr(session, "session_id", "")
            for e in getattr(session, "events", []):
                ts = getattr(e, "timestamp", datetime.now(timezone.utc))
                for res, _ in self._event_resources(e):
                    resource_uses[res].append((ts, sid))

        window = timedelta(milliseconds=self.sync_window_ms)

        for resource, uses in resource_uses.items():
            if len(uses) < 2:
                continue
            uses.sort(key=lambda x: x[0])

            # Sliding window to find clusters
            i = 0
            while i < len(uses):
                cluster_sessions: Set[str] = {uses[i][1]}
                cluster_start = uses[i][0]
                j = i + 1
                while j < len(uses) and (uses[j][0] - cluster_start) <= window:
                    cluster_sessions.add(uses[j][1])
                    j += 1

                if len(cluster_sessions) >= 2:
                    cluster_end = uses[min(j - 1, len(uses) - 1)][0]
                    window_ms = (cluster_end - cluster_start).total_seconds() * 1000
                    sync_points.append(SyncPoint(
                        timestamp=cluster_start,
                        resource_name=resource,
                        session_ids=sorted(cluster_sessions),
                        window_ms=window_ms,
                    ))
                    i = j  # Skip past this cluster
                else:
                    i += 1

        return sorted(sync_points, key=lambda s: s.timestamp)

    def find_model_hotspots(self) -> Dict[str, int]:
        """Find models with highest concurrent usage across sessions.

        Reuses the shared event index to avoid re-scanning all events.
        """
        idx = self._build_event_index()
        model_uses = idx["model_uses"]

        hotspots: Dict[str, int] = {}
        for model, uses in model_uses.items():
            if len(uses) < 2:
                continue
            # Sweep line for max concurrent
            events: List[Tuple[datetime, int]] = []
            for ts, dur in uses:
                events.append((ts, 1))
                events.append((ts + timedelta(milliseconds=dur), -1))
            events.sort(key=lambda x: x[0])
            current = 0
            peak = 0
            for _, delta in events:
                current += delta
                peak = max(peak, current)
            if peak >= 2:
                hotspots[model] = peak

        return hotspots

    # -- Full analysis ----------------------------------------------------

    def correlate(self) -> CorrelationReport:
        """Run all correlation analyses and return a unified report."""
        report = CorrelationReport(
            session_count=len(self._sessions),
            total_events=sum(w.event_count for w in self._windows),
        )

        if self._windows:
            report.analysis_window_start = min(w.start for w in self._windows)
            ends = [w.end or datetime.now(timezone.utc) for w in self._windows]
            report.analysis_window_end = max(ends)

        report.overlaps = self.find_overlaps()
        report.shared_resources = self.find_shared_resources()
        report.error_propagations = self.trace_error_propagation()
        report.sync_points = self.find_sync_points()
        report.contentions = self.detect_contention()
        report.model_hotspots = self.find_model_hotspots()

        return report

    # -- Static helpers ---------------------------------------------------

    @staticmethod
    def compare(
        sessions: list,
        sync_window_ms: float = 500.0,
        contention_threshold: int = 3,
        error_propagation_window_ms: float = 5000.0,
    ) -> CorrelationReport:
        """One-shot correlation analysis."""
        c = SessionCorrelator(
            sync_window_ms=sync_window_ms,
            contention_threshold=contention_threshold,
            error_propagation_window_ms=error_propagation_window_ms,
        )
        c.add_sessions(sessions)
        return c.correlate()
