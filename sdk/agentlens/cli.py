"""AgentLens CLI — query your AgentLens backend from the command line.

Usage:
    agentlens-cli sessions [--limit N] [--endpoint URL] [--api-key KEY]
    agentlens-cli session <session_id> [--endpoint URL] [--api-key KEY]
    agentlens-cli costs <session_id> [--endpoint URL] [--api-key KEY]
    agentlens-cli events [--session SESSION] [--type TYPE] [--model MODEL] [--limit N] [--endpoint URL] [--api-key KEY]
    agentlens-cli export <session_id> [--format json|csv] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli analytics [--endpoint URL] [--api-key KEY]
    agentlens-cli health <session_id> [--endpoint URL] [--api-key KEY]
    agentlens-cli compare <session_a> <session_b> [--endpoint URL] [--api-key KEY]
    agentlens-cli alerts [--endpoint URL] [--api-key KEY]
    agentlens-cli postmortem <session_id> [--endpoint URL] [--api-key KEY]
    agentlens-cli postmortem candidates [--min-errors N] [--limit N] [--endpoint URL] [--api-key KEY]
    agentlens-cli tail [--session SESSION] [--type TYPE] [--interval SECS] [--endpoint URL] [--api-key KEY]
    agentlens-cli top [--sort cost|tokens|events] [--limit N] [--interval SECS] [--endpoint URL] [--api-key KEY]
    agentlens-cli report [--period day|week|month] [--format table|json|markdown] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli flamegraph <session_id> [--output FILE] [--open] [--stats] [--endpoint URL] [--api-key KEY]
    agentlens-cli dashboard [--limit N] [--output FILE] [--open] [--endpoint URL] [--api-key KEY]
    agentlens-cli trace <session_id> [--no-color] [--json] [--type TYPE] [--min-ms N] [--endpoint URL] [--api-key KEY]
    agentlens-cli status [--endpoint URL] [--api-key KEY]

Environment variables:
    AGENTLENS_ENDPOINT  Backend URL (default: http://localhost:3000)
    AGENTLENS_API_KEY   API key (default: default)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _get_client(args: argparse.Namespace) -> tuple[httpx.Client, str]:
    endpoint = (
        getattr(args, "endpoint", None)
        or os.environ.get("AGENTLENS_ENDPOINT", "http://localhost:3000")
    ).rstrip("/")
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("AGENTLENS_API_KEY", "default")
    )
    client = httpx.Client(
        base_url=endpoint,
        headers={"x-api-key": api_key},
        timeout=15.0,
    )
    return client, endpoint


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_table(rows: list[dict], columns: list[str], *, max_width: int = 40) -> None:
    """Print a simple ASCII table."""
    if not rows:
        print("(no data)")
        return

    def trunc(val: Any, width: int) -> str:
        s = str(val) if val is not None else ""
        return s[:width] if len(s) > width else s

    widths = {c: max(len(c), *(len(trunc(r.get(c), max_width)) for r in rows)) for c in columns}
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    print(header)
    print(sep)
    for row in rows:
        line = " | ".join(trunc(row.get(c), max_width).ljust(widths[c]) for c in columns)
        print(line)


# ── Commands ─────────────────────────────────────────────────────────


def cmd_sessions(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    params: dict[str, Any] = {}
    if args.limit:
        params["limit"] = args.limit
    resp = client.get("/sessions", params=params)
    resp.raise_for_status()
    data = resp.json()
    sessions = data if isinstance(data, list) else data.get("sessions", [data])
    _print_table(
        sessions,
        ["id", "agent_name", "status", "event_count", "total_tokens", "created_at"],
    )


def cmd_session(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    _print_json(resp.json())


def cmd_costs(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get(f"/sessions/{args.session_id}/costs")
    resp.raise_for_status()
    data = resp.json()
    print(f"Session: {args.session_id}")
    print(f"Total cost: ${data.get('total_cost', 0):.6f}")
    print(f"  Input:    ${data.get('total_input_cost', 0):.6f}")
    print(f"  Output:   ${data.get('total_output_cost', 0):.6f}")
    models = data.get("model_costs", {})
    if models:
        print("\nBy model:")
        for model, cost_info in models.items():
            if isinstance(cost_info, dict):
                print(f"  {model}: ${cost_info.get('total', 0):.6f}")
            else:
                print(f"  {model}: ${cost_info:.6f}")


def cmd_events(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    params: dict[str, Any] = {}
    if args.session:
        params["session_id"] = args.session
    if args.type:
        params["type"] = args.type
    if args.model:
        params["model"] = args.model
    if args.limit:
        params["limit"] = args.limit
    resp = client.get("/events", params=params)
    resp.raise_for_status()
    data = resp.json()
    events = data if isinstance(data, list) else data.get("events", [data])
    _print_table(
        events,
        ["id", "session_id", "event_type", "model", "tokens_in", "tokens_out", "duration_ms"],
    )


def cmd_export(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    fmt = args.format or "json"
    resp = client.get(f"/sessions/{args.session_id}/export", params={"format": fmt})
    resp.raise_for_status()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            if fmt == "json":
                json.dump(resp.json(), f, indent=2, default=str)
            else:
                f.write(resp.text)
        print(f"Exported to {args.output}")
    else:
        if fmt == "json":
            _print_json(resp.json())
        else:
            print(resp.text)


def cmd_analytics(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get("/analytics")
    resp.raise_for_status()
    data = resp.json()
    stats = data.get("stats", data)
    print("=== AgentLens Analytics ===")
    for key, val in (stats if isinstance(stats, dict) else {}).items():
        print(f"  {key}: {val}")


def cmd_health(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()
    events = session_data.get("events", [])

    # Use local health scorer
    from agentlens import HealthScorer
    scorer = HealthScorer()
    from agentlens.models import AgentEvent
    parsed = []
    for e in events:
        try:
            parsed.append(AgentEvent(**e))
        except Exception:
            pass
    if not parsed:
        print(f"Session {args.session_id}: no events to score")
        return
    report = scorer.score(parsed)
    print(f"Session: {args.session_id}")
    print(f"Grade:   {report.grade.value}")
    print(f"Score:   {report.overall_score:.1f}/100")
    print(f"Events:  {report.event_count}")
    print("\nMetrics:")
    for m in report.metrics:
        print(f"  {m.name}: {m.score:.1f} (weight: {m.weight})")


def cmd_compare(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get(
        "/sessions/compare",
        params={"a": args.session_a, "b": args.session_b},
    )
    resp.raise_for_status()
    _print_json(resp.json())


def cmd_alerts(args: argparse.Namespace) -> None:
    client, _ = _get_client(args)
    resp = client.get("/alerts")
    resp.raise_for_status()
    data = resp.json()
    alerts = data if isinstance(data, list) else data.get("alerts", [data])
    _print_table(alerts, ["id", "rule_id", "severity", "message", "created_at"])


def cmd_postmortem(args: argparse.Namespace) -> None:
    """Generate an incident postmortem for a session, or list candidate sessions."""
    client, _ = _get_client(args)

    if args.candidates:
        params: dict[str, Any] = {}
        if args.min_errors:
            params["min_errors"] = args.min_errors
        if args.limit:
            params["limit"] = args.limit
        resp = client.get("/postmortem/candidates", params=params)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            print("No sessions with enough errors for a postmortem.")
            return
        _print_table(candidates, ["session_id", "agent_name", "started_at", "error_count"])
        return

    if not args.session_id:
        print("Error: provide a session ID or use --candidates to list candidates.", file=sys.stderr)
        sys.exit(1)

    resp = client.post(f"/postmortem/{args.session_id}")
    resp.raise_for_status()
    report = resp.json()

    if report.get("incident_id") == "INC-NONE":
        print(f"✅ Session {args.session_id}: No incidents detected.")
        print(f"   Events analysed: {report.get('event_count', 0)}")
        return

    # Pretty-print the postmortem
    severity = report.get("severity", "?")
    sev_colors = {"SEV-1": "🔴", "SEV-2": "🟠", "SEV-3": "🟡", "SEV-4": "🟢"}
    icon = sev_colors.get(severity, "⚪")

    print(f"\n{'='*60}")
    print(f"  {icon} INCIDENT POSTMORTEM — {report.get('incident_id', '')}")
    print(f"{'='*60}")
    print(f"\n  Title:    {report.get('title', '')}")
    print(f"  Severity: {severity}")
    print(f"  Session:  {report.get('session_id', '')}")
    print(f"  Duration: {_format_duration(report.get('duration_ms', 0))}")
    print(f"  Generated: {report.get('generated_at', '')}")
    print(f"\n  Summary: {report.get('summary', '')}")

    # Impact
    impact = report.get("impact", {})
    if impact:
        print(f"\n  {'─'*50}")
        print(f"  IMPACT")
        print(f"  {'─'*50}")
        print(f"    Errors:        {impact.get('error_count', 0)} / {impact.get('total_events', 0)} events ({_pct(impact.get('error_rate', 0))})")
        print(f"    Tokens wasted: {impact.get('tokens_wasted', 0):,}")
        print(f"    Est. cost:     ${impact.get('estimated_cost_impact', 0):.4f}")
        print(f"    Downtime:      {_format_duration(impact.get('downtime_ms', 0))}")
        tools = impact.get("affected_tools", [])
        if tools:
            print(f"    Tools:         {', '.join(tools)}")
        models = impact.get("affected_models", [])
        if models:
            print(f"    Models:        {', '.join(models)}")
        if impact.get("user_facing"):
            print(f"    ⚠ User-facing errors detected")

    # Root causes
    root_causes = report.get("root_causes", [])
    if root_causes:
        print(f"\n  {'─'*50}")
        print(f"  ROOT CAUSES")
        print(f"  {'─'*50}")
        for i, rc in enumerate(root_causes, 1):
            conf = rc.get("confidence", 0)
            conf_bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
            print(f"    {i}. {rc.get('description', '')}")
            print(f"       Confidence: [{conf_bar}] {conf:.0%}")
            print(f"       Category:   {rc.get('category', '')}")
            print(f"       Affected:   {rc.get('affected_events', 0)} events")
            for ev in rc.get("evidence", []):
                print(f"       • {ev}")

    # Timeline
    timeline = report.get("timeline", [])
    if timeline:
        print(f"\n  {'─'*50}")
        print(f"  TIMELINE")
        print(f"  {'─'*50}")
        for entry in timeline:
            sev_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(entry.get("severity", ""), "•")
            elapsed = _format_duration(entry.get("elapsed_ms", 0))
            print(f"    {sev_icon} +{elapsed:<10} {entry.get('description', '')}")

    print(f"\n{'='*60}\n")


def _format_duration(ms: Any) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms is None:
        return "—"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    if mins < 60:
        return f"{mins:.1f}m"
    hours = mins / 60
    return f"{hours:.1f}h"


def _pct(rate: Any) -> str:
    """Format a 0-1 rate as a percentage string."""
    if rate is None:
        return "0%"
    return f"{float(rate) * 100:.1f}%"


def cmd_tail(args: argparse.Namespace) -> None:
    """Live-follow events for a session, like ``tail -f`` for agent traces."""
    import time as _time

    client, endpoint = _get_client(args)
    interval = args.interval
    session_filter = args.session or None
    type_filter = args.type or None
    seen: set[str] = set()

    # Fetch initial events to populate seen set (avoid replaying history)
    params: dict[str, Any] = {"limit": 200}
    if session_filter:
        params["session_id"] = session_filter
    if type_filter:
        params["type"] = type_filter
    try:
        resp = client.get("/events", params=params)
        resp.raise_for_status()
        data = resp.json()
        initial = data if isinstance(data, list) else data.get("events", [])
        for ev in initial:
            eid = ev.get("event_id") or ev.get("id") or ""
            if eid:
                seen.add(eid)
        print(f"🔍 Tailing events at {endpoint} (interval={interval}s, Ctrl+C to stop)")
        if session_filter:
            print(f"   Session filter: {session_filter}")
        if type_filter:
            print(f"   Type filter: {type_filter}")
        print(f"   Skipped {len(seen)} existing events\n")
    except httpx.HTTPError:
        print(f"🔍 Tailing events at {endpoint} (interval={interval}s, Ctrl+C to stop)\n")

    def _format_event(ev: dict) -> str:
        ts = ev.get("timestamp", "")
        etype = ev.get("event_type", ev.get("type", "?"))
        model = ev.get("model", "")
        tok_in = ev.get("tokens_in", 0) or 0
        tok_out = ev.get("tokens_out", 0) or 0
        dur = ev.get("duration_ms")
        sid = ev.get("session_id", "")[:8]

        parts = [f"[{ts}]", f"{etype}"]
        if sid:
            parts.append(f"sess={sid}…")
        if model:
            parts.append(f"model={model}")
        if tok_in or tok_out:
            parts.append(f"tokens={tok_in}→{tok_out}")
        if dur is not None:
            parts.append(f"{dur}ms")
        return " ".join(parts)

    try:
        while True:
            _time.sleep(interval)
            try:
                resp = client.get("/events", params=params)
                resp.raise_for_status()
                data = resp.json()
                events = data if isinstance(data, list) else data.get("events", [])
                new_events = []
                for ev in events:
                    eid = ev.get("event_id") or ev.get("id") or ""
                    if eid and eid not in seen:
                        seen.add(eid)
                        new_events.append(ev)
                for ev in new_events:
                    print(_format_event(ev))
            except httpx.HTTPError as exc:
                print(f"⚠ poll error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n👋 Stopped tailing.")


def cmd_top(args: argparse.Namespace) -> None:
    """Live leaderboard of sessions ranked by cost, tokens, or event count — like htop for agents."""
    import time as _time

    client, endpoint = _get_client(args)
    interval = args.interval
    sort_key = args.sort
    limit = args.limit

    sort_field_map = {
        "cost": "total_cost",
        "tokens": "total_tokens",
        "events": "event_count",
    }
    sort_field = sort_field_map.get(sort_key, "total_cost")

    def _bar(value: float, max_val: float, width: int = 20) -> str:
        if max_val <= 0:
            return " " * width
        filled = int(round(value / max_val * width))
        filled = min(filled, width)
        return "█" * filled + "░" * (width - filled)

    def _fetch_and_display() -> None:
        try:
            resp = client.get("/sessions", params={"limit": limit * 2})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"⚠ Error fetching sessions: {exc}", file=sys.stderr)
            return

        data = resp.json()
        sessions = data if isinstance(data, list) else data.get("sessions", [data])

        # Enrich with cost data if sorting by cost
        if sort_key == "cost":
            for s in sessions:
                if "total_cost" not in s:
                    sid = s.get("id", "")
                    try:
                        cr = client.get(f"/sessions/{sid}/costs")
                        cr.raise_for_status()
                        cost_data = cr.json()
                        s["total_cost"] = cost_data.get("total_cost", 0)
                    except httpx.HTTPError:
                        s["total_cost"] = 0

        # Sort and limit
        sessions.sort(key=lambda s: s.get(sort_field, 0) or 0, reverse=True)
        sessions = sessions[:limit]

        # Clear screen
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")

        print(f"⚡ AgentLens Top — sorted by {sort_key} | {endpoint} | Ctrl+C to stop")
        print(f"   Refreshing every {interval}s\n")

        if not sessions:
            print("  (no sessions)")
            return

        max_val = max((s.get(sort_field, 0) or 0) for s in sessions) or 1

        # Header
        print(f"  {'#':<3} {'SESSION':<12} {'AGENT':<18} {'STATUS':<10} {'EVENTS':>7} {'TOKENS':>10} {'COST':>10}  {'':20}")
        print(f"  {'─'*3} {'─'*12} {'─'*18} {'─'*10} {'─'*7} {'─'*10} {'─'*10}  {'─'*20}")

        for i, s in enumerate(sessions, 1):
            sid = str(s.get("id", ""))[:12]
            agent = str(s.get("agent_name", "") or "")[:18]
            status = str(s.get("status", "") or "")[:10]
            events = s.get("event_count", 0) or 0
            tokens = s.get("total_tokens", 0) or 0
            cost = s.get("total_cost", 0) or 0
            val = s.get(sort_field, 0) or 0
            bar = _bar(val, max_val)

            cost_str = f"${cost:.4f}" if cost > 0 else "—"
            print(f"  {i:<3} {sid:<12} {agent:<18} {status:<10} {events:>7} {tokens:>10} {cost_str:>10}  {bar}")

        print(f"\n  Showing {len(sessions)} sessions")

    print(f"⚡ AgentLens Top — connecting to {endpoint}...")
    try:
        while True:
            _fetch_and_display()
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\n👋 Stopped.")


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
                sessions.append(s)  # include if can't parse
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


def cmd_flamegraph(args: argparse.Namespace) -> None:
    """Generate an interactive HTML flamegraph for a session."""
    import webbrowser as _wb
    from pathlib import Path

    from agentlens.flamegraph import Flamegraph
    from agentlens.models import AgentEvent

    client, _ = _get_client(args)

    # Fetch session metadata
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()
    session_name = session_data.get("agent_name", args.session_id)

    # Fetch events for this session
    resp = client.get("/events", params={"session_id": args.session_id, "limit": 5000})
    resp.raise_for_status()
    raw_events = resp.json()
    if isinstance(raw_events, dict):
        raw_events = raw_events.get("events", [raw_events])

    if not raw_events:
        print(f"⚠️  No events found for session {args.session_id}")
        sys.exit(1)

    # Convert raw dicts to AgentEvent objects
    events: list[AgentEvent] = []
    for raw in raw_events:
        try:
            events.append(AgentEvent(**raw))
        except Exception:
            # Skip malformed events
            continue

    print(f"📊 Building flamegraph for session {args.session_id} ({len(events)} events)...")

    fg = Flamegraph(events=events, session_name=session_name)

    if args.stats:
        stats = fg.get_stats()
        print(f"\n🔥 Flamegraph Statistics")
        print(f"   Total duration: {stats['total_ms']:.1f} ms")
        print(f"   Node count:     {stats['node_count']}")
        print(f"   Max depth:      {stats['max_depth']}")
        print(f"   Total tokens:   {stats['total_tokens']:,}")
        if stats.get("time_by_type"):
            print(f"\n   Time by event type:")
            for etype, ms in stats["time_by_type"].items():
                print(f"     {etype}: {ms:.1f} ms")
        if stats.get("slowest_events"):
            print(f"\n   Slowest events:")
            for i, ev in enumerate(stats["slowest_events"][:5], 1):
                print(f"     {i}. {ev.get('name', 'unknown')} — {ev.get('duration', 0):.1f} ms")
        return

    output = args.output or f"flamegraph-{args.session_id}.html"
    fg.save(output)
    abs_path = str(Path(output).resolve())
    print(f"✅ Flamegraph saved to {abs_path}")

    if args.open:
        _wb.open(f"file://{abs_path}")
        print("🌐 Opened in browser")


def cmd_trace(args: argparse.Namespace) -> None:
    """Render a session's events as a terminal waterfall/timeline with timing bars."""
    client, endpoint = _get_client(args)
    use_color = not getattr(args, "no_color", False) and sys.stdout.isatty()
    output_json = getattr(args, "json", False)
    type_filter = getattr(args, "type", None)
    min_ms = getattr(args, "min_ms", None)

    # ANSI helpers
    RESET = "\033[0m" if use_color else ""
    BOLD = "\033[1m" if use_color else ""
    DIM = "\033[2m" if use_color else ""
    type_colors = {
        "llm_call": "\033[38;5;117m" if use_color else "",   # blue
        "tool_call": "\033[38;5;114m" if use_color else "",   # green
        "decision": "\033[38;5;179m" if use_color else "",    # yellow
        "error": "\033[38;5;203m" if use_color else "",       # red
        "generic": "\033[38;5;145m" if use_color else "",     # grey
    }

    # Fetch session
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()

    # Fetch events
    resp = client.get("/events", params={"session_id": args.session_id, "limit": 5000})
    resp.raise_for_status()
    raw = resp.json()
    events = raw if isinstance(raw, list) else raw.get("events", [raw])

    # Sort by timestamp
    events.sort(key=lambda e: e.get("timestamp", ""))

    # Apply filters
    if type_filter:
        events = [e for e in events if e.get("event_type", e.get("type", "")) == type_filter]
    if min_ms is not None:
        events = [e for e in events if (e.get("duration_ms") or 0) >= min_ms]

    if not events:
        print(f"No events found for session {args.session_id}")
        return

    if output_json:
        trace_data = {
            "session_id": args.session_id,
            "agent": session_data.get("agent_name", "unknown"),
            "event_count": len(events),
            "events": [
                {
                    "event_id": e.get("event_id", e.get("id", "")),
                    "type": e.get("event_type", e.get("type", "")),
                    "model": e.get("model"),
                    "tokens_in": e.get("tokens_in", 0),
                    "tokens_out": e.get("tokens_out", 0),
                    "duration_ms": e.get("duration_ms"),
                    "timestamp": e.get("timestamp", ""),
                }
                for e in events
            ],
        }
        _print_json(trace_data)
        return

    # Determine time span for bar scaling
    durations = [e.get("duration_ms") or 0 for e in events]
    max_dur = max(durations) if durations else 1
    if max_dur == 0:
        max_dur = 1
    total_dur = sum(durations)
    total_tokens_in = sum(e.get("tokens_in", 0) or 0 for e in events)
    total_tokens_out = sum(e.get("tokens_out", 0) or 0 for e in events)
    error_count = sum(1 for e in events if e.get("event_type", e.get("type", "")) == "error")

    agent = session_data.get("agent_name", "unknown")
    status = session_data.get("status", "?")

    # Header
    print(f"\n{BOLD}🔎 Session Trace: {args.session_id}{RESET}")
    print(f"   Agent: {agent}  Status: {status}  Events: {len(events)}")
    print(f"   Total duration: {_format_duration(total_dur)}  Tokens: {total_tokens_in:,}→{total_tokens_out:,}", end="")
    if error_count:
        err_color = type_colors.get("error", "")
        print(f"  {err_color}Errors: {error_count}{RESET}")
    else:
        print()
    print()

    # Column header
    BAR_WIDTH = 30
    print(f"   {'TYPE':<12} {'MODEL':<20} {'TOKENS':>12} {'DURATION':>10}  {'WATERFALL':<{BAR_WIDTH}}")
    print(f"   {'─' * 12} {'─' * 20} {'─' * 12} {'─' * 10}  {'─' * BAR_WIDTH}")

    # Render each event
    for i, ev in enumerate(events):
        etype = ev.get("event_type", ev.get("type", "generic"))
        model = ev.get("model", "") or ""
        tok_in = ev.get("tokens_in", 0) or 0
        tok_out = ev.get("tokens_out", 0) or 0
        dur = ev.get("duration_ms") or 0
        tool = ev.get("tool_call", {})
        tool_name = ""
        if isinstance(tool, dict):
            tool_name = tool.get("tool_name", "")

        color = type_colors.get(etype, type_colors["generic"])
        tokens_str = f"{tok_in}→{tok_out}" if tok_in or tok_out else "—"
        dur_str = _format_duration(dur) if dur else "—"

        # Model or tool name display
        name_display = model[:20] if model else tool_name[:20] if tool_name else ""

        # Waterfall bar
        bar_len = int(round(dur / max_dur * BAR_WIDTH)) if dur > 0 else 0
        bar_len = max(bar_len, 1) if dur > 0 else 0

        # Color the bar based on event type
        if etype == "error":
            bar_char = "▓"
        elif etype == "llm_call":
            bar_char = "█"
        elif etype == "tool_call":
            bar_char = "▒"
        else:
            bar_char = "░"

        bar = bar_char * bar_len + " " * (BAR_WIDTH - bar_len)

        # Error indicator
        err_mark = " ✗" if etype == "error" else ""

        print(f"   {color}{etype:<12}{RESET} {DIM}{name_display:<20}{RESET} {tokens_str:>12} {dur_str:>10}  {color}{bar}{RESET}{err_mark}")

    # Summary footer
    print(f"\n   {'─' * (12 + 20 + 12 + 10 + BAR_WIDTH + 6)}")
    type_counts: dict[str, int] = {}
    type_durations: dict[str, float] = {}
    for ev in events:
        et = ev.get("event_type", ev.get("type", "generic"))
        type_counts[et] = type_counts.get(et, 0) + 1
        type_durations[et] = type_durations.get(et, 0) + (ev.get("duration_ms") or 0)

    print(f"   {BOLD}Breakdown:{RESET}")
    for et in sorted(type_counts, key=lambda k: type_durations.get(k, 0), reverse=True):
        color = type_colors.get(et, type_colors["generic"])
        pct = type_durations[et] / total_dur * 100 if total_dur > 0 else 0
        print(f"     {color}{et:<15}{RESET} {type_counts[et]:>4} events  {_format_duration(type_durations[et]):>8}  ({pct:.1f}%)")

    print()


def cmd_status(args: argparse.Namespace) -> None:
    client, endpoint = _get_client(args)
    try:
        resp = client.get("/health")
        resp.raise_for_status()
        print(f"✅ AgentLens backend is healthy at {endpoint}")
        data = resp.json()
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
    except httpx.HTTPError as e:
        print(f"❌ Cannot reach AgentLens backend at {endpoint}")
        print(f"   Error: {e}")
        sys.exit(1)


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Generate a self-contained HTML dashboard with interactive charts."""
    import webbrowser as _webbrowser
    from datetime import datetime, timezone

    client, endpoint = _get_client(args)
    limit = getattr(args, "limit", 100) or 100
    output = getattr(args, "output", None)

    # Fetch data
    print(f"📊 Fetching data from {endpoint} ...")
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    raw = resp.json()
    sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    try:
        resp2 = client.get("/analytics")
        resp2.raise_for_status()
        analytics = resp2.json()
    except Exception:
        analytics = {}

    # Gather per-session summaries
    session_rows = []
    model_counts: dict[str, int] = {}
    model_tokens: dict[str, int] = {}
    model_costs: dict[str, float] = {}
    status_counts: dict[str, int] = {}
    daily_sessions: dict[str, int] = {}
    daily_costs: dict[str, float] = {}
    total_cost = 0.0
    total_tokens = 0
    total_events = 0
    error_count = 0

    for s in sessions:
        sid = s.get("id", "?")
        agent = s.get("agent_name", "unknown")
        status = s.get("status", "unknown")
        tokens = int(s.get("total_tokens", 0) or 0)
        events = int(s.get("event_count", 0) or 0)
        cost = float(s.get("total_cost", 0) or 0)
        created = s.get("created_at", "")

        session_rows.append({"id": sid, "agent": agent, "status": status,
                             "tokens": tokens, "events": events, "cost": cost,
                             "created": created})

        status_counts[status] = status_counts.get(status, 0) + 1
        total_cost += cost
        total_tokens += tokens
        total_events += events
        if status in ("error", "failed"):
            error_count += 1

        # daily aggregation
        day = created[:10] if len(created) >= 10 else "unknown"
        daily_sessions[day] = daily_sessions.get(day, 0) + 1
        daily_costs[day] = daily_costs.get(day, 0) + cost

        # Try to get model info from events in session
        for ev in s.get("events", []):
            m = ev.get("model", "")
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1
                model_tokens[m] = model_tokens.get(m, 0) + int(ev.get("tokens_in", 0) or 0) + int(ev.get("tokens_out", 0) or 0)

    # Sort daily data
    sorted_days = sorted(daily_sessions.keys())
    day_labels = json.dumps(sorted_days)
    day_session_data = json.dumps([daily_sessions.get(d, 0) for d in sorted_days])
    day_cost_data = json.dumps([round(daily_costs.get(d, 0), 4) for d in sorted_days])

    # Status chart data
    status_labels = json.dumps(list(status_counts.keys()))
    status_data = json.dumps(list(status_counts.values()))

    # Model chart data
    m_labels = json.dumps(list(model_counts.keys())[:15])
    m_data = json.dumps(list(model_counts.values())[:15])

    # Top sessions by cost
    top_by_cost = sorted(session_rows, key=lambda r: r["cost"], reverse=True)[:10]
    top_labels = json.dumps([r["id"][:12] for r in top_by_cost])
    top_data = json.dumps([round(r["cost"], 4) for r in top_by_cost])

    # Session table rows
    table_html = ""
    for r in session_rows[:50]:
        css = ' class="error"' if r["status"] in ("error", "failed") else ""
        table_html += f'<tr{css}><td>{r["id"][:16]}</td><td>{r["agent"]}</td><td>{r["status"]}</td><td>{r["events"]}</td><td>{r["tokens"]:,}</td><td>${r["cost"]:.4f}</td><td>{r["created"][:19]}</td></tr>\n'

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    error_rate = (error_count / len(sessions) * 100) if sessions else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentLens Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}}
h1{{font-size:1.8rem;margin-bottom:4px}}
.subtitle{{color:#94a3b8;margin-bottom:24px;font-size:.9rem}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}}
.kpi{{background:#1e293b;border-radius:12px;padding:20px;text-align:center}}
.kpi .value{{font-size:2rem;font-weight:700;color:#38bdf8}}
.kpi .label{{font-size:.8rem;color:#94a3b8;margin-top:4px}}
.kpi.error .value{{color:#f87171}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px;margin-bottom:32px}}
.chart-card{{background:#1e293b;border-radius:12px;padding:20px}}
.chart-card h3{{margin-bottom:12px;font-size:1rem;color:#cbd5e1}}
canvas{{max-height:280px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #334155}}
th{{background:#1e293b;color:#94a3b8;position:sticky;top:0}}
tr:hover{{background:#1e293b}}
tr.error td{{color:#f87171}}
.table-wrap{{background:#1e293b;border-radius:12px;padding:20px;max-height:500px;overflow:auto}}
.table-wrap h3{{margin-bottom:12px;color:#cbd5e1}}
</style>
</head>
<body>
<h1>🔍 AgentLens Dashboard</h1>
<p class="subtitle">Generated {now_str} · {len(sessions)} sessions from {endpoint}</p>

<div class="kpi-grid">
<div class="kpi"><div class="value">{len(sessions)}</div><div class="label">Sessions</div></div>
<div class="kpi"><div class="value">{total_events:,}</div><div class="label">Total Events</div></div>
<div class="kpi"><div class="value">{total_tokens:,}</div><div class="label">Total Tokens</div></div>
<div class="kpi"><div class="value">${total_cost:.4f}</div><div class="label">Total Cost</div></div>
<div class="kpi error"><div class="value">{error_rate:.1f}%</div><div class="label">Error Rate</div></div>
<div class="kpi"><div class="value">{len(model_counts)}</div><div class="label">Models Used</div></div>
</div>

<div class="charts">
<div class="chart-card"><h3>Sessions per Day</h3><canvas id="dailyChart"></canvas></div>
<div class="chart-card"><h3>Daily Cost ($)</h3><canvas id="costChart"></canvas></div>
<div class="chart-card"><h3>Session Status</h3><canvas id="statusChart"></canvas></div>
<div class="chart-card"><h3>Top 10 Sessions by Cost</h3><canvas id="topChart"></canvas></div>
</div>

<div class="table-wrap">
<h3>Sessions (latest {min(50, len(sessions))})</h3>
<table>
<thead><tr><th>ID</th><th>Agent</th><th>Status</th><th>Events</th><th>Tokens</th><th>Cost</th><th>Created</th></tr></thead>
<tbody>{table_html}</tbody>
</table>
</div>

<script>
const colors = ['#38bdf8','#a78bfa','#34d399','#fbbf24','#f87171','#fb923c','#e879f9','#22d3ee'];
const doughnutColors = ['#34d399','#f87171','#fbbf24','#38bdf8','#a78bfa','#fb923c'];

new Chart(document.getElementById('dailyChart'),{{
  type:'bar',
  data:{{labels:{day_labels},datasets:[{{label:'Sessions',data:{day_session_data},backgroundColor:'#38bdf8',borderRadius:4}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},x:{{ticks:{{color:'#94a3b8',maxRotation:45}}}}}}}}
}});

new Chart(document.getElementById('costChart'),{{
  type:'line',
  data:{{labels:{day_labels},datasets:[{{label:'Cost ($)',data:{day_cost_data},borderColor:'#a78bfa',backgroundColor:'rgba(167,139,250,0.1)',fill:true,tension:0.3}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},x:{{ticks:{{color:'#94a3b8',maxRotation:45}}}}}}}}
}});

new Chart(document.getElementById('statusChart'),{{
  type:'doughnut',
  data:{{labels:{status_labels},datasets:[{{data:{status_data},backgroundColor:doughnutColors}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:'bottom',labels:{{color:'#cbd5e1'}}}}}}}}
}});

new Chart(document.getElementById('topChart'),{{
  type:'bar',
  data:{{labels:{top_labels},datasets:[{{label:'Cost ($)',data:{top_data},backgroundColor:'#fbbf24',borderRadius:4}}]}},
  options:{{responsive:true,indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},y:{{ticks:{{color:'#94a3b8'}}}}}}}}
}});
</script>
</body>
</html>"""

    fname = output or "agentlens-dashboard.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Dashboard written to {fname}")

    if getattr(args, "open", False):
        _webbrowser.open(fname)
        print("🌐 Opened in browser")


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentlens",
        description="AgentLens CLI — query your AgentLens backend from the command line.",
    )
    parser.add_argument("--endpoint", help="Backend URL (or set AGENTLENS_ENDPOINT)")
    parser.add_argument("--api-key", help="API key (or set AGENTLENS_API_KEY)")

    sub = parser.add_subparsers(dest="command", required=True)

    # sessions
    p = sub.add_parser("sessions", help="List recent sessions")
    p.add_argument("--limit", type=int, default=20, help="Max sessions to show")

    # session <id>
    p = sub.add_parser("session", help="Show session details")
    p.add_argument("session_id", help="Session ID")

    # costs <id>
    p = sub.add_parser("costs", help="Show cost breakdown for a session")
    p.add_argument("session_id", help="Session ID")

    # events
    p = sub.add_parser("events", help="Search events")
    p.add_argument("--session", help="Filter by session ID")
    p.add_argument("--type", help="Filter by event type")
    p.add_argument("--model", help="Filter by model name")
    p.add_argument("--limit", type=int, default=50, help="Max events")

    # export <id>
    p = sub.add_parser("export", help="Export session data")
    p.add_argument("session_id", help="Session ID")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output", "-o", help="Output file path")

    # analytics
    sub.add_parser("analytics", help="Show aggregate analytics")

    # health <id>
    p = sub.add_parser("health", help="Health score for a session")
    p.add_argument("session_id", help="Session ID")

    # compare
    p = sub.add_parser("compare", help="Compare two sessions")
    p.add_argument("session_a", help="First session ID")
    p.add_argument("session_b", help="Second session ID")

    # alerts
    sub.add_parser("alerts", help="List recent alerts")

    # postmortem
    p = sub.add_parser("postmortem", help="Generate incident postmortem for a session")
    p.add_argument("session_id", nargs="?", default=None, help="Session ID to generate postmortem for")
    p.add_argument("--candidates", action="store_true", help="List sessions eligible for postmortem")
    p.add_argument("--min-errors", type=int, default=None, help="Min errors for candidate listing (default: 2)")
    p.add_argument("--limit", type=int, default=None, help="Max candidates to show")

    # tail
    p = sub.add_parser("tail", help="Live-follow events (like tail -f)")
    p.add_argument("--session", help="Filter by session ID")
    p.add_argument("--type", help="Filter by event type")
    p.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds (default: 2)")

    # top
    p = sub.add_parser("top", help="Live session leaderboard (like htop for agents)")
    p.add_argument("--sort", choices=["cost", "tokens", "events"], default="cost", help="Sort sessions by (default: cost)")
    p.add_argument("--limit", type=int, default=15, help="Max sessions to show (default: 15)")
    p.add_argument("--interval", type=float, default=3.0, help="Refresh interval in seconds (default: 3)")

    # report
    p = sub.add_parser("report", help="Generate summary report for a time period")
    p.add_argument("--period", choices=["day", "week", "month"], default="day", help="Time period (default: day)")
    p.add_argument("--format", choices=["table", "json", "markdown"], default="table", help="Output format (default: table)")
    p.add_argument("--output", "-o", help="Write report to file")

    # flamegraph
    p = sub.add_parser("flamegraph", help="Generate interactive HTML flamegraph for a session")
    p.add_argument("session_id", help="Session ID to visualise")
    p.add_argument("--output", "-o", default=None, help="Output HTML file (default: flamegraph-<session_id>.html)")
    p.add_argument("--open", action="store_true", help="Open the flamegraph in a browser after generating")
    p.add_argument("--stats", action="store_true", help="Print flamegraph statistics instead of generating HTML")

    # dashboard
    p = sub.add_parser("dashboard", help="Generate self-contained HTML dashboard with charts")
    p.add_argument("--limit", type=int, default=100, help="Max sessions to include (default: 100)")
    p.add_argument("--output", "-o", default=None, help="Output HTML file (default: agentlens-dashboard.html)")
    p.add_argument("--open", action="store_true", help="Open dashboard in browser after generating")

    # trace
    p = sub.add_parser("trace", help="Render session events as a terminal waterfall timeline")
    p.add_argument("session_id", help="Session ID to trace")
    p.add_argument("--no-color", action="store_true", help="Disable colored output")
    p.add_argument("--json", action="store_true", help="Output trace as JSON instead of visual")
    p.add_argument("--type", help="Filter events by type (e.g. llm_call, tool_call, error)")
    p.add_argument("--min-ms", type=float, default=None, help="Only show events slower than N milliseconds")

    # status
    sub.add_parser("status", help="Check backend connectivity")

    args = parser.parse_args()

    commands = {
        "sessions": cmd_sessions,
        "session": cmd_session,
        "costs": cmd_costs,
        "events": cmd_events,
        "export": cmd_export,
        "analytics": cmd_analytics,
        "health": cmd_health,
        "compare": cmd_compare,
        "alerts": cmd_alerts,
        "postmortem": cmd_postmortem,
        "tail": cmd_tail,
        "top": cmd_top,
        "report": cmd_report,
        "flamegraph": cmd_flamegraph,
        "dashboard": cmd_dashboard,
        "trace": cmd_trace,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
