"""CLI ``profile`` command — aggregate performance profile for an agent.

Usage:
    agentlens-cli profile <agent_name> [--days N] [--format table|json] [--endpoint URL] [--api-key KEY]

Fetches all sessions for the named agent and produces a comprehensive
performance profile: cost distribution, token efficiency, error rate,
latency percentiles, model mix, and tool usage patterns.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from agentlens.cli_common import get_client, print_json


def _percentile(values: list[float], p: float) -> float:
    """Simple percentile without numpy."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_v):
        return sorted_v[f]
    return sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])


def _format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    return f"{mins / 60:.1f}h"


def _bar(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = int(round(value / max_val * width))
    filled = min(filled, width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def cmd_profile(args: argparse.Namespace) -> None:
    """Generate a comprehensive performance profile for a specific agent."""
    client, endpoint = get_client(args)
    agent_name = args.agent_name
    days = getattr(args, "days", 30) or 30
    output_json = getattr(args, "json_output", False)

    # Fetch sessions
    resp = client.get("/sessions", params={"limit": 1000})
    resp.raise_for_status()
    raw = resp.json()
    all_sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Filter by agent name (case-insensitive substring match)
    sessions = []
    for s in all_sessions:
        name = s.get("agent_name", "") or ""
        if agent_name.lower() not in name.lower():
            continue
        created = s.get("created_at", "")
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
        sessions.append(s)

    if not sessions:
        print(f"No sessions found for agent matching '{agent_name}' in the last {days} days.")
        return

    # Gather metrics
    costs: list[float] = []
    tokens_in_list: list[int] = []
    tokens_out_list: list[int] = []
    event_counts: list[int] = []
    durations: list[float] = []
    statuses: dict[str, int] = defaultdict(int)
    model_usage: dict[str, int] = defaultdict(int)
    model_tokens: dict[str, int] = defaultdict(int)
    tool_usage: dict[str, int] = defaultdict(int)
    daily_cost: dict[str, float] = defaultdict(float)
    daily_sessions: dict[str, int] = defaultdict(int)
    error_sessions = 0

    for s in sessions:
        cost = float(s.get("total_cost", 0) or 0)
        tok_in = int(s.get("total_tokens_in", 0) or s.get("total_tokens", 0) or 0)
        tok_out = int(s.get("total_tokens_out", 0) or 0)
        evt_count = int(s.get("event_count", 0) or 0)
        status = s.get("status", "unknown")

        costs.append(cost)
        tokens_in_list.append(tok_in)
        tokens_out_list.append(tok_out)
        event_counts.append(evt_count)
        statuses[status] += 1

        if status in ("error", "failed"):
            error_sessions += 1

        created = s.get("created_at", "")
        day = created[:10] if len(created) >= 10 else "unknown"
        daily_cost[day] += cost
        daily_sessions[day] += 1

        # Parse events for model/tool usage
        for ev in s.get("events", []):
            m = ev.get("model", "")
            if m:
                model_usage[m] += 1
                model_tokens[m] += int(ev.get("tokens_in", 0) or 0) + int(ev.get("tokens_out", 0) or 0)
            dur = ev.get("duration_ms")
            if dur is not None:
                durations.append(float(dur))
            tc = ev.get("tool_call", {})
            if isinstance(tc, dict) and tc.get("tool_name"):
                tool_usage[tc["tool_name"]] += 1

    total_cost = sum(costs)
    total_tokens_in = sum(tokens_in_list)
    total_tokens_out = sum(tokens_out_list)
    total_events = sum(event_counts)
    avg_cost = total_cost / len(sessions) if sessions else 0
    avg_tokens = (total_tokens_in + total_tokens_out) / len(sessions) if sessions else 0
    error_rate = error_sessions / len(sessions) * 100 if sessions else 0

    profile_data = {
        "agent": agent_name,
        "period_days": days,
        "sessions": len(sessions),
        "total_cost": round(total_cost, 6),
        "avg_cost_per_session": round(avg_cost, 6),
        "cost_p50": round(_percentile(costs, 50), 6),
        "cost_p95": round(_percentile(costs, 95), 6),
        "cost_max": round(max(costs), 6) if costs else 0,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "avg_tokens_per_session": round(avg_tokens),
        "total_events": total_events,
        "error_rate_pct": round(error_rate, 1),
        "statuses": dict(statuses),
        "latency_p50_ms": round(_percentile(durations, 50), 1) if durations else None,
        "latency_p95_ms": round(_percentile(durations, 95), 1) if durations else None,
        "latency_p99_ms": round(_percentile(durations, 99), 1) if durations else None,
        "models": {m: {"calls": model_usage[m], "tokens": model_tokens[m]} for m in sorted(model_usage, key=lambda k: model_usage[k], reverse=True)},
        "tools": {t: tool_usage[t] for t in sorted(tool_usage, key=lambda k: tool_usage[k], reverse=True)[:15]},
    }

    if output_json:
        print_json(profile_data)
        return

    # Pretty terminal output
    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    DIM = "\033[2m" if sys.stdout.isatty() else ""
    CYAN = "\033[36m" if sys.stdout.isatty() else ""
    GREEN = "\033[32m" if sys.stdout.isatty() else ""
    RED = "\033[31m" if sys.stdout.isatty() else ""
    YELLOW = "\033[33m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""

    print(f"\n{BOLD}\U0001f464 Agent Profile: {agent_name}{RESET}")
    print(f"   Period: last {days} days | Sessions: {len(sessions)}")
    print()

    # Cost summary
    print(f"   {BOLD}\U0001f4b0 Cost{RESET}")
    print(f"   {'Total:':<20} ${total_cost:.4f}")
    print(f"   {'Avg/session:':<20} ${avg_cost:.4f}")
    print(f"   {'P50:':<20} ${_percentile(costs, 50):.4f}")
    print(f"   {'P95:':<20} ${_percentile(costs, 95):.4f}")
    print(f"   {'Max:':<20} ${max(costs):.4f}" if costs else "")
    print()

    # Token summary
    print(f"   {BOLD}\U0001f4ac Tokens{RESET}")
    print(f"   {'Input:':<20} {total_tokens_in:,}")
    print(f"   {'Output:':<20} {total_tokens_out:,}")
    print(f"   {'Avg/session:':<20} {avg_tokens:,.0f}")
    ratio = total_tokens_out / total_tokens_in if total_tokens_in > 0 else 0
    print(f"   {'Output/Input ratio:':<20} {ratio:.2f}")
    print()

    # Reliability
    err_color = RED if error_rate > 10 else YELLOW if error_rate > 2 else GREEN
    print(f"   {BOLD}\u2764 Reliability{RESET}")
    print(f"   {'Error rate:':<20} {err_color}{error_rate:.1f}%{RESET}")
    print(f"   {'Status breakdown:':<20}", end="")
    parts = [f"{s}={c}" for s, c in sorted(statuses.items(), key=lambda x: -x[1])]
    print(", ".join(parts))
    print()

    # Latency
    if durations:
        print(f"   {BOLD}\u23f1 Latency (per event){RESET}")
        print(f"   {'P50:':<20} {_format_duration(_percentile(durations, 50))}")
        print(f"   {'P95:':<20} {_format_duration(_percentile(durations, 95))}")
        print(f"   {'P99:':<20} {_format_duration(_percentile(durations, 99))}")
        print()

    # Model mix
    if model_usage:
        print(f"   {BOLD}\U0001f916 Model Mix{RESET}")
        max_calls = max(model_usage.values())
        for m in sorted(model_usage, key=lambda k: model_usage[k], reverse=True)[:8]:
            calls = model_usage[m]
            tokens = model_tokens[m]
            bar = _bar(calls, max_calls, 15)
            print(f"   {CYAN}{m:<30}{RESET} {calls:>5} calls  {tokens:>10,} tok  {bar}")
        print()

    # Tool usage
    if tool_usage:
        print(f"   {BOLD}\U0001f527 Top Tools{RESET}")
        max_tool = max(tool_usage.values())
        for t in sorted(tool_usage, key=lambda k: tool_usage[k], reverse=True)[:10]:
            count = tool_usage[t]
            bar = _bar(count, max_tool, 15)
            print(f"   {t:<30} {count:>5} calls  {bar}")
        print()

    # Daily trend sparkline
    if daily_cost:
        sorted_days = sorted(daily_cost.keys())[-14:]  # last 14 days
        max_daily = max(daily_cost[d] for d in sorted_days) or 1
        sparkline_chars = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        spark = ""
        for d in sorted_days:
            level = int(daily_cost[d] / max_daily * 7)
            level = min(level, 7)
            spark += sparkline_chars[level + 1] if daily_cost[d] > 0 else sparkline_chars[0]
        print(f"   {BOLD}\U0001f4c8 Daily Cost Trend (last {len(sorted_days)}d){RESET}")
        print(f"   {spark}  (max: ${max_daily:.4f}/day)")
        print()

    print()


def register_profile_parser(subparsers: Any) -> None:
    """Register the ``profile`` subcommand on an existing argparse subparsers object."""
    p = subparsers.add_parser("profile", help="Comprehensive performance profile for an agent")
    p.add_argument("agent_name", help="Agent name to profile (substring match)")
    p.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
