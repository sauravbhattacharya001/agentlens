"""Agent Self-Correction Tracker for AgentLens.

Autonomously detects and analyzes when AI agents catch and correct their own
mistakes within sessions — measuring correction quality, latency, and patterns.

Answers: "How well does my agent self-correct? What patterns trigger corrections?
Are corrections effective?"

Detects 8 correction categories:
  1. Retry Correction — retried tool calls after failures with modified parameters
  2. Apology Correction — explicit acknowledgment/correction phrases
  3. Backtrack Correction — revisiting earlier approach after dead-end
  4. Error Recovery — successful operations following error events
  5. Strategy Pivot — switching tool/approach mid-task after poor results
  6. Output Revision — regenerated/revised outputs replacing earlier attempts
  7. Assumption Correction — explicitly correcting wrong assumptions
  8. Hallucination Fix — retracting/correcting factual claims after verification

Usage::

    from agentlens.self_correction import SelfCorrectionTracker

    tracker = SelfCorrectionTracker()
    report = tracker.analyze(session)
    print(report.format_report())
    print(f"Self-awareness score: {report.self_awareness_score}/100")
    print(f"Grade: {report.grade}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, json, collections).
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class CorrectionCategory(Enum):
    """Types of self-correction patterns."""
    RETRY_CORRECTION = "retry_correction"
    APOLOGY_CORRECTION = "apology_correction"
    BACKTRACK_CORRECTION = "backtrack_correction"
    ERROR_RECOVERY = "error_recovery"
    STRATEGY_PIVOT = "strategy_pivot"
    OUTPUT_REVISION = "output_revision"
    ASSUMPTION_CORRECTION = "assumption_correction"
    HALLUCINATION_FIX = "hallucination_fix"


class Grade(Enum):
    """Self-correction grade."""
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class CorrectionEvent:
    """A single detected self-correction instance."""
    category: CorrectionCategory
    trigger_event_index: int
    correction_event_index: int
    latency_events: int
    effectiveness: float  # 0-1
    confidence: float  # 0-1
    description: str = ""
    trigger_summary: str = ""
    correction_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "trigger_event_index": self.trigger_event_index,
            "correction_event_index": self.correction_event_index,
            "latency_events": self.latency_events,
            "effectiveness": round(self.effectiveness, 3),
            "confidence": round(self.confidence, 3),
            "description": self.description,
            "trigger_summary": self.trigger_summary,
            "correction_summary": self.correction_summary,
        }


@dataclass
class CorrectionPattern:
    """A meta-pattern detected across corrections."""
    name: str
    description: str
    evidence_count: int
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "evidence_count": self.evidence_count,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class SelfCorrectionReport:
    """Complete self-correction analysis report."""
    session_id: str
    total_events: int
    correction_count: int
    correction_rate: float  # per 100 events
    mean_correction_latency: float
    effectiveness_score: float  # 0-100
    self_awareness_score: float  # 0-100
    category_breakdown: Dict[str, int] = field(default_factory=dict)
    correction_timeline: List[CorrectionEvent] = field(default_factory=list)
    patterns: List[CorrectionPattern] = field(default_factory=list)
    grade: str = "F"
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "correction_count": self.correction_count,
            "correction_rate": round(self.correction_rate, 2),
            "mean_correction_latency": round(self.mean_correction_latency, 2),
            "effectiveness_score": round(self.effectiveness_score, 1),
            "self_awareness_score": round(self.self_awareness_score, 1),
            "category_breakdown": self.category_breakdown,
            "correction_timeline": [c.to_dict() for c in self.correction_timeline],
            "patterns": [p.to_dict() for p in self.patterns],
            "grade": self.grade,
            "recommendations": self.recommendations,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_report(self) -> str:
        """Format a human-readable text report."""
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  AGENT SELF-CORRECTION ANALYSIS")
        lines.append("=" * 60)
        lines.append(f"  Session: {self.session_id}")
        lines.append(f"  Events analyzed: {self.total_events}")
        lines.append("")

        # Grade banner
        grade_emoji = {"A": "🏆", "B": "✅", "C": "⚠️", "D": "🔻", "F": "❌"}.get(self.grade, "")
        lines.append(f"  Grade: {grade_emoji} {self.grade}")
        lines.append(f"  Self-Awareness Score: {self.self_awareness_score:.0f}/100")
        lines.append(f"  Effectiveness Score: {self.effectiveness_score:.0f}/100")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  SUMMARY")
        lines.append("-" * 60)
        lines.append(f"  Corrections detected: {self.correction_count}")
        lines.append(f"  Correction rate: {self.correction_rate:.1f} per 100 events")
        lines.append(f"  Mean correction latency: {self.mean_correction_latency:.1f} events")
        lines.append("")

        # Category breakdown
        if self.category_breakdown:
            lines.append("-" * 60)
            lines.append("  CATEGORY BREAKDOWN")
            lines.append("-" * 60)
            for cat, count in sorted(self.category_breakdown.items(), key=lambda x: -x[1]):
                bar = "█" * min(count * 2, 20)
                lines.append(f"  {cat:<25} {count:>3} {bar}")
            lines.append("")

        # Patterns
        if self.patterns:
            lines.append("-" * 60)
            lines.append("  DETECTED PATTERNS")
            lines.append("-" * 60)
            for p in self.patterns:
                lines.append(f"  • {p.name} (confidence: {p.confidence:.0%})")
                lines.append(f"    {p.description}")
            lines.append("")

        # Top corrections (first 5)
        if self.correction_timeline:
            lines.append("-" * 60)
            lines.append("  CORRECTION TIMELINE (top 5)")
            lines.append("-" * 60)
            for c in self.correction_timeline[:5]:
                lines.append(f"  [{c.category.value}] event {c.trigger_event_index} → {c.correction_event_index}")
                lines.append(f"    Latency: {c.latency_events} events | Effectiveness: {c.effectiveness:.0%} | Confidence: {c.confidence:.0%}")
                if c.description:
                    lines.append(f"    {c.description}")
            lines.append("")

        # Recommendations
        if self.recommendations:
            lines.append("-" * 60)
            lines.append("  RECOMMENDATIONS")
            lines.append("-" * 60)
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"  {i}. {rec}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)


# ── Detection Patterns ──────────────────────────────────────────────

# Apology/correction phrases
_CORRECTION_PHRASES = [
    r"\bi apologize\b", r"\blet me fix\b", r"\blet me correct\b",
    r"\bactually[,.]", r"\bcorrection:", r"\bmy mistake\b",
    r"\bi was wrong\b", r"\blet me try again\b", r"\bsorry[,.].*(?:wrong|incorrect|mistake)",
    r"\bon second thought\b", r"\bwait[,.].*(?:that's not|that isn't)",
    r"\bi made an error\b", r"\blet me redo\b",
]

_ASSUMPTION_PHRASES = [
    r"\bi assumed\b.*\bbut\b", r"\bi incorrectly assumed\b",
    r"\bmy assumption.*wrong\b", r"\bthat assumption.*incorrect\b",
    r"\bi shouldn't have assumed\b", r"\bcontrary to.*assumption\b",
]

_HALLUCINATION_PHRASES = [
    r"\bthat('s| is) not (actually |)correct\b",
    r"\bi (was |)hallucinated?\b", r"\bthat.*doesn't (actually |)exist\b",
    r"\bi fabricated\b", r"\bi made that up\b",
    r"\bupon (checking|verification|review)\b",
    r"\bafter verif", r"\bi incorrectly (stated|claimed|said)\b",
]

_BACKTRACK_PHRASES = [
    r"\blet me go back\b", r"\bgoing back to\b",
    r"\breturning to.*earlier\b", r"\bthat approach.*didn't work\b",
    r"\blet me try.*different\b", r"\bscrap that\b",
    r"\bstarting over\b", r"\blet me rethink\b",
]


# ── Core Engine ─────────────────────────────────────────────────────


class SelfCorrectionTracker:
    """Autonomous self-correction detection and analysis engine."""

    def __init__(self, min_confidence: float = 0.5):
        """Initialize tracker.

        Args:
            min_confidence: Minimum confidence threshold to include a correction.
        """
        self.min_confidence = min_confidence

    def analyze(self, session: Dict[str, Any]) -> SelfCorrectionReport:
        """Analyze a session for self-correction patterns.

        Args:
            session: Session dict with 'session_id' and 'events' list.

        Returns:
            SelfCorrectionReport with full analysis.
        """
        session_id = session.get("session_id", "unknown")
        events = session.get("events", [])

        if not events:
            return self._empty_report(session_id)

        # Run all detectors
        corrections: List[CorrectionEvent] = []
        corrections.extend(self._detect_retry_corrections(events))
        corrections.extend(self._detect_apology_corrections(events))
        corrections.extend(self._detect_backtrack_corrections(events))
        corrections.extend(self._detect_error_recovery(events))
        corrections.extend(self._detect_strategy_pivots(events))
        corrections.extend(self._detect_output_revisions(events))
        corrections.extend(self._detect_assumption_corrections(events))
        corrections.extend(self._detect_hallucination_fixes(events))

        # Filter by confidence
        corrections = [c for c in corrections if c.confidence >= self.min_confidence]

        # Sort by correction_event_index
        corrections.sort(key=lambda c: c.correction_event_index)

        # Deduplicate (same correction_event_index)
        seen_indices: set = set()
        deduped: List[CorrectionEvent] = []
        for c in corrections:
            if c.correction_event_index not in seen_indices:
                seen_indices.add(c.correction_event_index)
                deduped.append(c)
        corrections = deduped

        # Compute metrics
        total = len(events)
        count = len(corrections)
        rate = (count / total) * 100 if total > 0 else 0.0
        latencies = [c.latency_events for c in corrections]
        mean_latency = statistics.mean(latencies) if latencies else 0.0
        effectivenesses = [c.effectiveness for c in corrections]
        eff_score = (statistics.mean(effectivenesses) * 100) if effectivenesses else 0.0

        # Category breakdown
        breakdown: Dict[str, int] = defaultdict(int)
        for c in corrections:
            breakdown[c.category.value] += 1

        # Self-awareness score: composite of frequency, effectiveness, speed
        freq_factor = min(rate / 5.0, 1.0)  # caps at 5 corrections per 100 events
        eff_factor = eff_score / 100.0
        speed_factor = max(0, 1.0 - (mean_latency / 10.0)) if mean_latency > 0 else 1.0
        speed_factor = max(speed_factor, 0.0)
        self_awareness = (freq_factor * 0.3 + eff_factor * 0.5 + speed_factor * 0.2) * 100

        # Grade
        grade = self._compute_grade(self_awareness)

        # Patterns
        patterns = self._detect_patterns(corrections, events)

        # Recommendations
        recommendations = self._generate_recommendations(corrections, events, self_awareness, breakdown)

        return SelfCorrectionReport(
            session_id=session_id,
            total_events=total,
            correction_count=count,
            correction_rate=round(rate, 2),
            mean_correction_latency=round(mean_latency, 2),
            effectiveness_score=round(eff_score, 1),
            self_awareness_score=round(self_awareness, 1),
            category_breakdown=dict(breakdown),
            correction_timeline=corrections,
            patterns=patterns,
            grade=grade,
            recommendations=recommendations,
        )

    # ── Detectors ───────────────────────────────────────────────────

    def _detect_retry_corrections(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect retried tool calls after failures with modified parameters."""
        corrections = []
        for i, ev in enumerate(events):
            if ev.get("type") != "tool_result":
                continue
            if not ev.get("data", {}).get("error"):
                continue
            # Look for a retry of the same tool within next 5 events
            tool_name = ev.get("data", {}).get("name", "")
            if not tool_name:
                # Try to find from preceding tool_call
                for j in range(i - 1, max(i - 3, -1), -1):
                    if events[j].get("type") == "tool_call":
                        tool_name = events[j].get("data", {}).get("name", "")
                        break
            if not tool_name:
                continue
            for j in range(i + 1, min(i + 6, len(events))):
                ej = events[j]
                if ej.get("type") == "tool_call" and ej.get("data", {}).get("name") == tool_name:
                    # Check if parameters differ
                    orig_input = ev.get("data", {}).get("input", {})
                    new_input = ej.get("data", {}).get("input", {})
                    if orig_input != new_input or not orig_input:
                        # Check if the retry succeeded
                        effectiveness = self._check_retry_success(events, j)
                        corrections.append(CorrectionEvent(
                            category=CorrectionCategory.RETRY_CORRECTION,
                            trigger_event_index=i,
                            correction_event_index=j,
                            latency_events=j - i,
                            effectiveness=effectiveness,
                            confidence=0.85,
                            description=f"Retried '{tool_name}' with modified parameters after failure",
                            trigger_summary=f"Tool '{tool_name}' failed",
                            correction_summary=f"Retried '{tool_name}' with new params",
                        ))
                        break
        return corrections

    def _detect_apology_corrections(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect explicit correction/apology language."""
        corrections = []
        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue
            for pattern in _CORRECTION_PHRASES:
                if re.search(pattern, content, re.IGNORECASE):
                    # Find what triggered it (look back for errors or user messages)
                    trigger_idx = max(0, i - 1)
                    for j in range(i - 1, max(i - 5, -1), -1):
                        if events[j].get("type") in ("error", "tool_result"):
                            trigger_idx = j
                            break
                    effectiveness = self._check_post_correction_success(events, i)
                    corrections.append(CorrectionEvent(
                        category=CorrectionCategory.APOLOGY_CORRECTION,
                        trigger_event_index=trigger_idx,
                        correction_event_index=i,
                        latency_events=i - trigger_idx,
                        effectiveness=effectiveness,
                        confidence=0.7,
                        description=f"Agent acknowledged mistake with correction language",
                        trigger_summary="Previous error or issue",
                        correction_summary="Explicit correction acknowledgment",
                    ))
                    break  # One detection per event
        return corrections

    def _detect_backtrack_corrections(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect backtracking to earlier approaches."""
        corrections = []
        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue
            for pattern in _BACKTRACK_PHRASES:
                if re.search(pattern, content, re.IGNORECASE):
                    trigger_idx = max(0, i - 2)
                    effectiveness = self._check_post_correction_success(events, i)
                    corrections.append(CorrectionEvent(
                        category=CorrectionCategory.BACKTRACK_CORRECTION,
                        trigger_event_index=trigger_idx,
                        correction_event_index=i,
                        latency_events=i - trigger_idx,
                        effectiveness=effectiveness,
                        confidence=0.65,
                        description="Agent backtracked to a different approach",
                        trigger_summary="Dead-end approach",
                        correction_summary="Returned to alternative strategy",
                    ))
                    break
        return corrections

    def _detect_error_recovery(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect successful operations following error events."""
        corrections = []
        for i, ev in enumerate(events):
            if ev.get("type") != "error":
                continue
            # Look for next successful tool_result
            for j in range(i + 1, min(i + 8, len(events))):
                ej = events[j]
                if ej.get("type") == "tool_result" and not ej.get("data", {}).get("error"):
                    corrections.append(CorrectionEvent(
                        category=CorrectionCategory.ERROR_RECOVERY,
                        trigger_event_index=i,
                        correction_event_index=j,
                        latency_events=j - i,
                        effectiveness=0.8,
                        confidence=0.75,
                        description="Recovered from error with successful operation",
                        trigger_summary=f"Error: {ev.get('data', {}).get('error', 'unknown')[:50]}",
                        correction_summary="Successful subsequent operation",
                    ))
                    break
        return corrections

    def _detect_strategy_pivots(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect tool/approach changes after poor results."""
        corrections = []
        tool_sequence: List[Tuple[int, str]] = []
        for i, ev in enumerate(events):
            if ev.get("type") == "tool_call":
                name = ev.get("data", {}).get("name", "")
                if name:
                    tool_sequence.append((i, name))

        # Look for patterns: tool_A fails, then different tool_B used
        for idx in range(len(tool_sequence) - 1):
            i, tool_a = tool_sequence[idx]
            # Check if tool_a had a failure
            had_failure = False
            for k in range(i, min(i + 3, len(events))):
                if events[k].get("type") == "tool_result" and events[k].get("data", {}).get("error"):
                    had_failure = True
                    break
                if events[k].get("type") == "error":
                    had_failure = True
                    break
            if not had_failure:
                continue
            # Next tool is different
            j, tool_b = tool_sequence[idx + 1]
            if tool_a != tool_b:
                effectiveness = self._check_retry_success(events, j)
                corrections.append(CorrectionEvent(
                    category=CorrectionCategory.STRATEGY_PIVOT,
                    trigger_event_index=i,
                    correction_event_index=j,
                    latency_events=j - i,
                    effectiveness=effectiveness,
                    confidence=0.7,
                    description=f"Pivoted from '{tool_a}' to '{tool_b}' after failure",
                    trigger_summary=f"Tool '{tool_a}' failed",
                    correction_summary=f"Switched to '{tool_b}'",
                ))
        return corrections

    def _detect_output_revisions(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect regenerated/revised outputs."""
        corrections = []
        llm_outputs: List[Tuple[int, str]] = []
        for i, ev in enumerate(events):
            if ev.get("type") == "llm_call":
                content = ev.get("data", {}).get("output", "") or ev.get("data", {}).get("content", "")
                if content and len(content) > 50:
                    llm_outputs.append((i, content))

        # Look for similar outputs (likely revisions)
        for idx in range(len(llm_outputs) - 1):
            i, text_a = llm_outputs[idx]
            j, text_b = llm_outputs[idx + 1]
            similarity = self._text_similarity(text_a[:200], text_b[:200])
            if 0.3 < similarity < 0.85:  # Similar but not identical = revision
                corrections.append(CorrectionEvent(
                    category=CorrectionCategory.OUTPUT_REVISION,
                    trigger_event_index=i,
                    correction_event_index=j,
                    latency_events=j - i,
                    effectiveness=0.7,
                    confidence=min(0.5 + similarity * 0.3, 0.85),
                    description="Revised output replacing earlier attempt",
                    trigger_summary="Initial output",
                    correction_summary="Revised output",
                ))
        return corrections

    def _detect_assumption_corrections(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect explicit correction of wrong assumptions."""
        corrections = []
        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue
            for pattern in _ASSUMPTION_PHRASES:
                if re.search(pattern, content, re.IGNORECASE):
                    trigger_idx = max(0, i - 3)
                    effectiveness = self._check_post_correction_success(events, i)
                    corrections.append(CorrectionEvent(
                        category=CorrectionCategory.ASSUMPTION_CORRECTION,
                        trigger_event_index=trigger_idx,
                        correction_event_index=i,
                        latency_events=i - trigger_idx,
                        effectiveness=effectiveness,
                        confidence=0.75,
                        description="Agent corrected an incorrect assumption",
                        trigger_summary="Incorrect assumption made",
                        correction_summary="Assumption explicitly corrected",
                    ))
                    break
        return corrections

    def _detect_hallucination_fixes(self, events: List[Dict]) -> List[CorrectionEvent]:
        """Detect retraction/correction of factual claims after verification."""
        corrections = []
        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue
            for pattern in _HALLUCINATION_PHRASES:
                if re.search(pattern, content, re.IGNORECASE):
                    trigger_idx = max(0, i - 4)
                    # Higher confidence if preceded by a verification tool call
                    conf = 0.7
                    for j in range(max(0, i - 3), i):
                        if events[j].get("type") in ("tool_call", "tool_result"):
                            conf = 0.85
                            trigger_idx = j
                            break
                    effectiveness = self._check_post_correction_success(events, i)
                    corrections.append(CorrectionEvent(
                        category=CorrectionCategory.HALLUCINATION_FIX,
                        trigger_event_index=trigger_idx,
                        correction_event_index=i,
                        latency_events=i - trigger_idx,
                        effectiveness=effectiveness,
                        confidence=conf,
                        description="Agent corrected a hallucinated/incorrect claim",
                        trigger_summary="Factual claim made",
                        correction_summary="Claim retracted after verification",
                    ))
                    break
        return corrections

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_content(self, event: Dict) -> str:
        """Extract text content from an event."""
        data = event.get("data", {})
        return (data.get("content", "") or data.get("output", "") or
                data.get("text", "") or "")

    def _check_retry_success(self, events: List[Dict], tool_call_idx: int) -> float:
        """Check if a tool call at given index succeeded."""
        for j in range(tool_call_idx + 1, min(tool_call_idx + 3, len(events))):
            if events[j].get("type") == "tool_result":
                if events[j].get("data", {}).get("error"):
                    return 0.3
                return 0.9
        return 0.5  # Unknown

    def _check_post_correction_success(self, events: List[Dict], correction_idx: int) -> float:
        """Check if events after a correction show success."""
        errors_after = 0
        success_after = 0
        for j in range(correction_idx + 1, min(correction_idx + 6, len(events))):
            if events[j].get("type") == "error":
                errors_after += 1
            elif events[j].get("type") == "tool_result":
                if events[j].get("data", {}).get("error"):
                    errors_after += 1
                else:
                    success_after += 1
        total = errors_after + success_after
        if total == 0:
            return 0.5
        return success_after / total

    def _text_similarity(self, a: str, b: str) -> float:
        """Simple word-overlap Jaccard similarity."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    def _compute_grade(self, score: float) -> str:
        """Map self-awareness score to letter grade."""
        if score >= 80:
            return "A"
        elif score >= 65:
            return "B"
        elif score >= 45:
            return "C"
        elif score >= 25:
            return "D"
        return "F"

    def _detect_patterns(self, corrections: List[CorrectionEvent], events: List[Dict]) -> List[CorrectionPattern]:
        """Detect meta-patterns across corrections."""
        patterns = []
        if not corrections:
            return patterns

        total = len(events)

        # Pattern: corrections cluster at end of session
        if len(corrections) >= 3:
            end_threshold = total * 0.7
            end_corrections = [c for c in corrections if c.correction_event_index >= end_threshold]
            if len(end_corrections) / len(corrections) > 0.6:
                patterns.append(CorrectionPattern(
                    name="Late Session Corrections",
                    description="Corrections cluster toward the end of sessions, suggesting delayed self-awareness",
                    evidence_count=len(end_corrections),
                    confidence=0.75,
                ))

        # Pattern: fast at tool corrections, slow at logic corrections
        tool_cats = {CorrectionCategory.RETRY_CORRECTION, CorrectionCategory.ERROR_RECOVERY, CorrectionCategory.STRATEGY_PIVOT}
        logic_cats = {CorrectionCategory.ASSUMPTION_CORRECTION, CorrectionCategory.HALLUCINATION_FIX, CorrectionCategory.BACKTRACK_CORRECTION}
        tool_latencies = [c.latency_events for c in corrections if c.category in tool_cats]
        logic_latencies = [c.latency_events for c in corrections if c.category in logic_cats]
        if tool_latencies and logic_latencies:
            tool_avg = statistics.mean(tool_latencies)
            logic_avg = statistics.mean(logic_latencies)
            if logic_avg > tool_avg * 1.5:
                patterns.append(CorrectionPattern(
                    name="Faster Tool Than Logic Corrections",
                    description=f"Tool errors corrected in ~{tool_avg:.1f} events vs logic errors in ~{logic_avg:.1f} events",
                    evidence_count=len(tool_latencies) + len(logic_latencies),
                    confidence=0.7,
                ))

        # Pattern: high retry correction rate
        retry_count = sum(1 for c in corrections if c.category == CorrectionCategory.RETRY_CORRECTION)
        if retry_count >= 3 and retry_count / len(corrections) > 0.4:
            patterns.append(CorrectionPattern(
                name="Retry-Heavy Correction Style",
                description="Agent relies heavily on trial-and-error retry to correct mistakes",
                evidence_count=retry_count,
                confidence=0.8,
            ))

        # Pattern: improving correction speed over session
        if len(corrections) >= 4:
            first_half = corrections[:len(corrections)//2]
            second_half = corrections[len(corrections)//2:]
            first_avg = statistics.mean([c.latency_events for c in first_half])
            second_avg = statistics.mean([c.latency_events for c in second_half])
            if second_avg < first_avg * 0.6:
                patterns.append(CorrectionPattern(
                    name="Improving Self-Awareness",
                    description=f"Correction latency improved from ~{first_avg:.1f} to ~{second_avg:.1f} events over session",
                    evidence_count=len(corrections),
                    confidence=0.7,
                ))

        # Pattern: high effectiveness
        if corrections:
            avg_eff = statistics.mean([c.effectiveness for c in corrections])
            if avg_eff >= 0.8:
                patterns.append(CorrectionPattern(
                    name="High Correction Effectiveness",
                    description=f"Average correction effectiveness is {avg_eff:.0%} — corrections consistently resolve issues",
                    evidence_count=len(corrections),
                    confidence=0.85,
                ))

        return patterns

    def _generate_recommendations(self, corrections: List[CorrectionEvent], events: List[Dict],
                                   score: float, breakdown: Dict[str, int]) -> List[str]:
        """Generate actionable improvement recommendations."""
        recs = []

        if not corrections:
            recs.append("No self-corrections detected — consider adding validation steps after tool calls")
            recs.append("Implement pre-flight checks before executing actions to catch errors earlier")
            return recs

        # High latency
        latencies = [c.latency_events for c in corrections]
        avg_latency = statistics.mean(latencies)
        if avg_latency > 5:
            recs.append(f"Average correction latency is {avg_latency:.1f} events — add earlier error detection checks")

        # Low effectiveness
        avg_eff = statistics.mean([c.effectiveness for c in corrections])
        if avg_eff < 0.5:
            recs.append("Correction effectiveness is low — corrections often don't resolve the underlying issue")

        # Many retries
        if breakdown.get("retry_correction", 0) >= 3:
            recs.append("High retry count suggests insufficient parameter validation before tool calls")

        # Many hallucination fixes
        if breakdown.get("hallucination_fix", 0) >= 2:
            recs.append("Multiple hallucination corrections — consider verification before making factual claims")

        # Many backtracks
        if breakdown.get("backtrack_correction", 0) >= 2:
            recs.append("Frequent backtracking suggests need for better upfront planning before execution")

        # Strategy pivots without success
        pivot_corr = [c for c in corrections if c.category == CorrectionCategory.STRATEGY_PIVOT]
        if pivot_corr and statistics.mean([c.effectiveness for c in pivot_corr]) < 0.5:
            recs.append("Strategy pivots often fail — consider evaluating alternatives before switching")

        # General score-based
        if score < 30:
            recs.append("Very low self-awareness — add systematic error checking after each action")
        elif score >= 80:
            recs.append("Excellent self-correction — focus on reducing error rate rather than correction rate")

        return recs[:5]  # Cap at 5

    def _empty_report(self, session_id: str) -> SelfCorrectionReport:
        """Return an empty report for sessions with no events."""
        return SelfCorrectionReport(
            session_id=session_id,
            total_events=0,
            correction_count=0,
            correction_rate=0.0,
            mean_correction_latency=0.0,
            effectiveness_score=0.0,
            self_awareness_score=0.0,
            grade="F",
            recommendations=["No events to analyze"],
        )
