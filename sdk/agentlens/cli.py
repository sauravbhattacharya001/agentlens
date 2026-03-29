"""AgentLens CLI â€" query your AgentLens backend from the command line.

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
    agentlens-cli heatmap [--metric sessions|cost|tokens|events] [--weeks N] [--limit N] [--endpoint URL] [--api-key KEY]
    agentlens-cli replay <session_id> [--speed N] [--type TYPES] [--exclude TYPES] [--format text|json|markdown] [--live] [--no-color] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli outlier [--metric cost|tokens|duration|errors|all] [--limit N] [--threshold F] [--format table|json] [--top N] [--endpoint URL] [--api-key KEY]
    agentlens-cli digest [--period day|week|month] [--format text|markdown|html|json] [--output FILE] [--open] [--top N] [--endpoint URL] [--api-key KEY]
    agentlens-cli funnel [--stages TYPES] [--limit N] [--format table|json|html] [--output FILE] [--open] [--endpoint URL] [--api-key KEY]
    agentlens-cli depmap [--limit N] [--format ascii|json|html] [--output FILE] [--open] [--endpoint URL] [--api-key KEY]
    agentlens-cli budget list [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli budget set <scope> <period> <limit_usd> [--warn-pct N] [--endpoint URL] [--api-key KEY]
    agentlens-cli budget check <session_id> [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli budget delete <scope> [<period>] [--endpoint URL] [--api-key KEY]
    agentlens-cli snapshot [--label LABEL] [--output FILE] [--limit N] [--format json|table] [--endpoint URL] [--api-key KEY]
    agentlens-cli snapshot diff <file_a> <file_b> [--format table|json]
    agentlens-cli alert history [--severity LEVEL] [--since HOURS] [--limit N] [--ack|--unack] [--format table|json]
    agentlens-cli alert rules [--format table|json]
    agentlens-cli alert test <rule_id> <session_id>
    agentlens-cli alert ack <alert_id> [--note TEXT]
    agentlens-cli alert silence <rule_id> [--duration MINUTES]
    agentlens-cli alert unsilence <rule_id>
    agentlens-cli alert stats [--period day|week|month] [--format table|json]
    agentlens-cli forecast [--days N] [--metric cost|tokens|sessions] [--model MODEL] [--format table|json|chart] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli leaderboard [--sort efficiency|speed|reliability|cost|volume] [--days N] [--limit N] [--min-sessions N] [--order asc|desc] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli gantt <session_id> [--output FILE] [--open] [--format html|json|ascii] [--endpoint URL] [--api-key KEY]
    agentlens-cli audit [ENTRY_ID] [--agent NAME] [--action TYPE] [--severity LEVEL] [--model MODEL] [--session ID] [--since HOURS] [--limit N] [--format table|csv|json] [--output FILE] [--stats] [--no-color] [--endpoint URL] [--api-key KEY]
    agentlens-cli trends [--period day|week|month] [--metric METRIC|all] [--agent NAME] [--limit N] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli sla [--policy production|development] [--latency MS] [--error-rate PCT] [--token-budget N] [--slo PCT] [--agent NAME] [--limit N] [--verbose] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli diff <session_a> <session_b> [--label-a LABEL] [--label-b LABEL] [--no-color] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli profile <agent_name> [--days N] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli correlate [--metrics METRICS] [--limit N] [--min-sessions N] [--format table|json|csv] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli watch [--interval SECS] [--metric METRIC] [--agent NAME] [--alert-threshold N] [--compact] [--no-spark] [--duration MINS] [--endpoint URL] [--api-key KEY]
    agentlens-cli baseline list [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli baseline show <agent_name> [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli baseline record <session_id> [--endpoint URL] [--api-key KEY]
    agentlens-cli baseline check <session_id> [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli baseline delete <agent_name> [--endpoint URL] [--api-key KEY]
    agentlens-cli retention [--limit N] [--format table|json|chart] [--output FILE] [--open] [--endpoint URL] [--api-key KEY]
    agentlens-cli retention policy [--keep-days N] [--dry-run] [--json] [--endpoint URL] [--api-key KEY]
    agentlens-cli retention purge --older-than DAYS [--dry-run] [--yes] [--endpoint URL] [--api-key KEY]
    agentlens-cli scatter [--x METRIC] [--y METRIC] [--limit N] [--width W] [--height H] [--agent NAME] [--no-trend] [--format ascii|json] [--output FILE] [--endpoint URL] [--api-key KEY]
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

from agentlens.cli_analytics import cmd_report, cmd_outlier  # extracted
from agentlens.cli_common import get_client as _get_client, print_json as _print_json  # shared helpers
from agentlens.cli_digest import cmd_digest  # periodic digest summaries
from agentlens.cli_funnel import cmd_funnel  # workflow funnel analysis
from agentlens.cli_depmap import cmd_depmap  # dependency map visualization
from agentlens.cli_budget import cmd_budget  # cost budget management
from agentlens.cli_snapshot import cmd_snapshot  # point-in-time system snapshots
from agentlens.cli_alert import cmd_alert, register_alert_parser  # alert management
from agentlens.cli_forecast import cmd_forecast  # cost/usage forecasting
from agentlens.cli_gantt import cmd_gantt  # interactive Gantt chart
from agentlens.cli_audit import cmd_audit, register_audit_parser  # audit trail
from agentlens.cli_trends import cmd_trends  # period-over-period trends
from agentlens.cli_sla import cmd_sla  # SLA compliance evaluation
from agentlens.cli_diff import cmd_diff  # side-by-side session diff
from agentlens.cli_profile import cmd_profile, register_profile_parser  # agent performance profiler
from agentlens.cli_trace import cmd_trace  # terminal waterfall timeline
from agentlens.cli_heatmap import cmd_heatmap  # GitHub-style activity heatmap
from agentlens.cli_correlate import run as cmd_correlate, setup_parser as register_correlate_parser  # metric correlations
from agentlens.cli_capacity import cmd_capacity
from agentlens.cli_baseline import cmd_baseline, register_baseline_parser  # fleet capacity planning
from agentlens.cli_retention import cmd_retention, register_retention_parser  # data retention analysis
from agentlens.cli_scatter import cmd_scatter, register_scatter_parser  # terminal scatter plots


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


# â"€â"€ Commands â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


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
        print(f"âœ… Session {args.session_id}: No incidents detected.")
        print(f"   Events analysed: {report.get('event_count', 0)}")
        return

    # Pretty-print the postmortem
    severity = report.get("severity", "?")
    sev_colors = {"SEV-1": "ðŸ"´", "SEV-2": "ðŸŸ ", "SEV-3": "ðŸŸ¡", "SEV-4": "ðŸŸ¢"}
    icon = sev_colors.get(severity, "âšª")

    print(f"\n{'='*60}")
    print(f"  {icon} INCIDENT POSTMORTEM â€" {report.get('incident_id', '')}")
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
        print(f"\n  {'â"€'*50}")
        print(f"  IMPACT")
        print(f"  {'â"€'*50}")
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
            print(f"    âš  User-facing errors detected")

    # Root causes
    root_causes = report.get("root_causes", [])
    if root_causes:
        print(f"\n  {'â"€'*50}")
        print(f"  ROOT CAUSES")
        print(f"  {'â"€'*50}")
        for i, rc in enumerate(root_causes, 1):
            conf = rc.get("confidence", 0)
            conf_bar = "â-ˆ" * int(conf * 10) + "â-'" * (10 - int(conf * 10))
            print(f"    {i}. {rc.get('description', '')}")
            print(f"       Confidence: [{conf_bar}] {conf:.0%}")
            print(f"       Category:   {rc.get('category', '')}")
            print(f"       Affected:   {rc.get('affected_events', 0)} events")
            for ev in rc.get("evidence", []):
                print(f"       â€¢ {ev}")

    # Timeline
    timeline = report.get("timeline", [])
    if timeline:
        print(f"\n  {'â"€'*50}")
        print(f"  TIMELINE")
        print(f"  {'â"€'*50}")
        for entry in timeline:
            sev_icon = {"error": "âŒ", "warning": "âš ï¸", "info": "â"¹ï¸"}.get(entry.get("severity", ""), "â€¢")
            elapsed = _format_duration(entry.get("elapsed_ms", 0))
            print(f"    {sev_icon} +{elapsed:<10} {entry.get('description', '')}")

    print(f"\n{'='*60}\n")


def _format_duration(ms: Any) -> str:
    """Format milliseconds into a human-readable duration string.

    Delegates to the shared ``cli_common.format_duration`` implementation.
    Kept as a local alias for backward compatibility with callers in this module.
    """
    from agentlens.cli_common import format_duration
    return format_duration(ms)


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
        print(f"ðŸ" Tailing events at {endpoint} (interval={interval}s, Ctrl+C to stop)")
        if session_filter:
            print(f"   Session filter: {session_filter}")
        if type_filter:
            print(f"   Type filter: {type_filter}")
        print(f"   Skipped {len(seen)} existing events\n")
    except httpx.HTTPError:
        print(f"ðŸ" Tailing events at {endpoint} (interval={interval}s, Ctrl+C to stop)\n")

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
            parts.append(f"sess={sid}â€¦")
        if model:
            parts.append(f"model={model}")
        if tok_in or tok_out:
            parts.append(f"tokens={tok_in}â†'{tok_out}")
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
                print(f"âš  poll error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nðŸ'‹ Stopped tailing.")


def cmd_top(args: argparse.Namespace) -> None:
    """Live leaderboard of sessions ranked by cost, tokens, or event count â€" like htop for agents."""
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
        return "â-ˆ" * filled + "â-'" * (width - filled)

    def _fetch_and_display() -> None:
        try:
            resp = client.get("/sessions", params={"limit": limit * 2})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"âš  Error fetching sessions: {exc}", file=sys.stderr)
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

        print(f"âš¡ AgentLens Top â€" sorted by {sort_key} | {endpoint} | Ctrl+C to stop")
        print(f"   Refreshing every {interval}s\n")

        if not sessions:
            print("  (no sessions)")
            return

        max_val = max((s.get(sort_field, 0) or 0) for s in sessions) or 1

        # Header
        print(f"  {'#':<3} {'SESSION':<12} {'AGENT':<18} {'STATUS':<10} {'EVENTS':>7} {'TOKENS':>10} {'COST':>10}  {'':20}")
        print(f"  {'â"€'*3} {'â"€'*12} {'â"€'*18} {'â"€'*10} {'â"€'*7} {'â"€'*10} {'â"€'*10}  {'â"€'*20}")

        for i, s in enumerate(sessions, 1):
            sid = str(s.get("id", ""))[:12]
            agent = str(s.get("agent_name", "") or "")[:18]
            status = str(s.get("status", "") or "")[:10]
            events = s.get("event_count", 0) or 0
            tokens = s.get("total_tokens", 0) or 0
            cost = s.get("total_cost", 0) or 0
            val = s.get(sort_field, 0) or 0
            bar = _bar(val, max_val)

            cost_str = f"${cost:.4f}" if cost > 0 else "â€""
            print(f"  {i:<3} {sid:<12} {agent:<18} {status:<10} {events:>7} {tokens:>10} {cost_str:>10}  {bar}")

        print(f"\n  Showing {len(sessions)} sessions")

    print(f"âš¡ AgentLens Top â€" connecting to {endpoint}...")
    try:
        while True:
            _fetch_and_display()
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\nðŸ'‹ Stopped.")




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
        print(f"âš ï¸  No events found for session {args.session_id}")
        sys.exit(1)

    # Convert raw dicts to AgentEvent objects
    events: list[AgentEvent] = []
    for raw in raw_events:
        try:
            events.append(AgentEvent(**raw))
        except Exception:
            # Skip malformed events
            continue

    print(f"ðŸ"Š Building flamegraph for session {args.session_id} ({len(events)} events)...")

    fg = Flamegraph(events=events, session_name=session_name)

    if args.stats:
        stats = fg.get_stats()
        print(f"\nðŸ"¥ Flamegraph Statistics")
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
                print(f"     {i}. {ev.get('name', 'unknown')} â€" {ev.get('duration', 0):.1f} ms")
        return

    output = args.output or f"flamegraph-{args.session_id}.html"
    fg.save(output)
    abs_path = str(Path(output).resolve())
    print(f"âœ… Flamegraph saved to {abs_path}")

    if args.open:
        _wb.open(f"file://{abs_path}")
        print("ðŸŒ Opened in browser")


# cmd_trace extracted to cli_trace.py


# cmd_heatmap extracted to cli_heatmap.py


def cmd_status(args: argparse.Namespace) -> None:
    client, endpoint = _get_client(args)
    try:
        resp = client.get("/health")
        resp.raise_for_status()
        print(f"âœ… AgentLens backend is healthy at {endpoint}")
        data = resp.json()
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
    except httpx.HTTPError as e:
        print(f"âŒ Cannot reach AgentLens backend at {endpoint}")
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
    print(f"ðŸ"Š Fetching data from {endpoint} ...")
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
<h1>ðŸ" AgentLens Dashboard</h1>
<p class="subtitle">Generated {now_str} Â· {len(sessions)} sessions from {endpoint}</p>

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
    print(f"âœ… Dashboard written to {fname}")

    if getattr(args, "open", False):
        _webbrowser.open(fname)
        print("ðŸŒ Opened in browser")


# â"€â"€ Replay â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


def _build_session_from_api(session_data: dict, events_data: list[dict]) -> Any:
    """Construct a Session + AgentEvent tree from raw API dicts."""
    from datetime import datetime, timezone
    from agentlens.models import AgentEvent, Session, ToolCall

    def _parse_ts(val: Any) -> datetime:
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            # handle Z suffix and +00:00
            val = val.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)

    events: list[AgentEvent] = []
    for raw in events_data:
        tc = None
        if raw.get("tool_call"):
            tc_raw = raw["tool_call"]
            tc = ToolCall(
                tool_call_id=tc_raw.get("tool_call_id", ""),
                tool_name=tc_raw.get("tool_name", "unknown"),
                tool_input=tc_raw.get("tool_input", {}),
                tool_output=tc_raw.get("tool_output"),
                duration_ms=tc_raw.get("duration_ms"),
            )
        events.append(AgentEvent(
            event_id=raw.get("event_id", raw.get("id", "")),
            session_id=raw.get("session_id", session_data.get("session_id", "")),
            event_type=raw.get("event_type", raw.get("type", "generic")),
            timestamp=_parse_ts(raw.get("timestamp")),
            model=raw.get("model"),
            tokens_in=raw.get("tokens_in", 0),
            tokens_out=raw.get("tokens_out", 0),
            tool_call=tc,
            duration_ms=raw.get("duration_ms"),
        ))

    session = Session(
        session_id=session_data.get("session_id", session_data.get("id", "unknown")),
        agent_name=session_data.get("agent_name", "unknown"),
        started_at=_parse_ts(session_data.get("started_at", session_data.get("created_at"))),
        ended_at=_parse_ts(session_data["ended_at"]) if session_data.get("ended_at") else None,
        status=session_data.get("status", "completed"),
        events=events,
        total_tokens_in=sum(e.tokens_in for e in events),
        total_tokens_out=sum(e.tokens_out for e in events),
    )
    return session


def cmd_replay(args: argparse.Namespace) -> None:
    """Replay a session event-by-event in the terminal.

    Fetches session data and events from the API, then uses
    SessionReplayer to produce a formatted chronological replay
    with optional speed control, type filtering, and multiple
    output formats.
    """
    import time as _time
    from agentlens.replayer import SessionReplayer

    client, _ = _get_client(args)

    # Fetch session metadata
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()

    # Fetch events
    resp = client.get("/events", params={"session_id": args.session_id, "limit": 10000})
    resp.raise_for_status()
    events_raw = resp.json()
    if isinstance(events_raw, dict):
        events_raw = events_raw.get("events", [events_raw])

    if not events_raw:
        print(f"No events found for session {args.session_id}")
        return

    session = _build_session_from_api(session_data, events_raw)
    replayer = SessionReplayer(session, speed=args.speed)

    # Apply type filters
    if args.type:
        replayer.add_filter(*[t.strip() for t in args.type.split(",")])
    if args.exclude:
        replayer.exclude(*[t.strip() for t in args.exclude.split(",")])

    # Non-live output formats
    fmt = args.format or "text"
    if fmt == "json":
        output = replayer.to_json()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"âœ… JSON replay written to {args.output}")
        else:
            print(output)
        return

    if fmt == "markdown":
        output = replayer.to_markdown()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"âœ… Markdown replay written to {args.output}")
        else:
            print(output)
        return

    # Live text mode â€" stream frames to terminal with delays
    if args.live:
        print(
            f"â-¶ Replaying session {session.session_id}"
            f"  agent={session.agent_name}  speed={args.speed}x"
            f"  events={len(replayer.filtered_events)}"
        )
        print()

        use_color = not getattr(args, "no_color", False)

        _TYPE_COLORS = {
            "llm_call": "\033[36m",    # cyan
            "tool_call": "\033[33m",   # yellow
            "error": "\033[31m",       # red
            "decision": "\033[35m",    # magenta
            "guardrail": "\033[32m",   # green
        }
        _RESET = "\033[0m"

        for frame in replayer.play():
            # Sleep for the wall delay to simulate real-time playback
            if frame.wall_delay_ms > 0 and frame.index > 0:
                _time.sleep(frame.wall_delay_ms / 1000.0)

            e = frame.event
            # Build display line
            idx_str = f"[{frame.index + 1:>3}/{frame.total}]"
            pct_str = f"{frame.progress_pct:5.1f}%"

            type_str = e.event_type
            if use_color:
                color = _TYPE_COLORS.get(e.event_type, "\033[37m")
                type_str = f"{color}{e.event_type}{_RESET}"

            parts = [idx_str, type_str]
            if e.model:
                parts.append(f"model={e.model}")
            if e.tool_call:
                parts.append(f"tool={e.tool_call.tool_name}")
            if e.duration_ms is not None:
                parts.append(f"dur={e.duration_ms:.0f}ms")
            if e.tokens_in or e.tokens_out:
                parts.append(f"tok={e.tokens_in}â†'{e.tokens_out}")
            if frame.is_breakpoint:
                bp_marker = "\033[31;1mâ¸ BREAK\033[0m" if use_color else "â¸ BREAK"
                parts.append(bp_marker)

            # Progress bar
            bar_width = 20
            filled = int(frame.progress * bar_width)
            bar = "â-ˆ" * filled + "â-'" * (bar_width - filled)
            parts.append(f"[{bar}] {pct_str}")

            print(" | ".join(parts))

        print()
        print(replayer.stats.summary())
    else:
        # Static text dump
        output = replayer.to_text()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"âœ… Text replay written to {args.output}")
        else:
            print(output)


# â"€â"€ Main â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentlens",
        description="AgentLens CLI â€" query your AgentLens backend from the command line.",
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

    # heatmap
    p = sub.add_parser("heatmap", help="GitHub-style activity heatmap (day-of-week Ã- hour)")
    p.add_argument("--metric", choices=["sessions", "cost", "tokens", "events"], default="sessions", help="Metric to visualize (default: sessions)")
    p.add_argument("--weeks", type=int, default=12, help="Number of weeks to include (default: 12)")
    p.add_argument("--limit", type=int, default=500, help="Max sessions to fetch (default: 500)")

    # replay
    p = sub.add_parser("replay", help="Replay a session event-by-event in the terminal")
    p.add_argument("session_id", help="Session ID to replay")
    p.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0, e.g. 2.0 = 2x faster)")
    p.add_argument("--type", help="Include only these event types (comma-separated, e.g. llm_call,tool_call)")
    p.add_argument("--exclude", help="Exclude these event types (comma-separated)")
    p.add_argument("--format", choices=["text", "json", "markdown"], default="text", help="Output format (default: text)")
    p.add_argument("--live", action="store_true", help="Stream frames in real-time with delays and progress bar")
    p.add_argument("--no-color", action="store_true", help="Disable colored output in live mode")
    p.add_argument("--output", "-o", help="Write replay to file instead of stdout")

    # outlier
    p = sub.add_parser("outlier", help="Detect outlier sessions by cost, tokens, duration, or errors")
    p.add_argument("--metric", choices=["cost", "tokens", "duration", "errors", "all"], default="all", help="Metric to check (default: all)")
    p.add_argument("--limit", type=int, default=200, help="Max sessions to fetch (default: 200)")
    p.add_argument("--threshold", type=float, default=1.5, help="IQR multiplier for outlier detection (default: 1.5)")
    p.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p.add_argument("--top", type=int, default=10, help="Max outliers to show per metric (default: 10)")

    # leaderboard
    p = sub.add_parser("leaderboard", help="Rank agents by performance metrics")
    p.add_argument("--sort", choices=["efficiency", "speed", "reliability", "cost", "volume"], default="efficiency", help="Ranking metric (default: efficiency)")
    p.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    p.add_argument("--limit", type=int, default=20, help="Max agents to show (default: 20)")
    p.add_argument("--min-sessions", type=int, default=2, help="Min sessions to qualify (default: 2)")
    p.add_argument("--order", choices=["asc", "desc"], default=None, help="Sort order (default depends on metric)")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # gantt
    p = sub.add_parser("gantt", help="Generate interactive HTML Gantt chart for a session")
    p.add_argument("session_id", help="Session ID to visualise")
    p.add_argument("--output", "-o", help="Write output to file")
    p.add_argument("--open", action="store_true", help="Open HTML output in browser")
    p.add_argument("--format", choices=["html", "json", "ascii"], default="html", help="Output format (default: html)")

    # trends
    p = sub.add_parser("trends", help="Show metric trends with sparklines and period-over-period comparison")
    p.add_argument("--period", choices=["day", "week", "month"], default="week", help="Comparison period (default: week)")
    p.add_argument("--metric", choices=["sessions", "cost", "tokens", "events", "errors", "error_rate", "avg_cost", "avg_tokens", "all"], default="all", help="Metric to show (default: all)")
    p.add_argument("--agent", help="Filter by agent name (substring match)")
    p.add_argument("--limit", type=int, default=500, help="Max sessions to fetch (default: 500)")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # sla
    p = sub.add_parser("sla", help="Evaluate sessions against SLA policies and show compliance")
    p.add_argument("--policy", choices=["production", "development"], default="production", help="Preset SLA policy (default: production)")
    p.add_argument("--latency", type=float, default=None, help="Custom P95 latency target (ms)")
    p.add_argument("--error-rate", dest="error_rate_target", type=float, default=None, help="Custom max error rate target (%%)")
    p.add_argument("--token-budget", type=int, default=None, help="Custom token budget per session")
    p.add_argument("--slo", type=float, default=99.0, help="SLO percentage for custom targets (default: 99)")
    p.add_argument("--agent", help="Filter by agent name (substring match)")
    p.add_argument("--limit", type=int, default=100, help="Max sessions to evaluate (default: 100)")
    p.add_argument("--verbose", "-v", action="store_true", help="Show violating session IDs and stats")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # diff
    p = sub.add_parser("diff", help="Side-by-side comparison of two sessions with metric deltas")
    p.add_argument("session_a", help="First session ID")
    p.add_argument("session_b", help="Second session ID")
    p.add_argument("--label-a", dest="label_a", help="Label for session A (default: truncated ID)")
    p.add_argument("--label-b", dest="label_b", help="Label for session B (default: truncated ID)")
    p.add_argument("--no-color", action="store_true", help="Disable colored output")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # profile
    register_profile_parser(sub)

    # correlate
    register_correlate_parser(sub)

    # status
    sub.add_parser("status", help="Check backend connectivity")

    # funnel
    p = sub.add_parser("funnel", help="Analyse agent workflow funnels with drop-off between stages")
    p.add_argument("--stages", default=None, help="Comma-separated event types for funnel stages (default: plan,tool_call,llm_call,result,error)")
    p.add_argument("--limit", type=int, default=200, help="Max sessions to fetch (default: 200)")
    p.add_argument("--format", choices=["table", "json", "html"], default="table", help="Output format (default: table)")
    p.add_argument("--output", "-o", help="Write output to file")
    p.add_argument("--open", action="store_true", help="Open HTML output in browser")

    # depmap
    p = sub.add_parser("depmap", help="Visualise agent-to-tool dependency map across sessions")
    p.add_argument("--limit", type=int, default=50, help="Max sessions to scan (default: 50)")
    p.add_argument("--format", choices=["ascii", "json", "html"], default="ascii", help="Output format (default: ascii)")
    p.add_argument("--output", "-o", help="Write output to file")
    p.add_argument("--open", action="store_true", help="Open HTML output in browser")

    # budget
    p = sub.add_parser("budget", help="Manage and monitor cost budgets")
    budget_sub = p.add_subparsers(dest="budget_action")
    bp = budget_sub.add_parser("list", help="List all budgets with status")
    bp.add_argument("--json", action="store_true", help="Output as JSON")
    bp = budget_sub.add_parser("set", help="Create or update a budget")
    bp.add_argument("scope", help='Budget scope: "global" or "agent:<name>"')
    bp.add_argument("period", choices=["daily", "weekly", "monthly", "total"], help="Budget period")
    bp.add_argument("limit_usd", type=float, help="Spending limit in USD")
    bp.add_argument("--warn-pct", type=float, default=80, help="Warning threshold percentage (default: 80)")
    bp = budget_sub.add_parser("check", help="Check budget status for a session")
    bp.add_argument("session_id", help="Session ID to check")
    bp.add_argument("--json", action="store_true", help="Output as JSON")
    bp = budget_sub.add_parser("delete", help="Delete a budget")
    bp.add_argument("scope", help='Budget scope to delete')
    bp.add_argument("period", nargs="?", default=None, help="Period to delete (omit to delete all for scope)")

    p = sub.add_parser("digest", help="Generate periodic digest summary (daily/weekly/monthly)")
    p.add_argument("--period", choices=["day", "week", "month"], default="day")
    p.add_argument("--format", choices=["text", "markdown", "html", "json"], default="text")
    p.add_argument("--output", "-o")
    p.add_argument("--open", action="store_true", help="Open HTML output in browser")
    p.add_argument("--top", type=int, default=5, help="Number of top sessions to show")

    # -- snapshot --
    p = sub.add_parser("snapshot", help="Capture point-in-time system snapshot for before/after comparisons")
    snapshot_sub = p.add_subparsers(dest="snapshot_action")
    p.add_argument("--label", help="Label this snapshot (e.g. 'pre-deploy')")
    p.add_argument("--output", "-o", help="Save snapshot JSON to file")
    p.add_argument("--limit", type=int, default=20, help="Number of sessions to include")
    p.add_argument("--format", choices=["json", "table"], default="table")
    dp = snapshot_sub.add_parser("diff", help="Compare two snapshot files")
    dp.add_argument("file_a", help="First snapshot JSON file")
    dp.add_argument("file_b", help="Second snapshot JSON file")
    dp.add_argument("--format", choices=["table", "json"], default="table")

    # -- alert (rich alert management) --
    register_alert_parser(sub)

    # -- audit (action audit trail) --
    register_audit_parser(sub)

    # -- capacity --
    p = sub.add_parser("capacity", help="Fleet capacity planning: bottlenecks, sizing, projections")
    p.add_argument("--horizon", type=int, default=24, help="Projection horizon in hours (default: 24)")
    p.add_argument("--target-rpm", type=float, help="Target requests per minute for sizing")
    p.add_argument("--target-latency", type=float, help="Target P95 latency in ms for sizing")
    p.add_argument("--format", choices=["table", "json", "chart"], default="table", help="Output format")
    p.add_argument("--output", "-o", help="Write output to file")

    # -- baseline --
    register_baseline_parser(sub)

    # -- retention --
    register_retention_parser(sub)

    # -- scatter --
    register_scatter_parser(sub)

    # -- forecast --
    p = sub.add_parser("forecast", help="Predict future costs/usage from historical trends")
    p.add_argument("--days", type=int, default=7, help="Number of days to forecast (default: 7)")
    p.add_argument("--metric", choices=["cost", "tokens", "sessions"], default="cost", help="Metric to forecast")
    p.add_argument("--model", help="Filter by model name")
    p.add_argument("--format", choices=["table", "json", "chart"], default="table", help="Output format")
    p.add_argument("--output", "-o", help="Write output to file")

    # -- watch --
    p = sub.add_parser("watch", help="Real-time streaming metric monitor with live dashboard")
    p.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")
    p.add_argument("--metric", choices=["sessions", "cost", "tokens", "errors"], help="Show only this metric")
    p.add_argument("--agent", help="Filter by agent name")
    p.add_argument("--alert-threshold", type=float, help="Cost threshold for alerts (USD)")
    p.add_argument("--compact", action="store_true", help="Compact view (no per-agent/model breakdown)")
    p.add_argument("--no-spark", action="store_true", help="Disable sparkline trends")
    p.add_argument("--duration", type=int, help="Auto-stop after N minutes")

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
        "heatmap": cmd_heatmap,
        "replay": cmd_replay,
        "outlier": cmd_outlier,
        "digest": cmd_digest,
        "funnel": cmd_funnel,
        "depmap": cmd_depmap,
        "budget": lambda args: cmd_budget(args, _get_client(args)[0]),
        "snapshot": cmd_snapshot,
        "alert": lambda args: cmd_alert(_get_client(args)[0], args),
        "forecast": cmd_forecast,
        "capacity": cmd_capacity,
        "gantt": cmd_gantt,
        "audit": cmd_audit,
        "leaderboard": lambda args: __import__("agentlens.cli_leaderboard", fromlist=["cmd_leaderboard"]).cmd_leaderboard(args),
        "trends": cmd_trends,
        "sla": cmd_sla,
        "diff": cmd_diff,
        "profile": cmd_profile,
        "correlate": cmd_correlate,
        "status": cmd_status,
        "watch": lambda args: __import__("agentlens.cli_watch", fromlist=["cmd_watch"]).cmd_watch(args),
        "baseline": cmd_baseline,
        "retention": cmd_retention,
        "scatter": cmd_scatter,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
