"""Agent Context Utilization Analyzer for AgentLens.

Autonomously analyzes how efficiently AI agents use their context windows,
detecting token waste, context pollution, information density decay, and
providing optimization recommendations.

Answers: "Is my agent wasting context? Where are tokens being spent inefficiently?
When will context exhaustion occur?"

8 analysis engines:
  1. Token Density Analyzer — information density per token, filler detection
  2. Context Pollution Detector — redundant/stale/irrelevant content accumulation
  3. Working Memory Efficiency — active vs dead-weight context tracking
  4. Prompt Overhead Calculator — system prompt overhead as % of total context
  5. Tool Output Compaction Analyzer — verbose tool output detection
  6. Context Window Pressure Tracker — proximity to context limits over time
  7. Information Retrieval Efficiency — redundant fetch detection
  8. Utilization Insight Generator — cross-engine synthesis and recommendations

Usage::

    from agentlens.context_utilization import ContextUtilizationAnalyzer

    analyzer = ContextUtilizationAnalyzer()
    report = analyzer.analyze(session)
    print(report.format_report())
    print(f"Utilization score: {report.utilization_score}/100")
    print(f"Grade: {report.grade}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, json, collections, re).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class EfficiencyGrade(Enum):
    """Context utilization grade."""
    A = "A"  # 90-100 Excellent
    B = "B"  # 75-89  Good
    C = "C"  # 60-74  Fair
    D = "D"  # 40-59  Poor
    F = "F"  # 0-39   Wasteful


class PollutionType(Enum):
    """Type of context pollution."""
    REDUNDANT_CONTENT = "redundant_content"
    STALE_TOOL_OUTPUT = "stale_tool_output"
    IRRELEVANT_FILLER = "irrelevant_filler"
    REPEATED_INSTRUCTION = "repeated_instruction"
    ABANDONED_THREAD = "abandoned_thread"


class InsightSeverity(Enum):
    """Severity of utilization insight."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class InsightCategory(Enum):
    """Category of utilization insight."""
    DENSITY = "density"
    POLLUTION = "pollution"
    WORKING_MEMORY = "working_memory"
    OVERHEAD = "overhead"
    TOOL_OUTPUT = "tool_output"
    PRESSURE = "pressure"
    REDUNDANT_FETCH = "redundant_fetch"
    GENERAL = "general"


# ── Pre-compiled patterns ───────────────────────────────────────────

_RE_WORD = re.compile(r'\b[a-zA-Z]{3,}\b')
_RE_FILLER = re.compile(
    r'\b(um|uh|well|basically|actually|like|just|really|very|quite|'
    r'certainly|definitely|obviously|clearly|simply|honestly|literally|'
    r'absolutely|totally|completely|entirely|utterly|perfectly)\b',
    re.IGNORECASE,
)
_RE_REPETITION = re.compile(r'(.{20,}?)\1+', re.DOTALL)


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class ContextUtilizationConfig:
    """Configuration for the analyzer."""
    window_size: int = 5
    context_limit_tokens: int = 128000
    density_weight: float = 0.15
    pollution_weight: float = 0.15
    working_memory_weight: float = 0.15
    overhead_weight: float = 0.10
    tool_output_weight: float = 0.15
    pressure_weight: float = 0.15
    retrieval_weight: float = 0.15
    filler_threshold: float = 0.15
    pollution_similarity_threshold: float = 0.6
    overhead_warning_pct: float = 0.30
    pressure_warning_pct: float = 0.75
    min_events: int = 3
    tool_output_verbose_threshold: int = 500


@dataclass
class TokenDensityResult:
    """Result from token density analysis."""
    density_ratio: float  # unique concepts / total tokens
    filler_pct: float  # percentage of filler words
    unique_concept_count: int
    total_tokens: int
    score: float  # 0-100
    per_window_density: List[float] = field(default_factory=list)


@dataclass
class PollutionEvent:
    """A detected context pollution event."""
    pollution_type: PollutionType
    event_index: int
    severity: InsightSeverity
    waste_tokens: int
    description: str = ""


@dataclass
class WorkingMemorySnapshot:
    """Snapshot of working memory efficiency."""
    event_index: int
    active_tokens: int
    dead_weight_tokens: int
    efficiency_ratio: float  # active / total


@dataclass
class PressurePoint:
    """Context window pressure measurement."""
    event_index: int
    cumulative_tokens: int
    usage_pct: float
    projected_exhaustion_events: Optional[int] = None


@dataclass
class RedundantFetch:
    """A detected redundant information fetch."""
    original_index: int
    refetch_index: int
    info_key: str
    waste_tokens: int


@dataclass
class UtilizationInsight:
    """An actionable insight from the analysis."""
    category: InsightCategory
    severity: InsightSeverity
    description: str
    recommendation: str
    estimated_savings_pct: float = 0.0


@dataclass
class ContextUtilizationReport:
    """Complete context utilization analysis report."""
    session_id: str
    total_events: int
    total_tokens: int

    # Per-engine results
    density: TokenDensityResult
    pollution_events: List[PollutionEvent]
    pollution_score: float
    working_memory_snapshots: List[WorkingMemorySnapshot]
    working_memory_score: float
    overhead_pct: float
    overhead_score: float
    tool_output_score: float
    tool_output_verbose_count: int
    tool_output_total_waste: int
    pressure_points: List[PressurePoint]
    pressure_score: float
    redundant_fetches: List[RedundantFetch]
    retrieval_score: float

    # Composite
    utilization_score: float  # 0-100
    grade: EfficiencyGrade
    insights: List[UtilizationInsight] = field(default_factory=list)

    def format_report(self) -> str:
        """Produce a rich ASCII report."""
        lines: List[str] = []
        w = 62

        # Header
        lines.append("╔" + "═" * w + "╗")
        lines.append("║" + " CONTEXT UTILIZATION ANALYSIS ".center(w) + "║")
        lines.append("╠" + "═" * w + "╣")

        grade_icon = {"A": "🌟", "B": "✅", "C": "⚠️", "D": "🔶", "F": "🔴"}.get(
            self.grade.value, "❓"
        )
        lines.append(
            "║"
            + f"  Session: {self.session_id[:30]}".ljust(w)
            + "║"
        )
        lines.append(
            "║"
            + f"  Events: {self.total_events}  │  Tokens: {self.total_tokens:,}".ljust(w)
            + "║"
        )
        lines.append(
            "║"
            + f"  Utilization Score: {self.utilization_score:.0f}/100  {grade_icon} Grade {self.grade.value}".ljust(w)
            + "║"
        )

        # Engine scores
        lines.append("╠" + "─" * w + "╣")
        lines.append("║" + "  ENGINE SCORES".ljust(w) + "║")
        lines.append("║" + ("  " + "─" * 50).ljust(w) + "║")

        engines = [
            ("Token Density", self.density.score),
            ("Pollution Control", self.pollution_score),
            ("Working Memory", self.working_memory_score),
            ("Prompt Overhead", self.overhead_score),
            ("Tool Output", self.tool_output_score),
            ("Window Pressure", self.pressure_score),
            ("Retrieval Efficiency", self.retrieval_score),
        ]
        for name, score in engines:
            bar_len = int(score / 100 * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(
                "║"
                + f"  {name:<22} {bar} {score:5.1f}".ljust(w)
                + "║"
            )

        # Key metrics
        lines.append("╠" + "─" * w + "╣")
        lines.append("║" + "  KEY METRICS".ljust(w) + "║")
        lines.append("║" + ("  " + "─" * 50).ljust(w) + "║")
        lines.append(
            "║"
            + f"  Info Density: {self.density.density_ratio:.1%}  │  Filler: {self.density.filler_pct:.1%}".ljust(w)
            + "║"
        )
        lines.append(
            "║"
            + f"  Unique Concepts: {self.density.unique_concept_count}  │  Pollution Events: {len(self.pollution_events)}".ljust(w)
            + "║"
        )
        lines.append(
            "║"
            + f"  Prompt Overhead: {self.overhead_pct:.1%}  │  Verbose Tool Outputs: {self.tool_output_verbose_count}".ljust(w)
            + "║"
        )
        lines.append(
            "║"
            + f"  Redundant Fetches: {len(self.redundant_fetches)}  │  Peak Pressure: {max((p.usage_pct for p in self.pressure_points), default=0):.1%}".ljust(w)
            + "║"
        )

        # Insights
        if self.insights:
            lines.append("╠" + "─" * w + "╣")
            lines.append("║" + "  INSIGHTS & RECOMMENDATIONS".ljust(w) + "║")
            lines.append("║" + ("  " + "─" * 50).ljust(w) + "║")
            sev_icon = {
                InsightSeverity.CRITICAL: "🔴",
                InsightSeverity.WARNING: "🟡",
                InsightSeverity.INFO: "ℹ️",
            }
            for i, ins in enumerate(self.insights[:10], 1):
                icon = sev_icon.get(ins.severity, "•")
                lines.append(
                    "║"
                    + f"  {icon} {ins.description[:50]}".ljust(w)
                    + "║"
                )
                lines.append(
                    "║"
                    + f"     → {ins.recommendation[:46]}".ljust(w)
                    + "║"
                )
                if ins.estimated_savings_pct > 0:
                    lines.append(
                        "║"
                        + f"       Est. savings: {ins.estimated_savings_pct:.0%}".ljust(w)
                        + "║"
                    )

        lines.append("╚" + "═" * w + "╝")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable dict."""
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "total_tokens": self.total_tokens,
            "utilization_score": round(self.utilization_score, 1),
            "grade": self.grade.value,
            "engines": {
                "token_density": {
                    "score": round(self.density.score, 1),
                    "density_ratio": round(self.density.density_ratio, 4),
                    "filler_pct": round(self.density.filler_pct, 4),
                    "unique_concepts": self.density.unique_concept_count,
                    "per_window_density": [round(d, 4) for d in self.density.per_window_density],
                },
                "pollution_control": {
                    "score": round(self.pollution_score, 1),
                    "events": [
                        {
                            "type": p.pollution_type.value,
                            "event_index": p.event_index,
                            "severity": p.severity.value,
                            "waste_tokens": p.waste_tokens,
                            "description": p.description,
                        }
                        for p in self.pollution_events
                    ],
                },
                "working_memory": {
                    "score": round(self.working_memory_score, 1),
                    "snapshots": [
                        {
                            "event_index": s.event_index,
                            "active_tokens": s.active_tokens,
                            "dead_weight_tokens": s.dead_weight_tokens,
                            "efficiency_ratio": round(s.efficiency_ratio, 4),
                        }
                        for s in self.working_memory_snapshots
                    ],
                },
                "prompt_overhead": {
                    "score": round(self.overhead_score, 1),
                    "overhead_pct": round(self.overhead_pct, 4),
                },
                "tool_output": {
                    "score": round(self.tool_output_score, 1),
                    "verbose_count": self.tool_output_verbose_count,
                    "total_waste_tokens": self.tool_output_total_waste,
                },
                "window_pressure": {
                    "score": round(self.pressure_score, 1),
                    "pressure_points": [
                        {
                            "event_index": p.event_index,
                            "cumulative_tokens": p.cumulative_tokens,
                            "usage_pct": round(p.usage_pct, 4),
                            "projected_exhaustion_events": p.projected_exhaustion_events,
                        }
                        for p in self.pressure_points
                    ],
                },
                "retrieval_efficiency": {
                    "score": round(self.retrieval_score, 1),
                    "redundant_fetches": [
                        {
                            "original_index": r.original_index,
                            "refetch_index": r.refetch_index,
                            "info_key": r.info_key,
                            "waste_tokens": r.waste_tokens,
                        }
                        for r in self.redundant_fetches
                    ],
                },
            },
            "insights": [
                {
                    "category": ins.category.value,
                    "severity": ins.severity.value,
                    "description": ins.description,
                    "recommendation": ins.recommendation,
                    "estimated_savings_pct": round(ins.estimated_savings_pct, 4),
                }
                for ins in self.insights
            ],
        }


# ── Analyzer ────────────────────────────────────────────────────────


class ContextUtilizationAnalyzer:
    """Autonomous context utilization analysis engine."""

    def __init__(self, config: Optional[ContextUtilizationConfig] = None, **kwargs: Any):
        self.config = config or ContextUtilizationConfig(**kwargs)

    # ── Public API ──────────────────────────────────────────────────

    def analyze(self, session: Any) -> ContextUtilizationReport:
        """Analyze a session's context utilization."""
        events = getattr(session, "events", []) if not isinstance(session, dict) else session.get("events", [])
        session_id = (
            getattr(session, "session_id", "unknown")
            if not isinstance(session, dict)
            else session.get("session_id", "unknown")
        )

        total_tokens = self._total_tokens(events)

        # Run engines
        density = self._analyze_density(events)
        pollution_events, pollution_score = self._analyze_pollution(events)
        wm_snapshots, wm_score = self._analyze_working_memory(events)
        overhead_pct, overhead_score = self._analyze_overhead(events)
        tool_score, tool_verbose_count, tool_waste = self._analyze_tool_output(events)
        pressure_points, pressure_score = self._analyze_pressure(events)
        redundant_fetches, retrieval_score = self._analyze_retrieval(events)

        # Composite score
        cfg = self.config
        utilization_score = (
            density.score * cfg.density_weight
            + pollution_score * cfg.pollution_weight
            + wm_score * cfg.working_memory_weight
            + overhead_score * cfg.overhead_weight
            + tool_score * cfg.tool_output_weight
            + pressure_score * cfg.pressure_weight
            + retrieval_score * cfg.retrieval_weight
        )
        total_weight = (
            cfg.density_weight + cfg.pollution_weight + cfg.working_memory_weight
            + cfg.overhead_weight + cfg.tool_output_weight
            + cfg.pressure_weight + cfg.retrieval_weight
        )
        if total_weight > 0:
            utilization_score /= total_weight
        utilization_score = max(0.0, min(100.0, utilization_score))

        grade = self._score_to_grade(utilization_score)

        # Generate insights
        insights = self._generate_insights(
            density, pollution_events, pollution_score, wm_snapshots, wm_score,
            overhead_pct, overhead_score, tool_score, tool_verbose_count, tool_waste,
            pressure_points, pressure_score, redundant_fetches, retrieval_score,
        )

        return ContextUtilizationReport(
            session_id=session_id,
            total_events=len(events),
            total_tokens=total_tokens,
            density=density,
            pollution_events=pollution_events,
            pollution_score=pollution_score,
            working_memory_snapshots=wm_snapshots,
            working_memory_score=wm_score,
            overhead_pct=overhead_pct,
            overhead_score=overhead_score,
            tool_output_score=tool_score,
            tool_output_verbose_count=tool_verbose_count,
            tool_output_total_waste=tool_waste,
            pressure_points=pressure_points,
            pressure_score=pressure_score,
            redundant_fetches=redundant_fetches,
            retrieval_score=retrieval_score,
            utilization_score=utilization_score,
            grade=grade,
            insights=insights,
        )

    # ── Engine 1: Token Density ─────────────────────────────────────

    def _analyze_density(self, events: List[Any]) -> TokenDensityResult:
        """Measure information density per token."""
        if not events:
            return TokenDensityResult(
                density_ratio=0.0, filler_pct=0.0, unique_concept_count=0,
                total_tokens=0, score=100.0, per_window_density=[],
            )

        all_text = ""
        total_tok = 0
        per_window_density: List[float] = []
        ws = self.config.window_size

        for i, ev in enumerate(events):
            text = self._event_text(ev)
            all_text += " " + text
            tok = self._event_tokens(ev)
            total_tok += tok

            # Window density
            if (i + 1) % ws == 0 or i == len(events) - 1:
                window_start = max(0, i - ws + 1)
                window_text = ""
                window_tok = 0
                for j in range(window_start, i + 1):
                    window_text += " " + self._event_text(events[j])
                    window_tok += self._event_tokens(events[j])
                if window_tok > 0:
                    words = set(_RE_WORD.findall(window_text.lower()))
                    per_window_density.append(len(words) / max(window_tok, 1))
                else:
                    per_window_density.append(0.0)

        words_all = _RE_WORD.findall(all_text.lower())
        unique_concepts = set(words_all)
        total_tok = max(total_tok, 1)

        density_ratio = len(unique_concepts) / total_tok
        filler_matches = _RE_FILLER.findall(all_text)
        filler_pct = len(filler_matches) / max(len(words_all), 1)

        # Score: high density + low filler = good
        density_score = min(density_ratio * 500, 100)  # normalize
        filler_penalty = max(0, (filler_pct - 0.05) * 200)
        score = max(0.0, min(100.0, density_score * 0.7 + (100 - filler_penalty) * 0.3))

        return TokenDensityResult(
            density_ratio=density_ratio,
            filler_pct=filler_pct,
            unique_concept_count=len(unique_concepts),
            total_tokens=total_tok,
            score=score,
            per_window_density=per_window_density,
        )

    # ── Engine 2: Context Pollution ─────────────────────────────────

    def _analyze_pollution(self, events: List[Any]) -> Tuple[List[PollutionEvent], float]:
        """Detect redundant/stale/irrelevant content accumulation."""
        pollution: List[PollutionEvent] = []

        if len(events) < 2:
            return pollution, 100.0

        # Track seen content for repetition detection
        seen_texts: Dict[str, int] = {}  # text_hash -> first index
        seen_tool_calls: Dict[str, int] = {}  # tool_name+input_hash -> first index

        total_waste = 0
        total_tok = max(self._total_tokens(events), 1)

        for i, ev in enumerate(events):
            text = self._event_text(ev)
            tok = self._event_tokens(ev)

            # Check for repeated content
            text_key = self._content_fingerprint(text)
            if text_key and text_key in seen_texts and len(text) > 30:
                waste = tok
                total_waste += waste
                pollution.append(PollutionEvent(
                    pollution_type=PollutionType.REDUNDANT_CONTENT,
                    event_index=i,
                    severity=InsightSeverity.WARNING,
                    waste_tokens=waste,
                    description=f"Repeated content (first seen at event {seen_texts[text_key]})",
                ))
            elif text_key:
                seen_texts[text_key] = i

            # Check for repeated tool calls
            tool_call = self._get_tool_call(ev)
            if tool_call:
                tool_key = f"{self._get_tool_name(tool_call)}:{self._content_fingerprint(str(self._get_tool_input(tool_call)))}"
                if tool_key in seen_tool_calls:
                    waste = tok
                    total_waste += waste
                    pollution.append(PollutionEvent(
                        pollution_type=PollutionType.STALE_TOOL_OUTPUT,
                        event_index=i,
                        severity=InsightSeverity.WARNING,
                        waste_tokens=waste,
                        description=f"Repeated tool call (first at event {seen_tool_calls[tool_key]})",
                    ))
                else:
                    seen_tool_calls[tool_key] = i

            # Check for filler-heavy events
            if text and len(text) > 20:
                words = _RE_WORD.findall(text)
                fillers = _RE_FILLER.findall(text)
                if len(words) > 5 and len(fillers) / len(words) > self.config.filler_threshold:
                    waste = int(tok * len(fillers) / max(len(words), 1))
                    total_waste += waste
                    pollution.append(PollutionEvent(
                        pollution_type=PollutionType.IRRELEVANT_FILLER,
                        event_index=i,
                        severity=InsightSeverity.INFO,
                        waste_tokens=waste,
                        description=f"High filler ratio ({len(fillers)}/{len(words)} words)",
                    ))

        # Score: fewer pollution events and less waste = better
        waste_ratio = total_waste / total_tok
        event_penalty = min(len(pollution) * 5, 50)
        waste_penalty = min(waste_ratio * 200, 50)
        score = max(0.0, min(100.0, 100.0 - event_penalty - waste_penalty))

        return pollution, score

    # ── Engine 3: Working Memory Efficiency ─────────────────────────

    def _analyze_working_memory(
        self, events: List[Any]
    ) -> Tuple[List[WorkingMemorySnapshot], float]:
        """Track active vs dead-weight context."""
        snapshots: List[WorkingMemorySnapshot] = []

        if not events:
            return snapshots, 100.0

        ws = self.config.window_size
        cumulative_tokens = 0
        efficiency_ratios: List[float] = []

        for i, ev in enumerate(events):
            tok = self._event_tokens(ev)
            cumulative_tokens += tok

            # "Active" tokens: those in the recent window
            window_start = max(0, i - ws + 1)
            active_tokens = sum(self._event_tokens(events[j]) for j in range(window_start, i + 1))
            dead_weight = max(0, cumulative_tokens - active_tokens)
            ratio = active_tokens / max(cumulative_tokens, 1)

            snapshots.append(WorkingMemorySnapshot(
                event_index=i,
                active_tokens=active_tokens,
                dead_weight_tokens=dead_weight,
                efficiency_ratio=ratio,
            ))
            efficiency_ratios.append(ratio)

        # Score based on average efficiency and trend
        avg_eff = statistics.mean(efficiency_ratios) if efficiency_ratios else 1.0
        # Early events naturally have high efficiency; weight later events more
        if len(efficiency_ratios) > 3:
            later_half = efficiency_ratios[len(efficiency_ratios) // 2:]
            avg_eff = (avg_eff + statistics.mean(later_half)) / 2

        score = max(0.0, min(100.0, avg_eff * 100))

        return snapshots, score

    # ── Engine 4: Prompt Overhead ───────────────────────────────────

    def _analyze_overhead(self, events: List[Any]) -> Tuple[float, float]:
        """Measure system prompt overhead."""
        if not events:
            return 0.0, 100.0

        total_tok = max(self._total_tokens(events), 1)

        # Identify system/prompt events (first events, or events with high token_in)
        overhead_tokens = 0
        for i, ev in enumerate(events):
            ev_type = self._get_event_type(ev)
            tok_in = self._get_tokens_in(ev)

            # System prompts are typically first events or have specific types
            if ev_type in ("system", "system_prompt", "prompt"):
                overhead_tokens += tok_in + self._get_tokens_out(ev)
            elif i == 0 and tok_in > 0:
                # First event likely contains system prompt
                overhead_tokens += tok_in

        overhead_pct = overhead_tokens / total_tok

        # Score: lower overhead = better (up to threshold)
        if overhead_pct <= 0.10:
            score = 100.0
        elif overhead_pct <= self.config.overhead_warning_pct:
            score = 100.0 - (overhead_pct - 0.10) / (self.config.overhead_warning_pct - 0.10) * 40
        else:
            score = max(0.0, 60.0 - (overhead_pct - self.config.overhead_warning_pct) * 200)

        return overhead_pct, max(0.0, min(100.0, score))

    # ── Engine 5: Tool Output Compaction ────────────────────────────

    def _analyze_tool_output(self, events: List[Any]) -> Tuple[float, int, int]:
        """Detect verbose tool outputs."""
        verbose_count = 0
        total_waste = 0
        tool_events = 0

        for ev in events:
            tool_call = self._get_tool_call(ev)
            if not tool_call:
                continue
            tool_events += 1

            output = self._get_tool_output(tool_call)
            if not output:
                continue

            output_str = str(output) if not isinstance(output, str) else output
            output_len = len(output_str)

            if output_len > self.config.tool_output_verbose_threshold:
                verbose_count += 1
                # Estimate waste: assume 60% could be summarized
                waste = int(output_len * 0.6 / 4)  # rough token estimate
                total_waste += waste

        if tool_events == 0:
            return 100.0, 0, 0

        verbose_ratio = verbose_count / tool_events
        score = max(0.0, min(100.0, 100.0 - verbose_ratio * 80 - min(total_waste / 500, 20)))

        return score, verbose_count, total_waste

    # ── Engine 6: Context Window Pressure ───────────────────────────

    def _analyze_pressure(self, events: List[Any]) -> Tuple[List[PressurePoint], float]:
        """Track proximity to context limits."""
        points: List[PressurePoint] = []

        if not events:
            return points, 100.0

        limit = self.config.context_limit_tokens
        cumulative = 0
        max_pressure = 0.0
        token_rates: List[int] = []

        for i, ev in enumerate(events):
            tok = self._event_tokens(ev)
            cumulative += tok
            token_rates.append(tok)
            usage_pct = cumulative / max(limit, 1)
            max_pressure = max(max_pressure, usage_pct)

            # Project exhaustion
            projected = None
            if len(token_rates) >= 3:
                avg_rate = statistics.mean(token_rates[-5:])
                if avg_rate > 0:
                    remaining = limit - cumulative
                    projected = int(remaining / avg_rate) if remaining > 0 else 0

            points.append(PressurePoint(
                event_index=i,
                cumulative_tokens=cumulative,
                usage_pct=min(usage_pct, 1.0),
                projected_exhaustion_events=projected,
            ))

        # Score: lower peak pressure = better
        if max_pressure <= 0.5:
            score = 100.0
        elif max_pressure <= self.config.pressure_warning_pct:
            score = 100.0 - (max_pressure - 0.5) / (self.config.pressure_warning_pct - 0.5) * 30
        elif max_pressure <= 1.0:
            score = 70.0 - (max_pressure - self.config.pressure_warning_pct) / (1.0 - self.config.pressure_warning_pct) * 60
        else:
            score = max(0.0, 10.0 - (max_pressure - 1.0) * 50)

        return points, max(0.0, min(100.0, score))

    # ── Engine 7: Information Retrieval Efficiency ───────────────────

    def _analyze_retrieval(self, events: List[Any]) -> Tuple[List[RedundantFetch], float]:
        """Detect redundant information fetches."""
        fetches: List[RedundantFetch] = []

        if len(events) < 2:
            return fetches, 100.0

        # Track tool calls with their inputs
        tool_history: Dict[str, List[Tuple[int, int]]] = defaultdict(list)  # key -> [(index, tokens)]
        total_tool_events = 0

        for i, ev in enumerate(events):
            tool_call = self._get_tool_call(ev)
            if not tool_call:
                continue
            total_tool_events += 1

            tool_name = self._get_tool_name(tool_call)
            tool_input = str(self._get_tool_input(tool_call))
            key = f"{tool_name}:{self._content_fingerprint(tool_input)}"
            tok = self._event_tokens(ev)

            if key in tool_history and tool_history[key]:
                original = tool_history[key][0]
                fetches.append(RedundantFetch(
                    original_index=original[0],
                    refetch_index=i,
                    info_key=tool_name,
                    waste_tokens=tok,
                ))
            tool_history[key].append((i, tok))

        if total_tool_events == 0:
            return fetches, 100.0

        redundancy_ratio = len(fetches) / max(total_tool_events, 1)
        score = max(0.0, min(100.0, 100.0 - redundancy_ratio * 150))

        return fetches, score

    # ── Engine 8: Insight Generator ─────────────────────────────────

    def _generate_insights(
        self,
        density: TokenDensityResult,
        pollution_events: List[PollutionEvent],
        pollution_score: float,
        wm_snapshots: List[WorkingMemorySnapshot],
        wm_score: float,
        overhead_pct: float,
        overhead_score: float,
        tool_score: float,
        tool_verbose_count: int,
        tool_waste: int,
        pressure_points: List[PressurePoint],
        pressure_score: float,
        redundant_fetches: List[RedundantFetch],
        retrieval_score: float,
    ) -> List[UtilizationInsight]:
        """Synthesize cross-engine insights."""
        insights: List[UtilizationInsight] = []

        # Density insights
        if density.filler_pct > 0.10:
            insights.append(UtilizationInsight(
                category=InsightCategory.DENSITY,
                severity=InsightSeverity.WARNING,
                description=f"High filler word ratio ({density.filler_pct:.0%})",
                recommendation="Reduce hedging language and padding in prompts/responses",
                estimated_savings_pct=density.filler_pct * 0.5,
            ))

        if density.score < 50:
            insights.append(UtilizationInsight(
                category=InsightCategory.DENSITY,
                severity=InsightSeverity.CRITICAL,
                description="Low information density — many tokens carry little meaning",
                recommendation="Compress prompts and use structured data formats",
                estimated_savings_pct=0.20,
            ))

        # Pollution insights
        if len(pollution_events) > 5:
            redundant_count = sum(
                1 for p in pollution_events if p.pollution_type == PollutionType.REDUNDANT_CONTENT
            )
            if redundant_count > 3:
                insights.append(UtilizationInsight(
                    category=InsightCategory.POLLUTION,
                    severity=InsightSeverity.CRITICAL,
                    description=f"{redundant_count} instances of repeated content detected",
                    recommendation="Implement deduplication or context summarization",
                    estimated_savings_pct=0.15,
                ))

        stale_count = sum(
            1 for p in pollution_events if p.pollution_type == PollutionType.STALE_TOOL_OUTPUT
        )
        if stale_count > 2:
            insights.append(UtilizationInsight(
                category=InsightCategory.POLLUTION,
                severity=InsightSeverity.WARNING,
                description=f"{stale_count} repeated tool calls with stale outputs",
                recommendation="Cache tool results and reference previous outputs",
                estimated_savings_pct=0.10,
            ))

        # Working memory insights
        if wm_score < 40:
            insights.append(UtilizationInsight(
                category=InsightCategory.WORKING_MEMORY,
                severity=InsightSeverity.CRITICAL,
                description="Working memory efficiency is critically low",
                recommendation="Use context compression or sliding-window summarization",
                estimated_savings_pct=0.25,
            ))
        elif wm_score < 60:
            insights.append(UtilizationInsight(
                category=InsightCategory.WORKING_MEMORY,
                severity=InsightSeverity.WARNING,
                description="Significant dead-weight context accumulation",
                recommendation="Prune stale context entries periodically",
                estimated_savings_pct=0.15,
            ))

        # Overhead insights
        if overhead_pct > self.config.overhead_warning_pct:
            insights.append(UtilizationInsight(
                category=InsightCategory.OVERHEAD,
                severity=InsightSeverity.WARNING,
                description=f"Prompt overhead is {overhead_pct:.0%} of total tokens",
                recommendation="Compress system prompts or use dynamic loading",
                estimated_savings_pct=overhead_pct * 0.3,
            ))

        # Tool output insights
        if tool_verbose_count > 3:
            insights.append(UtilizationInsight(
                category=InsightCategory.TOOL_OUTPUT,
                severity=InsightSeverity.WARNING,
                description=f"{tool_verbose_count} verbose tool outputs detected",
                recommendation="Summarize or truncate long tool outputs before injection",
                estimated_savings_pct=min(tool_waste / max(1, self._total_tokens_from_density(density)) * 0.6, 0.20),
            ))

        # Pressure insights
        if pressure_points:
            max_pressure = max(p.usage_pct for p in pressure_points)
            if max_pressure > 0.90:
                insights.append(UtilizationInsight(
                    category=InsightCategory.PRESSURE,
                    severity=InsightSeverity.CRITICAL,
                    description=f"Context window reached {max_pressure:.0%} capacity",
                    recommendation="Implement proactive context pruning before exhaustion",
                    estimated_savings_pct=0.20,
                ))
            elif max_pressure > self.config.pressure_warning_pct:
                # Check for imminent exhaustion
                last_point = pressure_points[-1]
                if last_point.projected_exhaustion_events is not None and last_point.projected_exhaustion_events < 10:
                    insights.append(UtilizationInsight(
                        category=InsightCategory.PRESSURE,
                        severity=InsightSeverity.WARNING,
                        description=f"Context exhaustion projected in ~{last_point.projected_exhaustion_events} events",
                        recommendation="Begin context window management or session handoff",
                        estimated_savings_pct=0.15,
                    ))

        # Retrieval insights
        if len(redundant_fetches) > 2:
            insights.append(UtilizationInsight(
                category=InsightCategory.REDUNDANT_FETCH,
                severity=InsightSeverity.WARNING,
                description=f"{len(redundant_fetches)} redundant information fetches detected",
                recommendation="Implement result caching or reference previous tool outputs",
                estimated_savings_pct=0.10,
            ))

        # Cross-engine synthesis
        low_engines = sum(1 for s in [
            density.score, pollution_score, wm_score, overhead_score,
            tool_score, pressure_score, retrieval_score,
        ] if s < 50)
        if low_engines >= 3:
            insights.append(UtilizationInsight(
                category=InsightCategory.GENERAL,
                severity=InsightSeverity.CRITICAL,
                description=f"{low_engines} engines report poor utilization — systemic waste",
                recommendation="Conduct a full context management architecture review",
                estimated_savings_pct=0.30,
            ))

        # Sort by severity (critical first)
        severity_order = {InsightSeverity.CRITICAL: 0, InsightSeverity.WARNING: 1, InsightSeverity.INFO: 2}
        insights.sort(key=lambda x: severity_order.get(x.severity, 99))

        return insights

    # ── Helper Methods ──────────────────────────────────────────────

    def _total_tokens_from_density(self, density: TokenDensityResult) -> int:
        return density.total_tokens

    @staticmethod
    def _score_to_grade(score: float) -> EfficiencyGrade:
        if score >= 90:
            return EfficiencyGrade.A
        if score >= 75:
            return EfficiencyGrade.B
        if score >= 60:
            return EfficiencyGrade.C
        if score >= 40:
            return EfficiencyGrade.D
        return EfficiencyGrade.F

    @staticmethod
    def _event_text(ev: Any) -> str:
        """Extract text content from an event."""
        if isinstance(ev, dict):
            for key in ("content", "text", "message", "output"):
                val = ev.get(key)
                if val and isinstance(val, str):
                    return val
            # Check tool output
            tc = ev.get("tool_call")
            if tc:
                out = tc.get("tool_output") if isinstance(tc, dict) else getattr(tc, "tool_output", None)
                if out and isinstance(out, str):
                    return out
                if out and isinstance(out, dict):
                    return json.dumps(out)
            return ""
        # Object-style event
        for attr in ("content", "text", "message", "output"):
            val = getattr(ev, attr, None)
            if val and isinstance(val, str):
                return val
        tc = getattr(ev, "tool_call", None)
        if tc:
            out = getattr(tc, "tool_output", None)
            if out and isinstance(out, str):
                return out
            if out and isinstance(out, dict):
                return json.dumps(out)
        return ""

    @staticmethod
    def _event_tokens(ev: Any) -> int:
        """Get total tokens for an event."""
        if isinstance(ev, dict):
            return (ev.get("tokens_in", 0) or 0) + (ev.get("tokens_out", 0) or 0)
        return (getattr(ev, "tokens_in", 0) or 0) + (getattr(ev, "tokens_out", 0) or 0)

    @staticmethod
    def _get_event_type(ev: Any) -> str:
        if isinstance(ev, dict):
            return ev.get("event_type", "") or ""
        return getattr(ev, "event_type", "") or ""

    @staticmethod
    def _get_tokens_in(ev: Any) -> int:
        if isinstance(ev, dict):
            return ev.get("tokens_in", 0) or 0
        return getattr(ev, "tokens_in", 0) or 0

    @staticmethod
    def _get_tokens_out(ev: Any) -> int:
        if isinstance(ev, dict):
            return ev.get("tokens_out", 0) or 0
        return getattr(ev, "tokens_out", 0) or 0

    @staticmethod
    def _get_tool_call(ev: Any) -> Any:
        if isinstance(ev, dict):
            return ev.get("tool_call")
        return getattr(ev, "tool_call", None)

    @staticmethod
    def _get_tool_name(tc: Any) -> str:
        if isinstance(tc, dict):
            return tc.get("tool_name", "") or ""
        return getattr(tc, "tool_name", "") or ""

    @staticmethod
    def _get_tool_input(tc: Any) -> Any:
        if isinstance(tc, dict):
            return tc.get("tool_input", "")
        return getattr(tc, "tool_input", "")

    @staticmethod
    def _get_tool_output(tc: Any) -> Any:
        if isinstance(tc, dict):
            return tc.get("tool_output")
        return getattr(tc, "tool_output", None)

    def _total_tokens(self, events: List[Any]) -> int:
        return sum(self._event_tokens(ev) for ev in events)

    @staticmethod
    def _content_fingerprint(text: str) -> str:
        """Simple content fingerprint for dedup."""
        if not text or len(text) < 10:
            return ""
        # Normalize whitespace and lowercase
        normalized = re.sub(r'\s+', ' ', text.strip().lower())
        # Take first 100 chars as fingerprint
        return normalized[:100]
