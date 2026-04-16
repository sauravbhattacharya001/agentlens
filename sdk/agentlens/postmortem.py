"""Incident Postmortem Generator for AgentLens.

Analyzes a session's events to produce a structured incident postmortem
document when errors, anomalies, or SLA violations occur.  Generates a
timeline of significant events, identifies root causes through error
correlation and dependency analysis, estimates blast radius and user
impact, and produces actionable remediation suggestions.

Typical usage::

    from agentlens.postmortem import PostmortemGenerator, PostmortemConfig

    gen = PostmortemGenerator()
    report = gen.generate(events)
    print(report.to_markdown())
    # Or export as dict/JSON:
    d = report.to_dict()
"""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(Enum):
    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"
    SEV4 = "SEV-4"


class IncidentPhase(Enum):
    DETECTION = "detection"
    ESCALATION = "escalation"
    MITIGATION = "mitigation"
    RESOLUTION = "resolution"
    POST_INCIDENT = "post_incident"


class RemediationCategory(Enum):
    IMMEDIATE = "immediate"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


@dataclass
class TimelineEntry:
    timestamp: str
    elapsed_ms: float
    event_type: str
    description: str
    severity: str
    phase: IncidentPhase
    event_id: str = ""


@dataclass
class RootCause:
    cause_id: str
    description: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    category: str = "unknown"
    affected_events: int = 0


@dataclass
class ImpactAssessment:
    severity: Severity
    error_count: int
    total_events: int
    error_rate: float
    affected_tools: list[str]
    affected_models: list[str]
    downtime_ms: float
    tokens_wasted: int
    estimated_cost_impact: float
    user_facing: bool


@dataclass
class Remediation:
    action: str
    category: RemediationCategory
    priority: int
    rationale: str
    owner: str = "engineering"


@dataclass
class LessonLearned:
    lesson: str
    category: str
    action_item: str


@dataclass
class PostmortemReport:
    incident_id: str
    title: str
    severity: Severity
    summary: str
    duration_ms: float
    timeline: list[TimelineEntry]
    root_causes: list[RootCause]
    impact: ImpactAssessment
    remediations: list[Remediation]
    lessons_learned: list[LessonLearned]
    contributing_factors: list[str]
    what_went_well: list[str]
    generated_at: str = ""
    session_id: str = ""
    event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity.value,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
            "event_count": self.event_count,
            "generated_at": self.generated_at,
            "timeline": [
                {"timestamp": e.timestamp, "elapsed_ms": e.elapsed_ms,
                 "event_type": e.event_type, "description": e.description,
                 "severity": e.severity, "phase": e.phase.value}
                for e in self.timeline
            ],
            "root_causes": [
                {"cause_id": rc.cause_id, "description": rc.description,
                 "confidence": round(rc.confidence, 2), "evidence": rc.evidence,
                 "category": rc.category, "affected_events": rc.affected_events}
                for rc in self.root_causes
            ],
            "impact": {
                "severity": self.impact.severity.value,
                "error_count": self.impact.error_count,
                "total_events": self.impact.total_events,
                "error_rate": round(self.impact.error_rate, 4),
                "affected_tools": self.impact.affected_tools,
                "affected_models": self.impact.affected_models,
                "downtime_ms": round(self.impact.downtime_ms, 1),
                "tokens_wasted": self.impact.tokens_wasted,
                "estimated_cost_impact": round(self.impact.estimated_cost_impact, 4),
                "user_facing": self.impact.user_facing,
            },
            "remediations": [
                {"action": r.action, "category": r.category.value,
                 "priority": r.priority, "rationale": r.rationale, "owner": r.owner}
                for r in self.remediations
            ],
            "lessons_learned": [
                {"lesson": ll.lesson, "category": ll.category,
                 "action_item": ll.action_item}
                for ll in self.lessons_learned
            ],
            "contributing_factors": self.contributing_factors,
            "what_went_well": self.what_went_well,
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Incident Postmortem: {self.title}")
        lines.append("")
        lines.append(f"**Incident ID:** {self.incident_id}  ")
        lines.append(f"**Severity:** {self.severity.value}  ")
        lines.append(f"**Duration:** {self.duration_ms / 1000:.1f}s  ")
        lines.append(f"**Session:** {self.session_id}  ")
        lines.append(f"**Generated:** {self.generated_at}  ")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(self.summary)
        lines.append("")
        lines.append("## Timeline")
        lines.append("")
        lines.append("| Time (ms) | Type | Description | Severity | Phase |")
        lines.append("|-----------|------|-------------|----------|-------|")
        for entry in self.timeline:
            lines.append(
                f"| {entry.elapsed_ms:.0f} | {entry.event_type} | "
                f"{entry.description} | {entry.severity} | {entry.phase.value} |"
            )
        lines.append("")
        lines.append("## Root Cause Analysis")
        lines.append("")
        for i, rc in enumerate(self.root_causes, 1):
            lines.append(f"### {i}. {rc.description} ({rc.confidence * 100:.0f}% confidence)")
            lines.append(f"- **Category:** {rc.category}")
            lines.append(f"- **Affected events:** {rc.affected_events}")
            for ev in rc.evidence:
                lines.append(f"- {ev}")
            lines.append("")
        lines.append("## Impact Assessment")
        lines.append("")
        lines.append(f"- **Severity:** {self.impact.severity.value}")
        lines.append(f"- **Errors:** {self.impact.error_count}/{self.impact.total_events} events ({self.impact.error_rate:.1%})")
        lines.append(f"- **Downtime:** {self.impact.downtime_ms / 1000:.1f}s")
        lines.append(f"- **Tokens wasted:** {self.impact.tokens_wasted:,}")
        lines.append(f"- **Est. cost impact:** ${self.impact.estimated_cost_impact:.4f}")
        lines.append(f"- **Affected tools:** {', '.join(self.impact.affected_tools) or 'none'}")
        lines.append(f"- **Affected models:** {', '.join(self.impact.affected_models) or 'none'}")
        lines.append(f"- **User-facing:** {'Yes' if self.impact.user_facing else 'No'}")
        lines.append("")
        if self.contributing_factors:
            lines.append("## Contributing Factors")
            lines.append("")
            for cf in self.contributing_factors:
                lines.append(f"- {cf}")
            lines.append("")
        lines.append("## Remediation Actions")
        lines.append("")
        lines.append("| Priority | Action | Category | Owner |")
        lines.append("|----------|--------|----------|-------|")
        for r in sorted(self.remediations, key=lambda x: x.priority):
            lines.append(f"| P{r.priority} | {r.action} | {r.category.value} | {r.owner} |")
        lines.append("")
        if self.lessons_learned:
            lines.append("## Lessons Learned")
            lines.append("")
            for ll in self.lessons_learned:
                lines.append(f"### {ll.lesson}")
                lines.append(f"- **Category:** {ll.category}")
                lines.append(f"- **Action item:** {ll.action_item}")
                lines.append("")
        if self.what_went_well:
            lines.append("## What Went Well")
            lines.append("")
            for w in self.what_went_well:
                lines.append(f"- {w}")
            lines.append("")
        return "\n".join(lines)


@dataclass
class PostmortemConfig:
    cost_per_1k_input: float = 0.003
    cost_per_1k_output: float = 0.015
    sev1_error_rate: float = 0.50
    sev2_error_rate: float = 0.25
    sev3_error_rate: float = 0.10
    slow_event_ms: float = 10000.0
    timeout_event_ms: float = 30000.0
    min_events: int = 2
    error_types: tuple[str, ...] = (
        "error", "tool_error", "agent_error", "timeout", "rate_limit",
    )


class PostmortemGenerator:
    """Generates structured incident postmortems from session events."""

    def __init__(self, config: PostmortemConfig | None = None) -> None:
        self.config = config or PostmortemConfig()

    def generate(self, events: list[dict[str, Any]], session_id: str = "") -> PostmortemReport:
        """Produce a :class:`PostmortemReport` from a list of session events.

        The events are sorted by timestamp, filtered for errors, and then
        analysed through several stages: timeline construction, root-cause
        identification, impact assessment, remediation generation, and
        lesson extraction.

        Args:
            events: Session event dicts.  Each event should contain at
                least ``event_type`` and ``timestamp``; optional keys
                include ``duration_ms``, ``tokens_in``, ``tokens_out``,
                ``tool_call``, ``model``, ``error_message``, and
                ``output_data``.
            session_id: An optional session identifier embedded in the
                report for traceability.

        Returns:
            A fully populated :class:`PostmortemReport`.  If fewer than
            ``config.min_events`` events are provided or no errors are
            found, an empty "no incident" report is returned.
        """
        if len(events) < self.config.min_events:
            return self._empty_report(session_id)
        sorted_events = sorted(events, key=lambda e: str(e.get("timestamp", "")))
        errors = [e for e in sorted_events if self._is_error(e)]
        if not errors:
            return self._empty_report(session_id)

        timeline = self._build_timeline(sorted_events, errors)
        root_causes = self._identify_root_causes(sorted_events, errors)
        impact = self._assess_impact(sorted_events, errors)
        remediations = self._generate_remediations(root_causes, impact)
        lessons = self._extract_lessons(root_causes, impact)
        contributing = self._find_contributing_factors(sorted_events, errors)
        went_well = self._find_what_went_well(sorted_events, errors)

        first_ts = self._parse_ts(sorted_events[0].get("timestamp", ""))
        last_ts = self._parse_ts(sorted_events[-1].get("timestamp", ""))
        duration_ms = (last_ts - first_ts).total_seconds() * 1000 if first_ts and last_ts else 0

        content_hash = hashlib.sha256(
            f"{session_id}:{len(errors)}:{impact.severity.value}".encode()
        ).hexdigest()[:12]
        incident_id = f"INC-{content_hash[:8].upper()}"
        primary_cause = root_causes[0].description if root_causes else "Unknown error"
        title = f"{impact.severity.value}: {primary_cause}"
        summary = self._generate_summary(impact, root_causes, duration_ms)

        return PostmortemReport(
            incident_id=incident_id, title=title, severity=impact.severity,
            summary=summary, duration_ms=duration_ms, timeline=timeline,
            root_causes=root_causes, impact=impact, remediations=remediations,
            lessons_learned=lessons, contributing_factors=contributing,
            what_went_well=went_well,
            generated_at=datetime.now(timezone.utc).isoformat(),
            session_id=session_id, event_count=len(sorted_events),
        )

    def _build_timeline(self, events: list[dict], errors: list[dict]) -> list[TimelineEntry]:
        """Build a filtered timeline of significant events.

        Only events that are errors, abnormally slow, or the first/last
        event in the session are included.  Each entry is annotated with
        elapsed time from the first event, severity, and incident phase.
        """
        timeline: list[TimelineEntry] = []
        first_ts = self._parse_ts(events[0].get("timestamp", ""))
        error_ids = {id(e) for e in errors}

        for event in events:
            ts = self._parse_ts(event.get("timestamp", ""))
            elapsed = (ts - first_ts).total_seconds() * 1000 if ts and first_ts else 0
            is_error = id(event) in error_ids
            is_slow = (event.get("duration_ms") or 0) > self.config.slow_event_ms
            is_first = event is events[0]
            is_last = event is events[-1]

            if is_error or is_slow or is_first or is_last:
                severity = "error" if is_error else ("warning" if is_slow else "info")
                phase = self._classify_phase(event, events, errors)
                description = self._describe_event(event)
                timeline.append(TimelineEntry(
                    timestamp=str(event.get("timestamp", "")),
                    elapsed_ms=elapsed, event_type=event.get("event_type", "unknown"),
                    description=description, severity=severity, phase=phase,
                    event_id=event.get("event_id", ""),
                ))
        return timeline

    def _classify_phase(self, event: dict, all_events: list[dict], errors: list[dict]) -> IncidentPhase:
        """Assign an :class:`IncidentPhase` to *event* based on its
        temporal position relative to the error window."""
        if not errors:
            return IncidentPhase.POST_INCIDENT
        first_error_ts = self._parse_ts(errors[0].get("timestamp", ""))
        last_error_ts = self._parse_ts(errors[-1].get("timestamp", ""))
        event_ts = self._parse_ts(event.get("timestamp", ""))
        if not event_ts or not first_error_ts:
            return IncidentPhase.DETECTION
        if event_ts < first_error_ts:
            return IncidentPhase.DETECTION
        elif event_ts == first_error_ts:
            return IncidentPhase.DETECTION
        elif self._is_error(event):
            error_count_before = sum(
                1 for e in errors
                if self._parse_ts(e.get("timestamp", "")) and
                self._parse_ts(e.get("timestamp", "")) <= event_ts
            )
            return IncidentPhase.MITIGATION if error_count_before > len(errors) // 2 else IncidentPhase.ESCALATION
        elif last_error_ts and event_ts > last_error_ts:
            return IncidentPhase.RESOLUTION
        else:
            return IncidentPhase.MITIGATION

    def _describe_event(self, event: dict) -> str:
        """Return a concise human-readable description of *event*."""
        etype = event.get("event_type", "unknown")
        parts: list[str] = []
        if etype in ("error", "tool_error", "agent_error"):
            msg = event.get("error_message", "")
            if not msg and event.get("output_data"):
                msg = str(event["output_data"].get("error", ""))[:100]
            parts.append(f"Error: {msg[:120]}" if msg else "Error occurred")
        elif etype == "tool_call":
            tc = event.get("tool_call", {})
            tool_name = tc.get("tool_name", "unknown") if isinstance(tc, dict) else "unknown"
            parts.append(f"Tool call: {tool_name}")
        elif etype == "llm_call":
            parts.append(f"LLM call: {event.get('model', 'unknown')}")
        elif etype == "timeout":
            parts.append(f"Timeout after {event.get('duration_ms', 0):.0f}ms")
        elif etype == "rate_limit":
            parts.append("Rate limit hit")
        else:
            parts.append(f"{etype} event")
        dur = event.get("duration_ms")
        if dur and dur > self.config.slow_event_ms:
            parts.append(f"(slow: {dur:.0f}ms)")
        return " ".join(parts)

    def _identify_root_causes(self, events: list[dict], errors: list[dict]) -> list[RootCause]:
        """Correlate errors to identify probable root causes.

        Analyses error events across several dimensions — failing tools,
        failing models, error-interval acceleration (cascading failures),
        timeouts, rate limits, and repeated error messages — and returns
        a list of :class:`RootCause` entries sorted by confidence
        (highest first).
        """
        causes: list[RootCause] = []
        error_tools: Counter[str] = Counter()
        error_models: Counter[str] = Counter()
        error_messages: Counter[str] = Counter()

        for e in errors:
            tc = e.get("tool_call", {})
            if isinstance(tc, dict) and tc.get("tool_name"):
                error_tools[tc["tool_name"]] += 1
            if e.get("model"):
                error_models[e["model"]] += 1
            msg = e.get("error_message", "")
            if not msg and e.get("output_data"):
                msg = str(e["output_data"].get("error", ""))[:80]
            if msg:
                error_messages[msg[:80]] += 1

        if error_tools:
            top_tool, tool_count = error_tools.most_common(1)[0]
            causes.append(RootCause(
                cause_id=f"RC-TOOL-{hashlib.md5(top_tool.encode(), usedforsecurity=False).hexdigest()[:6]}",
                description=f"Tool '{top_tool}' failures",
                confidence=min(1.0, tool_count / max(len(errors), 1) + 0.1),
                evidence=[f"Tool '{top_tool}' failed {tool_count} times",
                          f"Represents {tool_count/len(errors):.0%} of all errors"],
                category="tool_failure", affected_events=tool_count,
            ))

        if error_models:
            top_model, model_count = error_models.most_common(1)[0]
            causes.append(RootCause(
                cause_id=f"RC-MODEL-{hashlib.md5(top_model.encode(), usedforsecurity=False).hexdigest()[:6]}",
                description=f"Model '{top_model}' errors",
                confidence=min(1.0, model_count / max(len(errors), 1) + 0.05),
                evidence=[f"Model '{top_model}' produced {model_count} errors",
                          f"Represents {model_count/len(errors):.0%} of all errors"],
                category="model_error", affected_events=model_count,
            ))

        if len(errors) >= 3:
            timestamps = [self._parse_ts(e.get("timestamp", "")) for e in errors]
            timestamps = [t for t in timestamps if t is not None]
            if len(timestamps) >= 3:
                intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]
                accelerating = all(intervals[i] <= intervals[i-1] for i in range(1, len(intervals))) if len(intervals) >= 2 else False
                if accelerating:
                    causes.append(RootCause(
                        cause_id="RC-CASCADE-001",
                        description="Cascading failure pattern detected",
                        confidence=0.7,
                        evidence=["Error intervals are decreasing (accelerating failures)",
                                  f"First interval: {intervals[0]:.1f}s, last: {intervals[-1]:.1f}s"],
                        category="cascading_failure", affected_events=len(errors),
                    ))

        timeouts = [e for e in errors if e.get("event_type") == "timeout"]
        if timeouts:
            durations = [e.get("duration_ms", 0) for e in timeouts]
            avg_dur = statistics.mean(durations) if durations else 0
            causes.append(RootCause(
                cause_id="RC-TIMEOUT-001",
                description=f"{len(timeouts)} timeout(s) detected",
                confidence=0.8,
                evidence=[f"{len(timeouts)} events exceeded timeout threshold",
                          f"Avg duration: {avg_dur:.0f}ms"],
                category="timeout", affected_events=len(timeouts),
            ))

        rate_limits = [e for e in errors if e.get("event_type") == "rate_limit"]
        if rate_limits:
            causes.append(RootCause(
                cause_id="RC-RATELIMIT-001",
                description=f"Rate limited {len(rate_limits)} time(s)",
                confidence=0.9,
                evidence=[f"{len(rate_limits)} rate limit events detected",
                          "Agent may be sending requests too quickly"],
                category="rate_limit", affected_events=len(rate_limits),
            ))

        if error_messages:
            top_msg, msg_count = error_messages.most_common(1)[0]
            if msg_count >= 2:
                causes.append(RootCause(
                    cause_id=f"RC-REPEAT-{hashlib.md5(top_msg.encode(), usedforsecurity=False).hexdigest()[:6]}",
                    description=f"Repeated error: {top_msg[:60]}",
                    confidence=min(1.0, msg_count / len(errors)),
                    evidence=[f"Same error occurred {msg_count} times",
                              "Suggests a systematic issue, not a transient failure"],
                    category="repeated_error", affected_events=msg_count,
                ))

        causes.sort(key=lambda c: c.confidence, reverse=True)
        return causes

    def _assess_impact(self, events: list[dict], errors: list[dict]) -> ImpactAssessment:
        """Quantify the blast radius: severity, error rate, affected
        tools/models, downtime, wasted tokens, and estimated cost."""
        total = len(events)
        error_count = len(errors)
        error_rate = error_count / max(total, 1)

        if error_rate >= self.config.sev1_error_rate:
            severity = Severity.SEV1
        elif error_rate >= self.config.sev2_error_rate:
            severity = Severity.SEV2
        elif error_rate >= self.config.sev3_error_rate:
            severity = Severity.SEV3
        else:
            severity = Severity.SEV4

        affected_tools: set[str] = set()
        affected_models: set[str] = set()
        for e in errors:
            tc = e.get("tool_call", {})
            if isinstance(tc, dict) and tc.get("tool_name"):
                affected_tools.add(tc["tool_name"])
            if e.get("model"):
                affected_models.add(e["model"])

        downtime = sum(e.get("duration_ms", 0) or 0 for e in errors)
        tokens_in_w = sum(e.get("tokens_in", 0) or 0 for e in errors)
        tokens_out_w = sum(e.get("tokens_out", 0) or 0 for e in errors)
        tokens_wasted = tokens_in_w + tokens_out_w
        cost = (tokens_in_w / 1000 * self.config.cost_per_1k_input
                + tokens_out_w / 1000 * self.config.cost_per_1k_output)
        user_facing = any(e.get("event_type") in ("tool_error", "agent_error", "error") for e in errors)

        return ImpactAssessment(
            severity=severity, error_count=error_count, total_events=total,
            error_rate=error_rate, affected_tools=sorted(affected_tools),
            affected_models=sorted(affected_models), downtime_ms=downtime,
            tokens_wasted=tokens_wasted, estimated_cost_impact=cost,
            user_facing=user_facing,
        )

    def _generate_remediations(self, causes: list[RootCause], impact: ImpactAssessment) -> list[Remediation]:
        """Produce prioritised remediation actions based on root causes
        and overall impact severity."""
        remediations: list[Remediation] = []
        priority = 1
        for cause in causes:
            if cause.category == "tool_failure":
                remediations.append(Remediation(
                    action="Add retry logic with exponential backoff for tool calls",
                    category=RemediationCategory.IMMEDIATE, priority=priority,
                    rationale=f"Tool failures caused {cause.affected_events} errors",
                ))
                remediations.append(Remediation(
                    action="Add circuit breaker for frequently failing tools",
                    category=RemediationCategory.SHORT_TERM, priority=priority + 1,
                    rationale="Prevent cascading failures from unreliable tools",
                ))
                priority += 2
            elif cause.category == "model_error":
                remediations.append(Remediation(
                    action="Configure fallback model for primary model failures",
                    category=RemediationCategory.IMMEDIATE, priority=priority,
                    rationale=f"Model errors caused {cause.affected_events} failures",
                ))
                remediations.append(Remediation(
                    action="Add input validation to catch malformed prompts before LLM call",
                    category=RemediationCategory.SHORT_TERM, priority=priority + 1,
                    rationale="Some model errors may be caused by invalid input",
                ))
                priority += 2
            elif cause.category == "timeout":
                remediations.append(Remediation(
                    action="Review and adjust timeout thresholds",
                    category=RemediationCategory.IMMEDIATE, priority=priority,
                    rationale=f"{cause.affected_events} timeouts suggest thresholds may be too tight",
                ))
                remediations.append(Remediation(
                    action="Add streaming/chunked processing for long-running operations",
                    category=RemediationCategory.LONG_TERM, priority=priority + 1,
                    rationale="Reduces wall-clock time for large inputs",
                ))
                priority += 2
            elif cause.category == "rate_limit":
                remediations.append(Remediation(
                    action="Implement request rate limiting with token bucket",
                    category=RemediationCategory.IMMEDIATE, priority=priority,
                    rationale="Proactive rate limiting prevents API rejections",
                ))
                remediations.append(Remediation(
                    action="Add request queuing with backpressure",
                    category=RemediationCategory.SHORT_TERM, priority=priority + 1,
                    rationale="Queue requests instead of dropping them on rate limit",
                ))
                priority += 2
            elif cause.category == "cascading_failure":
                remediations.append(Remediation(
                    action="Add circuit breakers between agent components",
                    category=RemediationCategory.SHORT_TERM, priority=priority,
                    rationale="Break cascading failure chains with isolation boundaries",
                ))
                priority += 1
            elif cause.category == "repeated_error":
                remediations.append(Remediation(
                    action="Investigate and fix the root cause of the repeated error",
                    category=RemediationCategory.IMMEDIATE, priority=priority,
                    rationale=f"Same error repeated {cause.affected_events} times",
                ))
                priority += 1
        if impact.severity in (Severity.SEV1, Severity.SEV2):
            remediations.append(Remediation(
                action="Add health check endpoint to detect issues earlier",
                category=RemediationCategory.SHORT_TERM, priority=priority,
                rationale=f"High severity ({impact.severity.value}) warrants better monitoring",
            ))
            priority += 1
        if impact.tokens_wasted > 10000:
            remediations.append(Remediation(
                action="Add token budget guards to fail fast on runaway calls",
                category=RemediationCategory.SHORT_TERM, priority=priority,
                rationale=f"{impact.tokens_wasted:,} tokens wasted in errors",
            ))
        return remediations

    def _extract_lessons(self, causes: list[RootCause], impact: ImpactAssessment) -> list[LessonLearned]:
        """Derive lessons learned from root causes and impact severity."""
        lessons: list[LessonLearned] = []
        for cause in causes:
            if cause.category == "tool_failure":
                lessons.append(LessonLearned(
                    lesson="External tool dependencies need resilience patterns",
                    category="architecture",
                    action_item="Audit all tool integrations for retry/fallback/timeout handling",
                ))
            elif cause.category == "cascading_failure":
                lessons.append(LessonLearned(
                    lesson="Failure isolation between components is insufficient",
                    category="architecture",
                    action_item="Add bulkheads and circuit breakers between agent subsystems",
                ))
            elif cause.category == "timeout":
                lessons.append(LessonLearned(
                    lesson="Timeout configuration needs review",
                    category="tooling",
                    action_item="Document timeout budgets for each tool and model",
                ))
            elif cause.category == "rate_limit":
                lessons.append(LessonLearned(
                    lesson="Request volume exceeded API capacity",
                    category="monitoring",
                    action_item="Add alerting on request rates approaching limits",
                ))
        if impact.severity in (Severity.SEV1, Severity.SEV2):
            lessons.append(LessonLearned(
                lesson="Detection time could be improved",
                category="monitoring",
                action_item="Add proactive health checks and SLA monitoring",
            ))
        return lessons

    def _find_contributing_factors(self, events: list[dict], errors: list[dict]) -> list[str]:
        """Identify environmental or behavioural factors that may have
        amplified the incident (high error density, slow precursors,
        missing retries, high token usage)."""
        factors: list[str] = []
        if len(errors) > len(events) * 0.3:
            factors.append(
                f"High error density ({len(errors)}/{len(events)} events = "
                f"{len(errors)/len(events):.0%} failure rate)"
            )
        slow_before_error = 0
        error_timestamps = {str(e.get("timestamp", "")) for e in errors}
        for e in events:
            dur = e.get("duration_ms", 0) or 0
            if dur > self.config.slow_event_ms and str(e.get("timestamp", "")) not in error_timestamps:
                slow_before_error += 1
        if slow_before_error:
            factors.append(
                f"{slow_before_error} slow event(s) preceded the errors "
                "(possible early warning signs missed)"
            )
        event_types = {e.get("event_type", "") for e in events}
        if "retry" not in event_types and len(errors) > 1:
            factors.append("No retry attempts observed \u2014 agent may lack retry logic")
        total_tokens = sum((e.get("tokens_in", 0) or 0) + (e.get("tokens_out", 0) or 0) for e in events)
        if total_tokens > 50000:
            factors.append(f"High token usage ({total_tokens:,} total) increased cost impact")
        return factors

    def _find_what_went_well(self, events: list[dict], errors: list[dict]) -> list[str]:
        """Highlight positive aspects of the session: successful events,
        error isolation, recovery, and early detection."""
        went_well: list[str] = []
        successes = len(events) - len(errors)
        if successes > 0:
            went_well.append(f"{successes}/{len(events)} events completed successfully")
        error_types = set(e.get("event_type", "") for e in errors)
        if len(error_types) == 1:
            went_well.append("Errors were isolated to a single event type \u2014 no cascade")
        if events and events[-1].get("event_type") not in self.config.error_types:
            went_well.append("Session recovered and completed after errors")
        if len(events) >= 3 and errors:
            first_error_idx = next((i for i, e in enumerate(events) if self._is_error(e)), len(events))
            if first_error_idx <= 2:
                went_well.append("Errors detected early in the session")
        return went_well

    def _generate_summary(self, impact: ImpactAssessment, causes: list[RootCause], duration_ms: float) -> str:
        """Compose a one-paragraph incident summary suitable for the
        report header."""
        parts: list[str] = []
        parts.append(
            f"A {impact.severity.value} incident occurred with "
            f"{impact.error_count} error(s) out of {impact.total_events} events "
            f"({impact.error_rate:.0%} error rate)."
        )
        if causes:
            primary = causes[0]
            parts.append(
                f"The primary root cause was {primary.description.lower()} "
                f"(confidence: {primary.confidence:.0%})."
            )
        if impact.affected_tools:
            parts.append(f"Affected tools: {', '.join(impact.affected_tools)}.")
        if impact.downtime_ms > 0:
            parts.append(f"Estimated downtime: {impact.downtime_ms / 1000:.1f}s.")
        if impact.tokens_wasted > 0:
            parts.append(f"Tokens wasted: {impact.tokens_wasted:,} (est. ${impact.estimated_cost_impact:.4f}).")
        return " ".join(parts)

    def _is_error(self, event: dict) -> bool:
        """Return ``True`` if *event*'s type is in the configured error types."""
        return event.get("event_type", "") in self.config.error_types

    def _parse_ts(self, ts: Any) -> datetime | None:
        """Parse a timestamp value into a timezone-aware ``datetime``.

        Accepts ``datetime`` objects, ISO-8601 strings (with or without
        a trailing ``Z``), and returns ``None`` for unparseable values.
        """
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        try:
            s = str(ts)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    def _empty_report(self, session_id: str) -> PostmortemReport:
        """Return a clean "no incident" report when there are no errors."""
        return PostmortemReport(
            incident_id="INC-NONE", title="No incident detected",
            severity=Severity.SEV4,
            summary="No errors were found in the session events.",
            duration_ms=0, timeline=[], root_causes=[],
            impact=ImpactAssessment(
                severity=Severity.SEV4, error_count=0, total_events=0,
                error_rate=0.0, affected_tools=[], affected_models=[],
                downtime_ms=0, tokens_wasted=0, estimated_cost_impact=0.0,
                user_facing=False,
            ),
            remediations=[], lessons_learned=[], contributing_factors=[],
            what_went_well=["No errors detected \u2014 system operated normally"],
            generated_at=datetime.now(timezone.utc).isoformat(),
            session_id=session_id, event_count=0,
        )
