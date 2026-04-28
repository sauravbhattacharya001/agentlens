"""CLI forecast command – predict future costs and usage from historical data.

Usage:
    agentlens-cli forecast [--days N] [--metric cost|tokens|sessions]
        [--model MODEL] [--format table|json|chart]
        [--output FILE] [--endpoint URL] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta


from agentlens.cli_common import get_client, sparkline as _spark, linear_regression as _linear_regression


def _exponential_smoothing(ys: list[float], alpha: float = 0.3) -> list[float]:
    """Single exponential smoothing."""
    if not ys:
        return []
    smoothed = [ys[0]]
    for y in ys[1:]:
        smoothed.append(alpha * y + (1 - alpha) * smoothed[-1])
    return smoothed


def _aggregate_daily(sessions: list[dict], metric: str) -> list[tuple[str, float]]:
    """Aggregate sessions into daily buckets for the chosen metric."""
    daily: dict[str, float] = defaultdict(float)
    daily_count: dict[str, int] = defaultdict(int)

    for s in sessions:
        ts = s.get("created_at") or s.get("timestamp") or ""
        if not ts:
            continue
        day = ts[:10]  # YYYY-MM-DD
        daily_count[day] += 1
        if metric == "cost":
            daily[day] += float(s.get("total_cost", 0) or s.get("cost_usd", 0) or 0)
        elif metric == "tokens":
            daily[day] += int(s.get("total_tokens", 0) or 0)
        else:  # sessions
            daily[day] += 1

    if metric == "sessions":
        daily = {k: float(v) for k, v in daily_count.items()}

    if not daily:
        return []

    # Fill gaps
    sorted_days = sorted(daily.keys())
    start = datetime.strptime(sorted_days[0], "%Y-%m-%d")
    end = datetime.strptime(sorted_days[-1], "%Y-%m-%d")
    result = []
    d = start
    while d <= end:
        key = d.strftime("%Y-%m-%d")
        result.append((key, daily.get(key, 0.0)))
        d += timedelta(days=1)
    return result


def _format_value(val: float, metric: str) -> str:
    if metric == "cost":
        return f"${val:,.2f}"
    elif metric == "tokens":
        if val >= 1_000_000:
            return f"{val / 1_000_000:,.1f}M"
        elif val >= 1_000:
            return f"{val / 1_000:,.1f}K"
        return f"{val:,.0f}"
    return f"{val:,.0f}"


def _render_chart(history: list[tuple[str, float]], predictions: list[tuple[str, float]], metric: str) -> str:
    """Render an ASCII chart of history + predictions."""
    all_vals = [v for _, v in history] + [v for _, v in predictions]
    if not all_vals:
        return "(no data)"
    mx = max(all_vals) if all_vals else 1
    if mx == 0:
        mx = 1
    width = 40
    lines = []
    lines.append(f"  {'─' * (width + 2)} {metric}")
    lines.append(f"  Historical ({len(history)} days):")
    for day, val in history[-14:]:  # last 14 days
        bar_len = int(val / mx * width)
        lines.append(f"  {day[5:]} │{'█' * bar_len}{'░' * (width - bar_len)}│ {_format_value(val, metric)}")
    lines.append(f"  {'─' * (width + 2)}")
    lines.append(f"  Forecast ({len(predictions)} days):")
    for day, val in predictions:
        bar_len = int(max(val, 0) / mx * width)
        lines.append(f"  {day[5:]} │{'▓' * bar_len}{'░' * (width - bar_len)}│ {_format_value(val, metric)}")
    lines.append(f"  {'─' * (width + 2)}")
    return "\n".join(lines)


def cmd_forecast(args: argparse.Namespace) -> None:
    """Execute the forecast CLI command."""
    client, _endpoint = get_client(args)

    days = getattr(args, "days", 7) or 7
    metric = getattr(args, "metric", "cost") or "cost"
    fmt = getattr(args, "format", "table") or "table"
    model_filter = getattr(args, "model", None)
    output = getattr(args, "output", None)

    # Fetch sessions
    try:
        resp = client.get("/sessions", params={"limit": 500})
        resp.raise_for_status()
        data = resp.json()
        sessions = data if isinstance(data, list) else data.get("sessions", [data])
    except Exception as e:
        print(f"Error fetching sessions: {e}", file=sys.stderr)
        sys.exit(1)

    # Optional model filter
    if model_filter:
        sessions = [s for s in sessions if (s.get("model") or "").lower() == model_filter.lower()]

    # Aggregate historical daily data
    history = _aggregate_daily(sessions, metric)
    if not history:
        print("No historical data found. Cannot forecast.", file=sys.stderr)
        sys.exit(1)

    values = [v for _, v in history]

    # Compute forecast using both linear regression and exponential smoothing
    xs = list(range(len(values)))
    slope, intercept = _linear_regression([float(x) for x in xs], values)
    smoothed = _exponential_smoothing(values, alpha=0.3)

    predictions: list[tuple[str, float]] = []
    last_date = datetime.strptime(history[-1][0], "%Y-%m-%d")
    last_smoothed = smoothed[-1] if smoothed else 0

    for i in range(1, days + 1):
        pred_date = last_date + timedelta(days=i)
        # Blend: 60% linear trend, 40% exponential smoothing continuation
        linear_pred = slope * (len(values) - 1 + i) + intercept
        ema_pred = last_smoothed  # carry forward
        blended = 0.6 * linear_pred + 0.4 * ema_pred
        blended = max(blended, 0)  # no negative forecasts
        predictions.append((pred_date.strftime("%Y-%m-%d"), blended))

    # Summary stats
    hist_total = sum(values)
    hist_avg = hist_total / len(values) if values else 0
    pred_total = sum(v for _, v in predictions)
    pred_avg = pred_total / days if days else 0
    trend_pct = ((pred_avg - hist_avg) / hist_avg * 100) if hist_avg else 0

    if fmt == "json":
        result = {
            "metric": metric,
            "history_days": len(history),
            "forecast_days": days,
            "model_filter": model_filter,
            "summary": {
                "historical_total": round(hist_total, 4),
                "historical_daily_avg": round(hist_avg, 4),
                "forecast_total": round(pred_total, 4),
                "forecast_daily_avg": round(pred_avg, 4),
                "trend_percent": round(trend_pct, 2),
                "regression_slope": round(slope, 6),
            },
            "sparkline": _spark(values + [v for _, v in predictions]),
            "history": [{"date": d, "value": round(v, 4)} for d, v in history],
            "predictions": [{"date": d, "value": round(v, 4)} for d, v in predictions],
        }
        out = json.dumps(result, indent=2)
    elif fmt == "chart":
        lines = []
        lines.append(f"AgentLens Cost Forecast — {metric.upper()}")
        lines.append(f"Based on {len(history)} days of history → {days}-day forecast")
        if model_filter:
            lines.append(f"Model filter: {model_filter}")
        lines.append("")
        lines.append(_render_chart(history, predictions, metric))
        lines.append("")
        trend_arrow = "↑" if trend_pct > 0 else ("↓" if trend_pct < 0 else "→")
        lines.append(f"  Trend: {trend_arrow} {abs(trend_pct):.1f}%  |  "
                      f"Avg: {_format_value(hist_avg, metric)}/day → {_format_value(pred_avg, metric)}/day  |  "
                      f"Forecast total: {_format_value(pred_total, metric)}")
        lines.append(f"  Sparkline: {_spark(values + [v for _, v in predictions])}")
        out = "\n".join(lines)
    else:  # table
        lines = []
        lines.append(f"AgentLens Forecast — {metric.upper()} ({days}-day projection)")
        if model_filter:
            lines.append(f"Model: {model_filter}")
        lines.append(f"History: {len(history)} days | Daily avg: {_format_value(hist_avg, metric)}")
        trend_arrow = "↑" if trend_pct > 0 else ("↓" if trend_pct < 0 else "→")
        lines.append(f"Trend: {trend_arrow} {abs(trend_pct):.1f}%")
        lines.append("")
        lines.append(f"{'Date':<12} {'Predicted':>12}")
        lines.append(f"{'-' * 12} {'-' * 12}")
        for d, v in predictions:
            lines.append(f"{d:<12} {_format_value(v, metric):>12}")
        lines.append(f"{'-' * 12} {'-' * 12}")
        lines.append(f"{'TOTAL':<12} {_format_value(pred_total, metric):>12}")
        lines.append("")
        lines.append(f"Sparkline: {_spark(values + [v for _, v in predictions])}")
        out = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Forecast written to {output}")
    else:
        print(out)
