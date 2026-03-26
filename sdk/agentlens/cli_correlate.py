"""CLI ``correlate`` command — find statistical correlations between session metrics.

Usage:
    agentlens-cli correlate [--metrics METRICS] [--limit N] [--min-sessions N]
                            [--format table|json|csv] [--output FILE]
                            [--endpoint URL] [--api-key KEY]

Fetches sessions and computes pairwise Pearson correlation coefficients between
numeric metrics (cost, tokens, duration, events, errors, tool_calls, models).
Helps answer questions like "do longer sessions cost more?" or "are errors
correlated with token usage?".

Examples:
    agentlens-cli correlate
    agentlens-cli correlate --metrics cost,duration,errors --format json
    agentlens-cli correlate --limit 500 --output correlations.csv --format csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import datetime, timezone
from typing import Any

from agentlens.cli_common import get_client, print_json, fetch_sessions

ALL_METRICS = ["cost", "tokens", "duration", "events", "errors", "tool_calls", "models"]


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Compute Pearson correlation coefficient. Returns None if undefined."""
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _extract_metric(session: dict, metric: str) -> float | None:
    """Extract a numeric metric value from a session dict."""
    if metric == "cost":
        return session.get("total_cost") or session.get("cost") or 0.0
    if metric == "tokens":
        return (
            session.get("total_tokens")
            or session.get("tokens")
            or (session.get("prompt_tokens", 0) + session.get("completion_tokens", 0))
        )
    if metric == "duration":
        start = session.get("started_at") or session.get("created_at")
        end = session.get("ended_at") or session.get("updated_at")
        if start and end:
            try:
                t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                return (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass
        return session.get("duration_ms", 0) / 1000.0 if session.get("duration_ms") else None
    if metric == "events":
        return session.get("event_count") or session.get("total_events") or 0
    if metric == "errors":
        return session.get("error_count") or session.get("errors") or 0
    if metric == "tool_calls":
        return session.get("tool_call_count") or session.get("tool_calls") or 0
    if metric == "models":
        models = session.get("models") or session.get("model_list") or []
        if isinstance(models, list):
            return len(models)
        return 1 if models else 0
    return None


def _strength_label(r: float) -> str:
    """Human-readable correlation strength."""
    ar = abs(r)
    if ar >= 0.8:
        return "strong"
    if ar >= 0.5:
        return "moderate"
    if ar >= 0.3:
        return "weak"
    return "negligible"


def _format_table(results: list[dict]) -> str:
    """Render correlation results as a formatted ASCII table."""
    lines = []
    header = f"{'Metric A':<14} {'Metric B':<14} {'r':>8} {'Strength':<12} {'N':>5}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in results:
        r_val = row["r"]
        r_str = f"{r_val:+.4f}" if r_val is not None else "   N/A"
        strength = row.get("strength", "")
        lines.append(
            f"{row['metric_a']:<14} {row['metric_b']:<14} {r_str:>8} {strength:<12} {row['n']:>5}"
        )
    return "\n".join(lines)


def _format_csv(results: list[dict]) -> str:
    """Render results as CSV."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["metric_a", "metric_b", "r", "strength", "n"])
    writer.writeheader()
    for row in results:
        writer.writerow(row)
    return buf.getvalue()


def run(args: argparse.Namespace) -> None:
    """Execute the ``correlate`` sub-command."""
    client, endpoint = get_client(args)
    limit = getattr(args, "limit", 200) or 200
    min_sessions = getattr(args, "min_sessions", 10) or 10
    fmt = getattr(args, "format", "table") or "table"
    output = getattr(args, "output", None)

    metrics_str = getattr(args, "metrics", None)
    if metrics_str:
        metrics = [m.strip() for m in metrics_str.split(",") if m.strip() in ALL_METRICS]
    else:
        metrics = list(ALL_METRICS)

    if len(metrics) < 2:
        print("Error: need at least 2 metrics to correlate.", file=sys.stderr)
        sys.exit(1)

    # Fetch sessions
    sessions = fetch_sessions(client, limit=limit)
    if len(sessions) < min_sessions:
        print(
            f"Only {len(sessions)} sessions found (minimum {min_sessions}). "
            "Use --min-sessions to lower the threshold.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract metric vectors
    vectors: dict[str, list[tuple[int, float]]] = {}
    for metric in metrics:
        vals = []
        for i, s in enumerate(sessions):
            v = _extract_metric(s, metric)
            if v is not None:
                vals.append((i, v))
        vectors[metric] = vals

    # Compute pairwise correlations
    results: list[dict] = []
    for i, ma in enumerate(metrics):
        for mb in metrics[i + 1 :]:
            # Align on common session indices
            set_a = {idx: v for idx, v in vectors[ma]}
            set_b = {idx: v for idx, v in vectors[mb]}
            common = sorted(set_a.keys() & set_b.keys())
            xs = [set_a[k] for k in common]
            ys = [set_b[k] for k in common]
            r = _pearson(xs, ys)
            results.append({
                "metric_a": ma,
                "metric_b": mb,
                "r": round(r, 4) if r is not None else None,
                "strength": _strength_label(r) if r is not None else "N/A",
                "n": len(common),
            })

    # Sort by absolute correlation (strongest first)
    results.sort(key=lambda x: abs(x["r"]) if x["r"] is not None else -1, reverse=True)

    # Output
    if fmt == "json":
        text = json.dumps(results, indent=2)
    elif fmt == "csv":
        text = _format_csv(results)
    else:
        print(f"\nCorrelation Matrix — {len(sessions)} sessions analyzed\n")
        text = _format_table(results)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Written to {output}")
    else:
        print(text)


def setup_parser(sub: Any) -> None:
    """Register the ``correlate`` sub-command on *sub*."""
    p = sub.add_parser("correlate", help="Find correlations between session metrics")
    p.add_argument("--metrics", help=f"Comma-separated metrics ({','.join(ALL_METRICS)})")
    p.add_argument("--limit", type=int, default=200, help="Max sessions to fetch (default 200)")
    p.add_argument("--min-sessions", type=int, default=10, help="Minimum sessions required")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--output", help="Write output to file")
    p.add_argument("--endpoint", help="AgentLens API endpoint")
    p.add_argument("--api-key", help="API key")
