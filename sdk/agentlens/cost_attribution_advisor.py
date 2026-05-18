"""Agentic cost-attribution advisor for AgentLens.

:class:`CostAttributionAdvisor` answers three questions that ops and
finance teams ask about an AgentLens-instrumented fleet:

* **Who** is burning the budget? (Pareto top spenders, gini concentration.)
* **Where** is the cost concentrated? (Per-(dimension, value) slices,
  rising/spiking trends.)
* **What** should we do about it?  (Chargeback, rate-limit, migrate,
  diversify, retire long-tail.)

This is the next agentic sibling to
:class:`~agentlens.sampling_advisor.SamplingAdvisor`,
:class:`~agentlens.incident_radar.IncidentRiskRadar`,
:class:`~agentlens.alert_rule_synthesizer.AlertRuleSynthesizer`,
:class:`~agentlens.model_migration_advisor.ModelMigrationAdvisor`,
:class:`~agentlens.slo_burn_rate_advisor.SLOBurnRateAdvisor`,
:class:`~agentlens.trace_completion_advisor.TraceCompletionAdvisor`, and
:class:`~agentlens.agent_loop_detector.AgentLoopDetector`.

The advisor is *pure*: it never mutates inputs, makes no network calls,
and uses only the standard library + the existing ``agentlens.budget``
pricing helpers.  It is deterministic given an injectable clock.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from agentlens.budget import estimate_cost as _estimate_cost
from agentlens.budget import get_pricing as _get_pricing


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class AttributionVerdict(Enum):
    TOP_SPENDER = "top_spender"
    HEAVY_USER = "heavy_user"
    SPIKE_DETECTED = "spike_detected"
    NEW_ARRIVAL = "new_arrival"
    LOW_USAGE = "low_usage"
    UNDERUTILIZED = "underutilized"
    NORMAL = "normal"


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


class CostGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class ConcentrationBand(Enum):
    DIVERSE = "diverse"
    CONCENTRATED = "concentrated"
    HIGHLY_CONCENTRATED = "highly_concentrated"


class TrendLabel(Enum):
    RISING = "rising"
    FLAT = "flat"
    DECLINING = "declining"
    INSUFFICIENT_DATA = "insufficient_data"


# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #


@dataclass
class AttributionSlice:
    dimension: str
    value: str
    total_cost_usd: float
    total_tokens: int
    event_count: int
    session_count: int
    error_count: int
    cost_share: float  # 0..1
    avg_cost_per_event: float
    top_models: list[tuple[str, float]]
    trend: TrendLabel
    verdict: AttributionVerdict
    priority: ActionPriority
    reasons: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.dimension}:{self.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "event_count": self.event_count,
            "session_count": self.session_count,
            "error_count": self.error_count,
            "cost_share": round(self.cost_share, 4),
            "avg_cost_per_event": round(self.avg_cost_per_event, 6),
            "top_models": [
                {"model": m, "share": round(s, 4)} for m, s in self.top_models
            ],
            "trend": self.trend.value,
            "verdict": self.verdict.value,
            "priority": self.priority.value,
            "reasons": list(self.reasons),
        }


@dataclass
class PortfolioSummary:
    total_cost_usd: float
    total_events: int
    total_sessions: int
    total_slices: int
    top1_share: float
    gini_coefficient: float
    pareto_top_n: int
    concentration_band: ConcentrationBand

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_events": self.total_events,
            "total_sessions": self.total_sessions,
            "total_slices": self.total_slices,
            "top1_share": round(self.top1_share, 4),
            "gini_coefficient": round(self.gini_coefficient, 4),
            "pareto_top_n": self.pareto_top_n,
            "concentration_band": self.concentration_band.value,
        }


@dataclass
class PlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int  # 1-5
    reversibility: str  # low/medium/high
    related_slice_keys: list[str] = field(default_factory=list)
    suggested_value: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority.value,
            "label": self.label,
            "reason": self.reason,
            "owner": self.owner,
            "blast_radius": self.blast_radius,
            "reversibility": self.reversibility,
            "related_slice_keys": list(self.related_slice_keys),
            "suggested_value": self.suggested_value,
        }


@dataclass
class CostAttributionReport:
    generated_at: datetime
    risk_appetite: RiskAppetite
    dimensions: list[str]
    portfolio: PortfolioSummary
    slices: list[AttributionSlice]
    playbook: list[PlaybookAction]
    insights: list[str]
    grade: CostGrade
    summary: str

    def actions_by_priority(self) -> dict[str, list[PlaybookAction]]:
        out: dict[str, list[PlaybookAction]] = {p.value: [] for p in ActionPriority}
        for a in self.playbook:
            out[a.priority.value].append(a)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "risk_appetite": self.risk_appetite.value,
            "dimensions": list(self.dimensions),
            "portfolio": self.portfolio.to_dict(),
            "slices": [s.to_dict() for s in self.slices],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": list(self.insights),
            "grade": self.grade.value,
            "summary": self.summary,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent, default=str)

    def to_text(self) -> str:
        lines: list[str] = []
        lines.append(
            f"CostAttributionAdvisor grade={self.grade.value} "
            f"risk={self.risk_appetite.value}"
        )
        lines.append(self.summary)
        p = self.portfolio
        lines.append(
            f"  total_cost=${p.total_cost_usd:.4f} events={p.total_events} "
            f"sessions={p.total_sessions} slices={p.total_slices} "
            f"top1_share={p.top1_share:.2%} gini={p.gini_coefficient:.3f} "
            f"pareto_top_n={p.pareto_top_n} band={p.concentration_band.value}"
        )
        if self.slices:
            lines.append("  Top slices:")
            for sl in self.slices[: min(10, len(self.slices))]:
                lines.append(
                    f"    [{sl.priority.value}] {sl.key} "
                    f"${sl.total_cost_usd:.4f} ({sl.cost_share:.1%}) "
                    f"verdict={sl.verdict.value} trend={sl.trend.value}"
                )
        else:
            lines.append("  No slices in window.")
        if self.playbook:
            lines.append("  Playbook:")
            for a in self.playbook:
                lines.append(f"    [{a.priority.value}] {a.id} - {a.label}")
        if self.insights:
            lines.append("  Insights:")
            for ins in self.insights:
                lines.append(f"    - {ins}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(
            f"# CostAttributionAdvisor (grade {self.grade.value})"
        )
        lines.append("")
        lines.append(self.summary)
        lines.append("")
        lines.append("## Summary")
        p = self.portfolio
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Total cost (USD) | ${p.total_cost_usd:.4f} |")
        lines.append(f"| Total events | {p.total_events} |")
        lines.append(f"| Total sessions | {p.total_sessions} |")
        lines.append(f"| Slices | {p.total_slices} |")
        lines.append(f"| Top-1 share | {p.top1_share:.2%} |")
        lines.append(f"| Gini coefficient | {p.gini_coefficient:.3f} |")
        lines.append(f"| Pareto top-N (>=80%) | {p.pareto_top_n} |")
        lines.append(f"| Concentration | {p.concentration_band.value} |")
        lines.append(f"| Risk appetite | {self.risk_appetite.value} |")
        lines.append("")
        lines.append("## Top slices")
        lines.append("")
        if self.slices:
            lines.append(
                "| Key | Cost (USD) | Share | Events | Verdict | Priority | Trend |"
            )
            lines.append("| --- | ---: | ---: | ---: | --- | --- | --- |")
            for sl in self.slices[: min(10, len(self.slices))]:
                lines.append(
                    f"| {sl.key} | ${sl.total_cost_usd:.4f} | "
                    f"{sl.cost_share:.2%} | {sl.event_count} | "
                    f"{sl.verdict.value} | {sl.priority.value} | {sl.trend.value} |"
                )
        else:
            lines.append("_No slices in window._")
        lines.append("")
        lines.append("## Playbook")
        lines.append("")
        if self.playbook:
            lines.append("| Priority | Id | Label | Owner | Blast | Reversibility |")
            lines.append("| --- | --- | --- | --- | ---: | --- |")
            for a in self.playbook:
                lines.append(
                    f"| {a.priority.value} | {a.id} | {a.label} | "
                    f"{a.owner} | {a.blast_radius} | {a.reversibility} |"
                )
        else:
            lines.append("_No actions._")
        lines.append("")
        lines.append("## Insights")
        lines.append("")
        if self.insights:
            for ins in self.insights:
                lines.append(f"- {ins}")
        else:
            lines.append("_No insights._")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _coerce_event(ev: Any) -> dict[str, Any]:
    """Coerce AgentEvent | dict | attr-object into a plain dict copy."""
    if isinstance(ev, dict):
        return copy.deepcopy(ev)
    md = getattr(ev, "model_dump", None)
    if callable(md):
        try:
            return md()  # type: ignore[return-value]
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in (
        "event_id",
        "session_id",
        "event_type",
        "timestamp",
        "model",
        "tokens_in",
        "tokens_out",
        "duration_ms",
        "input_data",
        "output_data",
    ):
        if hasattr(ev, key):
            out[key] = getattr(ev, key)
    # Optional custom tag dict.
    for key in ("tags", "metadata"):
        if hasattr(ev, key):
            out[key] = getattr(ev, key)
    return out


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            # Tolerate trailing 'Z'.
            v = value[:-1] + "+00:00" if value.endswith("Z") else value
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _default_tag_extractor(ev: dict[str, Any]) -> Mapping[str, str]:
    return {
        "session_id": str(ev.get("session_id") or "unknown"),
        "model": str(ev.get("model") or "unknown"),
    }


def _gini(values: Sequence[float]) -> float:
    """Standard Gini coefficient for a list of non-negative values."""
    n = len(values)
    if n == 0:
        return 0.0
    s = sum(values)
    if s <= 0:
        return 0.0
    sv = sorted(values)
    cum = 0.0
    for i, v in enumerate(sv, start=1):
        cum += i * v
    # (2 * sum(i*v) ) / (n * sum(v)) - (n+1)/n
    return max(0.0, min(1.0, (2 * cum) / (n * s) - (n + 1) / n))


def _slope_per_day(buckets: list[tuple[datetime, float]]) -> Optional[float]:
    """Simple least-squares slope (USD per day). None if fewer than 2 buckets."""
    if len(buckets) < 2:
        return None
    t0 = buckets[0][0]
    xs = [(b[0] - t0).total_seconds() / 86400.0 for b in buckets]
    ys = [b[1] for b in buckets]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class CostAttributionAdvisor:
    """Attribute fleet spend across user-defined dimensions and recommend actions.

    Parameters
    ----------
    dimensions:
        Iterable of dimension names to attribute by.  Defaults to ``("model",)``.
    tag_extractor:
        Callable mapping an event dict to ``{dimension: value}``.  Missing
        dimensions are reported as ``"unknown"``.  Defaults to a mapper that
        returns ``session_id`` and ``model`` from each event.
    risk_appetite:
        ``"cautious" | "balanced" | "aggressive"``.
    now_fn:
        Optional clock used as the "now" reference for trend / new-arrival
        detection.  If omitted, the maximum event timestamp is used.
    pricing:
        Optional ``{model: {input, output, currency}}`` override used as a
        fallback when :func:`agentlens.budget.get_pricing` returns ``None``.
    top_n:
        Number of slices to keep in the output (default 10).
    """

    _APPETITE_MULT = {
        RiskAppetite.CAUTIOUS: 1.10,
        RiskAppetite.BALANCED: 1.00,
        RiskAppetite.AGGRESSIVE: 0.85,
    }

    def __init__(
        self,
        *,
        dimensions: Iterable[str] = ("model",),
        tag_extractor: Optional[Callable[[Mapping[str, Any]], Mapping[str, str]]] = None,
        risk_appetite: "str | RiskAppetite" = "balanced",
        now_fn: Optional[Callable[[], datetime]] = None,
        pricing: Optional[Mapping[str, Mapping[str, float]]] = None,
        top_n: int = 10,
    ) -> None:
        self.dimensions: list[str] = sorted({d for d in dimensions if d})
        if not self.dimensions:
            self.dimensions = ["model"]
        self.tag_extractor = tag_extractor or _default_tag_extractor
        self.risk_appetite = RiskAppetite.parse(risk_appetite)
        self._now_fn = now_fn
        self._pricing = pricing or {}
        self.top_n = max(1, int(top_n))

    # ------------------------------------------------------------------ #
    def _now(self, fallback: Optional[datetime]) -> datetime:
        if self._now_fn is not None:
            n = self._now_fn()
            return n if n.tzinfo else n.replace(tzinfo=timezone.utc)
        if fallback is not None:
            return fallback if fallback.tzinfo else fallback.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def _cost_for(self, ev: dict[str, Any]) -> float:
        tokens_in = int(ev.get("tokens_in") or 0)
        tokens_out = int(ev.get("tokens_out") or 0)
        if tokens_in == 0 and tokens_out == 0:
            return 0.0
        model = ev.get("model")
        c = _estimate_cost(tokens_in, tokens_out, model)
        if c > 0:
            return c
        # Fallback to user-supplied pricing override.
        if model and model in self._pricing:
            p = self._pricing[model]
            return (
                (tokens_in / 1000.0) * float(p.get("input", 0.0))
                + (tokens_out / 1000.0) * float(p.get("output", 0.0))
            )
        return 0.0

    # ------------------------------------------------------------------ #
    def analyze(self, events: Iterable[Any]) -> CostAttributionReport:
        coerced: list[dict[str, Any]] = [_coerce_event(e) for e in events]

        # Determine "now" — caller's clock or max event timestamp.
        max_ts: Optional[datetime] = None
        for ev in coerced:
            ts = _to_datetime(ev.get("timestamp"))
            if ts is not None and (max_ts is None or ts > max_ts):
                max_ts = ts
        now = self._now(max_ts)

        # Compute per-slice aggregates.
        # Key: (dimension, value) -> dict of running stats.
        aggregates: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "cost": 0.0,
                "tokens": 0,
                "events": 0,
                "sessions": set(),
                "errors": 0,
                "models": Counter(),
                "first_ts": None,
                "last_ts": None,
                "daily": defaultdict(float),  # date -> cost
            }
        )

        total_cost = 0.0
        total_events = 0
        total_sessions: set[str] = set()

        for ev in coerced:
            cost = self._cost_for(ev)
            total_cost += cost
            total_events += 1
            session_id = str(ev.get("session_id") or "")
            if session_id:
                total_sessions.add(session_id)

            tags = dict(self.tag_extractor(ev) or {})
            ts = _to_datetime(ev.get("timestamp"))
            day = ts.date() if ts else None
            tokens = int(ev.get("tokens_in") or 0) + int(ev.get("tokens_out") or 0)
            is_err = str(ev.get("event_type") or "").lower() == "error"
            model = str(ev.get("model") or "unknown")

            for dim in self.dimensions:
                value = str(tags.get(dim, "unknown"))
                agg = aggregates[(dim, value)]
                agg["cost"] += cost
                agg["tokens"] += tokens
                agg["events"] += 1
                if session_id:
                    agg["sessions"].add(session_id)
                if is_err:
                    agg["errors"] += 1
                if cost > 0 or model != "unknown":
                    agg["models"][model] += cost
                if ts is not None:
                    if agg["first_ts"] is None or ts < agg["first_ts"]:
                        agg["first_ts"] = ts
                    if agg["last_ts"] is None or ts > agg["last_ts"]:
                        agg["last_ts"] = ts
                    if day is not None:
                        agg["daily"][day] += cost

        # Build slices.
        slices: list[AttributionSlice] = []
        for (dim, value), agg in aggregates.items():
            sc = agg["cost"]
            share = (sc / total_cost) if total_cost > 0 else 0.0
            avg = (sc / agg["events"]) if agg["events"] else 0.0

            # Top models by share within the slice.
            top_models: list[tuple[str, float]] = []
            mc = agg["models"]
            mtot = sum(mc.values())
            if mtot > 0:
                for m, c in sorted(mc.items(), key=lambda x: (-x[1], x[0]))[:3]:
                    top_models.append((m, c / mtot))

            # Trend over daily buckets.
            daily_items = sorted(agg["daily"].items())  # (date, cost) asc
            if len(daily_items) < 2:
                trend = TrendLabel.INSUFFICIENT_DATA
                slope = 0.0
            else:
                buckets = [
                    (datetime(d.year, d.month, d.day, tzinfo=timezone.utc), c)
                    for d, c in daily_items
                ]
                slope = _slope_per_day(buckets) or 0.0
                # Threshold scaled by per-slice average to be unitless-ish.
                mean_daily = sum(c for _, c in buckets) / len(buckets)
                rise_threshold = max(1e-9, 0.10 * mean_daily) * self._APPETITE_MULT[
                    self.risk_appetite
                ]
                if slope > rise_threshold:
                    trend = TrendLabel.RISING
                elif slope < -rise_threshold:
                    trend = TrendLabel.DECLINING
                else:
                    trend = TrendLabel.FLAT

            slices.append(
                AttributionSlice(
                    dimension=dim,
                    value=value,
                    total_cost_usd=sc,
                    total_tokens=agg["tokens"],
                    event_count=agg["events"],
                    session_count=len(agg["sessions"]),
                    error_count=agg["errors"],
                    cost_share=share,
                    avg_cost_per_event=avg,
                    top_models=top_models,
                    trend=trend,
                    verdict=AttributionVerdict.NORMAL,  # filled below
                    priority=ActionPriority.P3,
                    reasons=[],
                )
            )

        # Sort by cost desc, key asc.
        slices.sort(key=lambda s: (-s.total_cost_usd, s.key))

        # Pareto top-N.
        cum = 0.0
        pareto_top_n = 0
        for s in slices:
            if total_cost <= 0:
                break
            cum += s.total_cost_usd
            pareto_top_n += 1
            if cum / total_cost >= 0.80:
                break

        # Concentration.
        gini = _gini([s.total_cost_usd for s in slices if s.total_cost_usd > 0])
        if gini < 0.4:
            band = ConcentrationBand.DIVERSE
        elif gini < 0.7:
            band = ConcentrationBand.CONCENTRATED
        else:
            band = ConcentrationBand.HIGHLY_CONCENTRATED

        top1_share = slices[0].cost_share if slices else 0.0

        # Pareto member set.
        pareto_keys = {s.key for s in slices[:pareto_top_n]}

        # Observed time window across all events — used to decide if
        # "first_ts within last 24h" is meaningful (avoids tagging every
        # slice NEW_ARRIVAL when all events share a single timestamp).
        min_first_ts = None
        for agg in aggregates.values():
            ft = agg["first_ts"]
            if ft is not None and (min_first_ts is None or ft < min_first_ts):
                min_first_ts = ft
        observed_window = (
            now - min_first_ts if min_first_ts is not None else timedelta(0)
        )
        new_arrival_enabled = observed_window >= timedelta(hours=24)

        # Classify slices.
        app_mult = self._APPETITE_MULT[self.risk_appetite]
        heavy_threshold = 0.10 * app_mult
        spike_share_threshold = 0.05 * app_mult
        new_arrival_window = timedelta(hours=24)
        low_usage_share_threshold = 0.01 / app_mult  # tighter when aggressive

        for s in slices:
            reasons: list[str] = []
            verdict = AttributionVerdict.NORMAL

            # Find first_ts via aggregates.
            agg = aggregates[(s.dimension, s.value)]
            first_ts: Optional[datetime] = agg["first_ts"]
            is_new = (
                new_arrival_enabled
                and first_ts is not None
                and (now - first_ts) <= new_arrival_window
            )

            if s.trend is TrendLabel.RISING and s.cost_share >= spike_share_threshold:
                verdict = AttributionVerdict.SPIKE_DETECTED
                reasons.append("RISING_TREND")
                reasons.append("MATERIAL_SHARE")
            elif is_new and s.cost_share < 0.40:
                # New arrivals win over generic Pareto membership unless they
                # are already dominating fleet spend (in which case they are
                # genuinely the top spender story).
                verdict = AttributionVerdict.NEW_ARRIVAL
                reasons.append("FIRST_SEEN_LAST_24H")
            elif s.key in pareto_keys and total_cost > 0:
                verdict = AttributionVerdict.TOP_SPENDER
                reasons.append("IN_PARETO_TOP_80")
                if s.cost_share >= heavy_threshold:
                    reasons.append("HIGH_COST_SHARE")
            elif s.cost_share >= heavy_threshold:
                verdict = AttributionVerdict.HEAVY_USER
                reasons.append("HIGH_COST_SHARE")
            elif s.event_count < 5:
                verdict = AttributionVerdict.UNDERUTILIZED
                reasons.append("LOW_EVENT_COUNT")
            elif s.cost_share < low_usage_share_threshold and s.event_count >= 5:
                verdict = AttributionVerdict.LOW_USAGE
                reasons.append("MINIMAL_COST_SHARE")

            if s.error_count > 0 and s.event_count > 0 and (
                s.error_count / s.event_count >= 0.10
            ):
                reasons.append("ELEVATED_ERROR_RATE")

            # Priority by verdict.
            if verdict is AttributionVerdict.SPIKE_DETECTED:
                priority = ActionPriority.P0
            elif verdict in (
                AttributionVerdict.TOP_SPENDER,
                AttributionVerdict.HEAVY_USER,
            ):
                priority = ActionPriority.P1
            elif verdict in (
                AttributionVerdict.NEW_ARRIVAL,
                AttributionVerdict.LOW_USAGE,
            ):
                priority = ActionPriority.P2
            else:
                priority = ActionPriority.P3

            s.verdict = verdict
            s.priority = priority
            s.reasons = sorted(set(reasons))

        # Portfolio summary.
        portfolio = PortfolioSummary(
            total_cost_usd=total_cost,
            total_events=total_events,
            total_sessions=len(total_sessions),
            total_slices=len(slices),
            top1_share=top1_share,
            gini_coefficient=gini,
            pareto_top_n=pareto_top_n,
            concentration_band=band,
        )

        # Cross-fleet playbook.
        playbook = self._build_playbook(slices, portfolio)

        # Insights.
        insights = self._build_insights(slices, portfolio)

        # Grade.
        grade = self._grade(portfolio, slices)

        # Cautious tail.
        if self.risk_appetite is RiskAppetite.CAUTIOUS and grade in (
            CostGrade.C,
            CostGrade.D,
            CostGrade.F,
        ):
            playbook.append(
                PlaybookAction(
                    id="SCHEDULE_COST_REVIEW",
                    priority=ActionPriority.P2,
                    label="Schedule a follow-up cost review",
                    reason=(
                        "Cautious risk appetite + grade "
                        f"{grade.value}: schedule a finance/eng review."
                    ),
                    owner="finance",
                    blast_radius=1,
                    reversibility="high",
                )
            )

        # Aggressive trims P3 fallback + lone P2 when any P0/P1 present.
        if self.risk_appetite is RiskAppetite.AGGRESSIVE:
            has_high = any(
                a.priority in (ActionPriority.P0, ActionPriority.P1)
                for a in playbook
            )
            if has_high:
                playbook = [
                    a for a in playbook
                    if a.priority not in (ActionPriority.P3,)
                ]
                p2s = [a for a in playbook if a.priority is ActionPriority.P2]
                if len(p2s) == 1:
                    playbook = [a for a in playbook if a is not p2s[0]]

        # Final P0-first ordering.
        priority_rank = {p: i for i, p in enumerate(ActionPriority)}
        playbook.sort(key=lambda a: (priority_rank[a.priority], a.id))

        # Keep top_n slices in the report (preserve full count in summary).
        out_slices = slices[: self.top_n]

        # Summary headline.
        if total_cost <= 0:
            summary = (
                "VERDICT: no cost data observed — check pricing tables and "
                "event token instrumentation."
            )
        else:
            summary = (
                f"VERDICT: grade={grade.value} total=${total_cost:.4f} "
                f"top1={top1_share:.1%} gini={gini:.2f} "
                f"band={band.value} actions={len(playbook)}"
            )

        return CostAttributionReport(
            generated_at=now,
            risk_appetite=self.risk_appetite,
            dimensions=self.dimensions,
            portfolio=portfolio,
            slices=out_slices,
            playbook=playbook,
            insights=insights,
            grade=grade,
            summary=summary,
        )

    # ------------------------------------------------------------------ #
    def _build_playbook(
        self,
        slices: list[AttributionSlice],
        portfolio: PortfolioSummary,
    ) -> list[PlaybookAction]:
        out: list[PlaybookAction] = []
        # Convenience filters.
        # ENFORCE_CHARGEBACK fires whenever a slice consumes >=20% of fleet
        # cost regardless of whether the verdict landed on HEAVY_USER or
        # TOP_SPENDER — both are billable concentration risks.
        heavy = [
            s for s in slices
            if s.cost_share >= 0.20
            and s.verdict in (AttributionVerdict.HEAVY_USER, AttributionVerdict.TOP_SPENDER)
        ]
        spikes = [s for s in slices if s.verdict is AttributionVerdict.SPIKE_DETECTED]
        new_arrivals = [s for s in slices if s.verdict is AttributionVerdict.NEW_ARRIVAL]
        underutilized = [s for s in slices if s.verdict is AttributionVerdict.UNDERUTILIZED]

        # P0 — chargeback.
        if heavy:
            keys = sorted(s.key for s in heavy)
            out.append(
                PlaybookAction(
                    id="ENFORCE_CHARGEBACK",
                    priority=ActionPriority.P0,
                    label="Enforce internal chargeback on heavy users",
                    reason=(
                        f"{len(heavy)} slice(s) consume >=20% of fleet cost — "
                        "bill consumers directly to align incentives."
                    ),
                    owner="finance",
                    blast_radius=3,
                    reversibility="high",
                    related_slice_keys=keys,
                )
            )
        # P0 — investigate spike.
        if spikes:
            keys = sorted(s.key for s in spikes)
            out.append(
                PlaybookAction(
                    id="INVESTIGATE_SPIKE",
                    priority=ActionPriority.P0,
                    label="Investigate rising-cost spikes",
                    reason=(
                        f"{len(spikes)} slice(s) show rising cost trend AND "
                        "material share — likely runaway agent or bad prompt."
                    ),
                    owner="service_owner",
                    blast_radius=2,
                    reversibility="high",
                    related_slice_keys=keys,
                )
            )
        # P0 — rate limit top spender.
        if portfolio.top1_share >= 0.40 and slices:
            top = slices[0]
            out.append(
                PlaybookAction(
                    id="RATE_LIMIT_TOP_SPENDER",
                    priority=ActionPriority.P0,
                    label="Rate-limit top spender",
                    reason=(
                        f"Top slice consumes {portfolio.top1_share:.1%} of "
                        "fleet cost — impose per-key/per-user QPS cap."
                    ),
                    owner="platform",
                    blast_radius=4,
                    reversibility="medium",
                    related_slice_keys=[top.key],
                    suggested_value=top.key,
                )
            )

        # P1 — diversify.
        if (
            portfolio.concentration_band is ConcentrationBand.HIGHLY_CONCENTRATED
            and not heavy
            and not spikes
            and portfolio.top1_share < 0.40
        ):
            out.append(
                PlaybookAction(
                    id="DIVERSIFY_WORKLOAD",
                    priority=ActionPriority.P1,
                    label="Diversify workload across consumers",
                    reason=(
                        f"Gini={portfolio.gini_coefficient:.2f} indicates highly "
                        "concentrated spend with no single chargeback target."
                    ),
                    owner="architecture",
                    blast_radius=2,
                    reversibility="high",
                )
            )

        # P1 — migrate dominant model.
        # Aggregate cost by model across all slices.
        if portfolio.total_cost_usd > 0:
            model_costs: Counter = Counter()
            for s in slices:
                for m, share in s.top_models:
                    model_costs[m] += share * s.total_cost_usd
            if model_costs:
                top_model, top_model_cost = model_costs.most_common(1)[0]
                if top_model_cost / portfolio.total_cost_usd >= 0.50:
                    out.append(
                        PlaybookAction(
                            id="MIGRATE_TOP_MODELS",
                            priority=ActionPriority.P1,
                            label=f"Evaluate cheaper alternative to '{top_model}'",
                            reason=(
                                f"Model '{top_model}' accounts for "
                                f"{top_model_cost / portfolio.total_cost_usd:.1%} of "
                                "total cost — pair with ModelMigrationAdvisor."
                            ),
                            owner="platform",
                            blast_radius=3,
                            reversibility="medium",
                            suggested_value=top_model,
                        )
                    )

        # P2 — audit new arrivals.
        if new_arrivals:
            out.append(
                PlaybookAction(
                    id="AUDIT_NEW_ARRIVALS",
                    priority=ActionPriority.P2,
                    label="Audit new cost-incurring entities",
                    reason=(
                        f"{len(new_arrivals)} slice(s) first observed in last 24h — "
                        "confirm intended usage and budgets."
                    ),
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    related_slice_keys=sorted(s.key for s in new_arrivals),
                )
            )
        # P2 — retire long tail.
        if len(underutilized) >= 5:
            out.append(
                PlaybookAction(
                    id="RETIRE_UNDERUTILIZED",
                    priority=ActionPriority.P2,
                    label="Retire chronically underutilized integrations",
                    reason=(
                        f"{len(underutilized)} slice(s) generate <5 events — "
                        "instrumentation cost may exceed value."
                    ),
                    owner="product",
                    blast_radius=1,
                    reversibility="medium",
                    related_slice_keys=sorted(s.key for s in underutilized),
                )
            )

        if not out:
            out.append(
                PlaybookAction(
                    id="HEALTHY_DISTRIBUTION",
                    priority=ActionPriority.P3,
                    label="Cost distribution healthy — maintain observability",
                    reason="No actionable cost-attribution signals in window.",
                    owner="platform",
                    blast_radius=1,
                    reversibility="high",
                )
            )

        return out

    # ------------------------------------------------------------------ #
    def _build_insights(
        self,
        slices: list[AttributionSlice],
        portfolio: PortfolioSummary,
    ) -> list[str]:
        insights: list[str] = []
        if portfolio.total_cost_usd <= 0:
            insights.append("PRICING_UNAVAILABLE")
            return insights
        if portfolio.top1_share >= 0.40:
            insights.append("TOP_HEAVY")
        if portfolio.concentration_band is ConcentrationBand.DIVERSE:
            insights.append("DIVERSE_WORKLOAD")
        if any(s.verdict is AttributionVerdict.SPIKE_DETECTED for s in slices):
            insights.append("RISING_SPEND")
        new_arrivals = sum(
            1 for s in slices if s.verdict is AttributionVerdict.NEW_ARRIVAL
        )
        if new_arrivals >= 2:
            insights.append("NEW_USER_INFLUX")
        underutilized = sum(
            1 for s in slices if s.verdict is AttributionVerdict.UNDERUTILIZED
        )
        if underutilized >= 5:
            insights.append("LONG_TAIL_NOISE")
        # Model lock-in.
        model_costs: Counter = Counter()
        for s in slices:
            for m, share in s.top_models:
                model_costs[m] += share * s.total_cost_usd
        if model_costs:
            top_model, top_model_cost = model_costs.most_common(1)[0]
            if top_model_cost / portfolio.total_cost_usd >= 0.70:
                insights.append(f"MODEL_LOCK_IN:{top_model}")
        return insights

    # ------------------------------------------------------------------ #
    def _grade(
        self,
        portfolio: PortfolioSummary,
        slices: list[AttributionSlice],
    ) -> CostGrade:
        any_spike_p0 = any(
            s.verdict is AttributionVerdict.SPIKE_DETECTED
            and s.priority is ActionPriority.P0
            for s in slices
        )
        if portfolio.gini_coefficient >= 0.85 or portfolio.top1_share >= 0.50:
            return CostGrade.F
        if portfolio.gini_coefficient >= 0.70 or portfolio.top1_share >= 0.30:
            return CostGrade.D
        if portfolio.concentration_band is ConcentrationBand.CONCENTRATED:
            return CostGrade.C
        if portfolio.concentration_band is ConcentrationBand.DIVERSE and not any_spike_p0:
            return CostGrade.A
        return CostGrade.B


__all__ = [
    "AttributionVerdict",
    "ActionPriority",
    "RiskAppetite",
    "CostGrade",
    "ConcentrationBand",
    "TrendLabel",
    "AttributionSlice",
    "PortfolioSummary",
    "PlaybookAction",
    "CostAttributionReport",
    "CostAttributionAdvisor",
]
