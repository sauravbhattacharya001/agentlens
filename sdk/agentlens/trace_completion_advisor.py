"""Agentic trace completion / liveness advisor for AgentLens.

The AgentLens tracker collects :class:`~agentlens.models.AgentEvent`
objects keyed by ``session_id``.  Each session is, in effect, a *trace*:
a temporally ordered sequence of LLM calls, tool calls, decisions and
errors that should eventually reach a clean end state.

In production, traces don't always finish cleanly.  A worker crashes
mid-tool-call, a queue back-pressure stalls an LLM call, a recursive
agent gets stuck in a retry loop, or a session is simply silent because
something upstream never ran the agent at all.  Today, AgentLens has no
single place that says *"these N traces are stuck right now and here is
what to do about them."*

:class:`TraceCompletionAdvisor` is that place.  It is the next agentic
sibling to :class:`~agentlens.sampling_advisor.SamplingAdvisor`,
:class:`~agentlens.incident_radar.IncidentRiskRadar`,
:class:`~agentlens.alert_rule_synthesizer.AlertRuleSynthesizer`,
:class:`~agentlens.model_migration_advisor.ModelMigrationAdvisor`, and
:class:`~agentlens.slo_burn_rate_advisor.SLOBurnRateAdvisor`.

What it does
------------

Given an iterable of :class:`~agentlens.models.AgentEvent` (or plain
dicts with the same shape), the advisor:

1. Groups events by ``session_id`` into *trace snapshots*.
2. Classifies each trace with one of the verdicts in
   :class:`TraceVerdict` (``COMPLETE``, ``IN_PROGRESS``, ``NEAR_TIMEOUT``,
   ``HUNG``, ``ABANDONED``, ``ERRORED_OPEN``, ``SILENT``).
3. Scores each trace 0-100 on *incompletion risk* (higher = more likely
   to be stuck or to need intervention).
4. Detects per-trace issues (:class:`TraceIssueCode`).
5. Emits a deduped, P0-first cross-trace playbook
   (:class:`PlaybookAction`).
6. Synthesizes portfolio insights and an A-F grade.
7. Renders text / markdown / JSON.

The advisor is *pure*: it never mutates inputs, makes no network calls,
and uses only the standard library.  It is deterministic given an
injectable clock.

Example
-------
::

    from agentlens import TraceCompletionAdvisor

    advisor = TraceCompletionAdvisor(risk_appetite="cautious")
    report = advisor.analyze(events)
    print(report.render_markdown())
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class TraceVerdict(Enum):
    """Per-trace classification."""

    COMPLETE = "complete"
    IN_PROGRESS = "in_progress"
    NEAR_TIMEOUT = "near_timeout"
    HUNG = "hung"
    ABANDONED = "abandoned"
    ERRORED_OPEN = "errored_open"
    SILENT = "silent"


class TraceIssueCode(Enum):
    """Specific issues attached to a trace."""

    HUNG_OPERATION = "hung_operation"
    UNRESOLVED_ERROR = "unresolved_error"
    ORPHAN_TOOL_CALL = "orphan_tool_call"
    SILENT_TRACE = "silent_trace"
    NEAR_TIMEOUT = "near_timeout"
    ABANDONED = "abandoned"
    NO_DECISION_CONTEXT = "no_decision_context"
    RETRY_STORM = "retry_storm"


class ActionPriority(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RiskAppetite(Enum):
    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

    @classmethod
    def parse(cls, value: "str | RiskAppetite") -> "RiskAppetite":
        if isinstance(value, cls):
            return value
        try:
            return cls(value.lower())
        except (AttributeError, ValueError):
            return cls.BALANCED


class CompletionGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


@dataclass
class TraceIssue:
    code: TraceIssueCode
    severity: int  # 0-100
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity,
            "reason": self.reason,
        }


@dataclass
class TraceSnapshot:
    session_id: str
    started_at: datetime
    last_event_at: datetime
    event_count: int
    error_count: int
    open_tool_calls: int
    has_terminal_event: bool
    age_seconds: float
    idle_seconds: float
    longest_open_op_ms: float
    verdict: TraceVerdict
    incompletion_risk: int  # 0-100
    priority: ActionPriority
    issues: list[TraceIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "last_event_at": self.last_event_at.isoformat(),
            "event_count": self.event_count,
            "error_count": self.error_count,
            "open_tool_calls": self.open_tool_calls,
            "has_terminal_event": self.has_terminal_event,
            "age_seconds": round(self.age_seconds, 2),
            "idle_seconds": round(self.idle_seconds, 2),
            "longest_open_op_ms": round(self.longest_open_op_ms, 2),
            "verdict": self.verdict.value,
            "incompletion_risk": self.incompletion_risk,
            "priority": self.priority.value,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class PlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int  # 1-5
    reversibility: str  # low/medium/high
    related_session_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority.value,
            "label": self.label,
            "reason": self.reason,
            "owner": self.owner,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "related_session_ids": list(self.related_session_ids),
        }


@dataclass
class TraceCompletionReport:
    generated_at: datetime
    window_label: str
    risk_appetite: RiskAppetite
    total_traces: int
    completed_traces: int
    open_traces: int
    hung_traces: int
    abandoned_traces: int
    errored_open_traces: int
    silent_traces: int
    completion_rate: float       # 0..1
    incompletion_score: int      # 0-100 portfolio
    grade: CompletionGrade
    summary: str
    traces: list[TraceSnapshot]
    playbook: list[PlaybookAction]
    insights: list[str]

    # ------------------------------------------------------------------ #
    # Convenience: filter / lookup
    # ------------------------------------------------------------------ #

    def actions_by_priority(self) -> dict[str, list[PlaybookAction]]:
        out: dict[str, list[PlaybookAction]] = {p.value: [] for p in ActionPriority}
        for a in self.playbook:
            out[a.priority.value].append(a)
        return out

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_label": self.window_label,
            "risk_appetite": self.risk_appetite.value,
            "total_traces": self.total_traces,
            "completed_traces": self.completed_traces,
            "open_traces": self.open_traces,
            "hung_traces": self.hung_traces,
            "abandoned_traces": self.abandoned_traces,
            "errored_open_traces": self.errored_open_traces,
            "silent_traces": self.silent_traces,
            "completion_rate": round(self.completion_rate, 4),
            "incompletion_score": self.incompletion_score,
            "grade": self.grade.value,
            "summary": self.summary,
            "traces": [t.to_dict() for t in self.traces],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": list(self.insights),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent, default=str)

    # ------------------------------------------------------------------ #
    # Renderers
    # ------------------------------------------------------------------ #

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append(f"TraceCompletionAdvisor [{self.window_label}] grade={self.grade.value}")
        lines.append(self.summary)
        lines.append(
            f"  traces total={self.total_traces} completed={self.completed_traces} "
            f"open={self.open_traces} hung={self.hung_traces} "
            f"abandoned={self.abandoned_traces} errored_open={self.errored_open_traces} "
            f"silent={self.silent_traces}"
        )
        lines.append(f"  completion_rate={self.completion_rate:.2%} incompletion_score={self.incompletion_score}/100")

        if self.traces:
            lines.append("")
            lines.append("At-risk traces:")
            for t in self.traces:
                if t.verdict in (TraceVerdict.COMPLETE, TraceVerdict.IN_PROGRESS):
                    continue
                lines.append(
                    f"  [{t.priority.value}] {t.session_id} verdict={t.verdict.value} "
                    f"risk={t.incompletion_risk}/100 idle={t.idle_seconds:.0f}s "
                    f"open_tools={t.open_tool_calls} errors={t.error_count}"
                )

        if self.playbook:
            lines.append("")
            lines.append("Playbook:")
            for a in self.playbook:
                lines.append(f"  [{a.priority.value}] {a.label} (owner={a.owner}, blast={a.blast_radius})")
                lines.append(f"        {a.reason}")

        if self.insights:
            lines.append("")
            lines.append("Insights:")
            for i in self.insights:
                lines.append(f"  - {i}")

        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Trace Completion Advisor — {self.window_label}")
        lines.append("")
        lines.append(f"**Grade:** {self.grade.value}  ")
        lines.append(f"**Incompletion score:** {self.incompletion_score}/100  ")
        lines.append(f"**Completion rate:** {self.completion_rate:.2%}  ")
        lines.append(f"**Risk appetite:** {self.risk_appetite.value}")
        lines.append("")
        lines.append(f"_{self.summary}_")
        lines.append("")

        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Total traces | {self.total_traces} |")
        lines.append(f"| Completed | {self.completed_traces} |")
        lines.append(f"| Open (in progress) | {self.open_traces} |")
        lines.append(f"| Hung | {self.hung_traces} |")
        lines.append(f"| Abandoned | {self.abandoned_traces} |")
        lines.append(f"| Errored open | {self.errored_open_traces} |")
        lines.append(f"| Silent | {self.silent_traces} |")
        lines.append("")

        risky = [t for t in self.traces if t.verdict not in (TraceVerdict.COMPLETE, TraceVerdict.IN_PROGRESS)]
        if risky:
            lines.append("## At-risk traces")
            lines.append("")
            lines.append("| Priority | Session | Verdict | Risk | Idle (s) | Open tools | Errors | Issues |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for t in risky:
                codes = ", ".join(i.code.value for i in t.issues) or "—"
                lines.append(
                    f"| {t.priority.value} | `{t.session_id}` | {t.verdict.value} | "
                    f"{t.incompletion_risk} | {t.idle_seconds:.0f} | {t.open_tool_calls} | "
                    f"{t.error_count} | {codes} |"
                )
            lines.append("")

        if self.playbook:
            lines.append("## Playbook")
            lines.append("")
            lines.append("| Priority | Action | Owner | Blast | Reversibility | Sessions | Reason |")
            lines.append("|---|---|---|---|---|---|---|")
            for a in self.playbook:
                sess = ", ".join(f"`{s}`" for s in a.related_session_ids[:3]) or "—"
                if len(a.related_session_ids) > 3:
                    sess += f" +{len(a.related_session_ids) - 3}"
                lines.append(
                    f"| {a.priority.value} | **{a.label}** | {a.owner} | {a.blast_radius} | "
                    f"{a.reversibility} | {sess} | {a.reason} |"
                )
            lines.append("")

        if self.insights:
            lines.append("## Insights")
            lines.append("")
            for i in self.insights:
                lines.append(f"- {i}")
            lines.append("")

        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


# Event types that mark the end of a trace (case-insensitive).
TERMINAL_EVENT_TYPES = {
    "session_end",
    "session_complete",
    "completed",
    "complete",
    "final_response",
    "answer",
    "finish",
    "done",
}

ERROR_EVENT_TYPES = {"error", "exception", "failure", "failed", "tool_error"}
TOOL_CALL_TYPES = {"tool_call", "tool", "function_call"}
TOOL_RESULT_TYPES = {"tool_result", "tool_output", "function_result"}
RETRY_EVENT_TYPES = {"retry", "retry_attempt"}


@dataclass
class TraceCompletionAdvisor:
    """Per-trace + portfolio-level liveness analyzer."""

    # Trace is HUNG if its single longest pending op exceeds this many ms.
    hung_op_threshold_ms: float = 60_000.0
    # Trace is ABANDONED if no events for this long and not terminal.
    abandon_idle_seconds: float = 600.0
    # Trace is NEAR_TIMEOUT if open and age >= near_timeout_seconds.
    near_timeout_seconds: float = 300.0
    # Trace is SILENT if event_count <= silent_event_max.
    silent_event_max: int = 1
    silent_min_age_seconds: float = 120.0
    # Retry storm threshold.
    retry_storm_threshold: int = 4
    risk_appetite: "RiskAppetite | str" = RiskAppetite.BALANCED
    now_fn: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.risk_appetite = RiskAppetite.parse(self.risk_appetite)
        # Apply risk-appetite modulation to thresholds without mutating
        # caller intent: cautious shrinks tolerances (we cry wolf earlier),
        # aggressive expands them (we trust the system more).
        if self.risk_appetite is RiskAppetite.CAUTIOUS:
            self.hung_op_threshold_ms *= 0.75
            self.abandon_idle_seconds *= 0.75
            self.near_timeout_seconds *= 0.75
        elif self.risk_appetite is RiskAppetite.AGGRESSIVE:
            self.hung_op_threshold_ms *= 1.40
            self.abandon_idle_seconds *= 1.40
            self.near_timeout_seconds *= 1.40

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(
        self,
        events: Iterable[Any],
        *,
        window_label: str = "current",
    ) -> TraceCompletionReport:
        """Analyze a flat iterable of events grouped by ``session_id``."""

        now = _coerce_dt(self.now_fn())
        buckets: dict[str, list[dict[str, Any]]] = {}
        for raw in events:
            ev = _coerce_event(raw)
            sid = ev.get("session_id") or ""
            if not sid:
                # Drop events without a session id - we have no way to
                # attribute them to a trace.
                continue
            buckets.setdefault(sid, []).append(ev)

        traces: list[TraceSnapshot] = []
        for sid, evs in buckets.items():
            evs.sort(key=lambda e: e.get("timestamp") or now)
            traces.append(self._classify_trace(sid, evs, now))

        # Sort by priority then risk then session id for determinism.
        priority_order = {ActionPriority.P0: 0, ActionPriority.P1: 1, ActionPriority.P2: 2, ActionPriority.P3: 3}
        traces.sort(key=lambda t: (priority_order[t.priority], -t.incompletion_risk, t.session_id))

        playbook = self._build_playbook(traces)
        insights = self._build_insights(traces)
        portfolio = self._portfolio_summary(traces)
        summary = self._summary_line(traces, portfolio)

        return TraceCompletionReport(
            generated_at=now,
            window_label=window_label,
            risk_appetite=self.risk_appetite,  # type: ignore[arg-type]
            total_traces=len(traces),
            completed_traces=portfolio["completed"],
            open_traces=portfolio["open"],
            hung_traces=portfolio["hung"],
            abandoned_traces=portfolio["abandoned"],
            errored_open_traces=portfolio["errored_open"],
            silent_traces=portfolio["silent"],
            completion_rate=portfolio["completion_rate"],
            incompletion_score=portfolio["incompletion_score"],
            grade=portfolio["grade"],
            summary=summary,
            traces=traces,
            playbook=playbook,
            insights=insights,
        )

    # ------------------------------------------------------------------ #
    # Per-trace classification
    # ------------------------------------------------------------------ #

    def _classify_trace(
        self,
        session_id: str,
        events: list[dict[str, Any]],
        now: datetime,
    ) -> TraceSnapshot:
        started_at = _coerce_dt(events[0].get("timestamp")) if events else now
        last_event_at = _coerce_dt(events[-1].get("timestamp")) if events else now
        age = max(0.0, (now - started_at).total_seconds())
        idle = max(0.0, (now - last_event_at).total_seconds())

        error_count = 0
        retry_count = 0
        terminal = False
        open_tool_calls = 0  # tool_call without matching tool_result
        longest_open_op_ms = 0.0
        decisions_seen = 0
        tool_calls_seen = 0
        # Track open tool calls by tool_call_id (best-effort).
        outstanding: dict[str, dict[str, Any]] = {}

        for ev in events:
            etype = str(ev.get("event_type") or "").lower()
            if etype in TERMINAL_EVENT_TYPES:
                terminal = True
            if etype in ERROR_EVENT_TYPES:
                error_count += 1
            if etype in RETRY_EVENT_TYPES:
                retry_count += 1
            if etype in TOOL_CALL_TYPES:
                tool_calls_seen += 1
                tc = ev.get("tool_call") or {}
                tcid = tc.get("tool_call_id") or ev.get("event_id") or f"unk_{len(outstanding)}"
                # If tool_output already present on the same event, treat
                # as resolved (this matches the AgentEvent nested shape).
                if tc.get("tool_output") is not None or ev.get("output_data") is not None:
                    continue
                outstanding[tcid] = ev
            if etype in TOOL_RESULT_TYPES:
                # Best-effort: pop any outstanding tool call with same id.
                tc = ev.get("tool_call") or {}
                tcid = tc.get("tool_call_id") or ev.get("input_data", {}).get("tool_call_id") or None
                if tcid and tcid in outstanding:
                    outstanding.pop(tcid)
                elif outstanding:
                    # Fallback: resolve the oldest outstanding call.
                    oldest_key = next(iter(outstanding))
                    outstanding.pop(oldest_key)
            if ev.get("decision_trace"):
                decisions_seen += 1
            dur = ev.get("duration_ms")
            if isinstance(dur, (int, float)) and dur > longest_open_op_ms:
                longest_open_op_ms = float(dur)

        open_tool_calls = len(outstanding)

        # Compute longest *pending* op: time from oldest outstanding tool
        # call start until now, in ms.
        for _tcid, ev in outstanding.items():
            start = _coerce_dt(ev.get("timestamp")) if ev else now
            pending_ms = max(0.0, (now - start).total_seconds() * 1000.0)
            if pending_ms > longest_open_op_ms:
                longest_open_op_ms = pending_ms

        last_error_resolved = self._error_resolved(events)

        # ------------------------------------------------------------------ #
        # Verdict ladder
        # ------------------------------------------------------------------ #

        verdict: TraceVerdict
        if terminal and open_tool_calls == 0 and (last_error_resolved or error_count == 0):
            verdict = TraceVerdict.COMPLETE
        elif (
            len(events) <= self.silent_event_max
            and age >= self.silent_min_age_seconds
            and not terminal
        ):
            verdict = TraceVerdict.SILENT
        elif idle >= self.abandon_idle_seconds and not terminal:
            verdict = TraceVerdict.ABANDONED
        elif longest_open_op_ms >= self.hung_op_threshold_ms:
            verdict = TraceVerdict.HUNG
        elif error_count > 0 and not last_error_resolved and not terminal:
            verdict = TraceVerdict.ERRORED_OPEN
        elif age >= self.near_timeout_seconds and not terminal:
            verdict = TraceVerdict.NEAR_TIMEOUT
        else:
            verdict = TraceVerdict.IN_PROGRESS

        # ------------------------------------------------------------------ #
        # Issues + risk score
        # ------------------------------------------------------------------ #

        issues: list[TraceIssue] = []
        risk = 0

        if verdict is TraceVerdict.HUNG:
            sev = min(95, 60 + int(longest_open_op_ms / max(1.0, self.hung_op_threshold_ms) * 15))
            issues.append(
                TraceIssue(
                    TraceIssueCode.HUNG_OPERATION,
                    severity=sev,
                    reason=f"Pending op exceeds {self.hung_op_threshold_ms / 1000:.0f}s "
                           f"(longest={longest_open_op_ms/1000:.1f}s).",
                )
            )
            risk = max(risk, sev)

        if verdict is TraceVerdict.ABANDONED:
            sev = min(95, 55 + int(idle / max(1.0, self.abandon_idle_seconds) * 10))
            issues.append(
                TraceIssue(
                    TraceIssueCode.ABANDONED,
                    severity=sev,
                    reason=f"No events for {idle:.0f}s (threshold {self.abandon_idle_seconds:.0f}s).",
                )
            )
            risk = max(risk, sev)

        if verdict is TraceVerdict.ERRORED_OPEN:
            sev = 70 + min(20, error_count * 5)
            issues.append(
                TraceIssue(
                    TraceIssueCode.UNRESOLVED_ERROR,
                    severity=sev,
                    reason=f"{error_count} error event(s) with no recovery and no terminal event.",
                )
            )
            risk = max(risk, sev)

        if verdict is TraceVerdict.NEAR_TIMEOUT:
            sev = min(60, 35 + int((age - self.near_timeout_seconds) / max(1.0, self.near_timeout_seconds) * 20))
            issues.append(
                TraceIssue(
                    TraceIssueCode.NEAR_TIMEOUT,
                    severity=sev,
                    reason=f"Trace open for {age:.0f}s (warn threshold {self.near_timeout_seconds:.0f}s).",
                )
            )
            risk = max(risk, sev)

        if verdict is TraceVerdict.SILENT:
            sev = 50
            issues.append(
                TraceIssue(
                    TraceIssueCode.SILENT_TRACE,
                    severity=sev,
                    reason=f"Only {len(events)} event(s) in {age:.0f}s; trace may have failed upstream.",
                )
            )
            risk = max(risk, sev)

        if open_tool_calls > 0 and verdict not in (TraceVerdict.COMPLETE,):
            sev = 40 + min(30, open_tool_calls * 10)
            issues.append(
                TraceIssue(
                    TraceIssueCode.ORPHAN_TOOL_CALL,
                    severity=sev,
                    reason=f"{open_tool_calls} tool_call(s) with no matching result.",
                )
            )
            risk = max(risk, sev)

        if retry_count >= self.retry_storm_threshold:
            sev = 50 + min(30, retry_count * 4)
            issues.append(
                TraceIssue(
                    TraceIssueCode.RETRY_STORM,
                    severity=sev,
                    reason=f"{retry_count} retry events; possible retry storm.",
                )
            )
            risk = max(risk, sev)

        if tool_calls_seen > 0 and decisions_seen == 0 and verdict not in (TraceVerdict.COMPLETE,):
            issues.append(
                TraceIssue(
                    TraceIssueCode.NO_DECISION_CONTEXT,
                    severity=25,
                    reason="Tool calls present but no decision_trace captured (audit gap).",
                )
            )
            risk = max(risk, 25)

        if verdict is TraceVerdict.IN_PROGRESS and not issues:
            risk = 5
        if verdict is TraceVerdict.COMPLETE:
            risk = 0
        # Risk-appetite shift on the final score (small nudge).
        if self.risk_appetite is RiskAppetite.CAUTIOUS:
            risk = min(100, int(risk + 5))
        elif self.risk_appetite is RiskAppetite.AGGRESSIVE:
            risk = max(0, int(risk - 5))

        # Priority bucket.
        if risk >= 75 or verdict in (TraceVerdict.HUNG, TraceVerdict.ERRORED_OPEN):
            priority = ActionPriority.P0
        elif risk >= 50 or verdict in (TraceVerdict.ABANDONED, TraceVerdict.SILENT):
            priority = ActionPriority.P1
        elif risk >= 25 or verdict is TraceVerdict.NEAR_TIMEOUT:
            priority = ActionPriority.P2
        else:
            priority = ActionPriority.P3

        # Deterministic sort of issues.
        issues.sort(key=lambda i: (-i.severity, i.code.value))

        return TraceSnapshot(
            session_id=session_id,
            started_at=started_at,
            last_event_at=last_event_at,
            event_count=len(events),
            error_count=error_count,
            open_tool_calls=open_tool_calls,
            has_terminal_event=terminal,
            age_seconds=age,
            idle_seconds=idle,
            longest_open_op_ms=longest_open_op_ms,
            verdict=verdict,
            incompletion_risk=int(risk),
            priority=priority,
            issues=issues,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _error_resolved(events: list[dict[str, Any]]) -> bool:
        """True if every error event has at least one later non-error event."""
        seen_error_at = None
        for ev in events:
            etype = str(ev.get("event_type") or "").lower()
            if etype in ERROR_EVENT_TYPES:
                seen_error_at = _coerce_dt(ev.get("timestamp"))
            elif seen_error_at is not None:
                ts = _coerce_dt(ev.get("timestamp"))
                if ts >= seen_error_at:
                    seen_error_at = None
        return seen_error_at is None

    # ------------------------------------------------------------------ #
    # Cross-trace playbook + insights
    # ------------------------------------------------------------------ #

    def _build_playbook(self, traces: list[TraceSnapshot]) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []

        hung = [t for t in traces if t.verdict is TraceVerdict.HUNG]
        errored = [t for t in traces if t.verdict is TraceVerdict.ERRORED_OPEN]
        abandoned = [t for t in traces if t.verdict is TraceVerdict.ABANDONED]
        silent = [t for t in traces if t.verdict is TraceVerdict.SILENT]
        near = [t for t in traces if t.verdict is TraceVerdict.NEAR_TIMEOUT]
        orphan_tool = [t for t in traces if any(i.code is TraceIssueCode.ORPHAN_TOOL_CALL for i in t.issues)]
        retry_storms = [t for t in traces if any(i.code is TraceIssueCode.RETRY_STORM for i in t.issues)]
        audit_gaps = [t for t in traces if any(i.code is TraceIssueCode.NO_DECISION_CONTEXT for i in t.issues)]

        if hung:
            actions.append(PlaybookAction(
                id="terminate_hung_traces",
                priority=ActionPriority.P0,
                label="Terminate or restart hung traces",
                reason=f"{len(hung)} trace(s) have a pending op beyond the hung-op threshold; "
                       f"force-finish or restart to free worker capacity.",
                owner="on_call",
                blast_radius=3,
                reversibility="medium",
                related_session_ids=[t.session_id for t in hung],
            ))

        if errored:
            actions.append(PlaybookAction(
                id="recover_errored_open",
                priority=ActionPriority.P0,
                label="Drive errored-open traces to terminal",
                reason=f"{len(errored)} trace(s) have unresolved errors and no terminal event; "
                       f"either recover or mark failed so SLAs aren't violated silently.",
                owner="service_owner",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[t.session_id for t in errored],
            ))

        if abandoned:
            actions.append(PlaybookAction(
                id="reap_abandoned_traces",
                priority=ActionPriority.P1,
                label="Reap abandoned traces",
                reason=f"{len(abandoned)} trace(s) idle past the abandon threshold; "
                       f"close out or retry to keep dashboards honest.",
                owner="platform",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[t.session_id for t in abandoned],
            ))

        if silent:
            actions.append(PlaybookAction(
                id="investigate_silent_traces",
                priority=ActionPriority.P1,
                label="Investigate silent traces",
                reason=f"{len(silent)} trace(s) recorded only a session start and nothing else; "
                       f"check upstream queues, auth, or capacity.",
                owner="platform",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[t.session_id for t in silent],
            ))

        if orphan_tool:
            actions.append(PlaybookAction(
                id="reconcile_orphan_tool_calls",
                priority=ActionPriority.P1,
                label="Reconcile orphan tool calls",
                reason=f"{len(orphan_tool)} trace(s) have tool_call events with no matching result; "
                       f"verify tool sidecar health and retry handlers.",
                owner="tool_owner",
                blast_radius=3,
                reversibility="medium",
                related_session_ids=[t.session_id for t in orphan_tool],
            ))

        if retry_storms:
            actions.append(PlaybookAction(
                id="break_retry_storm",
                priority=ActionPriority.P1,
                label="Break the retry storm",
                reason=f"{len(retry_storms)} trace(s) exceeded the retry-storm threshold "
                       f"({self.retry_storm_threshold}); add backoff or a circuit breaker.",
                owner="service_owner",
                blast_radius=3,
                reversibility="medium",
                related_session_ids=[t.session_id for t in retry_storms],
            ))

        if near:
            actions.append(PlaybookAction(
                id="warn_near_timeout",
                priority=ActionPriority.P2,
                label="Warn on near-timeout traces",
                reason=f"{len(near)} trace(s) are within the near-timeout window; "
                       f"watch closely and pre-warm fallbacks.",
                owner="on_call",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[t.session_id for t in near],
            ))

        if audit_gaps:
            actions.append(PlaybookAction(
                id="capture_decision_traces",
                priority=ActionPriority.P2,
                label="Capture decision traces for tool calls",
                reason=f"{len(audit_gaps)} trace(s) call tools without a decision_trace; "
                       f"add decision capture so postmortems can explain *why*.",
                owner="agent_dev",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[t.session_id for t in audit_gaps],
            ))

        # Aggressive: drop P2 noise from the playbook.
        if self.risk_appetite is RiskAppetite.AGGRESSIVE:
            actions = [a for a in actions if a.priority is not ActionPriority.P2]

        # Cautious: append a P2 audit reminder if grade is C or worse and
        # nothing else is open.
        if self.risk_appetite is RiskAppetite.CAUTIOUS and not actions:
            actions.append(PlaybookAction(
                id="schedule_completion_audit",
                priority=ActionPriority.P3,
                label="Schedule next completion audit",
                reason="No active incidents; keep cadence so degradation is caught early.",
                owner="platform",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[],
            ))

        if not actions:
            actions.append(PlaybookAction(
                id="all_clear",
                priority=ActionPriority.P3,
                label="All traces healthy",
                reason="No hung, abandoned, errored-open, or silent traces detected.",
                owner="platform",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[],
            ))

        # Deterministic order: P0 first, then by id.
        priority_order = {ActionPriority.P0: 0, ActionPriority.P1: 1, ActionPriority.P2: 2, ActionPriority.P3: 3}
        actions.sort(key=lambda a: (priority_order[a.priority], a.id))
        return actions

    def _build_insights(self, traces: list[TraceSnapshot]) -> list[str]:
        out: list[str] = []
        total = len(traces)
        if total == 0:
            out.append("NO_TRACES: nothing to analyze in this window.")
            return out

        hung = sum(1 for t in traces if t.verdict is TraceVerdict.HUNG)
        abandoned = sum(1 for t in traces if t.verdict is TraceVerdict.ABANDONED)
        errored = sum(1 for t in traces if t.verdict is TraceVerdict.ERRORED_OPEN)
        silent = sum(1 for t in traces if t.verdict is TraceVerdict.SILENT)
        completed = sum(1 for t in traces if t.verdict is TraceVerdict.COMPLETE)

        if hung >= max(2, int(total * 0.10)):
            out.append(f"HUNG_CLUSTER: {hung}/{total} traces hung - investigate the tool backend or queue.")
        if abandoned >= max(2, int(total * 0.20)):
            out.append(f"ABANDONED_CLUSTER: {abandoned}/{total} traces abandoned - upstream may be dropping work.")
        if silent >= max(2, int(total * 0.20)):
            out.append(f"SILENT_CLUSTER: {silent}/{total} traces silent - sessions are being created without work.")
        if errored:
            out.append(f"UNRESOLVED_ERRORS: {errored} trace(s) ended on an unhandled error.")
        if completed == total and total > 0:
            out.append("ALL_COMPLETED: every trace in the window reached a terminal event.")

        # Open tool-call burden.
        open_tools = sum(t.open_tool_calls for t in traces)
        if open_tools >= 5:
            out.append(f"OPEN_TOOLCALL_BURDEN: {open_tools} tool calls awaiting a result across the window.")

        return out

    def _portfolio_summary(self, traces: list[TraceSnapshot]) -> dict[str, Any]:
        total = len(traces)
        completed = sum(1 for t in traces if t.verdict is TraceVerdict.COMPLETE)
        open_ = sum(1 for t in traces if t.verdict is TraceVerdict.IN_PROGRESS)
        hung = sum(1 for t in traces if t.verdict is TraceVerdict.HUNG)
        abandoned = sum(1 for t in traces if t.verdict is TraceVerdict.ABANDONED)
        errored_open = sum(1 for t in traces if t.verdict is TraceVerdict.ERRORED_OPEN)
        silent = sum(1 for t in traces if t.verdict is TraceVerdict.SILENT)
        near = sum(1 for t in traces if t.verdict is TraceVerdict.NEAR_TIMEOUT)

        if total == 0:
            return {
                "completed": 0,
                "open": 0,
                "hung": 0,
                "abandoned": 0,
                "errored_open": 0,
                "silent": 0,
                "near": 0,
                "completion_rate": 1.0,
                "incompletion_score": 0,
                "grade": CompletionGrade.A,
            }

        completion_rate = completed / total
        # Portfolio incompletion score: weighted mean of per-trace risks
        # plus a floor proportional to non-complete share.
        mean_risk = sum(t.incompletion_risk for t in traces) / total
        non_complete_share = 1.0 - completion_rate
        incompletion_score = int(min(100, max(0, round(mean_risk * 0.7 + non_complete_share * 100 * 0.3))))

        # Grade: F if any P0 issue OR incompletion >= 70, else cascade.
        any_p0 = any(t.priority is ActionPriority.P0 for t in traces)
        if any_p0 or incompletion_score >= 70:
            grade = CompletionGrade.F
        elif incompletion_score >= 50:
            grade = CompletionGrade.D
        elif incompletion_score >= 30:
            grade = CompletionGrade.C
        elif incompletion_score >= 15:
            grade = CompletionGrade.B
        else:
            grade = CompletionGrade.A

        return {
            "completed": completed,
            "open": open_,
            "hung": hung,
            "abandoned": abandoned,
            "errored_open": errored_open,
            "silent": silent,
            "near": near,
            "completion_rate": completion_rate,
            "incompletion_score": incompletion_score,
            "grade": grade,
        }

    @staticmethod
    def _summary_line(traces: list[TraceSnapshot], portfolio: dict[str, Any]) -> str:
        total = len(traces)
        if total == 0:
            return "No traces in window."
        parts = [
            f"{portfolio['completed']}/{total} complete ({portfolio['completion_rate']:.0%})",
        ]
        if portfolio["hung"]:
            parts.append(f"{portfolio['hung']} hung")
        if portfolio["errored_open"]:
            parts.append(f"{portfolio['errored_open']} errored-open")
        if portfolio["abandoned"]:
            parts.append(f"{portfolio['abandoned']} abandoned")
        if portfolio["silent"]:
            parts.append(f"{portfolio['silent']} silent")
        if portfolio["near"]:
            parts.append(f"{portfolio['near']} near-timeout")
        return "Trace liveness: " + ", ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _coerce_dt(value: Any) -> datetime:
    """Best-effort coerce ``value`` into a timezone-aware datetime."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _coerce_event(raw: Any) -> dict[str, Any]:
    """Return a *copy* of ``raw`` as a plain dict.

    Accepts pydantic ``AgentEvent`` instances (via ``.model_dump()`` or
    ``.dict()``), plain dicts, or arbitrary objects with the expected
    attribute names.  Never mutates the input.
    """

    if isinstance(raw, dict):
        return copy.deepcopy(raw)

    # pydantic v2
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            return dump()  # type: ignore[no-any-return]
        except Exception:
            pass

    # pydantic v1
    dump = getattr(raw, "dict", None)
    if callable(dump):
        try:
            return dump()  # type: ignore[no-any-return]
        except Exception:
            pass

    # Attribute fallback for plain objects.
    out: dict[str, Any] = {}
    for key in (
        "event_id",
        "session_id",
        "event_type",
        "timestamp",
        "input_data",
        "output_data",
        "model",
        "tokens_in",
        "tokens_out",
        "tool_call",
        "decision_trace",
        "duration_ms",
    ):
        if hasattr(raw, key):
            out[key] = getattr(raw, key)
    return out


__all__ = [
    "TraceCompletionAdvisor",
    "TraceCompletionReport",
    "TraceSnapshot",
    "TraceIssue",
    "TraceIssueCode",
    "TraceVerdict",
    "PlaybookAction",
    "ActionPriority",
    "RiskAppetite",
    "CompletionGrade",
]
