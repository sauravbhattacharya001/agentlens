"""Usage Quota Manager — organizational quota management for AI agents.

Manages token and cost quotas at multiple levels (agent, model, team,
organization) with rolling time windows, burst allowances, quota sharing
between agents, and automatic enforcement.

Different from budget.py (per-session budgets): quotas are ongoing
organizational limits that persist across sessions.

Usage::

    from agentlens.quota import QuotaManager, QuotaPolicy

    qm = QuotaManager()

    # Set a daily quota for an agent
    qm.create_quota("agent-alpha",
        scope="agent",
        max_tokens=100_000,
        window="daily",
        burst_multiplier=1.5)

    # Set a monthly team quota
    qm.create_quota("ml-team",
        scope="team",
        max_tokens=5_000_000,
        max_cost_usd=500.0,
        window="monthly")

    # Record usage (returns QuotaCheck with allow/deny + reason)
    check = qm.record_usage("agent-alpha", tokens=1500, cost_usd=0.12)
    if not check.allowed:
        print(check.reason)  # "Daily token quota exceeded (100000/100000)"

    # Share unused quota between agents
    qm.create_pool("shared-pool", members=["agent-alpha", "agent-beta"],
        pool_tokens=50_000, window="daily")

    # Reports
    report = qm.report("agent-alpha")
    fleet = qm.fleet_report()
    print(fleet.render())
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

from agentlens._utils import new_id as _new_id


class QuotaScope(Enum):
    """Scope at which a quota policy is enforced.

    Determines what the ``entity_id`` on a :class:`QuotaPolicy` refers to:
    a single agent, a model name, a team, or the whole organization.
    """

    AGENT = "agent"
    MODEL = "model"
    TEAM = "team"
    ORGANIZATION = "organization"


class QuotaWindow(Enum):
    """Time window used to aggregate usage when evaluating a quota.

    Fixed windows (``HOURLY``/``DAILY``/``WEEKLY``/``MONTHLY``) align to
    calendar boundaries (top of the hour, midnight, Monday, first of the
    month).  Rolling windows (``ROLLING_*``) are anchored to *now* and
    slide continuously.
    """

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ROLLING_1H = "rolling_1h"
    ROLLING_24H = "rolling_24h"
    ROLLING_7D = "rolling_7d"


WINDOW_DURATIONS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
    "rolling_1h": timedelta(hours=1),
    "rolling_24h": timedelta(hours=24),
    "rolling_7d": timedelta(days=7),
}


class QuotaAction(Enum):
    """Action taken when a quota is exceeded.

    ``WARN`` allows the call but emits a warning, ``THROTTLE`` allows it
    while marking the check as throttled (callers may slow the agent
    down), and ``DENY`` rejects the call outright.
    """

    WARN = "warn"
    THROTTLE = "throttle"
    DENY = "deny"


@dataclass
class UsageRecord:
    """A single recorded usage event (tokens + cost) for an entity.

    Created internally by :meth:`QuotaManager.record_usage` and stored on
    both the global record list and per-entity / per-pool indexes so
    window aggregations stay O(log n) per lookup.
    """

    record_id: str = field(default_factory=_new_id)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entity_id: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    session_id: str = ""


@dataclass
class QuotaPolicy:
    """Declarative quota for a single entity (agent / model / team / org).

    A policy bundles together the limits (``max_tokens``, ``max_cost_usd``,
    ``max_requests``), the :class:`QuotaWindow` they apply to, a
    ``burst_multiplier`` allowance, a ``warn_at`` threshold, and the
    :class:`QuotaAction` to take on breach.  Stored on
    :class:`QuotaManager` and looked up by ``entity_id``.
    """

    quota_id: str = field(default_factory=_new_id)
    entity_id: str = ""
    scope: QuotaScope = QuotaScope.AGENT
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_requests: Optional[int] = None
    window: QuotaWindow = QuotaWindow.DAILY
    burst_multiplier: float = 1.0
    warn_at: float = 0.8
    action_on_exceed: QuotaAction = QuotaAction.DENY
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuotaCheck:
    """Result of a quota evaluation for one call.

    Carries the allow/deny decision, a human-readable ``reason``, current
    utilisation ratios, remaining headroom on each axis, and flags for
    burst-mode or shared-pool borrowing.  Returned by
    :meth:`QuotaManager.record_usage` and :meth:`QuotaManager.check_usage`.
    """

    allowed: bool = True
    reason: str = ""
    utilization_tokens: float = 0.0
    utilization_cost: float = 0.0
    utilization_requests: float = 0.0
    remaining_tokens: Optional[int] = None
    remaining_cost: Optional[float] = None
    remaining_requests: Optional[int] = None
    warnings: list[str] = field(default_factory=list)
    burst_active: bool = False
    pool_contribution: int = 0


@dataclass
class QuotaReport:
    """Snapshot report for one entity's quota in the current window.

    Aggregates tokens / cost / requests used vs. configured limits, the
    resulting status (``ok`` / ``warning`` / ``exceeded`` / ``disabled``),
    burst usage, and the top-N models and sessions contributing to the
    spend.  Returned by :meth:`QuotaManager.report`.
    """

    entity_id: str = ""
    scope: str = ""
    window: str = ""
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    tokens_used: int = 0
    tokens_limit: Optional[int] = None
    cost_used: float = 0.0
    cost_limit: Optional[float] = None
    requests_used: int = 0
    requests_limit: Optional[int] = None
    utilization_tokens: float = 0.0
    utilization_cost: float = 0.0
    utilization_requests: float = 0.0
    burst_active: bool = False
    burst_tokens_remaining: int = 0
    warnings: list[str] = field(default_factory=list)
    status: str = "ok"
    top_models: list[dict[str, Any]] = field(default_factory=list)
    top_sessions: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        """Render a human-readable, terminal-friendly report.

        Returns a multi-line string with utilisation bars for each
        configured limit, burst/warning indicators, and the top models
        list (suitable for printing in CLI output).
        """
        lines = [
            f"═══ Quota Report: {self.entity_id} ({self.scope}) ═══",
            f"Window: {self.window}  |  Status: {self.status.upper()}",
        ]
        if self.period_start and self.period_end:
            lines.append(f"Period: {self.period_start:%Y-%m-%d %H:%M} → {self.period_end:%Y-%m-%d %H:%M}")
        lines.append("")
        if self.tokens_limit is not None:
            bar = _bar(self.utilization_tokens)
            lines.append(f"Tokens:   {self.tokens_used:>10,} / {self.tokens_limit:>10,}  [{bar}] {self.utilization_tokens:.0%}")
        if self.cost_limit is not None:
            bar = _bar(self.utilization_cost)
            lines.append(f"Cost:     ${self.cost_used:>9.2f} / ${self.cost_limit:>9.2f}  [{bar}] {self.utilization_cost:.0%}")
        if self.requests_limit is not None:
            bar = _bar(self.utilization_requests)
            lines.append(f"Requests: {self.requests_used:>10,} / {self.requests_limit:>10,}  [{bar}] {self.utilization_requests:.0%}")
        if self.burst_active:
            lines.append(f"⚡ Burst active — {self.burst_tokens_remaining:,} burst tokens remaining")
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        if self.top_models:
            lines.append("")
            lines.append("Top models:")
            for m in self.top_models[:5]:
                lines.append(f"  {m['model']:>20s}: {m['tokens']:>8,} tokens  ${m['cost']:.2f}")
        return "\n".join(lines)


@dataclass
class SharedPool:
    """A pool of borrowable tokens shared between multiple entities.

    When a member entity exceeds its individual :class:`QuotaPolicy`,
    :meth:`QuotaManager.record_usage` may transparently borrow from the
    pool (up to ``pool_tokens`` per ``window``) instead of denying the
    call outright.  Useful for absorbing transient bursts across a team.
    """

    pool_id: str = field(default_factory=_new_id)
    name: str = ""
    members: list[str] = field(default_factory=list)
    pool_tokens: int = 0
    pool_cost_usd: float = 0.0
    window: QuotaWindow = QuotaWindow.DAILY
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    enabled: bool = True


@dataclass
class FleetReport:
    """Aggregate view of every quota-managed entity in the fleet.

    Combines per-entity :class:`QuotaReport` summaries with shared-pool
    utilisation so operators can spot fleet-wide pressure at a glance.
    Returned by :meth:`QuotaManager.fleet_report`.
    """

    total_entities: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    entities_warning: int = 0
    entities_exceeded: int = 0
    entities_ok: int = 0
    entity_reports: list[QuotaReport] = field(default_factory=list)
    pool_reports: list[dict[str, Any]] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render(self) -> str:
        """Render the fleet report as a human-readable terminal string.

        Sorts entities by token utilisation (highest first) so noisy
        neighbours and exceeded quotas surface at the top.
        """
        lines = [
            "╔══════════════════════════════════════════╗",
            "║        Fleet Quota Report                ║",
            "╚══════════════════════════════════════════╝",
            f"Entities: {self.total_entities}  |  OK: {self.entities_ok}  |  Warning: {self.entities_warning}  |  Exceeded: {self.entities_exceeded}",
            f"Total tokens: {self.total_tokens:,}  |  Total cost: ${self.total_cost:.2f}  |  Total requests: {self.total_requests:,}",
            "",
        ]
        for er in sorted(self.entity_reports, key=lambda r: r.utilization_tokens, reverse=True):
            status_icon = {"ok": "✅", "warning": "⚠️", "exceeded": "🚫", "disabled": "⏸️"}.get(er.status, "?")
            token_str = f"{er.tokens_used:,}" + (f"/{er.tokens_limit:,}" if er.tokens_limit else "")
            lines.append(f"  {status_icon} {er.entity_id:>20s}  tokens={token_str}  ${er.cost_used:.2f}  {er.utilization_tokens:.0%}")
        if self.pool_reports:
            lines.append("")
            lines.append("Shared Pools:")
            for p in self.pool_reports:
                lines.append(f"  🏊 {p['name']}: {p['used']:,}/{p['total']:,} tokens  ({len(p['members'])} members)")
        return "\n".join(lines)


def _bar(ratio: float, width: int = 20) -> str:
    """Render a unicode progress bar for *ratio* (0.0..1.0+).

    Values above ``1.0`` clamp to a fully-filled bar.  Used by the report
    renderers above for terminal-friendly utilisation indicators.
    """
    filled = int(min(ratio, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


class QuotaManager:
    """Manages usage quotas across agents, models, and teams."""

    def __init__(self, *, now_fn=None):
        """Build an empty quota manager.

        Args:
            now_fn: Optional callable returning the current timezone-aware
                ``datetime``.  Defaults to ``datetime.now(timezone.utc)``;
                tests inject a deterministic clock here.
        """
        self._policies: dict[str, QuotaPolicy] = {}
        self._records: list[UsageRecord] = []
        # Per-entity index for O(entity_records) lookups instead of
        # O(all_records) linear scans in record_usage/check_usage/report.
        self._entity_records: dict[str, list[UsageRecord]] = defaultdict(list)
        # Per-entity timestamp index (parallel to _entity_records) for
        # O(log n) window-start lookups via bisect instead of O(n) linear scan.
        self._entity_timestamps: dict[str, list[datetime]] = defaultdict(list)
        self._pools: dict[str, SharedPool] = {}
        self._pool_usage: dict[str, list[UsageRecord]] = defaultdict(list)
        # Per-pool timestamp index for O(log n) window filtering.
        self._pool_timestamps: dict[str, list[datetime]] = defaultdict(list)
        self._callbacks: list[Any] = []
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def create_quota(self, entity_id: str, *, scope: str = "agent",
                     max_tokens: Optional[int] = None, max_cost_usd: Optional[float] = None,
                     max_requests: Optional[int] = None, window: str = "daily",
                     burst_multiplier: float = 1.0, warn_at: float = 0.8,
                     action_on_exceed: str = "deny", metadata: Optional[dict] = None,
                     **kwargs) -> QuotaPolicy:
        """Register a new :class:`QuotaPolicy` for *entity_id*.

        Any combination of ``max_tokens`` / ``max_cost_usd`` /
        ``max_requests`` may be set; ``None`` means no limit on that axis.
        Overwrites any existing policy with the same ``entity_id``.
        Extra ``**kwargs`` are ignored so callers can forward dicts from
        :meth:`import_state` without filtering.
        """
        policy = QuotaPolicy(
            entity_id=entity_id, scope=QuotaScope(scope),
            max_tokens=max_tokens, max_cost_usd=max_cost_usd,
            max_requests=max_requests, window=QuotaWindow(window),
            burst_multiplier=burst_multiplier, warn_at=warn_at,
            action_on_exceed=QuotaAction(action_on_exceed),
            metadata=metadata or {},
        )
        self._policies[entity_id] = policy
        return policy

    def get_quota(self, entity_id: str) -> Optional[QuotaPolicy]:
        """Return the :class:`QuotaPolicy` for *entity_id*, or ``None``."""
        return self._policies.get(entity_id)

    def update_quota(self, entity_id: str, **kwargs) -> QuotaPolicy:
        """Patch fields on an existing policy in place.

        Pass any subset of policy fields as keyword arguments; string
        values for ``scope`` / ``window`` / ``action_on_exceed`` are
        coerced to the matching enum.  Raises :class:`KeyError` when no
        policy exists for *entity_id*.
        """
        policy = self._policies.get(entity_id)
        if not policy:
            raise KeyError(f"No quota for '{entity_id}'")
        for k, v in kwargs.items():
            if k == "scope": v = QuotaScope(v)
            elif k == "window": v = QuotaWindow(v)
            elif k == "action_on_exceed": v = QuotaAction(v)
            if hasattr(policy, k):
                setattr(policy, k, v)
        return policy

    def delete_quota(self, entity_id: str) -> bool:
        """Remove the policy for *entity_id*.  Returns ``True`` if removed."""
        return self._policies.pop(entity_id, None) is not None

    def list_quotas(self, *, scope: Optional[str] = None) -> list[QuotaPolicy]:
        """Return all registered policies, optionally filtered by *scope*."""
        policies = list(self._policies.values())
        if scope:
            s = QuotaScope(scope)
            policies = [p for p in policies if p.scope == s]
        return policies

    def disable_quota(self, entity_id: str) -> None:
        """Mark *entity_id*'s policy as disabled (no enforcement, usage still tracked)."""
        if entity_id in self._policies:
            self._policies[entity_id].enabled = False

    def enable_quota(self, entity_id: str) -> None:
        """Re-enable a previously disabled policy for *entity_id*."""
        if entity_id in self._policies:
            self._policies[entity_id].enabled = True

    def create_pool(self, name: str, *, members: list[str], pool_tokens: int = 0,
                    pool_cost_usd: float = 0.0, window: str = "daily") -> SharedPool:
        """Create a :class:`SharedPool` of borrowable tokens.

        ``members`` is the list of ``entity_id`` values eligible to draw
        from this pool when their individual quota is exhausted.
        Overwrites any pool with the same ``name``.
        """
        pool = SharedPool(name=name, members=list(members), pool_tokens=pool_tokens,
                          pool_cost_usd=pool_cost_usd, window=QuotaWindow(window))
        self._pools[name] = pool
        return pool

    def get_pool(self, name: str) -> Optional[SharedPool]:
        """Return the shared pool named *name*, or ``None`` if absent."""
        return self._pools.get(name)

    def add_pool_member(self, pool_name: str, entity_id: str) -> None:
        """Add *entity_id* to the pool's member list (no-op if already a member)."""
        pool = self._pools.get(pool_name)
        if pool and entity_id not in pool.members:
            pool.members.append(entity_id)

    def remove_pool_member(self, pool_name: str, entity_id: str) -> None:
        """Remove *entity_id* from the pool's member list (no-op if absent)."""
        pool = self._pools.get(pool_name)
        if pool and entity_id in pool.members:
            pool.members.remove(entity_id)

    def delete_pool(self, name: str) -> bool:
        """Delete the pool named *name* (and its usage history).  Returns ``True`` if removed."""
        self._pool_usage.pop(name, None)
        return self._pools.pop(name, None) is not None

    def _window_totals(
        self, entity_id: str, window: QuotaWindow, now: datetime,
    ) -> tuple[int, float, int, list[UsageRecord]]:
        """Aggregate token/cost/request totals for *entity_id* in the
        current window.  Returns ``(tokens, cost, requests, records)``.

        Centralises the window-filter-and-sum logic that was previously
        duplicated across ``record_usage``, ``check_usage``, and
        ``report``.

        Uses bisect on the per-entity timestamp index to skip records
        before the window start in O(log n) instead of scanning all
        entity records linearly.  For entities with large histories
        (thousands of records) and narrow windows (hourly/rolling_1h),
        this eliminates the majority of iteration work.
        """
        start, end = self._window_bounds(window, now)
        entity_recs = self._entity_records.get(entity_id, [])
        if not entity_recs:
            return 0, 0.0, 0, []

        # O(log n) bisect to find the first record >= start
        timestamps = self._entity_timestamps.get(entity_id, [])
        lo = bisect.bisect_left(timestamps, start)

        tokens_used = 0
        cost_used = 0.0
        matched: list[UsageRecord] = []
        for i in range(lo, len(entity_recs)):
            r = entity_recs[i]
            if r.timestamp >= end:
                break
            tokens_used += r.tokens
            cost_used += r.cost_usd
            matched.append(r)
        return tokens_used, cost_used, len(matched), matched

    def record_usage(self, entity_id: str, *, tokens: int = 0, cost_usd: float = 0.0,
                     model: str = "", session_id: str = "") -> QuotaCheck:
        """Record a usage event for *entity_id* and evaluate quota state.

        Always appends the usage to history (even when no policy exists
        or the policy is disabled).  Returns a :class:`QuotaCheck`
        describing whether the call should be allowed, current
        utilisation on each axis, any warnings, and whether the entity
        is in burst mode or borrowed from a shared pool.
        """
        now = self._now_fn()
        record = UsageRecord(timestamp=now, entity_id=entity_id, tokens=tokens,
                             cost_usd=cost_usd, model=model, session_id=session_id)

        policy = self._policies.get(entity_id)
        if not policy or not policy.enabled:
            self._records.append(record)
            return QuotaCheck(allowed=True, reason="no active quota")

        current_tokens, current_cost, current_requests, _ = self._window_totals(
            entity_id, policy.window, now)

        new_tokens = current_tokens + tokens
        new_cost = current_cost + cost_usd
        new_requests = current_requests + 1

        burst_tokens = int(policy.max_tokens * policy.burst_multiplier) if policy.max_tokens else None
        burst_cost = policy.max_cost_usd * policy.burst_multiplier if policy.max_cost_usd else None

        check = QuotaCheck()
        warnings = []

        if policy.max_tokens is not None:
            check.utilization_tokens = new_tokens / policy.max_tokens if policy.max_tokens > 0 else 0
            check.remaining_tokens = max(0, policy.max_tokens - new_tokens)
            if new_tokens > policy.max_tokens:
                if burst_tokens and new_tokens <= burst_tokens:
                    check.burst_active = True
                    warnings.append(f"Burst mode: {new_tokens:,}/{burst_tokens:,} tokens (base limit: {policy.max_tokens:,})")
                else:
                    pool_tokens = self._try_pool_borrow(entity_id, tokens, now)
                    if pool_tokens > 0:
                        check.pool_contribution = pool_tokens
                        warnings.append(f"Borrowed {pool_tokens:,} tokens from shared pool")
                    else:
                        check.allowed = False
                        check.reason = f"{policy.window.value.capitalize()} token quota exceeded ({new_tokens:,}/{policy.max_tokens:,})"
            elif check.utilization_tokens >= policy.warn_at:
                warnings.append(f"Token usage at {check.utilization_tokens:.0%} of {policy.window.value} limit")

        if policy.max_cost_usd is not None:
            check.utilization_cost = new_cost / policy.max_cost_usd if policy.max_cost_usd > 0 else 0
            check.remaining_cost = max(0.0, policy.max_cost_usd - new_cost)
            if new_cost > policy.max_cost_usd:
                if burst_cost and new_cost <= burst_cost:
                    check.burst_active = True
                    warnings.append(f"Burst mode: ${new_cost:.2f}/${burst_cost:.2f} cost")
                else:
                    check.allowed = False
                    check.reason = check.reason or f"{policy.window.value.capitalize()} cost quota exceeded (${new_cost:.2f}/${policy.max_cost_usd:.2f})"
            elif check.utilization_cost >= policy.warn_at:
                warnings.append(f"Cost at {check.utilization_cost:.0%} of {policy.window.value} limit")

        if policy.max_requests is not None:
            check.utilization_requests = new_requests / policy.max_requests if policy.max_requests > 0 else 0
            check.remaining_requests = max(0, policy.max_requests - new_requests)
            if new_requests > policy.max_requests:
                check.allowed = False
                check.reason = check.reason or f"{policy.window.value.capitalize()} request quota exceeded ({new_requests:,}/{policy.max_requests:,})"
            elif check.utilization_requests >= policy.warn_at:
                warnings.append(f"Requests at {check.utilization_requests:.0%} of {policy.window.value} limit")

        check.warnings = warnings

        if not check.allowed and policy.action_on_exceed == QuotaAction.THROTTLE:
            check.allowed = True
            check.reason = "throttled: " + check.reason

        if not check.allowed and policy.action_on_exceed == QuotaAction.WARN:
            check.allowed = True
            check.warnings.append("QUOTA EXCEEDED (warn mode): " + check.reason)
            check.reason = ""

        self._records.append(record)
        self._entity_records[entity_id].append(record)
        self._entity_timestamps[entity_id].append(record.timestamp)

        for cb in self._callbacks:
            cb(entity_id, check)

        return check

    def check_usage(self, entity_id: str, *, tokens: int = 0, cost_usd: float = 0.0) -> QuotaCheck:
        """Dry-run check without recording."""
        policy = self._policies.get(entity_id)
        if not policy or not policy.enabled:
            return QuotaCheck(allowed=True, reason="no active quota")

        now = self._now_fn()
        base_tokens, base_cost, _, _ = self._window_totals(
            entity_id, policy.window, now)
        current_tokens = base_tokens + tokens
        current_cost = base_cost + cost_usd

        check = QuotaCheck()
        if policy.max_tokens is not None:
            check.utilization_tokens = current_tokens / policy.max_tokens if policy.max_tokens > 0 else 0
            check.remaining_tokens = max(0, policy.max_tokens - current_tokens)
            if current_tokens > policy.max_tokens:
                burst = int(policy.max_tokens * policy.burst_multiplier)
                if current_tokens > burst:
                    check.allowed = False
                    check.reason = f"Would exceed {policy.window.value} token quota ({current_tokens:,}/{policy.max_tokens:,})"
                else:
                    check.burst_active = True

        if policy.max_cost_usd is not None:
            check.utilization_cost = current_cost / policy.max_cost_usd if policy.max_cost_usd > 0 else 0
            check.remaining_cost = max(0.0, policy.max_cost_usd - current_cost)
            if current_cost > policy.max_cost_usd:
                burst = policy.max_cost_usd * policy.burst_multiplier
                if current_cost > burst:
                    check.allowed = False
                    check.reason = check.reason or f"Would exceed {policy.window.value} cost quota"

        return check

    def on_check(self, callback) -> None:
        """Register a callback invoked as ``callback(entity_id, QuotaCheck)``
        after every :meth:`record_usage`.

        Useful for emitting metrics, audit logs, or wiring up alerting.
        """
        self._callbacks.append(callback)

    def report(self, entity_id: str) -> QuotaReport:
        """Build a :class:`QuotaReport` for *entity_id* in its current window.

        Returns a sentinel report with ``status='no_quota'`` when no
        policy exists.  Otherwise aggregates window usage, computes
        utilisation / status, and includes the top contributing models
        and sessions.
        """
        policy = self._policies.get(entity_id)
        now = self._now_fn()
        if not policy:
            return QuotaReport(entity_id=entity_id, status="no_quota")

        start, end = self._window_bounds(policy.window, now)
        tokens_used, cost_used, requests_used, window_records = self._window_totals(
            entity_id, policy.window, now)

        util_t = tokens_used / policy.max_tokens if policy.max_tokens and policy.max_tokens > 0 else 0
        util_c = cost_used / policy.max_cost_usd if policy.max_cost_usd and policy.max_cost_usd > 0 else 0
        util_r = requests_used / policy.max_requests if policy.max_requests and policy.max_requests > 0 else 0

        max_util = max(util_t, util_c, util_r)
        if not policy.enabled:
            status = "disabled"
        elif max_util > 1.0:
            status = "exceeded"
        elif max_util >= policy.warn_at:
            status = "warning"
        else:
            status = "ok"

        burst_active = False
        burst_remaining = 0
        if policy.max_tokens and tokens_used > policy.max_tokens:
            burst_limit = int(policy.max_tokens * policy.burst_multiplier)
            if tokens_used <= burst_limit:
                burst_active = True
                burst_remaining = burst_limit - tokens_used

        model_stats: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "requests": 0})
        for r in window_records:
            m = r.model or "unknown"
            model_stats[m]["tokens"] += r.tokens
            model_stats[m]["cost"] += r.cost_usd
            model_stats[m]["requests"] += 1
        top_models = sorted([{"model": k, **v} for k, v in model_stats.items()],
                            key=lambda x: x["tokens"], reverse=True)[:5]

        session_stats: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
        for r in window_records:
            s = r.session_id or "unknown"
            session_stats[s]["tokens"] += r.tokens
            session_stats[s]["cost"] += r.cost_usd
        top_sessions = sorted([{"session": k, **v} for k, v in session_stats.items()],
                              key=lambda x: x["tokens"], reverse=True)[:5]

        warnings = []
        if status == "warning":
            if util_t >= policy.warn_at:
                warnings.append(f"Token usage at {util_t:.0%}")
            if util_c >= policy.warn_at:
                warnings.append(f"Cost at {util_c:.0%}")

        return QuotaReport(
            entity_id=entity_id, scope=policy.scope.value, window=policy.window.value,
            period_start=start, period_end=end,
            tokens_used=tokens_used, tokens_limit=policy.max_tokens,
            cost_used=cost_used, cost_limit=policy.max_cost_usd,
            requests_used=requests_used, requests_limit=policy.max_requests,
            utilization_tokens=util_t, utilization_cost=util_c, utilization_requests=util_r,
            burst_active=burst_active, burst_tokens_remaining=burst_remaining,
            warnings=warnings, status=status,
            top_models=top_models, top_sessions=top_sessions,
        )

    def fleet_report(self) -> FleetReport:
        """Build a :class:`FleetReport` covering every registered policy.

        Aggregates per-entity reports plus shared-pool utilisation in
        the current window.  O(n) in the number of policies + pools.
        """
        reports = [self.report(eid) for eid in self._policies]
        now = self._now_fn()
        pool_reports = []
        for pool in self._pools.values():
            used = self._pool_used_tokens(pool.name, pool.window, now)
            pool_reports.append({
                "name": pool.name, "members": pool.members,
                "total": pool.pool_tokens, "used": used,
                "remaining": max(0, pool.pool_tokens - used),
                "utilization": used / pool.pool_tokens if pool.pool_tokens > 0 else 0,
            })
        return FleetReport(
            total_entities=len(reports),
            total_tokens=sum(r.tokens_used for r in reports),
            total_cost=sum(r.cost_used for r in reports),
            total_requests=sum(r.requests_used for r in reports),
            entities_warning=sum(1 for r in reports if r.status == "warning"),
            entities_exceeded=sum(1 for r in reports if r.status == "exceeded"),
            entities_ok=sum(1 for r in reports if r.status == "ok"),
            entity_reports=reports, pool_reports=pool_reports, generated_at=now,
        )

    def reset_usage(self, entity_id: str) -> int:
        """Drop all stored usage for *entity_id*; returns the count removed.

        Leaves the policy itself intact — only the historical records
        and the per-entity indexes are wiped.
        """
        removed = self._entity_records.pop(entity_id, [])
        self._entity_timestamps.pop(entity_id, None)
        count = len(removed)
        if count:
            # Rebuild the flat list only when records were actually removed.
            removed_ids = {id(r) for r in removed}
            self._records = [r for r in self._records if id(r) not in removed_ids]
        return count

    def export_state(self) -> dict[str, Any]:
        """Serialise quotas and pools to a plain-dict snapshot.

        Records are *not* exported — only the configuration.  The
        ``records_count`` field is included for diagnostics.  Pair with
        :meth:`import_state` to rehydrate a fresh manager.
        """
        return {
            "quotas": {
                eid: {"scope": p.scope.value, "max_tokens": p.max_tokens,
                      "max_cost_usd": p.max_cost_usd, "max_requests": p.max_requests,
                      "window": p.window.value, "burst_multiplier": p.burst_multiplier,
                      "warn_at": p.warn_at, "action_on_exceed": p.action_on_exceed.value,
                      "enabled": p.enabled}
                for eid, p in self._policies.items()
            },
            "pools": {
                name: {"members": pool.members, "pool_tokens": pool.pool_tokens,
                       "pool_cost_usd": pool.pool_cost_usd, "window": pool.window.value}
                for name, pool in self._pools.items()
            },
            "records_count": len(self._records),
        }

    def import_state(self, state: dict[str, Any]) -> None:
        """Rehydrate quotas and pools from an :meth:`export_state` dict.

        Existing policies / pools with the same key are overwritten.
        Historical usage records are not restored.
        """
        for eid, cfg in state.get("quotas", {}).items():
            self.create_quota(eid, **cfg)
        for name, pcfg in state.get("pools", {}).items():
            self.create_pool(name, **pcfg)

    def _window_bounds(self, window: QuotaWindow, now: datetime) -> tuple[datetime, datetime]:
        """Return the ``(start, end)`` bounds of *window* at instant *now*.

        Fixed windows snap to the start of the hour/day/week/month and
        extend one window-duration forward.  Rolling windows return
        ``(now - duration, now)``.
        """
        if window in (QuotaWindow.ROLLING_1H, QuotaWindow.ROLLING_24H, QuotaWindow.ROLLING_7D):
            duration = WINDOW_DURATIONS[window.value]
            return (now - duration, now)
        if window == QuotaWindow.HOURLY:
            start = now.replace(minute=0, second=0, microsecond=0)
        elif window == QuotaWindow.DAILY:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif window == QuotaWindow.WEEKLY:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start -= timedelta(days=start.weekday())
        elif window == QuotaWindow.MONTHLY:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        duration = WINDOW_DURATIONS[window.value]
        return (start, start + duration)

    def _pool_used_tokens(self, pool_name: str, window: QuotaWindow, now: datetime) -> int:
        """Sum tokens drawn from *pool_name* in the current window.

        Uses bisect on the per-pool timestamp index for O(log n) start
        lookup instead of scanning all pool records linearly.
        """
        start, end = self._window_bounds(window, now)
        pool_recs = self._pool_usage.get(pool_name, [])
        if not pool_recs:
            return 0
        timestamps = self._pool_timestamps.get(pool_name, [])
        lo = bisect.bisect_left(timestamps, start)
        total = 0
        for i in range(lo, len(pool_recs)):
            r = pool_recs[i]
            if r.timestamp >= end:
                break
            total += r.tokens
        return total

    def _try_pool_borrow(self, entity_id: str, tokens: int, now: datetime) -> int:
        """Attempt to borrow *tokens* from any enabled shared pool that
        lists *entity_id* as a member.

        Returns the number of tokens actually borrowed (0 if no pool has
        the headroom).  On success the borrow is recorded against the
        pool so subsequent calls in the same window see reduced capacity.
        """
        for pool in self._pools.values():
            if not pool.enabled or entity_id not in pool.members:
                continue
            pool_used = self._pool_used_tokens(pool.name, pool.window, now)
            available = pool.pool_tokens - pool_used
            if available >= tokens:
                record = UsageRecord(timestamp=now, entity_id=entity_id, tokens=tokens)
                self._pool_usage[pool.name].append(record)
                self._pool_timestamps[pool.name].append(record.timestamp)
                return tokens
        return 0
