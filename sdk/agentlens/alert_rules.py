"""Advanced alert rules engine for AgentLens.

Provides a flexible, pattern-based alerting system that evaluates
declarative rules against streams of agent events. Supports threshold,
rate, consecutive-event, regex-pattern, and aggregate conditions, as
well as composite (AND/OR) logic.

Pure Python — no external dependencies beyond the standard library.
"""

from __future__ import annotations

import operator as _op
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ── Enums ──────────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ── Conditions ─────────────────────────────────────────────────────────

_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    ">": _op.gt,
    ">=": _op.ge,
    "<": _op.lt,
    "<=": _op.le,
    "==": _op.eq,
    "!=": _op.ne,
}


class AlertCondition:
    """Base class for alert conditions.

    Subclasses must implement ``evaluate(events) -> bool`` and may
    optionally implement ``matched_events(events) -> list`` to return
    the subset of events that triggered the condition.
    """

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        """Return True if the condition is met."""
        raise NotImplementedError

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the events relevant to this condition firing."""
        return list(events)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        raise NotImplementedError

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AlertCondition":
        """Deserialize from a plain dict."""
        kind = data.get("type")
        if kind == "threshold":
            return ThresholdCondition(data["metric"], data["operator"], data["value"])
        if kind == "rate":
            return RateCondition(data["event_type"], data["threshold_pct"], data.get("window_events", 100))
        if kind == "consecutive":
            return ConsecutiveCondition(data["event_type"], data["count"])
        if kind == "pattern":
            return PatternCondition(data["field"], data["pattern"])
        if kind == "aggregate":
            return AggregateCondition(
                data["metric"], data["agg_func"], data["operator"],
                data["value"], data.get("window", 50),
            )
        if kind == "composite":
            conditions = [AlertCondition.from_dict(c) for c in data["conditions"]]
            return CompositeCondition(conditions, data.get("mode", "all"))
        raise ValueError(f"Unknown condition type: {kind}")


class ThresholdCondition(AlertCondition):
    """Fires when the *sum* of a numeric metric across events crosses a threshold.

    Example: ``ThresholdCondition("cost", ">", 5.0)``
    """

    def __init__(self, metric: str, op: str, value: float) -> None:
        if op not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op}")
        self.metric = metric
        self.op = op
        self.value = value

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        """Return True if the summed metric value crosses the threshold."""
        total = sum(e.get(self.metric, 0) for e in events)
        return _OPERATORS[self.op](total, self.value)

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return events that contain the tracked metric."""
        return [e for e in events if self.metric in e]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return {"type": "threshold", "metric": self.metric, "operator": self.op, "value": self.value}


class RateCondition(AlertCondition):
    """Fires when the proportion of a given event_type exceeds a threshold.

    Example: ``RateCondition("error", 10.0, window_events=100)``
    means "alert if >10 % of the last 100 events are errors".
    """

    def __init__(self, event_type: str, threshold_pct: float, window_events: int = 100) -> None:
        self.event_type = event_type
        self.threshold_pct = threshold_pct
        self.window_events = window_events

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        window = events[-self.window_events:] if len(events) > self.window_events else events
        if not window:
            return False
        matches = sum(1 for e in window if e.get("event_type") == self.event_type)
        rate = (matches / len(window)) * 100.0
        return rate > self.threshold_pct

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        window = events[-self.window_events:] if len(events) > self.window_events else events
        return [e for e in window if e.get("event_type") == self.event_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "rate", "event_type": self.event_type,
            "threshold_pct": self.threshold_pct, "window_events": self.window_events,
        }


class ConsecutiveCondition(AlertCondition):
    """Fires when the last *count* events all have the given event_type.

    Example: ``ConsecutiveCondition("error", 3)``
    """

    def __init__(self, event_type: str, count: int) -> None:
        self.event_type = event_type
        self.count = count

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        if len(events) < self.count:
            return False
        tail = events[-self.count:]
        return all(e.get("event_type") == self.event_type for e in tail)

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return events[-self.count:]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "consecutive", "event_type": self.event_type, "count": self.count}


class PatternCondition(AlertCondition):
    """Fires when *any* event has a field value matching a regex pattern.

    Example: ``PatternCondition("message", r"timeout|connection refused")``
    """

    def __init__(self, field: str, pattern: str) -> None:
        self.field = field
        self.pattern = pattern
        try:
            self._regex = re.compile(pattern)
        except re.error:
            self._regex = None

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        if self._regex is None:
            return False
        return any(
            self._regex.search(str(e.get(self.field, "")))
            for e in events
            if self.field in e
        )

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._regex is None:
            return []
        return [
            e for e in events
            if self.field in e and self._regex.search(str(e.get(self.field, "")))
        ]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "pattern", "field": self.field, "pattern": self.pattern}


class AggregateCondition(AlertCondition):
    """Fires when an aggregate function over a metric crosses a threshold.

    Supported ``agg_func`` values: ``sum``, ``avg``, ``min``, ``max``, ``count``.

    Example: ``AggregateCondition("latency", "avg", ">", 500, window=50)``
    """

    _AGG_FUNCS = {"sum", "avg", "min", "max", "count"}

    def __init__(self, metric: str, agg_func: str, op: str, value: float, window: int = 50) -> None:
        if agg_func not in self._AGG_FUNCS:
            raise ValueError(f"Unsupported agg_func: {agg_func}")
        if op not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op}")
        self.metric = metric
        self.agg_func = agg_func
        self.op = op
        self.value = value
        self.window = window

    def _aggregate(self, values: list[float]) -> float:
        if not values:
            return 0.0
        if self.agg_func == "sum":
            return sum(values)
        if self.agg_func == "avg":
            return sum(values) / len(values)
        if self.agg_func == "min":
            return min(values)
        if self.agg_func == "max":
            return max(values)
        if self.agg_func == "count":
            return float(len(values))
        return 0.0  # pragma: no cover

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        window = events[-self.window:] if len(events) > self.window else events
        values = [e[self.metric] for e in window if self.metric in e]
        agg = self._aggregate(values)
        return _OPERATORS[self.op](agg, self.value)

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        window = events[-self.window:] if len(events) > self.window else events
        return [e for e in window if self.metric in e]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "aggregate", "metric": self.metric, "agg_func": self.agg_func,
            "operator": self.op, "value": self.value, "window": self.window,
        }


class CompositeCondition(AlertCondition):
    """Combines multiple conditions with AND (``all``) or OR (``any``) logic."""

    def __init__(self, conditions: list[AlertCondition], mode: str = "all") -> None:
        if mode not in ("all", "any"):
            raise ValueError(f"mode must be 'all' or 'any', got: {mode}")
        self.conditions = list(conditions)
        self.mode = mode

    def evaluate(self, events: list[dict[str, Any]]) -> bool:
        if not self.conditions:
            return False
        fn = all if self.mode == "all" else any
        return fn(c.evaluate(events) for c in self.conditions)

    def matched_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[int] = set()
        result: list[dict[str, Any]] = []
        for c in self.conditions:
            for e in c.matched_events(events):
                eid = id(e)
                if eid not in seen:
                    seen.add(eid)
                    result.append(e)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "composite",
            "mode": self.mode,
            "conditions": [c.to_dict() for c in self.conditions],
        }


# ── Alert result ───────────────────────────────────────────────────────

@dataclass
class AlertResult:
    """Represents a triggered alert."""
    rule_name: str
    severity: AlertSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    matched_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "matched_events": self.matched_events,
        }


# ── Alert rule ─────────────────────────────────────────────────────────

class AlertRule:
    """A named, configurable alert rule with cooldown support."""

    def __init__(
        self,
        name: str,
        condition: AlertCondition,
        severity: AlertSeverity = AlertSeverity.WARNING,
        description: str = "",
        cooldown_seconds: float = 0,
        enabled: bool = True,
    ) -> None:
        self.name = name
        self.condition = condition
        self.severity = severity
        self.description = description
        self.cooldown_seconds = cooldown_seconds
        self.enabled = enabled
        self._last_fired: float = 0.0

    def check(self, events: list[dict[str, Any]]) -> AlertResult | None:
        """Evaluate the condition; return an AlertResult or None.

        Respects the cooldown window — if the rule fired recently it
        returns None even if the condition is met.
        """
        if not self.enabled:
            return None
        now = time.time()
        if self.cooldown_seconds and (now - self._last_fired) < self.cooldown_seconds:
            return None
        if self.condition.evaluate(events):
            self._last_fired = now
            return AlertResult(
                rule_name=self.name,
                severity=self.severity,
                message=self.description or f"Alert rule '{self.name}' triggered",
                timestamp=now,
                matched_events=self.condition.matched_events(events),
            )
        return None

    def reset_cooldown(self) -> None:
        """Reset the cooldown timer, allowing the rule to fire immediately."""
        self._last_fired = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the rule to a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "severity": self.severity.value,
            "condition": self.condition.to_dict(),
            "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertRule":
        """Deserialize an AlertRule from a dictionary."""
        return cls(
            name=data["name"],
            condition=AlertCondition.from_dict(data["condition"]),
            severity=AlertSeverity(data.get("severity", "warning")),
            description=data.get("description", ""),
            cooldown_seconds=data.get("cooldown_seconds", 0),
            enabled=data.get("enabled", True),
        )


# ── Engine ─────────────────────────────────────────────────────────────

class AlertRulesEngine:
    """Central engine that manages rules, evaluates events, and dispatches alerts.

    Args:
        max_events: Maximum events retained in the incremental buffer.
            Oldest events are evicted when this limit is exceeded.
            Default: 10 000.
        max_history: Maximum alert results retained in history.
            Default: 5 000.
    """

    def __init__(
        self,
        max_events: int = 10_000,
        max_history: int = 5_000,
    ) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._handlers: list[Callable[[AlertResult], Any]] = []
        self._history: list[AlertResult] = []
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._max_events = max(1, max_events)
        self._max_history = max(1, max_history)

    # ── Rule management ────────────────────────────────────────────────

    def add_rule(self, rule: AlertRule) -> None:
        """Register a rule with the engine (replaces any existing rule with the same name)."""
        with self._lock:
            self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if the rule existed."""
        with self._lock:
            if name in self._rules:
                del self._rules[name]
                return True
            return False

    def get_rule(self, name: str) -> AlertRule | None:
        """Return the rule with *name*, or ``None`` if not found."""
        with self._lock:
            return self._rules.get(name)

    def list_rules(self) -> list[AlertRule]:
        """Return a snapshot of all registered rules."""
        with self._lock:
            return list(self._rules.values())

    def enable_rule(self, name: str) -> bool:
        """Enable a rule by name. Returns False if the rule doesn't exist."""
        with self._lock:
            rule = self._rules.get(name)
            if rule is None:
                return False
            rule.enabled = True
            return True

    def disable_rule(self, name: str) -> bool:
        """Disable a rule by name. Returns False if the rule doesn't exist."""
        with self._lock:
            rule = self._rules.get(name)
            if rule is None:
                return False
            rule.enabled = False
            return True

    # ── Handlers ───────────────────────────────────────────────────────

    def add_handler(self, callback: Callable[[AlertResult], Any]) -> None:
        """Register a callback invoked whenever an alert fires."""
        self._handlers.append(callback)

    # ── Evaluation ─────────────────────────────────────────────────────

    def evaluate(self, events: list[dict[str, Any]]) -> list[AlertResult]:
        """Evaluate all enabled rules against the given events."""
        with self._lock:
            rules = list(self._rules.values())

        results: list[AlertResult] = []
        for rule in rules:
            result = rule.check(events)
            if result is not None:
                results.append(result)
                with self._lock:
                    self._history.append(result)
                    if len(self._history) > self._max_history:
                        self._history = self._history[-self._max_history:]
                for handler in self._handlers:
                    try:
                        handler(result)
                    except Exception:
                        pass
        return results

    def evaluate_incremental(self, new_events: list[dict[str, Any]]) -> list[AlertResult]:
        """Append *new_events* to the internal buffer and evaluate all rules.

        The buffer is capped at ``max_events``; oldest events are dropped
        when the limit is exceeded to prevent unbounded memory growth.
        """
        with self._lock:
            self._events.extend(new_events)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]
            all_events = list(self._events)
        return self.evaluate(all_events)

    # ── History / state ────────────────────────────────────────────────

    def get_alert_history(self) -> list[AlertResult]:
        """Return a copy of all past alert results."""
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        """Clear all stored alert history."""
        with self._lock:
            self._history.clear()

    def reset_cooldowns(self) -> None:
        """Reset cooldown timers on all rules."""
        with self._lock:
            for rule in self._rules.values():
                rule.reset_cooldown()

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize the engine configuration (all rules) to a dictionary."""
        with self._lock:
            return {
                "rules": [r.to_dict() for r in self._rules.values()],
            }

    @property
    def event_count(self) -> int:
        """Number of events in the incremental buffer."""
        with self._lock:
            return len(self._events)

    @property
    def history_count(self) -> int:
        """Number of alert results in history."""
        with self._lock:
            return len(self._history)

    def clear_events(self) -> None:
        """Clear the incremental event buffer."""
        with self._lock:
            self._events.clear()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertRulesEngine":
        """Reconstruct an engine from a serialized dictionary."""
        engine = cls()
        for rule_data in data.get("rules", []):
            engine.add_rule(AlertRule.from_dict(rule_data))
        return engine
