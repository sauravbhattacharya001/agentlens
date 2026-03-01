"""Tests for the alert rules engine (agentlens.alert_rules)."""

from __future__ import annotations

import re
import threading
import time

import pytest

from agentlens.alert_rules import (
    AlertCondition,
    AlertResult,
    AlertRule,
    AlertRulesEngine,
    AlertSeverity,
    AggregateCondition,
    CompositeCondition,
    ConsecutiveCondition,
    PatternCondition,
    RateCondition,
    ThresholdCondition,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _ev(event_type: str = "success", **kw) -> dict:
    return {"event_type": event_type, **kw}


# ══════════════════════════════════════════════════════════════════════
# ThresholdCondition
# ══════════════════════════════════════════════════════════════════════

class TestThresholdCondition:
    def test_greater_than_true(self):
        c = ThresholdCondition("cost", ">", 5.0)
        assert c.evaluate([{"cost": 3.0}, {"cost": 3.0}])  # sum=6

    def test_greater_than_false(self):
        c = ThresholdCondition("cost", ">", 10.0)
        assert not c.evaluate([{"cost": 3.0}, {"cost": 3.0}])  # sum=6

    def test_less_than(self):
        c = ThresholdCondition("cost", "<", 5.0)
        assert c.evaluate([{"cost": 1.0}, {"cost": 2.0}])

    def test_equal(self):
        c = ThresholdCondition("cost", "==", 6.0)
        assert c.evaluate([{"cost": 3.0}, {"cost": 3.0}])

    def test_not_equal(self):
        c = ThresholdCondition("cost", "!=", 5.0)
        assert c.evaluate([{"cost": 3.0}, {"cost": 3.0}])

    def test_gte(self):
        c = ThresholdCondition("cost", ">=", 6.0)
        assert c.evaluate([{"cost": 3.0}, {"cost": 3.0}])

    def test_lte(self):
        c = ThresholdCondition("cost", "<=", 6.0)
        assert c.evaluate([{"cost": 3.0}, {"cost": 3.0}])

    def test_missing_metric_defaults_zero(self):
        c = ThresholdCondition("cost", ">", 0.0)
        assert not c.evaluate([{"other": 1}])  # sum=0, not > 0

    def test_empty_events(self):
        c = ThresholdCondition("cost", ">", 0.0)
        assert not c.evaluate([])  # sum=0, not > 0

    def test_empty_events_negative_threshold(self):
        # sum of empty = 0, which IS > -1
        c = ThresholdCondition("cost", ">", -1.0)
        assert c.evaluate([])

    def test_invalid_operator(self):
        with pytest.raises(ValueError):
            ThresholdCondition("cost", "~", 5.0)

    def test_matched_events(self):
        c = ThresholdCondition("cost", ">", 0)
        evts = [{"cost": 1}, {"other": 2}]
        assert c.matched_events(evts) == [{"cost": 1}]

    def test_to_dict(self):
        c = ThresholdCondition("cost", ">", 5.0)
        d = c.to_dict()
        assert d == {"type": "threshold", "metric": "cost", "operator": ">", "value": 5.0}


# ══════════════════════════════════════════════════════════════════════
# RateCondition
# ══════════════════════════════════════════════════════════════════════

class TestRateCondition:
    def test_rate_above_threshold(self):
        events = [_ev("error")] * 15 + [_ev("success")] * 85
        c = RateCondition("error", 10.0, window_events=100)
        assert c.evaluate(events)

    def test_rate_below_threshold(self):
        events = [_ev("error")] * 5 + [_ev("success")] * 95
        c = RateCondition("error", 10.0, window_events=100)
        assert not c.evaluate(events)

    def test_rate_at_threshold_does_not_fire(self):
        events = [_ev("error")] * 10 + [_ev("success")] * 90
        c = RateCondition("error", 10.0, window_events=100)
        assert not c.evaluate(events)  # 10% is not > 10%

    def test_empty_events(self):
        c = RateCondition("error", 5.0)
        assert not c.evaluate([])

    def test_window_smaller_than_events(self):
        # only last 10 events matter
        events = [_ev("error")] * 100 + [_ev("success")] * 10
        c = RateCondition("error", 50.0, window_events=10)
        assert not c.evaluate(events)  # last 10 are all success

    def test_matched_events(self):
        events = [_ev("error"), _ev("success"), _ev("error")]
        c = RateCondition("error", 10.0)
        matched = c.matched_events(events)
        assert len(matched) == 2
        assert all(e["event_type"] == "error" for e in matched)

    def test_to_dict(self):
        c = RateCondition("error", 10.0, 50)
        d = c.to_dict()
        assert d["type"] == "rate"
        assert d["threshold_pct"] == 10.0


# ══════════════════════════════════════════════════════════════════════
# ConsecutiveCondition
# ══════════════════════════════════════════════════════════════════════

class TestConsecutiveCondition:
    def test_consecutive_true(self):
        events = [_ev("success"), _ev("error"), _ev("error"), _ev("error")]
        c = ConsecutiveCondition("error", 3)
        assert c.evaluate(events)

    def test_consecutive_false(self):
        events = [_ev("error"), _ev("error"), _ev("success")]
        c = ConsecutiveCondition("error", 3)
        assert not c.evaluate(events)

    def test_not_enough_events(self):
        events = [_ev("error"), _ev("error")]
        c = ConsecutiveCondition("error", 3)
        assert not c.evaluate(events)

    def test_empty_events(self):
        c = ConsecutiveCondition("error", 1)
        assert not c.evaluate([])

    def test_exactly_count(self):
        events = [_ev("error")] * 3
        c = ConsecutiveCondition("error", 3)
        assert c.evaluate(events)

    def test_matched_events(self):
        events = [_ev("success")] + [_ev("error")] * 3
        c = ConsecutiveCondition("error", 3)
        assert len(c.matched_events(events)) == 3

    def test_to_dict(self):
        c = ConsecutiveCondition("error", 3)
        assert c.to_dict()["type"] == "consecutive"


# ══════════════════════════════════════════════════════════════════════
# PatternCondition
# ══════════════════════════════════════════════════════════════════════

class TestPatternCondition:
    def test_pattern_match(self):
        events = [{"message": "connection timeout error"}]
        c = PatternCondition("message", r"timeout")
        assert c.evaluate(events)

    def test_pattern_no_match(self):
        events = [{"message": "all good"}]
        c = PatternCondition("message", r"timeout")
        assert not c.evaluate(events)

    def test_field_missing(self):
        events = [{"other": "value"}]
        c = PatternCondition("message", r"timeout")
        assert not c.evaluate(events)

    def test_empty_events(self):
        c = PatternCondition("message", r".*")
        assert not c.evaluate([])

    def test_invalid_regex(self):
        c = PatternCondition("message", r"[invalid")
        assert not c.evaluate([{"message": "test"}])

    def test_invalid_regex_matched_events(self):
        c = PatternCondition("message", r"[invalid")
        assert c.matched_events([{"message": "test"}]) == []

    def test_matched_events(self):
        events = [{"message": "timeout"}, {"message": "ok"}, {"message": "timeout again"}]
        c = PatternCondition("message", r"timeout")
        assert len(c.matched_events(events)) == 2

    def test_to_dict(self):
        c = PatternCondition("msg", r"\d+")
        d = c.to_dict()
        assert d["type"] == "pattern"
        assert d["pattern"] == r"\d+"


# ══════════════════════════════════════════════════════════════════════
# AggregateCondition
# ══════════════════════════════════════════════════════════════════════

class TestAggregateCondition:
    def test_avg_above(self):
        events = [{"latency": 600}, {"latency": 700}]
        c = AggregateCondition("latency", "avg", ">", 500, window=50)
        assert c.evaluate(events)

    def test_avg_below(self):
        events = [{"latency": 100}, {"latency": 200}]
        c = AggregateCondition("latency", "avg", ">", 500, window=50)
        assert not c.evaluate(events)

    def test_sum(self):
        events = [{"cost": 3}, {"cost": 4}]
        c = AggregateCondition("cost", "sum", ">=", 7, window=10)
        assert c.evaluate(events)

    def test_min(self):
        events = [{"latency": 10}, {"latency": 20}]
        c = AggregateCondition("latency", "min", "<", 15, window=10)
        assert c.evaluate(events)

    def test_max(self):
        events = [{"latency": 10}, {"latency": 20}]
        c = AggregateCondition("latency", "max", ">", 15, window=10)
        assert c.evaluate(events)

    def test_count(self):
        events = [{"latency": 10}] * 5
        c = AggregateCondition("latency", "count", ">=", 5, window=10)
        assert c.evaluate(events)

    def test_empty_events(self):
        c = AggregateCondition("latency", "avg", ">", 0, window=10)
        assert not c.evaluate([])

    def test_missing_metric(self):
        events = [{"other": 1}]
        c = AggregateCondition("latency", "avg", ">", 0, window=10)
        assert not c.evaluate(events)  # no values -> agg=0, 0 > 0 false

    def test_window_slicing(self):
        events = [{"latency": 1000}] * 5 + [{"latency": 10}] * 5
        c = AggregateCondition("latency", "avg", ">", 500, window=5)
        assert not c.evaluate(events)  # last 5 are latency=10

    def test_invalid_agg_func(self):
        with pytest.raises(ValueError):
            AggregateCondition("x", "median", ">", 0)

    def test_invalid_operator(self):
        with pytest.raises(ValueError):
            AggregateCondition("x", "avg", "~", 0)

    def test_to_dict(self):
        c = AggregateCondition("latency", "avg", ">", 500, window=50)
        d = c.to_dict()
        assert d["type"] == "aggregate"
        assert d["agg_func"] == "avg"


# ══════════════════════════════════════════════════════════════════════
# CompositeCondition
# ══════════════════════════════════════════════════════════════════════

class TestCompositeCondition:
    def test_all_mode_both_true(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 5),
            ConsecutiveCondition("error", 2),
        ], mode="all")
        events = [_ev("error", cost=4), _ev("error", cost=4)]
        assert c.evaluate(events)  # cost sum=8>5 AND 2 consecutive errors

    def test_all_mode_one_false(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 100),
            ConsecutiveCondition("error", 2),
        ], mode="all")
        events = [_ev("error", cost=4), _ev("error", cost=4)]
        assert not c.evaluate(events)

    def test_any_mode_one_true(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 100),
            ConsecutiveCondition("error", 2),
        ], mode="any")
        events = [_ev("error", cost=4), _ev("error", cost=4)]
        assert c.evaluate(events)

    def test_any_mode_none_true(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 100),
            ConsecutiveCondition("error", 5),
        ], mode="any")
        events = [_ev("error", cost=4), _ev("error", cost=4)]
        assert not c.evaluate(events)

    def test_empty_conditions(self):
        c = CompositeCondition([], mode="all")
        assert not c.evaluate([_ev()])

    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            CompositeCondition([], mode="xor")

    def test_matched_events_deduped(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 0),
            PatternCondition("message", "err"),
        ], mode="any")
        events = [{"cost": 1, "message": "err"}]
        matched = c.matched_events(events)
        assert len(matched) == 1

    def test_to_dict_roundtrip(self):
        c = CompositeCondition([
            ThresholdCondition("cost", ">", 5),
            ConsecutiveCondition("error", 3),
        ], mode="any")
        d = c.to_dict()
        c2 = AlertCondition.from_dict(d)
        assert isinstance(c2, CompositeCondition)
        assert c2.mode == "any"
        assert len(c2.conditions) == 2


# ══════════════════════════════════════════════════════════════════════
# AlertRule
# ══════════════════════════════════════════════════════════════════════

class TestAlertRule:
    def test_fires_when_condition_met(self):
        rule = AlertRule("high_cost", ThresholdCondition("cost", ">", 5))
        result = rule.check([{"cost": 10}])
        assert result is not None
        assert result.rule_name == "high_cost"

    def test_no_fire_when_condition_not_met(self):
        rule = AlertRule("high_cost", ThresholdCondition("cost", ">", 100))
        assert rule.check([{"cost": 1}]) is None

    def test_disabled_rule_does_not_fire(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 0), enabled=False)
        assert rule.check([{"cost": 10}]) is None

    def test_cooldown_prevents_rapid_fire(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 0), cooldown_seconds=10)
        r1 = rule.check([{"cost": 10}])
        assert r1 is not None
        r2 = rule.check([{"cost": 10}])
        assert r2 is None  # within cooldown

    def test_reset_cooldown(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 0), cooldown_seconds=10)
        rule.check([{"cost": 10}])
        rule.reset_cooldown()
        r2 = rule.check([{"cost": 10}])
        assert r2 is not None

    def test_severity(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 0), severity=AlertSeverity.CRITICAL)
        result = rule.check([{"cost": 10}])
        assert result.severity == AlertSeverity.CRITICAL

    def test_description_in_message(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 0), description="Cost too high!")
        result = rule.check([{"cost": 10}])
        assert result.message == "Cost too high!"

    def test_default_message(self):
        rule = AlertRule("my_rule", ThresholdCondition("cost", ">", 0))
        result = rule.check([{"cost": 10}])
        assert "my_rule" in result.message

    def test_to_dict(self):
        rule = AlertRule("r", ThresholdCondition("cost", ">", 5), severity=AlertSeverity.INFO)
        d = rule.to_dict()
        assert d["name"] == "r"
        assert d["severity"] == "info"

    def test_from_dict(self):
        d = {
            "name": "test",
            "condition": {"type": "threshold", "metric": "cost", "operator": ">", "value": 5},
            "severity": "critical",
            "cooldown_seconds": 30,
            "enabled": True,
        }
        rule = AlertRule.from_dict(d)
        assert rule.name == "test"
        assert rule.severity == AlertSeverity.CRITICAL
        assert rule.cooldown_seconds == 30

    def test_roundtrip(self):
        rule = AlertRule("r", ConsecutiveCondition("error", 3), severity=AlertSeverity.WARNING, cooldown_seconds=60)
        d = rule.to_dict()
        rule2 = AlertRule.from_dict(d)
        assert rule2.name == rule.name
        assert rule2.cooldown_seconds == 60


# ══════════════════════════════════════════════════════════════════════
# AlertResult
# ══════════════════════════════════════════════════════════════════════

class TestAlertResult:
    def test_to_dict(self):
        r = AlertResult(rule_name="r", severity=AlertSeverity.INFO, message="hi", timestamp=123.0)
        d = r.to_dict()
        assert d["rule_name"] == "r"
        assert d["severity"] == "info"
        assert d["timestamp"] == 123.0


# ══════════════════════════════════════════════════════════════════════
# AlertRulesEngine
# ══════════════════════════════════════════════════════════════════════

class TestAlertRulesEngine:
    def test_add_and_list_rules(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        engine.add_rule(AlertRule("b", ThresholdCondition("cost", ">", 0)))
        assert len(engine.list_rules()) == 2

    def test_remove_rule(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        assert engine.remove_rule("a")
        assert len(engine.list_rules()) == 0

    def test_remove_nonexistent(self):
        engine = AlertRulesEngine()
        assert not engine.remove_rule("nope")

    def test_get_rule(self):
        engine = AlertRulesEngine()
        rule = AlertRule("a", ThresholdCondition("cost", ">", 0))
        engine.add_rule(rule)
        assert engine.get_rule("a") is rule
        assert engine.get_rule("nope") is None

    def test_enable_disable(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        assert engine.disable_rule("a")
        rule = engine.get_rule("a")
        assert not rule.enabled
        assert engine.enable_rule("a")
        assert rule.enabled

    def test_enable_disable_nonexistent(self):
        engine = AlertRulesEngine()
        assert not engine.enable_rule("nope")
        assert not engine.disable_rule("nope")

    def test_evaluate(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("high_cost", ThresholdCondition("cost", ">", 5)))
        results = engine.evaluate([{"cost": 10}])
        assert len(results) == 1
        assert results[0].rule_name == "high_cost"

    def test_evaluate_no_fire(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("high_cost", ThresholdCondition("cost", ">", 100)))
        results = engine.evaluate([{"cost": 1}])
        assert len(results) == 0

    def test_evaluate_disabled_rule_skipped(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0), enabled=False))
        assert engine.evaluate([{"cost": 10}]) == []

    def test_evaluate_incremental(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("consecutive_errors", ConsecutiveCondition("error", 3)))
        engine.evaluate_incremental([_ev("error")])
        engine.evaluate_incremental([_ev("error")])
        results = engine.evaluate_incremental([_ev("error")])
        assert len(results) == 1

    def test_evaluate_incremental_accumulates(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("cost", ThresholdCondition("cost", ">", 10)))
        engine.evaluate_incremental([{"cost": 4}])
        engine.evaluate_incremental([{"cost": 4}])
        results = engine.evaluate_incremental([{"cost": 4}])
        assert len(results) == 1  # sum=12 > 10

    def test_handler_called(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        received = []
        engine.add_handler(lambda r: received.append(r))
        engine.evaluate([{"cost": 10}])
        assert len(received) == 1

    def test_handler_exception_does_not_crash(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        engine.add_handler(lambda r: 1 / 0)
        results = engine.evaluate([{"cost": 10}])
        assert len(results) == 1  # still returns result

    def test_alert_history(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        engine.evaluate([{"cost": 10}])
        history = engine.get_alert_history()
        assert len(history) == 1

    def test_clear_history(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0)))
        engine.evaluate([{"cost": 10}])
        engine.clear_history()
        assert len(engine.get_alert_history()) == 0

    def test_reset_cooldowns(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 0), cooldown_seconds=999))
        engine.evaluate([{"cost": 10}])
        # Second eval: cooldown blocks
        assert len(engine.evaluate([{"cost": 10}])) == 0
        engine.reset_cooldowns()
        assert len(engine.evaluate([{"cost": 10}])) == 1

    def test_to_dict_from_dict(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 5), severity=AlertSeverity.CRITICAL))
        engine.add_rule(AlertRule("b", ConsecutiveCondition("error", 3)))
        d = engine.to_dict()
        engine2 = AlertRulesEngine.from_dict(d)
        assert len(engine2.list_rules()) == 2
        names = {r.name for r in engine2.list_rules()}
        assert names == {"a", "b"}

    def test_serialization_roundtrip_evaluate(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("a", ThresholdCondition("cost", ">", 5)))
        d = engine.to_dict()
        engine2 = AlertRulesEngine.from_dict(d)
        results = engine2.evaluate([{"cost": 10}])
        assert len(results) == 1

    def test_thread_safety_incremental(self):
        engine = AlertRulesEngine()
        engine.add_rule(AlertRule("cost", ThresholdCondition("cost", ">", 1000)))
        errors = []

        def feed():
            try:
                for _ in range(50):
                    engine.evaluate_incremental([{"cost": 1}])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=feed) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ══════════════════════════════════════════════════════════════════════
# AlertCondition.from_dict dispatch
# ══════════════════════════════════════════════════════════════════════

class TestConditionFromDict:
    def test_threshold(self):
        c = AlertCondition.from_dict({"type": "threshold", "metric": "x", "operator": ">", "value": 1})
        assert isinstance(c, ThresholdCondition)

    def test_rate(self):
        c = AlertCondition.from_dict({"type": "rate", "event_type": "e", "threshold_pct": 10})
        assert isinstance(c, RateCondition)

    def test_consecutive(self):
        c = AlertCondition.from_dict({"type": "consecutive", "event_type": "e", "count": 3})
        assert isinstance(c, ConsecutiveCondition)

    def test_pattern(self):
        c = AlertCondition.from_dict({"type": "pattern", "field": "f", "pattern": "p"})
        assert isinstance(c, PatternCondition)

    def test_aggregate(self):
        c = AlertCondition.from_dict({"type": "aggregate", "metric": "m", "agg_func": "avg", "operator": ">", "value": 1})
        assert isinstance(c, AggregateCondition)

    def test_unknown_type(self):
        with pytest.raises(ValueError):
            AlertCondition.from_dict({"type": "unknown"})


# ══════════════════════════════════════════════════════════════════════
# AlertSeverity enum
# ══════════════════════════════════════════════════════════════════════

class TestAlertSeverity:
    def test_values(self):
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_string_comparison(self):
        assert AlertSeverity.INFO == "info"
