"""Agentic tool dependency advisor for AgentLens.

:class:`ToolDependencyAdvisor` builds a tool dependency graph from agent
traces and detects unhealthy coupling patterns that threaten reliability:

* **Circular dependencies** -- tool A always calls B, which calls C, which
  calls A again, creating fragile loops.
* **Single-point-of-failure tools** -- tools that many others depend on but
  have no fallback or redundancy.
* **Over-reliance chains** -- sequential pipelines where every tool in a
  chain is required, creating a longest-path fragility.
* **Orphan tools** -- tools that are never called as part of any dependency
  chain (potential dead code or misconfigured integrations).
* **Fan-out bottlenecks** -- tools that trigger an excessive number of
  downstream calls, amplifying failures.

This is a pure, deterministic sibling to
:class:`~agentlens.tool_reliability_advisor.ToolReliabilityAdvisor`,
:class:`~agentlens.agent_loop_detector.AgentLoopDetector`,
:class:`~agentlens.cost_attribution_advisor.CostAttributionAdvisor`, and
the rest of the agentlens advisor family.  Never mutates inputs, makes no
network calls, uses only the standard library.

Example
-------
::

    from agentlens import ToolDependencyAdvisor

    advisor = ToolDependencyAdvisor(risk_appetite="cautious")
    report = advisor.analyze(events)
    print(report.render_markdown())
"""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class DependencyVerdict(Enum):
    """Per-tool dependency health classification."""

    HEALTHY = "healthy"
    ISOLATED = "isolated"
    OVER_RELIED = "over_relied"
    FRAGILE_CHAIN = "fragile_chain"
    CIRCULAR_RISK = "circular_risk"
    FAN_OUT_BOTTLENECK = "fan_out_bottleneck"
    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"


class DependencyIssueCode(Enum):
    """Specific dependency signals attached to a tool."""

    CIRCULAR_DEPENDENCY = "circular_dependency"
    NO_FALLBACK_PATH = "no_fallback_path"
    EXCESSIVE_FAN_OUT = "excessive_fan_out"
    LONG_CHAIN = "long_chain"
    ORPHAN_TOOL = "orphan_tool"
    OVER_DEPENDED = "over_depended"
    COUPLING_DRIFT = "coupling_drift"


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
            return cls(str(value).lower())
        except ValueError:
            return cls.BALANCED


class DependencyGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"

# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class ToolNode:
    """Represents a single tool in the dependency graph."""

    name: str
    in_degree: int = 0
    out_degree: int = 0
    total_calls: int = 0
    unique_sessions: int = 0
    dependents: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    verdict: DependencyVerdict = DependencyVerdict.HEALTHY
    issues: list[DependencyIssueCode] = field(default_factory=list)
    risk_score: float = 0.0
    priority: ActionPriority = ActionPriority.P3

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "total_calls": self.total_calls,
            "unique_sessions": self.unique_sessions,
            "dependents": self.dependents,
            "dependencies": self.dependencies,
            "verdict": self.verdict.value,
            "issues": [i.value for i in self.issues],
            "risk_score": round(self.risk_score, 1),
            "priority": self.priority.value,
        }


@dataclass
class DependencyEdge:
    """A directed edge in the tool dependency graph."""

    source: str
    target: str
    call_count: int = 0
    avg_gap_ms: float = 0.0
    session_count: int = 0
    co_occurrence_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "call_count": self.call_count,
            "avg_gap_ms": round(self.avg_gap_ms, 1),
            "session_count": self.session_count,
            "co_occurrence_rate": round(self.co_occurrence_rate, 3),
        }


@dataclass
class CycleInfo:
    """Describes a detected circular dependency."""

    tools: list[str]
    length: int
    session_count: int
    frequency: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "length": self.length,
            "session_count": self.session_count,
            "frequency": round(self.frequency, 3),
        }


@dataclass
class ChainInfo:
    """Describes a fragile sequential chain."""

    tools: list[str]
    length: int
    session_count: int
    break_probability: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "length": self.length,
            "session_count": self.session_count,
            "break_probability": round(self.break_probability, 3),
        }


@dataclass
class PlaybookAction:
    """A recommended action from the advisor."""

    action_id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str = "platform-team"
    blast_radius: int = 1
    reversibility: str = "high"
    affected_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "priority": self.priority.value,
            "label": self.label,
            "reason": self.reason,
            "owner": self.owner,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "affected_tools": self.affected_tools,
        }

# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


@dataclass
class DependencyReport:
    """Structured output from :meth:`ToolDependencyAdvisor.analyze`."""

    tool_nodes: list[ToolNode]
    edges: list[DependencyEdge]
    cycles: list[CycleInfo]
    chains: list[ChainInfo]
    playbook: list[PlaybookAction]
    insights: list[str]
    grade: DependencyGrade
    overall_risk: float
    total_tools: int
    total_edges: int
    total_sessions: int
    headline: str

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append(f"VERDICT: grade={self.grade.value} risk={self.overall_risk:.1f} "
                     f"tools={self.total_tools} edges={self.total_edges} "
                     f"sessions={self.total_sessions}")
        lines.append("")
        lines.append("-- Tool Dependency Nodes --")
        for node in sorted(self.tool_nodes, key=lambda n: n.risk_score, reverse=True)[:15]:
            lines.append(f"  [{node.priority.value}] {node.name}: "
                         f"verdict={node.verdict.value} risk={node.risk_score:.0f} "
                         f"in={node.in_degree} out={node.out_degree}")
        lines.append("")
        if self.cycles:
            lines.append("-- Circular Dependencies --")
            for cyc in self.cycles[:5]:
                lines.append(f"  cycle: {' -> '.join(cyc.tools)} "
                             f"(length={cyc.length}, sessions={cyc.session_count})")
            lines.append("")
        if self.chains:
            lines.append("-- Fragile Chains --")
            for chain in self.chains[:5]:
                lines.append(f"  chain: {' -> '.join(chain.tools)} "
                             f"(length={chain.length}, break_prob={chain.break_probability:.1%})")
            lines.append("")
        lines.append("-- Playbook --")
        for action in self.playbook:
            lines.append(f"  [{action.priority.value}] {action.label} -- {action.reason}")
        lines.append("")
        lines.append("-- Insights --")
        for insight in self.insights:
            lines.append(f"  * {insight}")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append("## Tool Dependency Analysis Report\n")
        lines.append(f"**{self.headline}**\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Grade | {self.grade.value} |")
        lines.append(f"| Overall Risk | {self.overall_risk:.1f} |")
        lines.append(f"| Tools Analyzed | {self.total_tools} |")
        lines.append(f"| Dependency Edges | {self.total_edges} |")
        lines.append(f"| Sessions | {self.total_sessions} |")
        lines.append(f"| Cycles Detected | {len(self.cycles)} |")
        lines.append(f"| Fragile Chains | {len(self.chains)} |")
        lines.append("")
        lines.append("### Tool Nodes\n")
        lines.append("| Tool | Verdict | Risk | In | Out | Priority |")
        lines.append("|------|---------|------|----|-----|----------|")
        for node in sorted(self.tool_nodes, key=lambda n: n.risk_score, reverse=True)[:20]:
            lines.append(f"| {node.name} | {node.verdict.value} | {node.risk_score:.0f} "
                         f"| {node.in_degree} | {node.out_degree} | {node.priority.value} |")
        lines.append("")
        if self.cycles:
            lines.append("### Circular Dependencies\n")
            for cyc in self.cycles:
                lines.append(f"- {' -> '.join(cyc.tools)} (sessions={cyc.session_count})")
            lines.append("")
        if self.chains:
            lines.append("### Fragile Chains\n")
            for chain in self.chains:
                lines.append(f"- {' -> '.join(chain.tools)} (break_prob={chain.break_probability:.1%})")
            lines.append("")
        lines.append("### Playbook\n")
        lines.append("| Priority | Action | Reason | Blast | Reversibility |")
        lines.append("|----------|--------|--------|-------|---------------|")
        for action in self.playbook:
            lines.append(f"| {action.priority.value} | {action.label} | "
                         f"{action.reason} | {action.blast_radius} | {action.reversibility} |")
        lines.append("")
        lines.append("### Insights\n")
        for insight in self.insights:
            lines.append(f"- {insight}")
        return "\n".join(lines)

    def render_json(self) -> str:
        data = {
            "grade": self.grade.value,
            "overall_risk": round(self.overall_risk, 1),
            "headline": self.headline,
            "total_tools": self.total_tools,
            "total_edges": self.total_edges,
            "total_sessions": self.total_sessions,
            "tool_nodes": [n.to_dict() for n in self.tool_nodes],
            "edges": [e.to_dict() for e in self.edges],
            "cycles": [c.to_dict() for c in self.cycles],
            "chains": [c.to_dict() for c in self.chains],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": self.insights,
        }
        return json.dumps(data, indent=2)

# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class ToolDependencyAdvisor:
    """Builds a tool dependency graph and detects coupling anti-patterns.

    Parameters
    ----------
    risk_appetite : str or RiskAppetite
        Sensitivity level: "cautious", "balanced" (default), or "aggressive".
    min_co_occurrence : float
        Minimum co-occurrence rate (0-1) to register a dependency edge.
    max_gap_ms : float
        Maximum time gap between sequential tool calls to consider them
        as a dependency pair.
    now_fn : callable, optional
        Injectable clock for deterministic testing.
    """

    def __init__(
        self,
        risk_appetite: str | RiskAppetite = "balanced",
        min_co_occurrence: float = 0.3,
        max_gap_ms: float = 30_000,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._appetite = RiskAppetite.parse(risk_appetite)
        self._min_co_occurrence = min_co_occurrence
        self._max_gap_ms = max_gap_ms
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def analyze(self, events: Iterable[Any]) -> DependencyReport:
        """Run the full dependency analysis."""
        sessions = self._group_by_session(events)
        total_sessions = len(sessions)

        if total_sessions == 0:
            return self._empty_report()

        edges_raw = self._extract_edges(sessions, total_sessions)
        tool_calls = self._count_tool_calls(sessions)

        tool_names = set(tool_calls.keys())
        for edge in edges_raw:
            tool_names.add(edge.source)
            tool_names.add(edge.target)

        nodes = self._build_nodes(tool_names, edges_raw, tool_calls, sessions)
        cycles = self._detect_cycles(edges_raw, sessions, total_sessions)
        chains = self._detect_chains(edges_raw, sessions, total_sessions)
        nodes = self._classify_nodes(nodes, edges_raw, cycles, chains, total_sessions)
        playbook = self._generate_playbook(nodes, cycles, chains)
        insights = self._generate_insights(nodes, edges_raw, cycles, chains, total_sessions)

        overall_risk = self._compute_risk(nodes, cycles, chains)
        has_p0 = any(n.priority == ActionPriority.P0 for n in nodes)
        grade = self._grade_from_risk(overall_risk, has_p0)

        headline = (
            f"VERDICT: grade={grade.value} risk={overall_risk:.1f} "
            f"tools={len(nodes)} edges={len(edges_raw)} "
            f"cycles={len(cycles)} chains={len(chains)}"
        )

        return DependencyReport(
            tool_nodes=sorted(nodes, key=lambda n: n.risk_score, reverse=True),
            edges=edges_raw,
            cycles=cycles,
            chains=chains,
            playbook=playbook,
            insights=insights,
            grade=grade,
            overall_risk=overall_risk,
            total_tools=len(nodes),
            total_edges=len(edges_raw),
            total_sessions=total_sessions,
            headline=headline,
        )

    # -- Internal: Event Parsing --

    def _group_by_session(self, events: Iterable[Any]) -> dict[str, list[dict]]:
        sessions: dict[str, list[dict]] = defaultdict(list)
        for ev in events:
            rec = self._normalize_event(ev)
            if rec and rec.get("session_id"):
                sessions[rec["session_id"]].append(rec)
        for sid in sessions:
            sessions[sid].sort(key=lambda e: e.get("timestamp", ""))
        return dict(sessions)

    def _normalize_event(self, ev: Any) -> Optional[dict]:
        if ev is None:
            return None
        if isinstance(ev, dict):
            return ev
        if hasattr(ev, "model_dump"):
            return ev.model_dump()
        if hasattr(ev, "__dict__"):
            return copy.copy(ev.__dict__)
        return None

    # -- Internal: Edge Extraction --

    def _extract_edges(self, sessions: dict[str, list[dict]], total_sessions: int) -> list[DependencyEdge]:
        pair_counts: Counter = Counter()
        pair_gaps: defaultdict = defaultdict(list)
        pair_sessions: defaultdict = defaultdict(set)

        for sid, events in sessions.items():
            tool_events = [e for e in events if self._get_tool_name(e)]
            for i in range(len(tool_events) - 1):
                src = self._get_tool_name(tool_events[i])
                tgt = self._get_tool_name(tool_events[i + 1])
                if not src or not tgt or src == tgt:
                    continue
                gap_ms = self._time_gap_ms(tool_events[i], tool_events[i + 1])
                if gap_ms is not None and gap_ms > self._max_gap_ms:
                    continue
                pair = (src, tgt)
                pair_counts[pair] += 1
                if gap_ms is not None:
                    pair_gaps[pair].append(gap_ms)
                pair_sessions[pair].add(sid)

        edges: list[DependencyEdge] = []
        for pair, count in pair_counts.most_common():
            session_count = len(pair_sessions[pair])
            co_rate = session_count / total_sessions if total_sessions > 0 else 0
            if co_rate < self._min_co_occurrence:
                continue
            gaps = pair_gaps[pair]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
            edges.append(DependencyEdge(
                source=pair[0], target=pair[1],
                call_count=count, avg_gap_ms=avg_gap,
                session_count=session_count, co_occurrence_rate=co_rate,
            ))
        return edges

    def _count_tool_calls(self, sessions: dict[str, list[dict]]) -> Counter:
        counts: Counter = Counter()
        for events in sessions.values():
            for e in events:
                name = self._get_tool_name(e)
                if name:
                    counts[name] += 1
        return counts

    def _get_tool_name(self, event: dict) -> Optional[str]:
        if event.get("event_type") == "tool_call":
            tc = event.get("tool_call")
            if isinstance(tc, dict):
                return tc.get("tool_name")
            if hasattr(tc, "tool_name"):
                return tc.tool_name
        return event.get("tool_name") if event.get("event_type") == "tool_call" else None

    def _time_gap_ms(self, ev1: dict, ev2: dict) -> Optional[float]:
        t1 = ev1.get("timestamp")
        t2 = ev2.get("timestamp")
        if t1 is None or t2 is None:
            return None
        if isinstance(t1, str):
            try:
                t1 = datetime.fromisoformat(t1)
            except (ValueError, TypeError):
                return None
        if isinstance(t2, str):
            try:
                t2 = datetime.fromisoformat(t2)
            except (ValueError, TypeError):
                return None
        if isinstance(t1, datetime) and isinstance(t2, datetime):
            return max(0, (t2 - t1).total_seconds() * 1000)
        return None

    # -- Internal: Node Building --

    def _build_nodes(self, tool_names: set[str], edges: list[DependencyEdge],
                     tool_calls: Counter, sessions: dict[str, list[dict]]) -> list[ToolNode]:
        tool_sessions: defaultdict = defaultdict(set)
        for sid, events in sessions.items():
            for e in events:
                name = self._get_tool_name(e)
                if name:
                    tool_sessions[name].add(sid)

        out_adj: defaultdict = defaultdict(set)
        in_adj: defaultdict = defaultdict(set)
        for edge in edges:
            out_adj[edge.source].add(edge.target)
            in_adj[edge.target].add(edge.source)

        nodes: list[ToolNode] = []
        for name in sorted(tool_names):
            nodes.append(ToolNode(
                name=name,
                in_degree=len(in_adj[name]),
                out_degree=len(out_adj[name]),
                total_calls=tool_calls.get(name, 0),
                unique_sessions=len(tool_sessions[name]),
                dependents=sorted(in_adj[name]),
                dependencies=sorted(out_adj[name]),
            ))
        return nodes

    # -- Internal: Cycle Detection --

    def _detect_cycles(self, edges: list[DependencyEdge], sessions: dict[str, list[dict]],
                       total_sessions: int) -> list[CycleInfo]:
        adj: defaultdict = defaultdict(set)
        for edge in edges:
            adj[edge.source].add(edge.target)

        all_nodes = set(adj.keys())
        for targets in adj.values():
            all_nodes.update(targets)

        visited: set = set()
        rec_stack: set = set()
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbor in sorted(adj.get(node, set())):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    idx = path.index(neighbor)
                    cycle = path[idx:] + [neighbor]
                    if len(cycle) >= 3:
                        cycles.append(cycle)
            path.pop()
            rec_stack.discard(node)

        for node in sorted(all_nodes):
            if node not in visited:
                dfs(node)

        seen_cycles: set = set()
        result: list[CycleInfo] = []
        for cycle in cycles:
            core = cycle[:-1]
            if not core:
                continue
            min_idx = core.index(min(core))
            normalized = tuple(core[min_idx:] + core[:min_idx])
            if normalized in seen_cycles:
                continue
            seen_cycles.add(normalized)

            cycle_set = set(core)
            session_count = 0
            for sid, events in sessions.items():
                tools_in_session = {self._get_tool_name(e) for e in events if self._get_tool_name(e)}
                if cycle_set.issubset(tools_in_session):
                    session_count += 1

            result.append(CycleInfo(
                tools=list(normalized) + [normalized[0]],
                length=len(normalized),
                session_count=session_count,
                frequency=session_count / total_sessions if total_sessions > 0 else 0,
            ))
        return sorted(result, key=lambda c: c.session_count, reverse=True)

    # -- Internal: Chain Detection --

    def _detect_chains(self, edges: list[DependencyEdge], sessions: dict[str, list[dict]],
                       total_sessions: int) -> list[ChainInfo]:
        adj: defaultdict = defaultdict(set)
        in_deg: Counter = Counter()
        for edge in edges:
            adj[edge.source].add(edge.target)
            in_deg[edge.target] += 1

        all_nodes = set(adj.keys())
        for targets in adj.values():
            all_nodes.update(targets)

        chain_heads = [n for n in sorted(all_nodes) if in_deg[n] == 0]
        if not chain_heads:
            chain_heads = sorted(all_nodes)[:5]

        chains: list[list[str]] = []
        for head in chain_heads:
            chain = self._follow_chain(head, adj)
            if len(chain) >= 3:
                chains.append(chain)

        result: list[ChainInfo] = []
        seen: set = set()
        for chain in sorted(chains, key=len, reverse=True)[:10]:
            key = tuple(chain)
            if key in seen:
                continue
            seen.add(key)

            chain_set = set(chain)
            session_count = 0
            for sid, events in sessions.items():
                tools_in_session = {self._get_tool_name(e) for e in events if self._get_tool_name(e)}
                if chain_set.issubset(tools_in_session):
                    session_count += 1

            link_success = 0.95
            break_prob = 1.0 - (link_success ** (len(chain) - 1))
            result.append(ChainInfo(
                tools=chain, length=len(chain),
                session_count=session_count, break_probability=break_prob,
            ))
        return result

    def _follow_chain(self, start: str, adj: dict[str, set]) -> list[str]:
        chain = [start]
        visited = {start}
        current = start
        while True:
            successors = adj.get(current, set()) - visited
            if len(successors) != 1:
                break
            next_node = next(iter(sorted(successors)))
            chain.append(next_node)
            visited.add(next_node)
            current = next_node
        return chain

    # -- Internal: Node Classification --

    def _classify_nodes(self, nodes: list[ToolNode], edges: list[DependencyEdge],
                        cycles: list[CycleInfo], chains: list[ChainInfo],
                        total_sessions: int) -> list[ToolNode]:
        appetite_mult = {
            RiskAppetite.CAUTIOUS: 1.15,
            RiskAppetite.BALANCED: 1.0,
            RiskAppetite.AGGRESSIVE: 0.85,
        }[self._appetite]

        cycle_tools: set = set()
        for cyc in cycles:
            cycle_tools.update(cyc.tools)

        chain_tools: set = set()
        for chain in chains:
            chain_tools.update(chain.tools)

        for node in nodes:
            risk = 0.0
            issues: list[DependencyIssueCode] = []

            if node.name in cycle_tools:
                issues.append(DependencyIssueCode.CIRCULAR_DEPENDENCY)
                risk += 30

            if node.in_degree >= 3:
                issues.append(DependencyIssueCode.OVER_DEPENDED)
                risk += node.in_degree * 8

            if node.out_degree >= 4:
                issues.append(DependencyIssueCode.EXCESSIVE_FAN_OUT)
                risk += node.out_degree * 5

            if node.name in chain_tools:
                issues.append(DependencyIssueCode.LONG_CHAIN)
                risk += 15

            if node.in_degree == 0 and node.out_degree == 0:
                issues.append(DependencyIssueCode.ORPHAN_TOOL)
                risk += 5

            risk = min(100, risk * appetite_mult)
            node.risk_score = risk
            node.issues = issues

            # Assign verdict
            if DependencyIssueCode.CIRCULAR_DEPENDENCY in issues and risk >= 50:
                node.verdict = DependencyVerdict.CIRCULAR_RISK
            elif node.in_degree >= 4 and risk >= 40:
                node.verdict = DependencyVerdict.SINGLE_POINT_OF_FAILURE
            elif node.in_degree >= 3:
                node.verdict = DependencyVerdict.OVER_RELIED
            elif node.out_degree >= 4:
                node.verdict = DependencyVerdict.FAN_OUT_BOTTLENECK
            elif DependencyIssueCode.LONG_CHAIN in issues:
                node.verdict = DependencyVerdict.FRAGILE_CHAIN
            elif DependencyIssueCode.ORPHAN_TOOL in issues:
                node.verdict = DependencyVerdict.ISOLATED
            else:
                node.verdict = DependencyVerdict.HEALTHY

            # Assign priority
            if risk >= 60:
                node.priority = ActionPriority.P0
            elif risk >= 40:
                node.priority = ActionPriority.P1
            elif risk >= 20:
                node.priority = ActionPriority.P2
            else:
                node.priority = ActionPriority.P3

        return nodes

    # -- Internal: Playbook --

    def _generate_playbook(self, nodes: list[ToolNode], cycles: list[CycleInfo],
                           chains: list[ChainInfo]) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []
        emitted: set = set()

        # Circular dependency actions
        cycle_nodes = [n for n in nodes if DependencyIssueCode.CIRCULAR_DEPENDENCY in n.issues]
        if cycle_nodes:
            aid = "BREAK_CIRCULAR_DEPENDENCIES"
            if aid not in emitted:
                emitted.add(aid)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P0,
                    label="Break circular dependencies",
                    reason=f"{len(cycles)} cycle(s) detected involving {len(cycle_nodes)} tools",
                    owner="platform-team", blast_radius=4, reversibility="low",
                    affected_tools=[n.name for n in cycle_nodes],
                ))

        # Single point of failure
        spof_nodes = [n for n in nodes if n.verdict == DependencyVerdict.SINGLE_POINT_OF_FAILURE]
        if spof_nodes:
            aid = "ADD_REDUNDANCY_FOR_SPOF"
            if aid not in emitted:
                emitted.add(aid)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P0,
                    label="Add redundancy for single-point-of-failure tools",
                    reason=f"{len(spof_nodes)} tool(s) with 4+ dependents and no fallback",
                    owner="platform-team", blast_radius=4, reversibility="medium",
                    affected_tools=[n.name for n in spof_nodes],
                ))

        # Over-relied tools
        over_relied = [n for n in nodes if n.verdict == DependencyVerdict.OVER_RELIED]
        if over_relied:
            aid = "REDUCE_TOOL_COUPLING"
            if aid not in emitted:
                emitted.add(aid)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P1,
                    label="Reduce tool coupling",
                    reason=f"{len(over_relied)} tool(s) with high in-degree coupling",
                    owner="platform-team", blast_radius=3, reversibility="medium",
                    affected_tools=[n.name for n in over_relied],
                ))

        # Fan-out bottlenecks
        fan_out = [n for n in nodes if n.verdict == DependencyVerdict.FAN_OUT_BOTTLENECK]
        if fan_out:
            aid = "REDUCE_FAN_OUT"
            if aid not in emitted:
                emitted.add(aid)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P1,
                    label="Reduce fan-out bottlenecks",
                    reason=f"{len(fan_out)} tool(s) triggering excessive downstream calls",
                    owner="platform-team", blast_radius=3, reversibility="high",
                    affected_tools=[n.name for n in fan_out],
                ))

        # Fragile chains
        if chains:
            aid = "SHORTEN_FRAGILE_CHAINS"
            if aid not in emitted:
                emitted.add(aid)
                max_len = max(c.length for c in chains)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P2,
                    label="Shorten fragile tool chains",
                    reason=f"{len(chains)} chain(s) detected (max length={max_len})",
                    owner="platform-team", blast_radius=2, reversibility="high",
                    affected_tools=list({t for c in chains for t in c.tools})[:10],
                ))

        # Orphan tools
        orphans = [n for n in nodes if n.verdict == DependencyVerdict.ISOLATED]
        if orphans:
            aid = "AUDIT_ORPHAN_TOOLS"
            if aid not in emitted:
                emitted.add(aid)
                actions.append(PlaybookAction(
                    action_id=aid, priority=ActionPriority.P3,
                    label="Audit orphan tools",
                    reason=f"{len(orphans)} tool(s) with no dependency connections",
                    owner="platform-team", blast_radius=1, reversibility="high",
                    affected_tools=[n.name for n in orphans],
                ))

        if not actions:
            actions.append(PlaybookAction(
                action_id="MAINTAIN_OBSERVATION", priority=ActionPriority.P3,
                label="Maintain observation",
                reason="Tool dependency graph appears healthy",
                owner="platform-team", blast_radius=1, reversibility="high",
            ))

        actions.sort(key=lambda a: (a.priority.value, a.action_id))
        return actions

    # -- Internal: Insights --

    def _generate_insights(self, nodes: list[ToolNode], edges: list[DependencyEdge],
                           cycles: list[CycleInfo], chains: list[ChainInfo],
                           total_sessions: int) -> list[str]:
        insights: list[str] = []

        if not nodes:
            insights.append("No tool dependency data available")
            return insights

        healthy = sum(1 for n in nodes if n.verdict == DependencyVerdict.HEALTHY)
        total = len(nodes)
        insights.append(f"{healthy}/{total} tools have healthy dependency profiles")

        if edges:
            avg_co = sum(e.co_occurrence_rate for e in edges) / len(edges)
            insights.append(f"Average co-occurrence rate across {len(edges)} edges: {avg_co:.1%}")

        if cycles:
            insights.append(f"{len(cycles)} circular dependency pattern(s) found -- "
                            "consider introducing circuit breakers or async decoupling")

        if chains:
            max_chain = max(chains, key=lambda c: c.length)
            insights.append(f"Longest fragile chain has {max_chain.length} tools "
                            f"with {max_chain.break_probability:.1%} estimated break probability")

        high_risk = [n for n in nodes if n.risk_score >= 50]
        if high_risk:
            insights.append(f"{len(high_risk)} tool(s) at elevated dependency risk (score >= 50)")

        return insights

    # -- Internal: Risk & Grade --

    def _compute_risk(self, nodes: list[ToolNode], cycles: list[CycleInfo],
                      chains: list[ChainInfo]) -> float:
        if not nodes:
            return 0.0
        node_risk = sum(n.risk_score for n in nodes) / len(nodes)
        cycle_penalty = min(30, len(cycles) * 10)
        chain_penalty = min(20, len(chains) * 5)
        return min(100, node_risk + cycle_penalty + chain_penalty)

    def _grade_from_risk(self, risk: float, has_p0: bool) -> DependencyGrade:
        if has_p0 or risk >= 70:
            return DependencyGrade.F
        if risk >= 50:
            return DependencyGrade.D
        if risk >= 30:
            return DependencyGrade.C
        if risk >= 15:
            return DependencyGrade.B
        return DependencyGrade.A

    def _empty_report(self) -> DependencyReport:
        return DependencyReport(
            tool_nodes=[], edges=[], cycles=[], chains=[],
            playbook=[PlaybookAction(
                action_id="NO_DATA", priority=ActionPriority.P3,
                label="Collect tool call data",
                reason="No events to analyze",
            )],
            insights=["No tool call events found"],
            grade=DependencyGrade.A, overall_risk=0.0,
            total_tools=0, total_edges=0, total_sessions=0,
            headline="VERDICT: grade=A risk=0.0 tools=0 edges=0 cycles=0 chains=0",
        )