"""CLI trends command — period-over-period metric comparison with sparklines."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agentlens.cli_common import get_client_only as _get_client


def _sparkline(values: list[float]) -> str:
    """Render a list of numbers as a Unicode sparkline."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    spread = hi - lo if hi != lo else 1
    return "".join(blocks[min(len(blocks) - 1, int((v - lo) / spread * (len(blocks) - 1)))] for v in values)


def _change_str(current: float, previous: float) -> str:
    """Format a change indicator with arrow and percentage."""
    if previous == 0:
        if current == 0:
            return "  →  0%"
        return " ↑ ∞"
    pct = (current - previous) / previous * 100
    if pct > 0:
        return f" ↑ +{pct:.1f}%"
    elif pct < 0:
        return f" ↓ {pct:.1f}%"
    return "  →  0%"


def _color_change(current: float, previous: float, *, invert: bool = False) -> str:
    """Colorize the change string (green = good, red = bad)."""
    raw = _change_str(current, previous)
    if not sys.stdout.isatty():
        return raw
    if previous == 0 and current == 0:
        return f"\033[90m{raw}\033[0m"
    pct = (current - previous) / previous * 100 if previous else 100
    # For cost/errors, increase is bad (red); for sessions/tokens, increase is neutral/good
    if invert:
        color = "\033[32m" if pct > 0 else "\033[31m" if pct < 0 else "\033[90m"
    else:
        color = "\033[31m" if pct > 0 else "\033[32m" if pct < 0 else "\033[90m"
    return f"{color}{raw}\033[0m"


def _parse_ts(val: str) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def cmd_trends(args: argparse.Namespace) -> None:
    """Show metric trends with sparklines and period-over-period comparison."""
    client = _get_client(args)
    period = getattr(args, "period", "week") or "week"
    metric = getattr(args, "metric", "all") or "all"
    limit = getattr(args, "limit", 500) or 500
    output_json = getattr(args, "json_output", False)
    agent_filter = getattr(args, "agent", None)

    period_days = {"day": 1, "week": 7, "month": 30}[period]
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=period_days)
    previous_start = current_start - timedelta(days=period_days)

    # Fetch sessions
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    raw = resp.json()
    sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    if agent_filter:
        sessions = [s for s in sessions if agent_filter.lower() in (s.get("agent_name", "") or "").lower()]

    # Bucket sessions into current and previous periods
    current_sessions: list[dict] = []
    previous_sessions: list[dict] = []
    daily_buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for s in sessions:
        ts = _parse_ts(s.get("created_at", ""))
        if not ts:
            continue
        if ts >= current_start:
            current_sessions.append(s)
        elif ts >= previous_start:
            previous_sessions.append(s)

        # Daily bucketing for sparklines (last 2 periods)
        if ts >= previous_start:
            day_key = ts.strftime("%Y-%m-%d")
            daily_buckets[day_key]["sessions"] += 1
            daily_buckets[day_key]["cost"] += float(s.get("total_cost", 0) or 0)
            daily_buckets[day_key]["tokens"] += int(s.get("total_tokens", 0) or 0)
            daily_buckets[day_key]["events"] += int(s.get("event_count", 0) or 0)
            if s.get("status") in ("error", "failed"):
                daily_buckets[day_key]["errors"] += 1

    # Compute aggregate metrics
    def _agg(sess: list[dict]) -> dict[str, float]:
        result: dict[str, float] = {
            "sessions": len(sess),
            "cost": sum(float(s.get("total_cost", 0) or 0) for s in sess),
            "tokens": sum(int(s.get("total_tokens", 0) or 0) for s in sess),
            "events": sum(int(s.get("event_count", 0) or 0) for s in sess),
            "errors": sum(1 for s in sess if s.get("status") in ("error", "failed")),
        }
        result["error_rate"] = (result["errors"] / result["sessions"] * 100) if result["sessions"] else 0
        result["avg_cost"] = (result["cost"] / result["sessions"]) if result["sessions"] else 0
        result["avg_tokens"] = (result["tokens"] / result["sessions"]) if result["sessions"] else 0
        return result

    curr = _agg(current_sessions)
    prev = _agg(previous_sessions)

    # Build sparkline data (sorted by date)
    sorted_days = sorted(daily_buckets.keys())
    spark_data: dict[str, list[float]] = {
        "sessions": [daily_buckets[d]["sessions"] for d in sorted_days],
        "cost": [daily_buckets[d]["cost"] for d in sorted_days],
        "tokens": [daily_buckets[d]["tokens"] for d in sorted_days],
        "events": [daily_buckets[d]["events"] for d in sorted_days],
        "errors": [daily_buckets[d]["errors"] for d in sorted_days],
    }

    # Determine which metrics to show
    all_metrics = ["sessions", "cost", "tokens", "events", "errors", "error_rate", "avg_cost", "avg_tokens"]
    show_metrics = all_metrics if metric == "all" else [metric]

    if output_json:
        result = {
            "period": period,
            "current_range": f"{current_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            "previous_range": f"{previous_start.strftime('%Y-%m-%d')} to {current_start.strftime('%Y-%m-%d')}",
            "metrics": {},
        }
        for m in show_metrics:
            c_val = curr.get(m, 0)
            p_val = prev.get(m, 0)
            pct_change = ((c_val - p_val) / p_val * 100) if p_val else (100 if c_val else 0)
            result["metrics"][m] = {
                "current": round(c_val, 4),
                "previous": round(p_val, 4),
                "change_pct": round(pct_change, 2),
                "sparkline": spark_data.get(m, []),
            }
        print(json.dumps(result, indent=2))
        return

    # Terminal output
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    DIM = "\033[2m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""
    CYAN = "\033[36m" if sys.stdout.isatty() else ""

    print(f"\n{BOLD}📈 AgentLens Trends — {period}ly comparison{RESET}")
    print(f"{DIM}   Current:  {current_start.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}{RESET}")
    print(f"{DIM}   Previous: {previous_start.strftime('%Y-%m-%d')} → {current_start.strftime('%Y-%m-%d')}{RESET}")
    if agent_filter:
        print(f"{DIM}   Agent:    {agent_filter}{RESET}")
    print()

    # Metric display config
    fmt_config: dict[str, dict[str, Any]] = {
        "sessions": {"label": "Sessions", "fmt": lambda v: f"{v:.0f}", "bad_up": False},
        "cost": {"label": "Total Cost", "fmt": lambda v: f"${v:.4f}", "bad_up": True},
        "tokens": {"label": "Total Tokens", "fmt": lambda v: f"{v:,.0f}", "bad_up": False},
        "events": {"label": "Total Events", "fmt": lambda v: f"{v:,.0f}", "bad_up": False},
        "errors": {"label": "Errors", "fmt": lambda v: f"{v:.0f}", "bad_up": True},
        "error_rate": {"label": "Error Rate", "fmt": lambda v: f"{v:.1f}%", "bad_up": True},
        "avg_cost": {"label": "Avg Cost/Session", "fmt": lambda v: f"${v:.4f}", "bad_up": True},
        "avg_tokens": {"label": "Avg Tokens/Session", "fmt": lambda v: f"{v:,.0f}", "bad_up": False},
    }

    for m in show_metrics:
        cfg = fmt_config.get(m, {"label": m, "fmt": str, "bad_up": False})
        c_val = curr.get(m, 0)
        p_val = prev.get(m, 0)
        spark = _sparkline(spark_data.get(m, []))
        change = _color_change(c_val, p_val, invert=not cfg["bad_up"])

        print(f"   {CYAN}{cfg['label']:<22}{RESET} {cfg['fmt'](c_val):>14}  {DIM}(was {cfg['fmt'](p_val)}){RESET} {change}")
        if spark:
            print(f"   {'':22} {DIM}{spark}{RESET}")
        print()

    # Top movers (agents with biggest changes)
    if not agent_filter and len(current_sessions) > 0:
        agent_curr: dict[str, float] = defaultdict(float)
        agent_prev: dict[str, float] = defaultdict(float)
        for s in current_sessions:
            agent_curr[s.get("agent_name", "unknown")] += float(s.get("total_cost", 0) or 0)
        for s in previous_sessions:
            agent_prev[s.get("agent_name", "unknown")] += float(s.get("total_cost", 0) or 0)

        all_agents = set(agent_curr) | set(agent_prev)
        movers = []
        for a in all_agents:
            c = agent_curr.get(a, 0)
            p = agent_prev.get(a, 0)
            if p > 0:
                pct = (c - p) / p * 100
            elif c > 0:
                pct = 100
            else:
                pct = 0
            movers.append((a, c, p, pct))

        movers.sort(key=lambda x: abs(x[3]), reverse=True)
        top_movers = movers[:5]

        if top_movers:
            print(f"   {BOLD}Top Movers (by cost change):{RESET}")
            for name, c, p, pct in top_movers:
                change = _color_change(c, p, invert=False)
                print(f"     {name:<20} ${c:.4f}{change}")
            print()
