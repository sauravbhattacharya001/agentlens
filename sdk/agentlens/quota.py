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

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional


class QuotaScope(Enum):
    AGENT = "agent"
    MODEL = "model"
    TEAM = "team"
    ORGANIZATION = "organization"


class QuotaWindow(Enum):
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
    WARN = "warn"
    THROTTLE = "throttle"
    DENY = "deny"


@dataclass
class UsageRecord:
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entity_id: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    session_id: str = ""


@dataclass
class QuotaPolicy:
    quota_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
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
    pool_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    members: list[str] = field(default_factory=list)
    pool_tokens: int = 0
    pool_cost_usd: float = 0.0
    window: QuotaWindow = QuotaWindow.DAILY
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    enabled: bool = True


@dataclass
class FleetReport:
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
    filled = int(min(ratio, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


class QuotaManager:
    """Manages usage quotas across agents, models, and teams."""

    def __init__(self, *, now_fn=None):
        self._policies: dict[str, QuotaPolicy] = {}
        self._records: list[UsageRecord] = []
        # Per-entity index for O(entity_records) lookups instead of
        # O(all_records) linear scans in record_usage/check_usage/report.
        self._entity_records: dict[str, list[UsageRecord]] = defaultdict(list)
        self._pools: dict[str, SharedPool] = {}
        self._pool_usage: dict[str, list[UsageRecord]] = defaultdict(list)
        self._callbacks: list[Any] = []
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def create_quota(self, entity_id: str, *, scope: str = "agent",
                     max_tokens: Optional[int] = None, max_cost_usd: Optional[float] = None,
                     max_requests: Optional[int] = None, window: str = "daily",
                     burst_multiplier: float = 1.0, warn_at: float = 0.8,
                     action_on_exceed: str = "deny", metadata: Optional[dict] = None,
                     **kwargs) -> QuotaPolicy:
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
        return self._policies.get(entity_id)

    def update_quota(self, entity_id: str, **kwargs) -> QuotaPolicy:
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
        return self._policies.pop(entity_id, None) is not None

    def list_quotas(self, *, scope: Optional[str] = None) -> list[QuotaPolicy]:
        policies = list(self._policies.values())
        if scope:
            s = QuotaScope(scope)
            policies = [p for p in policies if p.scope == s]
        return policies

    def disable_quota(self, entity_id: str) -> None:
        if entity_id in self._policies:
            self._policies[entity_id].enabled = False

    def enable_quota(self, entity_id: str) -> None:
        if entity_id in self._policies:
            self._policies[entity_id].enabled = True

    def create_pool(self, name: str, *, members: list[str], pool_tokens: int = 0,
                    pool_cost_usd: float = 0.0, window: str = "daily") -> SharedPool:
        pool = SharedPool(name=name, members=list(members), pool_tokens=pool_tokens,
                          pool_cost_usd=pool_cost_usd, window=QuotaWindow(window))
        self._pools[name] = pool
        return pool

    def get_pool(self, name: str) -> Optional[SharedPool]:
        return self._pools.get(name)

    def add_pool_member(self, pool_name: str, entity_id: str) -> None:
        pool = self._pools.get(pool_name)
        if pool and entity_id not in pool.members:
            pool.members.append(entity_id)

    def remove_pool_member(self, pool_name: str, entity_id: str) -> None:
        pool = self._pools.get(pool_name)
        if pool and entity_id in pool.members:
            pool.members.remove(entity_id)

    def delete_pool(self, name: str) -> bool:
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
        """
        start, end = self._window_bounds(window, now)
        entity_recs = self._entity_records.get(entity_id, [])
        tokens_used = 0
        cost_used = 0.0
        matched: list[UsageRecord] = []
        for r in entity_recs:
            if start <= r.timestamp < end:
                tokens_used += r.tokens
                cost_used += r.cost_usd
                matched.append(r)
        return tokens_used, cost_used, len(matched), matched

    def record_usage(self, entity_id: str, *, tokens: int = 0, cost_usd: float = 0.0,
                     model: str = "", session_id: str = "") -> QuotaCheck:
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
        self._callbacks.append(callback)

    def report(self, entity_id: str) -> QuotaReport:
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
        removed = self._entity_records.pop(entity_id, [])
        count = len(removed)
        if count:
            # Rebuild the flat list only when records were actually removed.
            removed_ids = {id(r) for r in removed}
            self._records = [r for r in self._records if id(r) not in removed_ids]
        return count

    def export_state(self) -> dict[str, Any]:
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
        for eid, cfg in state.get("quotas", {}).items():
            self.create_quota(eid, **cfg)
        for name, pcfg in state.get("pools", {}).items():
            self.create_pool(name, **pcfg)

    def _window_bounds(self, window: QuotaWindow, now: datetime) -> tuple[datetime, datetime]:
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
        """Sum tokens drawn from *pool_name* in the current window."""
        start, end = self._window_bounds(window, now)
        return sum(
            r.tokens for r in self._pool_usage.get(pool_name, [])
            if start <= r.timestamp < end
        )

    def _try_pool_borrow(self, entity_id: str, tokens: int, now: datetime) -> int:
        for pool in self._pools.values():
            if not pool.enabled or entity_id not in pool.members:
                continue
            pool_used = self._pool_used_tokens(pool.name, pool.window, now)
            available = pool.pool_tokens - pool_used
            if available >= tokens:
                self._pool_usage[pool.name].append(
                    UsageRecord(timestamp=now, entity_id=entity_id, tokens=tokens))
                return tokens
        return 0
