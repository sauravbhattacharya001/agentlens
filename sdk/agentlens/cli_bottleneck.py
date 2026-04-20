"""CLI bottleneck command – identify performance bottlenecks across sessions.

Analyzes sessions to find which agents, models, or event types contribute
the most to latency, cost, or error rates, helping users pinpoint where
to focus optimization efforts.

Usage:
    agentlens-cli bottleneck [--by agent|model|type] [--metric latency|cost|errors]
        [--limit N] [--min-sessions N] [--format table|json]
        [--output FILE] [--endpoint URL] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

import httpx

from agentlens.cli_common import get_client


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _bar(value: float, max_val: float, width: int = 20) -> str:
    if max_val <= 0:
        return ""
    filled = int(value / max_val * width)
    return "█" * filled + "░" * (width - filled)


def _severity(pct: float) -> str:
    if pct >= 40:
        return "🔴 CRITICAL"
    if pct >= 25:
        return "🟠 HIGH"
    if pct >= 10:
        return "🟡 MEDIUM"
    return "🟢 LOW"


def cmd_bottleneck(args: argparse.Namespace) -> None:
    client = get_client(args)
    group_by: str = getattr(args, "by", "agent") or "agent"
    metric: str = getattr(args, "metric", "latency") or "latency"
    limit: int = getattr(args, "limit", 10) or 10
    min_sessions: int = getattr(args, "min_sessions", 2) or 2
    fmt: str = getattr(args, "format", "table") or "table"
    output: str | None = getattr(args, "output", None)

    # Fetch sessions
    try:
        resp = client.get("/api/sessions", params={"limit": 200})
        resp.raise_for_status()
        sessions = resp.json()
        if isinstance(sessions, dict):
            sessions = sessions.get("sessions", [])
    except httpx.HTTPError as exc:
        print(f"Error fetching sessions: {exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch events for each session
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "total_latency": 0.0,
        "total_cost": 0.0,
        "total_errors": 0,
        "total_events": 0,
        "sessions": set(),
        "latencies": [],
    })

    for sess in sessions:
        sid = sess.get("id") or sess.get("session_id", "")
        try:
            eresp = client.get("/api/events", params={"session": sid, "limit": 500})
            eresp.raise_for_status()
            events = eresp.json()
            if isinstance(events, dict):
                events = events.get("events", [])
        except httpx.HTTPError:
            continue

        for ev in events:
            # Determine grouping key
            if group_by == "model":
                key = ev.get("model") or ev.get("meta", {}).get("model", "unknown")
            elif group_by == "type":
                key = ev.get("type", "unknown")
            else:  # agent
                key = ev.get("agent") or ev.get("meta", {}).get("agent", "unknown")

            bucket = buckets[key]
            bucket["sessions"].add(sid)
            bucket["total_events"] += 1

            lat = ev.get("duration_ms") or ev.get("latency_ms") or ev.get("duration", 0)
            if isinstance(lat, (int, float)):
                bucket["total_latency"] += lat
                bucket["latencies"].append(lat)

            cost = ev.get("cost") or ev.get("cost_usd", 0)
            if isinstance(cost, (int, float)):
                bucket["total_cost"] += cost

            if ev.get("error") or ev.get("level") == "error":
                bucket["total_errors"] += 1

    # Filter by min sessions
    filtered = {k: v for k, v in buckets.items() if len(v["sessions"]) >= min_sessions}

    if not filtered:
        print("No bottleneck data found. Try lowering --min-sessions.", file=sys.stderr)
        sys.exit(0)

    # Compute contribution percentages
    totals = {
        "latency": sum(v["total_latency"] for v in filtered.values()),
        "cost": sum(v["total_cost"] for v in filtered.values()),
        "errors": sum(v["total_errors"] for v in filtered.values()),
    }

    results = []
    for key, bucket in filtered.items():
        total_metric = totals.get(metric, 1) or 1
        if metric == "latency":
            value = bucket["total_latency"]
        elif metric == "cost":
            value = bucket["total_cost"]
        else:
            value = bucket["total_errors"]

        pct = (value / total_metric * 100) if total_metric else 0
        results.append({
            "key": key,
            "metric_value": value,
            "pct_contribution": round(pct, 1),
            "severity": _severity(pct),
            "sessions": len(bucket["sessions"]),
            "events": bucket["total_events"],
            "avg_latency_ms": round(bucket["total_latency"] / bucket["total_events"], 1) if bucket["total_events"] else 0,
            "p95_latency_ms": round(_percentile(bucket["latencies"], 95), 1),
            "total_cost_usd": round(bucket["total_cost"], 4),
            "error_count": bucket["total_errors"],
            "error_rate_pct": round(bucket["total_errors"] / bucket["total_events"] * 100, 1) if bucket["total_events"] else 0,
        })

    # Sort by contribution descending
    results.sort(key=lambda r: r["pct_contribution"], reverse=True)
    results = results[:limit]

    if fmt == "json":
        out = json.dumps(results, indent=2)
    else:
        max_pct = max((r["pct_contribution"] for r in results), default=1)
        lines = [
            f"🔍 Bottleneck Analysis (by {group_by}, metric: {metric})",
            f"   Analyzed {len(sessions)} sessions, {len(filtered)} {group_by}s",
            "",
        ]

        # Header
        key_label = group_by.upper()
        lines.append(f"{'#':<3} {key_label:<20} {'SHARE':>7}  {'BAR':<22} {'SEVERITY':<14} {'SESS':>5} {'EVENTS':>7} {'AVG ms':>8} {'P95 ms':>8} {'COST $':>8} {'ERR%':>6}")
        lines.append("─" * 120)

        for i, r in enumerate(results, 1):
            bar = _bar(r["pct_contribution"], max_pct)
            lines.append(
                f"{i:<3} {r['key']:<20} {r['pct_contribution']:>6.1f}%  {bar:<22} {r['severity']:<14} {r['sessions']:>5} {r['events']:>7} {r['avg_latency_ms']:>8.1f} {r['p95_latency_ms']:>8.1f} {r['total_cost_usd']:>8.4f} {r['error_rate_pct']:>5.1f}%"
            )

        lines.append("")
        lines.append("💡 Recommendations:")
        for r in results[:3]:
            if r["pct_contribution"] >= 25:
                lines.append(f"   • {r['key']}: contributes {r['pct_contribution']}% of {metric} — optimize or cache")
            if r["error_rate_pct"] >= 10:
                lines.append(f"   • {r['key']}: {r['error_rate_pct']}% error rate — investigate failures")
            if r["p95_latency_ms"] > r["avg_latency_ms"] * 3:
                lines.append(f"   • {r['key']}: P95 latency ({r['p95_latency_ms']}ms) is {r['p95_latency_ms']/r['avg_latency_ms']:.1f}x avg — check tail latency")

        out = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"Written to {output}")
    else:
        print(out)


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("bottleneck", help="Identify performance bottlenecks across sessions")
    p.add_argument("--by", choices=["agent", "model", "type"], default="agent",
                    help="Group bottlenecks by agent, model, or event type (default: agent)")
    p.add_argument("--metric", choices=["latency", "cost", "errors"], default="latency",
                    help="Primary metric to rank bottlenecks (default: latency)")
    p.add_argument("--limit", type=int, default=10, help="Number of top bottlenecks to show")
    p.add_argument("--min-sessions", type=int, default=2,
                    help="Minimum sessions a group must appear in (default: 2)")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--output", help="Write output to file")
    p.add_argument("--endpoint", help="Backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_bottleneck)
