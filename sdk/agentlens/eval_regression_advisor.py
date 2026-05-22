"""Agentic baseline-vs-current eval/regression advisor for AgentLens.

:class:`EvalRegressionAdvisor` is the 12th agentic sibling in the
AgentLens advisor family (sampling / alert_rule_synthesizer /
incident_radar / slo_burn_rate / trace_completion / agent_loop /
cost_attribution / prompt_drift / cacheability /
tool_reliability / model_migration / cost_optimizer).

It answers a single, focused question that every team running an
agent in production keeps asking after a deploy, a model swap, or a
prompt rev:

    "What got *worse* compared to last week, by how much, and which
    call-site should I open the runbook on first?"

Inputs:

* ``baseline`` -- any iterable of :class:`AgentEvent` / dict /
  attr-bearing objects describing the reference window (e.g. last
  week, or pre-deploy).
* ``current``  -- same shape, describing the window under test.
* ``key_fn``   -- optional callable returning a stable call-site key.
  Defaults to ``f"{model}::{tool}"`` falling back to ``event_type``.

For each call-site that appears in either window the advisor computes
per-metric deltas (latency p50/p95, error rate, tokens-in mean, cost
per call, throughput / events-per-minute) plus a 0..100 ``risk_score``
modulated by ``risk_appetite``.  Each site gets a structured verdict:

* ``MAJOR_REGRESSION`` (P0) -- something got materially worse on a
  hot site (e.g. error rate jumped >=15 pts or p95 doubled).
* ``REGRESSION`` (P1) -- meaningful deterioration above noise floor.
* ``NEW_FAILURE_MODE`` (P0/P1) -- the call-site started erroring in
  the current window after being clean in baseline.
* ``DISAPPEARED`` (P2) -- call-site dropped out of traffic entirely.
* ``NEW_CALL_SITE`` (P2) -- call-site started showing up in current.
* ``IMPROVEMENT`` (P3) -- got better; surfaced for celebration / to
  inform rollback decisions.
* ``STABLE`` (P3) -- within noise.
* ``INSUFFICIENT_DATA`` -- below ``min_sample`` either side.

Pure stdlib + the existing :mod:`agentlens.budget` pricing helpers.
Deterministic given an injectable clock; never mutates inputs.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from agentlens.budget import get_pricing as _get_pricing


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class RegressionVerdict(Enum):
    MAJOR_REGRESSION = "major_regression"
    REGRESSION = "regression"
    NEW_FAILURE_MODE = "new_failure_mode"
    DISAPPEARED = "disappeared"
    NEW_CALL_SITE = "new_call_site"
    IMPROVEMENT = "improvement"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


class ActionPriority(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RegressionGrade(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


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
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"unknown risk_appetite '{value}'; "
                "expected cautious|balanced|aggressive"
            ) from exc


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EvalRegressionOptions:
    """Tunables for :class:`EvalRegressionAdvisor`."""

    min_sample: int = 5
    """Minimum events per side for a regular verdict."""

    latency_p95_regression_pct: float = 0.30
    """p95 latency relative increase that qualifies as a regression."""

    latency_p95_major_pct: float = 1.00
    """p95 latency relative increase that qualifies as MAJOR."""

    error_rate_regression_pts: float = 0.05
    """Absolute error-rate increase (percentage points) for regression."""

    error_rate_major_pts: float = 0.15
    """Absolute error-rate increase that qualifies as MAJOR."""

    token_in_regression_pct: float = 0.20
    """Mean tokens-in inflation that qualifies as a regression."""

    cost_regression_pct: float = 0.20
    """Mean $/call inflation that qualifies as a regression."""

    throughput_drop_pct: float = 0.50
    """Drop in events/min (vs baseline) that triggers DISAPPEARED-ish flag."""

    improvement_pct: float = 0.20
    """Symmetric threshold for surfacing IMPROVEMENTs."""

    top_n: int = 10
    """Cap on the number of slices rendered in text/markdown output."""


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    baseline: float
    current: float
    delta: float
    delta_pct: Optional[float]  # None when baseline ~0
    severity: int  # 0..100 contribution to the slice risk score


@dataclass(frozen=True)
class CallSiteRegression:
    key: str
    model: str
    tool: Optional[str]
    baseline_events: int
    current_events: int
    baseline_window_seconds: float
    current_window_seconds: float
    verdict: RegressionVerdict
    priority: ActionPriority
    risk_score: int
    deltas: tuple[MetricDelta, ...]
    reason_codes: tuple[str, ...]
    baseline_error_rate: float
    current_error_rate: float
    baseline_p95_ms: Optional[float]
    current_p95_ms: Optional[float]
    baseline_avg_tokens_in: float
    current_avg_tokens_in: float
    baseline_avg_cost_usd: float
    current_avg_cost_usd: float
    projected_extra_cost_per_day_usd: float


@dataclass(frozen=True)
class PlaybookAction:
    id: str
    priority: ActionPriority
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_slice_keys: tuple[str, ...] = ()
    suggested_value: Optional[str] = None


@dataclass(frozen=True)
class RegressionPortfolio:
    baseline_events: int
    current_events: int
    baseline_window_seconds: float
    current_window_seconds: float
    total_call_sites: int
    p0_count: int
    p1_count: int
    p2_count: int
    p3_count: int
    overall_risk_score: int
    grade: RegressionGrade
    band: str  # HEALTHY / WATCH / DEGRADED / CRITICAL
    projected_extra_cost_per_day_usd: float


@dataclass(frozen=True)
class EvalRegressionReport:
    portfolio: RegressionPortfolio
    slices: tuple[CallSiteRegression, ...]
    playbook: tuple[PlaybookAction, ...]
    insights: tuple[str, ...]
    headline: str
    generated_at: datetime

    def to_text(self) -> str:
        return _render_text(self)

    def to_markdown(self) -> str:
        return _render_markdown(self)

    def to_json(self) -> str:
        return json.dumps(_to_jsonable(self), sort_keys=True, indent=2, default=str)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _coerce_event(ev: Any) -> Mapping[str, Any]:
    if hasattr(ev, "model_dump"):
        try:
            return ev.model_dump()
        except Exception:  # pragma: no cover
            pass
    if isinstance(ev, Mapping):
        return copy.deepcopy(dict(ev))
    out: dict[str, Any] = {}
    for key in (
        "event_id",
        "session_id",
        "event_type",
        "timestamp",
        "model",
        "tool",
        "tokens_in",
        "tokens_out",
        "duration_ms",
        "latency_ms",
        "status",
        "error",
        "is_error",
        "metadata",
    ):
        if hasattr(ev, key):
            out[key] = getattr(ev, key)
    return out


def _ev_key(ev: Mapping[str, Any]) -> str:
    model = str(ev.get("model") or "unknown")
    tool = ev.get("tool")
    if not tool:
        md = ev.get("metadata")
        if isinstance(md, Mapping):
            tool = md.get("tool") or md.get("function_name")
    if not tool:
        tool = ev.get("event_type") or "default"
    return f"{model}::{tool}"


def _ev_model_tool(key: str) -> tuple[str, Optional[str]]:
    if "::" in key:
        a, b = key.split("::", 1)
        return a, b or None
    return key, None


def _ev_duration(ev: Mapping[str, Any]) -> Optional[float]:
    for k in ("duration_ms", "latency_ms"):
        v = ev.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f >= 0:
            return f
    return None


def _ev_is_error(ev: Mapping[str, Any]) -> bool:
    for k in ("is_error", "error"):
        v = ev.get(k)
        if isinstance(v, bool):
            if v:
                return True
        elif v:
            return True
    status = ev.get("status")
    if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
        return True
    et = str(ev.get("event_type") or "").lower()
    if et in {"error", "exception", "tool_error"}:
        return True
    return False


def _ev_ts(ev: Mapping[str, Any]) -> Optional[datetime]:
    ts = ev.get("timestamp")
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _percentile(sorted_values: Sequence[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _cost_per_call(model: str, tokens_in: float, tokens_out: float) -> float:
    pricing = _get_pricing(model) or {}
    inp = pricing.get("input")
    out = pricing.get("output")
    if inp is None or out is None:
        return 0.0
    return (tokens_in * inp + tokens_out * out) / 1_000_000.0


def _window_seconds(events: Sequence[Mapping[str, Any]]) -> float:
    ts = [t for t in (_ev_ts(e) for e in events) if t is not None]
    if len(ts) < 2:
        return 0.0
    return max(0.0, (max(ts) - min(ts)).total_seconds())


def _appetite_thresholds(
    opts: EvalRegressionOptions, appetite: RiskAppetite
) -> EvalRegressionOptions:
    if appetite is RiskAppetite.BALANCED:
        return opts
    if appetite is RiskAppetite.CAUTIOUS:
        m = 0.75  # smaller deltas qualify
    else:  # AGGRESSIVE
        m = 1.40  # tolerate more before flagging
    return EvalRegressionOptions(
        min_sample=opts.min_sample,
        latency_p95_regression_pct=opts.latency_p95_regression_pct * m,
        latency_p95_major_pct=opts.latency_p95_major_pct * m,
        error_rate_regression_pts=opts.error_rate_regression_pts * m,
        error_rate_major_pts=opts.error_rate_major_pts * m,
        token_in_regression_pct=opts.token_in_regression_pct * m,
        cost_regression_pct=opts.cost_regression_pct * m,
        throughput_drop_pct=min(0.9, opts.throughput_drop_pct * m),
        improvement_pct=opts.improvement_pct * m,
        top_n=opts.top_n,
    )


# --------------------------------------------------------------------------- #
# Per-slice scoring
# --------------------------------------------------------------------------- #


def _score_slice(
    key: str,
    baseline: Sequence[Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
    baseline_window_s: float,
    current_window_s: float,
    opts: EvalRegressionOptions,
) -> CallSiteRegression:
    model, tool = _ev_model_tool(key)
    b_n = len(baseline)
    c_n = len(current)

    # Insufficient / disappeared / new short-circuits.
    if b_n == 0 and c_n == 0:
        return _make_site(key, model, tool, 0, 0, 0.0, 0.0,
                          RegressionVerdict.INSUFFICIENT_DATA,
                          ActionPriority.P3, 0, ())
    if b_n == 0 and c_n > 0:
        if c_n < opts.min_sample:
            verdict = RegressionVerdict.NEW_CALL_SITE
            priority = ActionPriority.P2
        else:
            err_now = sum(1 for e in current if _ev_is_error(e)) / max(1, c_n)
            if err_now >= opts.error_rate_major_pts:
                verdict = RegressionVerdict.NEW_FAILURE_MODE
                priority = ActionPriority.P0
            elif err_now >= opts.error_rate_regression_pts:
                verdict = RegressionVerdict.NEW_FAILURE_MODE
                priority = ActionPriority.P1
            else:
                verdict = RegressionVerdict.NEW_CALL_SITE
                priority = ActionPriority.P2
        return _make_site(
            key, model, tool, 0, c_n, 0.0, current_window_s,
            verdict, priority,
            risk_score=40 if verdict is RegressionVerdict.NEW_FAILURE_MODE else 15,
            deltas=(),
            current_n=c_n,
            current_window_seconds=current_window_s,
            current_events=current,
        )
    if c_n == 0 and b_n > 0:
        # DISAPPEARED is mostly informational unless this was a hot site.
        priority = ActionPriority.P2 if b_n >= opts.min_sample else ActionPriority.P3
        return _make_site(
            key, model, tool, b_n, 0, baseline_window_s, 0.0,
            RegressionVerdict.DISAPPEARED, priority,
            risk_score=10, deltas=(),
            baseline_events=baseline,
            baseline_window_seconds=baseline_window_s,
        )
    if b_n < opts.min_sample or c_n < opts.min_sample:
        return _make_site(
            key, model, tool, b_n, c_n, baseline_window_s, current_window_s,
            RegressionVerdict.INSUFFICIENT_DATA, ActionPriority.P3,
            0, (),
            baseline_events=baseline, current_events=current,
            baseline_window_seconds=baseline_window_s,
            current_window_seconds=current_window_s,
        )

    # Real comparison.
    b_lat = sorted(d for d in (_ev_duration(e) for e in baseline) if d is not None)
    c_lat = sorted(d for d in (_ev_duration(e) for e in current) if d is not None)
    b_p95 = _percentile(b_lat, 0.95)
    c_p95 = _percentile(c_lat, 0.95)
    b_p50 = _percentile(b_lat, 0.50)
    c_p50 = _percentile(c_lat, 0.50)

    b_err = sum(1 for e in baseline if _ev_is_error(e)) / b_n
    c_err = sum(1 for e in current if _ev_is_error(e)) / c_n

    b_tin = sum(float(e.get("tokens_in") or 0) for e in baseline) / b_n
    c_tin = sum(float(e.get("tokens_in") or 0) for e in current) / c_n
    b_tout = sum(float(e.get("tokens_out") or 0) for e in baseline) / b_n
    c_tout = sum(float(e.get("tokens_out") or 0) for e in current) / c_n

    b_cost = _cost_per_call(model, b_tin, b_tout)
    c_cost = _cost_per_call(model, c_tin, c_tout)

    deltas: list[MetricDelta] = []
    reasons: list[str] = []
    risk = 0

    # Latency p95
    if b_p95 is not None and c_p95 is not None and b_p95 > 0:
        ratio = (c_p95 - b_p95) / b_p95
        sev = 0
        if ratio >= opts.latency_p95_major_pct:
            sev = 45
            reasons.append("LATENCY_P95_MAJOR")
        elif ratio >= opts.latency_p95_regression_pct:
            sev = 25
            reasons.append("LATENCY_P95_REGRESSED")
        elif ratio <= -opts.improvement_pct:
            sev = -15
            reasons.append("LATENCY_P95_IMPROVED")
        deltas.append(MetricDelta("latency_p95_ms", b_p95, c_p95, c_p95 - b_p95, ratio, sev))
        risk += max(0, sev)

    # Latency p50 - small contribution.
    if b_p50 is not None and c_p50 is not None and b_p50 > 0:
        ratio = (c_p50 - b_p50) / b_p50
        sev = 0
        if ratio >= opts.latency_p95_regression_pct:
            sev = 10
            reasons.append("LATENCY_P50_REGRESSED")
        deltas.append(MetricDelta("latency_p50_ms", b_p50, c_p50, c_p50 - b_p50, ratio, sev))
        risk += max(0, sev)

    # Error rate (percentage points).
    err_delta = c_err - b_err
    sev_e = 0
    if err_delta >= opts.error_rate_major_pts:
        sev_e = 50
        reasons.append("ERROR_RATE_SPIKE")
    elif err_delta >= opts.error_rate_regression_pts:
        sev_e = 30
        reasons.append("ERROR_RATE_REGRESSED")
    elif err_delta <= -opts.improvement_pct:
        sev_e = -10
        reasons.append("ERROR_RATE_IMPROVED")
    deltas.append(MetricDelta("error_rate", b_err, c_err, err_delta, None if b_err == 0 else err_delta / max(b_err, 1e-9), sev_e))
    risk += max(0, sev_e)

    # Token-in inflation.
    if b_tin > 0:
        ratio = (c_tin - b_tin) / b_tin
        sev = 0
        if ratio >= opts.token_in_regression_pct:
            sev = 15
            reasons.append("TOKENS_IN_INFLATED")
        elif ratio <= -opts.improvement_pct:
            sev = -5
            reasons.append("TOKENS_IN_REDUCED")
        deltas.append(MetricDelta("avg_tokens_in", b_tin, c_tin, c_tin - b_tin, ratio, sev))
        risk += max(0, sev)

    # Cost per call.
    if b_cost > 0:
        ratio = (c_cost - b_cost) / b_cost
        sev = 0
        if ratio >= opts.cost_regression_pct:
            sev = 15
            reasons.append("COST_REGRESSED")
        elif ratio <= -opts.improvement_pct:
            sev = -5
            reasons.append("COST_IMPROVED")
        deltas.append(MetricDelta("avg_cost_usd", b_cost, c_cost, c_cost - b_cost, ratio, sev))
        risk += max(0, sev)

    # Throughput / traffic drop.
    b_rpm = (b_n / baseline_window_s * 60) if baseline_window_s > 0 else 0
    c_rpm = (c_n / current_window_s * 60) if current_window_s > 0 else 0
    if b_rpm > 0:
        ratio = (c_rpm - b_rpm) / b_rpm
        sev = 0
        if ratio <= -opts.throughput_drop_pct:
            sev = 20
            reasons.append("TRAFFIC_DROPPED")
        deltas.append(MetricDelta("events_per_minute", b_rpm, c_rpm, c_rpm - b_rpm, ratio, sev))
        risk += max(0, sev)

    risk = max(0, min(100, risk))

    # Projected extra cost: per-call cost diff * current rate / day.
    proj_extra = 0.0
    if b_cost > 0 and c_cost > b_cost and current_window_s > 0:
        per_day = (c_n / current_window_s) * 86400.0
        proj_extra = max(0.0, (c_cost - b_cost) * per_day)

    # Verdict + priority.
    has_major = any(r in {"LATENCY_P95_MAJOR", "ERROR_RATE_SPIKE"} for r in reasons)
    has_regression = any(
        r in {"LATENCY_P95_REGRESSED", "ERROR_RATE_REGRESSED", "TOKENS_IN_INFLATED",
              "COST_REGRESSED", "TRAFFIC_DROPPED", "LATENCY_P50_REGRESSED"}
        for r in reasons
    )
    has_improvement = any(r.endswith("_IMPROVED") or r == "TOKENS_IN_REDUCED" for r in reasons)

    if has_major:
        verdict = RegressionVerdict.MAJOR_REGRESSION
        priority = ActionPriority.P0
    elif has_regression:
        verdict = RegressionVerdict.REGRESSION
        priority = ActionPriority.P1
    elif has_improvement and risk == 0:
        verdict = RegressionVerdict.IMPROVEMENT
        priority = ActionPriority.P3
    else:
        verdict = RegressionVerdict.STABLE
        priority = ActionPriority.P3

    return CallSiteRegression(
        key=key,
        model=model,
        tool=tool,
        baseline_events=b_n,
        current_events=c_n,
        baseline_window_seconds=baseline_window_s,
        current_window_seconds=current_window_s,
        verdict=verdict,
        priority=priority,
        risk_score=risk,
        deltas=tuple(deltas),
        reason_codes=tuple(reasons),
        baseline_error_rate=b_err,
        current_error_rate=c_err,
        baseline_p95_ms=b_p95,
        current_p95_ms=c_p95,
        baseline_avg_tokens_in=b_tin,
        current_avg_tokens_in=c_tin,
        baseline_avg_cost_usd=b_cost,
        current_avg_cost_usd=c_cost,
        projected_extra_cost_per_day_usd=proj_extra,
    )


def _make_site(
    key: str,
    model: str,
    tool: Optional[str],
    b_n: int,
    c_n: int,
    bw: float,
    cw: float,
    verdict: RegressionVerdict,
    priority: ActionPriority,
    risk_score: int,
    deltas: tuple[MetricDelta, ...],
    *,
    baseline_events: Sequence[Mapping[str, Any]] = (),
    current_events: Sequence[Mapping[str, Any]] = (),
    baseline_window_seconds: float = 0.0,
    current_window_seconds: float = 0.0,
    current_n: Optional[int] = None,
) -> CallSiteRegression:
    b_err = (sum(1 for e in baseline_events if _ev_is_error(e)) / b_n) if b_n else 0.0
    c_err = (sum(1 for e in current_events if _ev_is_error(e)) / c_n) if c_n else 0.0
    b_tin = (sum(float(e.get("tokens_in") or 0) for e in baseline_events) / b_n) if b_n else 0.0
    c_tin = (sum(float(e.get("tokens_in") or 0) for e in current_events) / c_n) if c_n else 0.0
    b_tout = (sum(float(e.get("tokens_out") or 0) for e in baseline_events) / b_n) if b_n else 0.0
    c_tout = (sum(float(e.get("tokens_out") or 0) for e in current_events) / c_n) if c_n else 0.0
    return CallSiteRegression(
        key=key,
        model=model,
        tool=tool,
        baseline_events=b_n,
        current_events=c_n,
        baseline_window_seconds=baseline_window_seconds or bw,
        current_window_seconds=current_window_seconds or cw,
        verdict=verdict,
        priority=priority,
        risk_score=risk_score,
        deltas=deltas,
        reason_codes=(),
        baseline_error_rate=b_err,
        current_error_rate=c_err,
        baseline_p95_ms=None,
        current_p95_ms=None,
        baseline_avg_tokens_in=b_tin,
        current_avg_tokens_in=c_tin,
        baseline_avg_cost_usd=_cost_per_call(model, b_tin, b_tout),
        current_avg_cost_usd=_cost_per_call(model, c_tin, c_tout),
        projected_extra_cost_per_day_usd=0.0,
    )


# --------------------------------------------------------------------------- #
# Playbook
# --------------------------------------------------------------------------- #


def _playbook(
    slices: Sequence[CallSiteRegression],
    appetite: RiskAppetite,
    grade: RegressionGrade,
) -> tuple[PlaybookAction, ...]:
    actions: list[PlaybookAction] = []
    by_reason: dict[str, list[str]] = defaultdict(list)
    for s in slices:
        for r in s.reason_codes:
            by_reason[r].append(s.key)
        if s.verdict in (RegressionVerdict.NEW_FAILURE_MODE,):
            by_reason["NEW_FAILURE"].append(s.key)
        if s.verdict is RegressionVerdict.DISAPPEARED:
            by_reason["DISAPPEARED"].append(s.key)

    p0_sites = [s.key for s in slices if s.priority is ActionPriority.P0]
    p1_sites = [s.key for s in slices if s.priority is ActionPriority.P1]

    if p0_sites:
        actions.append(PlaybookAction(
            id="rollback_or_freeze_deploys",
            priority=ActionPriority.P0,
            label="Freeze deploys and consider rollback",
            reason="Major regression detected on one or more hot call-sites.",
            owner="release_mgr",
            blast_radius=4,
            reversibility="medium",
            related_slice_keys=tuple(sorted(p0_sites)),
        ))
        actions.append(PlaybookAction(
            id="page_oncall_for_regression",
            priority=ActionPriority.P0,
            label="Page on-call for regression triage",
            reason="P0 regression(s) need eyes-on investigation now.",
            owner="oncall",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(sorted(p0_sites)),
        ))
    if by_reason.get("ERROR_RATE_SPIKE"):
        actions.append(PlaybookAction(
            id="triage_error_spike",
            priority=ActionPriority.P0,
            label="Triage error-rate spike",
            reason="Error rate jumped above the major threshold for these call-sites.",
            owner="service_owner",
            blast_radius=3,
            reversibility="medium",
            related_slice_keys=tuple(sorted(by_reason["ERROR_RATE_SPIKE"])),
        ))
    if by_reason.get("NEW_FAILURE"):
        actions.append(PlaybookAction(
            id="investigate_new_failure_mode",
            priority=ActionPriority.P0 if any(
                s.priority is ActionPriority.P0
                for s in slices
                if s.verdict is RegressionVerdict.NEW_FAILURE_MODE
            ) else ActionPriority.P1,
            label="Investigate new failure mode",
            reason="Call-site started erroring in the current window after being absent or clean in baseline.",
            owner="service_owner",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(sorted(by_reason["NEW_FAILURE"])),
        ))
    if by_reason.get("LATENCY_P95_MAJOR"):
        actions.append(PlaybookAction(
            id="profile_hot_latency_path",
            priority=ActionPriority.P1,
            label="Profile hot latency path",
            reason="p95 latency at least doubled vs baseline.",
            owner="service_owner",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(sorted(by_reason["LATENCY_P95_MAJOR"])),
        ))
    if by_reason.get("LATENCY_P95_REGRESSED") or by_reason.get("LATENCY_P50_REGRESSED"):
        keys = sorted(set(by_reason.get("LATENCY_P95_REGRESSED", []) + by_reason.get("LATENCY_P50_REGRESSED", [])))
        actions.append(PlaybookAction(
            id="raise_timeouts_or_optimize",
            priority=ActionPriority.P1,
            label="Raise timeouts or optimize prompt size",
            reason="Latency drifted above the regression threshold.",
            owner="agent_dev",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(keys),
        ))
    if by_reason.get("TOKENS_IN_INFLATED") or by_reason.get("COST_REGRESSED"):
        keys = sorted(set(by_reason.get("TOKENS_IN_INFLATED", []) + by_reason.get("COST_REGRESSED", [])))
        actions.append(PlaybookAction(
            id="audit_prompt_growth",
            priority=ActionPriority.P1,
            label="Audit prompt growth and cost regression",
            reason="Average tokens-in or $/call increased materially.",
            owner="agent_dev",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(keys),
        ))
    if by_reason.get("TRAFFIC_DROPPED"):
        actions.append(PlaybookAction(
            id="investigate_traffic_drop",
            priority=ActionPriority.P2,
            label="Investigate traffic drop",
            reason="Throughput dropped well below the baseline window.",
            owner="product",
            blast_radius=2,
            reversibility="high",
            related_slice_keys=tuple(sorted(by_reason["TRAFFIC_DROPPED"])),
        ))
    if by_reason.get("DISAPPEARED"):
        actions.append(PlaybookAction(
            id="confirm_intentional_retirement",
            priority=ActionPriority.P2,
            label="Confirm call-site retirement",
            reason="Call-site present in baseline but absent in current window.",
            owner="product",
            blast_radius=1,
            reversibility="high",
            related_slice_keys=tuple(sorted(by_reason["DISAPPEARED"])),
        ))

    has_p01 = any(a.priority in (ActionPriority.P0, ActionPriority.P1) for a in actions)

    if appetite is RiskAppetite.CAUTIOUS and grade in (
        RegressionGrade.C, RegressionGrade.D, RegressionGrade.F
    ):
        actions.append(PlaybookAction(
            id="schedule_regression_review",
            priority=ActionPriority.P2,
            label="Schedule cross-team regression review",
            reason="Cautious appetite + degraded grade: lock in a follow-up.",
            owner="platform",
            blast_radius=1,
            reversibility="high",
        ))

    if not actions:
        actions.append(PlaybookAction(
            id="no_regression_action_needed",
            priority=ActionPriority.P3,
            label="No regression action needed",
            reason="All slices are stable or improving.",
            owner="platform",
            blast_radius=1,
            reversibility="high",
        ))
    elif appetite is RiskAppetite.AGGRESSIVE and has_p01:
        # Trim lone P2 fallback actions when there are real issues.
        actions = [a for a in actions if a.priority != ActionPriority.P2 or a.id == "schedule_regression_review"]
        if not any(a.priority in (ActionPriority.P0, ActionPriority.P1, ActionPriority.P2) for a in actions):
            pass

    # Dedupe by id (keep first), then P0-first / id asc.
    seen: set[str] = set()
    deduped: list[PlaybookAction] = []
    for a in actions:
        if a.id in seen:
            continue
        seen.add(a.id)
        deduped.append(a)
    deduped.sort(key=lambda a: (int(a.priority.value[1]), a.id))
    return tuple(deduped)


def _insights(slices: Sequence[CallSiteRegression], portfolio: RegressionPortfolio) -> tuple[str, ...]:
    out: list[str] = []
    if portfolio.total_call_sites == 0:
        out.append("NO_TRAFFIC")
        return tuple(out)
    if portfolio.p0_count >= 2:
        out.append("MULTIPLE_P0_REGRESSIONS")
    if portfolio.projected_extra_cost_per_day_usd >= 10:
        out.append(f"PROJECTED_EXTRA_COST_USD_PER_DAY:{portfolio.projected_extra_cost_per_day_usd:.2f}")
    new_failures = sum(1 for s in slices if s.verdict is RegressionVerdict.NEW_FAILURE_MODE)
    if new_failures:
        out.append(f"NEW_FAILURE_MODES:{new_failures}")
    disappeared = sum(1 for s in slices if s.verdict is RegressionVerdict.DISAPPEARED)
    if disappeared >= 2:
        out.append(f"CALL_SITES_DISAPPEARED:{disappeared}")
    improvements = sum(1 for s in slices if s.verdict is RegressionVerdict.IMPROVEMENT)
    if improvements >= 2:
        out.append(f"IMPROVEMENTS_DETECTED:{improvements}")
    if portfolio.p0_count == 0 and portfolio.p1_count == 0:
        out.append("NO_REGRESSIONS")
    if not out:
        out.append("MIXED_SIGNALS")
    return tuple(out)


def _grade(slices: Sequence[CallSiteRegression], portfolio_risk: int) -> tuple[RegressionGrade, str]:
    p0 = sum(1 for s in slices if s.priority is ActionPriority.P0)
    p1 = sum(1 for s in slices if s.priority is ActionPriority.P1)
    if p0 >= 2 or portfolio_risk >= 70:
        return RegressionGrade.F, "CRITICAL"
    if p0 >= 1 or portfolio_risk >= 50:
        return RegressionGrade.D, "DEGRADED"
    if p1 >= 2 or portfolio_risk >= 30:
        return RegressionGrade.C, "WATCH"
    if p1 >= 1 or portfolio_risk >= 15:
        return RegressionGrade.B, "WATCH"
    return RegressionGrade.A, "HEALTHY"


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, (EvalRegressionReport, RegressionPortfolio, CallSiteRegression,
                         MetricDelta, PlaybookAction)):
        return _to_jsonable(_dataclass_dict(obj))
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _dataclass_dict(obj: Any) -> dict:
    out = {}
    for f in obj.__dataclass_fields__:  # type: ignore[attr-defined]
        out[f] = getattr(obj, f)
    return out


def _render_text(report: EvalRegressionReport) -> str:
    p = report.portfolio
    lines = [report.headline, ""]
    lines.append(
        f"Baseline: {p.baseline_events} events / Current: {p.current_events} events"
        f" / call-sites: {p.total_call_sites} / grade {p.grade.value} ({p.band})"
    )
    if report.slices:
        lines.append("")
        lines.append("Top regressions:")
        for s in sorted(report.slices, key=lambda x: (int(x.priority.value[1]), -x.risk_score, x.key))[:10]:
            tag = s.verdict.value
            err = f"err {s.baseline_error_rate*100:.1f}->{s.current_error_rate*100:.1f}%"
            p95 = (
                f" p95 {s.baseline_p95_ms:.0f}->{s.current_p95_ms:.0f}ms"
                if s.baseline_p95_ms and s.current_p95_ms else ""
            )
            lines.append(
                f"  [{s.priority.value}] {s.key:40s} risk={s.risk_score:3d} {tag:18s} {err}{p95}"
            )
    if report.playbook:
        lines.append("")
        lines.append("Playbook:")
        for a in report.playbook:
            lines.append(f"  [{a.priority.value}] {a.label} -- {a.reason}")
    if report.insights:
        lines.append("")
        lines.append("Insights: " + ", ".join(report.insights))
    return "\n".join(lines)


def _render_markdown(report: EvalRegressionReport) -> str:
    p = report.portfolio
    lines = [f"# Eval Regression Report", ""]
    lines.append(f"_{report.headline}_")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Baseline events | {p.baseline_events} |")
    lines.append(f"| Current events | {p.current_events} |")
    lines.append(f"| Call-sites compared | {p.total_call_sites} |")
    lines.append(f"| Overall risk score | {p.overall_risk_score} |")
    lines.append(f"| Grade | {p.grade.value} ({p.band}) |")
    lines.append(f"| P0 / P1 / P2 / P3 | {p.p0_count} / {p.p1_count} / {p.p2_count} / {p.p3_count} |")
    lines.append(f"| Projected extra cost / day (USD) | {p.projected_extra_cost_per_day_usd:.2f} |")
    lines.append("")
    lines.append("## Call-sites")
    lines.append("")
    if report.slices:
        lines.append("| Pri | Key | Verdict | Risk | Err b->c | p95 b->c (ms) | Tokens-in b->c | Reasons |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for s in sorted(report.slices, key=lambda x: (int(x.priority.value[1]), -x.risk_score, x.key)):
            p95 = (
                f"{s.baseline_p95_ms:.0f}->{s.current_p95_ms:.0f}"
                if s.baseline_p95_ms and s.current_p95_ms else "n/a"
            )
            err = f"{s.baseline_error_rate*100:.1f}%->{s.current_error_rate*100:.1f}%"
            tin = f"{s.baseline_avg_tokens_in:.0f}->{s.current_avg_tokens_in:.0f}"
            reasons = ",".join(s.reason_codes) or "-"
            lines.append(
                f"| {s.priority.value} | {s.key} | {s.verdict.value} | {s.risk_score} | "
                f"{err} | {p95} | {tin} | {reasons} |"
            )
    else:
        lines.append("_No call-sites to compare._")
    lines.append("")
    lines.append("## Playbook")
    lines.append("")
    lines.append("| Pri | Action | Owner | Why |")
    lines.append("| --- | --- | --- | --- |")
    for a in report.playbook:
        lines.append(f"| {a.priority.value} | {a.label} | {a.owner} | {a.reason} |")
    lines.append("")
    lines.append("## Insights")
    lines.append("")
    for ins in report.insights:
        lines.append(f"- {ins}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #


class EvalRegressionAdvisor:
    """Baseline-vs-current eval/regression advisor."""

    def __init__(
        self,
        options: Optional[EvalRegressionOptions] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.options = options or EvalRegressionOptions()
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def analyze(
        self,
        baseline: Iterable[Any],
        current: Iterable[Any],
        *,
        key_fn: Optional[Callable[[Mapping[str, Any]], str]] = None,
        risk_appetite: "str | RiskAppetite" = "balanced",
    ) -> EvalRegressionReport:
        appetite = RiskAppetite.parse(risk_appetite)
        opts = _appetite_thresholds(self.options, appetite)
        keyer = key_fn or _ev_key

        b_buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        c_buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        baseline_total = 0
        current_total = 0
        for raw in baseline:
            ev = _coerce_event(raw)
            baseline_total += 1
            b_buckets[keyer(ev)].append(ev)
        for raw in current:
            ev = _coerce_event(raw)
            current_total += 1
            c_buckets[keyer(ev)].append(ev)

        baseline_window_s = _window_seconds([e for evs in b_buckets.values() for e in evs])
        current_window_s = _window_seconds([e for evs in c_buckets.values() for e in evs])
        # Fallbacks if windows have only 1 ts (avoid division by zero downstream).
        if baseline_window_s <= 0:
            baseline_window_s = float(60 * max(1, baseline_total))
        if current_window_s <= 0:
            current_window_s = float(60 * max(1, current_total))

        keys = sorted(set(b_buckets) | set(c_buckets))
        slices = tuple(
            _score_slice(
                k,
                b_buckets.get(k, []),
                c_buckets.get(k, []),
                _window_seconds(b_buckets.get(k, [])) or baseline_window_s,
                _window_seconds(c_buckets.get(k, [])) or current_window_s,
                opts,
            )
            for k in keys
        )

        p0 = sum(1 for s in slices if s.priority is ActionPriority.P0)
        p1 = sum(1 for s in slices if s.priority is ActionPriority.P1)
        p2 = sum(1 for s in slices if s.priority is ActionPriority.P2)
        p3 = sum(1 for s in slices if s.priority is ActionPriority.P3)

        if slices:
            top = sorted(slices, key=lambda s: -s.risk_score)[: max(1, len(slices) // 3 or 1)]
            overall = int(round(sum(s.risk_score for s in top) / max(1, len(top))))
        else:
            overall = 0
        if appetite is RiskAppetite.CAUTIOUS:
            overall = min(100, overall + 5)
        elif appetite is RiskAppetite.AGGRESSIVE:
            overall = max(0, overall - 5)

        grade, band = _grade(slices, overall)
        projected_extra = sum(s.projected_extra_cost_per_day_usd for s in slices)

        portfolio = RegressionPortfolio(
            baseline_events=baseline_total,
            current_events=current_total,
            baseline_window_seconds=baseline_window_s,
            current_window_seconds=current_window_s,
            total_call_sites=len(slices),
            p0_count=p0,
            p1_count=p1,
            p2_count=p2,
            p3_count=p3,
            overall_risk_score=overall,
            grade=grade,
            band=band,
            projected_extra_cost_per_day_usd=projected_extra,
        )
        playbook = _playbook(slices, appetite, grade)
        insights = _insights(slices, portfolio)
        headline = (
            f"VERDICT: grade {grade.value} ({band}) -- "
            f"P0={p0} P1={p1} over {portfolio.total_call_sites} call-sites; "
            f"projected extra ${projected_extra:.2f}/day"
        )
        return EvalRegressionReport(
            portfolio=portfolio,
            slices=slices,
            playbook=playbook,
            insights=insights,
            headline=headline,
            generated_at=self._now_fn(),
        )


__all__ = [
    "EvalRegressionAdvisor",
    "EvalRegressionReport",
    "EvalRegressionOptions",
    "CallSiteRegression",
    "MetricDelta",
    "PlaybookAction",
    "RegressionPortfolio",
    "RegressionVerdict",
    "RegressionGrade",
    "ActionPriority",
    "RiskAppetite",
]
