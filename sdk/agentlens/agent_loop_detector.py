"""Agentic in-flight loop detector for AgentLens.

While :class:`~agentlens.trace_completion_advisor.TraceCompletionAdvisor`
finds traces that are *stuck and silent*, this advisor finds the opposite
failure mode: traces that are *busy but not making progress* — agents
calling the same tool over and over, bouncing between two tools, throwing
the same error in a tight retry loop, or burning tokens without ever
reaching a decision.

These are some of the most common — and most expensive — agent failures
in production.  A single agent stuck in an infinite tool loop can drain
an LLM budget in minutes and never surface as a "timeout" because each
individual call succeeds.

:class:`AgentLoopDetector` is the next agentic sibling to
:class:`~agentlens.sampling_advisor.SamplingAdvisor`,
:class:`~agentlens.incident_radar.IncidentRiskRadar`,
:class:`~agentlens.alert_rule_synthesizer.AlertRuleSynthesizer`,
:class:`~agentlens.model_migration_advisor.ModelMigrationAdvisor`,
:class:`~agentlens.slo_burn_rate_advisor.SLOBurnRateAdvisor`, and
:class:`~agentlens.trace_completion_advisor.TraceCompletionAdvisor`.

What it does
------------

Given an iterable of :class:`~agentlens.models.AgentEvent` (or dicts):

1. Groups events by ``session_id`` into *loop snapshots*.
2. Detects per-trace loop patterns
   (:class:`LoopIssueCode`).
3. Classifies each trace with a :class:`LoopVerdict`
   (``HEALTHY``, ``BENIGN_RETRY``, ``PROGRESSING_LOOP``,
   ``TIGHT_LOOP``, ``INFINITE_LOOP_SUSPECTED``, ``ERROR_STORM``,
   ``TOOL_THRASH``).
4. Scores each trace 0-100 on *loop risk*.
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

    from agentlens import AgentLoopDetector

    advisor = AgentLoopDetector(risk_appetite="cautious")
    report = advisor.analyze(events)
    print(report.render_markdown())
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class LoopVerdict(Enum):
    """Per-trace loop classification."""

    HEALTHY = "healthy"
    BENIGN_RETRY = "benign_retry"
    PROGRESSING_LOOP = "progressing_loop"
    TIGHT_LOOP = "tight_loop"
    INFINITE_LOOP_SUSPECTED = "infinite_loop_suspected"
    ERROR_STORM = "error_storm"
    TOOL_THRASH = "tool_thrash"


class LoopIssueCode(Enum):
    """Specific loop signals attached to a trace."""

    REPEATED_TOOL_CALL = "repeated_tool_call"
    REPEATED_DECISION = "repeated_decision"
    ESCALATING_ERROR = "escalating_error"
    NO_PROGRESS = "no_progress"
    BOUNCING_TOOL_PAIR = "bouncing_tool_pair"
    HIGH_TOOL_RATE = "high_tool_rate"
    CONTEXT_REPETITION = "context_repetition"


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


class LoopGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


@dataclass
class LoopIssue:
    code: LoopIssueCode
    severity: int  # 0-100
    reason: str
    count: int = 0
    related_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity,
            "reason": self.reason,
            "count": self.count,
            "related_tools": list(self.related_tools),
        }


@dataclass
class LoopSnapshot:
    session_id: str
    started_at: datetime
    last_event_at: datetime
    event_count: int
    duration_seconds: float
    age_seconds: float
    error_count: int
    tool_call_count: int
    distinct_tools: int
    distinct_decisions: int
    tool_calls_per_min: float
    verdict: LoopVerdict
    loop_risk: int  # 0-100
    priority: ActionPriority
    dominant_signature: Optional[str] = None
    top_tool: Optional[str] = None
    issues: list[LoopIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "last_event_at": self.last_event_at.isoformat(),
            "event_count": self.event_count,
            "duration_seconds": round(self.duration_seconds, 2),
            "age_seconds": round(self.age_seconds, 2),
            "error_count": self.error_count,
            "tool_call_count": self.tool_call_count,
            "distinct_tools": self.distinct_tools,
            "distinct_decisions": self.distinct_decisions,
            "tool_calls_per_min": round(self.tool_calls_per_min, 2),
            "verdict": self.verdict.value,
            "loop_risk": self.loop_risk,
            "priority": self.priority.value,
            "dominant_signature": self.dominant_signature,
            "top_tool": self.top_tool,
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
    related_tools: list[str] = field(default_factory=list)
    suggested_value: Optional[float] = None

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
            "related_tools": list(self.related_tools),
            "suggested_value": self.suggested_value,
        }


@dataclass
class AgentLoopReport:
    generated_at: datetime
    window_label: str
    risk_appetite: RiskAppetite
    total_traces: int
    looping_traces: int
    infinite_suspected_count: int
    error_storm_count: int
    overall_loop_risk: int
    grade: LoopGrade
    summary: str
    snapshots: list[LoopSnapshot]
    playbook: list[PlaybookAction]
    insights: list[str]

    def actions_by_priority(self) -> dict[str, list[PlaybookAction]]:
        out: dict[str, list[PlaybookAction]] = {p.value: [] for p in ActionPriority}
        for a in self.playbook:
            out[a.priority.value].append(a)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_label": self.window_label,
            "risk_appetite": self.risk_appetite.value,
            "total_traces": self.total_traces,
            "looping_traces": self.looping_traces,
            "infinite_suspected_count": self.infinite_suspected_count,
            "error_storm_count": self.error_storm_count,
            "overall_loop_risk": self.overall_loop_risk,
            "grade": self.grade.value,
            "summary": self.summary,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": list(self.insights),
        }

    def render_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent, default=str)

    # Aliases mirroring sibling advisors.
    def to_json(self, *, indent: int = 2) -> str:
        return self.render_json(indent=indent)

    def render(self) -> str:
        return self.render_text()

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append(f"AgentLoopDetector [{self.window_label}] grade={self.grade.value}")
        lines.append(self.summary)
        lines.append(
            f"  traces total={self.total_traces} looping={self.looping_traces} "
            f"infinite_suspected={self.infinite_suspected_count} "
            f"error_storms={self.error_storm_count} "
            f"overall_risk={self.overall_loop_risk}/100"
        )

        looping = [s for s in self.snapshots if s.verdict is not LoopVerdict.HEALTHY]
        if looping:
            lines.append("")
            lines.append("Looping traces:")
            for s in looping[:10]:
                sig = f" sig={s.dominant_signature}" if s.dominant_signature else ""
                lines.append(
                    f"  [{s.priority.value}] {s.session_id} verdict={s.verdict.value} "
                    f"risk={s.loop_risk}/100 tools={s.tool_call_count} "
                    f"errors={s.error_count} rate={s.tool_calls_per_min:.1f}/min{sig}"
                )
        else:
            lines.append("")
            lines.append("No looping traces detected.")

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
        lines.append(f"# Agent Loop Detector — {self.window_label}")
        lines.append("")
        lines.append(f"**Grade:** {self.grade.value}  ")
        lines.append(f"**Overall loop risk:** {self.overall_loop_risk}/100  ")
        lines.append(f"**Risk appetite:** {self.risk_appetite.value}")
        lines.append("")
        lines.append(f"_{self.summary}_")
        lines.append("")

        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Total traces | {self.total_traces} |")
        lines.append(f"| Looping traces | {self.looping_traces} |")
        lines.append(f"| Infinite-loop suspected | {self.infinite_suspected_count} |")
        lines.append(f"| Error storms | {self.error_storm_count} |")
        lines.append("")

        looping = [s for s in self.snapshots if s.verdict is not LoopVerdict.HEALTHY]
        if looping:
            lines.append("## Looping traces")
            lines.append("")
            lines.append("| Priority | Session | Verdict | Risk | Tools | Errors | Rate/min | Signature | Issues |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for s in looping:
                codes = ", ".join(i.code.value for i in s.issues) or "—"
                sig = s.dominant_signature or "—"
                lines.append(
                    f"| {s.priority.value} | `{s.session_id}` | {s.verdict.value} | "
                    f"{s.loop_risk} | {s.tool_call_count} | {s.error_count} | "
                    f"{s.tool_calls_per_min:.1f} | {sig} | {codes} |"
                )
            lines.append("")

        if self.playbook:
            lines.append("## Playbook")
            lines.append("")
            lines.append("| Priority | Action | Owner | Blast | Reversibility | Sessions | Tools | Reason |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for a in self.playbook:
                sess = ", ".join(f"`{s}`" for s in a.related_session_ids[:3]) or "—"
                if len(a.related_session_ids) > 3:
                    sess += f" +{len(a.related_session_ids) - 3}"
                tools = ", ".join(f"`{t}`" for t in a.related_tools[:3]) or "—"
                if len(a.related_tools) > 3:
                    tools += f" +{len(a.related_tools) - 3}"
                lines.append(
                    f"| {a.priority.value} | **{a.label}** | {a.owner} | {a.blast_radius} | "
                    f"{a.reversibility} | {sess} | {tools} | {a.reason} |"
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
# Event type vocabulary
# --------------------------------------------------------------------------- #


ERROR_EVENT_TYPES = {"error", "exception", "failure", "failed", "tool_error"}
TOOL_CALL_TYPES = {"tool_call", "tool", "function_call"}
RETRY_EVENT_TYPES = {"retry", "retry_attempt"}


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


@dataclass
class AgentLoopDetector:
    """Per-trace + portfolio-level loop/thrash analyzer."""

    risk_appetite: "RiskAppetite | str" = RiskAppetite.BALANCED
    now_fn: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(timezone.utc))
    # Base thresholds — modulated by risk_appetite in __post_init__.
    repeated_call_threshold: int = 4
    high_tool_rate_per_min: float = 10.0
    silent_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        self.risk_appetite = RiskAppetite.parse(self.risk_appetite)
        if self.risk_appetite is RiskAppetite.CAUTIOUS:
            # Cry wolf earlier.
            self.repeated_call_threshold = max(2, self.repeated_call_threshold - 1)
            self.high_tool_rate_per_min *= 0.75
        elif self.risk_appetite is RiskAppetite.AGGRESSIVE:
            # Trust the system more.
            self.repeated_call_threshold = self.repeated_call_threshold + 2
            self.high_tool_rate_per_min *= 1.40

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(
        self,
        events: Iterable[Any],
        *,
        window_label: str = "current",
    ) -> AgentLoopReport:
        """Analyze a flat iterable of events grouped by ``session_id``."""

        now = _coerce_dt(self.now_fn())
        buckets: dict[str, list[dict[str, Any]]] = {}
        for raw in events:
            ev = _coerce_event(raw)
            sid = ev.get("session_id") or ""
            if not sid:
                continue
            buckets.setdefault(sid, []).append(ev)

        snapshots: list[LoopSnapshot] = []
        for sid, evs in buckets.items():
            evs.sort(key=lambda e: e.get("timestamp") or now)
            snapshots.append(self._classify(sid, evs, now))

        priority_order = {
            ActionPriority.P0: 0,
            ActionPriority.P1: 1,
            ActionPriority.P2: 2,
            ActionPriority.P3: 3,
        }
        snapshots.sort(key=lambda s: (priority_order[s.priority], -s.loop_risk, s.session_id))

        playbook = self._build_playbook(snapshots)
        insights = self._build_insights(snapshots)
        portfolio = self._portfolio_summary(snapshots)
        summary = self._summary_line(snapshots, portfolio)

        return AgentLoopReport(
            generated_at=now,
            window_label=window_label,
            risk_appetite=self.risk_appetite,  # type: ignore[arg-type]
            total_traces=len(snapshots),
            looping_traces=portfolio["looping"],
            infinite_suspected_count=portfolio["infinite"],
            error_storm_count=portfolio["error_storm"],
            overall_loop_risk=portfolio["overall_risk"],
            grade=portfolio["grade"],
            summary=summary,
            snapshots=snapshots,
            playbook=playbook,
            insights=insights,
        )

    # ------------------------------------------------------------------ #
    # Per-trace classification
    # ------------------------------------------------------------------ #

    def _classify(
        self,
        session_id: str,
        events: list[dict[str, Any]],
        now: datetime,
    ) -> LoopSnapshot:
        started_at = _coerce_dt(events[0].get("timestamp")) if events else now
        last_event_at = _coerce_dt(events[-1].get("timestamp")) if events else now
        duration = max(0.0, (last_event_at - started_at).total_seconds())
        age = max(0.0, (now - started_at).total_seconds())

        tool_signatures: list[str] = []      # canonical sig per tool call, in order
        tool_names: list[str] = []           # tool name only, in order
        decision_keys: list[str] = []
        error_fingerprints: list[str] = []
        content_hashes: list[str] = []
        error_count = 0

        for ev in events:
            etype = str(ev.get("event_type") or "").lower()
            if etype in TOOL_CALL_TYPES or ev.get("tool_call"):
                tc = ev.get("tool_call") or {}
                name = tc.get("tool_name") or tc.get("name") or "?"
                args = tc.get("tool_input") or tc.get("arguments") or {}
                tool_names.append(str(name))
                tool_signatures.append(f"{name}::{_canonical(args)}")

            if etype in ERROR_EVENT_TYPES:
                error_count += 1
                fp = (
                    ev.get("error_fingerprint")
                    or (ev.get("input_data") or {}).get("error_fingerprint")
                    or _error_fp_from(ev)
                )
                if fp:
                    error_fingerprints.append(fp)

            dt = ev.get("decision_trace")
            if dt:
                # Prefer 'key', 'trace_id' (semantic per step), or first chars of reasoning.
                key = (
                    dt.get("key")
                    if isinstance(dt, dict)
                    else None
                )
                if not key and isinstance(dt, dict):
                    key = dt.get("trace_id") or (dt.get("reasoning") or "")[:32]
                if key:
                    decision_keys.append(str(key))

            content = _extract_assistant_content(ev)
            if content:
                content_hashes.append(str(hash(content[:200])))

        tool_call_count = len(tool_signatures)
        distinct_tools = len(set(tool_names))
        distinct_decisions = len(set(decision_keys))
        rate = tool_call_count / max(duration / 60.0, 1.0) if tool_call_count else 0.0

        issues: list[LoopIssue] = []
        dominant_signature: Optional[str] = None
        top_tool: Optional[str] = None
        if tool_names:
            top_tool = Counter(tool_names).most_common(1)[0][0]

        # ---- REPEATED_TOOL_CALL ----
        repeated_count = 0
        repeated_sig: Optional[str] = None
        if tool_signatures:
            sig_counts = Counter(tool_signatures)
            repeated_sig, repeated_count = sig_counts.most_common(1)[0]
            if repeated_count >= self.repeated_call_threshold:
                sev = min(95, 55 + (repeated_count - self.repeated_call_threshold) * 8)
                issues.append(LoopIssue(
                    LoopIssueCode.REPEATED_TOOL_CALL,
                    severity=sev,
                    reason=f"Same tool invocation repeated {repeated_count}x (threshold "
                           f"{self.repeated_call_threshold}).",
                    count=repeated_count,
                    related_tools=[repeated_sig.split("::", 1)[0]],
                ))
                dominant_signature = f"tool:{repeated_sig.split('::', 1)[0]} ({repeated_count}x)"

        # ---- BOUNCING_TOOL_PAIR ----
        bounce_cycles = _detect_bouncing_pair(tool_names[-12:])
        bounce_pair: Optional[tuple[str, str]] = None
        if bounce_cycles >= 3:
            bounce_pair = _bouncing_pair_names(tool_names[-12:])
            sev = min(95, 50 + bounce_cycles * 8)
            issues.append(LoopIssue(
                LoopIssueCode.BOUNCING_TOOL_PAIR,
                severity=sev,
                reason=f"Tool pair bouncing {bounce_cycles} cycle(s) in recent calls.",
                count=bounce_cycles,
                related_tools=list(bounce_pair) if bounce_pair else [],
            ))
            if not dominant_signature and bounce_pair:
                dominant_signature = f"bounce:{bounce_pair[0]}<->{bounce_pair[1]} ({bounce_cycles}x)"

        # ---- ESCALATING_ERROR ----
        escalating_error_count = 0
        if error_fingerprints:
            fp_counts = Counter(error_fingerprints)
            top_fp, escalating_error_count = fp_counts.most_common(1)[0]
            if escalating_error_count >= 3:
                sev = min(95, 60 + escalating_error_count * 5)
                issues.append(LoopIssue(
                    LoopIssueCode.ESCALATING_ERROR,
                    severity=sev,
                    reason=f"Same error fingerprint repeated {escalating_error_count}x.",
                    count=escalating_error_count,
                ))

        # ---- REPEATED_DECISION ----
        if decision_keys:
            dk_counts = Counter(decision_keys)
            top_dk, top_dk_n = dk_counts.most_common(1)[0]
            if top_dk_n >= 3:
                sev = min(85, 45 + top_dk_n * 5)
                issues.append(LoopIssue(
                    LoopIssueCode.REPEATED_DECISION,
                    severity=sev,
                    reason=f"Decision key revisited {top_dk_n}x.",
                    count=top_dk_n,
                ))

        # ---- NO_PROGRESS ----
        if (
            len(events) >= 20
            and distinct_decisions <= 1
            and distinct_tools <= 2
        ):
            issues.append(LoopIssue(
                LoopIssueCode.NO_PROGRESS,
                severity=60,
                reason=f"{len(events)} events but only {distinct_decisions} decision(s) "
                       f"and {distinct_tools} tool(s).",
                count=len(events),
            ))

        # ---- HIGH_TOOL_RATE ----
        high_rate = False
        if rate > self.high_tool_rate_per_min and tool_call_count >= 5:
            high_rate = True
            sev = min(80, 40 + int((rate - self.high_tool_rate_per_min) * 2))
            issues.append(LoopIssue(
                LoopIssueCode.HIGH_TOOL_RATE,
                severity=sev,
                reason=f"{rate:.1f} tool calls/min sustained "
                       f"(threshold {self.high_tool_rate_per_min:.1f}).",
                count=int(rate),
            ))

        # ---- CONTEXT_REPETITION ----
        if content_hashes:
            ch_counts = Counter(content_hashes)
            top_ch, top_ch_n = ch_counts.most_common(1)[0]
            if top_ch_n >= 3:
                sev = min(80, 40 + top_ch_n * 5)
                issues.append(LoopIssue(
                    LoopIssueCode.CONTEXT_REPETITION,
                    severity=sev,
                    reason=f"Assistant content repeated {top_ch_n}x verbatim.",
                    count=top_ch_n,
                ))

        # ------------------------------------------------------------------ #
        # Verdict ladder
        # ------------------------------------------------------------------ #

        verdict: LoopVerdict
        if repeated_count >= 8 or bounce_cycles >= 5:
            verdict = LoopVerdict.INFINITE_LOOP_SUSPECTED
        elif escalating_error_count >= 5:
            verdict = LoopVerdict.ERROR_STORM
        elif high_rate and distinct_tools >= 3:
            verdict = LoopVerdict.TOOL_THRASH
        elif repeated_count >= self.repeated_call_threshold or bounce_cycles >= 3:
            verdict = LoopVerdict.TIGHT_LOOP
        elif repeated_count >= 2 and distinct_decisions >= 2:
            verdict = LoopVerdict.PROGRESSING_LOOP
        elif _retry_signal(events) == 1 or repeated_count == 2:
            verdict = LoopVerdict.BENIGN_RETRY
        else:
            verdict = LoopVerdict.HEALTHY

        # ------------------------------------------------------------------ #
        # Loop risk
        # ------------------------------------------------------------------ #

        base_by_verdict = {
            LoopVerdict.INFINITE_LOOP_SUSPECTED: 90,
            LoopVerdict.ERROR_STORM: 85,
            LoopVerdict.TOOL_THRASH: 70,
            LoopVerdict.TIGHT_LOOP: 55,
            LoopVerdict.PROGRESSING_LOOP: 30,
            LoopVerdict.BENIGN_RETRY: 15,
            LoopVerdict.HEALTHY: 0,
        }
        risk = base_by_verdict[verdict]
        # Count pressure (max repeated count contribution).
        count_pressure = min(15, max(0, repeated_count - self.repeated_call_threshold) * 2)
        risk += count_pressure
        # Long-running loop pressure.
        if duration > 300 and verdict not in (LoopVerdict.HEALTHY, LoopVerdict.BENIGN_RETRY):
            risk += 5

        if self.risk_appetite is RiskAppetite.CAUTIOUS:
            risk = int(round(risk * 1.15))
        elif self.risk_appetite is RiskAppetite.AGGRESSIVE:
            risk = int(round(risk * 0.85))
        risk = max(0, min(100, int(risk)))

        # Priority bucket.
        if (
            verdict in (LoopVerdict.INFINITE_LOOP_SUSPECTED, LoopVerdict.ERROR_STORM)
            or risk >= 80
        ):
            priority = ActionPriority.P0
        elif verdict in (LoopVerdict.TIGHT_LOOP, LoopVerdict.TOOL_THRASH) or risk >= 60:
            priority = ActionPriority.P1
        elif risk >= 35:
            priority = ActionPriority.P2
        else:
            priority = ActionPriority.P3

        # Set a dominant signature for TIGHT_LOOP / INFINITE if not set.
        if not dominant_signature:
            if verdict in (LoopVerdict.TIGHT_LOOP, LoopVerdict.INFINITE_LOOP_SUSPECTED):
                if repeated_sig:
                    dominant_signature = (
                        f"tool:{repeated_sig.split('::', 1)[0]} ({repeated_count}x)"
                    )
                elif top_tool:
                    dominant_signature = f"tool:{top_tool}"
            elif verdict is LoopVerdict.ERROR_STORM and error_fingerprints:
                dominant_signature = f"error:{error_fingerprints[0][:40]}"

        issues.sort(key=lambda i: (-i.severity, i.code.value))

        return LoopSnapshot(
            session_id=session_id,
            started_at=started_at,
            last_event_at=last_event_at,
            event_count=len(events),
            duration_seconds=duration,
            age_seconds=age,
            error_count=error_count,
            tool_call_count=tool_call_count,
            distinct_tools=distinct_tools,
            distinct_decisions=distinct_decisions,
            tool_calls_per_min=rate,
            verdict=verdict,
            loop_risk=risk,
            priority=priority,
            dominant_signature=dominant_signature,
            top_tool=top_tool,
            issues=issues,
        )

    # ------------------------------------------------------------------ #
    # Cross-trace playbook + insights
    # ------------------------------------------------------------------ #

    def _build_playbook(self, snaps: list[LoopSnapshot]) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []

        infinite = [s for s in snaps if s.verdict is LoopVerdict.INFINITE_LOOP_SUSPECTED]
        error_storms = [s for s in snaps if s.verdict is LoopVerdict.ERROR_STORM]
        tight = [s for s in snaps if s.verdict is LoopVerdict.TIGHT_LOOP]
        thrash = [s for s in snaps if s.verdict is LoopVerdict.TOOL_THRASH]
        repeated_tool = [
            s for s in snaps
            if any(i.code is LoopIssueCode.REPEATED_TOOL_CALL for i in s.issues)
        ]
        repeated_decision = [
            s for s in snaps
            if any(i.code is LoopIssueCode.REPEATED_DECISION for i in s.issues)
        ]

        # Tools that appear in 2+ looping traces (for circuit-break).
        tool_to_sessions: dict[str, set[str]] = {}
        for s in snaps:
            if s.verdict is LoopVerdict.HEALTHY:
                continue
            if s.top_tool:
                tool_to_sessions.setdefault(s.top_tool, set()).add(s.session_id)
        recurring_tools = [(t, sids) for t, sids in tool_to_sessions.items() if len(sids) >= 2]

        if infinite:
            actions.append(PlaybookAction(
                id="force_terminate_loops",
                priority=ActionPriority.P0,
                label="Force-terminate suspected infinite loops",
                reason=f"{len(infinite)} trace(s) show signatures consistent with an infinite "
                       f"tool/bouncing-pair loop; kill them to free worker capacity.",
                owner="on_call",
                blast_radius=3,
                reversibility="low",
                related_session_ids=[s.session_id for s in infinite],
            ))

        if recurring_tools:
            tools_sorted = sorted(recurring_tools, key=lambda x: (-len(x[1]), x[0]))
            tool_names = [t for t, _ in tools_sorted]
            all_sids: list[str] = []
            seen: set[str] = set()
            for _, sids in tools_sorted:
                for sid in sids:
                    if sid not in seen:
                        seen.add(sid)
                        all_sids.append(sid)
            actions.append(PlaybookAction(
                id="circuit_break_tool",
                priority=ActionPriority.P0,
                label="Circuit-break recurring loop tool(s)",
                reason=f"{len(tools_sorted)} tool(s) appear in 2+ looping traces; "
                       f"add a circuit breaker so a single bad tool can't drown the fleet.",
                owner="tool_owner",
                blast_radius=4,
                reversibility="medium",
                related_session_ids=all_sids,
                related_tools=tool_names,
            ))

        if error_storms:
            actions.append(PlaybookAction(
                id="triage_error_storm",
                priority=ActionPriority.P0,
                label="Triage repeating error storm",
                reason=f"{len(error_storms)} trace(s) hit the same error >=5x; "
                       f"investigate the root error before they retry forever.",
                owner="service_owner",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[s.session_id for s in error_storms],
            ))

        if tight or thrash:
            sids = [s.session_id for s in (tight + thrash)]
            # Suggest a per-trace cap = max observed repeated count or 8, whichever lower.
            max_rep = max(
                (
                    next((i.count for i in s.issues if i.code is LoopIssueCode.REPEATED_TOOL_CALL), 0)
                    for s in (tight + thrash)
                ),
                default=self.repeated_call_threshold,
            )
            cap = max(self.repeated_call_threshold, min(max_rep, 8))
            actions.append(PlaybookAction(
                id="cap_tool_invocations",
                priority=ActionPriority.P1,
                label="Cap tool invocations per trace",
                reason=f"{len(sids)} trace(s) showed tight loops or thrash; enforce a "
                       f"per-tool, per-trace cap to bound runaway cost.",
                owner="agent_dev",
                blast_radius=2,
                reversibility="high",
                related_session_ids=sids,
                suggested_value=float(cap),
            ))

        if repeated_tool:
            actions.append(PlaybookAction(
                id="add_state_memoization",
                priority=ActionPriority.P1,
                label="Add state memoization for repeated tool calls",
                reason=f"{len(repeated_tool)} trace(s) repeat identical tool invocations; "
                       f"memoize results so the agent stops re-asking the same question.",
                owner="agent_dev",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[s.session_id for s in repeated_tool],
            ))

        if repeated_decision:
            actions.append(PlaybookAction(
                id="tighten_retry_backoff",
                priority=ActionPriority.P1,
                label="Tighten retry backoff on revisited decisions",
                reason=f"{len(repeated_decision)} trace(s) revisit the same decision key; "
                       f"add exponential backoff or a max-revisits guard.",
                owner="service_owner",
                blast_radius=2,
                reversibility="high",
                related_session_ids=[s.session_id for s in repeated_decision],
            ))

        if any(s.verdict is not LoopVerdict.HEALTHY for s in snaps) and not actions:
            # Edge: low-risk loops only.
            actions.append(PlaybookAction(
                id="enable_loop_alarm",
                priority=ActionPriority.P2,
                label="Enable loop-detection alarm",
                reason="Low-risk loops present; wire a recurring loop alarm so any "
                       "escalation gets caught early.",
                owner="platform",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[
                    s.session_id for s in snaps if s.verdict is not LoopVerdict.HEALTHY
                ],
            ))

        # Aggressive: drop P2 noise.
        if self.risk_appetite is RiskAppetite.AGGRESSIVE:
            actions = [a for a in actions if a.priority is not ActionPriority.P2]

        if not actions:
            actions.append(PlaybookAction(
                id="no_action_needed",
                priority=ActionPriority.P3,
                label="No loops detected",
                reason="No tight loops, infinite-loop signatures, or error storms found.",
                owner="platform",
                blast_radius=1,
                reversibility="high",
                related_session_ids=[],
            ))

        priority_order = {
            ActionPriority.P0: 0,
            ActionPriority.P1: 1,
            ActionPriority.P2: 2,
            ActionPriority.P3: 3,
        }
        actions.sort(key=lambda a: (priority_order[a.priority], a.id))
        return actions

    def _build_insights(self, snaps: list[LoopSnapshot]) -> list[str]:
        out: list[str] = []
        if not snaps:
            out.append("NO_TRACES: nothing to analyze in this window.")
            return out

        looping = [s for s in snaps if s.verdict is not LoopVerdict.HEALTHY]
        if not looping:
            out.append("HEALTHY_FLEET: no looping traces detected.")
            return out

        # Tool cluster.
        tool_counts: Counter[str] = Counter()
        for s in looping:
            if s.top_tool:
                tool_counts[s.top_tool] += 1
        for tool, n in tool_counts.most_common(3):
            if n >= 2:
                out.append(
                    f"TOOL_LOOP_CLUSTER: {n} looping traces share top tool `{tool}`."
                )
                break

        # Error storm cluster.
        storms = sum(1 for s in snaps if s.verdict is LoopVerdict.ERROR_STORM)
        if storms >= 2:
            out.append(f"ERROR_STORM_CLUSTER: {storms} traces in concurrent error storms.")

        # Decision revisit pattern.
        decision_revisits = sum(
            1 for s in snaps
            if any(i.code is LoopIssueCode.REPEATED_DECISION for i in s.issues)
        )
        if decision_revisits >= 2:
            out.append(
                f"DECISION_REVISIT_PATTERN: {decision_revisits} traces revisit decisions."
            )

        # High-throughput loops.
        if looping:
            avg_rate = sum(s.tool_calls_per_min for s in looping) / len(looping)
            if avg_rate > 20:
                out.append(
                    f"HIGH_THROUGHPUT_LOOPS: looping traces averaging {avg_rate:.1f} "
                    f"tool calls/min — token burn likely elevated."
                )

        return out

    def _portfolio_summary(self, snaps: list[LoopSnapshot]) -> dict[str, Any]:
        total = len(snaps)
        looping = sum(1 for s in snaps if s.verdict is not LoopVerdict.HEALTHY)
        infinite = sum(1 for s in snaps if s.verdict is LoopVerdict.INFINITE_LOOP_SUSPECTED)
        error_storm = sum(1 for s in snaps if s.verdict is LoopVerdict.ERROR_STORM)
        overall_risk = max((s.loop_risk for s in snaps), default=0)

        if total == 0:
            grade = LoopGrade.A
        elif infinite >= 1 or overall_risk >= 80:
            grade = LoopGrade.F
        elif overall_risk >= 60:
            grade = LoopGrade.D
        elif overall_risk >= 40:
            grade = LoopGrade.C
        elif overall_risk >= 20:
            grade = LoopGrade.B
        else:
            grade = LoopGrade.A

        return {
            "looping": looping,
            "infinite": infinite,
            "error_storm": error_storm,
            "overall_risk": overall_risk,
            "grade": grade,
        }

    @staticmethod
    def _summary_line(snaps: list[LoopSnapshot], portfolio: dict[str, Any]) -> str:
        total = len(snaps)
        if total == 0:
            return "No traces in window."
        parts = [f"{portfolio['looping']}/{total} looping"]
        if portfolio["infinite"]:
            parts.append(f"{portfolio['infinite']} infinite-suspected")
        if portfolio["error_storm"]:
            parts.append(f"{portfolio['error_storm']} error-storm")
        return "Agent loop posture: " + ", ".join(parts) + "."


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _coerce_dt(value: Any) -> datetime:
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
    """Return a *copy* of ``raw`` as a plain dict."""
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            return dump()  # type: ignore[no-any-return]
        except Exception:
            pass
    dump = getattr(raw, "dict", None)
    if callable(dump):
        try:
            return dump()  # type: ignore[no-any-return]
        except Exception:
            pass
    # Attribute fallback.
    out: dict[str, Any] = {}
    for attr in (
        "event_id", "session_id", "event_type", "timestamp", "input_data",
        "output_data", "model", "tokens_in", "tokens_out", "tool_call",
        "decision_trace", "duration_ms",
    ):
        if hasattr(raw, attr):
            v = getattr(raw, attr)
            if hasattr(v, "model_dump"):
                try:
                    v = v.model_dump()
                except Exception:
                    pass
            elif hasattr(v, "dict"):
                try:
                    v = v.dict()
                except Exception:
                    pass
            out[attr] = v
    return out


def _canonical(obj: Any) -> str:
    """Canonical JSON-ish serialization for hashing tool args."""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(obj)


def _error_fp_from(ev: dict[str, Any]) -> str:
    """Synthesize an error fingerprint from message text if absent."""
    msg = (
        ev.get("error_message")
        or (ev.get("input_data") or {}).get("error")
        or (ev.get("output_data") or {}).get("error")
        or ""
    )
    if isinstance(msg, dict):
        msg = msg.get("message") or msg.get("error") or ""
    return str(msg)[:60].strip()


def _extract_assistant_content(ev: dict[str, Any]) -> str:
    """Pull assistant content from an event for context-repetition hashing."""
    out_data = ev.get("output_data") or {}
    if isinstance(out_data, dict):
        for key in ("content", "text", "message", "response"):
            v = out_data.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


def _detect_bouncing_pair(tool_names: list[str]) -> int:
    """Count A-B-A-B... cycles in the recent tool stream.

    A cycle is an A->B or B->A transition where the two tools alternate.
    Returns the number of *full* (A->B->A) cycles seen, or 0 if no clear pair.
    """
    if len(tool_names) < 4:
        return 0
    # Find the dominant pair in the last window.
    pair_counts: Counter[tuple[str, str]] = Counter()
    for i in range(len(tool_names) - 1):
        a, b = tool_names[i], tool_names[i + 1]
        if a != b:
            key = tuple(sorted((a, b)))
            pair_counts[key] += 1
    if not pair_counts:
        return 0
    (best_a, best_b), _ = pair_counts.most_common(1)[0]
    # Count alternation runs.
    cycles = 0
    streak = 0
    last: Optional[str] = None
    for name in tool_names:
        if name not in (best_a, best_b):
            streak = 0
            last = None
            continue
        if last is None:
            last = name
            streak = 1
            continue
        if name != last:
            streak += 1
            last = name
            if streak >= 3:
                # Every 2 alternations beyond the first = 1 cycle.
                cycles = (streak - 1) // 2
        else:
            streak = 1
            last = name
    return cycles


def _bouncing_pair_names(tool_names: list[str]) -> Optional[tuple[str, str]]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    for i in range(len(tool_names) - 1):
        a, b = tool_names[i], tool_names[i + 1]
        if a != b:
            key = tuple(sorted((a, b)))
            pair_counts[key] += 1
    if not pair_counts:
        return None
    return pair_counts.most_common(1)[0][0]


def _retry_signal(events: list[dict[str, Any]]) -> int:
    return sum(
        1 for ev in events
        if str(ev.get("event_type") or "").lower() in RETRY_EVENT_TYPES
    )


__all__ = [
    "AgentLoopDetector",
    "AgentLoopReport",
    "LoopSnapshot",
    "LoopIssue",
    "LoopIssueCode",
    "LoopVerdict",
    "PlaybookAction",
    "ActionPriority",
    "RiskAppetite",
    "LoopGrade",
]
