"""Agentic per-tool reliability advisor for AgentLens.

:class:`ToolReliabilityAdvisor` answers three questions about every tool
exposed to AgentLens-instrumented agents:

* **Which tools are reliable** and which are dragging the fleet down?
* **What's wrong** with the unreliable ones (error spikes, latency,
  retry storms, dominant error clusters)?
* **What should we do** about them (circuit-break, retry-backoff,
  optimize, add redundancy, deprecate)?

This is a pure, deterministic sibling to
:class:`~agentlens.cost_attribution_advisor.CostAttributionAdvisor`,
:class:`~agentlens.trace_completion_advisor.TraceCompletionAdvisor`,
:class:`~agentlens.agent_loop_detector.AgentLoopDetector`, and the rest
of the agentlens advisor family.  Never mutates inputs, makes no network
calls, uses only the standard library.  Deterministic given an injectable
``now_fn``.
"""

from __future__ import annotations

import copy
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from agentlens._utils import percentile as _percentile_impl, parse_iso_or_epoch as _parse_ts_raw


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a timestamp value into a timezone-aware datetime.

    Delegates to :func:`agentlens._utils.parse_iso_or_epoch` but ensures
    naive ``datetime`` objects are stamped UTC to match this module's
    historical contract.
    """
    dt = _parse_ts_raw(value)
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class ToolVerdict(Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    FLAKY = "flaky"
    DEGRADED = "degraded"
    CIRCUIT_BREAK = "circuit_break"
    DEPRECATE_CANDIDATE = "deprecate_candidate"
    INSUFFICIENT_DATA = "insufficient_data"


class ToolReliabilityGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


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


class ReliabilityBand(Enum):
    STABLE = "stable"
    WATCH = "watch"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class ToolSnapshot:
    tool_name: str
    total_calls: int
    error_count: int
    error_rate: float
    success_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    retry_density: float
    unique_sessions: int
    unique_callers: int
    top_error_codes: list[dict[str, Any]]
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    reliability_score: float
    verdict: ToolVerdict
    priority: ActionPriority
    reasons: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "total_calls": self.total_calls,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "success_rate": round(self.success_rate, 4),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "retry_density": round(self.retry_density, 4),
            "unique_sessions": self.unique_sessions,
            "unique_callers": self.unique_callers,
            "top_error_codes": list(self.top_error_codes),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "reliability_score": round(self.reliability_score, 2),
            "verdict": self.verdict.value,
            "priority": self.priority.value,
            "reasons": list(self.reasons),
            "suggested_action": self.suggested_action,
        }


@dataclass
class ReliabilityPortfolioSummary:
    total_tools: int
    total_calls: int
    total_errors: int
    mean_error_rate: float
    worst_tool: Optional[str]
    concentration_band: ReliabilityBand
    grade: ToolReliabilityGrade
    reliability_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tools": self.total_tools,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "mean_error_rate": round(self.mean_error_rate, 4),
            "worst_tool": self.worst_tool,
            "concentration_band": self.concentration_band.value,
            "grade": self.grade.value,
            "reliability_score": round(self.reliability_score, 2),
        }


@dataclass
class ToolReliabilityPlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    tool_names: list[str] = field(default_factory=list)
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
            "tool_names": list(self.tool_names),
            "suggested_value": self.suggested_value,
        }


@dataclass
class ToolReliabilityReport:
    generated_at: datetime
    risk_appetite: RiskAppetite
    portfolio: ReliabilityPortfolioSummary
    snapshots: list[ToolSnapshot]
    playbook: list[ToolReliabilityPlaybookAction]
    insights: list[str]
    summary_headline: str

    @property
    def grade(self) -> ToolReliabilityGrade:
        return self.portfolio.grade

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "risk_appetite": self.risk_appetite.value,
            "portfolio": self.portfolio.to_dict(),
            "snapshots": [s.to_dict() for s in self.snapshots],
            "playbook": [a.to_dict() for a in self.playbook],
            "insights": list(self.insights),
            "summary_headline": self.summary_headline,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent, default=str)

    def to_text(self) -> str:
        lines: list[str] = []
        lines.append(
            f"ToolReliabilityAdvisor grade={self.grade.value} "
            f"risk={self.risk_appetite.value}"
        )
        lines.append(self.summary_headline)
        p = self.portfolio
        lines.append(
            f"  tools={p.total_tools} calls={p.total_calls} "
            f"errors={p.total_errors} mean_error_rate={p.mean_error_rate:.2%} "
            f"reliability={p.reliability_score:.1f} band={p.concentration_band.value}"
        )
        if self.snapshots:
            lines.append("  Tools:")
            for s in self.snapshots[: min(10, len(self.snapshots))]:
                lines.append(
                    f"    [{s.priority.value}] {s.tool_name} "
                    f"score={s.reliability_score:.1f} verdict={s.verdict.value} "
                    f"err={s.error_rate:.1%} p95={s.p95_latency_ms:.0f}ms calls={s.total_calls}"
                )
        else:
            lines.append("  No tools observed.")
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
        lines.append(f"# ToolReliabilityAdvisor (grade {self.grade.value})")
        lines.append("")
        lines.append(self.summary_headline)
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        p = self.portfolio
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Total tools | {p.total_tools} |")
        lines.append(f"| Total calls | {p.total_calls} |")
        lines.append(f"| Total errors | {p.total_errors} |")
        lines.append(f"| Mean error rate | {p.mean_error_rate:.2%} |")
        lines.append(f"| Reliability score | {p.reliability_score:.1f} |")
        lines.append(f"| Concentration band | {p.concentration_band.value} |")
        lines.append(f"| Worst tool | {p.worst_tool or '-'} |")
        lines.append("")
        lines.append("## Tools")
        lines.append("")
        if self.snapshots:
            lines.append(
                "| Tool | Verdict | Priority | Score | Err% | p95 ms | Calls | Reasons |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for s in self.snapshots:
                reasons = ", ".join(s.reasons) if s.reasons else "-"
                lines.append(
                    f"| {s.tool_name} | {s.verdict.value} | {s.priority.value} | "
                    f"{s.reliability_score:.1f} | {s.error_rate * 100:.1f}% | "
                    f"{s.p95_latency_ms:.0f} | {s.total_calls} | {reasons} |"
                )
        else:
            lines.append("_No tools observed._")
        lines.append("")
        lines.append("## Playbook")
        lines.append("")
        if self.playbook:
            lines.append("| Priority | Id | Label | Owner | Blast | Reversibility |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for a in self.playbook:
                lines.append(
                    f"| {a.priority.value} | {a.id} | {a.label} | {a.owner} | "
                    f"{a.blast_radius} | {a.reversibility} |"
                )
        else:
            lines.append("_No actions._")
        lines.append("")
        lines.append("## Insights")
        lines.append("")
        for ins in self.insights:
            lines.append(f"- {ins}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _coerce_event(ev: Any) -> dict[str, Any]:
    """Coerce an AgentEvent / dict / attr-bearing object to a dict."""
    if ev is None:
        return {}
    if isinstance(ev, dict):
        return copy.deepcopy(ev)
    if hasattr(ev, "model_dump"):
        try:
            return ev.model_dump()
        except Exception:
            pass
    try:
        return dict(ev)
    except Exception:
        pass
    out: dict[str, Any] = {}
    for attr in (
        "event_type",
        "tool_name",
        "tool",
        "error",
        "error_code",
        "duration_ms",
        "latency_ms",
        "session_id",
        "agent_id",
        "caller",
        "retry_count",
        "retries",
        "timestamp",
        "ts",
        "metadata",
    ):
        if hasattr(ev, attr):
            out[attr] = getattr(ev, attr)
    return out



def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile via the shared ``_utils.percentile``.

    Thin compatibility wrapper around :func:`agentlens._utils.percentile`
    so this module keeps its historical ``(unsorted_values, pct)`` calling
    convention while delegating the actual interpolation to the SDK-wide
    helper.  Eliminates the per-module copy of the percentile formula that
    used to drift across advisors.

    Args:
        values: Unsorted numeric samples.
        pct: Percentile to compute, expressed on the 0..100 scale.

    Returns:
        The interpolated percentile as a ``float`` (``0.0`` for empty input).
    """
    if not values:
        return 0.0
    return float(_percentile_impl(sorted(values), pct))


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


_SEVERITY = {
    "HIGH_ERROR_RATE": 45,
    "ELEVATED_ERROR_RATE": 20,
    "LATENCY_OUTLIER": 30,
    "LATENCY_DEGRADED": 15,
    "RETRY_STORM": 25,
    "SINGLE_CALLER_DEPENDENCY": 10,
    "DOMINANT_ERROR_CLUSTER": 15,
    "STALE_TOOL": 10,
}


class ToolReliabilityAdvisor:
    """Per-tool reliability auditor over an AgentEvent stream."""

    def __init__(self, *, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    def analyze(
        self,
        events: Iterable[Any],
        *,
        risk_appetite: "str | RiskAppetite" = RiskAppetite.BALANCED,
    ) -> ToolReliabilityReport:
        appetite = RiskAppetite.parse(risk_appetite)
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        per_tool: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "errors": 0,
                "latencies": [],
                "retries": 0,
                "sessions": set(),
                "callers": set(),
                "error_codes": Counter(),
                "first_seen": None,
                "last_seen": None,
            }
        )

        for raw in events or []:
            ev = _coerce_event(raw)
            if not ev:
                continue
            tool = ev.get("tool_name") or ev.get("tool")
            if not tool:
                # Sometimes tool name lives in metadata.
                md = ev.get("metadata") or {}
                tool = md.get("tool_name") or md.get("tool")
            if not tool:
                continue
            etype = ev.get("event_type") or ""
            bucket = per_tool[str(tool)]
            ts = _parse_ts(ev.get("timestamp") or ev.get("ts"))
            if ts is not None:
                if bucket["first_seen"] is None or ts < bucket["first_seen"]:
                    bucket["first_seen"] = ts
                if bucket["last_seen"] is None or ts > bucket["last_seen"]:
                    bucket["last_seen"] = ts

            is_call = etype == "tool_call"
            err_code = ev.get("error_code") or (
                ev.get("error") if isinstance(ev.get("error"), str) else None
            )
            has_error = bool(err_code) or bool(ev.get("error"))
            is_result_error = etype == "tool_result" and has_error

            # Count attempts: every tool_call is an attempt; tool_result also
            # implies an attempt occurred even if upstream call event was
            # dropped.  We dedupe by counting tool_call OR (tool_result with
            # no preceding tool_call style) - in practice we just count
            # tool_call events as attempts and tool_result errors as failures.
            if is_call:
                bucket["calls"] += 1
            elif etype == "tool_result":
                # Treat result-only events as a completed attempt too.
                bucket["calls"] += 1

            if (is_result_error or (is_call and has_error)):
                bucket["errors"] += 1
                if err_code:
                    bucket["error_codes"][str(err_code)] += 1
                else:
                    bucket["error_codes"]["unknown"] += 1

            dur = ev.get("duration_ms") or ev.get("latency_ms")
            if isinstance(dur, (int, float)) and dur >= 0:
                bucket["latencies"].append(float(dur))

            retries = ev.get("retry_count") or ev.get("retries")
            if isinstance(retries, (int, float)) and retries > 0:
                bucket["retries"] += int(retries)

            sid = ev.get("session_id")
            if sid:
                bucket["sessions"].add(str(sid))
            caller = ev.get("agent_id") or ev.get("caller")
            if caller:
                bucket["callers"].add(str(caller))

        snapshots: list[ToolSnapshot] = []
        for name, b in per_tool.items():
            snapshots.append(self._build_snapshot(name, b, now, appetite))

        # Deterministic sort: lowest reliability_score first, then name.
        snapshots.sort(key=lambda s: (s.reliability_score, s.tool_name))

        portfolio = self._build_portfolio(snapshots, appetite)
        playbook = self._build_playbook(snapshots, portfolio, appetite)
        insights = self._build_insights(snapshots, portfolio)

        if not snapshots:
            headline = (
                "VERDICT: grade=A N=0 calls=0 errors=0 - no tool activity observed"
            )
        else:
            headline = (
                f"VERDICT: grade={portfolio.grade.value} N={portfolio.total_tools} "
                f"calls={portfolio.total_calls} errors={portfolio.total_errors} "
                f"reliability={portfolio.reliability_score:.1f}"
            )

        return ToolReliabilityReport(
            generated_at=now,
            risk_appetite=appetite,
            portfolio=portfolio,
            snapshots=snapshots,
            playbook=playbook,
            insights=insights,
            summary_headline=headline,
        )

    # ------------------------------------------------------------------ #
    def _build_snapshot(
        self,
        name: str,
        b: dict[str, Any],
        now: datetime,
        appetite: RiskAppetite,
    ) -> ToolSnapshot:
        calls = int(b["calls"])
        errors = int(b["errors"])
        error_rate = (errors / calls) if calls else 0.0
        success_rate = 1.0 - error_rate if calls else 0.0
        lats = list(b["latencies"])
        p50 = _percentile(lats, 50)
        p95 = _percentile(lats, 95)
        p99 = _percentile(lats, 99)
        retry_density = (b["retries"] / calls) if calls else 0.0
        unique_sessions = len(b["sessions"])
        unique_callers = len(b["callers"])
        first_seen = b["first_seen"]
        last_seen = b["last_seen"]

        top_codes_raw = b["error_codes"].most_common(3)
        top_codes = [{"code": c, "count": int(n)} for c, n in top_codes_raw]

        reasons: list[str] = []

        if calls == 0:
            return ToolSnapshot(
                tool_name=name,
                total_calls=0,
                error_count=0,
                error_rate=0.0,
                success_rate=0.0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                retry_density=0.0,
                unique_sessions=0,
                unique_callers=0,
                top_error_codes=[],
                first_seen=first_seen,
                last_seen=last_seen,
                reliability_score=50.0,
                verdict=ToolVerdict.INSUFFICIENT_DATA,
                priority=ActionPriority.P3,
                reasons=["INSUFFICIENT_DATA"],
                suggested_action="Wait for more telemetry before judging this tool.",
            )

        if error_rate >= 0.20:
            reasons.append("HIGH_ERROR_RATE")
        elif error_rate >= 0.05:
            reasons.append("ELEVATED_ERROR_RATE")

        if p95 >= 5000:
            reasons.append("LATENCY_OUTLIER")
        elif p95 >= 2000:
            reasons.append("LATENCY_DEGRADED")

        if retry_density >= 0.5:
            reasons.append("RETRY_STORM")

        if unique_callers == 1 and calls >= 5:
            reasons.append("SINGLE_CALLER_DEPENDENCY")

        if calls < 5:
            reasons.append("LOW_USAGE")

        if errors >= 3 and top_codes_raw:
            top_code, top_n = top_codes_raw[0]
            if errors and (top_n / errors) >= 0.60:
                reasons.append("DOMINANT_ERROR_CLUSTER")

        if first_seen is not None and (now - first_seen) <= timedelta(hours=24):
            reasons.append("NEW_TOOL")

        if last_seen is not None and (now - last_seen) > timedelta(days=14):
            reasons.append("STALE_TOOL")

        # Score
        sev_hits = [_SEVERITY[r] for r in reasons if r in _SEVERITY]
        if sev_hits:
            top = max(sev_hits)
            rest = sorted(sev_hits, reverse=True)[1:]
            penalty = top + 0.4 * min(sum(rest), 60)
        else:
            penalty = 0.0

        if appetite == RiskAppetite.CAUTIOUS:
            penalty *= 1.15
        elif appetite == RiskAppetite.AGGRESSIVE:
            penalty *= 0.85
        score = max(0.0, min(100.0, 100.0 - penalty))

        if not reasons or reasons == ["NEW_TOOL"]:
            reasons.append("HEALTHY")

        # Verdict ladder
        has_circuit = "HIGH_ERROR_RATE" in reasons
        is_stale = "STALE_TOOL" in reasons
        is_low_use = "LOW_USAGE" in reasons
        is_flaky = "ELEVATED_ERROR_RATE" in reasons or "RETRY_STORM" in reasons

        if score <= 25 or has_circuit:
            verdict = ToolVerdict.CIRCUIT_BREAK
        elif is_stale or (is_low_use and score <= 70):
            verdict = ToolVerdict.DEPRECATE_CANDIDATE
        elif score <= 50:
            verdict = ToolVerdict.DEGRADED
        elif is_flaky and score <= 70:
            verdict = ToolVerdict.FLAKY
        elif score <= 85 or len([r for r in reasons if r not in ("HEALTHY", "NEW_TOOL", "LOW_USAGE")]) > 0:
            verdict = ToolVerdict.WATCH
        else:
            verdict = ToolVerdict.HEALTHY

        if verdict == ToolVerdict.CIRCUIT_BREAK:
            priority = ActionPriority.P0
        elif verdict in (ToolVerdict.DEGRADED, ToolVerdict.FLAKY):
            priority = ActionPriority.P1
        elif verdict in (ToolVerdict.WATCH, ToolVerdict.DEPRECATE_CANDIDATE):
            priority = ActionPriority.P2
        else:
            priority = ActionPriority.P3

        suggested = self._suggest(verdict, reasons)

        return ToolSnapshot(
            tool_name=name,
            total_calls=calls,
            error_count=errors,
            error_rate=error_rate,
            success_rate=success_rate,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            retry_density=retry_density,
            unique_sessions=unique_sessions,
            unique_callers=unique_callers,
            top_error_codes=top_codes,
            first_seen=first_seen,
            last_seen=last_seen,
            reliability_score=score,
            verdict=verdict,
            priority=priority,
            reasons=sorted(set(reasons)),
            suggested_action=suggested,
        )

    @staticmethod
    def _suggest(verdict: ToolVerdict, reasons: list[str]) -> str:
        if verdict == ToolVerdict.CIRCUIT_BREAK:
            return "Open a circuit-breaker on this tool and investigate the failure cluster."
        if verdict == ToolVerdict.DEGRADED:
            return "Roll back recent changes or add retries with backoff."
        if verdict == ToolVerdict.FLAKY:
            return "Tighten retry-backoff policy and instrument additional telemetry."
        if verdict == ToolVerdict.DEPRECATE_CANDIDATE:
            return "Plan deprecation or consolidate callers into a healthier tool."
        if verdict == ToolVerdict.WATCH:
            return "Continue monitoring; consider preemptive guardrails."
        if verdict == ToolVerdict.INSUFFICIENT_DATA:
            return "Collect more telemetry before judging."
        return "No action needed."

    # ------------------------------------------------------------------ #
    def _build_portfolio(
        self,
        snapshots: list[ToolSnapshot],
        appetite: RiskAppetite,
    ) -> ReliabilityPortfolioSummary:
        if not snapshots:
            return ReliabilityPortfolioSummary(
                total_tools=0,
                total_calls=0,
                total_errors=0,
                mean_error_rate=0.0,
                worst_tool=None,
                concentration_band=ReliabilityBand.STABLE,
                grade=ToolReliabilityGrade.A,
                reliability_score=100.0,
            )
        total_calls = sum(s.total_calls for s in snapshots)
        total_errors = sum(s.error_count for s in snapshots)
        # Per-tool mean (giving each tool equal weight)
        active = [s for s in snapshots if s.total_calls > 0]
        mean_err = (
            statistics.fmean(s.error_rate for s in active) if active else 0.0
        )
        worst = snapshots[0].tool_name if snapshots else None
        rel = statistics.fmean(s.reliability_score for s in snapshots)

        # Grade
        has_circuit = any(s.verdict == ToolVerdict.CIRCUIT_BREAK for s in snapshots)
        has_degraded = any(s.verdict == ToolVerdict.DEGRADED for s in snapshots)
        has_flaky = any(s.verdict == ToolVerdict.FLAKY for s in snapshots)
        has_watch = any(s.verdict == ToolVerdict.WATCH for s in snapshots)
        all_insuf = all(s.verdict == ToolVerdict.INSUFFICIENT_DATA for s in snapshots)
        if all_insuf:
            grade = ToolReliabilityGrade.B
        elif has_circuit or mean_err >= 0.25:
            grade = ToolReliabilityGrade.F
        elif has_degraded or mean_err >= 0.10:
            grade = ToolReliabilityGrade.D
        elif has_flaky:
            grade = ToolReliabilityGrade.C
        elif has_watch:
            grade = ToolReliabilityGrade.B
        else:
            grade = ToolReliabilityGrade.A

        # Band
        if has_circuit:
            band = ReliabilityBand.CRITICAL
        elif has_degraded:
            band = ReliabilityBand.HIGH
        elif has_flaky:
            band = ReliabilityBand.ELEVATED
        elif has_watch:
            band = ReliabilityBand.WATCH
        else:
            band = ReliabilityBand.STABLE

        # Appetite shifts grade slightly: aggressive can downgrade severity, cautious upgrades.
        # We keep grade as-is for determinism; appetite already affected per-tool scores.

        return ReliabilityPortfolioSummary(
            total_tools=len(snapshots),
            total_calls=total_calls,
            total_errors=total_errors,
            mean_error_rate=mean_err,
            worst_tool=worst,
            concentration_band=band,
            grade=grade,
            reliability_score=rel,
        )

    # ------------------------------------------------------------------ #
    def _build_playbook(
        self,
        snapshots: list[ToolSnapshot],
        portfolio: ReliabilityPortfolioSummary,
        appetite: RiskAppetite,
    ) -> list[ToolReliabilityPlaybookAction]:
        actions: list[ToolReliabilityPlaybookAction] = []

        circuit_tools = sorted(
            s.tool_name for s in snapshots if s.verdict == ToolVerdict.CIRCUIT_BREAK
        )
        flaky_tools = sorted(
            s.tool_name for s in snapshots if s.verdict == ToolVerdict.FLAKY
        )
        deprecate_tools = sorted(
            s.tool_name
            for s in snapshots
            if s.verdict == ToolVerdict.DEPRECATE_CANDIDATE
        )

        new_tool_names = {
            s.tool_name for s in snapshots if "NEW_TOOL" in s.reasons
        }
        retry_tools = sorted(
            s.tool_name for s in snapshots if "RETRY_STORM" in s.reasons
        )
        latency_tools = sorted(
            s.tool_name for s in snapshots if "LATENCY_OUTLIER" in s.reasons
        )
        dominant_err_tools = sorted(
            s.tool_name for s in snapshots if "DOMINANT_ERROR_CLUSTER" in s.reasons
        )
        single_caller_tools = sorted(
            s.tool_name for s in snapshots if "SINGLE_CALLER_DEPENDENCY" in s.reasons
        )
        stale_tools = sorted(
            s.tool_name for s in snapshots if "STALE_TOOL" in s.reasons
        )

        if circuit_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="CIRCUIT_BREAK_FAILING_TOOLS",
                    priority=ActionPriority.P0,
                    label="Open circuit-breakers on failing tools",
                    reason="One or more tools exceeded the failure threshold; isolate them before downstream tasks pile up.",
                    owner="platform",
                    blast_radius=4,
                    reversibility="high",
                    tool_names=circuit_tools,
                )
            )

        circuit_new = sorted(set(circuit_tools) & new_tool_names)
        if circuit_new:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="ROLLBACK_RECENT_TOOL_CHANGE",
                    priority=ActionPriority.P0,
                    label="Roll back recent change to newly-deployed failing tool",
                    reason="Newly-deployed tools are failing; suspect the latest change.",
                    owner="tool_owner",
                    blast_radius=3,
                    reversibility="medium",
                    tool_names=circuit_new,
                )
            )

        if dominant_err_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="INVESTIGATE_DOMINANT_ERROR_CLUSTER",
                    priority=ActionPriority.P1,
                    label="Investigate dominant error cluster",
                    reason="A single error code accounts for the majority of failures; root-cause it once and likely fix the rest.",
                    owner="tool_owner",
                    blast_radius=2,
                    reversibility="high",
                    tool_names=dominant_err_tools,
                )
            )

        if retry_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="TIGHTEN_RETRY_BACKOFF",
                    priority=ActionPriority.P1,
                    label="Tighten retry backoff policy",
                    reason="Retry density is high; agents are hammering the tool and amplifying load.",
                    owner="platform",
                    blast_radius=2,
                    reversibility="high",
                    tool_names=retry_tools,
                )
            )

        if latency_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="OPTIMIZE_SLOW_TOOL",
                    priority=ActionPriority.P1,
                    label="Optimize slow tool",
                    reason="p95 latency exceeds the 5s outlier threshold.",
                    owner="tool_owner",
                    blast_radius=2,
                    reversibility="high",
                    tool_names=latency_tools,
                )
            )

        if single_caller_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="ADD_REDUNDANT_INTEGRATION",
                    priority=ActionPriority.P2,
                    label="Add redundant integration to remove single-caller dependency",
                    reason="Only one agent calls this tool; outages will only be noticed by that one caller.",
                    owner="architecture",
                    blast_radius=3,
                    reversibility="medium",
                    tool_names=single_caller_tools,
                )
            )

        deprecate_signals = sorted(set(deprecate_tools) | set(stale_tools))
        if len(deprecate_signals) >= 2:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="DEPRECATE_OR_RETIRE",
                    priority=ActionPriority.P2,
                    label="Deprecate or retire unused tools",
                    reason="Multiple tools are stale or barely used; retire them to reduce surface area.",
                    owner="product",
                    blast_radius=3,
                    reversibility="low",
                    tool_names=deprecate_signals,
                )
            )

        if flaky_tools and not circuit_tools:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="ENABLE_CIRCUIT_BREAKER_GUARDS",
                    priority=ActionPriority.P2,
                    label="Enable circuit-breaker guards on flaky tools",
                    reason="Flaky tools are not yet circuit-breakable but trending that way.",
                    owner="platform",
                    blast_radius=1,
                    reversibility="high",
                    tool_names=flaky_tools,
                )
            )

        if appetite == RiskAppetite.CAUTIOUS and portfolio.grade in (
            ToolReliabilityGrade.C,
            ToolReliabilityGrade.D,
            ToolReliabilityGrade.F,
        ):
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="SCHEDULE_RELIABILITY_REVIEW",
                    priority=ActionPriority.P2,
                    label="Schedule reliability review",
                    reason="Cautious-mode safety net: portfolio grade is below B; book a review.",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    tool_names=[],
                )
            )

        if not actions:
            actions.append(
                ToolReliabilityPlaybookAction(
                    id="HEALTHY_FLEET",
                    priority=ActionPriority.P3,
                    label="Maintain observability; fleet is healthy",
                    reason="No reliability issues detected in this window.",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    tool_names=[],
                )
            )

        # Aggressive: trim P3 + lone P2 when P0/P1 present.
        if appetite == RiskAppetite.AGGRESSIVE:
            has_high = any(a.priority in (ActionPriority.P0, ActionPriority.P1) for a in actions)
            if has_high:
                p2_count = sum(1 for a in actions if a.priority == ActionPriority.P2)
                trimmed: list[ToolReliabilityPlaybookAction] = []
                for a in actions:
                    if a.priority == ActionPriority.P3:
                        continue
                    if a.priority == ActionPriority.P2 and p2_count == 1:
                        continue
                    trimmed.append(a)
                actions = trimmed or [
                    a for a in actions if a.priority in (ActionPriority.P0, ActionPriority.P1)
                ]

        # Sort priority asc then id asc.
        prio_order = {ActionPriority.P0: 0, ActionPriority.P1: 1, ActionPriority.P2: 2, ActionPriority.P3: 3}
        actions.sort(key=lambda a: (prio_order[a.priority], a.id))
        # Dedupe by id keeping first.
        seen: set[str] = set()
        deduped: list[ToolReliabilityPlaybookAction] = []
        for a in actions:
            if a.id in seen:
                continue
            seen.add(a.id)
            deduped.append(a)
        return deduped

    # ------------------------------------------------------------------ #
    def _build_insights(
        self,
        snapshots: list[ToolSnapshot],
        portfolio: ReliabilityPortfolioSummary,
    ) -> list[str]:
        insights: list[str] = []
        if not snapshots:
            insights.append("EMPTY_FLEET")
            return insights

        verdicts = Counter(s.verdict for s in snapshots)

        if verdicts[ToolVerdict.CIRCUIT_BREAK] >= 1:
            insights.append("RELIABILITY_CRISIS")

        retry_count = sum(1 for s in snapshots if "RETRY_STORM" in s.reasons)
        if retry_count >= 2:
            insights.append("RETRY_AMPLIFICATION")

        latency_count = sum(1 for s in snapshots if "LATENCY_OUTLIER" in s.reasons)
        if latency_count >= 2:
            insights.append("LATENCY_DOMINATED_FAILURES")

        # Error-code cluster pattern: shared top error across >=2 tools
        with_cluster = [s for s in snapshots if "DOMINANT_ERROR_CLUSTER" in s.reasons]
        if len(with_cluster) >= 2:
            top_codes = Counter()
            for s in with_cluster:
                if s.top_error_codes:
                    top_codes[s.top_error_codes[0]["code"]] += 1
            if top_codes and top_codes.most_common(1)[0][1] >= 2:
                insights.append("ERROR_CLUSTER_PATTERN")

        if sum(1 for s in snapshots if "SINGLE_CALLER_DEPENDENCY" in s.reasons) >= 2:
            insights.append("SINGLE_OWNER_RISK")

        if sum(1 for s in snapshots if "STALE_TOOL" in s.reasons) >= 2:
            insights.append("STALE_TOOL_BACKLOG")

        if sum(1 for s in snapshots if "NEW_TOOL" in s.reasons) >= 2:
            insights.append("NEW_TOOLS_PROBATION")

        if all(s.verdict == ToolVerdict.INSUFFICIENT_DATA for s in snapshots):
            insights.append("INSUFFICIENT_DATA")
        elif all(s.verdict == ToolVerdict.HEALTHY for s in snapshots):
            insights.append("HEALTHY_FLEET")

        if not insights:
            insights.append("MIXED_FLEET_SIGNALS")
        return insights


# --------------------------------------------------------------------------- #
# Public exports
# --------------------------------------------------------------------------- #


__all__ = [
    "ToolReliabilityAdvisor",
    "ToolReliabilityReport",
    "ToolSnapshot",
    "ReliabilityPortfolioSummary",
    "ToolReliabilityPlaybookAction",
    "ToolVerdict",
    "ToolReliabilityGrade",
    "ReliabilityBand",
    "ActionPriority",
    "RiskAppetite",
]
