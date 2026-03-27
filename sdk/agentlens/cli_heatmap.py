"""agentlens heatmap — GitHub-style terminal activity heatmap (day-of-week × hour)."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from agentlens.cli_common import get_client


def cmd_heatmap(args: argparse.Namespace) -> None:
    """GitHub-style terminal activity heatmap (day-of-week × hour)."""
    client, endpoint = get_client(args)
    metric = getattr(args, "metric", "sessions") or "sessions"
    weeks = getattr(args, "weeks", 12) or 12
    limit = getattr(args, "limit", 500) or 500

    print(f"\U0001f4ca Fetching sessions from {endpoint} ...")
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    raw = resp.json()
    sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(weeks=weeks)

    # Aggregate into (weekday, hour) buckets
    grid: dict[tuple[int, int], float] = defaultdict(float)

    for s in sessions:
        created = s.get("created_at", "")
        if not created:
            continue
        try:
            ts = created.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            continue
        key = (dt.weekday(), dt.hour)
        if metric == "sessions":
            grid[key] += 1
        elif metric == "cost":
            grid[key] += float(s.get("total_cost", 0) or 0)
        elif metric == "tokens":
            grid[key] += int(s.get("total_tokens", 0) or 0)
        elif metric == "events":
            grid[key] += int(s.get("event_count", 0) or 0)

    if not grid:
        print("\u26a0\ufe0f  No session data found in the specified time range.")
        return

    max_val = max(grid.values()) if grid else 1
    if max_val == 0:
        max_val = 1

    blocks = [" ", "\u2591", "\u2592", "\u2593", "\u2588"]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    metric_label = {"sessions": "Sessions", "cost": "Cost ($)", "tokens": "Tokens", "events": "Events"}[metric]
    print(f"\n\U0001f5d3  Activity Heatmap \u2014 {metric_label} (last {weeks} weeks)\n")

    header = "      "
    for h in range(24):
        header += f"{h:>2} "
    print(DIM + header + RESET)
    print("      " + "\u2500\u2500\u2500" * 24)

    for day_idx in range(7):
        row = f" {day_names[day_idx]:>3}  "
        for hour in range(24):
            val = grid.get((day_idx, hour), 0)
            ratio = val / max_val
            level = 0 if val == 0 else min(4, max(1, int(ratio * 4) + (1 if ratio > 0 else 0)))
            block = blocks[level]
            if level == 0:
                row += DIM + " \u00b7 " + RESET
            elif level <= 2:
                row += GREEN + f" {block} " + RESET
            else:
                row += BRIGHT_GREEN + f" {block} " + RESET
        print(row)

    print("      " + "\u2500\u2500\u2500" * 24)

    # Legend
    print(f"\n  Legend: {DIM} \u00b7 {RESET}= none ", end="")
    for i, b in enumerate(blocks[1:], 1):
        color = GREEN if i <= 2 else BRIGHT_GREEN
        print(f" {color}{b}{RESET} ", end="")
    print(f"= max ({max_val:,.1f} {metric})")

    # Summary stats
    total = sum(grid.values())
    active_slots = sum(1 for v in grid.values() if v > 0)
    peak_key = max(grid, key=grid.get)
    peak_day, peak_hour = day_names[peak_key[0]], peak_key[1]
    print(f"\n  Total: {total:,.1f} | Active slots: {active_slots}/168 | Peak: {peak_day} {peak_hour}:00 ({grid[peak_key]:,.1f})")

    day_totals: dict[int, float] = defaultdict(float)
    hour_totals: dict[int, float] = defaultdict(float)
    for (d, h), v in grid.items():
        day_totals[d] += v
        hour_totals[h] += v

    busiest_day = max(day_totals, key=day_totals.get) if day_totals else 0
    busiest_hour = max(hour_totals, key=hour_totals.get) if hour_totals else 0
    print(f"  Busiest day: {day_names[busiest_day]} ({day_totals[busiest_day]:,.1f}) | Busiest hour: {busiest_hour}:00 ({hour_totals[busiest_hour]:,.1f})")
    print()
