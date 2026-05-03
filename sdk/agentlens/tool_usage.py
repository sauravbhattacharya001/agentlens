"""Agent Tool Usage Profiler for AgentLens.

Autonomously analyzes how agents use their tools across sessions: call
frequency, success rates, latency impact, tool coupling patterns,
overreliance detection, underutilization, and optimal tool selection
recommendations.

Answers: "Which tools are my agents using well?  Which are causing problems?
What tool habits should change?"

Usage::

    from agentlens.tool_usage import ToolUsageProfiler, ToolEvent

    profiler = ToolUsageProfiler()

    profiler.add_event(ToolEvent(
        session_id="sess-001", agent_id="agent-alpha",
        tool_name="web_search", success=True,
        latency_ms=320.0, tokens_consumed=150,
    ))
    # … add more events …

    report = profiler.profile()
    print(report.format_report())
    print(f"Tool health score: {report.health_score}/100")

    for rec in report.recommendations:
        print(f"  [{rec.urgency.value}] {rec.message}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, collections).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class ToolHealthTier(Enum):
    """Overall tool ecosystem health classification."""
    EXCELLENT = "excellent"      # 80-100
    HEALTHY = "healthy"          # 60-79
    CONCERNING = "concerning"    # 40-59
    UNHEALTHY = "unhealthy"     # 20-39
    CRITICAL = "critical"       # 0-19

    @property
    def label(self) -> str:
        return self.value.title()


class OverrelianceLevel(Enum):
    """How overreliant an agent is on a specific tool."""
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


class CouplingStrength(Enum):
    """Strength of coupling between two tools."""
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    LOCKED = "locked"


class RecommendationUrgency(Enum):
    """Urgency level for a recommendation."""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        return {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


class AntiPatternType(Enum):
    """Types of tool usage anti-patterns."""
    OVERRELIANCE = "overreliance"           # Single tool dominates
    SPRAY_AND_PRAY = "spray_and_pray"       # Too many tools, low success
    RETRY_STORM = "retry_storm"             # Repeated failing calls
    TOOL_AVOIDANCE = "tool_avoidance"       # Agent avoids useful tools
    SEQUENTIAL_LOCK = "sequential_lock"     # Always calls tools in rigid order
    LATENCY_BLINDNESS = "latency_blindness" # Uses slow tools when fast ones exist
    FAILURE_IGNORANCE = "failure_ignorance" # Keeps using failing tools
    TOKEN_WASTE = "token_waste"             # High token consumption per tool call


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class ToolEvent:
    """A single tool call event."""
    session_id: str
    agent_id: str
    tool_name: str
    success: bool = True
    latency_ms: float = 0.0
    tokens_consumed: int = 0
    error_message: str = ""
    timestamp_ms: float = 0.0  # epoch ms, 0 = unknown
    retry_of: str = ""  # tool name this retries, empty = not a retry
    context: str = ""   # optional context tag


@dataclass
class ToolProfile:
    """Aggregated profile for a single tool."""
    tool_name: str
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    latencies: list[float] = field(default_factory=list)
    sessions_used: set[str] = field(default_factory=set)
    agents_used: set[str] = field(default_factory=set)
    error_messages: list[str] = field(default_factory=list)
    retry_count: int = 0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.call_count if self.call_count > 0 else 0.0

    @property
    def failure_rate(self) -> float:
        return self.failure_count / self.call_count if self.call_count > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.call_count if self.call_count > 0 else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_l = sorted(self.latencies)
        idx = int(math.ceil(0.95 * len(sorted_l))) - 1
        return sorted_l[max(0, idx)]

    @property
    def avg_tokens(self) -> float:
        return self.total_tokens / self.call_count if self.call_count > 0 else 0.0

    @property
    def session_spread(self) -> int:
        return len(self.sessions_used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "call_count": self.call_count,
            "success_rate": round(self.success_rate, 4),
            "failure_rate": round(self.failure_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "avg_tokens": round(self.avg_tokens, 1),
            "total_tokens": self.total_tokens,
            "session_spread": self.session_spread,
            "agent_count": len(self.agents_used),
            "retry_count": self.retry_count,
            "top_errors": Counter(self.error_messages).most_common(3),
        }


@dataclass
class ToolCoupling:
    """Coupling relationship between two tools."""
    tool_a: str
    tool_b: str
    co_occurrence_count: int = 0
    sequential_count: int = 0  # A followed by B
    total_sessions: int = 0
    strength: CouplingStrength = CouplingStrength.NONE
    co_occurrence_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_a": self.tool_a,
            "tool_b": self.tool_b,
            "co_occurrence_count": self.co_occurrence_count,
            "sequential_count": self.sequential_count,
            "co_occurrence_rate": round(self.co_occurrence_rate, 4),
            "strength": self.strength.value,
        }


@dataclass
class AgentToolProfile:
    """Per-agent tool usage profile."""
    agent_id: str
    tool_counts: dict[str, int] = field(default_factory=dict)
    tool_success: dict[str, int] = field(default_factory=dict)
    total_calls: int = 0
    diversity_score: float = 0.0  # Shannon entropy normalized
    overreliance: dict[str, OverrelianceLevel] = field(default_factory=dict)
    preferred_tools: list[str] = field(default_factory=list)
    avoided_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tool_counts": self.tool_counts,
            "total_calls": self.total_calls,
            "diversity_score": round(self.diversity_score, 4),
            "overreliance": {k: v.value for k, v in self.overreliance.items()},
            "preferred_tools": self.preferred_tools,
            "avoided_tools": self.avoided_tools,
        }


@dataclass
class AntiPattern:
    """A detected tool usage anti-pattern."""
    pattern_type: AntiPatternType
    agent_id: str
    tool_name: str = ""
    severity: float = 0.0  # 0-1
    evidence: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type.value,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "severity": round(self.severity, 3),
            "evidence": self.evidence,
            "suggestion": self.suggestion,
        }


@dataclass
class Recommendation:
    """An actionable recommendation."""
    urgency: RecommendationUrgency
    message: str
    agent_id: str = ""
    tool_name: str = ""
    expected_impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "urgency": self.urgency.value,
            "message": self.message,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "expected_impact": self.expected_impact,
        }


@dataclass
class ToolUsageReport:
    """Complete tool usage profiling report."""
    tool_profiles: list[ToolProfile] = field(default_factory=list)
    agent_profiles: list[AgentToolProfile] = field(default_factory=list)
    couplings: list[ToolCoupling] = field(default_factory=list)
    anti_patterns: list[AntiPattern] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    health_score: float = 100.0
    health_tier: ToolHealthTier = ToolHealthTier.EXCELLENT
    total_events: int = 0
    total_tools: int = 0
    total_agents: int = 0
    total_sessions: int = 0
    insights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "health_score": round(self.health_score, 1),
            "health_tier": self.health_tier.value,
            "total_events": self.total_events,
            "total_tools": self.total_tools,
            "total_agents": self.total_agents,
            "total_sessions": self.total_sessions,
            "tool_profiles": [t.to_dict() for t in self.tool_profiles],
            "agent_profiles": [a.to_dict() for a in self.agent_profiles],
            "couplings": [c.to_dict() for c in self.couplings],
            "anti_patterns": [p.to_dict() for p in self.anti_patterns],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "insights": self.insights,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def format_report(self) -> str:
        """Human-readable report."""
        lines: list[str] = []
        tier_icons = {
            "excellent": "🟢", "healthy": "🔵", "concerning": "🟡",
            "unhealthy": "🟠", "critical": "🔴",
        }
        icon = tier_icons.get(self.health_tier.value, "•")

        lines.append("=" * 64)
        lines.append("  AGENT TOOL USAGE PROFILE")
        lines.append("=" * 64)
        lines.append(f"  Health Score: {icon} {self.health_score:.0f}/100 ({self.health_tier.label})")
        lines.append(f"  Events: {self.total_events}  |  Tools: {self.total_tools}"
                      f"  |  Agents: {self.total_agents}  |  Sessions: {self.total_sessions}")
        lines.append("")

        # Tool profiles
        if self.tool_profiles:
            lines.append("─" * 64)
            lines.append("  TOOL PROFILES")
            lines.append("─" * 64)
            hdr = f"  {'Tool':<20} {'Calls':<7} {'Success':<9} {'Avg ms':<9} {'Avg Tok':<9} {'P95 ms':<8}"
            lines.append(hdr)
            lines.append(f"  {'─'*20} {'─'*6} {'─'*8} {'─'*8} {'─'*8} {'─'*7}")
            for tp in sorted(self.tool_profiles, key=lambda x: x.call_count, reverse=True):
                name = tp.tool_name[:20]
                lines.append(
                    f"  {name:<20} {tp.call_count:<7} {tp.success_rate:>6.0%}   "
                    f"{tp.avg_latency_ms:>7.0f}  {tp.avg_tokens:>7.0f}  {tp.p95_latency_ms:>6.0f}"
                )
            lines.append("")

        # Agent diversity
        if self.agent_profiles:
            lines.append("─" * 64)
            lines.append("  AGENT TOOL DIVERSITY")
            lines.append("─" * 64)
            for ap in sorted(self.agent_profiles, key=lambda x: x.diversity_score):
                bar = "█" * int(ap.diversity_score * 20)
                over = [f"{t}({v.value})" for t, v in ap.overreliance.items()
                        if v != OverrelianceLevel.NONE]
                over_str = f" ⚠ {', '.join(over)}" if over else ""
                lines.append(
                    f"  {ap.agent_id:<20} diversity={ap.diversity_score:.2f} "
                    f"|{bar}|{over_str}"
                )
            lines.append("")

        # Couplings
        strong = [c for c in self.couplings
                  if c.strength in (CouplingStrength.STRONG, CouplingStrength.LOCKED)]
        if strong:
            lines.append("─" * 64)
            lines.append("  STRONG TOOL COUPLINGS")
            lines.append("─" * 64)
            for c in sorted(strong, key=lambda x: x.co_occurrence_rate, reverse=True):
                lock = "🔒" if c.strength == CouplingStrength.LOCKED else "🔗"
                lines.append(
                    f"  {lock} {c.tool_a} ↔ {c.tool_b}  "
                    f"co-occur={c.co_occurrence_rate:.0%} "
                    f"(seq={c.sequential_count})"
                )
            lines.append("")

        # Anti-patterns
        if self.anti_patterns:
            lines.append("─" * 64)
            lines.append("  ANTI-PATTERNS DETECTED")
            lines.append("─" * 64)
            ap_icons = {
                "overreliance": "🎯", "spray_and_pray": "🔫",
                "retry_storm": "🌪️", "tool_avoidance": "🚫",
                "sequential_lock": "⛓️", "latency_blindness": "🐌",
                "failure_ignorance": "🙈", "token_waste": "🪙",
            }
            for ap in sorted(self.anti_patterns, key=lambda x: x.severity, reverse=True):
                icon = ap_icons.get(ap.pattern_type.value, "⚠")
                lines.append(
                    f"  {icon} [{ap.pattern_type.value}] {ap.agent_id}"
                    + (f"/{ap.tool_name}" if ap.tool_name else "")
                    + f" (severity={ap.severity:.0%})"
                )
                lines.append(f"    {ap.evidence}")
                lines.append(f"    → {ap.suggestion}")
            lines.append("")

        # Recommendations
        if self.recommendations:
            lines.append("─" * 64)
            lines.append("  RECOMMENDATIONS")
            lines.append("─" * 64)
            urg_icons = {
                "info": "ℹ️", "low": "💡", "medium": "⚡",
                "high": "🔥", "critical": "🚨",
            }
            for rec in sorted(self.recommendations,
                              key=lambda x: x.urgency.severity, reverse=True):
                icon = urg_icons.get(rec.urgency.value, "•")
                lines.append(f"  {icon} [{rec.urgency.value.upper()}] {rec.message}")
                if rec.expected_impact:
                    lines.append(f"    Impact: {rec.expected_impact}")
            lines.append("")

        # Insights
        if self.insights:
            lines.append("─" * 64)
            lines.append("  INSIGHTS")
            lines.append("─" * 64)
            for insight in self.insights:
                lines.append(f"  💡 {insight}")
            lines.append("")

        lines.append("=" * 64)
        return "\n".join(lines)


# ── Configuration ───────────────────────────────────────────────────


@dataclass
class ToolUsageConfig:
    """Tunable parameters for the profiler."""
    overreliance_threshold: float = 0.60   # >60% calls = overreliance
    severe_overreliance_threshold: float = 0.80
    coupling_threshold: float = 0.50       # co-occurrence rate for coupling
    strong_coupling_threshold: float = 0.80
    failure_rate_warning: float = 0.20     # >20% failure rate = warning
    failure_rate_critical: float = 0.50    # >50% failure rate = critical
    latency_slow_threshold_ms: float = 2000.0  # >2s = slow
    token_waste_threshold: float = 500.0   # >500 avg tokens per call
    retry_storm_threshold: float = 0.30    # >30% retries = storm
    min_calls_for_analysis: int = 3        # minimum calls to analyze a tool


# ── Profiler Engine ─────────────────────────────────────────────────


class ToolUsageProfiler:
    """Autonomous tool usage analysis engine.

    Engines:
    1. Tool Profile Aggregator — per-tool metrics
    2. Agent Diversity Analyzer — Shannon entropy per agent
    3. Coupling Detector — co-occurrence and sequential patterns
    4. Anti-Pattern Scanner — 8 anti-pattern categories
    5. Recommendation Generator — actionable improvement suggestions
    6. Health Scorer — composite ecosystem health
    7. Insight Generator — autonomous observations
    """

    def __init__(self, config: Optional[ToolUsageConfig] = None) -> None:
        self.config = config or ToolUsageConfig()
        self._events: list[ToolEvent] = []

    def add_event(self, event: ToolEvent) -> None:
        """Add a single tool event."""
        self._events.append(event)

    def add_events(self, events: list[ToolEvent]) -> None:
        """Add multiple tool events."""
        self._events.extend(events)

    def profile(self) -> ToolUsageReport:
        """Run full profiling analysis. Returns a complete report."""
        if not self._events:
            return ToolUsageReport()

        report = ToolUsageReport()
        report.total_events = len(self._events)

        # Engine 1: Aggregate tool profiles
        tool_profiles = self._aggregate_tool_profiles()
        report.tool_profiles = list(tool_profiles.values())
        report.total_tools = len(tool_profiles)

        # Collect unique agents and sessions
        agents = set(e.agent_id for e in self._events)
        sessions = set(e.session_id for e in self._events)
        report.total_agents = len(agents)
        report.total_sessions = len(sessions)

        # Engine 2: Agent diversity analysis
        report.agent_profiles = self._analyze_agent_diversity(tool_profiles)

        # Engine 3: Coupling detection
        report.couplings = self._detect_couplings(sessions)

        # Engine 4: Anti-pattern scanning
        report.anti_patterns = self._scan_anti_patterns(
            tool_profiles, report.agent_profiles, report.couplings
        )

        # Engine 5: Recommendations
        report.recommendations = self._generate_recommendations(
            tool_profiles, report.agent_profiles, report.anti_patterns
        )

        # Engine 6: Health scoring
        report.health_score = self._compute_health_score(
            tool_profiles, report.agent_profiles, report.anti_patterns
        )
        report.health_tier = self._classify_tier(report.health_score)

        # Engine 7: Insights
        report.insights = self._generate_insights(
            tool_profiles, report.agent_profiles, report.couplings, report.anti_patterns
        )

        return report

    # ── Engine 1: Tool Profile Aggregator ───────────────────────────

    def _aggregate_tool_profiles(self) -> dict[str, ToolProfile]:
        profiles: dict[str, ToolProfile] = {}
        for ev in self._events:
            if ev.tool_name not in profiles:
                profiles[ev.tool_name] = ToolProfile(tool_name=ev.tool_name)
            tp = profiles[ev.tool_name]
            tp.call_count += 1
            if ev.success:
                tp.success_count += 1
            else:
                tp.failure_count += 1
                if ev.error_message:
                    tp.error_messages.append(ev.error_message)
            tp.total_latency_ms += ev.latency_ms
            tp.total_tokens += ev.tokens_consumed
            tp.latencies.append(ev.latency_ms)
            tp.sessions_used.add(ev.session_id)
            tp.agents_used.add(ev.agent_id)
            if ev.retry_of:
                tp.retry_count += 1
        return profiles

    # ── Engine 2: Agent Diversity Analyzer ──────────────────────────

    def _analyze_agent_diversity(
        self, tool_profiles: dict[str, ToolProfile]
    ) -> list[AgentToolProfile]:
        # Group events by agent
        agent_events: dict[str, list[ToolEvent]] = defaultdict(list)
        for ev in self._events:
            agent_events[ev.agent_id].append(ev)

        all_tools = set(tool_profiles.keys())
        result: list[AgentToolProfile] = []

        for agent_id, events in agent_events.items():
            ap = AgentToolProfile(agent_id=agent_id)
            ap.total_calls = len(events)

            # Count per-tool usage
            for ev in events:
                ap.tool_counts[ev.tool_name] = ap.tool_counts.get(ev.tool_name, 0) + 1
                if ev.success:
                    ap.tool_success[ev.tool_name] = ap.tool_success.get(ev.tool_name, 0) + 1

            # Shannon entropy for diversity (normalized 0-1)
            if ap.total_calls > 0 and len(ap.tool_counts) > 1:
                total = ap.total_calls
                entropy = 0.0
                for count in ap.tool_counts.values():
                    if count > 0:
                        p = count / total
                        entropy -= p * math.log2(p)
                max_entropy = math.log2(len(ap.tool_counts))
                ap.diversity_score = entropy / max_entropy if max_entropy > 0 else 0.0
            elif len(ap.tool_counts) == 1:
                ap.diversity_score = 0.0
            else:
                ap.diversity_score = 1.0

            # Overreliance detection
            for tool_name, count in ap.tool_counts.items():
                share = count / ap.total_calls if ap.total_calls > 0 else 0
                if share >= self.config.severe_overreliance_threshold:
                    ap.overreliance[tool_name] = OverrelianceLevel.SEVERE
                elif share >= self.config.overreliance_threshold:
                    ap.overreliance[tool_name] = OverrelianceLevel.MODERATE
                elif share >= 0.40:
                    ap.overreliance[tool_name] = OverrelianceLevel.MILD

            # Preferred / avoided
            sorted_tools = sorted(ap.tool_counts.items(), key=lambda x: x[1], reverse=True)
            ap.preferred_tools = [t for t, _ in sorted_tools[:3]]

            used_tools = set(ap.tool_counts.keys())
            ap.avoided_tools = sorted(all_tools - used_tools)[:5]

            result.append(ap)

        return result

    # ── Engine 3: Coupling Detector ─────────────────────────────────

    def _detect_couplings(self, all_sessions: set[str]) -> list[ToolCoupling]:
        """Detect co-occurrence and sequential coupling between tools."""
        # Group events by session, ordered by timestamp
        session_events: dict[str, list[ToolEvent]] = defaultdict(list)
        for ev in self._events:
            session_events[ev.session_id].append(ev)

        # Sort by timestamp within each session
        for sid in session_events:
            session_events[sid].sort(key=lambda e: e.timestamp_ms)

        # Count co-occurrences and sequential pairs
        co_occur: Counter[tuple[str, str]] = Counter()
        sequential: Counter[tuple[str, str]] = Counter()

        for sid, events in session_events.items():
            tools_in_session = set(e.tool_name for e in events)
            # Co-occurrence: all pairs present in same session
            tool_list = sorted(tools_in_session)
            for i, t1 in enumerate(tool_list):
                for t2 in tool_list[i + 1:]:
                    co_occur[(t1, t2)] += 1

            # Sequential: consecutive tool calls
            for i in range(len(events) - 1):
                a, b = events[i].tool_name, events[i + 1].tool_name
                if a != b:
                    sequential[(a, b)] += 1

        total_sessions = len(all_sessions)
        if total_sessions == 0:
            return []

        couplings: list[ToolCoupling] = []
        for (t1, t2), count in co_occur.most_common():
            rate = count / total_sessions
            seq_count = sequential.get((t1, t2), 0) + sequential.get((t2, t1), 0)

            if rate >= self.config.strong_coupling_threshold:
                strength = CouplingStrength.LOCKED
            elif rate >= self.config.coupling_threshold:
                strength = CouplingStrength.STRONG
            elif rate >= 0.25:
                strength = CouplingStrength.MODERATE
            elif rate >= 0.10:
                strength = CouplingStrength.WEAK
            else:
                strength = CouplingStrength.NONE

            if strength != CouplingStrength.NONE:
                couplings.append(ToolCoupling(
                    tool_a=t1, tool_b=t2,
                    co_occurrence_count=count,
                    sequential_count=seq_count,
                    total_sessions=total_sessions,
                    strength=strength,
                    co_occurrence_rate=rate,
                ))

        return sorted(couplings, key=lambda c: c.co_occurrence_rate, reverse=True)

    # ── Engine 4: Anti-Pattern Scanner ──────────────────────────────

    def _scan_anti_patterns(
        self,
        tool_profiles: dict[str, ToolProfile],
        agent_profiles: list[AgentToolProfile],
        couplings: list[ToolCoupling],
    ) -> list[AntiPattern]:
        patterns: list[AntiPattern] = []

        # Per-agent patterns
        for ap in agent_profiles:
            # Overreliance
            for tool, level in ap.overreliance.items():
                if level in (OverrelianceLevel.MODERATE, OverrelianceLevel.SEVERE,
                             OverrelianceLevel.CRITICAL):
                    share = ap.tool_counts.get(tool, 0) / ap.total_calls if ap.total_calls else 0
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.OVERRELIANCE,
                        agent_id=ap.agent_id,
                        tool_name=tool,
                        severity=min(1.0, share),
                        evidence=f"{tool} accounts for {share:.0%} of all tool calls",
                        suggestion=f"Diversify tool usage; consider alternatives to {tool}",
                    ))

            # Spray and pray: many tools, low avg success
            if len(ap.tool_counts) >= 5:
                total_success = sum(ap.tool_success.get(t, 0) for t in ap.tool_counts)
                avg_success = total_success / ap.total_calls if ap.total_calls else 1
                if avg_success < 0.60:
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.SPRAY_AND_PRAY,
                        agent_id=ap.agent_id,
                        severity=1.0 - avg_success,
                        evidence=f"Uses {len(ap.tool_counts)} tools with only {avg_success:.0%} average success",
                        suggestion="Focus on fewer tools with higher success rates",
                    ))

        # Per-tool patterns
        for tp in tool_profiles.values():
            if tp.call_count < self.config.min_calls_for_analysis:
                continue

            # Retry storm
            retry_rate = tp.retry_count / tp.call_count if tp.call_count else 0
            if retry_rate >= self.config.retry_storm_threshold:
                # Find which agents are doing this
                agent_retries: dict[str, int] = defaultdict(int)
                for ev in self._events:
                    if ev.tool_name == tp.tool_name and ev.retry_of:
                        agent_retries[ev.agent_id] += 1
                for agent_id, count in agent_retries.items():
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.RETRY_STORM,
                        agent_id=agent_id,
                        tool_name=tp.tool_name,
                        severity=min(1.0, retry_rate * 2),
                        evidence=f"{tp.tool_name}: {retry_rate:.0%} retry rate ({tp.retry_count}/{tp.call_count})",
                        suggestion=f"Add error handling before retrying {tp.tool_name}; consider backoff",
                    ))

            # Failure ignorance
            if tp.failure_rate >= self.config.failure_rate_critical:
                for agent_id in tp.agents_used:
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.FAILURE_IGNORANCE,
                        agent_id=agent_id,
                        tool_name=tp.tool_name,
                        severity=tp.failure_rate,
                        evidence=f"{tp.tool_name} fails {tp.failure_rate:.0%} of the time ({tp.failure_count}/{tp.call_count})",
                        suggestion=f"Stop calling {tp.tool_name} or fix underlying issues",
                    ))

            # Latency blindness
            if tp.avg_latency_ms >= self.config.latency_slow_threshold_ms:
                for agent_id in tp.agents_used:
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.LATENCY_BLINDNESS,
                        agent_id=agent_id,
                        tool_name=tp.tool_name,
                        severity=min(1.0, tp.avg_latency_ms / (self.config.latency_slow_threshold_ms * 3)),
                        evidence=f"{tp.tool_name} averages {tp.avg_latency_ms:.0f}ms (p95={tp.p95_latency_ms:.0f}ms)",
                        suggestion=f"Consider faster alternatives or caching for {tp.tool_name}",
                    ))

            # Token waste
            if tp.avg_tokens >= self.config.token_waste_threshold:
                for agent_id in tp.agents_used:
                    patterns.append(AntiPattern(
                        pattern_type=AntiPatternType.TOKEN_WASTE,
                        agent_id=agent_id,
                        tool_name=tp.tool_name,
                        severity=min(1.0, tp.avg_tokens / (self.config.token_waste_threshold * 3)),
                        evidence=f"{tp.tool_name} consumes {tp.avg_tokens:.0f} tokens/call on average",
                        suggestion=f"Optimize prompts or reduce output for {tp.tool_name}",
                    ))

        # Sequential lock from couplings
        for c in couplings:
            if c.strength == CouplingStrength.LOCKED:
                patterns.append(AntiPattern(
                    pattern_type=AntiPatternType.SEQUENTIAL_LOCK,
                    agent_id="(fleet)",
                    tool_name=f"{c.tool_a}→{c.tool_b}",
                    severity=c.co_occurrence_rate,
                    evidence=f"{c.tool_a} and {c.tool_b} always appear together ({c.co_occurrence_rate:.0%})",
                    suggestion="Evaluate if these tools can be decoupled or merged",
                ))

        return sorted(patterns, key=lambda p: p.severity, reverse=True)

    # ── Engine 5: Recommendation Generator ──────────────────────────

    def _generate_recommendations(
        self,
        tool_profiles: dict[str, ToolProfile],
        agent_profiles: list[AgentToolProfile],
        anti_patterns: list[AntiPattern],
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []

        # From anti-patterns
        for ap in anti_patterns:
            if ap.severity >= 0.7:
                urgency = RecommendationUrgency.HIGH
            elif ap.severity >= 0.4:
                urgency = RecommendationUrgency.MEDIUM
            else:
                urgency = RecommendationUrgency.LOW

            recs.append(Recommendation(
                urgency=urgency,
                message=ap.suggestion,
                agent_id=ap.agent_id,
                tool_name=ap.tool_name,
                expected_impact=f"Reduce {ap.pattern_type.value} (severity {ap.severity:.0%})",
            ))

        # Tool-specific recs
        for tp in tool_profiles.values():
            if tp.call_count < self.config.min_calls_for_analysis:
                continue

            # High failure rate tools
            if self.config.failure_rate_warning <= tp.failure_rate < self.config.failure_rate_critical:
                recs.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message=f"Investigate {tp.tool_name} failures ({tp.failure_rate:.0%} failure rate)",
                    tool_name=tp.tool_name,
                    expected_impact=f"Could recover {tp.failure_count} failed calls",
                ))

            # Latency outliers
            if tp.p95_latency_ms > 3 * tp.avg_latency_ms and tp.avg_latency_ms > 100:
                recs.append(Recommendation(
                    urgency=RecommendationUrgency.LOW,
                    message=f"{tp.tool_name} has high p95 variance (avg={tp.avg_latency_ms:.0f}ms, p95={tp.p95_latency_ms:.0f}ms)",
                    tool_name=tp.tool_name,
                    expected_impact="Reduce tail latency for more predictable performance",
                ))

        # Agent-level recs
        for ap in agent_profiles:
            if ap.diversity_score < 0.3 and len(ap.tool_counts) > 1:
                recs.append(Recommendation(
                    urgency=RecommendationUrgency.MEDIUM,
                    message=f"Agent {ap.agent_id} has very low tool diversity ({ap.diversity_score:.2f})",
                    agent_id=ap.agent_id,
                    expected_impact="Broader tool usage may improve task completion",
                ))

        # Deduplicate by message
        seen: set[str] = set()
        unique: list[Recommendation] = []
        for r in recs:
            key = r.message
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return sorted(unique, key=lambda r: r.urgency.severity, reverse=True)

    # ── Engine 6: Health Scorer ─────────────────────────────────────

    def _compute_health_score(
        self,
        tool_profiles: dict[str, ToolProfile],
        agent_profiles: list[AgentToolProfile],
        anti_patterns: list[AntiPattern],
    ) -> float:
        """Compute composite health score 0-100."""
        if not tool_profiles:
            return 100.0

        # Factor 1: Average success rate (weight 30)
        total_calls = sum(tp.call_count for tp in tool_profiles.values())
        total_success = sum(tp.success_count for tp in tool_profiles.values())
        avg_success = total_success / total_calls if total_calls > 0 else 1.0
        f1 = avg_success * 30

        # Factor 2: Average diversity (weight 20)
        if agent_profiles:
            avg_diversity = statistics.mean(ap.diversity_score for ap in agent_profiles)
        else:
            avg_diversity = 1.0
        f2 = avg_diversity * 20

        # Factor 3: Low failure rate (weight 20)
        analyzable = [tp for tp in tool_profiles.values()
                      if tp.call_count >= self.config.min_calls_for_analysis]
        if analyzable:
            avg_fail = statistics.mean(tp.failure_rate for tp in analyzable)
            f3 = (1 - avg_fail) * 20
        else:
            f3 = 20

        # Factor 4: Anti-pattern penalty (weight 15)
        if anti_patterns:
            total_severity = sum(ap.severity for ap in anti_patterns)
            penalty = min(1.0, total_severity / max(1, len(agent_profiles) * 3))
            f4 = (1 - penalty) * 15
        else:
            f4 = 15

        # Factor 5: Latency health (weight 15)
        if analyzable:
            slow_tools = sum(1 for tp in analyzable
                           if tp.avg_latency_ms > self.config.latency_slow_threshold_ms)
            latency_health = 1 - (slow_tools / len(analyzable))
            f5 = latency_health * 15
        else:
            f5 = 15

        score = f1 + f2 + f3 + f4 + f5
        return max(0.0, min(100.0, score))

    def _classify_tier(self, score: float) -> ToolHealthTier:
        if score >= 80:
            return ToolHealthTier.EXCELLENT
        elif score >= 60:
            return ToolHealthTier.HEALTHY
        elif score >= 40:
            return ToolHealthTier.CONCERNING
        elif score >= 20:
            return ToolHealthTier.UNHEALTHY
        else:
            return ToolHealthTier.CRITICAL

    # ── Engine 7: Insight Generator ─────────────────────────────────

    def _generate_insights(
        self,
        tool_profiles: dict[str, ToolProfile],
        agent_profiles: list[AgentToolProfile],
        couplings: list[ToolCoupling],
        anti_patterns: list[AntiPattern],
    ) -> list[str]:
        insights: list[str] = []

        if not tool_profiles:
            return insights

        # Most/least used tools
        by_count = sorted(tool_profiles.values(), key=lambda t: t.call_count, reverse=True)
        if by_count:
            top = by_count[0]
            insights.append(
                f"Most used tool: {top.tool_name} ({top.call_count} calls, "
                f"{top.success_rate:.0%} success)"
            )
            if len(by_count) > 1:
                bottom = by_count[-1]
                insights.append(
                    f"Least used tool: {bottom.tool_name} ({bottom.call_count} calls)"
                )

        # Best/worst success rates
        analyzable = [tp for tp in tool_profiles.values()
                      if tp.call_count >= self.config.min_calls_for_analysis]
        if analyzable:
            best = max(analyzable, key=lambda t: t.success_rate)
            worst = min(analyzable, key=lambda t: t.success_rate)
            if best.success_rate != worst.success_rate:
                insights.append(
                    f"Best success rate: {best.tool_name} ({best.success_rate:.0%}) | "
                    f"Worst: {worst.tool_name} ({worst.success_rate:.0%})"
                )

            # Fastest/slowest
            fastest = min(analyzable, key=lambda t: t.avg_latency_ms)
            slowest = max(analyzable, key=lambda t: t.avg_latency_ms)
            if fastest.tool_name != slowest.tool_name:
                insights.append(
                    f"Fastest tool: {fastest.tool_name} ({fastest.avg_latency_ms:.0f}ms) | "
                    f"Slowest: {slowest.tool_name} ({slowest.avg_latency_ms:.0f}ms)"
                )

        # Diversity spread
        if agent_profiles:
            div_scores = [ap.diversity_score for ap in agent_profiles]
            if len(div_scores) > 1:
                spread = max(div_scores) - min(div_scores)
                if spread > 0.5:
                    most_diverse = max(agent_profiles, key=lambda a: a.diversity_score)
                    least_diverse = min(agent_profiles, key=lambda a: a.diversity_score)
                    insights.append(
                        f"Tool diversity gap: {most_diverse.agent_id} ({most_diverse.diversity_score:.2f}) "
                        f"vs {least_diverse.agent_id} ({least_diverse.diversity_score:.2f})"
                    )

        # Anti-pattern summary
        if anti_patterns:
            pattern_counts = Counter(ap.pattern_type.value for ap in anti_patterns)
            top_pattern = pattern_counts.most_common(1)[0]
            insights.append(
                f"Most common anti-pattern: {top_pattern[0]} ({top_pattern[1]} instances)"
            )

        # Coupling insight
        locked = [c for c in couplings if c.strength == CouplingStrength.LOCKED]
        if locked:
            insights.append(
                f"{len(locked)} locked tool coupling(s) detected — "
                "these tools always appear together"
            )

        # Token efficiency
        total_tokens = sum(tp.total_tokens for tp in tool_profiles.values())
        total_calls = sum(tp.call_count for tp in tool_profiles.values())
        if total_calls > 0:
            avg_tokens = total_tokens / total_calls
            insights.append(
                f"Average token consumption: {avg_tokens:.0f} tokens/call "
                f"({total_tokens:,} total across {total_calls} calls)"
            )

        return insights
