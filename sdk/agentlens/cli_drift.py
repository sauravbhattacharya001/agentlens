"""CLI drift command - detect behavioral drift in agent sessions.

Compares recent sessions against a baseline period to identify shifts in
token usage, latency, tool-call patterns, error rates, and cost.

Usage:
    agentlens-cli drift [--agent AGENT] [--baseline-days 14] [--recent-days 3]
                        [--threshold 2.0] [--json] [--endpoint URL] [--api-key KEY]

Examples:
    agentlens-cli drift
    agentlens-cli drift --agent my-agent --threshold 1.5
    agentlens-cli drift --baseline-days 30 --recent-days 7 --json
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agentlens.cli_common import get_client, print_json, sparkline


# ── Drift dimensions ─────────────────────────────────────────────────

_DIMENSIONS = [
    "avg_tokens",
    "avg_latency_ms",
    "avg_tool_calls",
    "error_rate",
    "avg_cost",
    "avg_events",
]

_LABELS = {
    "avg_tokens": "Avg Tokens",
    "avg_latency_ms": "Avg Latency (ms)",
    "avg_tool_calls": "Avg Tool Calls",
    "error_rate": "Error Rate",
    "avg_cost": "Avg Cost ($)",
    "avg_events": "Avg Events",
}

_DRIFT_ICONS = {
    "stable": "✅",
    "minor": "⚠️ ",
    "major": "🔴",
    "insufficient_data": "⬜",
}


# ── Stats helpers ────────────────────────────────────────────────────

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def _z_score(baseline_mean: float, baseline_std: float, recent_mean: float) -> float:
    if baseline_std == 0:
        return 0.0 if recent_mean == baseline_mean else float("inf")
    return (recent_mean - baseline_mean) / baseline_std


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / abs(old)) * 100.0


# ── Data extraction ──────────────────────────────────────────────────

def _extract_metrics(sessions: list[dict]) -> dict[str, list[float]]:
    """Extract per-session metric values."""
    metrics: dict[str, list[float]] = {d: [] for d in _DIMENSIONS}

    for s in sessions:
        tokens = s.get("total_tokens") or s.get("tokens") or 0
        metrics["avg_tokens"].append(float(tokens))

        duration = s.get("duration_ms") or s.get("duration") or 0
        metrics["avg_latency_ms"].append(float(duration))

        tool_calls = s.get("tool_call_count") or s.get("tool_calls") or 0
        metrics["avg_tool_calls"].append(float(tool_calls))

        events = s.get("event_count") or s.get("events") or 0
        metrics["avg_events"].append(float(events))

        cost = s.get("total_cost") or s.get("cost") or 0.0
        metrics["avg_cost"].append(float(cost))

        has_error = 1.0 if s.get("has_error") or s.get("error") else 0.0
        metrics["error_rate"].append(has_error)

    return metrics


# ── Analysis ─────────────────────────────────────────────────────────

def _analyze_drift(
    baseline_sessions: list[dict],
    recent_sessions: list[dict],
    threshold: float,
) -> dict[str, Any]:
    """Compare baseline vs recent sessions and compute drift scores."""
    baseline_metrics = _extract_metrics(baseline_sessions)
    recent_metrics = _extract_metrics(recent_sessions)

    dimensions: list[dict[str, Any]] = []
    overall_drift = 0.0
    drift_count = 0

    for dim in _DIMENSIONS:
        b_vals = baseline_metrics[dim]
        r_vals = recent_metrics[dim]

        if len(b_vals) < 3 or len(r_vals) < 2:
            dimensions.append({
                "dimension": dim,
                "label": _LABELS[dim],
                "status": "insufficient_data",
                "z_score": 0.0,
                "pct_change": 0.0,
                "baseline_mean": _mean(b_vals),
                "recent_mean": _mean(r_vals),
            })
            continue

        b_mean = _mean(b_vals)
        b_std = _stddev(b_vals)
        r_mean = _mean(r_vals)
        z = _z_score(b_mean, b_std, r_mean)
        pct = _pct_change(b_mean, r_mean)

        abs_z = abs(z)
        if abs_z >= threshold * 1.5:
            status = "major"
        elif abs_z >= threshold:
            status = "minor"
        else:
            status = "stable"

        dimensions.append({
            "dimension": dim,
            "label": _LABELS[dim],
            "status": status,
            "z_score": round(z, 2),
            "pct_change": round(pct, 1),
            "baseline_mean": round(b_mean, 2),
            "baseline_std": round(b_std, 2),
            "recent_mean": round(r_mean, 2),
            "direction": "up" if z > 0 else "down" if z < 0 else "flat",
        })

        overall_drift += abs_z
        drift_count += 1

    avg_drift = overall_drift / drift_count if drift_count > 0 else 0.0

    if avg_drift >= threshold * 1.5:
        verdict = "SIGNIFICANT_DRIFT"
        grade = "F"
    elif avg_drift >= threshold:
        verdict = "MODERATE_DRIFT"
        grade = "C"
    elif avg_drift >= threshold * 0.5:
        verdict = "MINOR_DRIFT"
        grade = "B"
    else:
        verdict = "STABLE"
        grade = "A"

    return {
        "verdict": verdict,
        "grade": grade,
        "avg_drift_score": round(avg_drift, 2),
        "threshold": threshold,
        "baseline_sessions": len(baseline_sessions),
        "recent_sessions": len(recent_sessions),
        "dimensions": dimensions,
    }


# ── Display ──────────────────────────────────────────────────────────

def _print_drift_report(result: dict[str, Any]) -> None:
    """Pretty-print drift analysis to stdout."""
    icon = {"STABLE": "✅", "MINOR_DRIFT": "⚠️ ",
            "MODERATE_DRIFT": "🟠", "SIGNIFICANT_DRIFT": "🔴"}.get(result["verdict"], "❓")

    print(f"\n{'═' * 60}")
    print(f"  🔍 Agent Drift Report")
    print(f"{'═' * 60}")
    print(f"  Verdict:   {icon} {result['verdict']}")
    print(f"  Grade:     {result['grade']}")
    print(f"  Drift:     {result['avg_drift_score']:.2f}σ (threshold: {result['threshold']}σ)")
    print(f"  Baseline:  {result['baseline_sessions']} sessions")
    print(f"  Recent:    {result['recent_sessions']} sessions")
    print(f"{'─' * 60}")
    print()

    # Dimension table
    print(f"  {'Metric':<20} {'Status':<8} {'Baseline':<12} {'Recent':<12} {'Δ%':<8} {'Z-score':<8}")
    print(f"  {'─' * 20} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 8} {'─' * 8}")

    for d in result["dimensions"]:
        icon = _DRIFT_ICONS.get(d["status"], "  ")
        direction = ""
        if d.get("direction") == "up":
            direction = "↑"
        elif d.get("direction") == "down":
            direction = "↓"

        print(f"  {d['label']:<20} {icon:<8} {d['baseline_mean']:<12.2f} "
              f"{d['recent_mean']:<12.2f} {d['pct_change']:>+6.1f}% {d['z_score']:>+5.2f}σ {direction}")

    print()

    # Recommendations
    majors = [d for d in result["dimensions"] if d["status"] == "major"]
    minors = [d for d in result["dimensions"] if d["status"] == "minor"]

    if majors:
        print("  🚨 Major drift detected in:")
        for d in majors:
            print(f"     • {d['label']}: {d['pct_change']:+.1f}% ({d['z_score']:+.2f}σ)")
        print()

    if minors:
        print("  ⚠️  Minor drift in:")
        for d in minors:
            print(f"     • {d['label']}: {d['pct_change']:+.1f}% ({d['z_score']:+.2f}σ)")
        print()

    if not majors and not minors:
        print("  ✅ All metrics within normal range. No action needed.")
        print()

    print(f"{'═' * 60}\n")


# ── CLI handler ──────────────────────────────────────────────────────

def cmd_drift(args: argparse.Namespace) -> None:
    """Execute the drift analysis command."""
    client, endpoint = get_client(args)
    threshold = getattr(args, "threshold", 2.0) or 2.0
    baseline_days = getattr(args, "baseline_days", 14) or 14
    recent_days = getattr(args, "recent_days", 3) or 3
    agent = getattr(args, "agent", None)

    now = datetime.now(timezone.utc)
    baseline_start = (now - timedelta(days=baseline_days + recent_days)).isoformat()
    baseline_end = (now - timedelta(days=recent_days)).isoformat()
    recent_start = (now - timedelta(days=recent_days)).isoformat()
    recent_end = now.isoformat()

    # Fetch baseline sessions
    params: dict[str, Any] = {
        "start": baseline_start,
        "end": baseline_end,
        "limit": 200,
    }
    if agent:
        params["agent"] = agent

    try:
        resp = client.get(f"{endpoint}/api/sessions", params=params)
        resp.raise_for_status()
        baseline_sessions = resp.json().get("sessions", resp.json() if isinstance(resp.json(), list) else [])
    except httpx.HTTPError as e:
        print(f"Error fetching baseline sessions: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch recent sessions
    params["start"] = recent_start
    params["end"] = recent_end

    try:
        resp = client.get(f"{endpoint}/api/sessions", params=params)
        resp.raise_for_status()
        recent_sessions = resp.json().get("sessions", resp.json() if isinstance(resp.json(), list) else [])
    except httpx.HTTPError as e:
        print(f"Error fetching recent sessions: {e}", file=sys.stderr)
        sys.exit(1)

    if not baseline_sessions:
        print("⚠️  No baseline sessions found. Run your agent for a few days first.", file=sys.stderr)
        sys.exit(1)

    if not recent_sessions:
        print("⚠️  No recent sessions found.", file=sys.stderr)
        sys.exit(1)

    result = _analyze_drift(baseline_sessions, recent_sessions, threshold)

    if agent:
        result["agent"] = agent

    if getattr(args, "json", False):
        print_json(result)
    else:
        _print_drift_report(result)


# ── Parser registration ──────────────────────────────────────────────

def register_drift_parser(subparsers: Any) -> None:
    """Register the drift subcommand."""
    p = subparsers.add_parser(
        "drift",
        help="Detect behavioral drift by comparing recent sessions against a baseline period",
    )
    p.add_argument("--agent", help="Filter to specific agent name")
    p.add_argument("--baseline-days", type=int, default=14,
                   help="Number of days for baseline period (default: 14)")
    p.add_argument("--recent-days", type=int, default=3,
                   help="Number of days for recent period (default: 3)")
    p.add_argument("--threshold", type=float, default=2.0,
                   help="Z-score threshold for drift detection (default: 2.0)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_drift)
