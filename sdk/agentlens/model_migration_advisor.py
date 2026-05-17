"""Agentic Model Migration Advisor for AgentLens.

When you're forced (deprecation, price hike, EOL) or tempted (a cheaper
sibling, a faster model, a new flagship) to migrate one LLM model to
another, the painful question is *which call sites to move first* and
*which to leave alone*.  A naive "swap everywhere" rollout is how teams
quietly regress quality and inflate latency.

:class:`ModelMigrationAdvisor` is the agentic, observability-driven
counterpart to the cheaper-tier-suggester
:class:`~agentlens.cost_optimizer.CostOptimizer`.  Where the optimizer
asks "could you swap to a cheaper tier?", the migration advisor answers
"given that you *must* (or *want to*) swap from model X to model Y,
which of your existing call sites should go first, which should wait,
and which should never migrate?".

For each call site (a stable grouping of past LLM events) it produces:

* a **MigrationVerdict** in ``{MIGRATE_NOW, MIGRATE_SOON, REVIEW,
  PILOT_FIRST, BLOCK}`` with structured reasons,
* a **risk score 0-100** combining per-site complexity, context-window
  fit, tool-use density, latency sensitivity and historical error rate,
* a **0-100 priority** in ``{P0, P1, P2, P3}`` migration order
  (cheapest-first within risk band),
* projected **cost delta** and **latency delta** per site (deltas use
  the SDK's :func:`~agentlens.budget.get_pricing` table when available
  and the per-site empirical latency otherwise),
* a **rollback risk** label
  ``{LOW, MEDIUM, HIGH}`` driven by reversibility (still on old
  pricing? blast radius? was this a tool-heavy decision step?).

Across sites it emits a **playbook** with deduped P0/P1/P2 actions
(``PILOT_BEFORE_MIGRATING``, ``EXTEND_CONTEXT_BEFORE_SWAP``,
``KEEP_ON_LEGACY``, ``CANARY_5_PERCENT``, ``DRAIN_AND_RETIRE``,
``RAISE_TIMEOUTS_FIRST``...) and a portfolio summary with **A-F
overall_grade** based on the share of sites that can migrate cleanly.

The advisor is **pure read** -- it never mutates the tracker, the
pricing table or your events.  All time-dependent fields go through an
injectable ``now()`` callable so reports are reproducible.

Example
-------
::

    from agentlens.model_migration_advisor import ModelMigrationAdvisor

    advisor = ModelMigrationAdvisor()
    report = advisor.recommend(
        events,
        from_model="gpt-4o",
        to_model="gpt-4o-mini",
    )

    print(report.format_text())
    for site in report.sites:
        if site.verdict.name == "MIGRATE_NOW":
            print(f"swap {site.site_id}: -${-site.projected_cost_delta_usd:.2f}/day")

The output of :meth:`ModelMigrationAdvisor.recommend` is byte-stable
across runs given the same inputs and fixed ``now`` (JSON uses
``sort_keys=True`` and ``indent=2``).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

from agentlens.models import AgentEvent

try:  # pragma: no cover - pricing module is part of the SDK but we degrade gracefully
    from agentlens.budget import get_pricing as _get_pricing
except Exception:  # pragma: no cover
    def _get_pricing() -> dict[str, dict[str, float]]:  # type: ignore[misc]
        return {}


__all__ = [
    "MigrationVerdict",
    "MigrationPriority",
    "RollbackRisk",
    "MigrationReason",
    "CallSiteProfile",
    "SitePlan",
    "PlaybookAction",
    "MigrationReport",
    "ModelMigrationAdvisor",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MigrationVerdict(str, Enum):
    """Per-site verdict for the proposed migration."""

    MIGRATE_NOW = "migrate_now"           # safe, cheap, low complexity
    MIGRATE_SOON = "migrate_soon"         # safe but worth a short canary
    PILOT_FIRST = "pilot_first"           # ambiguous - run a percentage canary
    REVIEW = "review"                     # complexity or tools require eyeballs
    BLOCK = "block"                       # do not migrate (context fit, hard tool dep)


class MigrationPriority(str, Enum):
    """Migration order bucket."""

    P0 = "P0"   # do this first / drives biggest win
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RollbackRisk(str, Enum):
    """How painful would a rollback be if quality regresses?"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationReason:
    """A structured, paste-ready reason supporting a site verdict.

    Reasons are stable strings (``code``) plus a human ``message`` so
    downstream UI can group/filter without parsing prose.
    """

    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass
class CallSiteProfile:
    """Stable profile of one call site for the *from_model*.

    A call site is the tuple ``(model, event_type, tool_name or "_none")``
    by default.  All numeric fields are populated from the input events
    and never read external state.
    """

    site_id: str
    model: str
    event_type: str
    tool_name: str | None
    call_count: int
    avg_tokens_in: float
    p95_tokens_in: float
    avg_tokens_out: float
    avg_duration_ms: float
    p95_duration_ms: float
    error_rate: float
    tool_density: float          # share of events that carry a tool_call
    decision_density: float      # share of events that carry a decision_trace
    max_tokens_in: int
    max_tokens_out: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "model": self.model,
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "call_count": self.call_count,
            "avg_tokens_in": round(self.avg_tokens_in, 2),
            "p95_tokens_in": round(self.p95_tokens_in, 2),
            "avg_tokens_out": round(self.avg_tokens_out, 2),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "p95_duration_ms": round(self.p95_duration_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "tool_density": round(self.tool_density, 4),
            "decision_density": round(self.decision_density, 4),
            "max_tokens_in": self.max_tokens_in,
            "max_tokens_out": self.max_tokens_out,
        }


@dataclass
class SitePlan:
    """Migration plan for a single call site."""

    site_id: str
    profile: CallSiteProfile
    verdict: MigrationVerdict
    priority: MigrationPriority
    risk_score: float                       # 0..100, higher = riskier to migrate
    confidence: float                       # 0..1, advisor confidence in the verdict
    projected_cost_per_day_old_usd: float
    projected_cost_per_day_new_usd: float
    projected_cost_delta_usd: float         # negative = saving
    projected_latency_delta_pct: float | None  # negative = faster
    rollback_risk: RollbackRisk
    reasons: list[MigrationReason]
    notes: list[str] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple[int, float, str]:
        """Stable ordering for migration order: priority bucket -> savings -> id."""
        bucket = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[self.priority.value]
        # within bucket, biggest *saving* first; site_id breaks ties deterministically.
        return (bucket, self.projected_cost_delta_usd, self.site_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "profile": self.profile.to_dict(),
            "verdict": self.verdict.value,
            "priority": self.priority.value,
            "risk_score": round(self.risk_score, 2),
            "confidence": round(self.confidence, 3),
            "projected_cost_per_day_old_usd": round(self.projected_cost_per_day_old_usd, 6),
            "projected_cost_per_day_new_usd": round(self.projected_cost_per_day_new_usd, 6),
            "projected_cost_delta_usd": round(self.projected_cost_delta_usd, 6),
            "projected_latency_delta_pct": (
                None if self.projected_latency_delta_pct is None
                else round(self.projected_latency_delta_pct, 2)
            ),
            "rollback_risk": self.rollback_risk.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class PlaybookAction:
    """Cross-site action emitted by the advisor."""

    priority: MigrationPriority
    code: str
    title: str
    owner: str
    site_ids: tuple[str, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority.value,
            "code": self.code,
            "title": self.title,
            "owner": self.owner,
            "site_ids": list(self.site_ids),
            "detail": self.detail,
        }


@dataclass
class MigrationReport:
    """Full report from :meth:`ModelMigrationAdvisor.recommend`."""

    from_model: str
    to_model: str
    generated_at: str
    horizon_days: float
    sites: list[SitePlan]
    playbook: list[PlaybookAction]
    portfolio_cost_per_day_old_usd: float
    portfolio_cost_per_day_new_usd: float
    portfolio_cost_delta_usd: float
    portfolio_grade: str
    insights: list[str]
    risk_appetite: str

    # ------------------------------------------------------------------ utils

    def by_priority(self, priority: MigrationPriority | str) -> list[SitePlan]:
        """Return sites in a priority bucket, in migration order."""
        target = priority.value if isinstance(priority, MigrationPriority) else str(priority)
        return [s for s in self.sites if s.priority.value == target]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_model": self.from_model,
            "to_model": self.to_model,
            "generated_at": self.generated_at,
            "horizon_days": self.horizon_days,
            "sites": [s.to_dict() for s in self.sites],
            "playbook": [a.to_dict() for a in self.playbook],
            "portfolio_cost_per_day_old_usd": round(self.portfolio_cost_per_day_old_usd, 6),
            "portfolio_cost_per_day_new_usd": round(self.portfolio_cost_per_day_new_usd, 6),
            "portfolio_cost_delta_usd": round(self.portfolio_cost_delta_usd, 6),
            "portfolio_grade": self.portfolio_grade,
            "insights": list(self.insights),
            "risk_appetite": self.risk_appetite,
        }

    def format_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    def format_text(self) -> str:
        lines: list[str] = []
        lines.append(
            f"ModelMigrationAdvisor: {self.from_model} -> {self.to_model} "
            f"(risk_appetite={self.risk_appetite}, grade={self.portfolio_grade})"
        )
        delta = self.portfolio_cost_delta_usd
        sign = "-" if delta < 0 else "+"
        lines.append(
            f"  portfolio cost/day: ${self.portfolio_cost_per_day_old_usd:.4f} "
            f"-> ${self.portfolio_cost_per_day_new_usd:.4f} "
            f"({sign}${abs(delta):.4f}/day over {len(self.sites)} site(s))"
        )
        for s in self.sites:
            d = s.projected_cost_delta_usd
            sgn = "-" if d < 0 else "+"
            lat = (
                "n/a"
                if s.projected_latency_delta_pct is None
                else f"{s.projected_latency_delta_pct:+.1f}%"
            )
            lines.append(
                f"  [{s.priority.value}] {s.site_id}: {s.verdict.value} "
                f"(risk {s.risk_score:.0f}/100, cost {sgn}${abs(d):.4f}/day, "
                f"latency {lat}, rollback {s.rollback_risk.value})"
            )
            for r in s.reasons:
                lines.append(f"      - {r.code}: {r.message}")
        if self.playbook:
            lines.append("Playbook:")
            for a in self.playbook:
                lines.append(
                    f"  [{a.priority.value}] {a.code} (owner={a.owner}): {a.title}"
                )
                lines.append(f"      {a.detail}")
                if a.site_ids:
                    lines.append(f"      sites: {', '.join(a.site_ids)}")
        if self.insights:
            lines.append("Insights:")
            for ins in self.insights:
                lines.append(f"  - {ins}")
        return "\n".join(lines)

    def format_markdown(self) -> str:
        out: list[str] = []
        out.append(f"# Model migration: `{self.from_model}` -> `{self.to_model}`")
        out.append("")
        out.append(
            f"- **Grade:** {self.portfolio_grade}  "
            f"- **Risk appetite:** {self.risk_appetite}  "
            f"- **Sites:** {len(self.sites)}  "
            f"- **Generated:** {self.generated_at}"
        )
        delta = self.portfolio_cost_delta_usd
        sign = "-" if delta < 0 else "+"
        out.append(
            f"- **Cost/day:** ${self.portfolio_cost_per_day_old_usd:.4f} -> "
            f"${self.portfolio_cost_per_day_new_usd:.4f} "
            f"({sign}${abs(delta):.4f}/day)"
        )
        out.append("")
        out.append("## Sites (migration order)")
        out.append("")
        out.append(
            "| # | Priority | Site | Verdict | Risk | Cost ${/day} | Latency Δ | Rollback |"
        )
        out.append("|---|----------|------|---------|------|-------------|-----------|----------|")
        for i, s in enumerate(self.sites, start=1):
            d = s.projected_cost_delta_usd
            sgn = "-" if d < 0 else "+"
            lat = (
                "n/a"
                if s.projected_latency_delta_pct is None
                else f"{s.projected_latency_delta_pct:+.1f}%"
            )
            out.append(
                f"| {i} | {s.priority.value} | `{s.site_id}` | "
                f"{s.verdict.value} | {s.risk_score:.0f} | "
                f"{sgn}${abs(d):.4f} | {lat} | {s.rollback_risk.value} |"
            )
        if self.playbook:
            out.append("")
            out.append("## Playbook")
            for a in self.playbook:
                out.append(f"### [{a.priority.value}] {a.title}")
                out.append(f"- **Code:** `{a.code}`")
                out.append(f"- **Owner:** {a.owner}")
                if a.site_ids:
                    out.append(f"- **Sites:** {', '.join(f'`{x}`' for x in a.site_ids)}")
                out.append(f"- {a.detail}")
        if self.insights:
            out.append("")
            out.append("## Insights")
            for ins in self.insights:
                out.append(f"- {ins}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Catalogue of known model traits (used as a fallback when pricing is missing)
# ---------------------------------------------------------------------------


# Conservative best-effort defaults.  These are only consulted when the
# SDK's pricing table doesn't carry an entry for the supplied model name;
# user-supplied overrides via ``recommend(model_specs=...)`` always win.
_DEFAULT_MODEL_SPECS: dict[str, dict[str, float]] = {
    # name -> {"input_per_1m", "output_per_1m", "max_context", "speed_factor"}
    # speed_factor: relative latency multiplier vs gpt-4o baseline (1.0).
    "gpt-4o":         {"input_per_1m": 2.50,  "output_per_1m": 10.00, "max_context": 128_000, "speed_factor": 1.00},
    "gpt-4o-mini":    {"input_per_1m": 0.15,  "output_per_1m": 0.60,  "max_context": 128_000, "speed_factor": 0.70},
    "gpt-4-turbo":    {"input_per_1m": 10.00, "output_per_1m": 30.00, "max_context": 128_000, "speed_factor": 1.20},
    "gpt-3.5-turbo":  {"input_per_1m": 0.50,  "output_per_1m": 1.50,  "max_context": 16_000,  "speed_factor": 0.60},
    "claude-3-5-sonnet": {"input_per_1m": 3.00, "output_per_1m": 15.00, "max_context": 200_000, "speed_factor": 1.10},
    "claude-3-haiku": {"input_per_1m": 0.25,  "output_per_1m": 1.25,  "max_context": 200_000, "speed_factor": 0.65},
    "claude-3-opus":  {"input_per_1m": 15.00, "output_per_1m": 75.00, "max_context": 200_000, "speed_factor": 1.40},
}


# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------


@dataclass
class _ResolvedModel:
    name: str
    input_per_1m: float
    output_per_1m: float
    max_context: int
    speed_factor: float


class ModelMigrationAdvisor:
    """Agentic per-site migration planner.

    Parameters
    ----------
    now:
        Callable returning a timezone-aware ``datetime``.  Overridable
        for deterministic tests.
    risk_appetite:
        ``"cautious"``, ``"balanced"`` (default) or ``"aggressive"``.
        Cautious raises the bar for ``MIGRATE_NOW`` and demotes more
        sites to ``PILOT_FIRST``; aggressive does the opposite.
    """

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        risk_appetite: str = "balanced",
    ) -> None:
        if risk_appetite not in {"cautious", "balanced", "aggressive"}:
            raise ValueError(
                "risk_appetite must be one of: cautious, balanced, aggressive"
            )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._risk_appetite = risk_appetite

    # ------------------------------------------------------------------ public

    def recommend(
        self,
        events: Iterable[AgentEvent],
        *,
        from_model: str,
        to_model: str,
        horizon_days: float = 1.0,
        model_specs: dict[str, dict[str, float]] | None = None,
        site_key: Callable[[AgentEvent], str] | None = None,
    ) -> MigrationReport:
        """Build a :class:`MigrationReport` for the proposed migration.

        Parameters
        ----------
        events:
            Iterable of :class:`AgentEvent` objects to analyse.  Only
            events whose ``model`` equals ``from_model`` are considered.
        from_model:
            The model we are migrating away from.  Must match one or
            more events for the report to be useful.
        to_model:
            The destination model.
        horizon_days:
            Projection horizon, in days, for ``cost/day`` numbers.
            Defaults to 1 day -- i.e. the cost columns mean "if the
            observed traffic continued for one day".
        model_specs:
            Optional dict ``{model_name: {"input_per_1m", "output_per_1m",
            "max_context", "speed_factor"}}`` overriding the SDK pricing
            table and the bundled fallback catalogue.
        site_key:
            Optional callable returning a site identifier per event;
            defaults to ``(model, event_type, tool_name or "_none")``.
        """
        if horizon_days <= 0:
            raise ValueError("horizon_days must be > 0")
        if not from_model or not to_model:
            raise ValueError("from_model and to_model must be non-empty")

        relevant = [
            e for e in events
            if e.model == from_model and getattr(e, "event_type", None) != "error"
        ]
        # We still want to count errors when computing a site's error_rate,
        # so collect those separately keyed off the same site signature.
        all_for_model = [e for e in events if e.model == from_model]

        old_spec = self._resolve(from_model, model_specs)
        new_spec = self._resolve(to_model, model_specs)

        sites_by_id: dict[str, list[AgentEvent]] = {}
        errors_by_id: dict[str, int] = {}
        keyfn = site_key or self._default_site_key
        for ev in all_for_model:
            sid = keyfn(ev)
            sites_by_id.setdefault(sid, [])
            if getattr(ev, "event_type", None) == "error":
                errors_by_id[sid] = errors_by_id.get(sid, 0) + 1
            else:
                sites_by_id[sid].append(ev)

        site_plans: list[SitePlan] = []
        for sid in sorted(sites_by_id.keys()):
            evs = sites_by_id[sid]
            if not evs:
                # site is pure errors - score as REVIEW with high risk
                profile = self._empty_profile(sid, from_model)
                profile.error_rate = 1.0
                plan = self._plan_pure_errors(profile, old_spec, new_spec, horizon_days)
                site_plans.append(plan)
                continue
            profile = self._profile_site(sid, evs, errors=errors_by_id.get(sid, 0))
            plan = self._plan_site(profile, old_spec, new_spec, horizon_days)
            site_plans.append(plan)

        site_plans.sort(key=lambda s: s.sort_key)

        cost_old = sum(s.projected_cost_per_day_old_usd for s in site_plans)
        cost_new = sum(s.projected_cost_per_day_new_usd for s in site_plans)

        playbook = self._build_playbook(site_plans, old_spec, new_spec)
        grade = self._portfolio_grade(site_plans)
        insights = self._insights(site_plans, cost_old, cost_new)

        return MigrationReport(
            from_model=from_model,
            to_model=to_model,
            generated_at=self._now().isoformat(),
            horizon_days=float(horizon_days),
            sites=site_plans,
            playbook=playbook,
            portfolio_cost_per_day_old_usd=cost_old,
            portfolio_cost_per_day_new_usd=cost_new,
            portfolio_cost_delta_usd=cost_new - cost_old,
            portfolio_grade=grade,
            insights=insights,
            risk_appetite=self._risk_appetite,
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _default_site_key(ev: AgentEvent) -> str:
        # Group by (model, tool_name) so that ``error`` events land in
        # the same call site as the underlying ``llm_call`` / tool call
        # they failed against.  Callers who want finer-grained sites can
        # pass their own ``site_key`` callable to ``recommend()``.
        tool = "_none"
        tc = getattr(ev, "tool_call", None)
        if tc is not None:
            tool = getattr(tc, "tool_name", None) or "_none"
        model = getattr(ev, "model", None) or "_none"
        return f"{model}::{tool}"

    def _resolve(
        self,
        name: str,
        overrides: dict[str, dict[str, float]] | None,
    ) -> _ResolvedModel:
        spec: dict[str, float] = {}
        if overrides and name in overrides:
            spec = dict(overrides[name])
        else:
            # try SDK pricing table; it carries cost_per_1m fields.
            try:
                pricing = _get_pricing() or {}
            except Exception:
                pricing = {}
            row = pricing.get(name)
            if row:
                spec["input_per_1m"] = float(row.get("input_cost_per_1m", row.get("input_per_1m", 0.0)))
                spec["output_per_1m"] = float(row.get("output_cost_per_1m", row.get("output_per_1m", 0.0)))
                if "max_context" in row:
                    spec["max_context"] = float(row["max_context"])
            else:
                defaults = _DEFAULT_MODEL_SPECS.get(name, {})
                spec.update(defaults)
        # If still empty, give safe zeros so cost deltas read 0 instead of crashing.
        return _ResolvedModel(
            name=name,
            input_per_1m=float(spec.get("input_per_1m", 0.0)),
            output_per_1m=float(spec.get("output_per_1m", 0.0)),
            max_context=int(spec.get("max_context", 128_000)),
            speed_factor=float(spec.get("speed_factor", 1.0)),
        )

    @staticmethod
    def _percentile(values: Sequence[float], pct: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return float(values[0])
        ordered = sorted(values)
        # nearest-rank percentile -- matches the rest of the SDK.
        k = max(0, min(len(ordered) - 1, int(math.ceil(pct / 100.0 * len(ordered))) - 1))
        return float(ordered[k])

    @staticmethod
    def _empty_profile(sid: str, model: str) -> CallSiteProfile:
        parts = sid.split("::")
        # default key is now ``model::tool``; event_type comes from the
        # observed events only.
        event_type = "generic"
        tool = parts[1] if len(parts) > 1 and parts[1] != "_none" else None
        return CallSiteProfile(
            site_id=sid,
            model=model,
            event_type=event_type,
            tool_name=tool,
            call_count=0,
            avg_tokens_in=0.0,
            p95_tokens_in=0.0,
            avg_tokens_out=0.0,
            avg_duration_ms=0.0,
            p95_duration_ms=0.0,
            error_rate=0.0,
            tool_density=0.0,
            decision_density=0.0,
            max_tokens_in=0,
            max_tokens_out=0,
        )

    def _profile_site(
        self,
        sid: str,
        evs: Sequence[AgentEvent],
        *,
        errors: int,
    ) -> CallSiteProfile:
        toks_in = [int(getattr(e, "tokens_in", 0) or 0) for e in evs]
        toks_out = [int(getattr(e, "tokens_out", 0) or 0) for e in evs]
        durs = [float(getattr(e, "duration_ms", 0.0) or 0.0) for e in evs]
        tool_evts = sum(1 for e in evs if getattr(e, "tool_call", None) is not None)
        dec_evts = sum(1 for e in evs if getattr(e, "decision_trace", None) is not None)
        n = len(evs)
        n_total = n + errors
        first = evs[0]
        return CallSiteProfile(
            site_id=sid,
            model=getattr(first, "model", "") or "",
            event_type=getattr(first, "event_type", "generic") or "generic",
            tool_name=(
                getattr(getattr(first, "tool_call", None), "tool_name", None)
                if getattr(first, "tool_call", None) is not None
                else None
            ),
            call_count=n,
            avg_tokens_in=sum(toks_in) / n if n else 0.0,
            p95_tokens_in=self._percentile(toks_in, 95.0),
            avg_tokens_out=sum(toks_out) / n if n else 0.0,
            avg_duration_ms=sum(durs) / n if n else 0.0,
            p95_duration_ms=self._percentile(durs, 95.0),
            error_rate=(errors / n_total) if n_total else 0.0,
            tool_density=(tool_evts / n) if n else 0.0,
            decision_density=(dec_evts / n) if n else 0.0,
            max_tokens_in=max(toks_in) if toks_in else 0,
            max_tokens_out=max(toks_out) if toks_out else 0,
        )

    # ------------------------------------------------------------------ scoring

    def _risk_score(
        self,
        profile: CallSiteProfile,
        old: _ResolvedModel,
        new: _ResolvedModel,
    ) -> tuple[float, list[MigrationReason]]:
        """Compute 0..100 risk score with structured reasons."""
        reasons: list[MigrationReason] = []
        score = 0.0

        # 1) context-window fit -- if any single call exceeds the new model
        #    we cannot migrate it at all without truncation.
        if profile.max_tokens_in > 0 and profile.max_tokens_in >= new.max_context:
            score += 60
            reasons.append(MigrationReason(
                code="EXCEEDS_NEW_CONTEXT",
                message=(
                    f"observed max_tokens_in={profile.max_tokens_in} >= "
                    f"new max_context={new.max_context}"
                ),
            ))
        elif profile.p95_tokens_in > 0 and profile.p95_tokens_in >= 0.85 * new.max_context:
            score += 20
            reasons.append(MigrationReason(
                code="CONTEXT_PRESSURE",
                message=(
                    f"p95 tokens_in {profile.p95_tokens_in:.0f} is >=85% of "
                    f"new max_context {new.max_context}"
                ),
            ))

        # 2) complexity proxy -- long output averages mean the model is doing
        #    real generation work, not classification.  Cheap-to-cheap is fine
        #    but downgrading complexity is risky.
        downgrade = new.input_per_1m + new.output_per_1m < old.input_per_1m + old.output_per_1m
        complexity_proxy = profile.avg_tokens_out / 200.0  # 200 = "small reply"
        if downgrade and complexity_proxy >= 1.0:
            bonus = min(25.0, complexity_proxy * 10.0)
            score += bonus
            reasons.append(MigrationReason(
                code="COMPLEX_GENERATION",
                message=(
                    f"avg output tokens {profile.avg_tokens_out:.0f}; complex "
                    f"generation may regress on cheaper model"
                ),
            ))

        # 3) tool / decision density -- tool-heavy call sites depend on
        #    structured outputs that change subtly across models.
        if profile.tool_density >= 0.5:
            score += 15
            reasons.append(MigrationReason(
                code="TOOL_HEAVY",
                message=(
                    f"{profile.tool_density:.0%} of events call tools; "
                    "structured-output drift is a known model-swap hazard"
                ),
            ))
        if profile.decision_density >= 0.5:
            score += 10
            reasons.append(MigrationReason(
                code="DECISION_HEAVY",
                message=(
                    f"{profile.decision_density:.0%} of events carry decision "
                    "traces; reasoning chains may shift after the swap"
                ),
            ))

        # 4) historical error rate -- already-flaky sites are bad pilots.
        if profile.error_rate >= 0.10:
            score += 15
            reasons.append(MigrationReason(
                code="HIGH_ERROR_RATE",
                message=(
                    f"baseline error_rate {profile.error_rate:.0%}; stabilise "
                    "before changing models"
                ),
            ))

        # 5) latency sensitivity -- if new model is slower, p95 sensitive
        #    sites carry rollout risk.
        speed_delta = new.speed_factor - old.speed_factor
        if speed_delta > 0 and profile.p95_duration_ms >= 2000:
            score += 10
            reasons.append(MigrationReason(
                code="LATENCY_SENSITIVE",
                message=(
                    f"p95 latency {profile.p95_duration_ms:.0f} ms and target "
                    f"model is {speed_delta:+.2f}x slower"
                ),
            ))

        # risk-appetite shift
        if self._risk_appetite == "cautious":
            score += 5
        elif self._risk_appetite == "aggressive":
            score = max(0.0, score - 5)

        # clamp to [0, 100]
        score = max(0.0, min(100.0, score))
        return score, reasons

    def _verdict_for(
        self,
        risk: float,
        reasons: Sequence[MigrationReason],
    ) -> MigrationVerdict:
        codes = {r.code for r in reasons}
        if "EXCEEDS_NEW_CONTEXT" in codes:
            return MigrationVerdict.BLOCK
        thresholds_now, thresholds_soon, thresholds_pilot = self._thresholds()
        if risk <= thresholds_now:
            return MigrationVerdict.MIGRATE_NOW
        if risk <= thresholds_soon:
            return MigrationVerdict.MIGRATE_SOON
        if risk <= thresholds_pilot:
            return MigrationVerdict.PILOT_FIRST
        return MigrationVerdict.REVIEW

    def _thresholds(self) -> tuple[float, float, float]:
        if self._risk_appetite == "cautious":
            return 10.0, 25.0, 55.0
        if self._risk_appetite == "aggressive":
            return 25.0, 50.0, 80.0
        return 15.0, 35.0, 65.0

    def _priority_for(
        self,
        verdict: MigrationVerdict,
        cost_delta_per_day: float,
    ) -> MigrationPriority:
        # BLOCK / REVIEW never get P0/P1.
        if verdict == MigrationVerdict.BLOCK:
            return MigrationPriority.P3
        if verdict == MigrationVerdict.REVIEW:
            return MigrationPriority.P2
        # cost_delta_per_day is negative when migration saves money.
        if verdict == MigrationVerdict.MIGRATE_NOW:
            return MigrationPriority.P0 if cost_delta_per_day < 0 else MigrationPriority.P1
        if verdict == MigrationVerdict.MIGRATE_SOON:
            return MigrationPriority.P1 if cost_delta_per_day < 0 else MigrationPriority.P2
        # PILOT_FIRST
        return MigrationPriority.P2

    @staticmethod
    def _rollback_risk_for(profile: CallSiteProfile) -> RollbackRisk:
        # tool-heavy or decision-heavy = harder to roll back cleanly,
        # because downstream code may depend on the new model's format.
        if profile.tool_density >= 0.5 or profile.call_count >= 200:
            return RollbackRisk.HIGH
        if profile.decision_density >= 0.25 or profile.call_count >= 50:
            return RollbackRisk.MEDIUM
        return RollbackRisk.LOW

    # ------------------------------------------------------------------ planning

    def _plan_site(
        self,
        profile: CallSiteProfile,
        old: _ResolvedModel,
        new: _ResolvedModel,
        horizon_days: float,
    ) -> SitePlan:
        risk, reasons = self._risk_score(profile, old, new)
        verdict = self._verdict_for(risk, reasons)

        # cost projection: scale per-call cost by call_count, then by
        # horizon_days (call_count itself is treated as "per observation
        # window" so we multiply by horizon_days to get "per day"-equivalent
        # under the user's chosen horizon).
        per_call_old = self._per_call_cost(profile, old)
        per_call_new = self._per_call_cost(profile, new)
        scale = profile.call_count * float(horizon_days)
        cost_old = per_call_old * scale
        cost_new = per_call_new * scale
        cost_delta = cost_new - cost_old

        if profile.avg_duration_ms > 0:
            latency_delta_pct = (
                (new.speed_factor / old.speed_factor) - 1.0
            ) * 100.0
        else:
            latency_delta_pct = None

        rollback = self._rollback_risk_for(profile)
        priority = self._priority_for(verdict, cost_delta)

        notes: list[str] = []
        # confidence: small sample sizes get demoted; cautious bumps doubt.
        confidence = 0.5 + min(0.4, profile.call_count / 200.0)
        if self._risk_appetite == "cautious":
            confidence = max(0.4, confidence - 0.1)
        if profile.call_count < 5:
            notes.append("sample size < 5: profile is noisy; treat verdict as provisional")

        return SitePlan(
            site_id=profile.site_id,
            profile=profile,
            verdict=verdict,
            priority=priority,
            risk_score=risk,
            confidence=round(min(1.0, max(0.0, confidence)), 3),
            projected_cost_per_day_old_usd=cost_old,
            projected_cost_per_day_new_usd=cost_new,
            projected_cost_delta_usd=cost_delta,
            projected_latency_delta_pct=latency_delta_pct,
            rollback_risk=rollback,
            reasons=reasons,
            notes=notes,
        )

    def _plan_pure_errors(
        self,
        profile: CallSiteProfile,
        old: _ResolvedModel,
        new: _ResolvedModel,
        horizon_days: float,
    ) -> SitePlan:
        reasons = [MigrationReason(
            code="ONLY_ERRORS_OBSERVED",
            message="every observed call at this site errored; stabilise before migrating",
        )]
        return SitePlan(
            site_id=profile.site_id,
            profile=profile,
            verdict=MigrationVerdict.REVIEW,
            priority=MigrationPriority.P2,
            risk_score=80.0,
            confidence=0.3,
            projected_cost_per_day_old_usd=0.0,
            projected_cost_per_day_new_usd=0.0,
            projected_cost_delta_usd=0.0,
            projected_latency_delta_pct=None,
            rollback_risk=RollbackRisk.MEDIUM,
            reasons=reasons,
            notes=["no successful calls in window; advisor lacks token profile"],
        )

    @staticmethod
    def _per_call_cost(profile: CallSiteProfile, spec: _ResolvedModel) -> float:
        return (
            profile.avg_tokens_in / 1_000_000.0 * spec.input_per_1m
            + profile.avg_tokens_out / 1_000_000.0 * spec.output_per_1m
        )

    # ------------------------------------------------------------------ playbook

    def _build_playbook(
        self,
        sites: Sequence[SitePlan],
        old: _ResolvedModel,
        new: _ResolvedModel,
    ) -> list[PlaybookAction]:
        actions: list[PlaybookAction] = []

        blocked = tuple(sorted(s.site_id for s in sites if s.verdict == MigrationVerdict.BLOCK))
        pilot = tuple(sorted(s.site_id for s in sites if s.verdict == MigrationVerdict.PILOT_FIRST))
        review = tuple(sorted(s.site_id for s in sites if s.verdict == MigrationVerdict.REVIEW))
        migrate_now = tuple(sorted(s.site_id for s in sites if s.verdict == MigrationVerdict.MIGRATE_NOW))
        migrate_soon = tuple(sorted(s.site_id for s in sites if s.verdict == MigrationVerdict.MIGRATE_SOON))

        if blocked:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P0,
                code="KEEP_ON_LEGACY",
                title="Keep BLOCK sites on the legacy model",
                owner="platform",
                site_ids=blocked,
                detail=(
                    f"{len(blocked)} site(s) exceed `{new.name}`'s context window "
                    "or have hard incompatibilities; route them to the legacy "
                    f"`{old.name}` until the destination model raises context limits."
                ),
            ))
        if migrate_now:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P0,
                code="DRAIN_AND_RETIRE",
                title="Drain MIGRATE_NOW sites first to bank early savings",
                owner="platform",
                site_ids=migrate_now,
                detail=(
                    f"swap {len(migrate_now)} low-risk call site(s) to `{new.name}`; "
                    "monitor for 24h then close the migration ticket."
                ),
            ))
        if pilot:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P1,
                code="CANARY_5_PERCENT",
                title="Run a 5% canary on PILOT_FIRST sites",
                owner="oncall",
                site_ids=pilot,
                detail=(
                    f"shadow `{new.name}` against `{old.name}` for {len(pilot)} site(s); "
                    "auto-promote when error_rate, latency p95 and tool-call success "
                    "are within 5% of baseline."
                ),
            ))
        if migrate_soon:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P1,
                code="SCHEDULE_NEXT_WINDOW",
                title="Schedule MIGRATE_SOON sites into the next maintenance window",
                owner="platform",
                site_ids=migrate_soon,
                detail=(
                    f"{len(migrate_soon)} site(s) are safe but benefit from a brief "
                    "watch period; group them in the next planned rollout."
                ),
            ))
        if review:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P2,
                code="HUMAN_REVIEW",
                title="Eyeball REVIEW sites before any migration",
                owner="model-owner",
                site_ids=review,
                detail=(
                    f"{len(review)} site(s) have ambiguous signal (high tool density, "
                    "elevated errors, or noisy samples); a human should inspect "
                    "before scheduling."
                ),
            ))

        # context-pressure sites that aren't already BLOCK get a P1 "extend context first"
        ctx_pressure = tuple(sorted(
            s.site_id
            for s in sites
            if any(r.code == "CONTEXT_PRESSURE" for r in s.reasons)
            and s.verdict != MigrationVerdict.BLOCK
        ))
        if ctx_pressure:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P1,
                code="EXTEND_CONTEXT_BEFORE_SWAP",
                title="Reduce prompt size on context-pressured sites",
                owner="prompt-eng",
                site_ids=ctx_pressure,
                detail=(
                    f"{len(ctx_pressure)} site(s) sit within 15% of `{new.name}`'s "
                    "context budget at p95; compress prompts or shard before "
                    "migrating to avoid silent truncation."
                ),
            ))

        # latency-sensitive + slower-target -> raise timeouts first
        lat = tuple(sorted(
            s.site_id
            for s in sites
            if any(r.code == "LATENCY_SENSITIVE" for r in s.reasons)
        ))
        if lat and new.speed_factor > old.speed_factor:
            actions.append(PlaybookAction(
                priority=MigrationPriority.P1,
                code="RAISE_TIMEOUTS_FIRST",
                title="Raise client timeouts before swap",
                owner="platform",
                site_ids=lat,
                detail=(
                    f"`{new.name}` is ~{(new.speed_factor / max(old.speed_factor, 1e-9)) - 1.0:+.0%} "
                    "slower than the legacy model; bump SLA timeouts +25% on the "
                    f"{len(lat)} affected site(s) before turning traffic over."
                ),
            ))

        # de-dup: keep highest-priority entry per (code, site_ids)
        seen: dict[tuple[str, tuple[str, ...]], PlaybookAction] = {}
        for a in actions:
            key = (a.code, a.site_ids)
            cur = seen.get(key)
            if cur is None or _p_rank(a.priority) < _p_rank(cur.priority):
                seen[key] = a
        deduped = list(seen.values())
        deduped.sort(key=lambda a: (_p_rank(a.priority), a.code))
        return deduped

    # ------------------------------------------------------------------ summary

    @staticmethod
    def _portfolio_grade(sites: Sequence[SitePlan]) -> str:
        if not sites:
            return "A"
        total = len(sites)
        clean = sum(1 for s in sites if s.verdict in (
            MigrationVerdict.MIGRATE_NOW, MigrationVerdict.MIGRATE_SOON
        ))
        blocked = sum(1 for s in sites if s.verdict == MigrationVerdict.BLOCK)
        clean_pct = clean / total
        if blocked / total >= 0.20:
            return "F"
        if clean_pct >= 0.90:
            return "A"
        if clean_pct >= 0.75:
            return "B"
        if clean_pct >= 0.55:
            return "C"
        if clean_pct >= 0.35:
            return "D"
        return "F"

    @staticmethod
    def _insights(
        sites: Sequence[SitePlan],
        cost_old: float,
        cost_new: float,
    ) -> list[str]:
        out: list[str] = []
        if not sites:
            return out
        total = len(sites)
        migrate_now = sum(1 for s in sites if s.verdict == MigrationVerdict.MIGRATE_NOW)
        blocked = sum(1 for s in sites if s.verdict == MigrationVerdict.BLOCK)
        pilot = sum(1 for s in sites if s.verdict == MigrationVerdict.PILOT_FIRST)
        if cost_old > 0:
            saving = cost_old - cost_new
            pct = saving / cost_old * 100.0
            sign = "saves" if saving >= 0 else "raises"
            out.append(
                f"{sign} ${abs(saving):.4f}/day ({pct:+.1f}%) at observed traffic"
            )
        out.append(f"{migrate_now}/{total} sites can migrate immediately")
        if pilot:
            out.append(f"{pilot}/{total} site(s) require a canary before promotion")
        if blocked:
            out.append(
                f"{blocked}/{total} site(s) BLOCKED -- keep on legacy until destination "
                "context window grows"
            )
        # Top saver headline -- biggest single absolute saving.
        savers = [s for s in sites if s.projected_cost_delta_usd < 0]
        if savers:
            biggest = min(savers, key=lambda s: s.projected_cost_delta_usd)
            out.append(
                f"top saver: `{biggest.site_id}` ({biggest.projected_cost_delta_usd:+.4f} USD/day)"
            )
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _p_rank(p: MigrationPriority) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[p.value]
