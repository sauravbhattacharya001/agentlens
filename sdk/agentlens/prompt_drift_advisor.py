"""Agentic prompt-drift advisor for AgentLens.

:class:`PromptDriftAdvisor` answers: "Have the prompts/system messages
flowing through our agents drifted from a known-good baseline?"

It compares two streams of :class:`agentlens.AgentEvent` (or plain dicts /
attr-bearing objects) and emits a per-prompt-key drift report covering
length shifts, token overlap, instruction-count changes, security
keyword churn, and version forks.

Sibling to :class:`CostAttributionAdvisor`,
:class:`AgentLoopDetector`, :class:`TraceCompletionAdvisor`,
:class:`ModelMigrationAdvisor`, :class:`SLOBurnRateAdvisor`,
:class:`AlertRuleSynthesizer`, :class:`IncidentRiskRadar`, and
:class:`SamplingAdvisor`.

The advisor is *pure*: it never mutates inputs, makes no network calls,
and depends only on the standard library.  Deterministic given an
injectable clock.
"""

from __future__ import annotations

import copy
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class PromptDriftVerdict(Enum):
    HEALTHY = "healthy"
    MINOR_DRIFT = "minor_drift"
    SIGNIFICANT_DRIFT = "significant_drift"
    SECURITY_DRIFT = "security_drift"
    VERSION_FORK = "version_fork"
    NEW_PROMPT = "new_prompt"
    RETIRED_PROMPT = "retired_prompt"
    INSUFFICIENT_DATA = "insufficient_data"


class PromptDriftPriority(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class PromptDriftGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class PromptDriftIssueCode(Enum):
    LENGTH_INCREASE_LARGE = "length_increase_large"
    LENGTH_DECREASE_LARGE = "length_decrease_large"
    TOKEN_OVERLAP_LOW = "token_overlap_low"
    INSTRUCTION_COUNT_CHANGED = "instruction_count_changed"
    SECURITY_KEYWORD_APPEARED = "security_keyword_appeared"
    SECURITY_KEYWORD_DISAPPEARED = "security_keyword_disappeared"
    KEY_INSTRUCTION_REMOVED = "key_instruction_removed"
    NEW_PROMPT_KEY = "new_prompt_key"
    RETIRED_PROMPT_KEY = "retired_prompt_key"
    HIGH_VARIANCE = "high_variance"
    INSUFFICIENT_DATA = "insufficient_data"


class RiskAppetite(Enum):
    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PromptDriftFinding:
    code: PromptDriftIssueCode
    severity: int  # 0-100
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "severity": self.severity,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class KeywordSignal:
    keyword: str
    baseline_freq: float
    current_freq: float
    delta_pct: float

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "baseline_freq": round(self.baseline_freq, 4),
            "current_freq": round(self.current_freq, 4),
            "delta_pct": round(self.delta_pct, 2),
        }


@dataclass(frozen=True)
class PromptDriftSnapshot:
    key: str
    baseline_count: int
    current_count: int
    length_mean_baseline: float
    length_mean_current: float
    length_delta_pct: float
    token_overlap_jaccard: float
    instruction_count_baseline: float
    instruction_count_current: float
    keyword_signal_changes: Tuple[KeywordSignal, ...]
    verdict: PromptDriftVerdict
    drift_score: int
    priority: PromptDriftPriority
    issues: Tuple[PromptDriftFinding, ...]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "baseline_count": self.baseline_count,
            "current_count": self.current_count,
            "length_mean_baseline": round(self.length_mean_baseline, 2),
            "length_mean_current": round(self.length_mean_current, 2),
            "length_delta_pct": round(self.length_delta_pct, 2),
            "token_overlap_jaccard": round(self.token_overlap_jaccard, 4),
            "instruction_count_baseline": round(self.instruction_count_baseline, 2),
            "instruction_count_current": round(self.instruction_count_current, 2),
            "keyword_signal_changes": [k.to_dict() for k in self.keyword_signal_changes],
            "verdict": self.verdict.value,
            "drift_score": self.drift_score,
            "priority": self.priority.value,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class PromptDriftPlaybookAction:
    id: str
    priority: PromptDriftPriority
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_keys: Tuple[str, ...] = ()
    suggested_value: Optional[Any] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "priority": self.priority.value,
            "label": self.label,
            "reason": self.reason,
            "owner": self.owner,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "related_keys": list(self.related_keys),
        }
        if self.suggested_value is not None:
            d["suggested_value"] = self.suggested_value
        return d


@dataclass(frozen=True)
class PromptDriftPortfolio:
    total_keys: int
    drift_score_mean: float
    drift_score_max: int
    verdict: PromptDriftVerdict
    grade: PromptDriftGrade
    headline: str

    def to_dict(self) -> dict:
        return {
            "total_keys": self.total_keys,
            "drift_score_mean": round(self.drift_score_mean, 2),
            "drift_score_max": self.drift_score_max,
            "verdict": self.verdict.value,
            "grade": self.grade.value,
            "headline": self.headline,
        }


@dataclass(frozen=True)
class PromptDriftReport:
    portfolio: PromptDriftPortfolio
    snapshots: Tuple[PromptDriftSnapshot, ...]
    playbook: Tuple[PromptDriftPlaybookAction, ...]
    insights: Tuple[str, ...]
    generated_at: datetime
    grade: PromptDriftGrade

    def to_dict(self) -> dict:
        return {
            "portfolio": self.portfolio.to_dict(),
            "snapshots": [s.to_dict() for s in self.snapshots],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": list(self.insights),
            "generated_at": self.generated_at.isoformat(),
            "grade": self.grade.value,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, default=str)

    def to_text(self) -> str:
        lines = []
        p = self.portfolio
        lines.append(p.headline)
        lines.append("")
        lines.append(
            f"keys={p.total_keys} mean_drift={p.drift_score_mean:.1f} "
            f"max_drift={p.drift_score_max} verdict={p.verdict.value} grade={p.grade.value}"
        )
        lines.append("")
        if self.snapshots:
            lines.append("Drifted prompts:")
            for s in self.snapshots:
                lines.append(
                    f"  [{s.priority.value}] {s.key}: {s.verdict.value} "
                    f"score={s.drift_score} jaccard={s.token_overlap_jaccard:.2f} "
                    f"len_delta={s.length_delta_pct:+.1f}%"
                )
        else:
            lines.append("Drifted prompts: (none)")
        lines.append("")
        lines.append("Playbook:")
        if self.playbook:
            for a in self.playbook:
                lines.append(f"  [{a.priority.value}] {a.id}: {a.label} ({a.owner})")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Insights:")
        if self.insights:
            for i in self.insights:
                lines.append(f"  - {i}")
        else:
            lines.append("  (none)")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        p = self.portfolio
        lines = [f"# Prompt Drift Report", "", f"_{p.headline}_", ""]
        lines.append("## Summary")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        lines.append(f"| total_keys | {p.total_keys} |")
        lines.append(f"| drift_score_mean | {p.drift_score_mean:.2f} |")
        lines.append(f"| drift_score_max | {p.drift_score_max} |")
        lines.append(f"| verdict | {p.verdict.value} |")
        lines.append(f"| grade | {p.grade.value} |")
        lines.append("")
        lines.append("## Drifted prompts")
        lines.append("")
        lines.append("| priority | key | verdict | score | jaccard | len_delta_pct |")
        lines.append("|---|---|---|---|---|---|")
        if self.snapshots:
            for s in self.snapshots:
                lines.append(
                    f"| {s.priority.value} | {s.key} | {s.verdict.value} | "
                    f"{s.drift_score} | {s.token_overlap_jaccard:.2f} | "
                    f"{s.length_delta_pct:+.1f} |"
                )
        else:
            lines.append("| - | (no drift) | - | - | - | - |")
        lines.append("")
        lines.append("## Playbook")
        lines.append("")
        lines.append("| priority | id | label | owner | reason |")
        lines.append("|---|---|---|---|---|")
        if self.playbook:
            for a in self.playbook:
                lines.append(
                    f"| {a.priority.value} | {a.id} | {a.label} | {a.owner} | "
                    f"{a.reason} |"
                )
        else:
            lines.append("| - | - | (no actions) | - | - |")
        lines.append("")
        lines.append("## Insights")
        lines.append("")
        if self.insights:
            for i in self.insights:
                lines.append(f"- {i}")
        else:
            lines.append("- (none)")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


_SECURITY_APPEARED_KEYWORDS = (
    "ignore previous",
    "jailbreak",
    "disregard",
    "system override",
    "developer mode",
)

_SECURITY_DISAPPEARED_KEYWORDS = (
    "must",
    "never",
    "always",
)

_TRACKED_KEYWORDS = (
    "json",
    "markdown",
    "table",
    "cite",
    "source",
    "tool",
    "function",
    "step",
    "step-by-step",
    "do not",
    "must",
    "never",
    "always",
    "ignore previous",
    "jailbreak",
    "system",
    "role",
    "you are",
    "assistant",
)

_IMPERATIVE_VERBS = {
    "do",
    "use",
    "return",
    "include",
    "exclude",
    "avoid",
    "never",
    "always",
    "ensure",
    "verify",
    "check",
    "list",
    "summarize",
    "explain",
    "answer",
    "write",
    "format",
    "respond",
    "output",
    "provide",
    "generate",
    "make",
    "follow",
    "must",
    "should",
    "consider",
    "remember",
    "begin",
    "end",
    "stop",
    "call",
    "produce",
    "give",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.\)])\s+", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_APPETITE_MULT = {
    RiskAppetite.CAUTIOUS: 1.15,
    RiskAppetite.BALANCED: 1.0,
    RiskAppetite.AGGRESSIVE: 0.85,
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _coerce_event(ev: Any) -> dict:
    if ev is None:
        return {}
    if isinstance(ev, dict):
        return copy.deepcopy(ev)
    dump = getattr(ev, "model_dump", None)
    if callable(dump):
        try:
            return dump()  # type: ignore[no-any-return]
        except Exception:
            pass
    d = getattr(ev, "__dict__", None)
    if isinstance(d, dict):
        return copy.deepcopy(d)
    # Last resort: pull a few known attrs.
    out = {}
    for attr in ("event_id", "session_id", "event_type", "timestamp", "model",
                 "tokens_in", "tokens_out", "input", "output", "metadata"):
        if hasattr(ev, attr):
            out[attr] = getattr(ev, attr)
    return out


def _extract_prompt_text(event: dict) -> str:
    meta = event.get("metadata") or {}
    for key in ("system_prompt", "prompt", "system_message"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # Fall back to input.messages[0].content
    inp = event.get("input")
    if isinstance(inp, dict):
        msgs = inp.get("messages")
        if isinstance(msgs, list) and msgs:
            first = msgs[0]
            if isinstance(first, dict):
                c = first.get("content")
                if isinstance(c, str):
                    return c
        for k in ("system_prompt", "prompt"):
            v = inp.get(k)
            if isinstance(v, str):
                return v
    direct = event.get("prompt") or event.get("system_prompt")
    if isinstance(direct, str):
        return direct
    return ""


def _default_key(event: dict) -> str:
    m = event.get("model") or "unknown"
    meta = event.get("metadata") or {}
    tool = meta.get("tool") or meta.get("tool_name") or event.get("tool") or ""
    if tool:
        return f"{m}::{tool}"
    return str(m)


def _tokenize(text: str) -> list:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _top_token_set(prompts: list, top_n: int = 100) -> set:
    counter: Counter = Counter()
    for p in prompts:
        counter.update(_tokenize(p))
    return {tok for tok, _ in counter.most_common(top_n)}


def _instruction_count(text: str) -> int:
    if not text:
        return 0
    bullets = len(_BULLET_RE.findall(text))
    sents = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    imps = 0
    for s in sents:
        first = s.split()[0].lower() if s.split() else ""
        first = first.strip(",.:;!?\"'`")
        if first in _IMPERATIVE_VERBS:
            imps += 1
    return bullets + imps


def _keyword_freq(prompts: list) -> dict:
    if not prompts:
        return {kw: 0.0 for kw in _TRACKED_KEYWORDS}
    out = {}
    n = len(prompts)
    for kw in _TRACKED_KEYWORDS:
        kw_l = kw.lower()
        hits = sum(1 for p in prompts if kw_l in p.lower())
        out[kw] = hits / n
    return out


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


@dataclass
class PromptDriftOptions:
    risk_appetite: RiskAppetite = RiskAppetite.BALANCED
    key_fn: Optional[Callable[[dict], str]] = None
    min_events: int = 5
    length_large_delta_pct: float = 25.0
    jaccard_low_threshold: float = 0.40
    instruction_count_delta_pct: float = 30.0
    keyword_drop_pct: float = 40.0


class PromptDriftAdvisor:
    """Detects drift between baseline and current prompt fleets."""

    def __init__(
        self,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        key_fn: Optional[Callable[[dict], str]] = None,
    ):
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._key_fn = key_fn

    def analyze(
        self,
        baseline_events: Iterable[Any],
        current_events: Iterable[Any],
        options: Optional[PromptDriftOptions] = None,
    ) -> PromptDriftReport:
        opts = options or PromptDriftOptions()
        key_fn = opts.key_fn or self._key_fn or _default_key

        # Coerce + bucket prompts by key.
        baseline_by_key: dict = defaultdict(list)
        current_by_key: dict = defaultdict(list)
        for ev in baseline_events or []:
            d = _coerce_event(ev)
            text = _extract_prompt_text(d)
            if not text:
                continue
            baseline_by_key[key_fn(d)].append(text)
        for ev in current_events or []:
            d = _coerce_event(ev)
            text = _extract_prompt_text(d)
            if not text:
                continue
            current_by_key[key_fn(d)].append(text)

        all_keys = sorted(set(baseline_by_key.keys()) | set(current_by_key.keys()))
        snapshots = []
        for key in all_keys:
            b_prompts = baseline_by_key.get(key, [])
            c_prompts = current_by_key.get(key, [])
            snapshots.append(self._analyze_key(key, b_prompts, c_prompts, opts))

        # Sort: priority asc (P0 first), then drift_score desc, then key asc.
        prio_order = {p: i for i, p in enumerate(
            [PromptDriftPriority.P0, PromptDriftPriority.P1,
             PromptDriftPriority.P2, PromptDriftPriority.P3])}
        snapshots.sort(key=lambda s: (prio_order[s.priority], -s.drift_score, s.key))

        portfolio = self._portfolio(snapshots, opts)
        playbook = self._playbook(snapshots, portfolio, opts)
        insights = self._insights(snapshots, portfolio)

        return PromptDriftReport(
            portfolio=portfolio,
            snapshots=tuple(snapshots),
            playbook=tuple(playbook),
            insights=tuple(insights),
            generated_at=self._now_fn(),
            grade=portfolio.grade,
        )

    # ------------------------------------------------------------------ #
    # Per-key analysis
    # ------------------------------------------------------------------ #

    def _analyze_key(
        self,
        key: str,
        b_prompts: list,
        c_prompts: list,
        opts: PromptDriftOptions,
    ) -> PromptDriftSnapshot:
        b_count = len(b_prompts)
        c_count = len(c_prompts)

        b_lengths = [len(p) for p in b_prompts]
        c_lengths = [len(p) for p in c_prompts]
        b_mean = statistics.fmean(b_lengths) if b_lengths else 0.0
        c_mean = statistics.fmean(c_lengths) if c_lengths else 0.0
        length_delta_pct = 0.0
        if b_mean > 0 and c_mean > 0:
            length_delta_pct = (c_mean - b_mean) / b_mean * 100.0
        elif c_mean > 0 and b_mean == 0:
            length_delta_pct = 100.0
        elif b_mean > 0 and c_mean == 0:
            length_delta_pct = -100.0

        # Jaccard over top-100 tokens.
        b_tokens = _top_token_set(b_prompts) if b_prompts else set()
        c_tokens = _top_token_set(c_prompts) if c_prompts else set()
        if not b_tokens and not c_tokens:
            jaccard = 1.0
        elif not b_tokens or not c_tokens:
            jaccard = 0.0
        else:
            inter = len(b_tokens & c_tokens)
            union = len(b_tokens | c_tokens)
            jaccard = inter / union if union else 1.0

        # Instruction counts.
        b_ic = statistics.fmean([_instruction_count(p) for p in b_prompts]) if b_prompts else 0.0
        c_ic = statistics.fmean([_instruction_count(p) for p in c_prompts]) if c_prompts else 0.0

        # Keyword frequencies.
        b_kw = _keyword_freq(b_prompts)
        c_kw = _keyword_freq(c_prompts)
        movers = []
        for kw in _TRACKED_KEYWORDS:
            bf = b_kw.get(kw, 0.0)
            cf = c_kw.get(kw, 0.0)
            if bf == 0 and cf == 0:
                continue
            if bf == 0:
                delta = 100.0
            else:
                delta = (cf - bf) / bf * 100.0
            movers.append(KeywordSignal(kw, bf, cf, delta))
        movers.sort(key=lambda k: (-abs(k.delta_pct), k.keyword))
        keyword_signal_changes = tuple(movers[:5])

        # Variance (current side).
        high_variance = False
        if c_lengths and len(c_lengths) >= 3:
            sd = statistics.pstdev(c_lengths)
            if c_mean > 0 and sd > 0.5 * c_mean:
                high_variance = True

        # Build issues.
        issues = []
        is_new = b_count == 0 and c_count > 0
        is_retired = c_count == 0 and b_count > 0
        insufficient = (
            not is_new and not is_retired
            and (b_count < opts.min_events or c_count < opts.min_events)
        )

        if is_new:
            issues.append(PromptDriftFinding(
                PromptDriftIssueCode.NEW_PROMPT_KEY, 55,
                f"prompt key '{key}' only in current ({c_count} events)"))
        if is_retired:
            issues.append(PromptDriftFinding(
                PromptDriftIssueCode.RETIRED_PROMPT_KEY, 45,
                f"prompt key '{key}' only in baseline ({b_count} events)"))
        if insufficient:
            issues.append(PromptDriftFinding(
                PromptDriftIssueCode.INSUFFICIENT_DATA, 20,
                f"baseline={b_count} current={c_count} below min_events={opts.min_events}"))

        # Security keyword shifts.
        sec_appeared = False
        sec_disappeared = False
        for kw in _SECURITY_APPEARED_KEYWORDS:
            if b_kw.get(kw, 0.0) < 0.05 and c_kw.get(kw, 0.0) >= 0.10:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.SECURITY_KEYWORD_APPEARED, 90,
                    f"new security-sensitive phrase '{kw}' appeared "
                    f"(baseline={b_kw.get(kw,0.0):.2f} current={c_kw.get(kw,0.0):.2f})"))
                sec_appeared = True
        for kw in _SECURITY_DISAPPEARED_KEYWORDS:
            if b_kw.get(kw, 0.0) >= 0.30 and c_kw.get(kw, 0.0) < 0.10:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.SECURITY_KEYWORD_DISAPPEARED, 70,
                    f"guardrail keyword '{kw}' largely disappeared "
                    f"(baseline={b_kw.get(kw,0.0):.2f} current={c_kw.get(kw,0.0):.2f})"))
                sec_disappeared = True

        # Length issues (only when both sides have data).
        if b_count >= opts.min_events and c_count >= opts.min_events:
            if length_delta_pct >= opts.length_large_delta_pct:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.LENGTH_INCREASE_LARGE,
                    min(90, 30 + int(length_delta_pct / 2)),
                    f"prompt length grew {length_delta_pct:+.1f}% "
                    f"({b_mean:.0f}->{c_mean:.0f} chars)"))
            elif length_delta_pct <= -opts.length_large_delta_pct:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.LENGTH_DECREASE_LARGE,
                    min(90, 30 + int(abs(length_delta_pct) / 2)),
                    f"prompt length shrank {length_delta_pct:+.1f}% "
                    f"({b_mean:.0f}->{c_mean:.0f} chars)"))

            if jaccard < opts.jaccard_low_threshold:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.TOKEN_OVERLAP_LOW,
                    min(90, 40 + int((opts.jaccard_low_threshold - jaccard) * 100)),
                    f"token overlap jaccard={jaccard:.2f} below "
                    f"threshold={opts.jaccard_low_threshold:.2f}"))

            if b_ic > 0:
                ic_delta = (c_ic - b_ic) / b_ic * 100.0
                if abs(ic_delta) >= opts.instruction_count_delta_pct:
                    issues.append(PromptDriftFinding(
                        PromptDriftIssueCode.INSTRUCTION_COUNT_CHANGED,
                        min(70, 30 + int(abs(ic_delta) / 4)),
                        f"instruction count changed {ic_delta:+.1f}% "
                        f"({b_ic:.1f}->{c_ic:.1f})"))

            # Catalogued keyword removal.
            for kw in _TRACKED_KEYWORDS:
                bf = b_kw.get(kw, 0.0)
                cf = c_kw.get(kw, 0.0)
                if bf >= 0.30:
                    drop = (bf - cf) / bf * 100.0 if bf > 0 else 0.0
                    if drop >= opts.keyword_drop_pct:
                        issues.append(PromptDriftFinding(
                            PromptDriftIssueCode.KEY_INSTRUCTION_REMOVED,
                            min(65, 30 + int(drop / 4)),
                            f"keyword '{kw}' dropped {drop:.0f}% "
                            f"({bf:.2f}->{cf:.2f})"))

            if high_variance:
                issues.append(PromptDriftFinding(
                    PromptDriftIssueCode.HIGH_VARIANCE, 30,
                    f"current prompt length highly variable "
                    f"(stddev > 0.5 * mean={c_mean:.0f})"))

        # Compute drift score (weighted) — only when we have both sides.
        if is_new or is_retired or insufficient:
            drift_score = 0
        else:
            # Component scores all 0-100.
            length_comp = min(100.0, abs(length_delta_pct))
            jaccard_comp = (1.0 - max(0.0, min(1.0, jaccard))) * 100.0
            if b_ic > 0:
                ic_comp = min(100.0, abs((c_ic - b_ic) / b_ic) * 100.0)
            else:
                ic_comp = 50.0 if c_ic > 0 else 0.0
            # Keyword movement: mean of top-5 absolute deltas, capped.
            kw_deltas = [min(100.0, abs(k.delta_pct)) for k in keyword_signal_changes]
            kw_comp = statistics.fmean(kw_deltas) if kw_deltas else 0.0
            sec_comp = 100.0 if (sec_appeared or sec_disappeared) else 0.0
            raw = (
                0.20 * length_comp
                + 0.30 * jaccard_comp
                + 0.15 * ic_comp
                + 0.25 * kw_comp
                + 0.10 * sec_comp
            )
            mult = _APPETITE_MULT[opts.risk_appetite]
            drift_score = int(round(max(0.0, min(100.0, raw * mult))))

        # Verdict ladder.
        if is_new:
            verdict = PromptDriftVerdict.NEW_PROMPT
            priority = PromptDriftPriority.P2
        elif is_retired:
            verdict = PromptDriftVerdict.RETIRED_PROMPT
            priority = PromptDriftPriority.P2
        elif insufficient:
            verdict = PromptDriftVerdict.INSUFFICIENT_DATA
            priority = PromptDriftPriority.P3
        elif sec_appeared or sec_disappeared:
            verdict = PromptDriftVerdict.SECURITY_DRIFT
            priority = PromptDriftPriority.P0
        elif jaccard < opts.jaccard_low_threshold and b_count >= 10 and c_count >= 10:
            verdict = PromptDriftVerdict.VERSION_FORK
            priority = PromptDriftPriority.P1
        elif drift_score >= 60:
            verdict = PromptDriftVerdict.SIGNIFICANT_DRIFT
            priority = PromptDriftPriority.P1
        elif drift_score >= 30:
            verdict = PromptDriftVerdict.MINOR_DRIFT
            priority = PromptDriftPriority.P2
        else:
            verdict = PromptDriftVerdict.HEALTHY
            priority = PromptDriftPriority.P3

        # Sort issues deterministically: severity desc, code asc.
        issues.sort(key=lambda i: (-i.severity, i.code.value))

        return PromptDriftSnapshot(
            key=key,
            baseline_count=b_count,
            current_count=c_count,
            length_mean_baseline=b_mean,
            length_mean_current=c_mean,
            length_delta_pct=length_delta_pct,
            token_overlap_jaccard=jaccard,
            instruction_count_baseline=b_ic,
            instruction_count_current=c_ic,
            keyword_signal_changes=keyword_signal_changes,
            verdict=verdict,
            drift_score=drift_score,
            priority=priority,
            issues=tuple(issues),
        )

    # ------------------------------------------------------------------ #
    # Portfolio + Playbook + Insights
    # ------------------------------------------------------------------ #

    def _portfolio(
        self,
        snapshots: list,
        opts: PromptDriftOptions,
    ) -> PromptDriftPortfolio:
        n = len(snapshots)
        scores = [s.drift_score for s in snapshots]
        mean = statistics.fmean(scores) if scores else 0.0
        mx = max(scores) if scores else 0
        any_sec = any(s.verdict is PromptDriftVerdict.SECURITY_DRIFT for s in snapshots)
        any_p0 = any(s.priority is PromptDriftPriority.P0 for s in snapshots)
        any_p1 = any(s.priority is PromptDriftPriority.P1 for s in snapshots)

        # Top verdict (priority-ordered).
        ladder = [
            PromptDriftVerdict.SECURITY_DRIFT,
            PromptDriftVerdict.VERSION_FORK,
            PromptDriftVerdict.SIGNIFICANT_DRIFT,
            PromptDriftVerdict.MINOR_DRIFT,
            PromptDriftVerdict.NEW_PROMPT,
            PromptDriftVerdict.RETIRED_PROMPT,
            PromptDriftVerdict.INSUFFICIENT_DATA,
            PromptDriftVerdict.HEALTHY,
        ]
        verdict = PromptDriftVerdict.HEALTHY
        for v in ladder:
            if any(s.verdict is v for s in snapshots):
                verdict = v
                break

        # Grade.
        if any_sec or mx >= 80:
            grade = PromptDriftGrade.F
        elif any_p0 or any_p1 or mean >= 55:
            grade = PromptDriftGrade.D
        elif mean >= 35:
            grade = PromptDriftGrade.C
        elif mean >= 18:
            grade = PromptDriftGrade.B
        else:
            grade = PromptDriftGrade.A

        if n == 0:
            headline = "VERDICT: no prompt activity observed in either window."
        else:
            headline = (
                f"VERDICT: grade={grade.value} keys={n} "
                f"mean_drift={mean:.0f} max_drift={mx} verdict={verdict.value}"
            )

        return PromptDriftPortfolio(
            total_keys=n,
            drift_score_mean=mean,
            drift_score_max=mx,
            verdict=verdict,
            grade=grade,
            headline=headline,
        )

    def _playbook(
        self,
        snapshots: list,
        portfolio: PromptDriftPortfolio,
        opts: PromptDriftOptions,
    ) -> list:
        actions: list = []

        sec_keys = [s.key for s in snapshots if s.verdict is PromptDriftVerdict.SECURITY_DRIFT]
        fork_keys = [s.key for s in snapshots if s.verdict is PromptDriftVerdict.VERSION_FORK]
        new_keys = [s.key for s in snapshots if s.verdict is PromptDriftVerdict.NEW_PROMPT]
        retired_keys = [s.key for s in snapshots if s.verdict is PromptDriftVerdict.RETIRED_PROMPT]
        sig_keys = [s.key for s in snapshots if s.verdict is PromptDriftVerdict.SIGNIFICANT_DRIFT]

        if sec_keys:
            actions.append(PromptDriftPlaybookAction(
                id="INVESTIGATE_SECURITY_DRIFT",
                priority=PromptDriftPriority.P0,
                label="Investigate security-sensitive prompt drift",
                reason=f"{len(sec_keys)} prompt key(s) gained/lost security-relevant phrasing",
                owner="security",
                blast_radius=4,
                reversibility="low",
                related_keys=tuple(sorted(sec_keys)),
            ))
        # One rollback action per affected key (sec + fork).
        for k in sorted(set(sec_keys) | set(fork_keys)):
            actions.append(PromptDriftPlaybookAction(
                id=f"ROLLBACK_PROMPT_KEY::{k}",
                priority=PromptDriftPriority.P0 if k in sec_keys else PromptDriftPriority.P1,
                label=f"Roll back prompt for key '{k}' to baseline",
                reason="Significant drift detected vs known-good baseline",
                owner="prompt_owner",
                blast_radius=3,
                reversibility="high",
                related_keys=(k,),
            ))

        if fork_keys:
            actions.append(PromptDriftPlaybookAction(
                id="REVIEW_VERSION_FORK",
                priority=PromptDriftPriority.P1,
                label="Review version forks",
                reason=f"{len(fork_keys)} prompt key(s) show low token overlap with baseline",
                owner="prompt_owner",
                blast_radius=2,
                reversibility="high",
                related_keys=tuple(sorted(fork_keys)),
            ))
        if len(new_keys) >= 2:
            actions.append(PromptDriftPlaybookAction(
                id="AUDIT_NEW_PROMPTS",
                priority=PromptDriftPriority.P1,
                label="Audit newly observed prompt keys",
                reason=f"{len(new_keys)} prompt keys are new in current window",
                owner="qa",
                blast_radius=2,
                reversibility="high",
                related_keys=tuple(sorted(new_keys)),
            ))
        if len(retired_keys) >= 2:
            actions.append(PromptDriftPlaybookAction(
                id="INVESTIGATE_DEPRECATED_PROMPTS",
                priority=PromptDriftPriority.P2,
                label="Investigate deprecated prompts",
                reason=f"{len(retired_keys)} prompt keys disappeared from current window",
                owner="prompt_owner",
                blast_radius=1,
                reversibility="high",
                related_keys=tuple(sorted(retired_keys)),
            ))
        if len(sig_keys) >= 3:
            actions.append(PromptDriftPlaybookAction(
                id="TIGHTEN_PROMPT_GOVERNANCE",
                priority=PromptDriftPriority.P1,
                label="Tighten prompt governance",
                reason=f"{len(sig_keys)} prompt keys show significant drift",
                owner="governance",
                blast_radius=4,
                reversibility="medium",
                related_keys=tuple(sorted(sig_keys)),
            ))

        if opts.risk_appetite is RiskAppetite.CAUTIOUS and portfolio.grade in (
            PromptDriftGrade.C, PromptDriftGrade.D, PromptDriftGrade.F,
        ):
            actions.append(PromptDriftPlaybookAction(
                id="SCHEDULE_PROMPT_REVIEW",
                priority=PromptDriftPriority.P2,
                label="Schedule prompt review",
                reason="Cautious risk appetite + portfolio grade below B",
                owner="qa",
                blast_radius=1,
                reversibility="high",
            ))

        if not actions:
            actions.append(PromptDriftPlaybookAction(
                id="HEALTHY_PROMPTS",
                priority=PromptDriftPriority.P3,
                label="Prompts within baseline tolerance",
                reason="No drift signals exceed thresholds",
                owner="prompt_owner",
                blast_radius=1,
                reversibility="high",
            ))

        # Aggressive trims P3 + lone P2 when P0/P1 present.
        if opts.risk_appetite is RiskAppetite.AGGRESSIVE:
            has_p01 = any(a.priority in (PromptDriftPriority.P0, PromptDriftPriority.P1) for a in actions)
            if has_p01:
                actions = [a for a in actions if a.priority is not PromptDriftPriority.P3]
                p2s = [a for a in actions if a.priority is PromptDriftPriority.P2]
                if len(p2s) == 1:
                    actions = [a for a in actions if a.priority is not PromptDriftPriority.P2]

        # Dedupe by id, keep first.
        seen = set()
        deduped = []
        for a in actions:
            if a.id in seen:
                continue
            seen.add(a.id)
            deduped.append(a)

        prio_order = {PromptDriftPriority.P0: 0, PromptDriftPriority.P1: 1,
                      PromptDriftPriority.P2: 2, PromptDriftPriority.P3: 3}
        deduped.sort(key=lambda a: (prio_order[a.priority], a.id))
        return deduped

    def _insights(self, snapshots: list, portfolio: PromptDriftPortfolio) -> list:
        out = []
        if any(s.verdict is PromptDriftVerdict.SECURITY_DRIFT for s in snapshots):
            out.append("SECURITY_DRIFT_DETECTED")
        forks = sum(1 for s in snapshots if s.verdict is PromptDriftVerdict.VERSION_FORK)
        if forks >= 2:
            out.append(f"MULTIPLE_VERSION_FORKS ({forks})")
        new_c = sum(1 for s in snapshots if s.verdict is PromptDriftVerdict.NEW_PROMPT)
        ret_c = sum(1 for s in snapshots if s.verdict is PromptDriftVerdict.RETIRED_PROMPT)
        if new_c >= 2 or ret_c >= 2:
            out.append(f"PROMPT_CHURN_HIGH (new={new_c} retired={ret_c})")

        # Keyword shift cluster: top mover keyword shared across >=3 keys.
        top_movers: Counter = Counter()
        for s in snapshots:
            if s.keyword_signal_changes:
                top_movers[s.keyword_signal_changes[0].keyword] += 1
        for kw, cnt in top_movers.items():
            if cnt >= 3:
                out.append(f"KEYWORD_SHIFT_CLUSTER ('{kw}' top mover in {cnt} keys)")
                break

        insuf = sum(1 for s in snapshots if s.verdict is PromptDriftVerdict.INSUFFICIENT_DATA)
        if not snapshots or (insuf and insuf == len(snapshots)):
            out.append("INSUFFICIENT_BASELINE_OR_CURRENT")
        elif not out:
            out.append("HEALTHY_PROMPT_FLEET")
        return out


__all__ = [
    "PromptDriftAdvisor",
    "PromptDriftReport",
    "PromptDriftSnapshot",
    "PromptDriftFinding",
    "PromptDriftPlaybookAction",
    "PromptDriftPortfolio",
    "PromptDriftVerdict",
    "PromptDriftPriority",
    "PromptDriftGrade",
    "PromptDriftIssueCode",
    "PromptDriftOptions",
    "RiskAppetite",
    "KeywordSignal",
]
