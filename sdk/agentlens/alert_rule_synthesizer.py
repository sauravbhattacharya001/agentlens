"""Agentic alert-rule synthesizer for AgentLens.

The other AgentLens modules give you *detection* — but somebody still has
to sit down and write the :class:`agentlens.alerts.AlertRule` definitions
by hand, then pick "reasonable" thresholds, then re-pick them every time
the workload shifts. That's the kind of toil this module is designed to
get rid of.

:class:`AlertRuleSynthesizer` profiles a stream of historical events
(``AgentEvent`` instances *or* plain dicts — it sniffs both), models the
"normal" distribution of latency, error rate, cost, and tool usage, and
then synthesizes a ranked playbook of suggested :class:`AlertRule`\\ s
with thresholds chosen from the data itself rather than from a blog post.

Each suggestion carries:

* a P0/P1/P2 priority bucket,
* a severity (``info`` / ``warning`` / ``critical``),
* the metric + condition + threshold (ready to materialise as an
  ``AlertRule``),
* a window / cooldown sized from the volume of the workload,
* a human reason string + structured ``signals`` list,
* an estimated daily fire rate so you can spot rules that would page
  too often before you deploy them.

It is deterministic, depends only on the stdlib + AgentLens'
own ``alerts`` module, and ships three rendering modes (text / markdown
/ json) plus a ``build_rules()`` helper that hands you a
list of live :class:`AlertRule` objects ready to drop into an
:class:`AlertManager`.

Agency role:

* **Awareness** — surface metrics the operator might not have thought to
  alert on (cost spikes, tool-call timeouts, decision-confidence drops).
* **Recommendation** — propose concrete thresholds with reasoning.
* **Trust building** — predict per-rule fire-rate so the human can sanity
  check before flipping the rule on; nothing is deployed without
  explicit ``build_rules()`` or ``apply(manager)``.

Example
-------
::

    from agentlens import AlertRuleSynthesizer

    synth = AlertRuleSynthesizer(risk_appetite="cautious")
    report = synth.synthesize(events, workload_label="prod-last-24h")
    print(report.render_markdown())

    # Deploy the top-3 P0 rules into a live AlertManager:
    from agentlens import AlertManager
    mgr = AlertManager()
    report.apply(mgr, max_rules=3, min_priority="P0")
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from agentlens.alerts import AlertRule, Condition, Severity


# --------------------------------------------------------------------------- #
# Enums & light value types
# --------------------------------------------------------------------------- #


class SuggestionPriority(str, Enum):
    """Priority bucket for a synthesized rule."""

    P0 = "P0"  # deploy immediately — high-impact, low-noise
    P1 = "P1"  # deploy this week — useful, well-bounded fire rate
    P2 = "P2"  # consider — nice-to-have or noisy without tuning


_PRIORITY_RANK = {SuggestionPriority.P0: 0, SuggestionPriority.P1: 1, SuggestionPriority.P2: 2}


_RISK_APPETITES = ("cautious", "balanced", "aggressive")


# Multipliers applied to the data-driven threshold to make the rule
# easier or harder to trigger. Cautious operators page earlier
# (lower threshold for "high is bad" metrics).
_RISK_THRESHOLD_MULT: dict[str, dict[str, float]] = {
    "cautious":   {"high_bad": 0.90, "low_bad": 1.10},
    "balanced":   {"high_bad": 1.00, "low_bad": 1.00},
    "aggressive": {"high_bad": 1.20, "low_bad": 0.85},
}


# --------------------------------------------------------------------------- #
# Profile of the input workload
# --------------------------------------------------------------------------- #


@dataclass
class WorkloadProfile:
    """Summary statistics extracted from the input event stream."""

    total_events: int = 0
    error_events: int = 0
    tool_events: int = 0
    llm_events: int = 0
    span_seconds: float = 0.0  # max(ts) - min(ts) across events

    error_rate: float = 0.0  # 0..1
    events_per_minute: float = 0.0

    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None

    cost_p95: Optional[float] = None  # total_tokens p95 per event (proxy for cost)
    cost_p99: Optional[float] = None

    top_error_types: list[tuple[str, int]] = field(default_factory=list)
    top_tools: list[tuple[str, int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "error_events": self.error_events,
            "tool_events": self.tool_events,
            "llm_events": self.llm_events,
            "span_seconds": round(self.span_seconds, 2),
            "error_rate": round(self.error_rate, 4),
            "events_per_minute": round(self.events_per_minute, 2),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "cost_p95": self.cost_p95,
            "cost_p99": self.cost_p99,
            "top_error_types": self.top_error_types,
            "top_tools": self.top_tools,
        }


# --------------------------------------------------------------------------- #
# A single rule suggestion
# --------------------------------------------------------------------------- #


@dataclass
class RuleSuggestion:
    """One synthesized alert-rule recommendation."""

    name: str
    metric: str
    condition: Condition
    threshold: float
    severity: Severity
    window_seconds: int
    cooldown_seconds: int
    priority: SuggestionPriority
    reason: str
    signals: list[str] = field(default_factory=list)
    estimated_fires_per_day: float = 0.0
    agent_filter: Optional[str] = None

    def to_rule(self) -> AlertRule:
        """Materialise this suggestion into a live :class:`AlertRule`."""
        return AlertRule(
            name=self.name,
            metric=self.metric,
            condition=self.condition,
            threshold=self.threshold,
            window_seconds=self.window_seconds,
            cooldown_seconds=self.cooldown_seconds,
            severity=self.severity,
            enabled=True,
            agent_filter=self.agent_filter,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "condition": self.condition.value,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "window_seconds": self.window_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "priority": self.priority.value,
            "reason": self.reason,
            "signals": list(self.signals),
            "estimated_fires_per_day": round(self.estimated_fires_per_day, 2),
            "agent_filter": self.agent_filter,
        }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


@dataclass
class RuleSynthesisReport:
    """The full output of :meth:`AlertRuleSynthesizer.synthesize`."""

    workload_label: str
    profile: WorkloadProfile
    suggestions: list[RuleSuggestion]
    risk_appetite: str
    summary: str
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Convenience selectors
    # ------------------------------------------------------------------ #

    def by_priority(self, priority: str | SuggestionPriority) -> list[RuleSuggestion]:
        if isinstance(priority, str):
            priority = SuggestionPriority(priority)
        return [s for s in self.suggestions if s.priority == priority]

    def build_rules(
        self,
        *,
        max_rules: Optional[int] = None,
        min_priority: str | SuggestionPriority = SuggestionPriority.P1,
    ) -> list[AlertRule]:
        """Return materialised :class:`AlertRule` objects for the highest-priority suggestions.

        ``min_priority`` is inclusive: passing ``P1`` returns both P0 and P1.
        """
        if isinstance(min_priority, str):
            min_priority = SuggestionPriority(min_priority)
        cutoff = _PRIORITY_RANK[min_priority]
        picked = [s for s in self.suggestions if _PRIORITY_RANK[s.priority] <= cutoff]
        if max_rules is not None:
            picked = picked[:max_rules]
        return [s.to_rule() for s in picked]

    def apply(
        self,
        alert_manager: Any,
        *,
        max_rules: Optional[int] = None,
        min_priority: str | SuggestionPriority = SuggestionPriority.P0,
    ) -> list[AlertRule]:
        """Install the chosen rules into an :class:`AlertManager`.

        Returns the rules that were added. The ``alert_manager`` argument
        is typed as ``Any`` to keep the import cycle light — any object
        with ``add_rule(rule)`` works.
        """
        rules = self.build_rules(max_rules=max_rules, min_priority=min_priority)
        for r in rules:
            alert_manager.add_rule(r)
        return rules

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def render_text(self) -> str:
        lines = [f"Alert Rule Synthesis — {self.workload_label}", "=" * 60, self.summary, ""]
        p = self.profile
        lines.append(
            f"Workload: {p.total_events} events over {p.span_seconds:.0f}s "
            f"({p.events_per_minute:.1f}/min) — error_rate={p.error_rate * 100:.2f}%"
        )
        if p.latency_p95_ms is not None:
            lines.append(
                f"Latency: p50={p.latency_p50_ms:.0f}ms / p95={p.latency_p95_ms:.0f}ms / "
                f"p99={p.latency_p99_ms:.0f}ms"
            )
        lines.append("")
        for prio in (SuggestionPriority.P0, SuggestionPriority.P1, SuggestionPriority.P2):
            picks = self.by_priority(prio)
            if not picks:
                continue
            lines.append(f"[{prio.value}] ({len(picks)})")
            for s in picks:
                lines.append(
                    f"  - {s.name}  ({s.metric} {s.condition.value} {s.threshold:g}, "
                    f"sev={s.severity.value}, ~{s.estimated_fires_per_day:.1f}/day)"
                )
                lines.append(f"      {s.reason}")
            lines.append("")
        if self.notes:
            lines.append("Notes:")
            for n in self.notes:
                lines.append(f"  • {n}")
        return "\n".join(lines).rstrip()

    def render_markdown(self) -> str:
        p = self.profile
        out: list[str] = [
            f"# Alert Rule Synthesis — `{self.workload_label}`",
            "",
            f"_{self.summary}_",
            "",
            "## Workload Profile",
            "",
            f"- **Events:** {p.total_events:,} over {p.span_seconds:.0f}s "
            f"({p.events_per_minute:.1f}/min)",
            f"- **Error rate:** {p.error_rate * 100:.2f}% "
            f"({p.error_events} of {p.total_events})",
        ]
        if p.latency_p95_ms is not None:
            out.append(
                f"- **Latency:** p50={p.latency_p50_ms:.0f}ms / "
                f"p95={p.latency_p95_ms:.0f}ms / p99={p.latency_p99_ms:.0f}ms"
            )
        if p.cost_p95 is not None:
            out.append(f"- **Tokens/event:** p95={p.cost_p95:.0f} / p99={p.cost_p99:.0f}")
        if p.top_tools:
            tools = ", ".join(f"`{n}`×{c}" for n, c in p.top_tools[:5])
            out.append(f"- **Top tools:** {tools}")
        out += ["", f"**Risk appetite:** `{self.risk_appetite}`", ""]

        for prio in (SuggestionPriority.P0, SuggestionPriority.P1, SuggestionPriority.P2):
            picks = self.by_priority(prio)
            if not picks:
                continue
            out += [f"## {prio.value} — {len(picks)} suggestion(s)", ""]
            out += [
                "| Rule | Metric | Condition | Threshold | Sev | Window | Cooldown | ~Fires/day |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for s in picks:
                out.append(
                    f"| `{s.name}` | `{s.metric}` | `{s.condition.value}` | "
                    f"{s.threshold:g} | {s.severity.value} | {s.window_seconds}s | "
                    f"{s.cooldown_seconds}s | {s.estimated_fires_per_day:.1f} |"
                )
            out.append("")
            for s in picks:
                out += [f"### `{s.name}`", "", s.reason, ""]
                if s.signals:
                    out.append("Signals:")
                    for sig in s.signals:
                        out.append(f"- {sig}")
                    out.append("")
        if self.notes:
            out += ["## Notes", ""]
            for n in self.notes:
                out.append(f"- {n}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def render_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workload_label": self.workload_label,
            "risk_appetite": self.risk_appetite,
            "summary": self.summary,
            "profile": self.profile.as_dict(),
            "suggestions": [s.as_dict() for s in self.suggestions],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Synthesizer
# --------------------------------------------------------------------------- #


def _pct(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in [0, 100])."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(math.ceil(p / 100.0 * len(s))) - 1))
    return float(s[k])


def _round_to_nice(x: float) -> float:
    """Round a threshold to a 'nice' number for humans (2 sig figs)."""
    if x <= 0:
        return 0.0
    magnitude = 10 ** math.floor(math.log10(x))
    scaled = x / magnitude
    if scaled < 1.5:
        return round(1.0 * magnitude, 6)
    if scaled < 3.5:
        return round(round(scaled * 2) / 2 * magnitude, 6)
    if scaled < 7.5:
        return round(round(scaled) * magnitude, 6)
    return round(10 * magnitude, 6)


def _event_field(ev: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a pydantic event or a plain dict."""
    if isinstance(ev, dict):
        return ev.get(name, default)
    return getattr(ev, name, default)


class AlertRuleSynthesizer:
    """Synthesize :class:`AlertRule` candidates from a historical event stream.

    Parameters
    ----------
    risk_appetite:
        ``"cautious"`` (page early), ``"balanced"``, or ``"aggressive"``
        (page only on clear breaches). Defaults to ``"balanced"``.
    target_fires_per_day:
        Soft cap on the predicted daily fire rate for a P0 rule. P0
        suggestions whose predicted rate exceeds this are demoted to P1
        with a note. Defaults to 5.
    min_window_seconds / max_window_seconds:
        Bounds on the auto-sized ``window_seconds`` for rules.
    """

    def __init__(
        self,
        *,
        risk_appetite: str = "balanced",
        target_fires_per_day: float = 5.0,
        min_window_seconds: int = 60,
        max_window_seconds: int = 900,
    ) -> None:
        if risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"risk_appetite must be one of {_RISK_APPETITES!r}, got {risk_appetite!r}"
            )
        if target_fires_per_day <= 0:
            raise ValueError("target_fires_per_day must be > 0")
        if min_window_seconds <= 0 or max_window_seconds < min_window_seconds:
            raise ValueError("invalid window bounds")
        self.risk_appetite = risk_appetite
        self.target_fires_per_day = float(target_fires_per_day)
        self.min_window_seconds = int(min_window_seconds)
        self.max_window_seconds = int(max_window_seconds)

    # ------------------------------------------------------------------ #
    # Profile
    # ------------------------------------------------------------------ #

    def profile(self, events: Iterable[Any]) -> WorkloadProfile:
        """Compute summary statistics from a stream of events."""
        evs = list(events)
        prof = WorkloadProfile(total_events=len(evs))
        if not evs:
            return prof

        latencies: list[float] = []
        tokens_per_event: list[float] = []
        timestamps: list[float] = []
        error_types: dict[str, int] = {}
        tool_counts: dict[str, int] = {}

        for ev in evs:
            etype = (_event_field(ev, "event_type") or "generic").lower()
            if etype == "error":
                prof.error_events += 1
                output = _event_field(ev, "output_data") or {}
                if isinstance(output, dict):
                    err_kind = (
                        output.get("error_type")
                        or output.get("type")
                        or output.get("error")
                        or "unknown"
                    )
                    error_types[str(err_kind)] = error_types.get(str(err_kind), 0) + 1
            elif etype == "tool_call":
                prof.tool_events += 1
                tc = _event_field(ev, "tool_call")
                tool_name = None
                if tc is not None:
                    tool_name = _event_field(tc, "tool_name")
                if tool_name:
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif etype == "llm_call":
                prof.llm_events += 1

            d = _event_field(ev, "duration_ms")
            if isinstance(d, (int, float)) and d >= 0:
                latencies.append(float(d))

            tin = _event_field(ev, "tokens_in", 0) or 0
            tout = _event_field(ev, "tokens_out", 0) or 0
            total = float(tin) + float(tout)
            if total > 0:
                tokens_per_event.append(total)

            ts = _event_field(ev, "timestamp")
            if ts is not None:
                try:
                    if hasattr(ts, "timestamp"):
                        timestamps.append(ts.timestamp())
                    elif isinstance(ts, (int, float)):
                        timestamps.append(float(ts))
                    elif isinstance(ts, str):
                        # Best-effort ISO parse without importing dateutil
                        from datetime import datetime as _dt
                        timestamps.append(_dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
                except Exception:
                    pass

        prof.error_rate = prof.error_events / prof.total_events if prof.total_events else 0.0
        if timestamps:
            span = max(timestamps) - min(timestamps)
            prof.span_seconds = max(span, 0.0)
            if prof.span_seconds > 0:
                prof.events_per_minute = prof.total_events / (prof.span_seconds / 60.0)
            else:
                prof.events_per_minute = float(prof.total_events)
        else:
            prof.span_seconds = 0.0
            prof.events_per_minute = float(prof.total_events)

        if latencies:
            prof.latency_p50_ms = _pct(latencies, 50)
            prof.latency_p95_ms = _pct(latencies, 95)
            prof.latency_p99_ms = _pct(latencies, 99)

        if tokens_per_event:
            prof.cost_p95 = _pct(tokens_per_event, 95)
            prof.cost_p99 = _pct(tokens_per_event, 99)

        prof.top_error_types = sorted(error_types.items(), key=lambda kv: -kv[1])[:5]
        prof.top_tools = sorted(tool_counts.items(), key=lambda kv: -kv[1])[:5]
        return prof

    # ------------------------------------------------------------------ #
    # Synthesis
    # ------------------------------------------------------------------ #

    def synthesize(
        self,
        events: Iterable[Any],
        *,
        workload_label: str = "workload",
        agent_filter: Optional[str] = None,
    ) -> RuleSynthesisReport:
        """Produce a ranked playbook of suggested alert rules."""
        prof = self.profile(events)
        suggestions: list[RuleSuggestion] = []
        notes: list[str] = []

        if prof.total_events == 0:
            return RuleSynthesisReport(
                workload_label=workload_label,
                profile=prof,
                suggestions=[],
                risk_appetite=self.risk_appetite,
                summary="No events supplied — nothing to synthesize.",
                notes=["Provide a non-empty event stream covering at least a few minutes of traffic."],
            )

        if prof.total_events < 30:
            notes.append(
                f"Only {prof.total_events} events analysed — thresholds are best-effort; "
                "consider re-running on >=30 events for tighter bounds."
            )

        mult_high = _RISK_THRESHOLD_MULT[self.risk_appetite]["high_bad"]
        mult_low = _RISK_THRESHOLD_MULT[self.risk_appetite]["low_bad"]

        # Auto-size windows from event rate: try to fit ~30 events / window,
        # clamped to [min_window_seconds, max_window_seconds].
        if prof.events_per_minute > 0:
            target_window_s = int(min(self.max_window_seconds,
                                      max(self.min_window_seconds,
                                          round(30.0 / prof.events_per_minute * 60))))
        else:
            target_window_s = self.min_window_seconds
        cooldown_s = max(target_window_s * 3, 300)

        # ----- 1) Error-rate rule -----
        if prof.total_events >= 10:
            baseline = prof.error_rate
            # Threshold: max(baseline * 2, 0.05) — but if baseline is 0 we
            # use a conservative absolute floor.
            if baseline <= 0:
                threshold = 0.05
                reason = (
                    "Observed error rate is 0% so we set an absolute floor of "
                    "5%. Anything above this is a real regression."
                )
            else:
                threshold = max(baseline * 2.0, 0.05)
                reason = (
                    f"Observed error rate is {baseline * 100:.2f}%. We page when "
                    f"it doubles (and at least 5%) — that's a clear regression "
                    "above normal noise."
                )
            threshold = round(_round_to_nice(threshold * mult_high) / 1, 4)
            # Keep error_rate threshold sensible (capped at 1.0)
            threshold = min(max(threshold, 0.01), 1.0)

            est_fires = self._estimate_fires_per_day(
                base_rate_per_day=self._daily(prof.total_events, prof.span_seconds),
                triggers_per_check=baseline / max(threshold, 1e-9) if threshold > 0 else 0.0,
                target_window_s=target_window_s,
                cooldown_s=cooldown_s,
            )
            severity = (
                Severity.CRITICAL
                if threshold >= 0.20 or self.risk_appetite == "cautious"
                else Severity.WARNING
            )
            priority = SuggestionPriority.P0 if baseline > 0 else SuggestionPriority.P1
            suggestions.append(RuleSuggestion(
                name="auto_error_rate_spike",
                metric="error_rate",
                condition=Condition.GREATER_THAN,
                threshold=threshold,
                severity=severity,
                window_seconds=target_window_s,
                cooldown_seconds=cooldown_s,
                priority=priority,
                reason=reason,
                signals=[
                    f"baseline_error_rate={baseline * 100:.2f}%",
                    f"error_events={prof.error_events}/{prof.total_events}",
                ],
                estimated_fires_per_day=est_fires,
                agent_filter=agent_filter,
            ))

        # ----- 2) Latency p95 rule -----
        if prof.latency_p95_ms and prof.latency_p95_ms > 0:
            raw_threshold = prof.latency_p95_ms * 1.5
            threshold = _round_to_nice(raw_threshold * mult_high)
            severity = (
                Severity.CRITICAL
                if (prof.latency_p99_ms or 0) >= 5000
                else Severity.WARNING
            )
            # Estimate: a p95 metric in a window will exceed 1.5x its baseline
            # very rarely — about 1% of windows under a stationary workload.
            checks_per_day = self._daily(1, target_window_s)
            est_fires = self._cap_by_cooldown(checks_per_day * 0.01, cooldown_s)
            suggestions.append(RuleSuggestion(
                name="auto_latency_p95_high",
                metric="latency_p95",
                condition=Condition.GREATER_THAN,
                threshold=threshold,
                severity=severity,
                window_seconds=target_window_s,
                cooldown_seconds=cooldown_s,
                priority=SuggestionPriority.P0 if prof.latency_p95_ms >= 1000 else SuggestionPriority.P1,
                reason=(
                    f"Baseline p95 latency is {prof.latency_p95_ms:.0f}ms; "
                    f"alert when sustained latency goes ~1.5x above that "
                    f"({threshold:.0f}ms) — usually means a stuck dependency or "
                    "noisy-neighbour."
                ),
                signals=[
                    f"p50={prof.latency_p50_ms:.0f}ms",
                    f"p95={prof.latency_p95_ms:.0f}ms",
                    f"p99={prof.latency_p99_ms:.0f}ms" if prof.latency_p99_ms else "p99=n/a",
                ],
                estimated_fires_per_day=est_fires,
                agent_filter=agent_filter,
            ))

        # ----- 3) Heartbeat / absence rule -----
        if prof.events_per_minute >= 0.5 and prof.span_seconds >= 60:
            # If we usually see >=0.5 events/min, an absence of *any* event
            # for several heartbeat intervals is suspicious.
            heartbeat_window = max(
                self.min_window_seconds,
                min(self.max_window_seconds, int(180 / max(prof.events_per_minute, 0.1))),
            )
            est_fires = 0.5 if self.risk_appetite == "cautious" else 0.2
            suggestions.append(RuleSuggestion(
                name="auto_heartbeat_absent",
                metric="heartbeat",
                condition=Condition.ABSENT,
                threshold=0.0,
                severity=Severity.CRITICAL,
                window_seconds=heartbeat_window,
                cooldown_seconds=max(heartbeat_window * 2, 300),
                priority=SuggestionPriority.P0,
                reason=(
                    f"Workload normally sees ~{prof.events_per_minute:.1f} events/min. "
                    f"If nothing arrives for {heartbeat_window}s we likely have a stuck "
                    "ingest or a dead agent — page immediately."
                ),
                signals=[f"baseline_rate_per_min={prof.events_per_minute:.2f}"],
                estimated_fires_per_day=est_fires,
                agent_filter=agent_filter,
            ))

        # ----- 4) Cost / tokens rule -----
        if prof.cost_p95 and prof.cost_p95 > 0:
            # total_tokens metric is summed over the window. Project a
            # reasonable "spike" threshold = p99 per-event * events-per-window * 1.25.
            ev_per_window = (
                prof.events_per_minute * (target_window_s / 60.0)
                if prof.events_per_minute > 0 else 1.0
            )
            raw = max(prof.cost_p95, prof.cost_p99 or prof.cost_p95) * ev_per_window * 1.25
            threshold = _round_to_nice(raw * mult_high)
            est_fires = self._cap_by_cooldown(
                self._daily(1, target_window_s) * 0.02, cooldown_s,
            )
            suggestions.append(RuleSuggestion(
                name="auto_token_spend_spike",
                metric="total_tokens",
                condition=Condition.GREATER_THAN,
                threshold=threshold,
                severity=Severity.WARNING,
                window_seconds=target_window_s,
                cooldown_seconds=cooldown_s,
                priority=SuggestionPriority.P1,
                reason=(
                    f"Per-event token p95={prof.cost_p95:.0f}/p99={prof.cost_p99:.0f}. "
                    f"Alert at {threshold:.0f} total tokens in a {target_window_s}s window — "
                    "catches runaway prompts before they hit the budget."
                ),
                signals=[
                    f"tokens_p95_per_event={prof.cost_p95:.0f}",
                    f"tokens_p99_per_event={prof.cost_p99:.0f}",
                    f"events_per_window~{ev_per_window:.1f}",
                ],
                estimated_fires_per_day=est_fires,
                agent_filter=agent_filter,
            ))

        # ----- 5) Volume floor (low traffic) — aggressive only suppresses -----
        if prof.events_per_minute >= 2 and self.risk_appetite != "aggressive":
            # event_count over window dropping below 25% of baseline
            ev_per_window = prof.events_per_minute * (target_window_s / 60.0)
            threshold = max(1.0, _round_to_nice(ev_per_window * 0.25 * mult_low))
            est_fires = 0.3 if self.risk_appetite == "cautious" else 0.15
            suggestions.append(RuleSuggestion(
                name="auto_traffic_floor",
                metric="event_count",
                condition=Condition.LESS_THAN,
                threshold=threshold,
                severity=Severity.WARNING,
                window_seconds=target_window_s,
                cooldown_seconds=cooldown_s,
                priority=SuggestionPriority.P2,
                reason=(
                    f"Baseline ~{ev_per_window:.1f} events per {target_window_s}s window. "
                    f"A drop below {threshold:g} usually signals an upstream outage or "
                    "a paused producer."
                ),
                signals=[f"baseline_events_per_window={ev_per_window:.1f}"],
                estimated_fires_per_day=est_fires,
                agent_filter=agent_filter,
            ))

        # ----- Cross-cutting: demote noisy P0s -----
        for s in suggestions:
            if (
                s.priority == SuggestionPriority.P0
                and s.estimated_fires_per_day > self.target_fires_per_day
            ):
                s.priority = SuggestionPriority.P1
                s.signals.append(
                    f"demoted_from_P0: predicted_fires/day={s.estimated_fires_per_day:.1f} "
                    f"> target={self.target_fires_per_day:g}"
                )

        # ----- Sort: P0 first, then by est_fires asc (less noisy first) -----
        suggestions.sort(key=lambda s: (_PRIORITY_RANK[s.priority], s.estimated_fires_per_day))

        p0_count = sum(1 for s in suggestions if s.priority == SuggestionPriority.P0)
        p1_count = sum(1 for s in suggestions if s.priority == SuggestionPriority.P1)
        p2_count = sum(1 for s in suggestions if s.priority == SuggestionPriority.P2)
        summary = (
            f"Synthesized {len(suggestions)} alert rule(s) — "
            f"P0={p0_count}, P1={p1_count}, P2={p2_count} "
            f"(risk_appetite={self.risk_appetite})."
        )

        return RuleSynthesisReport(
            workload_label=workload_label,
            profile=prof,
            suggestions=suggestions,
            risk_appetite=self.risk_appetite,
            summary=summary,
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _daily(count: float, span_seconds: float) -> float:
        """Project a per-day rate from a sample observed over ``span_seconds``."""
        if span_seconds <= 0:
            return float(count)
        return count * (86400.0 / span_seconds)

    @staticmethod
    def _cap_by_cooldown(rate_per_day: float, cooldown_s: int) -> float:
        """Cap a fire-rate by the rule's cooldown."""
        if cooldown_s <= 0:
            return rate_per_day
        max_fires_per_day = 86400.0 / cooldown_s
        return min(rate_per_day, max_fires_per_day)

    def _estimate_fires_per_day(
        self,
        *,
        base_rate_per_day: float,
        triggers_per_check: float,
        target_window_s: int,
        cooldown_s: int,
    ) -> float:
        """Heuristic projection of how often a rule will fire per day."""
        # Assume the workload as observed represents 'normal'. A real
        # spike would push the metric above the threshold once every N
        # windows. With triggers_per_check ~= 1.0 the rule sits right at
        # the threshold (very noisy); we squash that into [0, 1].
        prob = max(0.0, min(0.5, triggers_per_check * 0.5))
        checks_per_day = self._daily(1, target_window_s) if target_window_s > 0 else 0.0
        raw = prob * checks_per_day
        return self._cap_by_cooldown(raw, cooldown_s)


__all__ = [
    "AlertRuleSynthesizer",
    "RuleSynthesisReport",
    "RuleSuggestion",
    "WorkloadProfile",
    "SuggestionPriority",
]
