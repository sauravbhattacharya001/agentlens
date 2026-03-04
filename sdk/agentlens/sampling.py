"""Trace sampling and rate limiting for production deployments.

In production, capturing every trace creates storage and performance
overhead. This module provides configurable sampling strategies to
keep interesting traces while dropping routine ones.

Sampling strategies
-------------------
1. **Probabilistic** -- random sampling at a fixed rate (e.g. 10%)
2. **Rate-limited** -- cap traces per time window (e.g. 100/minute)
3. **Priority-based** -- always keep error/slow traces, sample the rest
4. **Tail-based** -- decide after trace completes (keep if error/slow)
5. **Composite** -- chain multiple strategies with AND/OR logic

Usage::

    from agentlens.sampling import (
        ProbabilisticSampler,
        RateLimitSampler,
        PrioritySampler,
        TailSampler,
        CompositeSampler,
        SamplingDecision,
    )

    # Simple: keep 10% of traces
    sampler = ProbabilisticSampler(rate=0.1)

    # Production: keep errors + slow traces, sample 20% of the rest
    sampler = PrioritySampler(
        error_always_keep=True,
        slow_threshold_ms=5000,
        fallback_rate=0.2,
    )

    # Rate cap: max 50 traces per 60-second window
    sampler = RateLimitSampler(max_traces=50, window_seconds=60)

    # Composite: priority AND rate limit
    sampler = CompositeSampler(
        strategies=[
            PrioritySampler(error_always_keep=True, fallback_rate=0.5),
            RateLimitSampler(max_traces=100, window_seconds=60),
        ],
        mode="all",  # all must accept
    )

    decision = sampler.should_sample(trace_context)
    if decision.sampled:
        # send trace to backend
        ...
"""

from __future__ import annotations

import hashlib
import random
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence


class SamplingReason(Enum):
    PROBABILISTIC = "probabilistic"
    RATE_LIMITED = "rate_limited"
    RATE_ALLOWED = "rate_allowed"
    PRIORITY_ERROR = "priority_error"
    PRIORITY_SLOW = "priority_slow"
    PRIORITY_IMPORTANT = "priority_important"
    PRIORITY_FALLBACK = "priority_fallback"
    TAIL_ERROR = "tail_error"
    TAIL_SLOW = "tail_slow"
    TAIL_NORMAL = "tail_normal"
    COMPOSITE = "composite"
    FORCED_KEEP = "forced_keep"
    FORCED_DROP = "forced_drop"


@dataclass
class SamplingDecision:
    sampled: bool
    reason: SamplingReason
    strategy: str = ""
    rate: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.sampled


@dataclass
class TraceContext:
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    duration_ms: float | None = None
    has_error: bool = False
    error_type: str | None = None
    span_count: int = 0
    event_count: int = 0
    priority: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SamplerStats:
    total_decisions: int = 0
    sampled_count: int = 0
    dropped_count: int = 0
    forced_keep: int = 0
    forced_drop: int = 0

    @property
    def effective_rate(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return self.sampled_count / self.total_decisions

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "sampled": self.sampled_count,
            "dropped": self.dropped_count,
            "forced_keep": self.forced_keep,
            "forced_drop": self.forced_drop,
            "effective_rate": round(self.effective_rate, 4),
        }


class Sampler(ABC):
    def __init__(self) -> None:
        self._stats = SamplerStats()
        self._lock = threading.Lock()

    @abstractmethod
    def _decide(self, ctx: TraceContext) -> SamplingDecision: ...

    def should_sample(self, ctx: TraceContext) -> SamplingDecision:
        decision = self._decide(ctx)
        with self._lock:
            self._stats.total_decisions += 1
            if decision.sampled:
                self._stats.sampled_count += 1
            else:
                self._stats.dropped_count += 1
            if decision.reason == SamplingReason.FORCED_KEEP:
                self._stats.forced_keep += 1
            elif decision.reason == SamplingReason.FORCED_DROP:
                self._stats.forced_drop += 1
        return decision

    @property
    def stats(self) -> SamplerStats:
        with self._lock:
            return SamplerStats(
                total_decisions=self._stats.total_decisions,
                sampled_count=self._stats.sampled_count,
                dropped_count=self._stats.dropped_count,
                forced_keep=self._stats.forced_keep,
                forced_drop=self._stats.forced_drop,
            )

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = SamplerStats()

    @property
    @abstractmethod
    def name(self) -> str: ...


class ProbabilisticSampler(Sampler):
    """Sample traces at a fixed probability.

    Uses deterministic hashing on trace_id when available so the same
    trace always gets the same decision.  Falls back to random when
    trace_id is empty.
    """

    def __init__(self, rate: float = 0.1, deterministic: bool = True,
                 seed: int | None = None) -> None:
        super().__init__()
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"rate must be in [0, 1], got {rate}")
        self._rate = rate
        self._deterministic = deterministic
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "probabilistic"

    @property
    def rate(self) -> float:
        return self._rate

    def _hash_to_float(self, trace_id: str) -> float:
        h = hashlib.md5(trace_id.encode(), usedforsecurity=False).digest()
        return int.from_bytes(h[:4], "big") / 0x1_0000_0000

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        if self._rate >= 1.0:
            return SamplingDecision(True, SamplingReason.PROBABILISTIC,
                                    self.name, 1.0)
        if self._rate <= 0.0:
            return SamplingDecision(False, SamplingReason.PROBABILISTIC,
                                    self.name, 0.0)

        if self._deterministic and ctx.trace_id:
            roll = self._hash_to_float(ctx.trace_id)
        else:
            roll = self._rng.random()

        return SamplingDecision(
            sampled=roll < self._rate,
            reason=SamplingReason.PROBABILISTIC,
            strategy=self.name,
            rate=self._rate,
            metadata={"roll": round(roll, 6)},
        )


class RateLimitSampler(Sampler):
    """Cap the number of sampled traces per time window.

    Uses a sliding window of timestamps. Thread-safe.
    """

    def __init__(self, max_traces: int = 100,
                 window_seconds: float = 60.0) -> None:
        super().__init__()
        if max_traces < 0:
            raise ValueError(f"max_traces must be >= 0, got {max_traces}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0")
        self._max_traces = max_traces
        self._window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._window_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "rate_limit"

    @property
    def max_traces(self) -> int:
        return self._max_traces

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        now = time.monotonic()
        with self._window_lock:
            cutoff = now - self._window_seconds
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) < self._max_traces:
                self._timestamps.append(now)
                return SamplingDecision(
                    True, SamplingReason.RATE_ALLOWED, self.name, 1.0,
                    {"window_count": len(self._timestamps),
                     "max_traces": self._max_traces},
                )
            return SamplingDecision(
                False, SamplingReason.RATE_LIMITED, self.name, 0.0,
                {"window_count": len(self._timestamps),
                 "max_traces": self._max_traces},
            )

    def current_count(self) -> int:
        now = time.monotonic()
        with self._window_lock:
            cutoff = now - self._window_seconds
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)


class PrioritySampler(Sampler):
    """Always keep important traces; sample the rest probabilistically.

    Priority rules (evaluated in order):
    1. Error traces -> always keep
    2. Slow traces (duration > threshold) -> always keep
    3. High-priority traces (priority >= threshold) -> always keep
    4. Important tags match -> always keep
    5. Everything else -> sample at fallback_rate
    """

    def __init__(self, error_always_keep: bool = True,
                 slow_threshold_ms: float | None = 5000.0,
                 priority_threshold: int = 5,
                 fallback_rate: float = 0.1,
                 important_tags: dict[str, str] | None = None,
                 seed: int | None = None) -> None:
        super().__init__()
        self._error_keep = error_always_keep
        self._slow_threshold = slow_threshold_ms
        self._priority_threshold = priority_threshold
        if not 0.0 <= fallback_rate <= 1.0:
            raise ValueError(f"fallback_rate must be in [0, 1]")
        self._fallback_rate = fallback_rate
        self._important_tags = important_tags or {}
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "priority"

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        if self._error_keep and ctx.has_error:
            return SamplingDecision(True, SamplingReason.PRIORITY_ERROR,
                                    self.name, 1.0)

        if (self._slow_threshold is not None
                and ctx.duration_ms is not None
                and ctx.duration_ms > self._slow_threshold):
            return SamplingDecision(
                True, SamplingReason.PRIORITY_SLOW, self.name, 1.0,
                {"duration_ms": ctx.duration_ms,
                 "threshold_ms": self._slow_threshold},
            )

        if ctx.priority >= self._priority_threshold:
            return SamplingDecision(
                True, SamplingReason.PRIORITY_IMPORTANT, self.name, 1.0,
                {"priority": ctx.priority},
            )

        for key, value in self._important_tags.items():
            if ctx.tags.get(key) == value:
                return SamplingDecision(
                    True, SamplingReason.PRIORITY_IMPORTANT, self.name, 1.0,
                    {"matched_tag": f"{key}={value}"},
                )

        sampled = self._rng.random() < self._fallback_rate
        return SamplingDecision(sampled, SamplingReason.PRIORITY_FALLBACK,
                                self.name, self._fallback_rate)


class TailSampler(Sampler):
    """Decide after trace completes based on outcome.

    Unlike head-based samplers that decide before processing, tail
    sampling waits until the trace is finished.
    """

    def __init__(self, error_keep: bool = True,
                 slow_threshold_ms: float = 5000.0,
                 min_spans: int | None = None,
                 fallback_rate: float = 0.05,
                 seed: int | None = None) -> None:
        super().__init__()
        self._error_keep = error_keep
        self._slow_threshold = slow_threshold_ms
        self._min_spans = min_spans
        if not 0.0 <= fallback_rate <= 1.0:
            raise ValueError(f"fallback_rate must be in [0, 1]")
        self._fallback_rate = fallback_rate
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "tail"

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        if self._error_keep and ctx.has_error:
            return SamplingDecision(True, SamplingReason.TAIL_ERROR,
                                    self.name, 1.0)

        if (ctx.duration_ms is not None
                and ctx.duration_ms > self._slow_threshold):
            return SamplingDecision(
                True, SamplingReason.TAIL_SLOW, self.name, 1.0,
                {"duration_ms": ctx.duration_ms},
            )

        if (self._min_spans is not None
                and ctx.span_count > self._min_spans):
            return SamplingDecision(
                True, SamplingReason.TAIL_SLOW, self.name, 1.0,
                {"span_count": ctx.span_count},
            )

        sampled = self._rng.random() < self._fallback_rate
        return SamplingDecision(sampled, SamplingReason.TAIL_NORMAL,
                                self.name, self._fallback_rate)


class CompositeSampler(Sampler):
    """Chain multiple samplers with AND ("all") or OR ("any") logic.

    - mode="all": sampled only if ALL strategies accept
    - mode="any": sampled if ANY strategy accepts
    """

    def __init__(self, strategies: Sequence[Sampler],
                 mode: str = "all") -> None:
        super().__init__()
        if not strategies:
            raise ValueError("strategies must not be empty")
        if mode not in ("all", "any"):
            raise ValueError(f"mode must be 'all' or 'any', got {mode!r}")
        self._strategies = list(strategies)
        self._mode = mode

    @property
    def name(self) -> str:
        names = "+".join(s.name for s in self._strategies)
        return f"composite({self._mode}:{names})"

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        decisions = [s.should_sample(ctx) for s in self._strategies]

        if self._mode == "all":
            sampled = all(d.sampled for d in decisions)
        else:
            sampled = any(d.sampled for d in decisions)

        return SamplingDecision(
            sampled=sampled,
            reason=SamplingReason.COMPOSITE,
            strategy=self.name,
            rate=decisions[0].rate if decisions else 1.0,
            metadata={
                "mode": self._mode,
                "sub_decisions": [
                    {"strategy": d.strategy, "sampled": d.sampled,
                     "reason": d.reason.value}
                    for d in decisions
                ],
            },
        )


class AlwaysSampler(Sampler):
    """Always sample (keep every trace). Useful for dev/testing."""

    @property
    def name(self) -> str:
        return "always"

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        return SamplingDecision(True, SamplingReason.FORCED_KEEP,
                                self.name, 1.0)


class NeverSampler(Sampler):
    """Never sample (drop every trace). Useful for testing."""

    @property
    def name(self) -> str:
        return "never"

    def _decide(self, ctx: TraceContext) -> SamplingDecision:
        return SamplingDecision(False, SamplingReason.FORCED_DROP,
                                self.name, 0.0)
