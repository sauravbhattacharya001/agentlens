"""Agent Delegation Analyzer for AgentLens.

Autonomously analyzes how agents delegate work to sub-agents, tools,
and human escalation paths.  Detects delegation anti-patterns such as
over-delegation, rubber-stamping, bottleneck agents, deep chains,
circular delegation, accountability gaps, and unbalanced loads.

Answers: "Are my agents delegating effectively?  Where are the
bottlenecks and failure cascades?  Who is over-delegating?"

Usage::

    from agentlens.delegation import DelegationAnalyzer, DelegationEvent

    analyzer = DelegationAnalyzer()

    analyzer.add_event(DelegationEvent(
        session_id="sess-001",
        parent_agent_id="coordinator",
        child_agent_id="researcher",
        task_description="Find relevant papers",
        delegation_type="sub_agent",
        success=True, latency_ms=1200.0,
    ))
    # ... add more events ...

    report = analyzer.analyze()
    print(report.format_report())
    print(f"Delegation health: {report.health_score}/100")

Pure Python, stdlib only (math, statistics, dataclasses, enum, collections).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class DelegationHealthTier(Enum):
    """Overall delegation health classification."""
    EXCELLENT = "excellent"      # 80-100
    HEALTHY = "healthy"          # 60-79
    CONCERNING = "concerning"    # 40-59
    UNHEALTHY = "unhealthy"     # 20-39
    CRITICAL = "critical"       # 0-19

    @property
    def label(self) -> str:
        return self.value.title()


class AntiPatternType(Enum):
    """Types of delegation anti-patterns."""
    OVER_DELEGATION = "over_delegation"
    RUBBER_STAMPING = "rubber_stamping"
    BOTTLENECK_AGENT = "bottleneck_agent"
    DEEP_CHAIN = "deep_chain"
    CIRCULAR_DELEGATION = "circular_delegation"
    ACCOUNTABILITY_GAP = "accountability_gap"
    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"
    UNBALANCED_LOAD = "unbalanced_load"


class Severity(Enum):
    """Anti-pattern severity."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendationUrgency(Enum):
    """Urgency level for recommendations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Models ─────────────────────────────────────────────────────


@dataclass
class DelegationEvent:
    """A single delegation event."""
    session_id: str
    parent_agent_id: str
    child_agent_id: str
    task_description: str = ""
    delegation_type: str = "sub_agent"  # sub_agent | tool_call | human_escalation
    success: bool = True
    latency_ms: float = 0.0
    tokens_consumed: int = 0
    timestamp_ms: float = 0.0
    depth: int = 1
    was_re_delegated: bool = False
    error_message: str = ""


@dataclass
class DelegationConfig:
    """Configuration for the delegation analyzer."""
    max_healthy_depth: int = 3
    over_delegation_threshold: float = 0.80
    bottleneck_fan_in_threshold: int = 10
    accountability_gap_threshold: float = 0.5
    imbalance_gini_threshold: float = 0.6
    min_events_for_pattern: int = 3


@dataclass
class AntiPattern:
    """A detected delegation anti-pattern."""
    pattern_type: AntiPatternType
    severity: Severity
    agent_id: str
    description: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_type": self.pattern_type.value,
            "severity": self.severity.value,
            "agent_id": self.agent_id,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass
class Recommendation:
    """An actionable recommendation."""
    urgency: RecommendationUrgency
    message: str
    target_agent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "urgency": self.urgency.value,
            "message": self.message,
            "target_agent": self.target_agent,
        }


@dataclass
class DelegationEdge:
    """An edge in the delegation graph."""
    parent: str
    child: str
    count: int = 0
    successes: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.count if self.count > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.count if self.count > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parent": self.parent,
            "child": self.child,
            "count": self.count,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


@dataclass
class AgentDelegationProfile:
    """Profile of an agent's delegation behavior."""
    agent_id: str
    delegations_sent: int = 0
    delegations_received: int = 0
    tasks_self_handled: int = 0
    re_delegations: int = 0
    failures_as_parent: int = 0
    failures_as_child: int = 0
    unique_children: int = 0
    unique_parents: int = 0
    max_chain_depth: int = 0

    @property
    def delegation_ratio(self) -> float:
        """Fraction of work delegated vs self-handled."""
        total = self.delegations_sent + self.tasks_self_handled
        return self.delegations_sent / total if total > 0 else 0.0

    @property
    def fan_in(self) -> int:
        return self.unique_parents

    @property
    def fan_out(self) -> int:
        return self.unique_children

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "delegations_sent": self.delegations_sent,
            "delegations_received": self.delegations_received,
            "tasks_self_handled": self.tasks_self_handled,
            "re_delegations": self.re_delegations,
            "delegation_ratio": round(self.delegation_ratio, 3),
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "max_chain_depth": self.max_chain_depth,
        }


# ── Report ──────────────────────────────────────────────────────────


@dataclass
class DelegationReport:
    """Complete delegation analysis report."""
    delegation_graph: List[DelegationEdge] = field(default_factory=list)
    agent_profiles: List[AgentDelegationProfile] = field(default_factory=list)
    depth_distribution: Dict[int, int] = field(default_factory=dict)
    bottleneck_agents: List[str] = field(default_factory=list)
    anti_patterns: List[AntiPattern] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    health_score: float = 100.0
    health_tier: DelegationHealthTier = DelegationHealthTier.EXCELLENT
    total_events: int = 0
    total_agents: int = 0
    avg_chain_depth: float = 0.0
    max_chain_depth: int = 0
    circular_pairs: List[Tuple[str, str]] = field(default_factory=list)

    def format_report(self) -> str:
        """Format as a human-readable CLI report."""
        lines: List[str] = []
        tier_icons = {
            "excellent": "🟢", "healthy": "🔵", "concerning": "🟡",
            "unhealthy": "🟠", "critical": "🔴",
        }
        icon = tier_icons.get(self.health_tier.value, "⚪")

        lines.append("╔══════════════════════════════════════════════════════════╗")
        lines.append("║        AGENT DELEGATION ANALYZER                        ║")
        lines.append("╚══════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Health Score: {icon} {self.health_score:.0f}/100 ({self.health_tier.label})")
        lines.append(f"  Total Events: {self.total_events}")
        lines.append(f"  Total Agents: {self.total_agents}")
        lines.append(f"  Avg Chain Depth: {self.avg_chain_depth:.1f}")
        lines.append(f"  Max Chain Depth: {self.max_chain_depth}")
        lines.append("")

        # Delegation graph
        if self.delegation_graph:
            lines.append("── Delegation Graph ──────────────────────────────────────")
            for edge in sorted(self.delegation_graph, key=lambda e: e.count, reverse=True)[:10]:
                sr = f"{edge.success_rate * 100:.0f}%"
                lines.append(f"  {edge.parent} → {edge.child}  [{edge.count}x, {sr} success, {edge.avg_latency_ms:.0f}ms avg]")
            lines.append("")

        # Agent profiles
        if self.agent_profiles:
            lines.append("── Agent Profiles ────────────────────────────────────────")
            for p in sorted(self.agent_profiles, key=lambda x: x.delegations_sent, reverse=True)[:8]:
                lines.append(f"  {p.agent_id}: sent={p.delegations_sent} recv={p.delegations_received} "
                             f"ratio={p.delegation_ratio:.0%} fan-out={p.fan_out} fan-in={p.fan_in}")
            lines.append("")

        # Anti-patterns
        if self.anti_patterns:
            lines.append("── Anti-Patterns Detected ────────────────────────────────")
            sev_icons = {"low": "⚪", "medium": "🟡", "high": "🟠", "critical": "🔴"}
            for ap in self.anti_patterns:
                si = sev_icons.get(ap.severity.value, "⚪")
                lines.append(f"  {si} [{ap.pattern_type.value}] {ap.description}")
            lines.append("")

        # Bottlenecks
        if self.bottleneck_agents:
            lines.append("── Bottleneck Agents ─────────────────────────────────────")
            for ba in self.bottleneck_agents:
                lines.append(f"  ⚠️  {ba}")
            lines.append("")

        # Circular delegations
        if self.circular_pairs:
            lines.append("── Circular Delegations ──────────────────────────────────")
            for a, b in self.circular_pairs:
                lines.append(f"  🔄 {a} ↔ {b}")
            lines.append("")

        # Recommendations
        if self.recommendations:
            lines.append("── Recommendations ───────────────────────────────────────")
            urg_icons = {"low": "💡", "medium": "📋", "high": "⚠️", "critical": "🚨"}
            for rec in self.recommendations:
                ri = urg_icons.get(rec.urgency.value, "💡")
                lines.append(f"  {ri} {rec.message}")
            lines.append("")

        # Insights
        if self.insights:
            lines.append("── Autonomous Insights ───────────────────────────────────")
            for ins in self.insights:
                lines.append(f"  🧠 {ins}")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "health_score": round(self.health_score, 1),
            "health_tier": self.health_tier.value,
            "total_events": self.total_events,
            "total_agents": self.total_agents,
            "avg_chain_depth": round(self.avg_chain_depth, 2),
            "max_chain_depth": self.max_chain_depth,
            "delegation_graph": [e.to_dict() for e in self.delegation_graph],
            "agent_profiles": [p.to_dict() for p in self.agent_profiles],
            "depth_distribution": self.depth_distribution,
            "bottleneck_agents": self.bottleneck_agents,
            "circular_pairs": [list(p) for p in self.circular_pairs],
            "anti_patterns": [ap.to_dict() for ap in self.anti_patterns],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "insights": self.insights,
        }


# ── Analyzer ────────────────────────────────────────────────────────


class DelegationAnalyzer:
    """Autonomous delegation pattern analyzer.

    Engines:
    1. Delegation Graph Builder
    2. Depth Analyzer
    3. Bottleneck Detector
    4. Failure Cascade Tracker
    5. Over-Delegation Detector
    6. Accountability Gap Finder
    7. Insight Generator
    """

    def __init__(self, config: Optional[DelegationConfig] = None) -> None:
        self.config = config or DelegationConfig()
        self._events: List[DelegationEvent] = []

    def add_event(self, event: DelegationEvent) -> None:
        """Add a single delegation event."""
        self._events.append(event)

    def add_events(self, events: List[DelegationEvent]) -> None:
        """Add multiple delegation events."""
        self._events.extend(events)

    def analyze(self) -> DelegationReport:
        """Run all 7 analysis engines and produce a report."""
        if not self._events:
            return DelegationReport()

        report = DelegationReport()
        report.total_events = len(self._events)

        # Engine 1: Build delegation graph
        graph, edge_map = self._build_graph()
        report.delegation_graph = list(edge_map.values())

        # Collect all agents
        all_agents: Set[str] = set()
        for ev in self._events:
            all_agents.add(ev.parent_agent_id)
            all_agents.add(ev.child_agent_id)
        report.total_agents = len(all_agents)

        # Build profiles
        profiles = self._build_profiles(all_agents)
        report.agent_profiles = list(profiles.values())

        # Engine 2: Depth analysis
        self._analyze_depth(report)

        # Engine 3: Bottleneck detection
        self._detect_bottlenecks(report, profiles)

        # Engine 4: Failure cascade tracking
        self._track_failure_cascades(report, graph)

        # Engine 5: Over-delegation detection
        self._detect_over_delegation(report, profiles)

        # Engine 6: Accountability gap detection
        self._detect_accountability_gaps(report, graph, profiles)

        # Circular delegation detection
        self._detect_circular_delegations(report, graph)

        # Unbalanced load detection
        self._detect_unbalanced_load(report, profiles)

        # Engine 7: Insight generation
        self._generate_insights(report, profiles)

        # Compute health score
        self._compute_health_score(report)

        # Generate recommendations
        self._generate_recommendations(report)

        return report

    # ── Engine 1: Delegation Graph Builder ──────────────────────────

    def _build_graph(self) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], DelegationEdge]]:
        """Build directed delegation graph."""
        graph: Dict[str, Set[str]] = defaultdict(set)
        edge_map: Dict[Tuple[str, str], DelegationEdge] = {}

        for ev in self._events:
            key = (ev.parent_agent_id, ev.child_agent_id)
            graph[ev.parent_agent_id].add(ev.child_agent_id)

            if key not in edge_map:
                edge_map[key] = DelegationEdge(parent=ev.parent_agent_id, child=ev.child_agent_id)

            edge = edge_map[key]
            edge.count += 1
            if ev.success:
                edge.successes += 1
            edge.total_latency_ms += ev.latency_ms

        return dict(graph), edge_map

    # ── Engine 2: Depth Analyzer ────────────────────────────────────

    def _analyze_depth(self, report: DelegationReport) -> None:
        """Analyze delegation chain depths."""
        depths = [ev.depth for ev in self._events]
        depth_dist: Dict[int, int] = Counter(depths)
        report.depth_distribution = dict(sorted(depth_dist.items()))
        report.max_chain_depth = max(depths) if depths else 0
        report.avg_chain_depth = statistics.mean(depths) if depths else 0.0

        # Detect deep chains
        deep_count = sum(1 for d in depths if d > self.config.max_healthy_depth)
        if deep_count >= self.config.min_events_for_pattern:
            severity = Severity.HIGH if deep_count > len(depths) * 0.3 else Severity.MEDIUM
            report.anti_patterns.append(AntiPattern(
                pattern_type=AntiPatternType.DEEP_CHAIN,
                severity=severity,
                agent_id="(fleet)",
                description=f"{deep_count} delegations exceed max healthy depth of {self.config.max_healthy_depth} "
                            f"(max observed: {report.max_chain_depth})",
                evidence={"deep_count": deep_count, "max_depth": report.max_chain_depth},
            ))

    # ── Engine 3: Bottleneck Detector ───────────────────────────────

    def _detect_bottlenecks(self, report: DelegationReport,
                            profiles: Dict[str, AgentDelegationProfile]) -> None:
        """Find agents receiving too many delegations."""
        for agent_id, prof in profiles.items():
            if prof.delegations_received >= self.config.bottleneck_fan_in_threshold:
                report.bottleneck_agents.append(agent_id)
                severity = Severity.CRITICAL if prof.delegations_received > self.config.bottleneck_fan_in_threshold * 2 else Severity.HIGH
                report.anti_patterns.append(AntiPattern(
                    pattern_type=AntiPatternType.BOTTLENECK_AGENT,
                    severity=severity,
                    agent_id=agent_id,
                    description=f"Agent '{agent_id}' receives {prof.delegations_received} delegations "
                                f"from {prof.unique_parents} parents (threshold: {self.config.bottleneck_fan_in_threshold})",
                    evidence={"fan_in": prof.delegations_received, "unique_parents": prof.unique_parents},
                ))

    # ── Engine 4: Failure Cascade Tracker ───────────────────────────

    def _track_failure_cascades(self, report: DelegationReport,
                                graph: Dict[str, Set[str]]) -> None:
        """Detect failure propagation patterns."""
        # Group events by session and check for cascading failures
        session_events: Dict[str, List[DelegationEvent]] = defaultdict(list)
        for ev in self._events:
            session_events[ev.session_id].append(ev)

        cascade_count = 0
        for _session_id, events in session_events.items():
            failed = [e for e in events if not e.success]
            if len(failed) >= 2:
                # Check if failures form a chain (parent's child failed, then parent fails)
                failed_children = {e.child_agent_id for e in failed}
                for e in failed:
                    if e.parent_agent_id in failed_children:
                        cascade_count += 1
                        break

        if cascade_count >= 2:
            severity = Severity.HIGH if cascade_count > 5 else Severity.MEDIUM
            report.anti_patterns.append(AntiPattern(
                pattern_type=AntiPatternType.SINGLE_POINT_OF_FAILURE,
                severity=severity,
                agent_id="(fleet)",
                description=f"Detected {cascade_count} sessions with cascading delegation failures",
                evidence={"cascade_sessions": cascade_count},
            ))

    # ── Engine 5: Over-Delegation Detector ──────────────────────────

    def _detect_over_delegation(self, report: DelegationReport,
                                profiles: Dict[str, AgentDelegationProfile]) -> None:
        """Find agents that delegate too much without self-handling."""
        for agent_id, prof in profiles.items():
            if prof.delegations_sent < self.config.min_events_for_pattern:
                continue
            if prof.delegation_ratio >= self.config.over_delegation_threshold:
                severity = Severity.HIGH if prof.delegation_ratio > 0.95 else Severity.MEDIUM
                report.anti_patterns.append(AntiPattern(
                    pattern_type=AntiPatternType.OVER_DELEGATION,
                    severity=severity,
                    agent_id=agent_id,
                    description=f"Agent '{agent_id}' delegates {prof.delegation_ratio:.0%} of tasks "
                                f"({prof.delegations_sent} delegated, {prof.tasks_self_handled} self-handled)",
                    evidence={"delegation_ratio": round(prof.delegation_ratio, 3),
                              "sent": prof.delegations_sent, "self_handled": prof.tasks_self_handled},
                ))

            # Rubber-stamping: high re-delegation rate
            if prof.re_delegations > 0 and prof.delegations_received > 0:
                re_del_ratio = prof.re_delegations / prof.delegations_received
                if re_del_ratio >= 0.6 and prof.re_delegations >= self.config.min_events_for_pattern:
                    report.anti_patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.RUBBER_STAMPING,
                        severity=Severity.MEDIUM,
                        agent_id=agent_id,
                        description=f"Agent '{agent_id}' re-delegates {re_del_ratio:.0%} of received tasks "
                                    f"without adding value",
                        evidence={"re_delegation_ratio": round(re_del_ratio, 3),
                                  "re_delegations": prof.re_delegations},
                    ))

    # ── Engine 6: Accountability Gap Finder ─────────────────────────

    def _detect_accountability_gaps(self, report: DelegationReport,
                                     graph: Dict[str, Set[str]],
                                     profiles: Dict[str, AgentDelegationProfile]) -> None:
        """Find delegation chains where failures go unhandled."""
        # An accountability gap occurs when a child fails often but
        # the parent doesn't handle/retry/escalate
        parent_child_failures: Dict[Tuple[str, str], int] = defaultdict(int)
        parent_child_total: Dict[Tuple[str, str], int] = defaultdict(int)

        for ev in self._events:
            key = (ev.parent_agent_id, ev.child_agent_id)
            parent_child_total[key] += 1
            if not ev.success:
                parent_child_failures[key] += 1

        for key, failures in parent_child_failures.items():
            total = parent_child_total[key]
            if total < self.config.min_events_for_pattern:
                continue
            failure_rate = failures / total
            if failure_rate >= self.config.accountability_gap_threshold:
                parent, child = key
                report.anti_patterns.append(AntiPattern(
                    pattern_type=AntiPatternType.ACCOUNTABILITY_GAP,
                    severity=Severity.HIGH if failure_rate > 0.7 else Severity.MEDIUM,
                    agent_id=parent,
                    description=f"Agent '{parent}' delegates to '{child}' with {failure_rate:.0%} failure rate "
                                f"({failures}/{total}) — potential accountability gap",
                    evidence={"parent": parent, "child": child,
                              "failure_rate": round(failure_rate, 3), "failures": failures, "total": total},
                ))

    # ── Circular Delegation Detection ───────────────────────────────

    def _detect_circular_delegations(self, report: DelegationReport,
                                      graph: Dict[str, Set[str]]) -> None:
        """Detect A→B and B→A delegation patterns."""
        seen: Set[Tuple[str, str]] = set()
        for parent, children in graph.items():
            for child in children:
                if child in graph and parent in graph[child]:
                    pair = tuple(sorted([parent, child]))
                    if pair not in seen:
                        seen.add(pair)
                        report.circular_pairs.append((pair[0], pair[1]))
                        report.anti_patterns.append(AntiPattern(
                            pattern_type=AntiPatternType.CIRCULAR_DELEGATION,
                            severity=Severity.MEDIUM,
                            agent_id=f"{pair[0]},{pair[1]}",
                            description=f"Circular delegation between '{pair[0]}' and '{pair[1]}'",
                            evidence={"agent_a": pair[0], "agent_b": pair[1]},
                        ))

    # ── Unbalanced Load Detection ───────────────────────────────────

    def _detect_unbalanced_load(self, report: DelegationReport,
                                profiles: Dict[str, AgentDelegationProfile]) -> None:
        """Detect highly uneven delegation distribution using Gini coefficient."""
        received_counts = [p.delegations_received for p in profiles.values() if p.delegations_received > 0]
        if len(received_counts) < 3:
            return

        gini = self._gini_coefficient(received_counts)
        if gini >= self.config.imbalance_gini_threshold:
            severity = Severity.HIGH if gini > 0.8 else Severity.MEDIUM
            report.anti_patterns.append(AntiPattern(
                pattern_type=AntiPatternType.UNBALANCED_LOAD,
                severity=severity,
                agent_id="(fleet)",
                description=f"Delegation load is highly unbalanced (Gini={gini:.2f}, threshold={self.config.imbalance_gini_threshold})",
                evidence={"gini": round(gini, 3), "agent_count": len(received_counts)},
            ))

    # ── Engine 7: Insight Generator ─────────────────────────────────

    def _generate_insights(self, report: DelegationReport,
                           profiles: Dict[str, AgentDelegationProfile]) -> None:
        """Generate autonomous actionable insights."""
        if not self._events:
            return

        # Insight: delegation type breakdown
        type_counts = Counter(ev.delegation_type for ev in self._events)
        total = len(self._events)
        type_parts = ", ".join(f"{t}: {c} ({c/total:.0%})" for t, c in type_counts.most_common())
        report.insights.append(f"Delegation types: {type_parts}")

        # Insight: overall success rate
        successes = sum(1 for ev in self._events if ev.success)
        success_rate = successes / total
        report.insights.append(f"Overall delegation success rate: {success_rate:.1%} ({successes}/{total})")

        # Insight: busiest delegator
        if profiles:
            top_sender = max(profiles.values(), key=lambda p: p.delegations_sent)
            if top_sender.delegations_sent > 0:
                report.insights.append(
                    f"Top delegator: '{top_sender.agent_id}' with {top_sender.delegations_sent} delegations "
                    f"to {top_sender.fan_out} unique children"
                )

            # Busiest receiver
            top_receiver = max(profiles.values(), key=lambda p: p.delegations_received)
            if top_receiver.delegations_received > 0:
                report.insights.append(
                    f"Most delegated-to: '{top_receiver.agent_id}' with {top_receiver.delegations_received} "
                    f"tasks from {top_receiver.fan_in} parents"
                )

        # Insight: depth warning
        if report.max_chain_depth > self.config.max_healthy_depth:
            report.insights.append(
                f"⚠️ Maximum chain depth ({report.max_chain_depth}) exceeds healthy threshold "
                f"({self.config.max_healthy_depth}) — consider flattening delegation hierarchies"
            )

        # Insight: re-delegation rate
        re_del = sum(1 for ev in self._events if ev.was_re_delegated)
        if re_del > 0:
            report.insights.append(
                f"Re-delegation rate: {re_del/total:.1%} — tasks passed through without being processed"
            )

    # ── Health Score ────────────────────────────────────────────────

    def _compute_health_score(self, report: DelegationReport) -> None:
        """Compute composite health score 0-100."""
        score = 100.0

        # Penalty for deep chains (up to -20)
        if report.depth_distribution:
            deep = sum(c for d, c in report.depth_distribution.items() if d > self.config.max_healthy_depth)
            total = sum(report.depth_distribution.values())
            if total > 0:
                deep_ratio = deep / total
                score -= min(20.0, deep_ratio * 40.0)

        # Penalty for over-delegation patterns (up to -20)
        over_del_count = sum(1 for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.OVER_DELEGATION)
        score -= min(20.0, over_del_count * 7.0)

        # Penalty for bottlenecks (up to -15)
        score -= min(15.0, len(report.bottleneck_agents) * 8.0)

        # Penalty for failure cascades (up to -20)
        cascade_patterns = [ap for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.SINGLE_POINT_OF_FAILURE]
        for ap in cascade_patterns:
            cascades = ap.evidence.get("cascade_sessions", 0)
            score -= min(20.0, cascades * 3.0)

        # Penalty for accountability gaps (up to -15)
        gap_count = sum(1 for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.ACCOUNTABILITY_GAP)
        score -= min(15.0, gap_count * 5.0)

        # Penalty for circular delegations (up to -10)
        score -= min(10.0, len(report.circular_pairs) * 5.0)

        # Penalty for rubber stamping (up to -10)
        rubber_count = sum(1 for ap in report.anti_patterns if ap.pattern_type == AntiPatternType.RUBBER_STAMPING)
        score -= min(10.0, rubber_count * 5.0)

        # Clamp
        score = max(0.0, min(100.0, score))
        report.health_score = score

        # Tier classification
        if score >= 80:
            report.health_tier = DelegationHealthTier.EXCELLENT
        elif score >= 60:
            report.health_tier = DelegationHealthTier.HEALTHY
        elif score >= 40:
            report.health_tier = DelegationHealthTier.CONCERNING
        elif score >= 20:
            report.health_tier = DelegationHealthTier.UNHEALTHY
        else:
            report.health_tier = DelegationHealthTier.CRITICAL

    # ── Recommendations ─────────────────────────────────────────────

    def _generate_recommendations(self, report: DelegationReport) -> None:
        """Generate actionable recommendations based on detected patterns."""
        for ap in report.anti_patterns:
            if ap.pattern_type == AntiPatternType.OVER_DELEGATION:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.HIGH,
                    message=f"Reduce delegation ratio for '{ap.agent_id}' — consider handling simple tasks locally",
                    target_agent=ap.agent_id,
                ))
            elif ap.pattern_type == AntiPatternType.BOTTLENECK_AGENT:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.HIGH,
                    message=f"Distribute load away from bottleneck '{ap.agent_id}' — add parallel workers or shard tasks",
                    target_agent=ap.agent_id,
                ))
            elif ap.pattern_type == AntiPatternType.DEEP_CHAIN:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message="Flatten delegation hierarchies — prefer direct delegation over multi-hop chains",
                ))
            elif ap.pattern_type == AntiPatternType.CIRCULAR_DELEGATION:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message=f"Break circular delegation between {ap.agent_id} — assign clear ownership",
                    target_agent=ap.agent_id,
                ))
            elif ap.pattern_type == AntiPatternType.ACCOUNTABILITY_GAP:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.HIGH,
                    message=f"Add error handling/retry for '{ap.agent_id}' delegations with high failure rates",
                    target_agent=ap.agent_id,
                ))
            elif ap.pattern_type == AntiPatternType.RUBBER_STAMPING:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message=f"Remove pass-through agent '{ap.agent_id}' or add value-adding processing",
                    target_agent=ap.agent_id,
                ))
            elif ap.pattern_type == AntiPatternType.UNBALANCED_LOAD:
                report.recommendations.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message="Rebalance delegation distribution — some agents are overloaded while others are idle",
                ))

    # ── Helpers ─────────────────────────────────────────────────────

    def _build_profiles(self, all_agents: Set[str]) -> Dict[str, AgentDelegationProfile]:
        """Build per-agent profiles."""
        profiles: Dict[str, AgentDelegationProfile] = {
            a: AgentDelegationProfile(agent_id=a) for a in all_agents
        }

        children_of: Dict[str, Set[str]] = defaultdict(set)
        parents_of: Dict[str, Set[str]] = defaultdict(set)

        for ev in self._events:
            p = profiles[ev.parent_agent_id]
            c = profiles[ev.child_agent_id]

            p.delegations_sent += 1
            c.delegations_received += 1

            children_of[ev.parent_agent_id].add(ev.child_agent_id)
            parents_of[ev.child_agent_id].add(ev.parent_agent_id)

            if not ev.success:
                p.failures_as_parent += 1
                c.failures_as_child += 1

            if ev.was_re_delegated:
                c.re_delegations += 1

            if ev.depth > p.max_chain_depth:
                p.max_chain_depth = ev.depth

        # Count self-handled tasks: agents that appear as children but not parents
        # in the same session are doing work themselves
        sessions_as_child: Dict[str, Set[str]] = defaultdict(set)
        sessions_as_parent: Dict[str, Set[str]] = defaultdict(set)
        for ev in self._events:
            sessions_as_parent[ev.parent_agent_id].add(ev.session_id)
            sessions_as_child[ev.child_agent_id].add(ev.session_id)

        # Approximate self-handled: sessions where agent received work but didn't delegate
        for agent_id in all_agents:
            child_sessions = sessions_as_child[agent_id]
            parent_sessions = sessions_as_parent[agent_id]
            self_handled = len(child_sessions - parent_sessions)
            profiles[agent_id].tasks_self_handled = self_handled

        for agent_id in all_agents:
            profiles[agent_id].unique_children = len(children_of[agent_id])
            profiles[agent_id].unique_parents = len(parents_of[agent_id])

        return profiles

    @staticmethod
    def _gini_coefficient(values: List[int]) -> float:
        """Compute Gini coefficient for inequality measurement."""
        if not values or len(values) < 2:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        total = sum(sorted_vals)
        if total == 0:
            return 0.0
        cumulative = 0.0
        weighted_sum = 0.0
        for i, v in enumerate(sorted_vals):
            cumulative += v
            weighted_sum += (2 * (i + 1) - n - 1) * v
        return weighted_sum / (n * total)
