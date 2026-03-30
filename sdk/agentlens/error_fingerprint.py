"""Error Fingerprinting for AgentLens.

Automatic error grouping and tracking for agent systems.  When agents
fail, the raw error messages are noisy — the same root cause produces
slightly different stack traces, timestamps, and context.  This module
groups errors into *fingerprints* (clusters) by normalising stack traces,
extracting message templates, and computing structural signatures.

Features:

- **Automatic fingerprinting** — groups errors by (type, template, frame signature)
- **New vs. recurring classification** — detect first-seen errors instantly
- **Occurrence tracking** — count, first/last seen, frequency, trend
- **Error cluster reports** — top errors, trend direction, session impact
- **Noise reduction** — normalise hex addresses, UUIDs, timestamps, numbers
- **Session correlation** — which sessions are most error-prone
- **Resolution tracking** — mark fingerprints as resolved, detect regressions

Example::

    from agentlens.error_fingerprint import ErrorFingerprinter

    fp = ErrorFingerprinter()

    # Record errors from agent events
    fp.record("ValueError", "expected 3 items, got 5",
              session_id="s1", stack_trace="...")
    fp.record("ValueError", "expected 2 items, got 8",
              session_id="s2", stack_trace="...")

    # These group together because the message template matches
    report = fp.report()
    print(f"Unique errors:  {report.unique_count}")
    print(f"Total errors:   {report.total_count}")
    print(f"New this window: {len(report.new_fingerprints)}")

    for cluster in report.top_clusters:
        print(f"  [{cluster.trend.value}] {cluster.fingerprint_id[:8]} "
              f"{cluster.error_type}: {cluster.template}  "
              f"({cluster.occurrence_count}x)")
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Patterns replaced during message normalisation
_NORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # UUIDs (8-4-4-4-12 hex)
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<UUID>"),
    # Hex addresses / object ids  (0x...)
    (re.compile(r"0x[0-9a-fA-F]{4,16}"), "<HEX>"),
    # ISO timestamps (must precede number replacement)
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?"), "<TIMESTAMP>"),
    # File paths (must precede number replacement to avoid splitting
    # paths like /usr/lib/python3.12/site.py at version numbers)
    (re.compile(r"(?:/[\w./-]+){2,}"), "<PATH>"),
    (re.compile(r"[A-Z]:\\[\w.\\-]+"), "<PATH>"),
    # Quoted strings (single or double)
    (re.compile(r"'[^']{1,120}'"), "'<STR>'"),
    (re.compile(r'"[^"]{1,120}"'), '"<STR>"'),
    # Bare integers ≥ 2 digits (but not inside words)
    (re.compile(r"(?<![a-zA-Z_])\d{2,}(?![a-zA-Z_])"), "<NUM>"),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Trend(Enum):
    """Trend direction for an error fingerprint."""
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"
    NEW = "new"


class Resolution(Enum):
    """Resolution status for a fingerprint."""
    OPEN = "open"
    RESOLVED = "resolved"
    REGRESSED = "regressed"       # was resolved, appeared again
    IGNORED = "ignored"


@dataclass
class ErrorOccurrence:
    """A single recorded error occurrence."""
    error_type: str
    message: str
    session_id: str
    timestamp: datetime
    stack_trace: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint_id: str = ""


@dataclass
class ErrorCluster:
    """A group of errors sharing the same fingerprint."""
    fingerprint_id: str
    error_type: str
    template: str                          # normalised message template
    frame_signature: str                   # normalised stack top frames
    occurrence_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    session_ids: set[str] = field(default_factory=set)
    recent_counts: list[int] = field(default_factory=list)
    trend: Trend = Trend.NEW
    resolution: Resolution = Resolution.OPEN
    sample_message: str = ""               # first raw message for display
    sample_stack: str | None = None        # first raw stack for display


@dataclass
class ErrorReport:
    """Summary report of all error fingerprints."""
    unique_count: int = 0                  # distinct fingerprints
    total_count: int = 0                   # total error occurrences
    top_clusters: list[ErrorCluster] = field(default_factory=list)
    new_fingerprints: list[ErrorCluster] = field(default_factory=list)
    resolved_fingerprints: list[str] = field(default_factory=list)
    regressed_fingerprints: list[ErrorCluster] = field(default_factory=list)
    sessions_affected: int = 0
    most_affected_sessions: list[tuple[str, int]] = field(default_factory=list)
    error_rate: float = 0.0               # errors per session (if known)
    window_start: datetime | None = None
    window_end: datetime | None = None

    def render(self) -> str:
        """Render a human-readable text report."""
        lines: list[str] = []
        lines.append("═══ Error Fingerprint Report ═══")
        lines.append(f"Unique errors:    {self.unique_count}")
        lines.append(f"Total occurrences:{self.total_count}")
        lines.append(f"Sessions affected:{self.sessions_affected}")
        if self.new_fingerprints:
            lines.append(f"New errors:       {len(self.new_fingerprints)}")
        if self.regressed_fingerprints:
            lines.append(f"Regressions:      {len(self.regressed_fingerprints)}")
        lines.append("")

        if self.top_clusters:
            lines.append("Top Error Clusters:")
            for i, c in enumerate(self.top_clusters[:10], 1):
                trend_icon = {"rising": "↑", "falling": "↓",
                              "stable": "─", "new": "★"}.get(
                                  c.trend.value, "?")
                lines.append(
                    f"  {i}. [{trend_icon}] {c.error_type}: {c.template}"
                    f"  ({c.occurrence_count}x, "
                    f"{len(c.session_ids)} sessions)")

        if self.most_affected_sessions:
            lines.append("")
            lines.append("Most Affected Sessions:")
            for sid, count in self.most_affected_sessions[:5]:
                lines.append(f"  {sid}: {count} errors")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core fingerprinter
# ---------------------------------------------------------------------------

class ErrorFingerprinter:
    """Groups agent errors into fingerprints for noise-reduced monitoring.

    Parameters
    ----------
    window_buckets : int
        Number of time buckets for trend calculation (default 5).
    top_frames : int
        Number of stack frames to include in the frame signature
        (default 3 — the top of the call stack is most distinctive).
    """

    def __init__(
        self,
        *,
        window_buckets: int = 5,
        top_frames: int = 3,
    ) -> None:
        self._window_buckets = max(2, window_buckets)
        self._top_frames = max(1, top_frames)
        self._clusters: dict[str, ErrorCluster] = {}
        self._occurrences: list[ErrorOccurrence] = []
        self._resolved: set[str] = set()           # manually resolved IDs
        self._session_error_counts: dict[str, int] = defaultdict(int)
        self._total_sessions: int = 0               # for error_rate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        error_type: str,
        message: str,
        *,
        session_id: str = "",
        stack_trace: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """Record an error occurrence and return its fingerprint ID.

        Parameters
        ----------
        error_type : str
            Exception class name (e.g. ``"ValueError"``).
        message : str
            The error message string.
        session_id : str
            Agent session that produced this error.
        stack_trace : str, optional
            Full stack trace as a string.
        metadata : dict, optional
            Arbitrary context (tool name, model, etc.).
        timestamp : datetime, optional
            When the error occurred (defaults to now).

        Returns
        -------
        str
            The fingerprint ID for this error group.
        """
        ts = timestamp or _utcnow()
        occ = ErrorOccurrence(
            error_type=error_type,
            message=message,
            session_id=session_id,
            timestamp=ts,
            stack_trace=stack_trace,
            metadata=metadata or {},
        )
        self._occurrences.append(occ)

        template = self._normalise_message(message)
        frame_sig = self._normalise_stack(stack_trace)
        fp_id = self._compute_fingerprint(error_type, template, frame_sig)
        occ.fingerprint_id = fp_id

        if fp_id not in self._clusters:
            self._clusters[fp_id] = ErrorCluster(
                fingerprint_id=fp_id,
                error_type=error_type,
                template=template,
                frame_signature=frame_sig,
                sample_message=message,
                sample_stack=stack_trace,
            )

        cluster = self._clusters[fp_id]
        cluster.occurrence_count += 1
        if cluster.first_seen is None or ts < cluster.first_seen:
            cluster.first_seen = ts
        if cluster.last_seen is None or ts > cluster.last_seen:
            cluster.last_seen = ts
        cluster.session_ids.add(session_id)

        # Detect regression: was resolved but appeared again
        if fp_id in self._resolved:
            cluster.resolution = Resolution.REGRESSED
            self._resolved.discard(fp_id)

        if session_id:
            self._session_error_counts[session_id] += 1

        return fp_id

    def record_from_event(
        self,
        event: dict[str, Any],
        *,
        session_id: str = "",
    ) -> str | None:
        """Record an error from an AgentEvent-like dict.

        Looks for ``event_type == "error"`` or an ``error`` key in
        ``output_data``.  Returns the fingerprint ID or ``None`` if
        no error is found.
        """
        error_type: str | None = None
        message: str | None = None
        stack_trace: str | None = None

        if event.get("event_type") == "error":
            od = event.get("output_data") or {}
            error_type = od.get("error_type") or event.get("error_type", "Error")
            message = od.get("message") or od.get("error", "unknown error")
            stack_trace = od.get("stack_trace") or od.get("traceback")
        elif isinstance(event.get("output_data"), dict):
            od = event["output_data"]
            if "error" in od or "error_type" in od:
                error_type = od.get("error_type", "Error")
                message = od.get("error") or od.get("message", "unknown error")
                stack_trace = od.get("stack_trace") or od.get("traceback")

        if error_type is None or message is None:
            return None

        sid = session_id or event.get("session_id", "")
        ts_raw = event.get("timestamp")
        ts: datetime | None = None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        elif isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (ValueError, TypeError):
                pass

        return self.record(
            error_type, message,
            session_id=sid,
            stack_trace=stack_trace,
            metadata=event.get("metadata") or event.get("input_data") or {},
            timestamp=ts,
        )

    def resolve(self, fingerprint_id: str) -> bool:
        """Mark a fingerprint as resolved.

        Returns ``True`` if the fingerprint existed and was marked.
        """
        if fingerprint_id in self._clusters:
            self._clusters[fingerprint_id].resolution = Resolution.RESOLVED
            self._resolved.add(fingerprint_id)
            return True
        return False

    def ignore(self, fingerprint_id: str) -> bool:
        """Mark a fingerprint as ignored (suppress from reports).

        Returns ``True`` if the fingerprint existed and was marked.
        """
        if fingerprint_id in self._clusters:
            self._clusters[fingerprint_id].resolution = Resolution.IGNORED
            return True
        return False

    def set_total_sessions(self, count: int) -> None:
        """Set the total session count for error-rate calculation."""
        self._total_sessions = max(0, count)

    def get_cluster(self, fingerprint_id: str) -> ErrorCluster | None:
        """Retrieve a single error cluster by its fingerprint ID."""
        return self._clusters.get(fingerprint_id)

    def report(
        self,
        *,
        top_n: int = 10,
        include_ignored: bool = False,
        include_resolved: bool = False,
    ) -> ErrorReport:
        """Generate an error fingerprint report.

        Parameters
        ----------
        top_n : int
            Number of top clusters to include (by occurrence count).
        include_ignored : bool
            Whether to include ignored fingerprints in the report.
        include_resolved : bool
            Whether to include resolved fingerprints in the report.
        """
        # Compute trends
        self._compute_trends()

        # Filter clusters
        clusters = list(self._clusters.values())
        if not include_ignored:
            clusters = [c for c in clusters
                        if c.resolution != Resolution.IGNORED]
        if not include_resolved:
            active = [c for c in clusters
                      if c.resolution != Resolution.RESOLVED]
            resolved_ids = [c.fingerprint_id for c in clusters
                            if c.resolution == Resolution.RESOLVED]
        else:
            active = clusters
            resolved_ids = []

        # Sort by occurrence count descending
        active.sort(key=lambda c: c.occurrence_count, reverse=True)

        new_fps = [c for c in active if c.trend == Trend.NEW]
        regressed = [c for c in active
                     if c.resolution == Resolution.REGRESSED]

        # Session stats
        all_sessions = set()
        for c in active:
            all_sessions.update(c.session_ids)

        session_counts = sorted(
            self._session_error_counts.items(),
            key=lambda x: x[1], reverse=True,
        )

        total = sum(c.occurrence_count for c in active)
        error_rate = (total / self._total_sessions
                      if self._total_sessions > 0 else 0.0)

        # Time window
        all_times = [c.first_seen for c in active if c.first_seen] + \
                    [c.last_seen for c in active if c.last_seen]

        return ErrorReport(
            unique_count=len(active),
            total_count=total,
            top_clusters=active[:top_n],
            new_fingerprints=new_fps,
            resolved_fingerprints=resolved_ids,
            regressed_fingerprints=regressed,
            sessions_affected=len(all_sessions),
            most_affected_sessions=session_counts[:10],
            error_rate=error_rate,
            window_start=min(all_times) if all_times else None,
            window_end=max(all_times) if all_times else None,
        )

    def reset(self) -> None:
        """Clear all recorded data."""
        self._clusters.clear()
        self._occurrences.clear()
        self._resolved.clear()
        self._session_error_counts.clear()
        self._total_sessions = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_message(message: str) -> str:
        """Normalise an error message into a template.

        Replaces variable parts (numbers, UUIDs, paths, timestamps,
        quoted strings) with placeholders so that structurally identical
        messages group together.
        """
        result = message
        for pattern, replacement in _NORM_PATTERNS:
            result = pattern.sub(replacement, result)
        # Collapse repeated whitespace
        result = re.sub(r"\s+", " ", result).strip()
        return result

    @staticmethod
    def _normalise_stack(stack_trace: str | None) -> str:
        """Extract a normalised signature from a stack trace.

        Takes the top N frames (closest to the error site), strips
        line numbers and file paths, keeping only function/method names.
        This produces a stable signature even when line numbers shift.
        """
        if not stack_trace:
            return ""

        # Extract frame lines — look for common patterns:
        # Python:  File "path", line N, in func_name
        # JS:      at funcName (path:line:col)
        # Java:    at package.Class.method(File.java:line)
        frames: list[str] = []

        # Python frames
        py_frames = re.findall(
            r'File\s+"[^"]*",\s+line\s+\d+,\s+in\s+(\w+)',
            stack_trace,
        )
        if py_frames:
            frames = py_frames

        # JavaScript frames (if no Python frames found)
        if not frames:
            js_frames = re.findall(
                r"at\s+([\w.<>$]+)\s*\(",
                stack_trace,
            )
            if js_frames:
                frames = js_frames

        # Java frames
        if not frames:
            java_frames = re.findall(
                r"at\s+([\w.$]+)\(",
                stack_trace,
            )
            if java_frames:
                frames = java_frames

        # Generic: any word after "in " or "at "
        if not frames:
            generic = re.findall(
                r"(?:in|at)\s+(\w+)",
                stack_trace,
            )
            if generic:
                frames = generic

        # Take only top N frames (reversed for Python — innermost last)
        if py_frames:
            # Python: innermost frame is last, so take last N
            frames = frames[-3:]  # top_frames default
        else:
            # JS/Java: innermost is first
            frames = frames[:3]

        return " > ".join(frames) if frames else ""

    def _compute_fingerprint(
        self, error_type: str, template: str, frame_sig: str,
    ) -> str:
        """Compute a stable fingerprint ID from error components."""
        raw = f"{error_type}|{template}|{frame_sig}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _compute_trends(self) -> None:
        """Compute trend direction for each cluster.

        Splits occurrences into time buckets and checks whether the
        count is increasing, decreasing, or stable.
        """
        if not self._occurrences:
            return

        timestamps = [o.timestamp for o in self._occurrences]
        t_min = min(timestamps)
        t_max = max(timestamps)
        span = (t_max - t_min).total_seconds()

        if span <= 0:
            # All errors at the same instant — all are "new"
            for cluster in self._clusters.values():
                if cluster.resolution not in (Resolution.RESOLVED,
                                               Resolution.IGNORED):
                    cluster.trend = Trend.NEW
            return

        bucket_width = span / self._window_buckets

        # Build per-cluster bucket counts
        cluster_buckets: dict[str, list[int]] = {
            fp_id: [0] * self._window_buckets
            for fp_id in self._clusters
        }

        for occ in self._occurrences:
            fp_id = occ.fingerprint_id
            if fp_id not in cluster_buckets:
                continue
            bucket_idx = min(
                int((occ.timestamp - t_min).total_seconds() / bucket_width),
                self._window_buckets - 1,
            )
            cluster_buckets[fp_id][bucket_idx] += 1

        for fp_id, buckets in cluster_buckets.items():
            cluster = self._clusters[fp_id]
            cluster.recent_counts = buckets

            if cluster.resolution in (Resolution.RESOLVED,
                                       Resolution.IGNORED):
                continue

            # Only in the last bucket? → new
            non_zero = [i for i, c in enumerate(buckets) if c > 0]
            if len(non_zero) == 1 and non_zero[0] == len(buckets) - 1:
                cluster.trend = Trend.NEW
                continue

            # Linear trend: compare first half vs second half
            mid = len(buckets) // 2
            first_half = sum(buckets[:mid]) if mid > 0 else 0
            second_half = sum(buckets[mid:])

            if second_half > first_half * 1.5:
                cluster.trend = Trend.RISING
            elif first_half > second_half * 1.5:
                cluster.trend = Trend.FALLING
            else:
                cluster.trend = Trend.STABLE
