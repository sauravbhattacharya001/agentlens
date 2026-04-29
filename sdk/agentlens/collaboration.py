"""Agent Collaboration Analyzer for AgentLens.

Analyzes multi-agent collaboration sessions to detect teamwork patterns,
handoff quality, communication bottlenecks, delegation chains, workload
balance, and collective intelligence. Answers: "Are my agents working
well together? Where are the collaboration problems?"

Usage::

    from agentlens.collaboration import CollaborationAnalyzer

    analyzer = CollaborationAnalyzer()
    report = analyzer.analyze(events)
    print(report.format_report())
    print(f"Teamwork score: {report.teamwork_score}/100")
    print(f"Pattern: {report.collaboration_pattern.value}")

Pure Python, stdlib only (math, dataclasses, enum, json, collections).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ───────────────────────────────────────────────────────────


class TeamworkGrade(Enum):
    """Overall teamwork classification."""
    ELITE = "elite"                    # 90+
    STRONG = "strong"                  # 75+
    FUNCTIONAL = "functional"          # 60+
    STRUGGLING = "struggling"          # 40+
    DYSFUNCTIONAL = "dysfunctional"    # <40

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class CollaborationPattern(Enum):
    """Detected collaboration topology."""
    ORCHESTRATED = "orchestrated"        # Central orchestrator + workers
    PEER_TO_PEER = "peer_to_peer"        # Agents communicate as equals
    PIPELINE = "pipeline"                # Linear chain A→B→C
    SWARM = "swarm"                      # Many-to-many, no clear structure
    HIERARCHICAL = "hierarchical"        # Multi-level delegation tree
    SOLO = "solo"                        # Single agent (no collaboration)

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class BottleneckSeverity(Enum):
    """How severe a communication bottleneck is."""
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


class HandoffVerdict(Enum):
    """Quality of an individual handoff."""
    CLEAN = "clean"
    ACCEPTABLE = "acceptable"
    LOSSY = "lossy"
    FAILED = "failed"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class CollaborationEvent:
    """A single event in a multi-agent collaboration session."""
    timestamp: float
    agent_id: str
    event_type: str  # handoff, delegate, complete, error, message, tool_call, decision
    target_agent: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class HandoffDetail:
    """Analysis of a single agent-to-agent handoff."""
    source_agent: str
    target_agent: str
    timestamp: float
    latency_ms: float
    context_loss_score: float  # 0-1, higher = more loss
    verdict: HandoffVerdict
    redundant_work: bool

    def to_dict(self) -> dict:
        return {
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "context_loss_score": self.context_loss_score,
            "verdict": self.verdict.value,
            "redundant_work": self.redundant_work,
        }


@dataclass
class BottleneckAgent:
    """An agent identified as a communication bottleneck."""
    agent_id: str
    fan_in: int
    fan_out: int
    severity: BottleneckSeverity
    waiting_agents: list[str]
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "severity": self.severity.value,
            "waiting_agents": self.waiting_agents,
            "suggestion": self.suggestion,
        }


@dataclass
class DelegationNode:
    """A node in the delegation tree."""
    agent_id: str
    depth: int
    children: list[str]
    completed: bool
    circular: bool

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "depth": self.depth,
            "children": self.children,
            "completed": self.completed,
            "circular": self.circular,
        }


@dataclass
class WorkloadEntry:
    """Workload stats for a single agent."""
    agent_id: str
    event_count: int
    tool_calls: int
    errors: int
    load_fraction: float  # fraction of total work
    status: str  # overloaded, balanced, idle

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "event_count": self.event_count,
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "load_fraction": round(self.load_fraction, 4),
            "status": self.status,
        }


@dataclass
class EngineResult:
    """Result from a single analysis engine."""
    engine: str
    score: float  # 0-100
    findings: list[str]
    details: dict

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "score": round(self.score, 1),
            "findings": self.findings,
            "details": self.details,
        }


@dataclass
class CollaborationReport:
    """Full collaboration analysis report."""
    session_id: str
    agent_count: int
    event_count: int
    teamwork_score: float  # 0-100
    grade: TeamworkGrade
    collaboration_pattern: CollaborationPattern
    engine_results: list[EngineResult] = field(default_factory=list)
    handoffs: list[HandoffDetail] = field(default_factory=list)
    bottlenecks: list[BottleneckAgent] = field(default_factory=list)
    delegation_nodes: list[DelegationNode] = field(default_factory=list)
    workload_entries: list[WorkloadEntry] = field(default_factory=list)
    handoff_quality_score: float = 0.0
    bottleneck_score: float = 0.0
    delegation_score: float = 0.0
    workload_balance_score: float = 0.0
    rhythm_score: float = 0.0
    synergy_score: float = 0.0
    gini_coefficient: float = 0.0
    coordination_overhead_pct: float = 0.0
    max_delegation_depth: int = 0
    abandoned_delegations: int = 0
    circular_delegations: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent_count": self.agent_count,
            "event_count": self.event_count,
            "teamwork_score": round(self.teamwork_score, 1),
            "grade": self.grade.value,
            "collaboration_pattern": self.collaboration_pattern.value,
            "handoff_quality_score": round(self.handoff_quality_score, 1),
            "bottleneck_score": round(self.bottleneck_score, 1),
            "delegation_score": round(self.delegation_score, 1),
            "workload_balance_score": round(self.workload_balance_score, 1),
            "rhythm_score": round(self.rhythm_score, 1),
            "synergy_score": round(self.synergy_score, 1),
            "gini_coefficient": round(self.gini_coefficient, 4),
            "coordination_overhead_pct": round(self.coordination_overhead_pct, 1),
            "max_delegation_depth": self.max_delegation_depth,
            "abandoned_delegations": self.abandoned_delegations,
            "circular_delegations": self.circular_delegations,
            "engine_results": [e.to_dict() for e in self.engine_results],
            "handoffs": [h.to_dict() for h in self.handoffs],
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "delegation_nodes": [d.to_dict() for d in self.delegation_nodes],
            "workload_entries": [w.to_dict() for w in self.workload_entries],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_report(self) -> str:
        lines = []
        lines.append("=" * 62)
        lines.append("  AGENT COLLABORATION ANALYSIS")
        lines.append("=" * 62)
        lines.append(f"  Session:    {self.session_id}")
        lines.append(f"  Agents:     {self.agent_count}")
        lines.append(f"  Events:     {self.event_count}")
        lines.append(f"  Pattern:    {self.collaboration_pattern.label}")
        lines.append(f"  Grade:      {self.grade.label} ({self.teamwork_score:.0f}/100)")
        lines.append("")

        # Engine scores
        lines.append("─" * 62)
        lines.append("  ENGINE SCORES")
        lines.append("─" * 62)
        for er in self.engine_results:
            bar_len = int(er.score / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {er.engine:<30} {bar} {er.score:.0f}")
        lines.append("")

        # Handoff summary
        if self.handoffs:
            lines.append("─" * 62)
            lines.append("  HANDOFF QUALITY")
            lines.append("─" * 62)
            verdict_counts = Counter(h.verdict.value for h in self.handoffs)
            for v, c in verdict_counts.most_common():
                lines.append(f"  {v:<20} {c}")
            lines.append(f"  Overall score: {self.handoff_quality_score:.0f}/100")
            lines.append("")

        # Bottlenecks
        if self.bottlenecks:
            lines.append("─" * 62)
            lines.append("  COMMUNICATION BOTTLENECKS")
            lines.append("─" * 62)
            for b in self.bottlenecks:
                lines.append(f"  ⚠ {b.agent_id} [{b.severity.value}] "
                             f"fan-in={b.fan_in} fan-out={b.fan_out}")
                lines.append(f"    → {b.suggestion}")
            lines.append("")

        # Workload balance
        if self.workload_entries:
            lines.append("─" * 62)
            lines.append("  WORKLOAD BALANCE (Gini={:.3f})".format(self.gini_coefficient))
            lines.append("─" * 62)
            for w in self.workload_entries:
                bar_len = int(w.load_fraction * 40)
                bar = "█" * max(bar_len, 1)
                lines.append(f"  {w.agent_id:<20} {bar} {w.event_count} events [{w.status}]")
            lines.append("")

        # Delegation
        if self.delegation_nodes:
            lines.append("─" * 62)
            lines.append("  DELEGATION CHAINS")
            lines.append("─" * 62)
            lines.append(f"  Max depth:    {self.max_delegation_depth}")
            lines.append(f"  Abandoned:    {self.abandoned_delegations}")
            lines.append(f"  Circular:     {self.circular_delegations}")
            lines.append("")

        # Findings
        all_findings = []
        for er in self.engine_results:
            all_findings.extend(er.findings)
        if all_findings:
            lines.append("─" * 62)
            lines.append("  KEY FINDINGS")
            lines.append("─" * 62)
            for f in all_findings[:10]:
                lines.append(f"  • {f}")
            lines.append("")

        lines.append("=" * 62)
        return "\n".join(lines)


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class CollaborationConfig:
    """Configuration for CollaborationAnalyzer."""
    handoff_latency_threshold_ms: float = 5000.0
    bottleneck_fan_in_threshold: int = 3
    max_delegation_depth_warn: int = 5
    workload_overload_factor: float = 2.0
    workload_idle_factor: float = 0.25
    coordination_event_types: tuple = ("message", "handoff", "delegate")
    work_event_types: tuple = ("tool_call", "decision", "complete")

    # Engine weights for composite score
    weight_handoff: float = 0.20
    weight_bottleneck: float = 0.15
    weight_delegation: float = 0.15
    weight_workload: float = 0.20
    weight_rhythm: float = 0.15
    weight_synergy: float = 0.15


# ── Analyzer ────────────────────────────────────────────────────────


class CollaborationAnalyzer:
    """Analyzes multi-agent collaboration sessions."""

    def __init__(self, config: CollaborationConfig | None = None):
        self.config = config or CollaborationConfig()

    def analyze(
        self,
        events: list[CollaborationEvent],
        session_id: str = "unknown",
    ) -> CollaborationReport:
        """Run all 6 analysis engines and produce a collaboration report."""
        agents = set()
        for e in events:
            agents.add(e.agent_id)
            if e.target_agent:
                agents.add(e.target_agent)

        if not events or len(agents) < 1:
            return self._empty_report(session_id)

        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Run engines
        handoff_result, handoffs = self._analyze_handoffs(sorted_events)
        bottleneck_result, bottlenecks = self._analyze_bottlenecks(sorted_events)
        delegation_result, del_nodes = self._analyze_delegations(sorted_events)
        workload_result, wl_entries, gini = self._analyze_workload(sorted_events, agents)
        rhythm_result, coord_overhead = self._analyze_rhythm(sorted_events)
        synergy_result = self._analyze_synergy(sorted_events, agents)

        engine_results = [
            handoff_result, bottleneck_result, delegation_result,
            workload_result, rhythm_result, synergy_result,
        ]

        # Composite score
        cfg = self.config
        teamwork_score = (
            handoff_result.score * cfg.weight_handoff
            + bottleneck_result.score * cfg.weight_bottleneck
            + delegation_result.score * cfg.weight_delegation
            + workload_result.score * cfg.weight_workload
            + rhythm_result.score * cfg.weight_rhythm
            + synergy_result.score * cfg.weight_synergy
        )
        teamwork_score = max(0.0, min(100.0, teamwork_score))
        grade = self._classify_grade(teamwork_score)
        pattern = self._detect_pattern(sorted_events, agents, del_nodes)

        # Delegation stats
        max_depth = max((d.depth for d in del_nodes), default=0)
        abandoned = sum(1 for d in del_nodes if not d.completed and d.children == [] and d.depth > 0)
        circular = sum(1 for d in del_nodes if d.circular)

        return CollaborationReport(
            session_id=session_id,
            agent_count=len(agents),
            event_count=len(events),
            teamwork_score=teamwork_score,
            grade=grade,
            collaboration_pattern=pattern,
            engine_results=engine_results,
            handoffs=handoffs,
            bottlenecks=bottlenecks,
            delegation_nodes=del_nodes,
            workload_entries=wl_entries,
            handoff_quality_score=handoff_result.score,
            bottleneck_score=bottleneck_result.score,
            delegation_score=delegation_result.score,
            workload_balance_score=workload_result.score,
            rhythm_score=rhythm_result.score,
            synergy_score=synergy_result.score,
            gini_coefficient=gini,
            coordination_overhead_pct=coord_overhead,
            max_delegation_depth=max_depth,
            abandoned_delegations=abandoned,
            circular_delegations=circular,
        )

    # ── Engine 1: Handoff Quality ───────────────────────────────────

    def _analyze_handoffs(
        self, events: list[CollaborationEvent]
    ) -> tuple[EngineResult, list[HandoffDetail]]:
        handoff_events = [e for e in events if e.event_type == "handoff" and e.target_agent]
        if not handoff_events:
            return EngineResult(
                engine="Handoff Quality",
                score=100.0,
                findings=["No handoffs detected — single agent or no delegation."],
                details={"handoff_count": 0},
            ), []

        handoffs = []
        total_quality = 0.0
        redundant_count = 0
        seen_work: dict[str, set[str]] = defaultdict(set)

        # Track what each agent has done (tool calls) to detect redundancy
        for e in events:
            if e.event_type == "tool_call":
                tool_name = e.metadata.get("tool", "unknown")
                seen_work[e.agent_id].add(tool_name)

        for he in handoff_events:
            latency_ms = he.metadata.get("latency_ms", 0.0)
            context_size = he.metadata.get("context_size", 0)
            received_context = he.metadata.get("received_context", 0)

            # Context loss
            if context_size > 0 and received_context >= 0:
                context_loss = 1.0 - min(received_context / context_size, 1.0)
            else:
                context_loss = 0.0

            # Redundant work detection
            source_tools = seen_work.get(he.agent_id, set())
            target_tools = seen_work.get(he.target_agent, set())
            redundant = len(source_tools & target_tools) > len(source_tools) * 0.5 if source_tools else False

            if redundant:
                redundant_count += 1

            # Verdict
            if context_loss > 0.5 or latency_ms > self.config.handoff_latency_threshold_ms * 2:
                verdict = HandoffVerdict.FAILED
                quality = 20.0
            elif context_loss > 0.2 or latency_ms > self.config.handoff_latency_threshold_ms:
                verdict = HandoffVerdict.LOSSY
                quality = 50.0
            elif context_loss > 0.05 or redundant:
                verdict = HandoffVerdict.ACCEPTABLE
                quality = 75.0
            else:
                verdict = HandoffVerdict.CLEAN
                quality = 100.0

            total_quality += quality
            handoffs.append(HandoffDetail(
                source_agent=he.agent_id,
                target_agent=he.target_agent,
                timestamp=he.timestamp,
                latency_ms=latency_ms,
                context_loss_score=context_loss,
                verdict=verdict,
                redundant_work=redundant,
            ))

        avg_quality = total_quality / len(handoffs) if handoffs else 100.0
        findings = []
        failed = sum(1 for h in handoffs if h.verdict == HandoffVerdict.FAILED)
        lossy = sum(1 for h in handoffs if h.verdict == HandoffVerdict.LOSSY)
        if failed:
            findings.append(f"{failed} handoff(s) failed with excessive context loss or latency.")
        if lossy:
            findings.append(f"{lossy} handoff(s) were lossy — information degraded during transfer.")
        if redundant_count:
            findings.append(f"{redundant_count} handoff(s) led to redundant work.")

        return EngineResult(
            engine="Handoff Quality",
            score=avg_quality,
            findings=findings,
            details={
                "handoff_count": len(handoffs),
                "failed_count": failed,
                "lossy_count": lossy,
                "redundant_count": redundant_count,
                "avg_latency_ms": sum(h.latency_ms for h in handoffs) / len(handoffs) if handoffs else 0,
            },
        ), handoffs

    # ── Engine 2: Communication Bottleneck ──────────────────────────

    def _analyze_bottlenecks(
        self, events: list[CollaborationEvent]
    ) -> tuple[EngineResult, list[BottleneckAgent]]:
        incoming: dict[str, set[str]] = defaultdict(set)
        outgoing: dict[str, set[str]] = defaultdict(set)

        for e in events:
            if e.target_agent and e.event_type in ("handoff", "delegate", "message"):
                incoming[e.target_agent].add(e.agent_id)
                outgoing[e.agent_id].add(e.target_agent)

        if not incoming and not outgoing:
            return EngineResult(
                engine="Communication Bottleneck",
                score=100.0,
                findings=["No inter-agent communication detected."],
                details={},
            ), []

        all_agents = set(incoming.keys()) | set(outgoing.keys())
        bottlenecks = []
        threshold = self.config.bottleneck_fan_in_threshold

        for agent in all_agents:
            fi = len(incoming.get(agent, set()))
            fo = len(outgoing.get(agent, set()))

            if fi >= threshold:
                if fi >= threshold * 2:
                    severity = BottleneckSeverity.CRITICAL
                elif fi >= threshold * 1.5:
                    severity = BottleneckSeverity.SEVERE
                elif fi >= threshold:
                    severity = BottleneckSeverity.MODERATE
                else:
                    severity = BottleneckSeverity.MILD

                waiting = list(incoming.get(agent, set()))
                suggestion = (
                    f"Agent '{agent}' receives from {fi} agents. "
                    f"Consider splitting responsibilities or adding parallel workers."
                )
                bottlenecks.append(BottleneckAgent(
                    agent_id=agent,
                    fan_in=fi,
                    fan_out=fo,
                    severity=severity,
                    waiting_agents=waiting,
                    suggestion=suggestion,
                ))

        # Score: 100 if no bottlenecks, lower with more/worse bottlenecks
        if not bottlenecks:
            score = 100.0
        else:
            severity_penalties = {
                BottleneckSeverity.MILD: 10,
                BottleneckSeverity.MODERATE: 20,
                BottleneckSeverity.SEVERE: 35,
                BottleneckSeverity.CRITICAL: 50,
            }
            total_penalty = sum(severity_penalties.get(b.severity, 0) for b in bottlenecks)
            score = max(0.0, 100.0 - total_penalty)

        findings = []
        for b in bottlenecks:
            findings.append(
                f"Agent '{b.agent_id}' is a {b.severity.value} bottleneck "
                f"(fan-in={b.fan_in}, fan-out={b.fan_out})."
            )

        return EngineResult(
            engine="Communication Bottleneck",
            score=score,
            findings=findings,
            details={
                "bottleneck_count": len(bottlenecks),
                "agents_analyzed": len(all_agents),
            },
        ), bottlenecks

    # ── Engine 3: Delegation Chain ──────────────────────────────────

    def _analyze_delegations(
        self, events: list[CollaborationEvent]
    ) -> tuple[EngineResult, list[DelegationNode]]:
        delegations: list[tuple[str, str]] = []
        completions: set[str] = set()

        for e in events:
            if e.event_type == "delegate" and e.target_agent:
                delegations.append((e.agent_id, e.target_agent))
            if e.event_type == "complete":
                completions.add(e.agent_id)

        if not delegations:
            return EngineResult(
                engine="Delegation Chain",
                score=100.0,
                findings=["No delegations detected."],
                details={"delegation_count": 0},
            ), []

        # Build delegation tree
        children_map: dict[str, list[str]] = defaultdict(list)
        parents: set[str] = set()
        all_delegated: set[str] = set()

        for parent, child in delegations:
            children_map[parent].append(child)
            parents.add(parent)
            all_delegated.add(child)

        # Find roots (agents that delegate but aren't delegated to)
        roots = parents - all_delegated
        if not roots:
            roots = parents  # circular — everyone is a root

        # Detect circular delegations via edge analysis
        circular_agents: set[str] = set()
        for start in parents:
            visited: set[str] = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node in visited:
                    circular_agents.add(node)
                    continue
                visited.add(node)
                for child in children_map.get(node, []):
                    stack.append(child)

        # Build nodes with depth
        nodes = []
        visited_global: set[str] = set()

        def build_tree(agent: str, depth: int, path: set[str]) -> None:
            if agent in visited_global:
                return
            visited_global.add(agent)
            is_circular = agent in circular_agents
            nodes.append(DelegationNode(
                agent_id=agent,
                depth=depth,
                children=children_map.get(agent, []),
                completed=agent in completions,
                circular=is_circular,
            ))
            for child in children_map.get(agent, []):
                build_tree(child, depth + 1, path | {agent})

        for root in roots:
            build_tree(root, 0, set())

        # Also add agents that only appear as targets
        for _, child in delegations:
            if child not in visited_global:
                nodes.append(DelegationNode(
                    agent_id=child,
                    depth=1,
                    children=children_map.get(child, []),
                    completed=child in completions,
                    circular=False,
                ))

        max_depth = max((n.depth for n in nodes), default=0)
        # Only leaf nodes (no children) that were delegated to but never completed
        delegated_to = set(child for _, child in delegations)
        abandoned = sum(1 for n in nodes if not n.completed and n.children == [] and n.agent_id in delegated_to)
        circular = sum(1 for n in nodes if n.circular)

        # Score
        score = 100.0
        if max_depth > self.config.max_delegation_depth_warn:
            score -= (max_depth - self.config.max_delegation_depth_warn) * 10
        if abandoned > 0:
            score -= abandoned * 15
        if circular > 0:
            score -= circular * 25
        score = max(0.0, min(100.0, score))

        findings = []
        if max_depth > self.config.max_delegation_depth_warn:
            findings.append(f"Excessive delegation depth ({max_depth}) — consider flattening.")
        if abandoned:
            findings.append(f"{abandoned} delegation(s) were abandoned (delegated but never completed).")
        if circular:
            findings.append(f"{circular} circular delegation(s) detected — agents delegating in loops.")

        return EngineResult(
            engine="Delegation Chain",
            score=score,
            findings=findings,
            details={
                "delegation_count": len(delegations),
                "max_depth": max_depth,
                "abandoned_count": abandoned,
                "circular_count": circular,
                "node_count": len(nodes),
            },
        ), nodes

    # ── Engine 4: Workload Balance ──────────────────────────────────

    def _analyze_workload(
        self, events: list[CollaborationEvent], agents: set[str]
    ) -> tuple[EngineResult, list[WorkloadEntry], float]:
        agent_counts: Counter = Counter()
        agent_tools: Counter = Counter()
        agent_errors: Counter = Counter()

        for e in events:
            agent_counts[e.agent_id] += 1
            if e.event_type == "tool_call":
                agent_tools[e.agent_id] += 1
            if e.event_type == "error":
                agent_errors[e.agent_id] += 1

        if not agent_counts:
            return EngineResult(
                engine="Workload Balance",
                score=100.0,
                findings=["No agent activity."],
                details={"gini": 0.0},
            ), [], 0.0

        total = sum(agent_counts.values())
        avg = total / len(agents) if agents else 1

        entries = []
        for agent in sorted(agents):
            count = agent_counts.get(agent, 0)
            fraction = count / total if total > 0 else 0
            if count > avg * self.config.workload_overload_factor:
                status = "overloaded"
            elif count < avg * self.config.workload_idle_factor:
                status = "idle"
            else:
                status = "balanced"
            entries.append(WorkloadEntry(
                agent_id=agent,
                event_count=count,
                tool_calls=agent_tools.get(agent, 0),
                errors=agent_errors.get(agent, 0),
                load_fraction=fraction,
                status=status,
            ))

        # Gini coefficient
        values = sorted(agent_counts.get(a, 0) for a in agents)
        n = len(values)
        if n == 0 or sum(values) == 0:
            gini = 0.0
        else:
            numerator = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(values))
            denominator = n * sum(values)
            gini = numerator / denominator if denominator > 0 else 0.0
            gini = max(0.0, min(1.0, gini))

        # Score: 100 for perfectly balanced, lower for imbalanced
        score = max(0.0, 100.0 * (1.0 - gini))
        overloaded = sum(1 for e in entries if e.status == "overloaded")
        idle = sum(1 for e in entries if e.status == "idle")

        findings = []
        if overloaded:
            findings.append(f"{overloaded} agent(s) are overloaded — consider redistributing work.")
        if idle:
            findings.append(f"{idle} agent(s) are idle — underutilized resources.")
        if gini > 0.5:
            findings.append(f"High workload imbalance (Gini={gini:.3f}).")

        return EngineResult(
            engine="Workload Balance",
            score=score,
            findings=findings,
            details={
                "gini": round(gini, 4),
                "agent_count": len(agents),
                "overloaded_count": overloaded,
                "idle_count": idle,
            },
        ), entries, gini

    # ── Engine 5: Teamwork Rhythm ───────────────────────────────────

    def _analyze_rhythm(
        self, events: list[CollaborationEvent]
    ) -> tuple[EngineResult, float]:
        if len(events) < 2:
            return EngineResult(
                engine="Teamwork Rhythm",
                score=75.0,
                findings=["Insufficient events for rhythm analysis."],
                details={},
            ), 0.0

        coord_types = set(self.config.coordination_event_types)
        work_types = set(self.config.work_event_types)

        coord_count = sum(1 for e in events if e.event_type in coord_types)
        work_count = sum(1 for e in events if e.event_type in work_types)
        total = coord_count + work_count

        coord_overhead = (coord_count / total * 100) if total > 0 else 0.0

        # Sync analysis: do agents overlap in time windows?
        agents_per_window: list[set[str]] = []
        if events:
            window_size = max(
                (events[-1].timestamp - events[0].timestamp) / 10,
                1.0,
            )
            window_start = events[0].timestamp
            current_window: set[str] = set()
            for e in events:
                if e.timestamp - window_start > window_size:
                    if current_window:
                        agents_per_window.append(current_window)
                    current_window = set()
                    window_start = e.timestamp
                current_window.add(e.agent_id)
            if current_window:
                agents_per_window.append(current_window)

        all_agents = set(e.agent_id for e in events)
        if agents_per_window and len(all_agents) > 1:
            avg_overlap = sum(len(w) for w in agents_per_window) / len(agents_per_window)
            sync_ratio = avg_overlap / len(all_agents)
            sync_score = min(100.0, sync_ratio * 100)
        else:
            sync_score = 50.0

        # Inter-agent response time
        agent_last_event: dict[str, float] = {}
        response_times: list[float] = []
        for e in events:
            if e.target_agent and e.target_agent in agent_last_event:
                rt = e.timestamp - agent_last_event[e.target_agent]
                if rt > 0:
                    response_times.append(rt)
            agent_last_event[e.agent_id] = e.timestamp

        avg_response = sum(response_times) / len(response_times) if response_times else 0

        # Combine sync + coordination overhead into rhythm score
        # Lower overhead is better, higher sync is better
        overhead_penalty = min(coord_overhead, 80.0) / 80.0 * 40  # up to 40 point penalty
        rhythm_score = max(0.0, min(100.0, sync_score - overhead_penalty))

        findings = []
        if coord_overhead > 50:
            findings.append(f"High coordination overhead ({coord_overhead:.0f}%) — agents spend more time coordinating than working.")
        if sync_score < 40:
            findings.append("Low synchronization — agents are working out of phase.")

        return EngineResult(
            engine="Teamwork Rhythm",
            score=rhythm_score,
            findings=findings,
            details={
                "sync_score": round(sync_score, 1),
                "coordination_overhead_pct": round(coord_overhead, 1),
                "avg_response_time_s": round(avg_response, 3),
                "time_windows": len(agents_per_window),
            },
        ), coord_overhead

    # ── Engine 6: Collective Intelligence ───────────────────────────

    def _analyze_synergy(
        self, events: list[CollaborationEvent], agents: set[str]
    ) -> EngineResult:
        if len(agents) < 2:
            return EngineResult(
                engine="Collective Intelligence",
                score=50.0,
                findings=["Single agent — no collaboration to evaluate."],
                details={"synergy_score": 50.0},
            )

        # Error correction: does agent B fix agent A's errors?
        error_agents: list[str] = []
        fix_after_error = 0
        for i, e in enumerate(events):
            if e.event_type == "error":
                error_agents.append(e.agent_id)
                # Look for a subsequent complete/decision by a different agent
                for j in range(i + 1, min(i + 10, len(events))):
                    if (events[j].event_type in ("complete", "decision")
                            and events[j].agent_id != e.agent_id):
                        fix_after_error += 1
                        break

        error_correction_rate = fix_after_error / len(error_agents) if error_agents else 0.5

        # Knowledge complementarity: do different agents use different tools?
        agent_tools: dict[str, set[str]] = defaultdict(set)
        for e in events:
            if e.event_type == "tool_call":
                tool = e.metadata.get("tool", "unknown")
                agent_tools[e.agent_id].add(tool)

        if len(agent_tools) >= 2:
            all_tools: set[str] = set()
            for tools in agent_tools.values():
                all_tools.update(tools)
            # Complementarity: ratio of unique tool contributions
            unique_contributions = 0
            for agent, tools in agent_tools.items():
                other_tools = set()
                for other, ot in agent_tools.items():
                    if other != agent:
                        other_tools.update(ot)
                unique_contributions += len(tools - other_tools)
            complementarity = unique_contributions / len(all_tools) if all_tools else 0.5
        else:
            complementarity = 0.5

        # Consensus quality: when multiple agents agree (same decision), check outcome
        decisions_by_topic: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e in events:
            if e.event_type == "decision":
                topic = e.metadata.get("topic", "default")
                outcome = e.metadata.get("outcome", "unknown")
                decisions_by_topic[topic].append((e.agent_id, outcome))

        consensus_quality = 0.5
        if decisions_by_topic:
            consensus_count = 0
            good_consensus = 0
            for topic, decs in decisions_by_topic.items():
                if len(decs) >= 2:
                    outcomes = [d[1] for d in decs]
                    if len(set(outcomes)) == 1:  # all agree
                        consensus_count += 1
                        if outcomes[0] != "error":
                            good_consensus += 1
            if consensus_count > 0:
                consensus_quality = good_consensus / consensus_count

        # Synergy score: weighted combination
        synergy = (
            error_correction_rate * 35
            + complementarity * 35
            + consensus_quality * 30
        )
        synergy = max(0.0, min(100.0, synergy))

        findings = []
        if error_correction_rate > 0.5:
            findings.append(f"Good error correction — {fix_after_error} error(s) caught by teammate agents.")
        if complementarity > 0.6:
            findings.append("Strong knowledge complementarity — agents contribute different skills.")
        if synergy < 40:
            findings.append("Low collective intelligence — team underperforms relative to individual capacity.")
        if synergy > 70:
            findings.append("High synergy — the team produces better outcomes than individual agents.")

        return EngineResult(
            engine="Collective Intelligence",
            score=synergy,
            findings=findings,
            details={
                "error_correction_rate": round(error_correction_rate, 3),
                "complementarity": round(complementarity, 3),
                "consensus_quality": round(consensus_quality, 3),
                "synergy_score": round(synergy, 1),
            },
        )

    # ── Pattern Detection ───────────────────────────────────────────

    def _detect_pattern(
        self,
        events: list[CollaborationEvent],
        agents: set[str],
        delegation_nodes: list[DelegationNode],
    ) -> CollaborationPattern:
        if len(agents) <= 1:
            return CollaborationPattern.SOLO

        # Check for orchestrator pattern: one agent delegates to many
        delegators: Counter = Counter()
        for e in events:
            if e.event_type == "delegate" and e.target_agent:
                delegators[e.agent_id] += 1

        if delegators:
            top_delegator_count = delegators.most_common(1)[0][1]
            if top_delegator_count >= len(agents) - 1:
                return CollaborationPattern.ORCHESTRATED

        # Check for pipeline: A→B→C (strictly linear chain, no branching)
        edges: set[tuple[str, str]] = set()
        for e in events:
            if e.target_agent and e.event_type in ("handoff", "delegate"):
                edges.add((e.agent_id, e.target_agent))

        sources = set(s for s, _ in edges)
        targets = set(t for _, t in edges)
        if edges and len(edges) == len(agents) - 1:
            # Check linearity: each source has exactly 1 outgoing edge
            out_degree: dict[str, int] = defaultdict(int)
            for s, _ in edges:
                out_degree[s] += 1
            start_nodes = sources - targets
            if len(start_nodes) == 1 and all(v == 1 for v in out_degree.values()):
                return CollaborationPattern.PIPELINE

        # Check for hierarchical: multi-level delegation tree
        max_depth = max((n.depth for n in delegation_nodes), default=0)
        if max_depth >= 2:
            return CollaborationPattern.HIERARCHICAL

        # Check for swarm: many-to-many with no clear structure
        if len(edges) > len(agents):
            return CollaborationPattern.SWARM

        return CollaborationPattern.PEER_TO_PEER

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_grade(score: float) -> TeamworkGrade:
        if score >= 90:
            return TeamworkGrade.ELITE
        if score >= 75:
            return TeamworkGrade.STRONG
        if score >= 60:
            return TeamworkGrade.FUNCTIONAL
        if score >= 40:
            return TeamworkGrade.STRUGGLING
        return TeamworkGrade.DYSFUNCTIONAL

    @staticmethod
    def _empty_report(session_id: str) -> CollaborationReport:
        return CollaborationReport(
            session_id=session_id,
            agent_count=0,
            event_count=0,
            teamwork_score=0.0,
            grade=TeamworkGrade.DYSFUNCTIONAL,
            collaboration_pattern=CollaborationPattern.SOLO,
        )
