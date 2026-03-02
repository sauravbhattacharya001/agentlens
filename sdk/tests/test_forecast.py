"""Tests for CostForecaster — cost prediction and budget alerts."""

import pytest
from datetime import datetime, timedelta, timezone

from agentlens.forecast import (
    CostForecaster,
    UsageRecord,
    ForecastResult,
    SpendingSummary,
    BudgetAlert,
    DailyPrediction,
)


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


def _make_records(days=10, base_cost=1.0, tokens_per_day=1000, model="gpt-4o"):
    """Generate N days of usage records starting 2026-02-20."""
    records = []
    start = _utc(2026, 2, 20)
    for i in range(days):
        records.append(UsageRecord(
            timestamp=start + timedelta(days=i),
            tokens_in=tokens_per_day // 2,
            tokens_out=tokens_per_day // 2,
            cost_usd=base_cost,
            model=model,
            session_id=f"session-{i}",
        ))
    return records


class TestUsageRecord:
    def test_defaults(self):
        r = UsageRecord(timestamp=_utc(2026, 3, 1))
        assert r.tokens_in == 0
        assert r.tokens_out == 0
        assert r.cost_usd == 0.0
        assert r.model is None

    def test_all_fields(self):
        r = UsageRecord(
            timestamp=_utc(2026, 3, 1),
            tokens_in=500, tokens_out=200,
            cost_usd=0.05, model="gpt-4o",
            session_id="s1", agent_name="test",
        )
        assert r.tokens_in == 500
        assert r.cost_usd == 0.05


class TestCostForecasterBasic:
    def test_empty_forecaster(self):
        f = CostForecaster()
        assert f.record_count == 0

    def test_add_record(self):
        f = CostForecaster()
        f.add_record(UsageRecord(timestamp=_utc(2026, 3, 1), cost_usd=1.0))
        assert f.record_count == 1

    def test_add_records_bulk(self):
        f = CostForecaster()
        f.add_records(_make_records(5))
        assert f.record_count == 5

    def test_negative_cost_rejected(self):
        f = CostForecaster()
        with pytest.raises(ValueError, match="negative"):
            f.add_record(UsageRecord(timestamp=_utc(2026, 3, 1), cost_usd=-1.0))

    def test_negative_tokens_rejected(self):
        f = CostForecaster()
        with pytest.raises(ValueError, match="negative"):
            f.add_record(UsageRecord(timestamp=_utc(2026, 3, 1), tokens_in=-5))

    def test_clear(self):
        f = CostForecaster()
        f.add_records(_make_records(3))
        f.clear()
        assert f.record_count == 0


class TestLinearRegression:
    def test_constant_values(self):
        slope, intercept = CostForecaster._linear_regression([5.0, 5.0, 5.0])
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(5.0)

    def test_perfect_linear(self):
        # y = 2x + 1 → values at x=0,1,2,3: 1,3,5,7
        slope, intercept = CostForecaster._linear_regression([1.0, 3.0, 5.0, 7.0])
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(1.0)

    def test_single_value(self):
        slope, intercept = CostForecaster._linear_regression([42.0])
        assert slope == 0.0
        assert intercept == 42.0

    def test_empty(self):
        slope, intercept = CostForecaster._linear_regression([])
        assert slope == 0.0
        assert intercept == 0.0

    def test_decreasing(self):
        slope, _ = CostForecaster._linear_regression([10.0, 8.0, 6.0, 4.0, 2.0])
        assert slope < 0


class TestEMA:
    def test_single_value(self):
        assert CostForecaster._ema([5.0]) == 5.0

    def test_empty(self):
        assert CostForecaster._ema([]) == 0.0

    def test_increasing_weights_recent(self):
        # With high alpha, EMA should be closer to last value
        result = CostForecaster._ema([1.0, 1.0, 1.0, 10.0], alpha=0.9)
        assert result > 5.0

    def test_low_alpha_smooths(self):
        # With low alpha, EMA should be closer to first value
        result = CostForecaster._ema([1.0, 1.0, 1.0, 10.0], alpha=0.1)
        assert result < 5.0


class TestForecastDaily:
    def test_no_records_raises(self):
        f = CostForecaster()
        with pytest.raises(ValueError, match="No usage records"):
            f.forecast_daily(days=7)

    def test_invalid_days(self):
        f = CostForecaster()
        f.add_records(_make_records(5))
        with pytest.raises(ValueError, match="days must be"):
            f.forecast_daily(days=0)
        with pytest.raises(ValueError, match="days must be"):
            f.forecast_daily(days=91)

    def test_forecast_returns_correct_count(self):
        f = CostForecaster()
        f.add_records(_make_records(10))
        result = f.forecast_daily(days=7)
        assert len(result.daily_predictions) == 7

    def test_forecast_linear_method(self):
        f = CostForecaster()
        f.add_records(_make_records(10, base_cost=5.0))
        result = f.forecast_daily(days=3, method="linear")
        assert result.method == "linear"
        assert result.data_points_used == 10
        assert result.total_predicted_cost > 0
        assert all(p.method == "linear" for p in result.daily_predictions)

    def test_forecast_ema_method(self):
        f = CostForecaster()
        f.add_records(_make_records(5, base_cost=3.0))
        result = f.forecast_daily(days=3, method="ema")
        assert result.method == "ema"
        for p in result.daily_predictions:
            assert p.predicted_cost > 0
            assert p.method == "ema"

    def test_forecast_average_method(self):
        f = CostForecaster()
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=10.0, tokens_in=100, tokens_out=100
        ))
        result = f.forecast_daily(days=5, method="average")
        assert result.method == "average"
        for p in result.daily_predictions:
            assert p.predicted_cost == pytest.approx(10.0)

    def test_auto_selects_linear_with_enough_data(self):
        f = CostForecaster()
        f.add_records(_make_records(10))
        result = f.forecast_daily(days=3, method="auto")
        assert result.method == "linear"

    def test_auto_selects_ema_with_moderate_data(self):
        f = CostForecaster()
        f.add_records(_make_records(3))
        result = f.forecast_daily(days=3, method="auto")
        assert result.method == "ema"

    def test_auto_selects_average_with_single_day(self):
        f = CostForecaster()
        f.add_record(UsageRecord(timestamp=_utc(2026, 3, 1), cost_usd=5.0))
        result = f.forecast_daily(days=3, method="auto")
        assert result.method == "average"

    def test_predictions_have_confidence_bounds(self):
        f = CostForecaster()
        f.add_records(_make_records(10))
        result = f.forecast_daily(days=5)
        for p in result.daily_predictions:
            assert p.confidence_low <= p.predicted_cost
            assert p.confidence_high >= p.predicted_cost

    def test_prediction_dates_are_sequential(self):
        f = CostForecaster()
        records = _make_records(5)
        f.add_records(records)
        result = f.forecast_daily(days=3)
        dates = [p.date for p in result.daily_predictions]
        for i in range(len(dates) - 1):
            d1 = datetime.strptime(dates[i], "%Y-%m-%d")
            d2 = datetime.strptime(dates[i+1], "%Y-%m-%d")
            assert (d2 - d1).days == 1

    def test_to_dict(self):
        f = CostForecaster()
        f.add_records(_make_records(5))
        result = f.forecast_daily(days=2)
        d = result.to_dict()
        assert "daily_predictions" in d
        assert "total_predicted_cost" in d
        assert "method" in d
        assert len(d["daily_predictions"]) == 2

    def test_increasing_trend_projects_higher(self):
        f = CostForecaster()
        # Cost increases each day: 1, 2, 3, 4, 5
        start = _utc(2026, 2, 20)
        for i in range(5):
            f.add_record(UsageRecord(
                timestamp=start + timedelta(days=i),
                cost_usd=float(i + 1),
                tokens_in=100, tokens_out=100,
            ))
        result = f.forecast_daily(days=3, method="linear")
        # Each prediction should be higher than the last known (5.0)
        assert result.daily_predictions[0].predicted_cost > 5.0

    def test_zero_cost_records(self):
        f = CostForecaster()
        f.add_records([
            UsageRecord(timestamp=_utc(2026, 3, 1), cost_usd=0.0),
            UsageRecord(timestamp=_utc(2026, 3, 2), cost_usd=0.0),
        ])
        result = f.forecast_daily(days=3)
        for p in result.daily_predictions:
            assert p.predicted_cost >= 0.0


class TestSpendingSummary:
    def test_no_records_raises(self):
        f = CostForecaster()
        with pytest.raises(ValueError, match="No usage records"):
            f.spending_summary()

    def test_basic_summary(self):
        f = CostForecaster()
        f.add_records(_make_records(10, base_cost=2.0, tokens_per_day=1000))
        s = f.spending_summary()
        assert s.total_cost == pytest.approx(20.0)
        assert s.total_tokens == 10000
        assert s.record_count == 10
        assert s.days_tracked == 10
        assert s.daily_average_cost == pytest.approx(2.0)
        assert s.weekly_projection == pytest.approx(14.0)
        assert s.monthly_projection == pytest.approx(60.0)

    def test_model_breakdown(self):
        f = CostForecaster()
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=1.0,
            tokens_in=100, tokens_out=50, model="gpt-4o"
        ))
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=3.0,
            tokens_in=100, tokens_out=50, model="claude-3-opus"
        ))
        s = f.spending_summary()
        assert "gpt-4o" in s.model_breakdown
        assert "claude-3-opus" in s.model_breakdown
        assert s.model_breakdown["gpt-4o"]["cost"] == pytest.approx(1.0)
        assert s.model_breakdown["claude-3-opus"]["count"] == 1

    def test_busiest_day(self):
        f = CostForecaster()
        f.add_record(UsageRecord(timestamp=_utc(2026, 3, 1), cost_usd=1.0))
        f.add_record(UsageRecord(timestamp=_utc(2026, 3, 2), cost_usd=10.0))
        f.add_record(UsageRecord(timestamp=_utc(2026, 3, 3), cost_usd=2.0))
        s = f.spending_summary()
        assert s.busiest_day == "2026-03-02"
        assert s.busiest_day_cost == pytest.approx(10.0)

    def test_cost_per_1k_tokens(self):
        f = CostForecaster()
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=10.0,
            tokens_in=5000, tokens_out=5000
        ))
        s = f.spending_summary()
        # $10 / 10000 tokens * 1000 = $1/1k tokens
        assert s.cost_per_1k_tokens == pytest.approx(1.0)

    def test_trend_increasing(self):
        f = CostForecaster()
        start = _utc(2026, 2, 20)
        for i in range(5):
            f.add_record(UsageRecord(
                timestamp=start + timedelta(days=i),
                cost_usd=1.0 + i * 2.0,  # 1, 3, 5, 7, 9
            ))
        s = f.spending_summary()
        assert s.trend == "increasing"
        assert s.trend_pct_change > 0

    def test_trend_decreasing(self):
        f = CostForecaster()
        start = _utc(2026, 2, 20)
        for i in range(5):
            f.add_record(UsageRecord(
                timestamp=start + timedelta(days=i),
                cost_usd=10.0 - i * 2.0,  # 10, 8, 6, 4, 2
            ))
        s = f.spending_summary()
        assert s.trend == "decreasing"
        assert s.trend_pct_change < 0

    def test_trend_stable(self):
        f = CostForecaster()
        f.add_records(_make_records(5, base_cost=5.0))
        s = f.spending_summary()
        assert s.trend == "stable"

    def test_trend_insufficient_data(self):
        f = CostForecaster()
        f.add_records(_make_records(2, base_cost=5.0))
        s = f.spending_summary()
        assert s.trend == "insufficient_data"

    def test_to_dict(self):
        f = CostForecaster()
        f.add_records(_make_records(5))
        s = f.spending_summary()
        d = s.to_dict()
        assert "total_cost" in d
        assert "model_breakdown" in d
        assert "trend" in d
        assert "monthly_projection" in d


class TestBudgetAlert:
    def test_no_records_raises(self):
        f = CostForecaster()
        with pytest.raises(ValueError, match="No usage records"):
            f.check_budget(monthly_budget=100.0)

    def test_invalid_budget(self):
        f = CostForecaster()
        f.add_records(_make_records(3))
        with pytest.raises(ValueError, match="positive"):
            f.check_budget(monthly_budget=0)

    def test_safe_budget(self):
        f = CostForecaster()
        # $1/day * 30 days = $30 projected, budget = $100
        f.add_records(_make_records(10, base_cost=1.0))
        alert = f.check_budget(monthly_budget=100.0)
        assert alert.severity == "safe"
        assert alert.overshoot_pct == 0.0

    def test_warning_budget(self):
        f = CostForecaster()
        # $3/day * 30 = $90 projected, budget = $100
        f.add_records(_make_records(10, base_cost=3.0))
        alert = f.check_budget(monthly_budget=100.0)
        assert alert.severity == "warning"

    def test_critical_budget(self):
        f = CostForecaster()
        # $5/day * 30 = $150 projected, budget = $100
        f.add_records(_make_records(10, base_cost=5.0))
        alert = f.check_budget(monthly_budget=100.0)
        assert alert.severity == "critical"
        assert alert.overshoot_pct > 0
        assert alert.days_until_exceeded is not None

    def test_already_exceeded(self):
        f = CostForecaster()
        # $20/day * 10 days = $200 already spent, budget = $100
        f.add_records(_make_records(10, base_cost=20.0))
        alert = f.check_budget(monthly_budget=100.0)
        assert alert.severity == "critical"
        assert alert.days_until_exceeded == 0

    def test_alert_message_not_empty(self):
        f = CostForecaster()
        f.add_records(_make_records(5, base_cost=2.0))
        alert = f.check_budget(monthly_budget=100.0)
        assert len(alert.message) > 10

    def test_custom_period(self):
        f = CostForecaster()
        f.add_records(_make_records(5, base_cost=1.0))
        alert = f.check_budget(monthly_budget=10.0, days_in_period=7)
        # $1/day * 7 = $7, budget $10
        assert alert.severity == "safe"


class TestMultipleRecordsSameDay:
    def test_aggregation(self):
        f = CostForecaster()
        ts = _utc(2026, 3, 1)
        f.add_record(UsageRecord(timestamp=ts, cost_usd=1.0, tokens_in=100, tokens_out=50))
        f.add_record(UsageRecord(timestamp=ts, cost_usd=2.0, tokens_in=200, tokens_out=100))
        s = f.spending_summary()
        assert s.days_tracked == 1
        assert s.total_cost == pytest.approx(3.0)
        assert s.daily_average_cost == pytest.approx(3.0)

    def test_forecast_with_same_day_records(self):
        f = CostForecaster()
        ts = _utc(2026, 3, 1)
        for i in range(5):
            f.add_record(UsageRecord(timestamp=ts, cost_usd=1.0))
        result = f.forecast_daily(days=3)
        assert result.data_points_used == 1  # all on same day
        for p in result.daily_predictions:
            assert p.predicted_cost == pytest.approx(5.0)  # $5 total that day


class TestModelMix:
    def test_multiple_models_tracked(self):
        f = CostForecaster()
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=0.5, model="gpt-4o",
            tokens_in=1000, tokens_out=500,
        ))
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=5.0, model="claude-3-opus",
            tokens_in=1000, tokens_out=500,
        ))
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 2), cost_usd=0.01, model="gpt-4o-mini",
            tokens_in=500, tokens_out=200,
        ))
        s = f.spending_summary()
        assert len(s.model_breakdown) == 3
        assert s.model_breakdown["claude-3-opus"]["cost"] == pytest.approx(5.0)

    def test_unknown_model(self):
        f = CostForecaster()
        f.add_record(UsageRecord(
            timestamp=_utc(2026, 3, 1), cost_usd=1.0, model=None
        ))
        s = f.spending_summary()
        assert "unknown" in s.model_breakdown
