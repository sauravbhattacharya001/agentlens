"""CLI leaderboard command — rank agents by performance metrics."""

from __future__ import annotations

import json
from typing import Any


def _medal(rank: int) -> str:
    return {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(rank, f"#{rank}")


def _bar(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return " " * width
    filled = int(round(value / max_val * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _fmt_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


def _fmt_cost(usd: float) -> str:
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def cmd_leaderboard(args: Any) -> None:
    """Fetch and display the agent leaderboard."""
    from agentlens.cli import _get_client

    base_url, headers = _get_client(args)

    import urllib.request

    params = []
    if args.sort:
        params.append(f"sort={args.sort}")
    if args.days:
        params.append(f"days={args.days}")
    if args.limit:
        params.append(f"limit={args.limit}")
    if args.min_sessions:
        params.append(f"min_sessions={args.min_sessions}")
    if args.order:
        params.append(f"order={args.order}")

    qs = ("?" + "&".join(params)) if params else ""
    url = f"{base_url}/leaderboard{qs}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    agents = data.get("agents", [])

    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2))
        return

    if not agents:
        print("No qualifying agents found.")
        print(f"  Period: {data.get('period_days', '?')} days")
        print(f"  Min sessions: {data.get('min_sessions', '?')}")
        return

    sort_by = data.get("sort", args.sort or "efficiency")
    metric_label = {
        "efficiency": "Efficiency (out/in tokens)",
        "speed": "Avg Session Duration",
        "reliability": "Success Rate",
        "cost": "Cost / Session",
        "volume": "Total Sessions",
    }.get(sort_by, sort_by)

    print(f"\n\U0001f3c6  Agent Leaderboard — sorted by {metric_label}")
    print(f"   Period: {data.get('period_days', '?')}d | "
          f"Qualifying: {data.get('total_qualifying_agents', '?')} agents | "
          f"Min sessions: {data.get('min_sessions', '?')}")
    print()

    # Determine the bar metric
    bar_key = {
        "efficiency": "efficiency_ratio",
        "speed": "avg_session_duration_ms",
        "reliability": "success_rate",
        "cost": "cost_per_session_usd",
        "volume": "total_sessions",
    }.get(sort_by, "total_sessions")

    max_bar = max((a.get(bar_key, 0) for a in agents), default=1) or 1

    # Header
    name_w = max(len(a.get("agent_name", "")) for a in agents)
    name_w = max(name_w, 10)

    print(f"  {'Rank':<6} {'Agent':<{name_w}}  {'Sessions':>8}  {'Success':>7}  "
          f"{'Avg Duration':>12}  {'Cost/Sess':>10}  {'Efficiency':>10}  {'Bar'}")
    print(f"  {'─' * 6} {'─' * name_w}  {'─' * 8}  {'─' * 7}  {'─' * 12}  {'─' * 10}  {'─' * 10}  {'─' * 20}")

    for a in agents:
        rank = a.get("rank", "?")
        medal = _medal(rank) if isinstance(rank, int) else f"#{rank}"
        name = a.get("agent_name", "?")
        sessions = a.get("total_sessions", 0)
        success = f"{a.get('success_rate', 0):.0f}%"
        duration = _fmt_duration(a.get("avg_session_duration_ms", 0))
        cost = _fmt_cost(a.get("cost_per_session_usd", 0))
        eff = f"{a.get('efficiency_ratio', 0):.3f}"
        bar = _bar(a.get(bar_key, 0), max_bar)

        print(f"  {medal:<6} {name:<{name_w}}  {sessions:>8}  {success:>7}  "
              f"{duration:>12}  {cost:>10}  {eff:>10}  {bar}")

    print()

    # Summary footer
    total_sessions = sum(a.get("total_sessions", 0) for a in agents)
    total_cost = sum(a.get("total_cost_usd", 0) for a in agents)
    avg_success = sum(a.get("success_rate", 0) for a in agents) / len(agents) if agents else 0

    print(f"  Totals: {total_sessions} sessions | {_fmt_cost(total_cost)} spent | "
          f"{avg_success:.0f}% avg success rate")
    print()
