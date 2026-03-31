"""Cost Forecasting for AI agent usage.

Predicts future costs based on historical usage patterns using
linear regression and exponential smoothing. Useful for budget
planning, alerts before overspend, and capacity forecasting.

Example::

    from agentlens.forecast import CostForecaster, UsageRecord

    forecaster = CostForecaster()

    # Feed historical data
    forecaster.add_record(UsageRecord(
        timestamp=datetime(2026, 3, 1, 10, 0),
        tokens_in=5000, tokens_out=2000,
        cost_usd=0.035, model="gpt-4o"))

    # ... add more records ...

    # Forecast next 7 days
    forecast = forecaster.forecast_daily(days=7)
    print(forecast.total_predicted_cost)
    print(forecast.daily_predictions)

    # Get a spending summary
    summary = forecaster.spending_summary()
    print(summary.daily_average)
    print(summary.monthly_projection)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class UsageRecord:
    """A single usage data point for forecasting."""
    timestamp: datetime
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str | None = None
    session_id: str | None = None
    agent_name: str | None = None


@dataclass
class DailyPrediction:
    """Predicted cost for a single day."""
    date: str  # ISO date
    predicted_cost: float
    predicted_tokens: int
    confidence_low: float  # lower bound of prediction interval
    confidence_high: float  # upper bound of prediction interval
    method: str  # "linear", "ema", "average"


@dataclass
class ForecastResult:
    """Complete forecast output."""
    daily_predictions: list[DailyPrediction]
    total_predicted_cost: float
    total_predicted_tokens: int
    method: str
    data_points_used: int
    forecast_generated_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_predictions": [
                {
                    "date": p.date,
                    "predicted_cost": round(p.predicted_cost, 6),
                    "predicted_tokens": p.predicted_tokens,
                    "confidence_low": round(p.confidence_low, 6),
                    "confidence_high": round(p.confidence_high, 6),
                    "method": p.method,
                }
                for p in self.daily_predictions
            ],
            "total_predicted_cost": round(self.total_predicted_cost, 6),
            "total_predicted_tokens": self.total_predicted_tokens,
            "method": self.method,
            "data_points_used": self.data_points_used,
            "forecast_generated_at": self.forecast_generated_at.isoformat(),
        }


@dataclass
class SpendingSummary:
    """Aggregated spending statistics."""
    total_cost: float
    total_tokens: int
    total_tokens_in: int
    total_tokens_out: int
    record_count: int
    days_tracked: int
    daily_average_cost: float
    daily_average_tokens: int
    weekly_projection: float
    monthly_projection: float
    cost_per_1k_tokens: float
    busiest_day: str | None
    busiest_day_cost: float
    model_breakdown: dict[str, dict[str, Any]]
    trend: str  # "increasing", "decreasing", "stable", "insufficient_data"
    trend_pct_change: float  # percentage change per day

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost": round(self.total_cost, 6),
            "total_tokens": self.total_tokens,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "record_count": self.record_count,
            "days_tracked": self.days_tracked,
            "daily_average_cost": round(self.daily_average_cost, 6),
            "daily_average_tokens": self.daily_average_tokens,
            "weekly_projection": round(self.weekly_projection, 4),
            "monthly_projection": round(self.monthly_projection, 4),
            "cost_per_1k_tokens": round(self.cost_per_1k_tokens, 6),
            "busiest_day": self.busiest_day,
            "busiest_day_cost": round(self.busiest_day_cost, 6),
            "model_breakdown": {
                m: {k: round(v, 6) if isinstance(v, float) else v
                     for k, v in info.items()}
                for m, info in self.model_breakdown.items()
            },
            "trend": self.trend,
            "trend_pct_change": round(self.trend_pct_change, 4),
        }


@dataclass
class BudgetAlert:
    """Alert when forecasted spending is likely to exceed a budget."""
    budget_usd: float
    days_until_exceeded: int | None  # None if not expected to exceed
    projected_spend_at_limit: float
    overshoot_pct: float  # how much over budget (0 if under)
    severity: str  # "safe", "warning", "critical"
    message: str


class CostForecaster:
    """Forecasts future AI costs from historical usage data.

    Supports multiple forecasting methods:
    - **Linear regression**: fits a trend line to daily cost data
    - **Exponential moving average (EMA)**: weights recent data more heavily
    - **Simple average**: fallback when insufficient data for trend analysis

    Usage::

        forecaster = CostForecaster()
        forecaster.add_records(historical_records)
        forecast = forecaster.forecast_daily(days=14)
        summary = forecaster.spending_summary()
        alert = forecaster.check_budget(monthly_budget=100.0)
    """

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._daily_cache: dict[str, dict[str, Any]] | None = None

    @property
    def record_count(self) -> int:
        return len(self._records)

    def _invalidate_cache(self) -> None:
        """Mark the daily aggregates cache as stale."""
        self._daily_cache = None

    def add_record(self, record: UsageRecord) -> None:
        """Add a single usage record."""
        if record.cost_usd < 0:
            raise ValueError("cost_usd cannot be negative")
        if record.tokens_in < 0 or record.tokens_out < 0:
            raise ValueError("token counts cannot be negative")
        self._records.append(record)
        self._invalidate_cache()

    def add_records(self, records: list[UsageRecord]) -> None:
        """Add multiple usage records."""
        for r in records:
            if r.cost_usd < 0:
                raise ValueError("cost_usd cannot be negative")
            if r.tokens_in < 0 or r.tokens_out < 0:
                raise ValueError("token counts cannot be negative")
            self._records.append(r)
        if records:
            self._invalidate_cache()

    def clear(self) -> None:
        """Remove all records."""
        self._records.clear()
        self._invalidate_cache()

    # ── Aggregation helpers ──────────────────────────────────

    def _daily_aggregates(self) -> dict[str, dict[str, Any]]:
        """Group records by date and aggregate cost/tokens.

        Results are cached and only recomputed when records change.
        """
        if self._daily_cache is not None:
            return self._daily_cache
        daily: dict[str, dict[str, Any]] = {}
        for r in self._records:
            key = r.timestamp.strftime("%Y-%m-%d")
            if key not in daily:
                daily[key] = {
                    "cost": 0.0, "tokens": 0,
                    "tokens_in": 0, "tokens_out": 0, "count": 0,
                }
            daily[key]["cost"] += r.cost_usd
            daily[key]["tokens"] += r.tokens_in + r.tokens_out
            daily[key]["tokens_in"] += r.tokens_in
            daily[key]["tokens_out"] += r.tokens_out
            daily[key]["count"] += 1
        self._daily_cache = daily
        return daily

    def _sorted_daily_costs(self) -> list[tuple[str, float]]:
        """Return (date_str, total_cost) sorted chronologically."""
        agg = self._daily_aggregates()
        return sorted(
            [(k, v["cost"]) for k, v in agg.items()],
            key=lambda x: x[0],
        )

    def _sorted_daily_tokens(self) -> list[tuple[str, int]]:
        """Return (date_str, total_tokens) sorted chronologically."""
        agg = self._daily_aggregates()
        return sorted(
            [(k, v["tokens"]) for k, v in agg.items()],
            key=lambda x: x[0],
        )

    # ── Linear regression ────────────────────────────────────

    @staticmethod
    def _linear_regression(values: list[float]) -> tuple[float, float]:
        """Simple OLS linear regression: y = slope * x + intercept.

        Args:
            values: y-values indexed 0, 1, 2, ...

        Returns:
            (slope, intercept) tuple.
        """
        n = len(values)
        if n == 0:
            return 0.0, 0.0
        if n == 1:
            return 0.0, values[0]

        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n

        numerator = 0.0
        denominator = 0.0
        for i, y in enumerate(values):
            dx = i - x_mean
            numerator += dx * (y - y_mean)
            denominator += dx * dx

        if denominator == 0:
            return 0.0, y_mean

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        return slope, intercept

    @staticmethod
    def _prediction_interval(
        values: list[float], slope: float, intercept: float,
        future_x: int, confidence: float = 0.9
    ) -> tuple[float, float]:
        """Approximate prediction interval using residual std error.

        Returns (low, high) bounds.
        """
        n = len(values)
        if n < 3:
            # Not enough data for meaningful intervals — use ±50%
            predicted = max(0.0, slope * future_x + intercept)
            return predicted * 0.5, predicted * 1.5

        # Residual standard error
        residuals = [y - (slope * i + intercept) for i, y in enumerate(values)]
        ss_res = sum(r * r for r in residuals)
        se = math.sqrt(ss_res / (n - 2))

        # Simple approximation: z * se (using normal approx for t-dist)
        z = 1.645 if confidence >= 0.9 else 1.28  # 90% or 80%
        predicted = slope * future_x + intercept
        margin = z * se * math.sqrt(1 + 1 / n)

        return max(0.0, predicted - margin), predicted + margin

    # ── Exponential moving average ───────────────────────────

    @staticmethod
    def _ema(values: list[float], alpha: float = 0.3) -> float:
        """Exponential moving average of a series.

        Higher alpha = more weight on recent values.
        """
        if not values:
            return 0.0
        ema_val = values[0]
        for v in values[1:]:
            ema_val = alpha * v + (1 - alpha) * ema_val
        return ema_val

    # ── Forecasting ──────────────────────────────────────────

    def forecast_daily(self, days: int = 7, method: str = "auto") -> ForecastResult:
        """Forecast daily costs for the next N days.

        Args:
            days: Number of future days to predict (1-90).
            method: "linear", "ema", "average", or "auto" (picks best).

        Returns:
            ForecastResult with daily predictions.

        Raises:
            ValueError: If no records exist or days is invalid.
        """
        if days < 1 or days > 90:
            raise ValueError("days must be between 1 and 90")
        if not self._records:
            raise ValueError("No usage records — add data before forecasting")

        daily_costs = self._sorted_daily_costs()
        cost_values = [c for _, c in daily_costs]
        daily_tokens = self._sorted_daily_tokens()
        token_values = [t for _, t in daily_tokens]
        n = len(cost_values)

        # Auto-select method
        if method == "auto":
            if n >= 5:
                method = "linear"
            elif n >= 2:
                method = "ema"
            else:
                method = "average"

        # Determine the start date for predictions
        last_date_str = daily_costs[-1][0]
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )

        predictions: list[DailyPrediction] = []

        if method == "linear":
            c_slope, c_intercept = self._linear_regression(cost_values)
            t_slope, t_intercept = self._linear_regression(
                [float(t) for t in token_values]
            )

            for d in range(1, days + 1):
                future_x = n - 1 + d
                pred_cost = max(0.0, c_slope * future_x + c_intercept)
                pred_tokens = max(0, int(t_slope * future_x + t_intercept))
                low, high = self._prediction_interval(
                    cost_values, c_slope, c_intercept, future_x
                )
                pred_date = last_date + timedelta(days=d)
                predictions.append(DailyPrediction(
                    date=pred_date.strftime("%Y-%m-%d"),
                    predicted_cost=pred_cost,
                    predicted_tokens=pred_tokens,
                    confidence_low=low,
                    confidence_high=high,
                    method="linear",
                ))

        elif method == "ema":
            ema_cost = self._ema(cost_values)
            ema_tokens = self._ema([float(t) for t in token_values])

            # Confidence based on variance
            if n >= 2:
                std = statistics.stdev(cost_values)
            else:
                std = ema_cost * 0.5

            for d in range(1, days + 1):
                pred_date = last_date + timedelta(days=d)
                # EMA is constant for all future days (no trend extrapolation)
                predictions.append(DailyPrediction(
                    date=pred_date.strftime("%Y-%m-%d"),
                    predicted_cost=max(0.0, ema_cost),
                    predicted_tokens=max(0, int(ema_tokens)),
                    confidence_low=max(0.0, ema_cost - 1.5 * std),
                    confidence_high=ema_cost + 1.5 * std,
                    method="ema",
                ))

        else:  # average
            avg_cost = sum(cost_values) / n if n else 0
            avg_tokens = sum(token_values) // n if n else 0

            if n >= 2:
                std = statistics.stdev(cost_values)
            else:
                std = avg_cost * 0.5

            for d in range(1, days + 1):
                pred_date = last_date + timedelta(days=d)
                predictions.append(DailyPrediction(
                    date=pred_date.strftime("%Y-%m-%d"),
                    predicted_cost=max(0.0, avg_cost),
                    predicted_tokens=max(0, avg_tokens),
                    confidence_low=max(0.0, avg_cost - 1.5 * std),
                    confidence_high=avg_cost + 1.5 * std,
                    method="average",
                ))

        return ForecastResult(
            daily_predictions=predictions,
            total_predicted_cost=sum(p.predicted_cost for p in predictions),
            total_predicted_tokens=sum(p.predicted_tokens for p in predictions),
            method=method,
            data_points_used=n,
        )

    # ── Spending summary ─────────────────────────────────────

    def spending_summary(self) -> SpendingSummary:
        """Generate an aggregated spending summary.

        Returns:
            SpendingSummary with totals, averages, projections, and trend.

        Raises:
            ValueError: If no records exist.
        """
        if not self._records:
            raise ValueError("No usage records — add data first")

        daily = self._daily_aggregates()
        sorted_days = sorted(daily.items(), key=lambda x: x[0])
        cost_values = [v["cost"] for _, v in sorted_days]
        n_days = len(sorted_days)

        # Derive totals from pre-aggregated daily data to avoid re-scanning records
        total_cost = sum(v["cost"] for v in daily.values())
        total_in = sum(v["tokens_in"] for v in daily.values())
        total_out = sum(v["tokens_out"] for v in daily.values())
        total_tokens = total_in + total_out

        daily_avg_cost = total_cost / n_days if n_days else 0.0
        daily_avg_tokens = total_tokens // n_days if n_days else 0

        # Cost per 1K tokens
        cpt = (total_cost / total_tokens * 1000) if total_tokens > 0 else 0.0

        # Busiest day
        busiest = max(sorted_days, key=lambda x: x[1]["cost"])

        # Model breakdown — must scan records for per-model granularity
        model_stats: dict[str, dict[str, Any]] = {}
        for r in self._records:
            m = r.model or "unknown"
            if m not in model_stats:
                model_stats[m] = {
                    "cost": 0.0, "tokens_in": 0, "tokens_out": 0, "count": 0,
                }
            model_stats[m]["cost"] += r.cost_usd
            model_stats[m]["tokens_in"] += r.tokens_in
            model_stats[m]["tokens_out"] += r.tokens_out
            model_stats[m]["count"] += 1

        # Trend detection
        trend, trend_pct = self._detect_trend(cost_values)

        return SpendingSummary(
            total_cost=total_cost,
            total_tokens=total_tokens,
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            record_count=len(self._records),
            days_tracked=n_days,
            daily_average_cost=daily_avg_cost,
            daily_average_tokens=daily_avg_tokens,
            weekly_projection=daily_avg_cost * 7,
            monthly_projection=daily_avg_cost * 30,
            cost_per_1k_tokens=cpt,
            busiest_day=busiest[0],
            busiest_day_cost=busiest[1]["cost"],
            model_breakdown=model_stats,
            trend=trend,
            trend_pct_change=trend_pct,
        )

    def _detect_trend(self, daily_costs: list[float]) -> tuple[str, float]:
        """Detect cost trend from daily aggregates.

        Returns:
            (trend_label, pct_change_per_day)
        """
        if len(daily_costs) < 3:
            return "insufficient_data", 0.0

        slope, intercept = self._linear_regression(daily_costs)
        avg = sum(daily_costs) / len(daily_costs) if daily_costs else 1.0
        if avg == 0:
            return "stable", 0.0

        pct_per_day = (slope / avg) * 100

        if pct_per_day > 5:
            return "increasing", pct_per_day
        elif pct_per_day < -5:
            return "decreasing", pct_per_day
        else:
            return "stable", pct_per_day

    # ── Budget alerts ────────────────────────────────────────

    def check_budget(
        self, monthly_budget: float, days_in_period: int = 30
    ) -> BudgetAlert:
        """Check if current spending pace will exceed a monthly budget.

        Args:
            monthly_budget: Budget limit in USD.
            days_in_period: Days in the budget period (default 30).

        Returns:
            BudgetAlert with severity and projections.

        Raises:
            ValueError: If budget is not positive or no records exist.
        """
        if monthly_budget <= 0:
            raise ValueError("monthly_budget must be positive")
        if not self._records:
            raise ValueError("No usage records — add data first")

        daily = self._daily_aggregates()
        sorted_days = sorted(daily.items(), key=lambda x: x[0])
        cost_values = [v["cost"] for _, v in sorted_days]
        n_days = len(cost_values)

        total_spent = sum(cost_values)
        daily_avg = total_spent / n_days if n_days else 0

        # Project total spend for the period
        projected = daily_avg * days_in_period

        # Days until budget exceeded (from now)
        if daily_avg > 0:
            remaining_budget = monthly_budget - total_spent
            days_until = int(remaining_budget / daily_avg) if remaining_budget > 0 else 0
            if remaining_budget <= 0:
                days_until = 0
        else:
            days_until = None  # Not going to exceed

        overshoot = max(0.0, (projected - monthly_budget) / monthly_budget * 100)

        # Severity
        if projected <= monthly_budget * 0.8:
            severity = "safe"
            msg = (
                f"On track: projected ${projected:.2f} of "
                f"${monthly_budget:.2f} budget ({projected/monthly_budget*100:.0f}%)"
            )
        elif projected <= monthly_budget:
            severity = "warning"
            msg = (
                f"Approaching limit: projected ${projected:.2f} of "
                f"${monthly_budget:.2f} ({projected/monthly_budget*100:.0f}%). "
                f"Consider reducing usage."
            )
        else:
            severity = "critical"
            msg = (
                f"Budget overrun likely: projected ${projected:.2f} vs "
                f"${monthly_budget:.2f} limit (+{overshoot:.0f}%). "
                f"{'Already exceeded!' if days_until == 0 else f'~{days_until} days until exceeded.'}"
            )

        return BudgetAlert(
            budget_usd=monthly_budget,
            days_until_exceeded=days_until,
            projected_spend_at_limit=projected,
            overshoot_pct=overshoot,
            severity=severity,
            message=msg,
        )
