"""Agent Cognitive Bias Detector for AgentLens.

Autonomously detects systematic reasoning biases in agent sessions by analyzing
decision patterns, tool usage, and behavioral signals across the event timeline.

Answers: "Is my agent reasoning objectively? What biases affect its decisions?"

Detects 8 cognitive bias categories:
  1. Anchoring Bias — over-reliance on initial context/instructions
  2. Confirmation Bias — seeking confirming evidence, ignoring contradictions
  3. Recency Bias — overweighting latest inputs over historical patterns
  4. Sunk Cost Bias — persisting with failing strategies despite repeated failures
  5. Availability Bias — favoring recently-used tools over optimal ones
  6. Automation Bias — over-trusting tool outputs without verification
  7. Bandwagon Bias — mimicking other agents without independent reasoning
  8. Dunning-Kruger Bias — confidence-competence mismatch

Usage::

    from agentlens.cognitive_bias import CognitiveBiasDetector

    detector = CognitiveBiasDetector()
    report = detector.analyze(session)
    print(report.format_report())
    print(f"Objectivity score: {report.objectivity_score}/100")
    print(f"Grade: {report.grade}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, json, collections, re).
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class BiasCategory(Enum):
    """Types of cognitive biases detected."""
    ANCHORING = "anchoring"
    CONFIRMATION = "confirmation"
    RECENCY = "recency"
    SUNK_COST = "sunk_cost"
    AVAILABILITY = "availability"
    AUTOMATION = "automation"
    BANDWAGON = "bandwagon"
    DUNNING_KRUGER = "dunning_kruger"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class BiasSeverity(Enum):
    """Severity of detected bias."""
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"

    @property
    def weight(self) -> float:
        return {"none": 0.0, "mild": 0.15, "moderate": 0.35,
                "severe": 0.6, "critical": 1.0}[self.value]


class BiasGrade(Enum):
    """Overall objectivity grade."""
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class TrendDirection(Enum):
    """Trend of bias over session."""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class BiasSignal:
    """A single detected bias instance."""
    category: BiasCategory
    event_index: int
    confidence: float  # 0-1
    severity: BiasSeverity
    description: str = ""
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "event_index": self.event_index,
            "confidence": round(self.confidence, 3),
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass
class BiasProfile:
    """Aggregated profile for a single bias category."""
    category: BiasCategory
    signal_count: int
    avg_confidence: float
    severity: BiasSeverity
    trend: TrendDirection

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "signal_count": self.signal_count,
            "avg_confidence": round(self.avg_confidence, 3),
            "severity": self.severity.value,
            "trend": self.trend.value,
        }


@dataclass
class CognitiveBiasReport:
    """Complete cognitive bias analysis report."""
    session_id: str
    total_events: int
    bias_signals_detected: int
    objectivity_score: float  # 0-100
    dominant_bias: Optional[BiasCategory]
    bias_profiles: List[BiasProfile] = field(default_factory=list)
    signal_timeline: List[BiasSignal] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    grade: str = "A"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "bias_signals_detected": self.bias_signals_detected,
            "objectivity_score": round(self.objectivity_score, 1),
            "dominant_bias": self.dominant_bias.value if self.dominant_bias else None,
            "bias_profiles": [p.to_dict() for p in self.bias_profiles],
            "signal_timeline": [s.to_dict() for s in self.signal_timeline],
            "recommendations": self.recommendations,
            "grade": self.grade,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_report(self) -> str:
        """Format a human-readable text report."""
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  AGENT COGNITIVE BIAS ANALYSIS")
        lines.append("=" * 60)
        lines.append(f"  Session: {self.session_id}")
        lines.append(f"  Events analyzed: {self.total_events}")
        lines.append("")

        # Grade banner
        grade_emoji = {"A": "🧠", "B": "✅", "C": "⚠️", "D": "🔻", "F": "❌"}.get(self.grade, "")
        lines.append(f"  Grade: {grade_emoji} {self.grade}")
        lines.append(f"  Objectivity Score: {self.objectivity_score:.0f}/100")
        lines.append(f"  Biases detected: {self.bias_signals_detected}")
        if self.dominant_bias:
            lines.append(f"  Dominant bias: {self.dominant_bias.label}")
        lines.append("")

        # Bias profiles
        if self.bias_profiles:
            lines.append("-" * 60)
            lines.append("  BIAS PROFILES")
            lines.append("-" * 60)
            for p in sorted(self.bias_profiles, key=lambda x: -x.signal_count):
                bar = "█" * min(p.signal_count * 2, 20)
                trend_icon = {"increasing": "↑", "decreasing": "↓", "stable": "→"}[p.trend.value]
                lines.append(f"  {p.category.label:<20} {p.signal_count:>3} [{p.severity.value:<8}] {trend_icon} {bar}")
            lines.append("")

        # Signal timeline (top 10)
        if self.signal_timeline:
            lines.append("-" * 60)
            lines.append("  SIGNAL TIMELINE (top 10)")
            lines.append("-" * 60)
            sorted_signals = sorted(self.signal_timeline, key=lambda x: -x.confidence)
            for s in sorted_signals[:10]:
                lines.append(f"  [{s.category.value}] event {s.event_index} "
                             f"(conf: {s.confidence:.0%}, {s.severity.value})")
                if s.description:
                    lines.append(f"    {s.description}")
            lines.append("")

        # Recommendations
        if self.recommendations:
            lines.append("-" * 60)
            lines.append("  RECOMMENDATIONS")
            lines.append("-" * 60)
            for r in self.recommendations:
                lines.append(f"  • {r}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)


# ── Detector ────────────────────────────────────────────────────────


class CognitiveBiasDetector:
    """Autonomous cognitive bias detector for agent sessions.

    Analyzes session event streams to identify systematic reasoning biases
    that may impair agent decision quality.
    """

    def __init__(self, min_confidence: float = 0.5, window_size: int = 10):
        self.min_confidence = min_confidence
        self.window_size = window_size

    def analyze(self, session: Dict[str, Any]) -> CognitiveBiasReport:
        """Analyze a session for cognitive biases.

        Args:
            session: Dict with 'id' (or 'session_id') and 'events' list.

        Returns:
            CognitiveBiasReport with all detected biases.
        """
        session_id = session.get("id") or session.get("session_id", "unknown")
        events = session.get("events", [])
        total_events = len(events)

        if total_events < 3:
            return CognitiveBiasReport(
                session_id=session_id,
                total_events=total_events,
                bias_signals_detected=0,
                objectivity_score=100.0,
                dominant_bias=None,
                grade="A",
                recommendations=["Insufficient data for bias analysis (need 3+ events)."],
            )

        # Run all detectors
        all_signals: List[BiasSignal] = []
        all_signals.extend(self._detect_anchoring(events))
        all_signals.extend(self._detect_confirmation(events))
        all_signals.extend(self._detect_recency(events))
        all_signals.extend(self._detect_sunk_cost(events))
        all_signals.extend(self._detect_availability(events))
        all_signals.extend(self._detect_automation(events))
        all_signals.extend(self._detect_bandwagon(events))
        all_signals.extend(self._detect_dunning_kruger(events))

        # Filter by confidence
        filtered_signals = [s for s in all_signals if s.confidence >= self.min_confidence]

        # Build profiles
        profiles = self._build_profiles(filtered_signals, total_events)

        # Score
        objectivity_score = self._compute_objectivity_score(profiles)
        grade = self._compute_grade(objectivity_score)

        # Dominant bias
        dominant = None
        if profiles:
            top = max(profiles, key=lambda p: p.signal_count * p.avg_confidence)
            if top.signal_count > 0:
                dominant = top.category

        # Recommendations
        recommendations = self._generate_recommendations(profiles, dominant)

        return CognitiveBiasReport(
            session_id=session_id,
            total_events=total_events,
            bias_signals_detected=len(filtered_signals),
            objectivity_score=objectivity_score,
            dominant_bias=dominant,
            bias_profiles=profiles,
            signal_timeline=filtered_signals,
            recommendations=recommendations,
            grade=grade,
        )

    # ── Individual Detectors ────────────────────────────────────────

    def _detect_anchoring(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect anchoring bias: over-reliance on initial context."""
        signals: List[BiasSignal] = []
        n = len(events)
        if n < 5:
            return signals

        # Split into first 20% and rest
        split = max(2, n // 5)
        early_tools = self._get_tool_names(events[:split])
        if not early_tools:
            return signals

        early_set = set(early_tools)
        # Check if later decisions are dominated by early patterns
        later_events = events[split:]
        later_tools = self._get_tool_names(later_events)

        if not later_tools:
            return signals

        # Measure overlap ratio
        later_counter = Counter(later_tools)
        total_later = len(later_tools)
        early_dominated_count = sum(later_counter[t] for t in early_set)
        ratio = early_dominated_count / total_later if total_later > 0 else 0

        # If > 80% of later tool usage matches early tools, that's anchoring
        if ratio > 0.8 and len(early_set) <= 3 and total_later >= 3:
            confidence = min(1.0, (ratio - 0.7) * 3.0)
            severity = self._ratio_to_severity(ratio, 0.8, 0.85, 0.9, 0.95)
            signals.append(BiasSignal(
                category=BiasCategory.ANCHORING,
                event_index=split,
                confidence=confidence,
                severity=severity,
                description=f"Tool usage in later {100 - split * 100 // n}% of session mirrors early pattern ({ratio:.0%} overlap)",
                evidence=f"Early tools: {list(early_set)}, later usage ratio: {ratio:.2f}",
            ))

        # Also check: if agent keeps referencing early context despite new info
        early_content = self._get_content_keywords(events[:split])
        if early_content:
            for i, event in enumerate(later_events):
                content = self._get_content(event)
                if content and self._keyword_overlap(early_content, content) > 0.6:
                    meta = event.get("metadata", {})
                    if meta.get("type") == "decision" or event.get("type") == "decision":
                        confidence = 0.65
                        signals.append(BiasSignal(
                            category=BiasCategory.ANCHORING,
                            event_index=split + i,
                            confidence=confidence,
                            severity=BiasSeverity.MILD,
                            description="Decision heavily references initial context despite new information",
                            evidence=f"High keyword overlap with initial context at event {split + i}",
                        ))

        return signals

    def _detect_confirmation(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect confirmation bias: seeking only confirming evidence."""
        signals: List[BiasSignal] = []
        n = len(events)

        # Look for repeated similar tool calls that return similar results
        tool_calls = [(i, e) for i, e in enumerate(events)
                      if e.get("type") == "tool_call"]

        if len(tool_calls) < 3:
            return signals

        # Group by tool name
        tool_groups: Dict[str, List[Tuple[int, Dict]]] = defaultdict(list)
        for idx, evt in tool_calls:
            name = evt.get("metadata", {}).get("tool_name", "")
            if name:
                tool_groups[name].append((idx, evt))

        for tool_name, calls in tool_groups.items():
            if len(calls) < 3:
                continue

            # Check if repeated calls have similar inputs (suggesting seeking confirmation)
            consecutive_similar = 0
            for j in range(1, len(calls)):
                prev_meta = calls[j - 1][1].get("metadata", {})
                curr_meta = calls[j][1].get("metadata", {})
                # Similar if same parameters or similar content
                if self._similar_metadata(prev_meta, curr_meta):
                    consecutive_similar += 1

            if consecutive_similar >= 2:
                ratio = consecutive_similar / (len(calls) - 1)
                confidence = min(1.0, ratio * 0.9)
                severity = self._ratio_to_severity(ratio, 0.5, 0.65, 0.8, 0.9)
                signals.append(BiasSignal(
                    category=BiasCategory.CONFIRMATION,
                    event_index=calls[-1][0],
                    confidence=confidence,
                    severity=severity,
                    description=f"Repeated similar calls to '{tool_name}' ({consecutive_similar + 1} similar in sequence)",
                    evidence=f"Tool '{tool_name}' called {len(calls)} times, {consecutive_similar} consecutive similar",
                ))

        return signals

    def _detect_recency(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect recency bias: overweighting latest inputs."""
        signals: List[BiasSignal] = []
        n = len(events)
        if n < 6:
            return signals

        ws = min(self.window_size, n // 3)
        if ws < 2:
            return signals

        # Check if decision patterns correlate more with recent window
        decisions = [(i, e) for i, e in enumerate(events)
                     if e.get("type") in ("decision", "agent_response", "llm_call")]

        if len(decisions) < 3:
            return signals

        # For each decision after first window, check if it mirrors
        # the immediately preceding window more than earlier patterns
        for d_idx, (event_idx, decision) in enumerate(decisions):
            if event_idx < ws:
                continue

            recent_window = events[max(0, event_idx - ws):event_idx]
            earlier = events[:max(1, event_idx - ws)]

            if not earlier:
                continue

            recent_tools = set(self._get_tool_names(recent_window))
            earlier_tools = set(self._get_tool_names(earlier))
            decision_tools = self._get_tool_names([decision])

            if not decision_tools:
                continue

            # Check if decision aligns more with recent than earlier
            recent_overlap = len(set(decision_tools) & recent_tools) / max(1, len(decision_tools))
            earlier_overlap = len(set(decision_tools) & earlier_tools) / max(1, len(decision_tools))

            if recent_overlap > 0.8 and earlier_overlap < 0.3 and len(earlier_tools) >= 2:
                confidence = min(1.0, (recent_overlap - earlier_overlap) * 1.2)
                signals.append(BiasSignal(
                    category=BiasCategory.RECENCY,
                    event_index=event_idx,
                    confidence=confidence,
                    severity=BiasSeverity.MODERATE if confidence > 0.7 else BiasSeverity.MILD,
                    description="Decision pattern aligns strongly with recent window, ignoring earlier context",
                    evidence=f"Recent overlap: {recent_overlap:.2f}, earlier overlap: {earlier_overlap:.2f}",
                ))

        return signals

    def _detect_sunk_cost(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect sunk cost bias: persisting with failing strategies."""
        signals: List[BiasSignal] = []
        n = len(events)

        # Look for retry chains on same tool after failures
        tool_attempts: Dict[str, List[Tuple[int, bool]]] = defaultdict(list)

        for i, event in enumerate(events):
            if event.get("type") == "tool_call":
                meta = event.get("metadata", {})
                tool_name = meta.get("tool_name", "")
                success = meta.get("success", True)
                if tool_name:
                    tool_attempts[tool_name].append((i, success))

        for tool_name, attempts in tool_attempts.items():
            # Find consecutive failure chains
            consecutive_failures = 0
            chain_start = -1
            for idx, (event_idx, success) in enumerate(attempts):
                if not success:
                    if consecutive_failures == 0:
                        chain_start = event_idx
                    consecutive_failures += 1
                else:
                    if consecutive_failures >= 3:
                        confidence = min(1.0, consecutive_failures * 0.2)
                        severity = self._count_to_severity(consecutive_failures, 3, 4, 6, 8)
                        signals.append(BiasSignal(
                            category=BiasCategory.SUNK_COST,
                            event_index=chain_start,
                            confidence=confidence,
                            severity=severity,
                            description=f"Persisted with '{tool_name}' through {consecutive_failures} consecutive failures",
                            evidence=f"Failure chain: events {chain_start} to {event_idx} ({consecutive_failures} failures before success)",
                        ))
                    consecutive_failures = 0

            # Handle trailing failures
            if consecutive_failures >= 3:
                confidence = min(1.0, consecutive_failures * 0.2)
                severity = self._count_to_severity(consecutive_failures, 3, 4, 6, 8)
                signals.append(BiasSignal(
                    category=BiasCategory.SUNK_COST,
                    event_index=chain_start,
                    confidence=confidence,
                    severity=severity,
                    description=f"Persisted with '{tool_name}' through {consecutive_failures} consecutive failures without strategy change",
                    evidence=f"Unresolved failure chain starting at event {chain_start}",
                ))

        return signals

    def _detect_availability(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect availability bias: favoring recently-used tools."""
        signals: List[BiasSignal] = []
        n = len(events)

        tool_calls = [(i, e.get("metadata", {}).get("tool_name", ""))
                      for i, e in enumerate(events)
                      if e.get("type") == "tool_call" and e.get("metadata", {}).get("tool_name")]

        if len(tool_calls) < 5:
            return signals

        # Check if tool selection correlates with recency of last use
        unique_tools = list(set(t for _, t in tool_calls))
        if len(unique_tools) < 2:
            return signals

        # Sliding window: for each tool call, check if it was the most recently used
        consecutive_same = 0
        prev_tool = ""
        for idx, (event_idx, tool_name) in enumerate(tool_calls):
            if tool_name == prev_tool:
                consecutive_same += 1
            else:
                if consecutive_same >= 4:
                    confidence = min(1.0, consecutive_same * 0.15)
                    signals.append(BiasSignal(
                        category=BiasCategory.AVAILABILITY,
                        event_index=event_idx,
                        confidence=confidence,
                        severity=BiasSeverity.MODERATE if consecutive_same >= 6 else BiasSeverity.MILD,
                        description=f"Repeated use of '{prev_tool}' {consecutive_same + 1} times consecutively",
                        evidence=f"May indicate availability heuristic over task-optimal selection",
                    ))
                consecutive_same = 0
            prev_tool = tool_name

        # Check trailing
        if consecutive_same >= 4:
            confidence = min(1.0, consecutive_same * 0.15)
            signals.append(BiasSignal(
                category=BiasCategory.AVAILABILITY,
                event_index=tool_calls[-1][0],
                confidence=confidence,
                severity=BiasSeverity.MODERATE if consecutive_same >= 6 else BiasSeverity.MILD,
                description=f"Repeated use of '{prev_tool}' {consecutive_same + 1} times consecutively",
                evidence=f"May indicate availability heuristic over task-optimal selection",
            ))

        return signals

    def _detect_automation(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect automation bias: over-trusting tool outputs without verification."""
        signals: List[BiasSignal] = []
        n = len(events)

        for i in range(n - 1):
            event = events[i]
            if event.get("type") != "tool_call":
                continue

            meta = event.get("metadata", {})
            success = meta.get("success", True)

            # Check if next event is immediate acceptance without verification
            next_event = events[i + 1]
            next_type = next_event.get("type", "")

            # If tool returned error but agent immediately uses result
            if not success and next_type in ("agent_response", "decision", "llm_call"):
                next_content = self._get_content(next_event)
                # If agent doesn't acknowledge error in response
                if next_content and not self._mentions_error(next_content):
                    signals.append(BiasSignal(
                        category=BiasCategory.AUTOMATION,
                        event_index=i + 1,
                        confidence=0.75,
                        severity=BiasSeverity.MODERATE,
                        description="Agent proceeded without acknowledging tool failure",
                        evidence=f"Tool call at event {i} failed, but response at {i + 1} ignores error",
                    ))

            # Also: tool success followed by immediate acceptance without any
            # verification step (no follow-up check)
            if success and next_type in ("agent_response", "decision"):
                # Look ahead: is there any verification within next 2 events?
                has_verification = False
                for j in range(i + 1, min(i + 3, n)):
                    if events[j].get("type") == "tool_call":
                        verify_tool = events[j].get("metadata", {}).get("tool_name", "")
                        if "verify" in verify_tool.lower() or "check" in verify_tool.lower() or "validate" in verify_tool.lower():
                            has_verification = True
                            break

                # Only flag if tool had high-stakes content and no verification
                if not has_verification and meta.get("high_stakes"):
                    signals.append(BiasSignal(
                        category=BiasCategory.AUTOMATION,
                        event_index=i + 1,
                        confidence=0.6,
                        severity=BiasSeverity.MILD,
                        description="High-stakes tool output accepted without verification",
                        evidence=f"No verification step found after tool call at event {i}",
                    ))

        return signals

    def _detect_bandwagon(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect bandwagon bias: mimicking other agents in multi-agent sessions."""
        signals: List[BiasSignal] = []

        # Only relevant for multi-agent sessions
        agent_ids = set()
        for event in events:
            aid = event.get("agent_id")
            if aid:
                agent_ids.add(aid)

        if len(agent_ids) < 2:
            return signals

        # Group events by agent
        agent_events: Dict[str, List[Tuple[int, Dict]]] = defaultdict(list)
        for i, event in enumerate(events):
            aid = event.get("agent_id", "unknown")
            agent_events[aid].append((i, event))

        # For each agent, check if their patterns closely follow another agent's
        for agent_a in agent_ids:
            for agent_b in agent_ids:
                if agent_a == agent_b:
                    continue

                tools_a = [e.get("metadata", {}).get("tool_name", "")
                           for _, e in agent_events[agent_a]
                           if e.get("type") == "tool_call"]
                tools_b = [e.get("metadata", {}).get("tool_name", "")
                           for _, e in agent_events[agent_b]
                           if e.get("type") == "tool_call"]

                if len(tools_a) < 3 or len(tools_b) < 3:
                    continue

                # Check sequence similarity
                overlap = self._sequence_similarity(tools_a, tools_b)
                if overlap > 0.7:
                    last_idx = agent_events[agent_a][-1][0] if agent_events[agent_a] else 0
                    confidence = min(1.0, overlap)
                    signals.append(BiasSignal(
                        category=BiasCategory.BANDWAGON,
                        event_index=last_idx,
                        confidence=confidence,
                        severity=BiasSeverity.MODERATE if overlap > 0.85 else BiasSeverity.MILD,
                        description=f"Agent '{agent_a}' tool pattern closely mirrors agent '{agent_b}' ({overlap:.0%} similarity)",
                        evidence=f"Possible mimicry rather than independent reasoning",
                    ))

        return signals

    def _detect_dunning_kruger(self, events: List[Dict]) -> List[BiasSignal]:
        """Detect Dunning-Kruger bias: confidence-competence mismatch."""
        signals: List[BiasSignal] = []
        n = len(events)

        # Look for high-confidence expressions followed by failures
        for i in range(n - 1):
            event = events[i]
            meta = event.get("metadata", {})
            content = self._get_content(event)

            # Check for confidence indicators
            confidence_level = meta.get("confidence", 0)
            has_high_confidence = confidence_level > 0.8

            if not has_high_confidence and content:
                # Check content for confidence language
                has_high_confidence = self._has_confidence_language(content)

            if not has_high_confidence:
                continue

            # Look ahead for failures within next 3 events
            for j in range(i + 1, min(i + 4, n)):
                next_event = events[j]
                next_type = next_event.get("type", "")
                next_meta = next_event.get("metadata", {})

                is_failure = (
                    next_type == "error" or
                    next_meta.get("success") is False or
                    next_type == "tool_call" and next_meta.get("success") is False
                )

                if is_failure:
                    signals.append(BiasSignal(
                        category=BiasCategory.DUNNING_KRUGER,
                        event_index=i,
                        confidence=0.7,
                        severity=BiasSeverity.MODERATE,
                        description="High confidence expression followed by failure",
                        evidence=f"Confident statement at event {i}, failure at event {j}",
                    ))
                    break

        return signals

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_tool_names(self, events: List[Dict]) -> List[str]:
        """Extract tool names from events."""
        names = []
        for e in events:
            if e.get("type") == "tool_call":
                name = e.get("metadata", {}).get("tool_name", "")
                if name:
                    names.append(name)
        return names

    def _get_content(self, event: Dict) -> str:
        """Extract text content from an event."""
        content = event.get("metadata", {}).get("content", "")
        if not content:
            content = event.get("content", "")
        return str(content) if content else ""

    def _get_content_keywords(self, events: List[Dict]) -> set:
        """Extract significant keywords from events."""
        words: set = set()
        for e in events:
            content = self._get_content(e)
            if content:
                tokens = re.findall(r'\b[a-zA-Z]{4,}\b', content.lower())
                words.update(tokens[:20])  # Cap per event
        return words

    def _keyword_overlap(self, keywords: set, content: str) -> float:
        """Measure keyword overlap ratio."""
        if not keywords:
            return 0.0
        content_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', content.lower()))
        if not content_words:
            return 0.0
        overlap = len(keywords & content_words)
        return overlap / min(len(keywords), len(content_words))

    def _similar_metadata(self, meta_a: Dict, meta_b: Dict) -> bool:
        """Check if two metadata dicts are similar (beyond just tool name)."""
        # Compare substantive fields (not just tool_name)
        for key in ("parameters", "query", "input"):
            val_a = meta_a.get(key)
            val_b = meta_b.get(key)
            if val_a and val_b and val_a == val_b:
                return True
        # If no substantive fields to compare, check content similarity
        content_a = meta_a.get("content", "")
        content_b = meta_b.get("content", "")
        if content_a and content_b and content_a == content_b:
            return True
        return False

    def _mentions_error(self, content: str) -> bool:
        """Check if content acknowledges an error."""
        error_patterns = [
            r'\berror\b', r'\bfail', r'\bsorry\b', r'\bcannot\b',
            r'\bunable\b', r'\bproblem\b', r'\bissue\b', r'\bretry\b',
        ]
        lower = content.lower()
        return any(re.search(p, lower) for p in error_patterns)

    def _has_confidence_language(self, content: str) -> bool:
        """Check if content contains high-confidence language."""
        patterns = [
            r'\bcertainly\b', r'\bdefinitely\b', r'\babsolutely\b',
            r'\bguarantee\b', r'\bno doubt\b', r'\bsurely\b',
            r'\b100%\b', r'\bperfect\b', r'\bconfident\b',
            r'\bwithout question\b', r'\bclearly\b',
        ]
        lower = content.lower()
        return any(re.search(p, lower) for p in patterns)

    def _sequence_similarity(self, seq_a: List[str], seq_b: List[str]) -> float:
        """Compute Jaccard similarity between two sequences."""
        if not seq_a or not seq_b:
            return 0.0
        set_a = set(seq_a)
        set_b = set(seq_b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _ratio_to_severity(self, ratio: float, mild: float, moderate: float,
                           severe: float, critical: float) -> BiasSeverity:
        """Convert a ratio to severity based on thresholds."""
        if ratio >= critical:
            return BiasSeverity.CRITICAL
        elif ratio >= severe:
            return BiasSeverity.SEVERE
        elif ratio >= moderate:
            return BiasSeverity.MODERATE
        elif ratio >= mild:
            return BiasSeverity.MILD
        return BiasSeverity.NONE

    def _count_to_severity(self, count: int, mild: int, moderate: int,
                           severe: int, critical: int) -> BiasSeverity:
        """Convert a count to severity based on thresholds."""
        if count >= critical:
            return BiasSeverity.CRITICAL
        elif count >= severe:
            return BiasSeverity.SEVERE
        elif count >= moderate:
            return BiasSeverity.MODERATE
        elif count >= mild:
            return BiasSeverity.MILD
        return BiasSeverity.NONE

    # ── Profile & Scoring ───────────────────────────────────────────

    def _build_profiles(self, signals: List[BiasSignal], total_events: int) -> List[BiasProfile]:
        """Build aggregated profiles from signals."""
        by_category: Dict[BiasCategory, List[BiasSignal]] = defaultdict(list)
        for s in signals:
            by_category[s.category].append(s)

        profiles: List[BiasProfile] = []
        for category in BiasCategory:
            cat_signals = by_category.get(category, [])
            if not cat_signals:
                continue

            avg_conf = statistics.mean(s.confidence for s in cat_signals)
            # Determine overall severity for this category
            severities = [s.severity for s in cat_signals]
            severity = max(severities, key=lambda s: s.weight)

            # Determine trend (first half vs second half of signals by event_index)
            trend = self._compute_trend(cat_signals)

            profiles.append(BiasProfile(
                category=category,
                signal_count=len(cat_signals),
                avg_confidence=avg_conf,
                severity=severity,
                trend=trend,
            ))

        return profiles

    def _compute_trend(self, signals: List[BiasSignal]) -> TrendDirection:
        """Compute trend direction from signal positions."""
        if len(signals) < 2:
            return TrendDirection.STABLE

        indices = sorted(s.event_index for s in signals)
        mid = len(indices) // 2
        first_half = indices[:mid]
        second_half = indices[mid:]

        if not first_half or not second_half:
            return TrendDirection.STABLE

        # Compare density (signals per event span)
        first_span = max(1, first_half[-1] - first_half[0] + 1)
        second_span = max(1, second_half[-1] - second_half[0] + 1)

        first_density = len(first_half) / first_span
        second_density = len(second_half) / second_span

        if second_density > first_density * 1.3:
            return TrendDirection.INCREASING
        elif first_density > second_density * 1.3:
            return TrendDirection.DECREASING
        return TrendDirection.STABLE

    def _compute_objectivity_score(self, profiles: List[BiasProfile]) -> float:
        """Compute objectivity score 0-100 from profiles."""
        if not profiles:
            return 100.0

        # Weighted penalty based on severity and count
        total_penalty = 0.0
        for p in profiles:
            penalty = p.severity.weight * p.signal_count * p.avg_confidence * 15.0
            total_penalty += penalty

        score = max(0.0, 100.0 - total_penalty)
        return round(score, 1)

    def _compute_grade(self, score: float) -> str:
        """Convert objectivity score to letter grade."""
        if score >= 90:
            return "A"
        elif score >= 75:
            return "B"
        elif score >= 60:
            return "C"
        elif score >= 45:
            return "D"
        return "F"

    def _generate_recommendations(self, profiles: List[BiasProfile],
                                  dominant: Optional[BiasCategory]) -> List[str]:
        """Generate actionable recommendations based on detected biases."""
        recs: List[str] = []

        bias_advice = {
            BiasCategory.ANCHORING: "Introduce periodic context refresh points to reduce anchoring on initial instructions",
            BiasCategory.CONFIRMATION: "Add explicit contradiction-seeking steps; query for disconfirming evidence",
            BiasCategory.RECENCY: "Implement weighted context windows that balance recent and historical signals",
            BiasCategory.SUNK_COST: "Set explicit failure thresholds (e.g., 3 retries max) before mandatory strategy pivot",
            BiasCategory.AVAILABILITY: "Rotate tool selection based on task analysis rather than usage recency",
            BiasCategory.AUTOMATION: "Add verification steps after tool calls, especially for high-stakes operations",
            BiasCategory.BANDWAGON: "Encourage independent reasoning; add diversity incentives in multi-agent orchestration",
            BiasCategory.DUNNING_KRUGER: "Calibrate confidence expressions against actual success rates",
        }

        for p in sorted(profiles, key=lambda x: -x.signal_count * x.avg_confidence):
            if p.category in bias_advice:
                recs.append(bias_advice[p.category])

        if not recs:
            recs.append("No significant biases detected. Agent reasoning appears objective.")

        return recs
