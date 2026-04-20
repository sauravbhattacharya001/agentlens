"""Analytics CLI commands — extracted from cli.py to reduce module size.

Contains cmd_report and cmd_outlier, both of which are self-contained
analytics commands that share the report-generation / stats-analysis theme.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from agentlens.cli_common import get_client as _get_client, print_json as _print_json


# ── cmd_report ───────────────────────────────────────────────────────


def cmd_report(args: argparse.Namespace) -> None:
    """Generate a summary report for sessions over a time period."""
    from datetime import datetime, timedelta, timezone

    client, endpoint = _get_client(args)
    period = getattr(args, "period", "day") or "day"
    fmt = getattr(args, "format", "table") or "table"
    output = getattr(args, "output", None)

    # Calculate time range
    now = datetime.now(timezone.utc)
    period_days = {"day": 1, "week": 7, "month": 30}[period]
    since = now - timedelta(days=period_days)
    period_label = {"day": "Daily", "week": "Weekly", "month": "Monthly"}[period]

    # Fetch all sessions
    resp = client.get("/sessions", params={"limit": 500})
    resp.raise_for_status()
    data = resp.json()
    all_sessions = data if isinstance(data, list) else data.get("sessions", [data])

    # Filter to time range
    sessions = []
    for s in all_sessions:
        created = s.get("created_at", "")
        if created:
            try:
                ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if ts >= since:
                    sessions.append(s)
            except (ValueError, TypeError):
                sessions.append(s)
        else:
            sessions.append(s)

    # Aggregate stats
    total_sessions = len(sessions)
    total_events = sum(s.get("event_count", 0) or 0 for s in sessions)
    total_tokens = sum(s.get("total_tokens", 0) or 0 for s in sessions)
    total_errors = sum(s.get("error_count", 0) or 0 for s in sessions)

    # Status breakdown
    status_counts: dict[str, int] = {}
    for s in sessions:
        st = str(s.get("status", "unknown") or "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

    # Agent breakdown
    agent_counts: dict[str, dict[str, Any]] = {}
    for s in sessions:
        agent = str(s.get("agent_name", "") or "unknown")
        if agent not in agent_counts:
            agent_counts[agent] = {"sessions": 0, "events": 0, "tokens": 0, "errors": 0}
        agent_counts[agent]["sessions"] += 1
        agent_counts[agent]["events"] += s.get("event_count", 0) or 0
        agent_counts[agent]["tokens"] += s.get("total_tokens", 0) or 0
        agent_counts[agent]["errors"] += s.get("error_count", 0) or 0

    # Fetch cost data (sample up to 20 sessions)
    total_cost = 0.0
    model_costs: dict[str, float] = {}
    cost_sessions = sessions[:20]
    for s in cost_sessions:
        sid = s.get("id", "")
        if not sid:
            continue
        try:
            cr = client.get(f"/sessions/{sid}/costs")
            cr.raise_for_status()
            cd = cr.json()
            cost = cd.get("total_cost", 0) or 0
            total_cost += cost
            for model, mc in (cd.get("model_costs", {}) or {}).items():
                c = mc.get("total", mc) if isinstance(mc, dict) else mc
                model_costs[model] = model_costs.get(model, 0) + (c or 0)
        except httpx.HTTPError:
            pass

    # Extrapolate cost if we sampled
    if len(cost_sessions) < total_sessions and len(cost_sessions) > 0:
        factor = total_sessions / len(cost_sessions)
        total_cost *= factor
        model_costs = {k: v * factor for k, v in model_costs.items()}

    error_rate = (total_errors / total_events * 100) if total_events > 0 else 0

    # Sort agents by sessions desc
    top_agents = sorted(agent_counts.items(), key=lambda x: x[1]["sessions"], reverse=True)[:10]

    # Build report
    report: dict[str, Any] = {
        "title": f"{period_label} Report",
        "period": period,
        "from": since.isoformat(),
        "to": now.isoformat(),
        "endpoint": endpoint,
        "summary": {
            "total_sessions": total_sessions,
            "total_events": total_events,
            "total_tokens": total_tokens,
            "total_errors": total_errors,
            "error_rate_pct": round(error_rate, 2),
            "estimated_cost": round(total_cost, 4),
        },
        "status_breakdown": status_counts,
        "model_costs": {k: round(v, 4) for k, v in sorted(model_costs.items(), key=lambda x: x[1], reverse=True)},
        "top_agents": [
            {"agent": a, **stats} for a, stats in top_agents
        ],
    }

    if fmt == "json":
        text = json.dumps(report, indent=2, default=str)
    elif fmt == "markdown":
        lines = [
            f"# 📊 AgentLens {period_label} Report",
            f"",
            f"**Period:** {since.strftime('%Y-%m-%d %H:%M')} → {now.strftime('%Y-%m-%d %H:%M')} UTC",
            f"**Endpoint:** {endpoint}",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Sessions | {total_sessions} |",
            f"| Events | {total_events:,} |",
            f"| Tokens | {total_tokens:,} |",
            f"| Errors | {total_errors} |",
            f"| Error Rate | {error_rate:.1f}% |",
            f"| Est. Cost | ${total_cost:.4f} |",
            f"",
            f"## Status Breakdown",
            f"",
        ]
        for st, cnt in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- **{st}**: {cnt}")
        lines.append("")

        if model_costs:
            lines.append("## Cost by Model")
            lines.append("")
            lines.append("| Model | Cost |")
            lines.append("|-------|------|")
            for m, c in sorted(model_costs.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {m} | ${c:.4f} |")
            lines.append("")

        if top_agents:
            lines.append("## Top Agents")
            lines.append("")
            lines.append("| Agent | Sessions | Events | Tokens | Errors |")
            lines.append("|-------|----------|--------|--------|--------|")
            for a, stats in top_agents:
                lines.append(f"| {a} | {stats['sessions']} | {stats['events']} | {stats['tokens']:,} | {stats['errors']} |")
            lines.append("")

        text = "\n".join(lines)
    else:
        # table format
        lines = [
            f"╔══════════════════════════════════════════════════╗",
            f"║  📊 AgentLens {period_label} Report{' ' * (34 - len(period_label))}║",
            f"╠══════════════════════════════════════════════════╣",
            f"║  Period: {since.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}{' ' * 17}║",
            f"╚══════════════════════════════════════════════════╝",
            f"",
            f"  Sessions:    {total_sessions}",
            f"  Events:      {total_events:,}",
            f"  Tokens:      {total_tokens:,}",
            f"  Errors:      {total_errors}  ({error_rate:.1f}%)",
            f"  Est. Cost:   ${total_cost:.4f}",
            f"",
        ]

        if status_counts:
            lines.append("  Status Breakdown:")
            for st, cnt in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
                pct = cnt / total_sessions * 100 if total_sessions > 0 else 0
                bar_len = int(pct / 5)
                lines.append(f"    {st:<15} {cnt:>5}  {'█' * bar_len}{'░' * (20 - bar_len)} {pct:.0f}%")
            lines.append("")

        if model_costs:
            lines.append("  Cost by Model:")
            for m, c in sorted(model_costs.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"    {m:<30} ${c:.4f}")
            lines.append("")

        if top_agents:
            lines.append("  Top Agents:")
            lines.append(f"    {'AGENT':<20} {'SESS':>5} {'EVENTS':>7} {'TOKENS':>10} {'ERRORS':>6}")
            lines.append(f"    {'─' * 20} {'─' * 5} {'─' * 7} {'─' * 10} {'─' * 6}")
            for a, stats in top_agents:
                lines.append(f"    {a:<20} {stats['sessions']:>5} {stats['events']:>7} {stats['tokens']:>10,} {stats['errors']:>6}")
            lines.append("")

        text = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Report written to {output}")
    else:
        print(text)


# ── cmd_outlier ──────────────────────────────────────────────────────


def cmd_outlier(args: argparse.Namespace) -> None:
    """Detect outlier sessions using IQR-based anomaly detection.

    Fetches recent sessions and identifies statistical outliers by cost,
    token usage, duration, or error count.
    """

    import statistics
    from datetime import datetime

    client, _ = _get_client(args)
    resp = client.get("/sessions", params={"limit": args.limit})
    resp.raise_for_status()
    data = resp.json()
    sessions = data if isinstance(data, list) else data.get("sessions", [data])

    if len(sessions) < 4:
        print("⚠️  Need at least 4 sessions for outlier detection.")
        return

    def _dur(s: dict) -> float | None:
        start_raw = s.get("started_at") or s.get("created_at")
        end_raw = s.get("ended_at")
        if not start_raw or not end_raw:
            return None
        try:
            start_s = str(start_raw).replace("Z", "+00:00")
            end_s = str(end_raw).replace("Z", "+00:00")
            start = datetime.fromisoformat(start_s)
            end = datetime.fromisoformat(end_s)
            return max((end - start).total_seconds(), 0.0)
        except (ValueError, TypeError):
            return None

    metric_extractors: dict[str, Any] = {
        "cost": lambda s: s.get("total_cost") or s.get("cost") or 0.0,
        "tokens": lambda s: (s.get("total_tokens_in", 0) or 0) + (s.get("total_tokens_out", 0) or 0) + (s.get("total_tokens", 0) or 0),
        "duration": lambda s: _dur(s),
        "errors": lambda s: s.get("error_count", 0) or len([e for e in s.get("events", []) if isinstance(e, dict) and e.get("event_type") == "error"]),
    }

    metrics_to_check = list(metric_extractors.keys()) if args.metric == "all" else [args.metric]

    def _iqr_outliers(
        values: list[tuple[dict, float]],
        multiplier: float,
    ) -> list[tuple[dict, float, str]]:
        nums = [v for _, v in values]
        if len(nums) < 4:
            return []
        sorted_nums = sorted(nums)
        n = len(sorted_nums)
        q1 = sorted_nums[n // 4]
        q3 = sorted_nums[(3 * n) // 4]
        iqr = q3 - q1
        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr
        results: list[tuple[dict, float, str]] = []
        for sess, val in values:
            if val > upper:
                results.append((sess, val, f"above Q3+{multiplier}×IQR ({upper:.4f})"))
            elif val < lower and lower > 0:
                results.append((sess, val, f"below Q1-{multiplier}×IQR ({lower:.4f})"))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:args.top]

    all_outliers: dict[str, list[tuple[dict, float, str]]] = {}
    summary_stats: dict[str, dict[str, float]] = {}

    for metric in metrics_to_check:
        extractor = metric_extractors[metric]
        pairs: list[tuple[dict, float]] = []
        for s in sessions:
            val = extractor(s)
            if val is not None:
                pairs.append((s, float(val)))

        if len(pairs) < 4:
            continue

        nums = [v for _, v in pairs]
        summary_stats[metric] = {
            "count": len(nums),
            "mean": statistics.mean(nums),
            "median": statistics.median(nums),
            "stdev": statistics.stdev(nums) if len(nums) > 1 else 0.0,
            "min": min(nums),
            "max": max(nums),
        }

        outliers = _iqr_outliers(pairs, args.threshold)
        if outliers:
            all_outliers[metric] = outliers

    # Output
    if args.format == "json":
        output: dict[str, Any] = {
            "threshold": args.threshold,
            "sessions_analyzed": len(sessions),
            "metrics": {},
        }
        for metric in metrics_to_check:
            entry: dict[str, Any] = {"stats": summary_stats.get(metric, {})}
            if metric in all_outliers:
                entry["outliers"] = [
                    {
                        "session_id": s.get("session_id") or s.get("id", "?"),
                        "agent": s.get("agent_name", ""),
                        "value": val,
                        "reason": reason,
                    }
                    for s, val, reason in all_outliers[metric]
                ]
            output["metrics"][metric] = entry
        _print_json(output)
        return

    total_outliers = sum(len(v) for v in all_outliers.values())
    print(f"🔍 Outlier Detection — {len(sessions)} sessions, IQR×{args.threshold}")
    print()

    for metric in metrics_to_check:
        stats = summary_stats.get(metric)
        if not stats:
            continue

        unit = {"cost": "$", "tokens": " tok", "duration": "s", "errors": " err"}[metric]
        fmt_v = (lambda v, u=unit: f"${v:.4f}" if u == "$" else f"{v:,.1f}{u}")

        print(f"── {metric.upper()} ──")
        print(f"   mean={fmt_v(stats['mean'])}  median={fmt_v(stats['median'])}  "
              f"stdev={fmt_v(stats['stdev'])}  min={fmt_v(stats['min'])}  max={fmt_v(stats['max'])}")

        outliers = all_outliers.get(metric, [])
        if not outliers:
            print("   ✅ No outliers detected\n")
            continue

        print(f"   ⚠️  {len(outliers)} outlier(s):")
        for sess, val, reason in outliers:
            sid = sess.get("session_id") or sess.get("id", "?")
            agent = sess.get("agent_name", "")
            label = f"{sid[:12]}"
            if agent:
                label += f" ({agent})"
            print(f"   • {label}: {fmt_v(val)} — {reason}")
        print()

    if total_outliers == 0:
        print("✅ No outliers detected across all metrics. Your agents look healthy!")
    else:
        print(f"⚠️  {total_outliers} total outlier(s) detected. Investigate high-value sessions with:")
        print("   agentlens-cli session <session_id>")
        print("   agentlens-cli costs <session_id>")
        print("   agentlens-cli trace <session_id>")
