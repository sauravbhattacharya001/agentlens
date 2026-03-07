"""Rate limiter for tracking and enforcing token/request budgets.

Provides sliding-window rate limiting for LLM API calls, helping agents
stay within provider rate limits and avoid 429 errors. Supports both
token-based and request-based limits with configurable windows.

Usage::

    from agentlens import RateLimiter, RateLimit, RateLimitPolicy

    policy = RateLimitPolicy(limits=[
        RateLimit(resource="requests", limit=60, window_seconds=60),
        RateLimit(resource="tokens", limit=90_000, window_seconds=60),
        RateLimit(resource="tokens", limit=1_000_000, window_seconds=3600),
    ])

    limiter = RateLimiter(policy)

    # Check before making a call
    result = limiter.check("tokens", estimated=1500)
    if not result.allowed:
        print(f"Rate limited! Retry after {result.retry_after_ms}ms")

    # Record usage after a call
    limiter.record("tokens", 1200)
    limiter.record("requests", 1)

    # Get current utilization
    report = limiter.report()
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RateLimitAction(str, Enum):
    """What to do when a rate limit is hit."""
    WARN = "warn"
    BLOCK = "block"


@dataclass
class RateLimit:
    """A single rate limit rule.

    Attributes:
        resource: What is being limited (e.g. ``"tokens"``, ``"requests"``).
        limit: Maximum allowed usage within the window.
        window_seconds: Sliding window duration in seconds.
        action: Whether to warn or block when the limit is exceeded.
        label: Optional human-readable label for this limit.
    """
    resource: str
    limit: int
    window_seconds: float
    action: RateLimitAction = RateLimitAction.BLOCK
    label: str | None = None

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError(f"limit must be positive, got {self.limit}")
        if self.window_seconds <= 0:
            raise ValueError(f"window_seconds must be positive, got {self.window_seconds}")


@dataclass
class RateLimitPolicy:
    """Collection of rate limit rules.

    Attributes:
        limits: List of rate limit rules to enforce.
        warn_at_pct: Emit a warning callback when utilization reaches
            this percentage (0-100). Default 80%.
    """
    limits: list[RateLimit] = field(default_factory=list)
    warn_at_pct: float = 80.0

    def add(self, resource: str, limit: int, window_seconds: float,
            action: RateLimitAction = RateLimitAction.BLOCK,
            label: str | None = None) -> RateLimitPolicy:
        """Add a rate limit rule (fluent API)."""
        self.limits.append(RateLimit(
            resource=resource, limit=limit,
            window_seconds=window_seconds, action=action, label=label,
        ))
        return self


@dataclass
class CheckResult:
    """Result of a rate limit check.

    Attributes:
        allowed: Whether the request is allowed.
        resource: The resource that was checked.
        current_usage: Current usage in the window.
        limit: The limit value.
        utilization_pct: Current utilization as a percentage.
        retry_after_ms: Suggested wait time in ms before retrying (0 if allowed).
        warnings: Any warning messages (e.g. approaching limit).
        violated_limits: Labels/descriptions of limits that were violated.
    """
    allowed: bool
    resource: str
    current_usage: int
    limit: int
    utilization_pct: float
    retry_after_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    violated_limits: list[str] = field(default_factory=list)


@dataclass
class WindowStats:
    """Statistics for a single rate limit window."""
    resource: str
    label: str
    current_usage: int
    limit: int
    window_seconds: float
    utilization_pct: float
    remaining: int
    oldest_entry_age_ms: float
    action: RateLimitAction


@dataclass
class RateLimitReport:
    """Full report of all rate limit windows.

    Attributes:
        windows: Per-window statistics.
        total_recorded: Total usage recorded per resource (all time).
        total_blocked: Total requests blocked per resource (all time).
        total_warnings: Total warnings emitted per resource (all time).
    """
    windows: list[WindowStats]
    total_recorded: dict[str, int]
    total_blocked: dict[str, int]
    total_warnings: dict[str, int]

    @property
    def healthy(self) -> bool:
        """True if all windows are below 90% utilization."""
        return all(w.utilization_pct < 90.0 for w in self.windows)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "healthy": self.healthy,
            "windows": [
                {
                    "resource": w.resource,
                    "label": w.label,
                    "current_usage": w.current_usage,
                    "limit": w.limit,
                    "window_seconds": w.window_seconds,
                    "utilization_pct": round(w.utilization_pct, 1),
                    "remaining": w.remaining,
                    "oldest_entry_age_ms": round(w.oldest_entry_age_ms, 1),
                    "action": w.action.value,
                }
                for w in self.windows
            ],
            "total_recorded": self.total_recorded,
            "total_blocked": self.total_blocked,
            "total_warnings": self.total_warnings,
        }


class _SlidingWindow:
    """Thread-safe sliding window counter."""

    def __init__(self, window_seconds: float) -> None:
        self.window_seconds = window_seconds
        self._entries: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()

    def add(self, amount: int, now: float | None = None) -> None:
        now = now or time.monotonic()
        with self._lock:
            self._entries.append((now, amount))
            self._prune(now)

    def total(self, now: float | None = None) -> int:
        now = now or time.monotonic()
        with self._lock:
            self._prune(now)
            return sum(amt for _, amt in self._entries)

    def oldest_age_ms(self, now: float | None = None) -> float:
        now = now or time.monotonic()
        with self._lock:
            self._prune(now)
            if not self._entries:
                return 0.0
            return (now - self._entries[0][0]) * 1000

    def ms_until_capacity(self, needed: int, limit: int, now: float | None = None) -> int:
        """Estimate ms to wait until `needed` capacity is available."""
        now = now or time.monotonic()
        with self._lock:
            self._prune(now)
            current = sum(amt for _, amt in self._entries)
            if current + needed <= limit:
                return 0
            # Walk entries oldest-first until enough would expire
            freed = 0
            target = current + needed - limit
            for ts, amt in self._entries:
                freed += amt
                if freed >= target:
                    wait = (ts + self.window_seconds) - now
                    return max(0, int(wait * 1000))
            # All entries need to expire
            if self._entries:
                wait = (self._entries[-1][0] + self.window_seconds) - now
                return max(0, int(wait * 1000))
            return 0


class RateLimiter:
    """Sliding-window rate limiter for agent API calls.

    Thread-safe. Supports multiple resources (tokens, requests) with
    multiple windows per resource (e.g. per-minute and per-hour limits).

    Args:
        policy: The rate limit policy to enforce.
        on_warning: Optional callback ``(resource, message)`` when
            utilization crosses the warning threshold.
    """

    def __init__(
        self,
        policy: RateLimitPolicy,
        on_warning: Any | None = None,
    ) -> None:
        self.policy = policy
        self.on_warning = on_warning
        self._windows: dict[int, _SlidingWindow] = {}
        self._stats_recorded: dict[str, int] = {}
        self._stats_blocked: dict[str, int] = {}
        self._stats_warnings: dict[str, int] = {}

        for i, rl in enumerate(policy.limits):
            self._windows[i] = _SlidingWindow(rl.window_seconds)
            self._stats_recorded.setdefault(rl.resource, 0)
            self._stats_blocked.setdefault(rl.resource, 0)
            self._stats_warnings.setdefault(rl.resource, 0)

    def check(self, resource: str, estimated: int = 1) -> CheckResult:
        """Check if a request would be allowed under rate limits.

        Does NOT record usage — call :meth:`record` after the actual API call.

        Args:
            resource: Resource type (e.g. ``"tokens"``, ``"requests"``).
            estimated: Estimated usage amount for this request.

        Returns:
            A :class:`CheckResult` with allow/deny and utilization info.
        """
        now = time.monotonic()
        worst_util = 0.0
        worst_limit = 0
        worst_usage = 0
        max_retry = 0
        warnings: list[str] = []
        violated: list[str] = []
        all_allowed = True

        for i, rl in enumerate(self.policy.limits):
            if rl.resource != resource:
                continue
            window = self._windows[i]
            current = window.total(now)
            util_pct = (current / rl.limit) * 100 if rl.limit else 0

            if current + estimated > rl.limit:
                if rl.action == RateLimitAction.BLOCK:
                    all_allowed = False
                    retry = window.ms_until_capacity(estimated, rl.limit, now)
                    max_retry = max(max_retry, retry)
                label = rl.label or f"{rl.resource}/{rl.window_seconds}s"
                violated.append(label)
                self._stats_blocked[resource] = self._stats_blocked.get(resource, 0) + 1

            if util_pct >= self.policy.warn_at_pct:
                label = rl.label or f"{rl.resource}/{rl.window_seconds}s"
                msg = f"{label}: {util_pct:.0f}% utilized ({current}/{rl.limit})"
                warnings.append(msg)
                self._stats_warnings[resource] = self._stats_warnings.get(resource, 0) + 1
                if self.on_warning:
                    self.on_warning(resource, msg)

            if util_pct > worst_util:
                worst_util = util_pct
                worst_limit = rl.limit
                worst_usage = current

        return CheckResult(
            allowed=all_allowed,
            resource=resource,
            current_usage=worst_usage,
            limit=worst_limit,
            utilization_pct=round(worst_util, 1),
            retry_after_ms=max_retry,
            warnings=warnings,
            violated_limits=violated,
        )

    def record(self, resource: str, amount: int = 1) -> None:
        """Record actual usage after an API call completes.

        Args:
            resource: Resource type (e.g. ``"tokens"``, ``"requests"``).
            amount: Actual usage amount consumed.
        """
        now = time.monotonic()
        for i, rl in enumerate(self.policy.limits):
            if rl.resource != resource:
                continue
            self._windows[i].add(amount, now)
        self._stats_recorded[resource] = self._stats_recorded.get(resource, 0) + amount

    def report(self) -> RateLimitReport:
        """Get a full utilization report across all windows."""
        now = time.monotonic()
        windows: list[WindowStats] = []
        for i, rl in enumerate(self.policy.limits):
            window = self._windows[i]
            current = window.total(now)
            util = (current / rl.limit) * 100 if rl.limit else 0
            windows.append(WindowStats(
                resource=rl.resource,
                label=rl.label or f"{rl.resource}/{rl.window_seconds}s",
                current_usage=current,
                limit=rl.limit,
                window_seconds=rl.window_seconds,
                utilization_pct=round(util, 1),
                remaining=max(0, rl.limit - current),
                oldest_entry_age_ms=window.oldest_age_ms(now),
                action=rl.action,
            ))
        return RateLimitReport(
            windows=windows,
            total_recorded=dict(self._stats_recorded),
            total_blocked=dict(self._stats_blocked),
            total_warnings=dict(self._stats_warnings),
        )

    def reset(self) -> None:
        """Reset all windows and counters."""
        for i, rl in enumerate(self.policy.limits):
            self._windows[i] = _SlidingWindow(rl.window_seconds)
        self._stats_recorded = {k: 0 for k in self._stats_recorded}
        self._stats_blocked = {k: 0 for k in self._stats_blocked}
        self._stats_warnings = {k: 0 for k in self._stats_warnings}


# ── Preset policies ─────────────────────────────────────────────────

def openai_tier1_policy() -> RateLimitPolicy:
    """Preset policy matching OpenAI Tier 1 rate limits (GPT-4o).

    - 500 requests/min
    - 30,000 tokens/min
    - 5,000,000 tokens/day
    """
    return RateLimitPolicy(limits=[
        RateLimit("requests", 500, 60, label="OpenAI requests/min"),
        RateLimit("tokens", 30_000, 60, label="OpenAI tokens/min"),
        RateLimit("tokens", 5_000_000, 86400, label="OpenAI tokens/day"),
    ])


def anthropic_tier1_policy() -> RateLimitPolicy:
    """Preset policy matching Anthropic Tier 1 rate limits.

    - 60 requests/min
    - 60,000 tokens/min
    - 1,000,000 tokens/day
    """
    return RateLimitPolicy(limits=[
        RateLimit("requests", 60, 60, label="Anthropic requests/min"),
        RateLimit("tokens", 60_000, 60, label="Anthropic tokens/min"),
        RateLimit("tokens", 1_000_000, 86400, label="Anthropic tokens/day"),
    ])


def conservative_policy() -> RateLimitPolicy:
    """Conservative policy suitable for development/testing.

    - 10 requests/min
    - 10,000 tokens/min
    """
    return RateLimitPolicy(limits=[
        RateLimit("requests", 10, 60, label="dev requests/min"),
        RateLimit("tokens", 10_000, 60, label="dev tokens/min"),
    ])
