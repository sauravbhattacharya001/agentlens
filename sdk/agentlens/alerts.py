"""Client-side alert rules and metric aggregation for AgentLens.

Provides local, real-time alerting that evaluates incoming events
against declarative rules without requiring a backend round-trip.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Severity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Condition(str, Enum):
    """Comparison conditions for alert rules."""
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    ABSENT = "absent"
    RATE_CHANGE = "rate_change"


@dataclass
class AlertRule:
    """Declarative alert rule definition.

    Attributes:
        name: Human-readable rule name (unique identifier).
        metric: Metric to monitor (latency_p95, error_rate, event_count,
            total_cost, total_tokens, heartbeat).
        condition: Comparison condition.
        threshold: Threshold value.
        window_seconds: Time window for metric aggregation (default 300 = 5 min).
        cooldown_seconds: Minimum seconds between alerts for this rule (default 900 = 15 min).
        severity: Alert severity level.
        enabled: Whether this rule is active.
        agent_filter: Optional agent name filter (only evaluate events from this agent).
    """
    name: str
    metric: str
    condition: Condition
    threshold: float
    window_seconds: int = 300
    cooldown_seconds: int = 900
    severity: Severity = Severity.WARNING
    enabled: bool = True
    agent_filter: str | None = None


@dataclass
class Alert:
    """A triggered alert event.

    Attributes:
        rule_name: Name of the rule that fired.
        metric: Metric that triggered the alert.
        value: Current metric value.
        threshold: Threshold that was exceeded.
        severity: Alert severity.
        message: Human-readable alert message.
        timestamp: When the alert fired (monotonic seconds via time.time()).
        agent_name: Agent that triggered the alert (if filtered).
    """
    rule_name: str
    metric: str
    value: float
    threshold: float
    severity: Severity
    message: str
    timestamp: float = field(default_factory=time.time)
    agent_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict for serialization."""
        return {
            "rule_name": self.rule_name,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "agent_name": self.agent_name,
        }


class MetricAggregator:
    """Computes rolling metrics over a sliding time window.

    Maintains a time-sorted deque of event records and computes
    aggregate statistics on demand. Automatically evicts expired entries.

    Thread-safe: all mutations are guarded by a lock.
    """

    def __init__(self, window_seconds: int = 300) -> None:
        self._window = window_seconds
        self._events: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()

    @property
    def window_seconds(self) -> int:
        return self._window

    def record(self, event: dict[str, Any]) -> None:
        """Record an event for aggregation.

        Expected keys (all optional):
            - timestamp (float): Event time (defaults to now).
            - duration_ms (float): Event duration in milliseconds.
            - tokens_in (int): Input tokens.
            - tokens_out (int): Output tokens.
            - cost (float): Estimated cost.
            - error (bool): Whether this event is an error.
            - agent_name (str): Agent that produced the event.
        """
        entry = dict(event)
        if "timestamp" not in entry:
            entry["timestamp"] = time.time()
        with self._lock:
            self._events.append(entry)
            self._evict()

    def _evict(self) -> None:
        """Remove events outside the time window. Must hold lock."""
        cutoff = time.time() - self._window
        while self._events and self._events[0].get("timestamp", 0) < cutoff:
            self._events.popleft()

    def _filtered(self, agent_filter: str | None = None) -> list[dict[str, Any]]:
        """Get non-expired events, optionally filtered by agent. Must hold lock."""
        self._evict()
        events = list(self._events)
        if agent_filter:
            events = [e for e in events if e.get("agent_name") == agent_filter]
        return events

    def get_metric(self, metric: str, agent_filter: str | None = None) -> float:
        """Compute a metric value over the current window.

        Supported metrics:
            - event_count: Total events in window.
            - error_rate: Fraction of events with error=True.
            - total_tokens: Sum of tokens_in + tokens_out.
            - total_cost: Sum of cost values.
            - latency_p50: 50th percentile duration_ms.
            - latency_p95: 95th percentile duration_ms.
            - latency_p99: 99th percentile duration_ms.
            - avg_duration_ms: Mean duration_ms.
            - heartbeat: Seconds since last event (for absence detection).

        Returns:
            The computed metric value. Returns 0.0 for empty windows
            (except heartbeat, which returns infinity).
        """
        with self._lock:
            events = self._filtered(agent_filter)

        if metric == "heartbeat":
            if not events:
                return float("inf")
            last_ts = max(e.get("timestamp", 0) for e in events)
            return time.time() - last_ts

        if not events:
            return 0.0

        if metric == "event_count":
            return float(len(events))

        if metric == "error_rate":
            errors = sum(1 for e in events if e.get("error"))
            return errors / len(events)

        if metric == "total_tokens":
            return float(sum(
                e.get("tokens_in", 0) + e.get("tokens_out", 0) for e in events
            ))

        if metric == "total_cost":
            return sum(e.get("cost", 0.0) for e in events)

        if metric in ("latency_p50", "latency_p95", "latency_p99"):
            durations = sorted(e.get("duration_ms", 0.0) for e in events if e.get("duration_ms") is not None)
            if not durations:
                return 0.0
            p = {"latency_p50": 50, "latency_p95": 95, "latency_p99": 99}[metric]
            return self._percentile(durations, p)

        if metric == "avg_duration_ms":
            durations = [e.get("duration_ms", 0.0) for e in events if e.get("duration_ms") is not None]
            if not durations:
                return 0.0
            return sum(durations) / len(durations)

        raise ValueError(f"Unknown metric: {metric}")

    @staticmethod
    def _percentile(sorted_values: list[float], p: int) -> float:
        """Compute the p-th percentile using linear interpolation."""
        n = len(sorted_values)
        if n == 0:
            return 0.0
        if n == 1:
            return sorted_values[0]
        k = (p / 100.0) * (n - 1)
        lo = int(k)
        hi = min(lo + 1, n - 1)
        frac = k - lo
        return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])

    def clear(self) -> None:
        """Clear all recorded events."""
        with self._lock:
            self._events.clear()


class AlertManager:
    """Evaluates alert rules against incoming events and dispatches notifications.

    Manages cooldown state to prevent alert storms. Thread-safe.

    Usage::

        rules = [
            AlertRule(name="high_latency", metric="latency_p95",
                      condition=Condition.GREATER_THAN, threshold=5000),
        ]
        manager = AlertManager(rules)
        manager.on_alert(lambda alert: print(f"ALERT: {alert.message}"))

        # Feed events as they arrive
        manager.process_event({"duration_ms": 6000, "agent_name": "my-agent"})
    """

    def __init__(self, rules: list[AlertRule] | None = None, default_window: int = 300) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._aggregators: dict[int, MetricAggregator] = {}  # window_seconds -> aggregator
        self._cooldowns: dict[str, float] = {}  # rule_name -> last_alert_time
        self._callbacks: list[Callable[[Alert], Any]] = []
        self._alert_history: list[Alert] = []
        self._lock = threading.Lock()
        self._default_window = default_window

        for rule in (rules or []):
            self.add_rule(rule)

    def add_rule(self, rule: AlertRule) -> None:
        """Add or replace an alert rule."""
        with self._lock:
            self._rules[rule.name] = rule
            # Ensure we have an aggregator for this window size
            if rule.window_seconds not in self._aggregators:
                self._aggregators[rule.window_seconds] = MetricAggregator(rule.window_seconds)

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if found."""
        with self._lock:
            if name in self._rules:
                del self._rules[name]
                self._cooldowns.pop(name, None)
                return True
            return False

    def get_rules(self) -> list[AlertRule]:
        """Get all registered rules."""
        with self._lock:
            return list(self._rules.values())

    def on_alert(self, callback: Callable[[Alert], Any]) -> None:
        """Register a callback for when an alert fires."""
        self._callbacks.append(callback)

    def process_event(self, event: dict[str, Any]) -> list[Alert]:
        """Process an incoming event and evaluate all rules.

        Records the event in all aggregators, then evaluates each enabled
        rule. Returns any alerts that fired.

        Args:
            event: Event dict with optional keys: duration_ms, tokens_in,
                tokens_out, cost, error, agent_name, timestamp.

        Returns:
            List of Alert objects that fired during this evaluation.
        """
        # Record in all aggregators
        with self._lock:
            for agg in self._aggregators.values():
                agg.record(event)

        return self.evaluate()

    def evaluate(self) -> list[Alert]:
        """Evaluate all rules against current metrics. Returns fired alerts."""
        fired: list[Alert] = []
        now = time.time()

        with self._lock:
            rules = list(self._rules.values())

        for rule in rules:
            if not rule.enabled:
                continue

            # Atomically check cooldown AND reserve slot
            with self._lock:
                last_fired = self._cooldowns.get(rule.name, 0)
                if now - last_fired < rule.cooldown_seconds:
                    continue
                # Reserve cooldown slot to prevent concurrent duplicate fires
                self._cooldowns[rule.name] = now

            # Get aggregator
            with self._lock:
                agg = self._aggregators.get(rule.window_seconds)
            if agg is None:
                continue

            try:
                value = agg.get_metric(rule.metric, agent_filter=rule.agent_filter)
            except ValueError:
                # Rule didn't fire — release the cooldown reservation
                with self._lock:
                    if self._cooldowns.get(rule.name) == now:
                        self._cooldowns[rule.name] = last_fired
                continue

            if self._check_condition(rule.condition, value, rule.threshold):
                alert = Alert(
                    rule_name=rule.name,
                    metric=rule.metric,
                    value=value,
                    threshold=rule.threshold,
                    severity=rule.severity,
                    message=self._format_message(rule, value),
                    timestamp=now,
                    agent_name=rule.agent_filter,
                )
                fired.append(alert)

                with self._lock:
                    self._alert_history.append(alert)

                for cb in self._callbacks:
                    try:
                        cb(alert)
                    except Exception:
                        pass
            else:
                # Condition not met — release the cooldown reservation
                with self._lock:
                    if self._cooldowns.get(rule.name) == now:
                        self._cooldowns[rule.name] = last_fired

        return fired

    @staticmethod
    def _check_condition(condition: Condition, value: float, threshold: float) -> bool:
        """Evaluate a condition against a value and threshold."""
        if condition == Condition.GREATER_THAN:
            return value > threshold
        if condition == Condition.LESS_THAN:
            return value < threshold
        if condition == Condition.EQUALS:
            return value == threshold
        if condition == Condition.NOT_EQUALS:
            return value != threshold
        if condition == Condition.ABSENT:
            # For heartbeat: value (seconds since last event) > threshold
            return value > threshold
        if condition == Condition.RATE_CHANGE:
            # For rate_change: treat threshold as a percentage
            return abs(value) > threshold
        return False

    @staticmethod
    def _format_message(rule: AlertRule, value: float) -> str:
        """Create a human-readable alert message."""
        cond_str = rule.condition.value.replace("_", " ")
        agent_str = f" (agent: {rule.agent_filter})" if rule.agent_filter else ""
        return (
            f"[{rule.severity.value.upper()}] {rule.name}: "
            f"{rule.metric} = {value:.2f} ({cond_str} {rule.threshold})"
            f"{agent_str}"
        )

    def get_alert_history(self, limit: int = 50) -> list[Alert]:
        """Get recent alert history."""
        with self._lock:
            return list(self._alert_history[-limit:])

    def clear_cooldowns(self) -> None:
        """Reset all cooldown timers."""
        with self._lock:
            self._cooldowns.clear()

    def clear_history(self) -> None:
        """Clear alert history."""
        with self._lock:
            self._alert_history.clear()

    def reset(self) -> None:
        """Reset all state: cooldowns, history, and aggregator data."""
        with self._lock:
            self._cooldowns.clear()
            self._alert_history.clear()
            for agg in self._aggregators.values():
                agg.clear()
