"""Token Budget Tracker for controlling and monitoring token usage.

Provides budget management for AI agent sessions — set per-session or
per-agent token limits, receive warnings at configurable thresholds,
and optionally enforce hard caps that raise exceptions.

Example::

    from agentlens.budget import TokenBudget, BudgetTracker

    tracker = BudgetTracker()

    # Set a 10K-token budget with warning at 80%
    budget = tracker.create_budget("session-123",
        max_tokens=10_000,
        warn_at=0.8)

    # Record usage
    tracker.record(budget.budget_id, tokens_in=500, tokens_out=200)

    # Check status
    report = tracker.report(budget.budget_id)
    print(report.utilization)   # 0.07 (7%)
    print(report.remaining)     # 9300

    # With cost tracking (uses model pricing)
    budget = tracker.create_budget("session-456",
        max_tokens=50_000,
        max_cost_usd=5.00,
        model="gpt-4o")
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Model pricing (per 1M tokens, USD) ──────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4":            {"input": 30.00, "output": 60.00},
    "gpt-4-turbo":      {"input": 10.00, "output": 30.00},
    "gpt-4o":           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
    "gpt-3.5-turbo":    {"input": 0.50,  "output": 1.50},
    "claude-3-opus":    {"input": 15.00, "output": 75.00},
    "claude-3-sonnet":  {"input": 3.00,  "output": 15.00},
    "claude-3-haiku":   {"input": 0.25,  "output": 1.25},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-4-opus":    {"input": 15.00, "output": 75.00},
    "claude-4-sonnet":  {"input": 3.00,  "output": 15.00},
    "gemini-pro":       {"input": 0.50,  "output": 1.50},
    "gemini-1.5-pro":   {"input": 1.25,  "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
}


class BudgetStatus(str, Enum):
    """Current state of a token budget."""
    ACTIVE = "active"
    WARNING = "warning"
    EXCEEDED = "exceeded"
    EXHAUSTED = "exhausted"


@dataclass
class BudgetEntry:
    """A single usage record against a budget."""
    tokens_in: int
    tokens_out: int
    model: str | None
    cost_usd: float
    timestamp: datetime
    event_id: str | None = None


@dataclass
class TokenBudget:
    """A token budget with limits, thresholds, and usage tracking."""
    budget_id: str = field(default_factory=_new_id)
    session_id: str = ""
    agent_name: str = ""
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    warn_at: float = 0.8  # fraction (0.0-1.0) to trigger warning
    hard_limit: bool = False  # raise on exceed?
    model: str | None = None  # default model for cost estimation
    created_at: datetime = field(default_factory=_utcnow)
    entries: list[BudgetEntry] = field(default_factory=list)

    # Running totals (updated on record())
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    @property
    def token_utilization(self) -> float | None:
        """Fraction of token budget used (0.0-1.0+), or None if no limit."""
        if self.max_tokens is None or self.max_tokens <= 0:
            return None
        return self.total_tokens / self.max_tokens

    @property
    def cost_utilization(self) -> float | None:
        """Fraction of cost budget used (0.0-1.0+), or None if no limit."""
        if self.max_cost_usd is None or self.max_cost_usd <= 0:
            return None
        return self.total_cost_usd / self.max_cost_usd

    @property
    def utilization(self) -> float:
        """Highest utilization across all limits (token and cost)."""
        vals = []
        if self.token_utilization is not None:
            vals.append(self.token_utilization)
        if self.cost_utilization is not None:
            vals.append(self.cost_utilization)
        return max(vals) if vals else 0.0

    @property
    def remaining_tokens(self) -> int | None:
        """Tokens remaining before limit, or None if no limit."""
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self.total_tokens)

    @property
    def remaining_cost(self) -> float | None:
        """USD remaining before cost limit, or None if no limit."""
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self.total_cost_usd)

    @property
    def status(self) -> BudgetStatus:
        """Current budget status based on utilization."""
        u = self.utilization
        if u >= 1.0:
            return BudgetStatus.EXHAUSTED if self.hard_limit else BudgetStatus.EXCEEDED
        if u >= self.warn_at:
            return BudgetStatus.WARNING
        return BudgetStatus.ACTIVE


class BudgetExceededError(Exception):
    """Raised when a hard-limited budget is exhausted."""

    def __init__(self, budget: TokenBudget, attempted_tokens: int) -> None:
        self.budget = budget
        self.attempted_tokens = attempted_tokens
        super().__init__(
            f"Budget {budget.budget_id} exhausted: "
            f"{budget.total_tokens}/{budget.max_tokens} tokens used, "
            f"attempted to add {attempted_tokens}"
        )


@dataclass
class BudgetReport:
    """Snapshot of budget status for reporting."""
    budget_id: str
    session_id: str
    agent_name: str
    status: BudgetStatus
    total_tokens: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    max_tokens: int | None
    max_cost_usd: float | None
    token_utilization: float | None
    cost_utilization: float | None
    utilization: float
    remaining_tokens: int | None
    remaining_cost: float | None
    warn_at: float
    hard_limit: bool
    entry_count: int
    created_at: datetime
    model: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "budget_id": self.budget_id,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "status": self.status.value,
            "total_tokens": self.total_tokens,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "token_utilization": round(self.token_utilization, 4) if self.token_utilization is not None else None,
            "cost_utilization": round(self.cost_utilization, 4) if self.cost_utilization is not None else None,
            "utilization": round(self.utilization, 4),
            "remaining_tokens": self.remaining_tokens,
            "remaining_cost": round(self.remaining_cost, 6) if self.remaining_cost is not None else None,
            "warn_at": self.warn_at,
            "hard_limit": self.hard_limit,
            "entry_count": self.entry_count,
            "created_at": self.created_at.isoformat(),
            "model": self.model,
        }

    @property
    def summary(self) -> str:
        """Human-readable one-line summary."""
        parts = [f"[{self.status.value.upper()}]"]
        if self.max_tokens is not None:
            pct = round((self.token_utilization or 0) * 100, 1)
            parts.append(f"{self.total_tokens:,}/{self.max_tokens:,} tokens ({pct}%)")
        else:
            parts.append(f"{self.total_tokens:,} tokens (no limit)")
        if self.max_cost_usd is not None:
            parts.append(f"${self.total_cost_usd:.4f}/${self.max_cost_usd:.2f}")
        elif self.total_cost_usd > 0:
            parts.append(f"${self.total_cost_usd:.4f}")
        return " | ".join(parts)


def estimate_cost(tokens_in: int, tokens_out: int, model: str | None) -> float:
    """Estimate cost in USD for a given token count and model.

    Returns 0.0 if the model is unknown or not provided.
    """
    if not model or model not in MODEL_PRICING:
        return 0.0
    pricing = MODEL_PRICING[model]
    cost_in = (tokens_in / 1_000_000) * pricing["input"]
    cost_out = (tokens_out / 1_000_000) * pricing["output"]
    return cost_in + cost_out


class BudgetTracker:
    """Manages multiple token budgets across sessions and agents.

    Acts as a registry for budgets -- create, record usage, query status,
    and receive callbacks when thresholds are crossed.
    """

    def __init__(self) -> None:
        self._budgets: dict[str, TokenBudget] = {}
        self._session_index: dict[str, str] = {}  # session_id -> budget_id
        self._callbacks: list[Callable[[TokenBudget, BudgetStatus], None]] = []

    def on_threshold(self, callback: Callable[[TokenBudget, BudgetStatus], None]) -> None:
        """Register a callback for when a budget crosses a threshold.

        The callback receives the budget and its new status whenever
        the status changes (ACTIVE -> WARNING, WARNING -> EXCEEDED, etc.).

        Args:
            callback: Function(budget, new_status) to invoke.
        """
        self._callbacks.append(callback)

    def create_budget(
        self,
        session_id: str,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        warn_at: float = 0.8,
        hard_limit: bool = False,
        agent_name: str = "",
        model: str | None = None,
    ) -> TokenBudget:
        """Create and register a new token budget.

        Args:
            session_id: The session this budget is for.
            max_tokens: Maximum total tokens (in + out). None = unlimited.
            max_cost_usd: Maximum cost in USD. None = unlimited.
            warn_at: Utilization fraction to trigger warning (default 0.8).
            hard_limit: If True, raise BudgetExceededError when exhausted.
            agent_name: Agent identifier for grouping.
            model: Default model for cost estimation.

        Returns:
            The created TokenBudget.

        Raises:
            ValueError: If warn_at is not in (0, 1] or limits are invalid.
        """
        if warn_at <= 0 or warn_at > 1.0:
            raise ValueError("warn_at must be in (0.0, 1.0]")
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")

        if session_id in self._session_index:
            raise ValueError(
                f"Session {session_id!r} already has a budget "
                f"({self._session_index[session_id]}). "
                f"Remove it first with remove_budget()."
            )

        budget = TokenBudget(
            session_id=session_id,
            agent_name=agent_name,
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            warn_at=warn_at,
            hard_limit=hard_limit,
            model=model,
        )
        self._budgets[budget.budget_id] = budget
        self._session_index[session_id] = budget.budget_id
        return budget

    def record(
        self,
        budget_id: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        model: str | None = None,
        event_id: str | None = None,
    ) -> BudgetStatus:
        """Record token usage against a budget.

        Args:
            budget_id: The budget to charge.
            tokens_in: Input tokens consumed.
            tokens_out: Output tokens consumed.
            model: Model used (overrides budget default for cost calc).
            event_id: Optional event ID for traceability.

        Returns:
            The budget's status after recording.

        Raises:
            KeyError: If budget_id is not found.
            BudgetExceededError: If hard_limit is True and budget is exhausted.
        """
        budget = self._budgets.get(budget_id)
        if budget is None:
            raise KeyError(f"Budget {budget_id} not found")

        total_new = tokens_in + tokens_out

        # Check hard limit BEFORE recording
        if budget.hard_limit and budget.max_tokens is not None:
            if budget.total_tokens + total_new > budget.max_tokens:
                raise BudgetExceededError(budget, total_new)

        effective_model = model or budget.model
        cost = estimate_cost(tokens_in, tokens_out, effective_model)

        # Check cost hard limit
        if budget.hard_limit and budget.max_cost_usd is not None:
            if budget.total_cost_usd + cost > budget.max_cost_usd:
                raise BudgetExceededError(budget, total_new)

        old_status = budget.status

        entry = BudgetEntry(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=effective_model,
            cost_usd=cost,
            timestamp=_utcnow(),
            event_id=event_id,
        )
        budget.entries.append(entry)
        budget.total_tokens_in += tokens_in
        budget.total_tokens_out += tokens_out
        budget.total_tokens += total_new
        budget.total_cost_usd += cost

        new_status = budget.status

        # Fire callbacks on status change
        if new_status != old_status:
            for cb in self._callbacks:
                cb(budget, new_status)

        return new_status

    def record_for_session(
        self,
        session_id: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        model: str | None = None,
        event_id: str | None = None,
    ) -> BudgetStatus | None:
        """Record usage by session ID (convenience method).

        Returns None if no budget exists for this session.
        """
        budget_id = self._session_index.get(session_id)
        if budget_id is None:
            return None
        return self.record(budget_id, tokens_in, tokens_out, model, event_id)

    def report(self, budget_id: str) -> BudgetReport:
        """Generate a snapshot report for a budget.

        Args:
            budget_id: The budget to report on.

        Returns:
            A BudgetReport with all current metrics.

        Raises:
            KeyError: If budget_id is not found.
        """
        budget = self._budgets.get(budget_id)
        if budget is None:
            raise KeyError(f"Budget {budget_id} not found")

        return BudgetReport(
            budget_id=budget.budget_id,
            session_id=budget.session_id,
            agent_name=budget.agent_name,
            status=budget.status,
            total_tokens=budget.total_tokens,
            total_tokens_in=budget.total_tokens_in,
            total_tokens_out=budget.total_tokens_out,
            total_cost_usd=budget.total_cost_usd,
            max_tokens=budget.max_tokens,
            max_cost_usd=budget.max_cost_usd,
            token_utilization=budget.token_utilization,
            cost_utilization=budget.cost_utilization,
            utilization=budget.utilization,
            remaining_tokens=budget.remaining_tokens,
            remaining_cost=budget.remaining_cost,
            warn_at=budget.warn_at,
            hard_limit=budget.hard_limit,
            entry_count=len(budget.entries),
            created_at=budget.created_at,
            model=budget.model,
        )

    def report_for_session(self, session_id: str) -> BudgetReport | None:
        """Get report by session ID. Returns None if no budget exists."""
        budget_id = self._session_index.get(session_id)
        if budget_id is None:
            return None
        return self.report(budget_id)

    def all_reports(self) -> list[BudgetReport]:
        """Get reports for all registered budgets."""
        return [self.report(bid) for bid in self._budgets]

    def get_budget(self, budget_id: str) -> TokenBudget | None:
        """Look up a budget by ID."""
        return self._budgets.get(budget_id)

    def remove_budget(self, budget_id: str) -> bool:
        """Remove a budget. Returns True if it existed."""
        budget = self._budgets.pop(budget_id, None)
        if budget is None:
            return False
        # Only remove the session index entry if it still points to this budget
        if self._session_index.get(budget.session_id) == budget_id:
            del self._session_index[budget.session_id]
        return True
