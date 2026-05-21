"""Agentic prompt-cache opportunity advisor for AgentLens.

:class:`CacheabilityAdvisor` answers a single, focused question that ops
and finance teams ask once they realise prompt-prefix caching exists:

    "Of the LLM calls we're already paying for, which ones could be
    served from a cached prompt prefix, how much would that save, and
    what should we do about it?"

It is the next agentic sibling to
:class:`~agentlens.cost_attribution_advisor.CostAttributionAdvisor`
(which finds *where* the money goes) and
:class:`~agentlens.cost_optimizer.CostOptimizer` (which suggests model
downgrades).  Cacheability is *orthogonal*: a call may be perfectly
priced on the right model and still leave money on the table because
its prompt prefix is identical to 200 other calls.

The advisor is pure: it never mutates inputs, makes no network calls,
and uses only the standard library plus the existing
``agentlens.budget`` pricing helpers.  Deterministic given an
injectable clock.

Verdict ladder (per (model, prompt-prefix) slice):

* ``HOT_CACHE_CANDIDATE``  -- >= 5 hits sharing a >= 512-token prefix.
* ``WARM_PREFIX_CANDIDATE`` -- >= 3 hits with a >= 256-token prefix.
* ``DUPLICATE_HEAVY``       -- >= 3 byte-identical prompts (response
  cache, not provider prefix cache).
* ``UNIQUE_TAIL``           -- single observation, but length suggests
  a system-prompt prefix worth caching if the same agent hits the
  endpoint again.
* ``SINGLETON``             -- one-off call, nothing to do.
* ``NOT_CACHEABLE``         -- prefix too short / no prompt extracted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from agentlens.budget import get_pricing as _get_pricing


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class CacheabilityVerdict(Enum):
    HOT_CACHE_CANDIDATE = "hot_cache_candidate"
    WARM_PREFIX_CANDIDATE = "warm_prefix_candidate"
    DUPLICATE_HEAVY = "duplicate_heavy"
    UNIQUE_TAIL = "unique_tail"
    SINGLETON = "singleton"
    NOT_CACHEABLE = "not_cacheable"


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


class CacheabilityGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CacheabilityOptions:
    """Tunables for :class:`CacheabilityAdvisor`.

    The defaults are deliberately conservative (mirror published
    OpenAI / Anthropic prompt-cache discount of ~50% on prefix tokens,
    256-token minimum prefix, 1024-token "hot" threshold) so that real
    workloads err on the side of under-promising savings.
    """

    min_prefix_tokens: int = 256
    hot_prefix_tokens: int = 1024
    warm_min_hits: int = 3
    hot_min_hits: int = 5
    duplicate_min_hits: int = 3
    cache_hit_discount: float = 0.5  # share of prefix-token cost saved
    top_n: int = 10
    chars_per_token: float = 4.0  # rough OpenAI heuristic
    prefix_chars_for_signature: int = 4096


@dataclass(frozen=True)
class CacheabilityFinding:
    code: str
    severity: int
    reason: str


@dataclass
class CacheabilitySlice:
    slice_id: str
    model: str
    prefix_preview: str
    prefix_tokens: int
    hit_count: int
    distinct_session_count: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    projected_cost_with_cache_usd: float
    projected_savings_usd: float
    savings_share: float
    verdict: CacheabilityVerdict
    priority: ActionPriority
    findings: list[CacheabilityFinding] = field(default_factory=list)


@dataclass
class PlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_slice_ids: list[str] = field(default_factory=list)
    suggested_value: Optional[str] = None


@dataclass
class CacheabilityPortfolio:
    total_events: int
    total_llm_events: int
    total_cost_usd: float
    projected_savings_usd: float
    projected_savings_share: float
    hot_candidate_count: int
    warm_candidate_count: int
    duplicate_heavy_count: int
    portfolio_grade: CacheabilityGrade
    headline: str


@dataclass
class CacheabilityReport:
    generated_at: datetime
    options: CacheabilityOptions
    risk_appetite: RiskAppetite
    portfolio: CacheabilityPortfolio
    slices: list[CacheabilitySlice]
    playbook: list[PlaybookAction]
    insights: list[str]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_WS_RE = re.compile(r"\s+")


def _coerce_event(ev: Any) -> Mapping[str, Any]:
    """Best-effort view of an event as a read-only mapping."""
    if hasattr(ev, "model_dump"):
        try:
            return ev.model_dump()
        except Exception:  # pragma: no cover - defensive
            pass
    if isinstance(ev, Mapping):
        return copy.deepcopy(dict(ev))
    out: dict[str, Any] = {}
    for key in (
        "event_id",
        "session_id",
        "event_type",
        "timestamp",
        "input_data",
        "output_data",
        "model",
        "tokens_in",
        "tokens_out",
        "duration_ms",
    ):
        if hasattr(ev, key):
            out[key] = getattr(ev, key)
    return out


def _extract_prompt(ev: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(prefix_text, full_text)`` for an event.

    The *prefix* is the part of the prompt that is genuinely shared
    across calls -- typically the system / instructions message.  The
    *full* prompt is the whole thing, used for exact-duplicate
    detection (response cache opportunities).
    """
    prefix = ""
    full = ""
    input_data = ev.get("input_data") or {}
    if isinstance(input_data, Mapping):
        # Standard chat-style payload.
        messages = input_data.get("messages")
        if isinstance(messages, list) and messages:
            parts: list[str] = []
            prefix_parts: list[str] = []
            seen_non_system = False
            for msg in messages:
                if not isinstance(msg, Mapping):
                    continue
                role = str(msg.get("role", "")).lower()
                content = msg.get("content", "")
                if isinstance(content, list):
                    pieces = []
                    for chunk in content:
                        if isinstance(chunk, Mapping):
                            text = chunk.get("text") or chunk.get("content")
                            if isinstance(text, str):
                                pieces.append(text)
                    content_str = "\n".join(pieces)
                else:
                    content_str = str(content) if content is not None else ""
                parts.append(f"{role}: {content_str}")
                # The shareable prefix is the leading system/developer
                # turn(s) plus any contiguous tool-result preamble.
                if not seen_non_system and role in {"system", "developer"}:
                    prefix_parts.append(content_str)
                elif role in {"user", "assistant", "tool"}:
                    seen_non_system = True
            full = "\n".join(parts)
            prefix = "\n".join(prefix_parts)
        if not prefix:
            for key in ("system_prompt", "system_message", "instructions"):
                value = input_data.get(key)
                if isinstance(value, str) and value.strip():
                    prefix = value
                    break
        if not full:
            for key in ("prompt", "system_prompt", "system_message", "instructions"):
                value = input_data.get(key)
                if isinstance(value, str) and value.strip():
                    full = value
                    break
    if not full:
        for key in ("prompt", "system_prompt"):
            value = ev.get(key)
            if isinstance(value, str) and value.strip():
                full = value
                if not prefix:
                    prefix = value
                break
    if not prefix:
        prefix = full
    return prefix, full


def _normalise(text: str) -> str:
    """Collapse whitespace so trivial reformatting doesn't blow up the hash."""
    return _WS_RE.sub(" ", text).strip()


def _prefix_signature(text: str, prefix_chars: int) -> tuple[str, str]:
    """Return (sha256 of prefix, short preview) for grouping."""
    head = text[:prefix_chars]
    digest = hashlib.sha256(head.encode("utf-8", errors="replace")).hexdigest()
    preview = head.replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "..."
    return digest, preview


def _approx_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return max(1, int(round(len(text) / max(0.5, chars_per_token))))


def _is_llm_event(ev: Mapping[str, Any]) -> bool:
    event_type = str(ev.get("event_type") or "").lower()
    if event_type and event_type not in {"llm_call", "llm", "generation"}:
        # Be permissive: if event has a model + tokens it counts.
        if not ev.get("model"):
            return False
        if not (ev.get("tokens_in") or ev.get("tokens_out")):
            return False
    return ev.get("model") is not None


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = _get_pricing(model) or {}
    inp = pricing.get("input")
    out = pricing.get("output")
    if inp is None or out is None:
        return 0.0
    # pricing is $/MTok in agentlens.budget
    return (tokens_in * inp + tokens_out * out) / 1_000_000.0


def _appetite_multiplier(appetite: RiskAppetite) -> tuple[float, float]:
    """(savings_optimism, min_hits_relaxer).

    Cautious shrinks projected savings and demands more hits before
    flagging candidates.  Aggressive does the opposite.
    """
    if appetite is RiskAppetite.CAUTIOUS:
        return 0.85, 1.25
    if appetite is RiskAppetite.AGGRESSIVE:
        return 1.10, 0.75
    return 1.0, 1.0


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class CacheabilityAdvisor:
    """Audit an AgentLens event stream for prompt-cache opportunities."""

    def __init__(
        self,
        options: Optional[CacheabilityOptions] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.options = options or CacheabilityOptions()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def analyze(
        self,
        events: Iterable[Any],
        *,
        risk_appetite: "str | RiskAppetite" = "balanced",
    ) -> CacheabilityReport:
        appetite = RiskAppetite.parse(risk_appetite)
        savings_mult, hits_mult = _appetite_multiplier(appetite)
        opts = self.options

        warm_min_hits = max(2, int(round(opts.warm_min_hits * hits_mult)))
        hot_min_hits = max(warm_min_hits + 1, int(round(opts.hot_min_hits * hits_mult)))
        dup_min_hits = max(2, int(round(opts.duplicate_min_hits * hits_mult)))

        # Bucket entries: each is (event, normalised_prefix, normalised_full).
        groups: dict[tuple[str, str], list[tuple[Mapping[str, Any], str, str]]] = defaultdict(list)
        previews: dict[tuple[str, str], str] = {}

        total_events = 0
        total_llm_events = 0
        total_cost = 0.0

        for raw_ev in events:
            total_events += 1
            ev = _coerce_event(raw_ev)
            if not _is_llm_event(ev):
                continue
            total_llm_events += 1
            model = str(ev.get("model") or "")
            tokens_in = int(ev.get("tokens_in") or 0)
            tokens_out = int(ev.get("tokens_out") or 0)
            total_cost += _cost(model, tokens_in, tokens_out)

            prefix_text, full_text = _extract_prompt(ev)
            prefix_text = _normalise(prefix_text)
            full_text = _normalise(full_text) or prefix_text
            if not prefix_text:
                continue

            digest, preview = _prefix_signature(prefix_text, opts.prefix_chars_for_signature)
            key = (model, digest)
            groups[key].append((ev, prefix_text, full_text))
            previews.setdefault(key, preview)

        slices: list[CacheabilitySlice] = []

        for (model, digest), bucket in groups.items():
            prefix_chars_len = min(
                opts.prefix_chars_for_signature,
                min(len(prefix) for _ev_, prefix, _full in bucket) if bucket else 0,
            )
            prefix_tokens = _approx_tokens(
                "x" * prefix_chars_len, opts.chars_per_token
            )
            hit_count = len(bucket)
            sessions = {str(e.get("session_id") or "") for e, _p, _f in bucket}
            distinct_sessions = len([s for s in sessions if s])

            tokens_in_total = sum(int(e.get("tokens_in") or 0) for e, _p, _f in bucket)
            tokens_out_total = sum(int(e.get("tokens_out") or 0) for e, _p, _f in bucket)
            total_slice_cost = sum(
                _cost(
                    str(e.get("model") or ""),
                    int(e.get("tokens_in") or 0),
                    int(e.get("tokens_out") or 0),
                )
                for e, _p, _f in bucket
            )

            findings: list[CacheabilityFinding] = []

            # Decide verdict
            if prefix_tokens < opts.min_prefix_tokens:
                verdict = (
                    CacheabilityVerdict.SINGLETON
                    if hit_count == 1
                    else CacheabilityVerdict.NOT_CACHEABLE
                )
            elif hit_count >= hot_min_hits and prefix_tokens >= opts.hot_prefix_tokens:
                verdict = CacheabilityVerdict.HOT_CACHE_CANDIDATE
                findings.append(
                    CacheabilityFinding(
                        code="HOT_PREFIX",
                        severity=80,
                        reason=(
                            f"{hit_count} calls share a >={opts.hot_prefix_tokens}-token "
                            f"prompt prefix on {model}"
                        ),
                    )
                )
            elif hit_count >= warm_min_hits:
                verdict = CacheabilityVerdict.WARM_PREFIX_CANDIDATE
                findings.append(
                    CacheabilityFinding(
                        code="WARM_PREFIX",
                        severity=55,
                        reason=(
                            f"{hit_count} calls share a >={opts.min_prefix_tokens}-token "
                            f"prompt prefix on {model}"
                        ),
                    )
                )
            elif hit_count == 1:
                verdict = CacheabilityVerdict.UNIQUE_TAIL
                findings.append(
                    CacheabilityFinding(
                        code="LONG_SYSTEM_PROMPT",
                        severity=20,
                        reason="Single call but prefix is large; cache once a sibling call lands.",
                    )
                )
            else:
                verdict = CacheabilityVerdict.NOT_CACHEABLE

            # Projected savings: prefix tokens get the cache discount for
            # hit_count - 1 calls (first call must populate the cache).
            cacheable_calls = max(0, hit_count - 1)
            avg_tokens_in = (tokens_in_total / hit_count) if hit_count else 0
            shareable_prefix_tokens = min(prefix_tokens, int(avg_tokens_in)) if avg_tokens_in else 0
            per_call_savings = _cost(model, shareable_prefix_tokens, 0) * opts.cache_hit_discount
            projected_savings = per_call_savings * cacheable_calls * savings_mult
            if verdict in (
                CacheabilityVerdict.NOT_CACHEABLE,
                CacheabilityVerdict.SINGLETON,
                CacheabilityVerdict.UNIQUE_TAIL,
            ):
                projected_savings = 0.0
            projected_after = max(0.0, total_slice_cost - projected_savings)
            share = (projected_savings / total_slice_cost) if total_slice_cost > 0 else 0.0

            # Exact-duplicate detection (response cache).
            session_full_hashes = Counter(
                hashlib.sha256(full.encode("utf-8", errors="replace")).hexdigest()
                for _e, _p, full in bucket
            )
            best_dup_count = max(session_full_hashes.values(), default=0)
            if best_dup_count >= dup_min_hits and verdict in (
                CacheabilityVerdict.WARM_PREFIX_CANDIDATE,
                CacheabilityVerdict.HOT_CACHE_CANDIDATE,
                CacheabilityVerdict.NOT_CACHEABLE,
                CacheabilityVerdict.UNIQUE_TAIL,
            ):
                verdict = CacheabilityVerdict.DUPLICATE_HEAVY
                findings.append(
                    CacheabilityFinding(
                        code="EXACT_DUPLICATE",
                        severity=90,
                        reason=(
                            f"{best_dup_count} byte-identical prompts on {model}; "
                            "response-level cache would skip the call entirely."
                        ),
                    )
                )
                # Exact-dup savings: the duplicate calls can be skipped
                # entirely (all input + output tokens) for (best_dup_count - 1).
                dup_skip = best_dup_count - 1
                dup_per_call_cost = total_slice_cost / hit_count if hit_count else 0.0
                projected_savings = max(projected_savings, dup_per_call_cost * dup_skip * savings_mult)
                projected_after = max(0.0, total_slice_cost - projected_savings)
                share = (projected_savings / total_slice_cost) if total_slice_cost > 0 else 0.0

            priority = self._priority_for(verdict, share, hit_count)
            slice_id = f"{model}::{digest[:12]}"
            slices.append(
                CacheabilitySlice(
                    slice_id=slice_id,
                    model=model,
                    prefix_preview=previews.get((model, digest), ""),
                    prefix_tokens=prefix_tokens,
                    hit_count=hit_count,
                    distinct_session_count=distinct_sessions,
                    total_tokens_in=tokens_in_total,
                    total_tokens_out=tokens_out_total,
                    total_cost_usd=round(total_slice_cost, 6),
                    projected_cost_with_cache_usd=round(projected_after, 6),
                    projected_savings_usd=round(projected_savings, 6),
                    savings_share=round(share, 4),
                    verdict=verdict,
                    priority=priority,
                    findings=findings,
                )
            )

        # Sort: largest absolute savings first, then largest hit count.
        slices.sort(
            key=lambda s: (-s.projected_savings_usd, -s.hit_count, s.slice_id)
        )
        top_slices = slices[: opts.top_n]

        total_savings = sum(s.projected_savings_usd for s in slices)
        savings_share = (total_savings / total_cost) if total_cost > 0 else 0.0

        hot_count = sum(1 for s in slices if s.verdict is CacheabilityVerdict.HOT_CACHE_CANDIDATE)
        warm_count = sum(1 for s in slices if s.verdict is CacheabilityVerdict.WARM_PREFIX_CANDIDATE)
        dup_count = sum(1 for s in slices if s.verdict is CacheabilityVerdict.DUPLICATE_HEAVY)

        grade = self._grade(savings_share, hot_count, dup_count, total_llm_events)
        portfolio = CacheabilityPortfolio(
            total_events=total_events,
            total_llm_events=total_llm_events,
            total_cost_usd=round(total_cost, 6),
            projected_savings_usd=round(total_savings, 6),
            projected_savings_share=round(savings_share, 4),
            hot_candidate_count=hot_count,
            warm_candidate_count=warm_count,
            duplicate_heavy_count=dup_count,
            portfolio_grade=grade,
            headline=self._headline(savings_share, hot_count, dup_count, warm_count),
        )

        playbook = self._build_playbook(top_slices, appetite, grade)
        insights = self._build_insights(slices, savings_share, total_cost)

        return CacheabilityReport(
            generated_at=self._now_fn(),
            options=opts,
            risk_appetite=appetite,
            portfolio=portfolio,
            slices=top_slices,
            playbook=playbook,
            insights=insights,
        )

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def to_text(self, report: CacheabilityReport) -> str:
        lines: list[str] = []
        p = report.portfolio
        lines.append(f"Cacheability advisor [{report.risk_appetite.value}]")
        lines.append(p.headline)
        lines.append(
            f"  events={p.total_events} llm_events={p.total_llm_events} "
            f"cost=${p.total_cost_usd:.4f} savings=${p.projected_savings_usd:.4f} "
            f"({p.projected_savings_share * 100:.1f}%) grade={p.portfolio_grade.value}"
        )
        lines.append("")
        lines.append("Top slices:")
        if not report.slices:
            lines.append("  (no LLM activity)")
        for s in report.slices:
            lines.append(
                f"  - [{s.priority.value}] {s.verdict.value} {s.model} "
                f"hits={s.hit_count} prefix={s.prefix_tokens}tok "
                f"savings=${s.projected_savings_usd:.4f} share={s.savings_share * 100:.1f}%"
            )
            lines.append(f"      preview: {s.prefix_preview}")
        lines.append("")
        lines.append("Playbook:")
        if not report.playbook:
            lines.append("  (none)")
        for action in report.playbook:
            lines.append(f"  - [{action.priority.value}] {action.label} :: {action.reason}")
        if report.insights:
            lines.append("")
            lines.append("Insights:")
            for insight in report.insights:
                lines.append(f"  - {insight}")
        return "\n".join(lines)

    def to_markdown(self, report: CacheabilityReport) -> str:
        p = report.portfolio
        lines: list[str] = []
        lines.append(f"## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Events | {p.total_events} |")
        lines.append(f"| LLM events | {p.total_llm_events} |")
        lines.append(f"| Total cost (USD) | ${p.total_cost_usd:.4f} |")
        lines.append(f"| Projected savings (USD) | ${p.projected_savings_usd:.4f} |")
        lines.append(f"| Savings share | {p.projected_savings_share * 100:.1f}% |")
        lines.append(f"| Hot candidates | {p.hot_candidate_count} |")
        lines.append(f"| Warm candidates | {p.warm_candidate_count} |")
        lines.append(f"| Duplicate-heavy | {p.duplicate_heavy_count} |")
        lines.append(f"| Grade | {p.portfolio_grade.value} |")
        lines.append("")
        lines.append("## Top slices")
        lines.append("")
        lines.append(
            "| Slice | Model | Verdict | Hits | Prefix tok | Savings (USD) | Share | Priority |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        if not report.slices:
            lines.append("| _none_ |  |  |  |  |  |  |  |")
        for s in report.slices:
            lines.append(
                f"| {s.slice_id} | {s.model} | {s.verdict.value} | {s.hit_count} | "
                f"{s.prefix_tokens} | ${s.projected_savings_usd:.4f} | "
                f"{s.savings_share * 100:.1f}% | {s.priority.value} |"
            )
        lines.append("")
        lines.append("## Playbook")
        lines.append("")
        lines.append("| Priority | Action | Owner | Reason |")
        lines.append("| --- | --- | --- | --- |")
        if not report.playbook:
            lines.append("| _none_ |  |  |  |")
        for action in report.playbook:
            lines.append(
                f"| {action.priority.value} | {action.label} | {action.owner} | {action.reason} |"
            )
        lines.append("")
        lines.append("## Insights")
        lines.append("")
        if not report.insights:
            lines.append("- (none)")
        for insight in report.insights:
            lines.append(f"- {insight}")
        return "\n".join(lines)

    def to_json(self, report: CacheabilityReport) -> str:
        payload = {
            "generated_at": report.generated_at.isoformat(),
            "risk_appetite": report.risk_appetite.value,
            "options": {
                "min_prefix_tokens": report.options.min_prefix_tokens,
                "hot_prefix_tokens": report.options.hot_prefix_tokens,
                "warm_min_hits": report.options.warm_min_hits,
                "hot_min_hits": report.options.hot_min_hits,
                "duplicate_min_hits": report.options.duplicate_min_hits,
                "cache_hit_discount": report.options.cache_hit_discount,
                "top_n": report.options.top_n,
                "chars_per_token": report.options.chars_per_token,
                "prefix_chars_for_signature": report.options.prefix_chars_for_signature,
            },
            "portfolio": {
                "total_events": report.portfolio.total_events,
                "total_llm_events": report.portfolio.total_llm_events,
                "total_cost_usd": report.portfolio.total_cost_usd,
                "projected_savings_usd": report.portfolio.projected_savings_usd,
                "projected_savings_share": report.portfolio.projected_savings_share,
                "hot_candidate_count": report.portfolio.hot_candidate_count,
                "warm_candidate_count": report.portfolio.warm_candidate_count,
                "duplicate_heavy_count": report.portfolio.duplicate_heavy_count,
                "portfolio_grade": report.portfolio.portfolio_grade.value,
                "headline": report.portfolio.headline,
            },
            "slices": [
                {
                    "slice_id": s.slice_id,
                    "model": s.model,
                    "prefix_preview": s.prefix_preview,
                    "prefix_tokens": s.prefix_tokens,
                    "hit_count": s.hit_count,
                    "distinct_session_count": s.distinct_session_count,
                    "total_tokens_in": s.total_tokens_in,
                    "total_tokens_out": s.total_tokens_out,
                    "total_cost_usd": s.total_cost_usd,
                    "projected_cost_with_cache_usd": s.projected_cost_with_cache_usd,
                    "projected_savings_usd": s.projected_savings_usd,
                    "savings_share": s.savings_share,
                    "verdict": s.verdict.value,
                    "priority": s.priority.value,
                    "findings": [
                        {"code": f.code, "severity": f.severity, "reason": f.reason}
                        for f in s.findings
                    ],
                }
                for s in report.slices
            ],
            "playbook": [
                {
                    "id": a.id,
                    "priority": a.priority.value,
                    "label": a.label,
                    "reason": a.reason,
                    "owner": a.owner,
                    "blast_radius": a.blast_radius,
                    "reversibility": a.reversibility,
                    "related_slice_ids": list(a.related_slice_ids),
                    "suggested_value": a.suggested_value,
                }
                for a in report.playbook
            ],
            "insights": list(report.insights),
        }
        return json.dumps(payload, sort_keys=True, indent=2, default=str)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _priority_for(
        self,
        verdict: CacheabilityVerdict,
        share: float,
        hit_count: int,
    ) -> ActionPriority:
        if verdict is CacheabilityVerdict.DUPLICATE_HEAVY:
            return ActionPriority.P0 if hit_count >= 5 else ActionPriority.P1
        if verdict is CacheabilityVerdict.HOT_CACHE_CANDIDATE:
            return ActionPriority.P0 if share >= 0.30 else ActionPriority.P1
        if verdict is CacheabilityVerdict.WARM_PREFIX_CANDIDATE:
            return ActionPriority.P1 if share >= 0.20 else ActionPriority.P2
        if verdict is CacheabilityVerdict.UNIQUE_TAIL:
            return ActionPriority.P3
        return ActionPriority.P3

    def _grade(
        self,
        savings_share: float,
        hot_count: int,
        dup_count: int,
        total_llm_events: int,
    ) -> CacheabilityGrade:
        if total_llm_events == 0:
            return CacheabilityGrade.A
        if dup_count >= 3 or savings_share >= 0.40:
            return CacheabilityGrade.F
        if hot_count >= 2 or savings_share >= 0.25:
            return CacheabilityGrade.D
        if savings_share >= 0.10:
            return CacheabilityGrade.C
        if savings_share >= 0.03:
            return CacheabilityGrade.B
        return CacheabilityGrade.A

    def _headline(
        self,
        savings_share: float,
        hot_count: int,
        dup_count: int,
        warm_count: int,
    ) -> str:
        if dup_count >= 1:
            return f"Response cache opportunity: {dup_count} duplicate-heavy slice(s)."
        if hot_count >= 1:
            return (
                f"Prompt-prefix cache opportunity: {hot_count} hot slice(s) "
                f"({savings_share * 100:.1f}% projected savings)."
            )
        if warm_count >= 1:
            return (
                f"{warm_count} warm prefix candidate(s); start with prompt-prefix caching."
            )
        return "No significant cache opportunities detected."

    def _build_playbook(
        self,
        slices: Sequence[CacheabilitySlice],
        appetite: RiskAppetite,
        grade: CacheabilityGrade,
    ) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []
        hot = [s for s in slices if s.verdict is CacheabilityVerdict.HOT_CACHE_CANDIDATE]
        warm = [s for s in slices if s.verdict is CacheabilityVerdict.WARM_PREFIX_CANDIDATE]
        dup = [s for s in slices if s.verdict is CacheabilityVerdict.DUPLICATE_HEAVY]
        long_tail = [s for s in slices if s.verdict is CacheabilityVerdict.UNIQUE_TAIL]

        if dup:
            actions.append(
                PlaybookAction(
                    id="enable_response_cache",
                    priority=ActionPriority.P0,
                    label="Enable response cache for byte-identical prompts",
                    reason=(
                        f"{len(dup)} slice(s) repeat the exact same prompt; "
                        "skip the LLM call entirely with a content-addressed cache."
                    ),
                    owner="platform",
                    blast_radius=3,
                    reversibility="high",
                    related_slice_ids=[s.slice_id for s in dup],
                )
            )
        if hot:
            actions.append(
                PlaybookAction(
                    id="turn_on_prompt_prefix_cache",
                    priority=ActionPriority.P0,
                    label="Turn on provider prompt-prefix caching",
                    reason=(
                        f"{len(hot)} slice(s) share >= "
                        f"{self.options.hot_prefix_tokens}-token prefixes; "
                        "enable provider prompt cache (OpenAI/Anthropic/Gemini)."
                    ),
                    owner="agent_dev",
                    blast_radius=2,
                    reversibility="high",
                    related_slice_ids=[s.slice_id for s in hot],
                )
            )
        if warm:
            actions.append(
                PlaybookAction(
                    id="consolidate_system_prompts",
                    priority=ActionPriority.P1,
                    label="Consolidate near-duplicate system prompts",
                    reason=(
                        f"{len(warm)} warm slice(s); merging minor template "
                        "variations into one canonical prefix unlocks the cache."
                    ),
                    owner="agent_dev",
                    blast_radius=2,
                    reversibility="high",
                    related_slice_ids=[s.slice_id for s in warm],
                )
            )
        if hot or warm or dup:
            actions.append(
                PlaybookAction(
                    id="instrument_cache_hit_rate",
                    priority=ActionPriority.P1,
                    label="Instrument cache_hit / cache_miss metrics",
                    reason=(
                        "Track cache_hit token count per call so the savings "
                        "projection can be replaced with a measurement."
                    ),
                    owner="platform",
                    blast_radius=1,
                    reversibility="high",
                    related_slice_ids=[s.slice_id for s in (hot + warm + dup)],
                )
            )
        if long_tail and not (hot or warm or dup):
            actions.append(
                PlaybookAction(
                    id="pre_cache_long_system_prompts",
                    priority=ActionPriority.P2,
                    label="Pre-warm cache for long single-call system prompts",
                    reason=(
                        f"{len(long_tail)} singletons with large prefixes; "
                        "second invocation lands today's savings."
                    ),
                    owner="agent_dev",
                    blast_radius=1,
                    reversibility="high",
                    related_slice_ids=[s.slice_id for s in long_tail],
                )
            )
        if appetite is RiskAppetite.CAUTIOUS and grade in (
            CacheabilityGrade.C,
            CacheabilityGrade.D,
            CacheabilityGrade.F,
        ):
            actions.append(
                PlaybookAction(
                    id="schedule_cache_review",
                    priority=ActionPriority.P2,
                    label="Schedule weekly cacheability review",
                    reason="Cautious mode: keep eyes on cache effectiveness until grade <= B.",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                )
            )
        if not actions:
            actions.append(
                PlaybookAction(
                    id="no_cache_action_needed",
                    priority=ActionPriority.P3,
                    label="Cache posture healthy",
                    reason="No significant prefix-cache or response-cache opportunities found.",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                )
            )
        if appetite is RiskAppetite.AGGRESSIVE:
            # Drop P3 fillers when any real action exists.
            has_real = any(a.priority is not ActionPriority.P3 for a in actions)
            if has_real:
                actions = [a for a in actions if a.priority is not ActionPriority.P3]
        # Stable P0-first ordering.
        priority_rank = {
            ActionPriority.P0: 0,
            ActionPriority.P1: 1,
            ActionPriority.P2: 2,
            ActionPriority.P3: 3,
        }
        actions.sort(key=lambda a: (priority_rank[a.priority], a.id))
        return actions

    def _build_insights(
        self,
        slices: Sequence[CacheabilitySlice],
        savings_share: float,
        total_cost: float,
    ) -> list[str]:
        insights: list[str] = []
        if not slices:
            insights.append("NO_LLM_ACTIVITY")
            return insights
        models = {s.model for s in slices if s.verdict is not CacheabilityVerdict.NOT_CACHEABLE}
        if len(models) == 1 and any(
            s.verdict in (CacheabilityVerdict.HOT_CACHE_CANDIDATE, CacheabilityVerdict.WARM_PREFIX_CANDIDATE)
            for s in slices
        ):
            insights.append(f"SINGLE_MODEL_DOMINATES: {next(iter(models))}")
        if any(s.verdict is CacheabilityVerdict.DUPLICATE_HEAVY for s in slices):
            insights.append("RESPONSE_CACHE_AVAILABLE")
        if savings_share >= 0.30:
            insights.append("LARGE_SAVINGS_HEADROOM")
        elif savings_share >= 0.10:
            insights.append("MODERATE_SAVINGS_HEADROOM")
        long_singletons = [
            s for s in slices
            if s.verdict is CacheabilityVerdict.UNIQUE_TAIL
            and s.prefix_tokens >= self.options.hot_prefix_tokens
        ]
        if len(long_singletons) >= 2:
            insights.append("LONG_SYSTEM_PROMPTS_DETECTED")
        if total_cost == 0.0 and slices:
            insights.append("PRICING_UNAVAILABLE")
        if not insights:
            insights.append("HEALTHY_CACHE_POSTURE")
        return insights


__all__ = [
    "CacheabilityAdvisor",
    "CacheabilityOptions",
    "CacheabilityReport",
    "CacheabilitySlice",
    "CacheabilityFinding",
    "CacheabilityPortfolio",
    "PlaybookAction",
    "CacheabilityVerdict",
    "CacheabilityGrade",
    "ActionPriority",
    "RiskAppetite",
]
