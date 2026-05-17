"""Agentic cross-module pre-incident risk radar for AgentLens.

The individual AgentLens analyzers (anomaly, drift, error_fingerprint,
retry_tracker, latency, budget, health) each see one slice of reality.
:class:`IncidentRiskRadar` fuses signals across whichever of them are
available into a single **0-100 pre-incident risk score**, classifies
the situation into a :class:`RiskBand` (CALM/WATCH/ELEVATED/HIGH/CRITICAL),
and emits a ranked P0/P1/P2 pre-emptive action playbook.

This is the next step on AgentLens' agency ladder:

1. Cross-system awareness — combine signals no single module can see.
2. Recommendation — suggest concrete pre-emptive moves with owner +
   blast-radius + reversibility metadata.
3. (Future) Autonomous mitigation — wire the same actions to runtime
   knobs (samplers, rate limiters, circuit breakers).

The radar gracefully degrades: pass only the reports you have. Missing
signals are simply not included in the fused score. It is deterministic
and depends only on the stdlib + pydantic (already an AgentLens dep).

Example
-------
::

    from agentlens import IncidentRiskRadar, RadarInputs

    radar = IncidentRiskRadar(risk_appetite="cautious")
    report = radar.assess(RadarInputs(
        anomaly_report=anomaly_report,
        budget_report=budget_report,
        retry_report=retry_report,
        window_label="last_15_min",
    ))
    print(report.summary)
    print(report.render_markdown())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from agentlens.anomaly import AnomalyReport
from agentlens.budget import BudgetReport
from agentlens.drift import DriftReport
from agentlens.error_fingerprint import ErrorReport
from agentlens.health import HealthReport
from agentlens.latency import SessionReport as LatencySessionReport
from agentlens.retry_tracker import RetryReport


# --------------------------------------------------------------------------- #
# Enums and value types
# --------------------------------------------------------------------------- #


class RiskBand(Enum):
    """Coarse-grained band for a fused risk score (0-100)."""

    CALM = "calm"            # 0-19
    WATCH = "watch"          # 20-39
    ELEVATED = "elevated"    # 40-59
    HIGH = "high"            # 60-79
    CRITICAL = "critical"    # 80-100

    @classmethod
    def from_score(cls, score: float) -> "RiskBand":
        if score >= 80:
            return cls.CRITICAL
        if score >= 60:
            return cls.HIGH
        if score >= 40:
            return cls.ELEVATED
        if score >= 20:
            return cls.WATCH
        return cls.CALM


class ActionPriority(Enum):
    """Priority of a pre-emptive action."""

    P0 = "P0"  # do now, blocking
    P1 = "P1"  # do this shift
    P2 = "P2"  # do this week

    @property
    def rank(self) -> int:
        return {"P0": 0, "P1": 1, "P2": 2}[self.value]


@dataclass
class RiskSignal:
    """One per-source contribution to the fused risk score."""

    source: str
    score: float  # 0-100
    weight: float  # normalised across present signals; sums to 1
    leading: bool  # True if signal typically precedes incidents
    evidence: list[str] = field(default_factory=list)
    suggested_action_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "score": round(self.score, 2),
            "weight": round(self.weight, 4),
            "leading": self.leading,
            "evidence": list(self.evidence),
            "suggested_action_keys": list(self.suggested_action_keys),
        }


@dataclass
class RiskAction:
    """A ranked pre-emptive action."""

    key: str
    title: str
    priority: ActionPriority
    reason: str
    blast_radius: int  # 1=tiny .. 5=org-wide
    reversible: bool
    owner: str
    related_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "priority": self.priority.value,
            "reason": self.reason,
            "blast_radius": self.blast_radius,
            "reversible": self.reversible,
            "owner": self.owner,
            "related_signals": list(self.related_signals),
        }


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


_RISK_APPETITES = ("cautious", "balanced", "aggressive")


@dataclass
class RadarInputs:
    """Bundle of (optional) analyzer reports the radar fuses."""

    anomaly_report: Optional[AnomalyReport] = None
    drift_report: Optional[DriftReport] = None
    error_report: Optional[ErrorReport] = None
    retry_report: Optional[RetryReport] = None
    latency_report: Optional[LatencySessionReport] = None
    budget_report: Optional[BudgetReport] = None
    health_report: Optional[HealthReport] = None
    window_label: str = "current"
    risk_appetite: str = "balanced"


# --------------------------------------------------------------------------- #
# Action catalogue
# --------------------------------------------------------------------------- #


# (key, title, priority, blast_radius, reversible, owner)
_ACTION_CATALOGUE: dict[str, tuple[str, ActionPriority, int, bool, str]] = {
    "page_oncall":             ("Page on-call engineer",        ActionPriority.P0, 1, True,  "oncall"),
    "enable_circuit_breaker":  ("Enable circuit breaker",       ActionPriority.P0, 2, True,  "sre"),
    "rollback_last_change":    ("Roll back last change",        ActionPriority.P0, 4, False, "deploy-bot"),
    "snapshot_state":          ("Snapshot state for forensics", ActionPriority.P2, 1, True,  "sre"),
    "throttle_traffic":        ("Throttle inbound traffic",     ActionPriority.P1, 2, True,  "sre"),
    "raise_budget":            ("Raise token / cost budget",    ActionPriority.P1, 1, True,  "platform"),
    "freeze_deploys":          ("Freeze deploys",               ActionPriority.P1, 3, True,  "release-mgr"),
    "scale_up_capacity":       ("Scale up serving capacity",    ActionPriority.P1, 2, True,  "sre"),
    "investigate_top_cluster": ("Investigate top error cluster", ActionPriority.P2, 1, True, "owner"),
    "tune_retry_backoff":      ("Tune retry / backoff policy",  ActionPriority.P2, 1, True,  "owner"),
    "notify_stakeholders":     ("Notify stakeholders",          ActionPriority.P2, 1, True,  "comms"),
}


def _build_action(
    key: str,
    reason: str,
    related: list[str],
) -> RiskAction:
    title, priority, blast, reversible, owner = _ACTION_CATALOGUE[key]
    return RiskAction(
        key=key,
        title=title,
        priority=priority,
        reason=reason,
        blast_radius=blast,
        reversible=reversible,
        owner=owner,
        related_signals=list(related),
    )


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


@dataclass
class RiskRadarReport:
    """Result of one IncidentRiskRadar.assess() call."""

    window_label: str
    risk_appetite: str
    fused_score: float
    band: RiskBand
    signals: list[RiskSignal]
    actions: list[RiskAction]
    generated_at: datetime

    # -- summaries --

    @property
    def summary(self) -> str:
        top = ", ".join(a.key for a in self.top_actions(3)) or "no actions"
        return (
            f"[{self.band.value.upper()}] fused={self.fused_score:.1f} "
            f"over {len(self.signals)} signals — {top}"
        )

    def top_actions(self, n: int = 3) -> list[RiskAction]:
        return list(self.actions[:n])

    # -- serialisers --

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_label": self.window_label,
            "risk_appetite": self.risk_appetite,
            "fused_score": round(self.fused_score, 2),
            "band": self.band.value,
            "signals": [s.to_dict() for s in self.signals],
            "actions": [a.to_dict() for a in self.actions],
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary,
        }

    def render_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def render_text(self) -> str:
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  Incident Risk Radar")
        lines.append("=" * 60)
        lines.append(f"  Window:        {self.window_label}")
        lines.append(f"  Appetite:      {self.risk_appetite}")
        lines.append(f"  Fused score:   {self.fused_score:.1f}/100")
        lines.append(f"  Band:          {self.band.value.upper()}")
        lines.append(f"  Generated at:  {self.generated_at.isoformat()}")
        lines.append("")
        lines.append("  -- Signals --")
        if not self.signals:
            lines.append("    (no signals provided)")
        for s in self.signals:
            lead = "leading" if s.leading else "trailing"
            lines.append(
                f"    {s.source:<8} score={s.score:5.1f}  w={s.weight:.2f}  ({lead})"
            )
            for ev in s.evidence:
                lines.append(f"        - {ev}")
        lines.append("")
        lines.append("  -- Actions --")
        if not self.actions:
            lines.append("    (no actions recommended)")
        for a in self.actions:
            rev = "reversible" if a.reversible else "irreversible"
            lines.append(
                f"    [{a.priority.value}] {a.title} "
                f"(blast={a.blast_radius}, {rev}, owner={a.owner})"
            )
            lines.append(f"        reason: {a.reason}")
        lines.append("")
        lines.append(f"  Summary: {self.summary}")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Incident Risk Radar — `{self.band.value.upper()}`")
        lines.append("")
        lines.append(f"- **Window:** {self.window_label}")
        lines.append(f"- **Appetite:** {self.risk_appetite}")
        lines.append(f"- **Fused score:** {self.fused_score:.1f} / 100")
        lines.append(f"- **Band:** {self.band.value.upper()}")
        lines.append(f"- **Generated at:** {self.generated_at.isoformat()}")
        lines.append("")
        lines.append("## Signals")
        lines.append("")
        if not self.signals:
            lines.append("_No signals provided._")
        else:
            lines.append("| Source | Score | Weight | Leading | Evidence |")
            lines.append("|---|---:|---:|:---:|---|")
            for s in self.signals:
                ev = "; ".join(s.evidence) if s.evidence else "—"
                lead = "✓" if s.leading else " "
                lines.append(
                    f"| `{s.source}` | {s.score:.1f} | {s.weight:.2f} | {lead} | {ev} |"
                )
        lines.append("")
        lines.append("## Recommended actions")
        lines.append("")
        if not self.actions:
            lines.append("_No pre-emptive actions recommended at this risk level._")
        else:
            for a in self.actions:
                rev = "reversible" if a.reversible else "**irreversible**"
                lines.append(
                    f"- **[{a.priority.value}] {a.title}** "
                    f"(blast={a.blast_radius}, {rev}, owner=`{a.owner}`)"
                )
                lines.append(f"  - _Why:_ {a.reason}")
        lines.append("")
        lines.append(f"_Summary: {self.summary}_")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The radar itself
# --------------------------------------------------------------------------- #


class IncidentRiskRadar:
    """Fuse cross-module signals into a unified pre-incident risk picture."""

    def __init__(self, risk_appetite: str = "balanced") -> None:
        if risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"risk_appetite must be one of {_RISK_APPETITES}, got {risk_appetite!r}"
            )
        self.risk_appetite = risk_appetite

    # ----- per-source scorers (static, for unit testability) -----

    @staticmethod
    def _score_anomaly(report: AnomalyReport) -> RiskSignal:
        crit = report.critical_count
        warn = report.warning_count
        score = min(100.0, 60.0 * crit + 20.0 * warn)
        evidence: list[str] = []
        if crit:
            evidence.append(f"{crit} CRITICAL anomalies")
        if warn:
            evidence.append(f"{warn} WARNING anomalies")
        top_kinds = sorted({a.kind.value for a in report.anomalies})[:3]
        if top_kinds:
            evidence.append("kinds: " + ", ".join(top_kinds))
        actions: list[str] = []
        if score >= 80:
            actions.extend(["page_oncall", "snapshot_state"])
        elif score >= 50:
            actions.extend(["enable_circuit_breaker", "snapshot_state"])
        elif score > 0:
            actions.append("investigate_top_cluster")
        return RiskSignal(
            source="anomaly",
            score=float(score),
            weight=1.0,
            leading=True,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_drift(report: DriftReport) -> RiskSignal:
        name = getattr(report.status, "name", "STABLE").upper()
        if "DEGRAD" in name or "SIGNIF" in name or "CRIT" in name:
            score = 90.0
        elif "MINOR" in name or "WARN" in name or "MOD" in name:
            score = 50.0
        elif "STABLE" in name or "NORMAL" in name:
            score = 0.0
        else:
            score = 10.0
        evidence: list[str] = [
            f"status={report.status.value}",
            f"drift_score={report.drift_score}",
        ]
        if report.drifting_metrics:
            shown = ", ".join(report.drifting_metrics[:3])
            evidence.append(f"drifting: {shown}")
        actions: list[str] = []
        if score >= 70:
            actions.extend(["rollback_last_change", "freeze_deploys"])
        elif score >= 40:
            actions.append("freeze_deploys")
        return RiskSignal(
            source="drift",
            score=float(score),
            weight=1.0,
            leading=True,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_errors(report: ErrorReport) -> RiskSignal:
        total = int(getattr(report, "total_count", 0) or 0)
        score = min(100.0, total * 8.0)
        # boost for rising trend in top clusters
        rising = sum(
            1 for c in (report.top_clusters or [])
            if getattr(c, "trend", None) is not None
            and "RIS" in c.trend.name.upper()
        )
        if rising:
            score = min(100.0, score + 20.0)
        evidence: list[str] = [
            f"{total} errors across {report.unique_count} clusters",
            f"{report.sessions_affected} sessions affected",
        ]
        if rising:
            evidence.append(f"{rising} clusters trending UP")
        actions: list[str] = []
        if score >= 60:
            actions.append("enable_circuit_breaker")
        if score >= 30:
            actions.append("investigate_top_cluster")
        return RiskSignal(
            source="errors",
            score=float(score),
            weight=1.0,
            leading=False,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_retries(report: RetryReport) -> RiskSignal:
        total = int(report.total_retries or 0)
        storms = len(report.storms or [])
        score = min(100.0, 5.0 * total + 25.0 * storms)
        evidence: list[str] = [
            f"{total} retries (rate {report.retry_rate:.1%})",
        ]
        if storms:
            evidence.append(f"{storms} retry storm(s)")
        actions: list[str] = []
        if score >= 50:
            actions.append("throttle_traffic")
        if score >= 40:
            actions.append("tune_retry_backoff")
        return RiskSignal(
            source="retries",
            score=float(score),
            weight=1.0,
            leading=True,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_latency(report: LatencySessionReport) -> RiskSignal:
        failed = int(report.failed_count or 0)
        bottleneck_pct = float(report.bottleneck_pct or 0.0)
        score = min(100.0, 15.0 * failed + bottleneck_pct)
        evidence: list[str] = [
            f"{report.step_count} steps in {report.total_duration_s:.2f}s",
        ]
        if failed:
            evidence.append(f"{failed} failed steps")
        if report.bottleneck_name and bottleneck_pct:
            evidence.append(
                f"bottleneck: {report.bottleneck_name} ({bottleneck_pct:.0f}%)"
            )
        actions: list[str] = []
        if failed > 0 or bottleneck_pct > 50:
            actions.append("scale_up_capacity")
        if score >= 50:
            actions.append("throttle_traffic")
        return RiskSignal(
            source="latency",
            score=float(score),
            weight=1.0,
            leading=False,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_budget(report: BudgetReport) -> RiskSignal:
        tok = float(report.token_utilization or 0.0)
        cost = float(report.cost_utilization or 0.0)
        util = max(tok, cost)
        score = min(100.0, util * 110.0)
        status_name = getattr(report.status, "name", "ACTIVE").upper()
        if "EXCEED" in status_name or "EXHAUST" in status_name or "BLOCK" in status_name:
            score = 100.0
        evidence: list[str] = [
            f"status={report.status.value}",
            f"utilisation={util:.0%}",
        ]
        if report.total_cost_usd:
            evidence.append(f"${report.total_cost_usd:.4f} spent")
        actions: list[str] = []
        if score >= 70:
            actions.append("raise_budget")
        if score >= 90:
            actions.append("throttle_traffic")
        return RiskSignal(
            source="budget",
            score=float(score),
            weight=1.0,
            leading=True,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    @staticmethod
    def _score_health(report: HealthReport) -> RiskSignal:
        overall = float(report.overall_score or 0.0)
        score = max(0.0, 100.0 - overall)
        evidence: list[str] = [
            f"grade={report.grade.value} ({overall:.0f}/100)",
            f"errors={report.error_count}",
        ]
        actions: list[str] = []
        if score >= 60:
            actions.append("notify_stakeholders")
        if score >= 40:
            actions.append("investigate_top_cluster")
        return RiskSignal(
            source="health",
            score=float(score),
            weight=1.0,
            leading=False,
            evidence=evidence,
            suggested_action_keys=actions,
        )

    # ----- main entry point -----

    def assess(self, inputs: RadarInputs) -> RiskRadarReport:
        if inputs.risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"inputs.risk_appetite must be one of {_RISK_APPETITES}, "
                f"got {inputs.risk_appetite!r}"
            )

        signals: list[RiskSignal] = []

        if inputs.anomaly_report is not None:
            signals.append(self._score_anomaly(inputs.anomaly_report))
        if inputs.drift_report is not None:
            signals.append(self._score_drift(inputs.drift_report))
        if inputs.error_report is not None:
            signals.append(self._score_errors(inputs.error_report))
        if inputs.retry_report is not None:
            signals.append(self._score_retries(inputs.retry_report))
        if inputs.latency_report is not None:
            signals.append(self._score_latency(inputs.latency_report))
        if inputs.budget_report is not None:
            signals.append(self._score_budget(inputs.budget_report))
        if inputs.health_report is not None:
            signals.append(self._score_health(inputs.health_report))

        # Weight adjustment by risk appetite (leading-indicator weighting only).
        lead_multiplier = {
            "cautious":   1.4,
            "balanced":   1.0,
            "aggressive": 0.7,
        }[inputs.risk_appetite]

        raw_weights: list[float] = []
        for s in signals:
            raw_weights.append(lead_multiplier if s.leading else 1.0)
        total_w = sum(raw_weights) or 1.0
        for s, rw in zip(signals, raw_weights):
            s.weight = rw / total_w

        fused = sum(s.score * s.weight for s in signals) if signals else 0.0
        fused = round(fused, 1)
        band = RiskBand.from_score(fused)

        actions = self._build_actions(signals, band)

        return RiskRadarReport(
            window_label=inputs.window_label,
            risk_appetite=inputs.risk_appetite,
            fused_score=fused,
            band=band,
            signals=signals,
            actions=actions,
            generated_at=datetime.now(timezone.utc),
        )

    # ----- action selection -----

    @staticmethod
    def _scores_by_source(signals: list[RiskSignal]) -> dict[str, float]:
        return {s.source: s.score for s in signals}

    def _build_actions(
        self,
        signals: list[RiskSignal],
        band: RiskBand,
    ) -> list[RiskAction]:
        if band is RiskBand.CALM:
            return []

        by = self._scores_by_source(signals)
        anomaly = by.get("anomaly", 0.0)
        drift = by.get("drift", 0.0)
        errors = by.get("errors", 0.0)
        retries = by.get("retries", 0.0)
        latency = by.get("latency", 0.0)
        budget = by.get("budget", 0.0)

        # build (key -> (reason, related_signals))
        chosen: dict[str, tuple[str, list[str]]] = {}

        def add(key: str, reason: str, related: list[str]) -> None:
            if key not in chosen:
                chosen[key] = (reason, related)
            else:
                # merge related, keep first reason but extend if shorter
                prev_reason, prev_related = chosen[key]
                merged = list(dict.fromkeys([*prev_related, *related]))
                chosen[key] = (prev_reason, merged)

        if band is RiskBand.CRITICAL:
            add("page_oncall",
                f"CRITICAL band (fused signals: anomaly={anomaly:.0f}, "
                f"errors={errors:.0f}, retries={retries:.0f})",
                [s.source for s in signals if s.score > 0])
            add("snapshot_state",
                "Preserve state before any mitigation alters it",
                [s.source for s in signals if s.score > 0])
            add("freeze_deploys",
                "Stop introducing new variables while critical",
                [s.source for s in signals if s.score > 0])
            if drift >= 70 or anomaly >= 80:
                add("rollback_last_change",
                    f"drift={drift:.0f}, anomaly={anomaly:.0f} -> recent change "
                    f"likely implicated",
                    ["drift", "anomaly"])

        if band in (RiskBand.CRITICAL, RiskBand.HIGH):
            if latency >= 50 or retries >= 50:
                add("throttle_traffic",
                    f"latency={latency:.0f}, retries={retries:.0f} -> shed load",
                    ["latency", "retries"])
            if errors >= 60:
                add("enable_circuit_breaker",
                    f"errors={errors:.0f} -> trip breaker to stop cascade",
                    ["errors"])
            if budget >= 70:
                add("raise_budget",
                    f"budget={budget:.0f} -> avoid hard cutoff mid-incident",
                    ["budget"])
            lat_failed = False
            lat_bottleneck = False
            for s in signals:
                if s.source == "latency":
                    for ev in s.evidence:
                        if "failed" in ev:
                            lat_failed = True
                        if "bottleneck" in ev:
                            lat_bottleneck = True
            if lat_failed or lat_bottleneck:
                add("scale_up_capacity",
                    "latency report shows failures or a >50% bottleneck",
                    ["latency"])

        if band is RiskBand.ELEVATED:
            if retries >= 40:
                add("tune_retry_backoff",
                    f"retries={retries:.0f} -> back off harder",
                    ["retries"])
            if errors >= 30:
                add("investigate_top_cluster",
                    f"errors={errors:.0f} -> dig into top fingerprint",
                    ["errors"])
            if budget >= 60:
                add("raise_budget",
                    f"budget={budget:.0f} approaching ceiling",
                    ["budget"])
            add("notify_stakeholders",
                "Elevated risk window -- keep humans in the loop",
                [s.source for s in signals if s.score > 0])

        if band is RiskBand.WATCH:
            add("snapshot_state",
                "Capture baseline now in case it escalates",
                [s.source for s in signals if s.score > 0])
            add("investigate_top_cluster",
                "Quiet enough to dig into root causes before they grow",
                [s.source for s in signals if s.score > 0])

        # materialise + sort
        materialised: list[RiskAction] = [
            _build_action(key, reason, related)
            for key, (reason, related) in chosen.items()
        ]

        def sort_key(a: RiskAction) -> tuple[int, float]:
            related_score = -sum(by.get(src, 0.0) for src in a.related_signals)
            return (a.priority.rank, related_score)

        materialised.sort(key=sort_key)
        return materialised


__all__ = [
    "IncidentRiskRadar",
    "RiskRadarReport",
    "RiskSignal",
    "RiskBand",
    "RiskAction",
    "ActionPriority",
    "RadarInputs",
]
