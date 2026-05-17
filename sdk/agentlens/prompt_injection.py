"""Agent Prompt Injection Detector for AgentLens.

Autonomously detects prompt injection attempts, jailbreak patterns, and
adversarial manipulation in agent sessions by scanning user inputs, tool
parameters, and agent outputs for known attack signatures.

Answers: "Is someone trying to manipulate my agent? What attack vectors are being used?"

Detects 8 injection categories:
  1. Direct Override — explicit instructions to ignore/override system prompts
  2. Role Hijack — attempts to make the agent adopt a different persona
  3. Context Manipulation — injecting fake context, history, or system messages
  4. Instruction Smuggling — hiding instructions in data fields, tool outputs, encoded text
  5. Goal Diversion — steering the agent away from its intended task
  6. Privilege Escalation — requesting elevated permissions, disabled safeguards
  7. Information Extraction — probing for system prompts, API keys, internal state
  8. Recursive Injection — payloads designed to survive summarization/forwarding

Usage::

    from agentlens.prompt_injection import PromptInjectionDetector

    detector = PromptInjectionDetector()
    report = detector.analyze(session)
    print(report.format_report())
    print(f"Safety score: {report.safety_score}/100")
    print(f"Grade: {report.grade}")

Pure Python, stdlib only (math, statistics, dataclasses, enum, json, collections, re).
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Tuple


# ── Enums ───────────────────────────────────────────────────────────


class InjectionCategory(Enum):
    """Types of prompt injection attacks detected."""
    DIRECT_OVERRIDE = "direct_override"
    ROLE_HIJACK = "role_hijack"
    CONTEXT_MANIPULATION = "context_manipulation"
    INSTRUCTION_SMUGGLING = "instruction_smuggling"
    GOAL_DIVERSION = "goal_diversion"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    INFORMATION_EXTRACTION = "information_extraction"
    RECURSIVE_INJECTION = "recursive_injection"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class ThreatLevel(Enum):
    """Severity of a detected injection attempt."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> float:
        return {"none": 0.0, "low": 0.15, "medium": 0.35,
                "high": 0.6, "critical": 1.0}[self.value]


class SafetyGrade(Enum):
    """Overall safety grade."""
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class TrendDirection(Enum):
    """Whether injection attempts escalate over time."""
    ESCALATING = "escalating"
    STABLE = "stable"
    DEESCALATING = "de-escalating"


# ── Data classes ────────────────────────────────────────────────────


@dataclass
class InjectionSignal:
    """A single detected injection attempt signal."""
    category: InjectionCategory
    event_index: int
    confidence: float  # 0.0 – 1.0
    threat_level: ThreatLevel
    description: str = ""
    evidence: str = ""
    matched_pattern: str = ""
    source_field: str = ""  # "user_input", "tool_output", "parameter", etc.


@dataclass
class CategoryProfile:
    """Aggregated stats for one injection category."""
    category: InjectionCategory
    signal_count: int = 0
    max_confidence: float = 0.0
    avg_confidence: float = 0.0
    threat_level: ThreatLevel = ThreatLevel.NONE
    first_seen_index: int = -1
    last_seen_index: int = -1
    escalating: bool = False


@dataclass
class AttackerProfile:
    """Inferred attacker sophistication profile."""
    sophistication: str = "unknown"  # naive, intermediate, advanced, expert
    persistence: float = 0.0  # 0-1, how persistent is the attacker
    diversity: float = 0.0  # 0-1, how many categories used
    escalation_rate: float = 0.0  # positive = escalating


@dataclass
class PromptInjectionReport:
    """Full prompt injection analysis report."""
    session_id: str = ""
    total_events: int = 0
    events_scanned: int = 0
    injection_signals_detected: int = 0
    safety_score: float = 100.0  # 0–100, higher = safer
    grade: str = "A"
    threat_level: ThreatLevel = ThreatLevel.NONE
    trend: TrendDirection = TrendDirection.STABLE
    category_profiles: Dict[str, CategoryProfile] = field(default_factory=dict)
    signal_timeline: List[InjectionSignal] = field(default_factory=list)
    attacker_profile: AttackerProfile = field(default_factory=AttackerProfile)
    recommendations: List[str] = field(default_factory=list)
    autonomous_insights: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "session_id": self.session_id,
            "total_events": self.total_events,
            "events_scanned": self.events_scanned,
            "injection_signals_detected": self.injection_signals_detected,
            "safety_score": round(self.safety_score, 1),
            "grade": self.grade,
            "threat_level": self.threat_level.value,
            "trend": self.trend.value,
            "category_profiles": {
                k: {
                    "category": v.category.value,
                    "signal_count": v.signal_count,
                    "max_confidence": round(v.max_confidence, 3),
                    "avg_confidence": round(v.avg_confidence, 3),
                    "threat_level": v.threat_level.value,
                    "first_seen_index": v.first_seen_index,
                    "last_seen_index": v.last_seen_index,
                    "escalating": v.escalating,
                }
                for k, v in self.category_profiles.items()
            },
            "signal_timeline": [
                {
                    "category": s.category.value,
                    "event_index": s.event_index,
                    "confidence": round(s.confidence, 3),
                    "threat_level": s.threat_level.value,
                    "description": s.description,
                    "evidence": s.evidence,
                    "matched_pattern": s.matched_pattern,
                    "source_field": s.source_field,
                }
                for s in self.signal_timeline
            ],
            "attacker_profile": {
                "sophistication": self.attacker_profile.sophistication,
                "persistence": round(self.attacker_profile.persistence, 3),
                "diversity": round(self.attacker_profile.diversity, 3),
                "escalation_rate": round(self.attacker_profile.escalation_rate, 3),
            },
            "recommendations": self.recommendations,
            "autonomous_insights": self.autonomous_insights,
        }

    def format_report(self) -> str:
        """Human-readable report."""
        lines = []
        lines.append("=" * 60)
        lines.append("  AGENT PROMPT INJECTION DETECTOR")
        lines.append("=" * 60)
        lines.append(f"  Session: {self.session_id}")
        lines.append(f"  Events scanned: {self.events_scanned}/{self.total_events}")
        lines.append(f"  Injection signals: {self.injection_signals_detected}")
        lines.append(f"  Safety score: {self.safety_score:.0f}/100")
        lines.append(f"  Grade: {self.grade}")
        lines.append(f"  Threat level: {self.threat_level.value.upper()}")
        lines.append(f"  Trend: {self.trend.value}")
        lines.append("")

        if self.injection_signals_detected > 0:
            lines.append("-" * 60)
            lines.append("  ATTACKER PROFILE")
            lines.append("-" * 60)
            ap = self.attacker_profile
            lines.append(f"  Sophistication: {ap.sophistication}")
            lines.append(f"  Persistence:    {ap.persistence:.0%}")
            lines.append(f"  Diversity:      {ap.diversity:.0%}")
            lines.append(f"  Escalation:     {ap.escalation_rate:+.2f}")
            lines.append("")

        # Category breakdown
        active = {k: v for k, v in self.category_profiles.items() if v.signal_count > 0}
        if active:
            lines.append("-" * 60)
            lines.append("  CATEGORY BREAKDOWN")
            lines.append("-" * 60)
            for name, prof in sorted(active.items(), key=lambda x: -x[1].signal_count):
                esc = " ⬆ ESCALATING" if prof.escalating else ""
                lines.append(f"  {prof.category.label}")
                lines.append(f"    Signals: {prof.signal_count}  |  "
                             f"Threat: {prof.threat_level.value}  |  "
                             f"Max conf: {prof.max_confidence:.0%}{esc}")

        if self.recommendations:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  RECOMMENDATIONS")
            lines.append("-" * 60)
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"  {i}. {rec}")

        if self.autonomous_insights:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  AUTONOMOUS INSIGHTS")
            lines.append("-" * 60)
            for ins in self.autonomous_insights:
                lines.append(f"  • {ins}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Pattern libraries ───────────────────────────────────────────────

# Each pattern: (compiled_regex, confidence, description)
# Patterns are pre-compiled at module load time to avoid redundant
# re.compile() calls on every scan — O(patterns) compilation once
# instead of O(events × fields × patterns) per analyze() invocation.

def _compile_patterns(raw: List[Tuple[str, float, str]]) -> List[Tuple["re.Pattern[str]", float, str]]:
    """Pre-compile raw regex pattern tuples into compiled pattern tuples."""
    return [(re.compile(pat, re.IGNORECASE), conf, desc) for pat, conf, desc in raw]

_DIRECT_OVERRIDE_PATTERNS = _compile_patterns([
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|context)", 0.95, "Ignore previous instructions"),
    (r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", 0.90, "Disregard prior rules"),
    (r"forget\s+(everything|all)\s+(you|that)\s+(know|were\s+told|learned)", 0.90, "Forget all prior context"),
    (r"new\s+instructions?\s*:", 0.80, "Explicit new instruction block"),
    (r"override\s+(system|safety|previous)\s+(prompt|instructions?|rules?)", 0.95, "System prompt override"),
    (r"your\s+(real|actual|true)\s+instructions?\s+(are|is)\s*:", 0.90, "Fake instruction replacement"),
    (r"do\s+not\s+follow\s+(your|the|any)\s+(original|system|safety)\s+(prompt|instructions?)", 0.95, "Block original instructions"),
    (r"from\s+now\s+on[,.]?\s+(you\s+)?(will|must|should|are)", 0.70, "Temporal override"),
])

_ROLE_HIJACK_PATTERNS = _compile_patterns([
    (r"you\s+are\s+(now|actually|really)\s+(a|an|the)\s+\w+", 0.85, "Role reassignment"),
    (r"pretend\s+(you\s+are|to\s+be)\s+(a|an)\s+\w+", 0.80, "Pretend role play"),
    (r"act\s+as\s+(a|an|if\s+you\s+were)\s+\w+", 0.70, "Act-as directive"),
    (r"you\s+are\s+(dan|stan|dude|kevin|aim|jailbroken)", 0.95, "Known jailbreak persona"),
    (r"switch\s+to\s+(\w+)\s+mode", 0.75, "Mode switch attempt"),
    (r"enable\s+(developer|admin|debug|god|unrestricted)\s+mode", 0.90, "Privileged mode request"),
    (r"enter\s+(jailbreak|unrestricted|uncensored)\s+mode", 0.95, "Jailbreak mode request"),
])

_CONTEXT_MANIPULATION_PATTERNS = _compile_patterns([
    (r"\[system\]|\[SYSTEM\]|<<\s*system\s*>>|<\|system\|>", 0.90, "Fake system message tag"),
    (r"system\s*prompt\s*:", 0.80, "System prompt injection"),
    (r"<\|im_start\|>|<\|im_end\|>", 0.95, "Chat ML delimiter injection"),
    (r"(###|---)\s*(system|instruction|context)\s*(###|---)", 0.85, "Delimiter-based injection"),
    (r"the\s+(user|human|admin)\s+(previously|earlier)\s+(said|asked|instructed)", 0.75, "Fake conversation history"),
    (r"(assistant|ai|bot)\s*:\s*", 0.70, "Fake assistant turn"),
    (r"<\|?endoftext\|?>|<\|?sep\|?>", 0.90, "Token boundary injection"),
])

_INSTRUCTION_SMUGGLING_PATTERNS = _compile_patterns([
    (r"base64\s*:", 0.70, "Base64-encoded instruction"),
    (r"\\x[0-9a-fA-F]{2}", 0.60, "Hex-encoded payload"),
    (r"&#\d{2,4};", 0.60, "HTML entity encoding"),
    (r"translate\s+the\s+following\s+(and|then)\s+(also|additionally)\s+", 0.75, "Piggyback on translation"),
    (r"(hidden|secret|invisible)\s+instruction", 0.85, "Hidden instruction marker"),
    (r"ignore\s+the\s+above.*?instead\s+(do|perform|execute)", 0.90, "Override-then-execute"),
    (r"(?:zero[-\s]?width|invisible)\s+(?:char|text|space)", 0.80, "Zero-width character mention"),
])

_GOAL_DIVERSION_PATTERNS = _compile_patterns([
    (r"(but\s+)?first[,.]?\s+(do|tell|show|write|create|generate)\s+", 0.60, "Task prepend diversion"),
    (r"(before|instead\s+of)\s+(that|answering|responding)[,.]?\s+", 0.65, "Task replacement"),
    (r"(actually|wait)[,.]?\s+(can\s+you|I\s+need\s+you\s+to)\s+", 0.50, "Redirect via 'actually'"),
    (r"(also|additionally|while\s+you.re\s+at\s+it)[,.]?\s+(please\s+)?", 0.40, "Scope expansion"),
    (r"(most\s+important|urgent|critical)\s*:", 0.55, "Priority override"),
    (r"forget\s+(about\s+)?(the|that|my)\s+(previous|original|first)\s+(request|question|task)", 0.85, "Explicit task abandonment"),
])

_PRIVILEGE_ESCALATION_PATTERNS = _compile_patterns([
    (r"(disable|turn\s+off|remove)\s+(your\s+)?(safety|content|ethical)\s+(filters?|guardrails?|restrictions?)", 0.95, "Disable safety filters"),
    (r"(no|without)\s+(restrictions?|limitations?|censorship|filters?)", 0.85, "Remove restrictions"),
    (r"(admin|sudo|root|superuser)\s+(access|mode|privileges?|command)", 0.90, "Admin privilege request"),
    (r"(bypass|circumvent|skip|ignore)\s+(the\s+)?(content|safety|moderation)\s+(policy|filter|check)", 0.95, "Policy bypass"),
    (r"(unlock|enable)\s+(all|full)\s+(capabilities?|features?|permissions?)", 0.80, "Unlock capabilities"),
    (r"(i\s+am|i.m)\s+(an?\s+)?(admin|administrator|developer|owner|moderator)", 0.75, "False authority claim"),
    (r"(execute|run)\s+(this\s+)?(code|command|script)\s+(as|with)\s+(admin|root|elevated)", 0.90, "Elevated execution"),
])

_INFORMATION_EXTRACTION_PATTERNS = _compile_patterns([
    (r"(what|show|reveal|display|print|output)\s+(is\s+)?(your|the)\s+(system\s+prompt|instructions?|rules?|guidelines?)", 0.90, "System prompt extraction"),
    (r"(repeat|echo|recite|write\s+out)\s+(your\s+)?(system\s+prompt|instructions?|initial\s+prompt)", 0.95, "Prompt echo request"),
    (r"(what|show)\s+(me\s+)?(are\s+)?(your|the)\s+(api\s+keys?|credentials?|secrets?|tokens?|passwords?)", 0.95, "Credential extraction"),
    (r"(list|show|display)\s+(all\s+)?(your\s+)?(tools?|functions?|capabilities?|endpoints?)", 0.60, "Capability enumeration"),
    (r"(what|how)\s+(do\s+you|can\s+you)\s+(access|connect\s+to|reach)", 0.50, "Access probe"),
    (r"(tell|show)\s+me\s+(about\s+)?(your|the)\s+(architecture|backend|infrastructure|internal)", 0.70, "Architecture probe"),
    (r"(what|which)\s+(model|version|framework)\s+are\s+you", 0.45, "Model identification"),
])

_RECURSIVE_INJECTION_PATTERNS = _compile_patterns([
    (r"(when\s+)?(you|someone)\s+(summarize|forward|copy|repeat)\s+this", 0.80, "Summarization-surviving payload"),
    (r"(include|embed|insert)\s+this\s+(instruction|text|message)\s+in\s+(every|all|your)", 0.85, "Self-propagating instruction"),
    (r"(if|when)\s+(asked|prompted|queried)\s+(about|for)\s+.{5,30}[,.]?\s+(say|respond|answer|reply)", 0.75, "Conditional trigger payload"),
    (r"(always|forever)\s+(remember|include|add)\s+this\s+(in|to)\s+(your|every|all)", 0.80, "Persistence payload"),
    (r"(this\s+is\s+a\s+)?(permanent|persistent)\s+(instruction|rule|directive)", 0.85, "Persistence claim"),
    (r"(append|prepend|inject)\s+(this|the\s+following)\s+(to|into|in)\s+(every|all|each)", 0.90, "Injection propagation directive"),
])

_ALL_PATTERNS: Dict[InjectionCategory, List[Tuple[str, float, str]]] = {
    InjectionCategory.DIRECT_OVERRIDE: _DIRECT_OVERRIDE_PATTERNS,
    InjectionCategory.ROLE_HIJACK: _ROLE_HIJACK_PATTERNS,
    InjectionCategory.CONTEXT_MANIPULATION: _CONTEXT_MANIPULATION_PATTERNS,
    InjectionCategory.INSTRUCTION_SMUGGLING: _INSTRUCTION_SMUGGLING_PATTERNS,
    InjectionCategory.GOAL_DIVERSION: _GOAL_DIVERSION_PATTERNS,
    InjectionCategory.PRIVILEGE_ESCALATION: _PRIVILEGE_ESCALATION_PATTERNS,
    InjectionCategory.INFORMATION_EXTRACTION: _INFORMATION_EXTRACTION_PATTERNS,
    InjectionCategory.RECURSIVE_INJECTION: _RECURSIVE_INJECTION_PATTERNS,
}


# ── Detector engines ────────────────────────────────────────────────


def _extract_text_fields(event: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Extract all text content from an event, returning (field_name, text) pairs."""
    fields: List[Tuple[str, str]] = []
    meta = event.get("metadata", {}) or {}
    etype = event.get("type", "")

    # User input content
    content = meta.get("content", "")
    if content:
        fields.append(("user_input", str(content)))

    # Input data (for LLM calls)
    input_data = meta.get("input_data") or event.get("input_data")
    if isinstance(input_data, dict):
        for k, v in input_data.items():
            if isinstance(v, str) and len(v) > 5:
                fields.append((f"input.{k}", v))
        # Check messages array
        msgs = input_data.get("messages", [])
        if isinstance(msgs, list):
            for msg in msgs:
                if isinstance(msg, dict):
                    mc = msg.get("content", "")
                    if isinstance(mc, str) and len(mc) > 5:
                        role = msg.get("role", "unknown")
                        fields.append((f"message.{role}", mc))

    # Tool parameters
    params = meta.get("parameters") or meta.get("tool_input")
    if isinstance(params, dict):
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 5:
                fields.append((f"parameter.{k}", v))
    elif isinstance(params, str) and len(params) > 5:
        fields.append(("parameter", params))

    # Tool output (indirect injection via tool responses)
    tool_output = meta.get("tool_output") or meta.get("output")
    if isinstance(tool_output, str) and len(tool_output) > 5:
        fields.append(("tool_output", tool_output))
    elif isinstance(tool_output, dict):
        for k, v in tool_output.items():
            if isinstance(v, str) and len(v) > 5:
                fields.append((f"tool_output.{k}", v))

    # Generic text/description
    for key in ("text", "description", "query", "prompt", "body", "title"):
        val = meta.get(key)
        if isinstance(val, str) and len(val) > 5:
            fields.append((key, val))

    return fields


def _scan_patterns(text: str, category: InjectionCategory,
                   patterns: List[Tuple["re.Pattern[str]", float, str]],
                   min_confidence: float) -> List[Tuple[float, str, str]]:
    """Scan text against pre-compiled patterns for a category.

    Patterns are already compiled at module load time, so each call
    performs only ``pattern.search()`` — no per-call ``re.compile()``.

    Returns list of (confidence, description, matched_text) tuples.
    """
    results = []
    for compiled_pat, conf, desc in patterns:
        if conf < min_confidence:
            continue
        match = compiled_pat.search(text)
        if match:
            results.append((conf, desc, match.group(0)))
    return results


def _compute_threat_level(confidence: float) -> ThreatLevel:
    """Map confidence to threat level."""
    if confidence >= 0.90:
        return ThreatLevel.CRITICAL
    elif confidence >= 0.75:
        return ThreatLevel.HIGH
    elif confidence >= 0.55:
        return ThreatLevel.MEDIUM
    elif confidence >= 0.35:
        return ThreatLevel.LOW
    return ThreatLevel.NONE


def _aggregate_threat(signals: List[InjectionSignal]) -> ThreatLevel:
    """Compute overall threat level from signals."""
    if not signals:
        return ThreatLevel.NONE
    max_conf = max(s.confidence for s in signals)
    count = len(signals)
    # Boost threat for volume
    if count >= 10 and max_conf >= 0.5:
        return ThreatLevel.CRITICAL
    if count >= 5 and max_conf >= 0.6:
        return ThreatLevel.CRITICAL
    return _compute_threat_level(max_conf)


def _detect_trend(signals: List[InjectionSignal], total_events: int) -> TrendDirection:
    """Detect if injection attempts escalate over the session."""
    if len(signals) < 3:
        return TrendDirection.STABLE
    indices = [s.event_index for s in signals]
    mid = total_events // 2
    first_half = sum(1 for i in indices if i < mid)
    second_half = sum(1 for i in indices if i >= mid)
    if second_half > first_half * 1.5:
        return TrendDirection.ESCALATING
    elif first_half > second_half * 1.5:
        return TrendDirection.DEESCALATING
    return TrendDirection.STABLE


def _infer_attacker_profile(signals: List[InjectionSignal],
                            total_events: int) -> AttackerProfile:
    """Infer attacker sophistication from signal patterns."""
    if not signals:
        return AttackerProfile(sophistication="none", persistence=0.0,
                               diversity=0.0, escalation_rate=0.0)

    categories_used = set(s.category for s in signals)
    diversity = len(categories_used) / len(InjectionCategory)
    persistence = min(1.0, len(signals) / max(1, total_events))

    # Escalation: average confidence trend
    confs = [s.confidence for s in signals]
    escalation = 0.0
    if len(confs) >= 2:
        first_avg = statistics.mean(confs[:len(confs) // 2])
        second_avg = statistics.mean(confs[len(confs) // 2:])
        escalation = second_avg - first_avg

    # Sophistication tiers
    has_smuggling = InjectionCategory.INSTRUCTION_SMUGGLING in categories_used
    has_recursive = InjectionCategory.RECURSIVE_INJECTION in categories_used
    has_context = InjectionCategory.CONTEXT_MANIPULATION in categories_used
    avg_conf = statistics.mean(confs)

    if (has_recursive or has_smuggling) and diversity >= 0.5:
        sophistication = "expert"
    elif has_context and len(categories_used) >= 3:
        sophistication = "advanced"
    elif len(categories_used) >= 2 or avg_conf >= 0.7:
        sophistication = "intermediate"
    else:
        sophistication = "naive"

    return AttackerProfile(
        sophistication=sophistication,
        persistence=round(persistence, 3),
        diversity=round(diversity, 3),
        escalation_rate=round(escalation, 3),
    )


def _generate_recommendations(signals: List[InjectionSignal],
                              report: PromptInjectionReport) -> List[str]:
    """Generate actionable recommendations based on findings."""
    recs = []
    categories_found = set(s.category for s in signals)

    if not signals:
        recs.append("No injection attempts detected. Continue monitoring.")
        return recs

    if InjectionCategory.DIRECT_OVERRIDE in categories_found:
        recs.append("Add instruction-hierarchy enforcement — system prompts should be immutable regardless of user input")

    if InjectionCategory.ROLE_HIJACK in categories_found:
        recs.append("Implement persona anchoring — periodically re-assert the agent's identity in the conversation")

    if InjectionCategory.CONTEXT_MANIPULATION in categories_found:
        recs.append("Validate message boundaries — sanitize inputs for chat-ML delimiters and fake system tags")

    if InjectionCategory.INSTRUCTION_SMUGGLING in categories_found:
        recs.append("Add input pre-processing — decode and inspect encoded payloads before passing to the model")

    if InjectionCategory.GOAL_DIVERSION in categories_found:
        recs.append("Implement task-focus guardrails — detect and reject off-topic diversions from the assigned task")

    if InjectionCategory.PRIVILEGE_ESCALATION in categories_found:
        recs.append("Enforce principle of least privilege — never grant elevated access based on conversational claims")

    if InjectionCategory.INFORMATION_EXTRACTION in categories_found:
        recs.append("Protect internal state — ensure system prompts, API keys, and architecture details are never revealed")

    if InjectionCategory.RECURSIVE_INJECTION in categories_found:
        recs.append("Add summarization sanitization — scrub instruction-like content before forwarding or summarizing")

    if report.attacker_profile.sophistication in ("advanced", "expert"):
        recs.append("URGENT: Sophisticated attack detected — consider rate-limiting or blocking this session")

    if report.trend == TrendDirection.ESCALATING:
        recs.append("Attack intensity is escalating — trigger automated session termination policy")

    return recs


def _generate_insights(signals: List[InjectionSignal],
                       report: PromptInjectionReport) -> List[str]:
    """Generate autonomous insights."""
    insights = []

    if not signals:
        insights.append("Session appears clean — no adversarial intent detected")
        return insights

    n = len(signals)
    cats = set(s.category for s in signals)

    insights.append(f"Detected {n} injection signal(s) across {len(cats)} categor{'y' if len(cats) == 1 else 'ies'}")

    # Source analysis
    sources = Counter(s.source_field.split(".")[0] for s in signals)
    top_source = sources.most_common(1)[0]
    if top_source[0] == "tool_output":
        insights.append("⚠ Indirect injection via tool outputs detected — the threat may originate from external data sources")
    elif top_source[0] == "parameter":
        insights.append("Injection payloads found in tool parameters — input validation at the parameter level is needed")
    elif top_source[0] in ("user_input", "message"):
        insights.append(f"Primary attack vector: direct {top_source[0]} ({top_source[1]} signals)")

    if report.attacker_profile.persistence >= 0.3:
        insights.append(f"Attacker persistence is high ({report.attacker_profile.persistence:.0%}) — repeated attempts suggest automated probing")

    if report.trend == TrendDirection.ESCALATING:
        insights.append("Attack sophistication increases over time — likely an adaptive adversary testing boundaries")
    elif report.trend == TrendDirection.DEESCALATING:
        insights.append("Attack intensity decreased — adversary may have given up or switched to subtler techniques")

    if InjectionCategory.RECURSIVE_INJECTION in cats:
        insights.append("Recursive injection payloads detected — these can survive summarization and infect downstream agents")

    # Multi-vector correlation
    if len(cats) >= 4:
        insights.append(f"Multi-vector attack using {len(cats)} categories — this is a sophisticated, coordinated attempt")

    return insights


# ── Main detector class ─────────────────────────────────────────────


class PromptInjectionDetector:
    """Autonomous prompt injection detector for agent sessions.

    Args:
        min_confidence: Minimum pattern match confidence to report (default: 0.5).
        scan_tool_outputs: Also scan tool outputs for indirect injection (default: True).
    """

    def __init__(self, min_confidence: float = 0.5,
                 scan_tool_outputs: bool = True):
        self.min_confidence = min_confidence
        self.scan_tool_outputs = scan_tool_outputs

    def analyze(self, session: Dict[str, Any]) -> PromptInjectionReport:
        """Analyze a session for prompt injection attempts.

        Args:
            session: A dict with ``"id"`` and ``"events"`` keys.

        Returns:
            A ``PromptInjectionReport`` with findings.
        """
        session_id = session.get("id", "unknown")
        events = session.get("events") or []
        report = PromptInjectionReport(
            session_id=session_id,
            total_events=len(events),
        )

        all_signals: List[InjectionSignal] = []
        scanned = 0

        for idx, event in enumerate(events):
            text_fields = _extract_text_fields(event)
            if not text_fields:
                continue
            scanned += 1

            for field_name, text in text_fields:
                # Skip tool outputs if not scanning them
                if not self.scan_tool_outputs and field_name.startswith("tool_output"):
                    continue

                for category, patterns in _ALL_PATTERNS.items():
                    matches = _scan_patterns(text, category, patterns,
                                             self.min_confidence)
                    for conf, desc, matched in matches:
                        signal = InjectionSignal(
                            category=category,
                            event_index=idx,
                            confidence=conf,
                            threat_level=_compute_threat_level(conf),
                            description=desc,
                            evidence=matched[:100],
                            matched_pattern=desc,
                            source_field=field_name,
                        )
                        all_signals.append(signal)

        # Deduplicate: keep highest confidence per (category, event_index, source_field)
        seen = {}
        for sig in all_signals:
            key = (sig.category, sig.event_index, sig.source_field)
            if key not in seen or sig.confidence > seen[key].confidence:
                seen[key] = sig

        deduped = sorted(seen.values(), key=lambda s: s.event_index)
        report.signal_timeline = deduped
        report.events_scanned = scanned
        report.injection_signals_detected = len(deduped)

        # Category profiles
        cat_signals: Dict[InjectionCategory, List[InjectionSignal]] = defaultdict(list)
        for sig in deduped:
            cat_signals[sig.category].append(sig)

        for cat in InjectionCategory:
            sigs = cat_signals.get(cat, [])
            profile = CategoryProfile(category=cat)
            if sigs:
                confs = [s.confidence for s in sigs]
                profile.signal_count = len(sigs)
                profile.max_confidence = max(confs)
                profile.avg_confidence = statistics.mean(confs)
                profile.threat_level = _compute_threat_level(max(confs))
                profile.first_seen_index = min(s.event_index for s in sigs)
                profile.last_seen_index = max(s.event_index for s in sigs)
                # Escalation within category
                if len(sigs) >= 2:
                    mid = len(sigs) // 2
                    first_avg = statistics.mean(c.confidence for c in sigs[:mid])
                    second_avg = statistics.mean(c.confidence for c in sigs[mid:])
                    profile.escalating = second_avg > first_avg * 1.2
            report.category_profiles[cat.value] = profile

        # Overall scores
        report.threat_level = _aggregate_threat(deduped)
        report.trend = _detect_trend(deduped, len(events))
        report.attacker_profile = _infer_attacker_profile(deduped, len(events))

        # Safety score: 100 minus weighted penalties
        if deduped:
            penalty = sum(s.threat_level.weight * 8 for s in deduped)
            # Volume penalty
            penalty += min(20, len(deduped) * 2)
            # Diversity penalty
            unique_cats = len(set(s.category for s in deduped))
            penalty += unique_cats * 3
            report.safety_score = max(0.0, min(100.0, 100.0 - penalty))
        else:
            report.safety_score = 100.0

        # Grade
        score = report.safety_score
        if score >= 90:
            report.grade = "A"
        elif score >= 75:
            report.grade = "B"
        elif score >= 55:
            report.grade = "C"
        elif score >= 35:
            report.grade = "D"
        else:
            report.grade = "F"

        # Recommendations and insights
        report.recommendations = _generate_recommendations(deduped, report)
        report.autonomous_insights = _generate_insights(deduped, report)

        return report
