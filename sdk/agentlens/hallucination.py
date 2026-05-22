"""Agent Hallucination Detector for AgentLens.

Autonomously detects hallucination signals in agent sessions by analyzing
response patterns, self-contradictions, fabricated references, confidence-
reality gaps, unverifiable assertions, and consistency drift.

Answers: "Is my agent making things up? Where are the hallucination risks?"

Detects 8 hallucination categories:
  1. Self-Contradiction — conflicting claims within the same session
  2. Fabricated Reference — invented citations, URLs, version numbers
  3. Confidence-Reality Gap — high confidence followed by failure/correction
  4. Unverifiable Assertion — specific claims with no supporting evidence
  5. Consistency Drift — topic claims that shift over the session
  6. Phantom Knowledge — referencing information never in context
  7. Specificity Escalation — adding detail without new information sources
  8. Hedging Collapse — appropriate uncertainty disappearing without cause

Usage::

    from agentlens.hallucination import HallucinationDetector

    detector = HallucinationDetector()
    report = detector.analyze(session)
    print(report.format_report())
    print(f"Veracity score: {report.veracity_score}/100")
    print(f"Tier: {report.tier}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, json, collections, re).
"""

from __future__ import annotations

import heapq
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ── Pre-compiled regex patterns ─────────────────────────────────────

_RE_WORD = re.compile(r'\b[a-zA-Z]{3,}\b')

_RE_CONFIDENCE = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bcertainly\b', r'\bdefinitely\b', r'\babsolutely\b',
        r'\bguarantee\b', r'\bno doubt\b', r'\bsurely\b',
        r'\b100%\b', r'\bwithout question\b', r'\bconfident\b',
        r'\bperfect(?:ly)?\b', r'\bclearly\b', r'\bobviously\b',
        r'\bundeniably\b', r'\bunquestionably\b',
    ]
]

_RE_HEDGING = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bmight\b', r'\bpossibly\b', r'\bperhaps\b', r'\bcould be\b',
        r'\bmaybe\b', r'\buncertain\b', r'\bnot sure\b', r'\blikely\b',
        r'\bprobably\b', r'\bseems?\b', r'\bappears?\b', r'\bapproximate',
        r'\bI think\b', r'\bI believe\b', r'\broughly\b',
    ]
]

_RE_FABRICATED_URL = re.compile(
    r'https?://[a-z0-9.-]+\.[a-z]{2,}/[a-zA-Z0-9/_.-]{10,}', re.IGNORECASE
)

_RE_DOI = re.compile(r'\b10\.\d{4,}/[^\s]{5,}\b')

_RE_VERSION = re.compile(r'\bv?\d+\.\d+\.\d+(?:\.\d+)?\b')

_RE_CITATION = re.compile(
    r'(?:[A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+))?\s*\(\d{4}\)'
    r'|\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+))?,?\s*\d{4}\))',
)

_RE_STAT_CLAIM = re.compile(
    r'\d{1,3}(?:\.\d+)?%|\b\d+(?:,\d{3})+\b|\b\d+\s*(?:million|billion|thousand)\b',
    re.IGNORECASE,
)

_RE_SPECIFIC_DATE = re.compile(
    r'\b(?:January|February|March|April|May|June|July|August|September|'
    r'October|November|December)\s+\d{1,2},?\s+\d{4}\b',
    re.IGNORECASE,
)

_RE_ERROR = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\berror\b', r'\bfail(?:ed|ure|s)?\b', r'\bsorry\b',
        r'\bcannot\b', r'\bunable\b', r'\bretry\b', r'\bcorrection\b',
        r'\bactually\b', r'\bwait\b.*\bwrong\b', r'\bmy mistake\b',
        r'\blet me correct\b', r'\bI was wrong\b', r'\bapologi[sz]e\b',
    ]
]

_RE_PHANTOM_REF = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bas (?:you|I) (?:mentioned|said|noted|asked|stated)\b',
        r'\bearlier (?:you|I|we) (?:discussed|agreed|decided)\b',
        r'\baccording to (?:the|your) (?:previous|earlier|last)\b',
        r'\bfrom (?:the|our) previous\b',
        r'\byou (?:asked|told|said|mentioned) (?:that|about|earlier)\b',
        r'\bas shown (?:in|by) the\b',
        r'\bthe (?:output|result|response) (?:showed|indicated|returned)\b',
    ]
]

_RE_ASSERTION = re.compile(
    r'\b(?:is|are|was|were|has|have|will|always|never|every|all|none|'
    r'must|exactly|precisely|specifically)\b',
    re.IGNORECASE,
)


# ── Enums ───────────────────────────────────────────────────────────


class HallucinationType(Enum):
    """Types of hallucination signals detected."""
    SELF_CONTRADICTION = "self_contradiction"
    FABRICATED_REFERENCE = "fabricated_reference"
    CONFIDENCE_REALITY_GAP = "confidence_reality_gap"
    UNVERIFIABLE_ASSERTION = "unverifiable_assertion"
    CONSISTENCY_DRIFT = "consistency_drift"
    PHANTOM_KNOWLEDGE = "phantom_knowledge"
    SPECIFICITY_ESCALATION = "specificity_escalation"
    HEDGING_COLLAPSE = "hedging_collapse"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class HallucinationSeverity(Enum):
    """Severity of a detected hallucination signal."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> float:
        return {
            "none": 0.0, "low": 0.15, "medium": 0.35,
            "high": 0.6, "critical": 1.0,
        }[self.value]


class VeracityTier(Enum):
    """Overall veracity tier classification."""
    EXCELLENT = "excellent"      # 80-100
    GOOD = "good"                # 60-79
    QUESTIONABLE = "questionable"  # 40-59
    POOR = "poor"                # 20-39
    UNRELIABLE = "unreliable"    # 0-19

    @property
    def label(self) -> str:
        return self.value.title()


class TrendDirection(Enum):
    """Trend of hallucination over the session."""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


# ── Data Classes ────────────────────────────────────────────────────


@dataclass
class HallucinationSignal:
    """A single detected hallucination instance."""
    type: HallucinationType
    event_index: int
    confidence: float  # 0-1
    severity: HallucinationSeverity
    description: str = ""
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "event_index": self.event_index,
            "confidence": round(self.confidence, 3),
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass
class HallucinationProfile:
    """Aggregated profile for one hallucination type."""
    type: HallucinationType
    signal_count: int
    avg_confidence: float
    severity: HallucinationSeverity
    trend: TrendDirection

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "signal_count": self.signal_count,
            "avg_confidence": round(self.avg_confidence, 3),
            "severity": self.severity.value,
            "trend": self.trend.value,
        }


@dataclass
class HallucinationReport:
    """Complete hallucination analysis report."""
    session_id: str
    total_events: int
    signals_detected: int
    veracity_score: float  # 0-100
    dominant_type: Optional[HallucinationType]
    profiles: List[HallucinationProfile] = field(default_factory=list)
    signal_timeline: List[HallucinationSignal] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    tier: str = "excellent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "signals_detected": self.signals_detected,
            "veracity_score": round(self.veracity_score, 1),
            "dominant_type": self.dominant_type.value if self.dominant_type else None,
            "profiles": [p.to_dict() for p in self.profiles],
            "signal_timeline": [s.to_dict() for s in self.signal_timeline],
            "recommendations": self.recommendations,
            "insights": self.insights,
            "tier": self.tier,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def format_report(self) -> str:
        """Format a human-readable text report."""
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  AGENT HALLUCINATION ANALYSIS")
        lines.append("=" * 60)
        lines.append(f"  Session: {self.session_id}")
        lines.append(f"  Events analyzed: {self.total_events}")
        lines.append("")

        tier_emoji = {
            "excellent": "🧠", "good": "✅", "questionable": "⚠️",
            "poor": "🔻", "unreliable": "❌",
        }.get(self.tier, "")
        lines.append(f"  Tier: {tier_emoji} {self.tier.title()}")
        lines.append(f"  Veracity Score: {self.veracity_score:.0f}/100")
        lines.append(f"  Signals detected: {self.signals_detected}")
        if self.dominant_type:
            lines.append(f"  Dominant type: {self.dominant_type.label}")
        lines.append("")

        if self.profiles:
            lines.append("-" * 60)
            lines.append("  HALLUCINATION PROFILES")
            lines.append("-" * 60)
            for p in sorted(self.profiles, key=lambda x: -x.signal_count):
                bar = "█" * min(p.signal_count * 2, 20)
                trend_icon = {
                    "increasing": "↑", "decreasing": "↓", "stable": "→",
                }[p.trend.value]
                lines.append(
                    f"  {p.type.label:<25} {p.signal_count:>3} "
                    f"[{p.severity.value:<8}] {trend_icon} {bar}"
                )
            lines.append("")

        if self.signal_timeline:
            lines.append("-" * 60)
            lines.append("  SIGNAL TIMELINE (top 10)")
            lines.append("-" * 60)
            top = heapq.nlargest(10, self.signal_timeline, key=lambda x: x.confidence)
            for s in top:
                lines.append(
                    f"  [{s.type.value}] event {s.event_index} "
                    f"(conf: {s.confidence:.0%}, {s.severity.value})"
                )
                if s.description:
                    lines.append(f"    {s.description}")
            lines.append("")

        if self.insights:
            lines.append("-" * 60)
            lines.append("  INSIGHTS")
            lines.append("-" * 60)
            for ins in self.insights:
                lines.append(f"  💡 {ins}")
            lines.append("")

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


class HallucinationDetector:
    """Autonomous hallucination detector for agent sessions.

    Analyzes session event streams to identify hallucination signals that
    indicate an agent may be fabricating or distorting information.
    """

    def __init__(self, min_confidence: float = 0.5, window_size: int = 10):
        self.min_confidence = min_confidence
        self.window_size = window_size

    # ── Public API ──────────────────────────────────────────────────

    def analyze(self, session: Dict[str, Any]) -> HallucinationReport:
        """Analyze a session for hallucination signals.

        Args:
            session: Dict with 'id'/'session_id' and 'events' list.

        Returns:
            HallucinationReport with all detected hallucinations.
        """
        session_id = session.get("id") or session.get("session_id", "unknown")
        events = session.get("events", [])
        total_events = len(events)

        if total_events < 3:
            return HallucinationReport(
                session_id=session_id,
                total_events=total_events,
                signals_detected=0,
                veracity_score=100.0,
                dominant_type=None,
                tier="excellent",
                recommendations=[
                    "Insufficient data for hallucination analysis (need 3+ events)."
                ],
            )

        all_signals: List[HallucinationSignal] = []
        all_signals.extend(self._detect_self_contradiction(events))
        all_signals.extend(self._detect_fabricated_reference(events))
        all_signals.extend(self._detect_confidence_reality_gap(events))
        all_signals.extend(self._detect_unverifiable_assertion(events))
        all_signals.extend(self._detect_consistency_drift(events))
        all_signals.extend(self._detect_phantom_knowledge(events))
        all_signals.extend(self._detect_specificity_escalation(events))
        all_signals.extend(self._detect_hedging_collapse(events))

        filtered = [s for s in all_signals if s.confidence >= self.min_confidence]
        profiles = self._build_profiles(filtered, total_events)
        veracity_score = self._compute_veracity_score(profiles, len(filtered), total_events)
        tier = self._compute_tier(veracity_score)

        dominant = None
        if profiles:
            top = max(profiles, key=lambda p: p.signal_count * p.avg_confidence)
            if top.signal_count > 0:
                dominant = top.type

        recommendations = self._generate_recommendations(profiles, dominant)
        insights = self._generate_insights(profiles, filtered, total_events)

        return HallucinationReport(
            session_id=session_id,
            total_events=total_events,
            signals_detected=len(filtered),
            veracity_score=veracity_score,
            dominant_type=dominant,
            profiles=profiles,
            signal_timeline=filtered,
            recommendations=recommendations,
            insights=insights,
            tier=tier,
        )

    # ── Detector 1: Self-Contradiction ──────────────────────────────

    def _detect_self_contradiction(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect conflicting claims within the session.

        Precomputes keywords, numbers, and negation-pattern matches per
        claim so the O(n²) pair loop does only set-intersection + dict
        lookup instead of re-running regex and keyword extraction on
        every pair (previously ~20 × N² redundant regex evaluations).
        """
        signals: List[HallucinationSignal] = []
        claims: List[Tuple[int, str]] = []  # (index, content)

        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue
            claims.append((i, content.lower()))

        if len(claims) < 2:
            return signals

        # Compile negation patterns once (list index → compiled pair)
        negation_pairs = [
            (re.compile(r'\bis\b'), re.compile(r'\bis not\b')),
            (re.compile(r'\bcan\b'), re.compile(r'\bcannot\b')),
            (re.compile(r'\bwill\b'), re.compile(r'\bwill not\b')),
            (re.compile(r'\bshould\b'), re.compile(r'\bshould not\b')),
            (re.compile(r'\btrue\b'), re.compile(r'\bfalse\b')),
            (re.compile(r'\byes\b'), re.compile(r'\bno\b')),
            (re.compile(r'\balways\b'), re.compile(r'\bnever\b')),
            (re.compile(r'\bsupports?\b'), re.compile(r'\bdoes not support\b')),
            (re.compile(r'\bexists?\b'), re.compile(r'\bdoes not exist\b')),
            (re.compile(r'\bworks?\b'), re.compile(r'\bdoes not work\b')),
        ]

        # --- Precompute per-claim data (O(N × P)) ---
        # Keywords and numbers: computed once per claim, not N-1 times.
        claim_keywords: List[set] = []
        claim_numbers: List[set] = []  # set of number strings
        # Negation signature: list of (pos_match, neg_match) booleans per pattern
        claim_negation: List[List[Tuple[bool, bool]]] = []

        _RE_NUM = re.compile(r'\b(\d+(?:\.\d+)?)\b')

        for _idx, text in claims:
            claim_keywords.append(self._get_keywords(text))
            claim_numbers.append(set(_RE_NUM.findall(text)))
            claim_negation.append([
                (bool(pos_re.search(text)), bool(neg_re.search(text)))
                for pos_re, neg_re in negation_pairs
            ])

        # --- O(n²) pair comparison using precomputed data ---
        for idx_a in range(len(claims)):
            i_a = claims[idx_a][0]
            kw_a = claim_keywords[idx_a]
            neg_a = claim_negation[idx_a]

            for idx_b in range(idx_a + 1, len(claims)):
                i_b = claims[idx_b][0]
                kw_b = claim_keywords[idx_b]
                overlap = kw_a & kw_b

                if len(overlap) < 2:
                    continue  # no shared topic — skip both checks

                # Check negation-based contradictions via precomputed flags
                neg_b = claim_negation[idx_b]
                found_negation = False
                for p_idx in range(len(negation_pairs)):
                    a_pos, a_neg = neg_a[p_idx]
                    b_pos, b_neg = neg_b[p_idx]
                    if (a_pos and b_neg) or (a_neg and b_pos):
                        conf = min(0.5 + len(overlap) * 0.1, 0.95)
                        signals.append(HallucinationSignal(
                            type=HallucinationType.SELF_CONTRADICTION,
                            event_index=i_b,
                            confidence=conf,
                            severity=self._severity_from_conf(conf),
                            description=f"Contradicts claim from event {i_a}",
                            evidence=f"Shared topics: {', '.join(sorted(overlap)[:5])}",
                        ))
                        found_negation = True
                        break  # one signal per pair

                if found_negation:
                    continue

                # Check numeric contradictions using precomputed number sets
                nums_a = claim_numbers[idx_a]
                nums_b = claim_numbers[idx_b]
                if nums_a and nums_b and not nums_a & nums_b:
                    conf = min(0.4 + len(overlap) * 0.08, 0.85)
                    if conf >= self.min_confidence:
                        signals.append(HallucinationSignal(
                            type=HallucinationType.SELF_CONTRADICTION,
                            event_index=i_b,
                            confidence=conf,
                            severity=self._severity_from_conf(conf),
                            description=f"Numeric values conflict with event {i_a}",
                            evidence=f"Topic overlap: {', '.join(sorted(overlap)[:5])}",
                        ))

        return signals

    # ── Detector 2: Fabricated Reference ────────────────────────────

    def _detect_fabricated_reference(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect potentially fabricated citations, URLs, or version numbers."""
        signals: List[HallucinationSignal] = []

        # Collect tool outputs for cross-reference
        tool_outputs: set = set()
        for ev in events:
            result = ev.get("tool_result", "")
            if result:
                tool_outputs.add(str(result).lower())

        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue

            # Check for academic citations not from tool outputs
            citations = _RE_CITATION.findall(content)
            for cite in citations:
                in_tool = any(cite.lower() in to for to in tool_outputs)
                if not in_tool:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.FABRICATED_REFERENCE,
                        event_index=i,
                        confidence=0.7,
                        severity=HallucinationSeverity.MEDIUM,
                        description="Academic citation not sourced from tool output",
                        evidence=f"Citation: {cite}",
                    ))

            # Check for DOIs
            dois = _RE_DOI.findall(content)
            for doi in dois:
                in_tool = any(doi.lower() in to for to in tool_outputs)
                if not in_tool:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.FABRICATED_REFERENCE,
                        event_index=i,
                        confidence=0.75,
                        severity=HallucinationSeverity.HIGH,
                        description="DOI reference not sourced from tool output",
                        evidence=f"DOI: {doi}",
                    ))

            # Check for URLs with suspicious specificity
            urls = _RE_FABRICATED_URL.findall(content)
            for url in urls:
                in_tool = any(url.lower() in to for to in tool_outputs)
                if not in_tool:
                    # Longer paths are more suspicious
                    path_depth = url.count("/") - 2
                    conf = min(0.4 + path_depth * 0.1, 0.85)
                    signals.append(HallucinationSignal(
                        type=HallucinationType.FABRICATED_REFERENCE,
                        event_index=i,
                        confidence=conf,
                        severity=self._severity_from_conf(conf),
                        description="URL not sourced from tool output",
                        evidence=f"URL: {url[:80]}",
                    ))

            # Check version numbers not from context
            versions = _RE_VERSION.findall(content)
            if len(versions) >= 3:
                in_tool = sum(1 for v in versions
                              if any(v.lower() in to for to in tool_outputs))
                unsourced = len(versions) - in_tool
                if unsourced >= 2:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.FABRICATED_REFERENCE,
                        event_index=i,
                        confidence=0.6,
                        severity=HallucinationSeverity.MEDIUM,
                        description=f"{unsourced} version numbers without source",
                        evidence=f"Versions: {', '.join(versions[:5])}",
                    ))

        return signals

    # ── Detector 3: Confidence-Reality Gap ──────────────────────────

    def _detect_confidence_reality_gap(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect high-confidence language followed by failures/corrections."""
        signals: List[HallucinationSignal] = []

        for i, ev in enumerate(events):
            content = self._get_content(ev)
            if not content:
                continue

            conf_matches = sum(1 for p in _RE_CONFIDENCE if p.search(content))
            if conf_matches == 0:
                continue

            # Look ahead for failure/correction signals
            lookahead = min(i + 4, len(events))
            failure_found = False
            for j in range(i + 1, lookahead):
                next_ev = events[j]
                next_content = self._get_content(next_ev)

                # Tool failure
                if next_ev.get("success") is False:
                    failure_found = True
                    break

                # Error/correction language
                if next_content:
                    error_hits = sum(1 for p in _RE_ERROR if p.search(next_content))
                    if error_hits >= 1:
                        failure_found = True
                        break

            if failure_found:
                conf = min(0.5 + conf_matches * 0.15, 0.95)
                signals.append(HallucinationSignal(
                    type=HallucinationType.CONFIDENCE_REALITY_GAP,
                    event_index=i,
                    confidence=conf,
                    severity=self._severity_from_conf(conf),
                    description="High confidence language followed by failure/correction",
                    evidence=f"{conf_matches} confidence markers, failure within {lookahead - i} events",
                ))

        return signals

    # ── Detector 4: Unverifiable Assertion ──────────────────────────

    def _detect_unverifiable_assertion(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect specific claims with no supporting evidence in context."""
        signals: List[HallucinationSignal] = []

        # Collect all tool outputs and user inputs as "evidence base"
        evidence_text = ""
        for ev in events:
            if ev.get("type") in ("user", "input", "tool_result", "system"):
                evidence_text += " " + self._get_content(ev)
            result = ev.get("tool_result", "")
            if result:
                evidence_text += " " + str(result)
        evidence_lower = evidence_text.lower()

        for i, ev in enumerate(events):
            if ev.get("type") not in ("assistant", "response", "agent", "llm"):
                continue
            content = self._get_content(ev)
            if not content:
                continue

            # Check for specific statistics not in evidence
            stats = _RE_STAT_CLAIM.findall(content)
            for stat in stats:
                if stat.lower() not in evidence_lower:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.UNVERIFIABLE_ASSERTION,
                        event_index=i,
                        confidence=0.6,
                        severity=HallucinationSeverity.MEDIUM,
                        description="Specific statistic without evidence source",
                        evidence=f"Claim: {stat}",
                    ))

            # Check for specific dates not in evidence
            dates = _RE_SPECIFIC_DATE.findall(content)
            for date in dates:
                if date.lower() not in evidence_lower:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.UNVERIFIABLE_ASSERTION,
                        event_index=i,
                        confidence=0.65,
                        severity=HallucinationSeverity.MEDIUM,
                        description="Specific date without evidence source",
                        evidence=f"Date: {date}",
                    ))

            # High assertion density without tool backing
            assertion_count = len(_RE_ASSERTION.findall(content))
            word_count = len(content.split())
            if word_count > 20:
                density = assertion_count / word_count
                if density > 0.15:
                    signals.append(HallucinationSignal(
                        type=HallucinationType.UNVERIFIABLE_ASSERTION,
                        event_index=i,
                        confidence=min(0.4 + density, 0.8),
                        severity=HallucinationSeverity.LOW,
                        description="High assertion density without verification",
                        evidence=f"Density: {density:.2f} ({assertion_count} assertions in {word_count} words)",
                    ))

        return signals

    # ── Detector 5: Consistency Drift ───────────────────────────────

    def _detect_consistency_drift(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect claims about the same topic that shift over the session."""
        signals: List[HallucinationSignal] = []

        # Group agent responses by sliding window
        agent_responses: List[Tuple[int, set]] = []
        for i, ev in enumerate(events):
            if ev.get("type") not in ("assistant", "response", "agent", "llm"):
                continue
            content = self._get_content(ev)
            if not content:
                continue
            kws = self._get_keywords(content)
            if len(kws) >= 3:
                agent_responses.append((i, kws))

        # Compare adjacent windows for topic overlap + keyword drift
        for idx in range(1, len(agent_responses)):
            i_prev, kw_prev = agent_responses[idx - 1]
            i_curr, kw_curr = agent_responses[idx]

            # Jaccard similarity
            intersection = kw_prev & kw_curr
            union = kw_prev | kw_curr
            if not union:
                continue
            jaccard = len(intersection) / len(union)

            # High overlap means same topic; then check for drift
            if 0.15 < jaccard < 0.6:
                # Similar topic but significant keyword changes
                new_kws = kw_curr - kw_prev
                lost_kws = kw_prev - kw_curr
                if len(new_kws) > 3 and len(lost_kws) > 3:
                    conf = min(0.4 + (len(new_kws) + len(lost_kws)) * 0.03, 0.85)
                    signals.append(HallucinationSignal(
                        type=HallucinationType.CONSISTENCY_DRIFT,
                        event_index=i_curr,
                        confidence=conf,
                        severity=self._severity_from_conf(conf),
                        description=f"Topic drift from event {i_prev}",
                        evidence=f"Jaccard: {jaccard:.2f}, +{len(new_kws)} new, -{len(lost_kws)} lost keywords",
                    ))

        return signals

    # ── Detector 6: Phantom Knowledge ───────────────────────────────

    def _detect_phantom_knowledge(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect references to information never in the conversation."""
        signals: List[HallucinationSignal] = []

        # Build context up to each event
        context_so_far: List[str] = []

        for i, ev in enumerate(events):
            ev_type = ev.get("type", "")
            content = self._get_content(ev)

            if ev_type in ("assistant", "response", "agent", "llm") and content:
                # Check for phantom references
                for pat in _RE_PHANTOM_REF:
                    if pat.search(content):
                        # Verify reference is grounded in prior context
                        all_context = " ".join(context_so_far).lower()

                        # Extract what's being referenced
                        match = pat.search(content)
                        if match:
                            ref_text = content[match.start():min(match.end() + 50, len(content))]
                            ref_kws = self._get_keywords(ref_text)
                            context_kws = self._get_keywords(all_context)

                            grounded = len(ref_kws & context_kws)
                            if grounded < 2 and len(ref_kws) >= 2:
                                conf = min(0.5 + (len(ref_kws) - grounded) * 0.1, 0.9)
                                signals.append(HallucinationSignal(
                                    type=HallucinationType.PHANTOM_KNOWLEDGE,
                                    event_index=i,
                                    confidence=conf,
                                    severity=self._severity_from_conf(conf),
                                    description="References information not in conversation history",
                                    evidence=f"Reference: {ref_text[:80]}",
                                ))
                                break

                # Check for referencing non-existent tool results
                if "the output" in content.lower() or "the result" in content.lower():
                    has_prior_tool = any(
                        e.get("tool_result") or e.get("type") == "tool_result"
                        for e in events[:i]
                    )
                    if not has_prior_tool and i > 0:
                        signals.append(HallucinationSignal(
                            type=HallucinationType.PHANTOM_KNOWLEDGE,
                            event_index=i,
                            confidence=0.7,
                            severity=HallucinationSeverity.HIGH,
                            description="References tool output that doesn't exist",
                            evidence="No tool results in prior context",
                        ))

            # Always add to running context
            if content:
                context_so_far.append(content)
            result = ev.get("tool_result", "")
            if result:
                context_so_far.append(str(result))

        return signals

    # ── Detector 7: Specificity Escalation ──────────────────────────

    def _detect_specificity_escalation(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect progressive detail addition without new information sources."""
        signals: List[HallucinationSignal] = []

        specificity_scores: List[Tuple[int, float]] = []

        for i, ev in enumerate(events):
            ev_type = ev.get("type", "")

            if ev_type in ("user", "input", "tool_result", "system"):
                continue

            if ev_type in ("assistant", "response", "agent", "llm"):
                content = self._get_content(ev)
                if not content:
                    continue
                score = self._specificity_score(content)
                specificity_scores.append((i, score))

        # Look for increasing specificity without new inputs
        for idx in range(2, len(specificity_scores)):
            i_curr, s_curr = specificity_scores[idx]
            i_prev, s_prev = specificity_scores[idx - 1]
            i_pp, s_pp = specificity_scores[idx - 2]

            # Monotonically increasing specificity
            if s_curr > s_prev > s_pp and s_curr - s_pp > 0.2:
                # Check if there was new input between these responses
                new_input = any(
                    events[j].get("type") in ("user", "input", "tool_result", "system")
                    for j in range(specificity_scores[idx - 2][0], i_curr)
                )
                if not new_input:
                    delta = s_curr - s_pp
                    conf = min(0.4 + delta * 0.8, 0.9)
                    signals.append(HallucinationSignal(
                        type=HallucinationType.SPECIFICITY_ESCALATION,
                        event_index=i_curr,
                        confidence=conf,
                        severity=self._severity_from_conf(conf),
                        description="Increasing specificity without new information",
                        evidence=f"Specificity: {s_pp:.2f} → {s_prev:.2f} → {s_curr:.2f}",
                    ))

        return signals

    # ── Detector 8: Hedging Collapse ────────────────────────────────

    def _detect_hedging_collapse(
        self, events: List[Dict[str, Any]]
    ) -> List[HallucinationSignal]:
        """Detect when appropriate hedging disappears without new evidence."""
        signals: List[HallucinationSignal] = []

        hedging_history: List[Tuple[int, float, float]] = []  # (idx, hedge_ratio, conf_ratio)

        for i, ev in enumerate(events):
            if ev.get("type") not in ("assistant", "response", "agent", "llm"):
                continue
            content = self._get_content(ev)
            if not content or len(content.split()) < 10:
                continue

            words = len(content.split())
            hedge_hits = sum(1 for p in _RE_HEDGING if p.search(content))
            conf_hits = sum(1 for p in _RE_CONFIDENCE if p.search(content))
            hedge_ratio = hedge_hits / words
            conf_ratio = conf_hits / words
            hedging_history.append((i, hedge_ratio, conf_ratio))

        for idx in range(1, len(hedging_history)):
            i_prev, h_prev, c_prev = hedging_history[idx - 1]
            i_curr, h_curr, c_curr = hedging_history[idx]

            # Hedging dropped and confidence rose
            if h_prev > 0 and h_curr == 0 and c_curr > c_prev:
                # Check for new evidence between
                new_evidence = any(
                    events[j].get("type") in ("tool_result", "system")
                    or events[j].get("tool_result")
                    for j in range(i_prev + 1, i_curr)
                )
                if not new_evidence:
                    conf = min(0.5 + (h_prev - h_curr) * 10 + (c_curr - c_prev) * 10, 0.9)
                    conf = max(conf, 0.5)
                    signals.append(HallucinationSignal(
                        type=HallucinationType.HEDGING_COLLAPSE,
                        event_index=i_curr,
                        confidence=conf,
                        severity=self._severity_from_conf(conf),
                        description="Hedging language disappeared without new evidence",
                        evidence=f"Hedge ratio: {h_prev:.3f}→{h_curr:.3f}, confidence: {c_prev:.3f}→{c_curr:.3f}",
                    ))

        return signals

    # ── Profile Building ────────────────────────────────────────────

    def _build_profiles(
        self, signals: List[HallucinationSignal], total_events: int
    ) -> List[HallucinationProfile]:
        """Build per-type aggregate profiles."""
        by_type: Dict[HallucinationType, List[HallucinationSignal]] = defaultdict(list)
        for s in signals:
            by_type[s.type].append(s)

        profiles: List[HallucinationProfile] = []
        for ht in HallucinationType:
            type_signals = by_type.get(ht, [])
            count = len(type_signals)
            avg_conf = (
                statistics.mean(s.confidence for s in type_signals)
                if type_signals else 0.0
            )
            severity = self._aggregate_severity(type_signals)
            trend = self._compute_trend(type_signals, total_events)
            profiles.append(HallucinationProfile(
                type=ht,
                signal_count=count,
                avg_confidence=avg_conf,
                severity=severity,
                trend=trend,
            ))

        return profiles

    def _aggregate_severity(
        self, signals: List[HallucinationSignal]
    ) -> HallucinationSeverity:
        """Aggregate severity from multiple signals."""
        if not signals:
            return HallucinationSeverity.NONE
        max_sev = max(s.severity.weight for s in signals)
        if max_sev >= 1.0:
            return HallucinationSeverity.CRITICAL
        if max_sev >= 0.6:
            return HallucinationSeverity.HIGH
        if max_sev >= 0.35:
            return HallucinationSeverity.MEDIUM
        if max_sev >= 0.15:
            return HallucinationSeverity.LOW
        return HallucinationSeverity.NONE

    def _compute_trend(
        self, signals: List[HallucinationSignal], total_events: int
    ) -> TrendDirection:
        """Compute trend by comparing first-half vs second-half signal density."""
        if len(signals) < 2:
            return TrendDirection.STABLE
        mid = total_events // 2
        first_half = sum(1 for s in signals if s.event_index < mid)
        second_half = sum(1 for s in signals if s.event_index >= mid)
        if second_half > first_half * 1.5:
            return TrendDirection.INCREASING
        if first_half > second_half * 1.5:
            return TrendDirection.DECREASING
        return TrendDirection.STABLE

    # ── Scoring ─────────────────────────────────────────────────────

    def _compute_veracity_score(
        self,
        profiles: List[HallucinationProfile],
        signal_count: int,
        total_events: int,
    ) -> float:
        """Compute overall veracity score 0-100."""
        if total_events == 0:
            return 100.0

        # Base penalty from signal density
        density = signal_count / max(total_events, 1)
        density_penalty = min(density * 80, 50)

        # Severity-weighted penalty
        severity_penalty = 0.0
        for p in profiles:
            severity_penalty += p.signal_count * p.severity.weight * 3.0

        severity_penalty = min(severity_penalty, 40)

        # Diversity penalty — more types = worse
        active_types = sum(1 for p in profiles if p.signal_count > 0)
        diversity_penalty = active_types * 2.5

        raw = 100.0 - density_penalty - severity_penalty - diversity_penalty
        return max(0.0, min(100.0, raw))

    def _compute_tier(self, score: float) -> str:
        """Map veracity score to tier."""
        if score >= 80:
            return VeracityTier.EXCELLENT.value
        if score >= 60:
            return VeracityTier.GOOD.value
        if score >= 40:
            return VeracityTier.QUESTIONABLE.value
        if score >= 20:
            return VeracityTier.POOR.value
        return VeracityTier.UNRELIABLE.value

    # ── Recommendations ─────────────────────────────────────────────

    def _generate_recommendations(
        self,
        profiles: List[HallucinationProfile],
        dominant: Optional[HallucinationType],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recs: List[str] = []
        _rec_map = {
            HallucinationType.SELF_CONTRADICTION: (
                "Add consistency checking: require agents to review prior claims before making new assertions."
            ),
            HallucinationType.FABRICATED_REFERENCE: (
                "Require source verification: agents should only cite references retrieved via tools."
            ),
            HallucinationType.CONFIDENCE_REALITY_GAP: (
                "Calibrate confidence language: penalize agents for high-confidence claims that fail."
            ),
            HallucinationType.UNVERIFIABLE_ASSERTION: (
                "Enforce evidence grounding: require agents to cite specific tool outputs for factual claims."
            ),
            HallucinationType.CONSISTENCY_DRIFT: (
                "Pin topic context: provide agents with summaries of prior claims on recurring topics."
            ),
            HallucinationType.PHANTOM_KNOWLEDGE: (
                "Strict context adherence: instrument prompts to discourage referencing prior conversation."
            ),
            HallucinationType.SPECIFICITY_ESCALATION: (
                "Cap unsupported detail: detect and truncate responses that add specifics without new inputs."
            ),
            HallucinationType.HEDGING_COLLAPSE: (
                "Preserve uncertainty: instruct agents to maintain hedging language until evidence arrives."
            ),
        }

        for p in sorted(profiles, key=lambda x: -x.signal_count):
            if p.signal_count > 0 and p.type in _rec_map:
                recs.append(_rec_map[p.type])

        if dominant:
            recs.insert(0, f"Priority: address {dominant.label} — dominant hallucination type in this session.")

        if not recs:
            recs.append("No significant hallucination patterns detected. Session appears grounded.")

        return recs

    # ── Insights ────────────────────────────────────────────────────

    def _generate_insights(
        self,
        profiles: List[HallucinationProfile],
        signals: List[HallucinationSignal],
        total_events: int,
    ) -> List[str]:
        """Generate autonomous insights about hallucination patterns."""
        insights: List[str] = []

        active = [p for p in profiles if p.signal_count > 0]
        if not active:
            insights.append("Clean session — no hallucination signals detected.")
            return insights

        # Cluster analysis
        if signals:
            indices = [s.event_index for s in signals]
            if len(indices) >= 3:
                mid = total_events // 2
                first = sum(1 for i in indices if i < mid)
                second = sum(1 for i in indices if i >= mid)
                if second > first * 2:
                    insights.append(
                        "Hallucination signals concentrate in the second half of the session, "
                        "suggesting context window pressure or fatigue."
                    )
                elif first > second * 2:
                    insights.append(
                        "Hallucination signals concentrate early in the session, "
                        "possibly from insufficient initial grounding."
                    )

                # Check for burst clusters
                sorted_idx = sorted(indices)
                max_gap = max(
                    sorted_idx[i + 1] - sorted_idx[i]
                    for i in range(len(sorted_idx) - 1)
                )
                min_gap = min(
                    sorted_idx[i + 1] - sorted_idx[i]
                    for i in range(len(sorted_idx) - 1)
                )
                if min_gap <= 1 and len(sorted_idx) >= 4:
                    insights.append(
                        f"Hallucination signals cluster in bursts (gap range: {min_gap}-{max_gap} events)."
                    )

        # Confidence-reality correlation
        conf_gap = next(
            (p for p in profiles if p.type == HallucinationType.CONFIDENCE_REALITY_GAP),
            None,
        )
        fabricated = next(
            (p for p in profiles if p.type == HallucinationType.FABRICATED_REFERENCE),
            None,
        )
        if conf_gap and fabricated and conf_gap.signal_count > 0 and fabricated.signal_count > 0:
            insights.append(
                "Confidence-reality gaps co-occur with fabricated references — "
                "the agent is confidently citing invented sources."
            )

        # Hedging collapse + specificity escalation
        hedge = next(
            (p for p in profiles if p.type == HallucinationType.HEDGING_COLLAPSE),
            None,
        )
        specific = next(
            (p for p in profiles if p.type == HallucinationType.SPECIFICITY_ESCALATION),
            None,
        )
        if hedge and specific and hedge.signal_count > 0 and specific.signal_count > 0:
            insights.append(
                "Hedging collapse correlates with specificity escalation — "
                "the agent grows more certain AND more detailed, a classic hallucination escalation pattern."
            )

        # Contradiction + phantom knowledge
        contradiction = next(
            (p for p in profiles if p.type == HallucinationType.SELF_CONTRADICTION),
            None,
        )
        phantom = next(
            (p for p in profiles if p.type == HallucinationType.PHANTOM_KNOWLEDGE),
            None,
        )
        if contradiction and phantom and contradiction.signal_count > 0 and phantom.signal_count > 0:
            insights.append(
                "Self-contradictions combined with phantom knowledge suggest "
                "the agent is confabulating — constructing plausible but false narratives."
            )

        # Type diversity
        if len(active) >= 5:
            insights.append(
                f"Hallucinations span {len(active)} of 8 categories — "
                "this indicates broad grounding problems, not isolated issues."
            )

        # Increasing trend warning
        increasing = [p for p in active if p.trend == TrendDirection.INCREASING]
        if increasing:
            names = ", ".join(p.type.label for p in increasing[:3])
            insights.append(
                f"Worsening trends detected in: {names}. "
                "Hallucination risk grows as the session continues."
            )

        return insights

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _get_content(event: Dict[str, Any]) -> str:
        """Extract text content from an event."""
        content = event.get("content", "")
        if not content:
            content = event.get("text", "")
        if not content:
            content = event.get("message", "")
        return str(content) if content else ""

    @staticmethod
    def _get_keywords(text: str) -> set:
        """Extract meaningful keywords from text."""
        words = set(_RE_WORD.findall(text.lower()))
        # Remove common stop words
        stops = {
            "that", "this", "with", "from", "have", "been", "were", "will",
            "would", "could", "should", "does", "they", "them", "their",
            "what", "when", "where", "which", "while", "about", "into",
            "than", "then", "also", "just", "more", "some", "other",
            "very", "only", "most", "much", "each", "such", "like",
            "after", "before", "over", "under", "between", "through",
            "here", "there", "these", "those", "your", "same",
        }
        return words - stops

    @staticmethod
    def _severity_from_conf(confidence: float) -> HallucinationSeverity:
        """Map confidence to severity."""
        if confidence >= 0.85:
            return HallucinationSeverity.CRITICAL
        if confidence >= 0.7:
            return HallucinationSeverity.HIGH
        if confidence >= 0.5:
            return HallucinationSeverity.MEDIUM
        if confidence >= 0.3:
            return HallucinationSeverity.LOW
        return HallucinationSeverity.NONE

    @staticmethod
    def _specificity_score(text: str) -> float:
        """Score how specific a text is (0-1). Higher = more specific."""
        if not text:
            return 0.0

        words = text.split()
        word_count = len(words)
        if word_count == 0:
            return 0.0

        # Count specific elements
        numbers = len(re.findall(r'\b\d+(?:\.\d+)?\b', text))
        proper_nouns = len(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text))
        tech_terms = len(re.findall(
            r'\b(?:API|SDK|HTTP|JSON|SQL|REST|OAuth|JWT|TCP|UDP|DNS|SSL|TLS'
            r'|HTTPS|SSH|SMTP|IMAP|CORS|CSRF|XSS)\b', text
        ))
        versions = len(_RE_VERSION.findall(text))

        raw = (numbers * 0.3 + proper_nouns * 0.2 + tech_terms * 0.3 + versions * 0.4) / word_count
        return min(raw * 5, 1.0)
