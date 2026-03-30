"""CLI capacity command – fleet capacity planning from historical session data.

Usage:
    agentlens-cli capacity [--horizon N] [--target-rpm N] [--target-latency N]
        [--format table|json|chart] [--output FILE]
        [--endpoint URL] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agentlens.cli_common import get_client


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _spark(values: list[float], width: int = 30) -> str:
    """Render a sparkline string from values."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1.0
    return "".join(bars[min(int((v - mn) / rng * (len(bars) - 1)), len(bars) - 1)] for v in values)


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Simple OLS. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    slope = ss_xy / ss_xx if ss_xx else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _percentile(values: list[float], p: float) -> float:
    """Compute p-th percentile (0-100)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    d = k - f
    return s[f] + d * (s[c] - s[f])


def _aggregate_hourly(sessions: list[dict]) -> dict[str, list[float]]:
    """Aggregate sessions into hourly buckets, returning metrics per hour."""
    hourly_counts: dict[str, int] = defaultdict(int)
    hourly_tokens: dict[str, float] = defaultdict(float)
    hourly_costs: dict[str, float] = defaultdict(float)
    hourly_latencies: dict[str, list[float]] = defaultdict(list)

    for s in sessions:
        ts = s.get("created_at") or s.get("timestamp") or ""
        if not ts:
            continue
        hour_key = ts[:13]  # YYYY-MM-DDTHH
        hourly_counts[hour_key] += 1
        hourly_tokens[hour_key] += float(s.get("total_tokens", 0) or 0)
        hourly_costs[hour_key] += float(s.get("total_cost", 0) or s.get("cost_usd", 0) or 0)
        duration = s.get("duration_ms") or s.get("latency_ms")
        if duration is not None:
            hourly_latencies[hour_key].append(float(duration))

    return {
        "counts": hourly_counts,
        "tokens": hourly_tokens,
        "costs": hourly_costs,
        "latencies": hourly_latencies,
    }


def _detect_bottlenecks(
    hourly: dict[str, Any],
    peak_sessions_per_hour: float,
    p95_latency: float,
) -> list[dict]:
    """Detect potential bottlenecks based on usage patterns."""
    bottlenecks = []

    # High concurrency risk
    if peak_sessions_per_hour > 50:
        bottlenecks.append({
            "resource": "concurrency",
            "severity": "high" if peak_sessions_per_hour > 100 else "medium",
            "detail": f"Peak {peak_sessions_per_hour:.0f} sessions/hour — consider connection pooling or load balancing",
            "metric": peak_sessions_per_hour,
        })

    # Latency risk
    if p95_latency > 5000:
        bottlenecks.append({
            "resource": "latency",
            "severity": "high" if p95_latency > 10000 else "medium",
            "detail": f"P95 latency {p95_latency:.0f}ms — consider caching, model optimization, or timeout tuning",
            "metric": p95_latency,
        })

    # Token throughput risk
    token_vals = list(hourly["tokens"].values())
    if token_vals:
        peak_tokens = max(token_vals)
        if peak_tokens > 1_000_000:
            bottlenecks.append({
                "resource": "token_budget",
                "severity": "high" if peak_tokens > 5_000_000 else "medium",
                "detail": f"Peak {peak_tokens/1_000_000:.1f}M tokens/hour — monitor rate limits and budget caps",
                "metric": peak_tokens,
            })

    return bottlenecks


def _sizing_recommendation(
    avg_rpm: float,
    target_rpm: float | None,
    target_latency: float | None,
    p95_latency: float,
    peak_rpm: float,
) -> dict:
    """Compute resource sizing recommendations."""
    effective_target = target_rpm or (peak_rpm * 1.5)  # 50% headroom
    effective_latency = target_latency or 3000.0

    # Estimate required capacity multiplier
    current_capacity = peak_rpm if peak_rpm > 0 else 1
    scale_factor = effective_target / current_capacity

    # Scaling action
    if scale_factor > 2.0:
        action = "scale_out"
        urgency = "high"
    elif scale_factor > 1.3:
        action = "scale_up"
        urgency = "medium"
    elif scale_factor < 0.5:
        action = "scale_down"
        urgency = "low"
    else:
        action = "none"
        urgency = "low"

    # Latency-based recommendation
    latency_action = None
    if p95_latency > effective_latency:
        ratio = p95_latency / effective_latency
        if ratio > 3:
            latency_action = "Urgent: P95 latency is 3x+ above target. Add caching layer, optimize prompts, or use faster model tiers."
        elif ratio > 1.5:
            latency_action = "Consider: P95 latency exceeds target. Evaluate prompt length reduction or model selection."
        else:
            latency_action = "Minor: P95 latency slightly above target. Monitor for degradation."

    return {
        "target_rpm": effective_target,
        "target_latency_ms": effective_latency,
        "scale_factor": round(scale_factor, 2),
        "action": action,
        "urgency": urgency,
        "latency_recommendation": latency_action,
        "estimated_instances": max(1, math.ceil(scale_factor)),
    }


def _project_workload(
    hourly_counts: dict[str, int], horizon_hours: int
) -> list[tuple[str, float]]:
    """Project future workload using linear regression on hourly data."""
    if not hourly_counts:
        return []
    sorted_hours = sorted(hourly_counts.keys())
    values = [float(hourly_counts[h]) for h in sorted_hours]
    xs = [float(i) for i in range(len(values))]
    slope, intercept = _linear_regression(xs, values)

    projections = []
    try:
        last_dt = datetime.strptime(sorted_hours[-1], "%Y-%m-%dT%H")
    except ValueError:
        last_dt = _utcnow()

    for i in range(1, horizon_hours + 1):
        pred = max(0, slope * (len(values) - 1 + i) + intercept)
        future_dt = last_dt + timedelta(hours=i)
        projections.append((future_dt.strftime("%Y-%m-%d %H:00"), pred))

    return projections


def cmd_capacity(args: argparse.Namespace) -> None:
    """Execute the capacity CLI command."""
    client, _endpoint = get_client(args)

    horizon = getattr(args, "horizon", 24) or 24
    target_rpm = getattr(args, "target_rpm", None)
    target_latency = getattr(args, "target_latency", None)
    fmt = getattr(args, "format", "table") or "table"
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

    if not sessions:
        print("No session data found. Cannot plan capacity.", file=sys.stderr)
        sys.exit(1)

    # Aggregate hourly metrics
    hourly = _aggregate_hourly(sessions)
    counts = hourly["counts"]
    count_vals = list(counts.values()) if counts else [0]

    # Compute key metrics
    peak_sessions_per_hour = max(count_vals) if count_vals else 0
    avg_sessions_per_hour = sum(count_vals) / len(count_vals) if count_vals else 0
    peak_rpm = peak_sessions_per_hour / 60.0
    avg_rpm = avg_sessions_per_hour / 60.0

    # Latency stats
    all_latencies: list[float] = []
    for lats in hourly["latencies"].values():
        all_latencies.extend(lats)
    p50_latency = _percentile(all_latencies, 50) if all_latencies else 0
    p95_latency = _percentile(all_latencies, 95) if all_latencies else 0
    p99_latency = _percentile(all_latencies, 99) if all_latencies else 0

    # Token stats
    token_vals = list(hourly["tokens"].values()) if hourly["tokens"] else [0]
    total_tokens = sum(token_vals)
    peak_tokens_per_hour = max(token_vals) if token_vals else 0

    # Cost stats
    cost_vals = list(hourly["costs"].values()) if hourly["costs"] else [0]
    total_cost = sum(cost_vals)

    # Detect bottlenecks
    bottlenecks = _detect_bottlenecks(hourly, peak_sessions_per_hour, p95_latency)

    # Sizing recommendation
    sizing = _sizing_recommendation(avg_rpm, target_rpm, target_latency, p95_latency, peak_rpm)

    # Workload projection
    projections = _project_workload(counts, horizon)

    if fmt == "json":
        result = {
            "summary": {
                "total_sessions": len(sessions),
                "observation_hours": len(counts),
                "peak_sessions_per_hour": peak_sessions_per_hour,
                "avg_sessions_per_hour": round(avg_sessions_per_hour, 2),
                "peak_rpm": round(peak_rpm, 2),
                "avg_rpm": round(avg_rpm, 2),
                "total_tokens": total_tokens,
                "peak_tokens_per_hour": peak_tokens_per_hour,
                "total_cost_usd": round(total_cost, 4),
            },
            "latency": {
                "p50_ms": round(p50_latency, 1),
                "p95_ms": round(p95_latency, 1),
                "p99_ms": round(p99_latency, 1),
                "samples": len(all_latencies),
            },
            "bottlenecks": bottlenecks,
            "sizing": sizing,
            "projection": [{"time": t, "sessions": round(v, 1)} for t, v in projections],
            "sparkline": _spark([float(v) for v in count_vals]),
        }
        out = json.dumps(result, indent=2)

    elif fmt == "chart":
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║       AgentLens Capacity Planning Report        ║")
        lines.append("╚══════════════════════════════════════════════════╝")
        lines.append("")

        # Current load chart
        lines.append("  📊 Current Load (sessions/hour)")
        display_vals = list(count_vals)[-24:]  # last 24 hours
        mx = max(display_vals) if display_vals else 1
        if mx == 0:
            mx = 1
        width = 40
        for i, v in enumerate(display_vals):
            bar_len = int(v / mx * width)
            marker = "█" if v < mx * 0.7 else ("▓" if v < mx * 0.9 else "░")
            lines.append(f"  {i:>3}h │{marker * bar_len}{'·' * (width - bar_len)}│ {v:.0f}")
        lines.append("")

        # Projection chart
        if projections:
            lines.append(f"  🔮 {horizon}h Workload Projection")
            proj_vals = [v for _, v in projections]
            proj_mx = max(proj_vals) if proj_vals else 1
            if proj_mx == 0:
                proj_mx = 1
            for t, v in projections[:24]:  # show up to 24h
                bar_len = int(v / proj_mx * width)
                lines.append(f"  {t[11:16]} │{'▓' * bar_len}{'·' * (width - bar_len)}│ {v:.1f}")
            lines.append("")

        # Bottlenecks
        if bottlenecks:
            lines.append("  ⚠️  Bottlenecks Detected")
            for b in bottlenecks:
                icon = "🔴" if b["severity"] == "high" else "🟡"
                lines.append(f"  {icon} [{b['resource']}] {b['detail']}")
            lines.append("")

        # Sizing
        lines.append("  📐 Sizing Recommendation")
        action_icons = {"none": "✅", "scale_up": "⬆️", "scale_out": "↗️", "scale_down": "⬇️"}
        lines.append(f"  {action_icons.get(sizing['action'], '❓')} Action: {sizing['action']} (×{sizing['scale_factor']})")
        lines.append(f"     Estimated instances: {sizing['estimated_instances']}")
        if sizing["latency_recommendation"]:
            lines.append(f"     💡 {sizing['latency_recommendation']}")
        lines.append("")
        lines.append(f"  Sparkline: {_spark([float(v) for v in count_vals])}")
        out = "\n".join(lines)

    else:  # table
        lines = []
        lines.append("AgentLens Capacity Report")
        lines.append("=" * 50)
        lines.append("")
        lines.append("WORKLOAD SUMMARY")
        lines.append(f"  Sessions analyzed:      {len(sessions)}")
        lines.append(f"  Observation window:     {len(counts)} hours")
        lines.append(f"  Peak sessions/hour:     {peak_sessions_per_hour}")
        lines.append(f"  Avg sessions/hour:      {avg_sessions_per_hour:.1f}")
        lines.append(f"  Peak RPM:               {peak_rpm:.2f}")
        lines.append(f"  Total tokens:           {total_tokens:,.0f}")
        lines.append(f"  Peak tokens/hour:       {peak_tokens_per_hour:,.0f}")
        lines.append(f"  Total cost:             ${total_cost:,.2f}")
        lines.append(f"  Load sparkline:         {_spark([float(v) for v in count_vals])}")
        lines.append("")

        if all_latencies:
            lines.append("LATENCY")
            lines.append(f"  P50:  {p50_latency:,.0f}ms")
            lines.append(f"  P95:  {p95_latency:,.0f}ms")
            lines.append(f"  P99:  {p99_latency:,.0f}ms")
            lines.append(f"  Samples: {len(all_latencies)}")
            lines.append("")

        if bottlenecks:
            lines.append("BOTTLENECKS")
            for b in bottlenecks:
                sev = "!!" if b["severity"] == "high" else "!"
                lines.append(f"  [{sev}] {b['resource']}: {b['detail']}")
            lines.append("")

        lines.append("SIZING RECOMMENDATION")
        lines.append(f"  Action:              {sizing['action']}")
        lines.append(f"  Scale factor:        {sizing['scale_factor']}x")
        lines.append(f"  Target RPM:          {sizing['target_rpm']:.1f}")
        lines.append(f"  Target latency:      {sizing['target_latency_ms']:.0f}ms")
        lines.append(f"  Est. instances:      {sizing['estimated_instances']}")
        if sizing["latency_recommendation"]:
            lines.append(f"  Note: {sizing['latency_recommendation']}")
        lines.append("")

        if projections:
            lines.append(f"WORKLOAD PROJECTION ({horizon}h)")
            lines.append(f"  {'Time':<18} {'Sessions':>10}")
            lines.append(f"  {'-' * 18} {'-' * 10}")
            for t, v in projections[:24]:
                lines.append(f"  {t:<18} {v:>10.1f}")
            if len(projections) > 24:
                lines.append(f"  ... ({len(projections) - 24} more hours)")
            lines.append("")

        out = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Capacity report written to {output}")
    else:
        print(out)
