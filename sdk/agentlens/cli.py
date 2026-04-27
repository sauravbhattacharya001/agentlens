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
    agentlens-cli config show
    agentlens-cli config set <key> <value>
    agentlens-cli config unset <key>
    agentlens-cli config reset
    agentlens-cli config path
    agentlens-cli bottleneck [--by agent|model|type] [--metric latency|cost|errors] [--limit N] [--min-sessions N] [--format table|json] [--output FILE] [--endpoint URL] [--api-key KEY]
    agentlens-cli status [--endpoint URL] [--api-key KEY]

Environment variables:
    AGENTLENS_ENDPOINT  Backend URL (default: http://localhost:3000)
    AGENTLENS_API_KEY   API key (default: default)
"""

from __future__ import annotations

import argparse
import json
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
from agentlens.cli_dashboard import cmd_dashboard  # self-contained HTML dashboard
from agentlens.cli_replay import cmd_replay  # session replay
from agentlens.cli_capacity import cmd_capacity
from agentlens.cli_baseline import cmd_baseline, register_baseline_parser  # fleet capacity planning
from agentlens.cli_retention import cmd_retention, register_retention_parser  # data retention analysis
from agentlens.cli_scatter import cmd_scatter, register_scatter_parser  # terminal scatter plots
from agentlens.cli_bottleneck import cmd_bottleneck, register as register_bottleneck_parser  # bottleneck analysis
from agentlens.cli_config import cmd_config, register_config_parser, apply_config_defaults  # persistent config
from agentlens.cli_triage import cmd_triage, register_triage_parser  # auto-triage engine


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
            conf_bar = "█" * int(conf * 10) + "▒" * (10 - int(conf * 10))
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
            sev_icon = {"error": "❌", "warning": "⚠️", "info": "┹️"}.get(entry.get("severity", ""), "•")
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
        print("\n💋 Stopped tailing.")


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
        return "█" * filled + "▒" * (width - filled)

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
        print("\n💋 Stopped.")




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

    print(f"🔊 Building flamegraph for session {args.session_id} ({len(events)} events)...")

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


# cmd_trace extracted to cli_trace.py


# cmd_heatmap extracted to cli_heatmap.py


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


# cmd_dashboard extracted to cli_dashboard.py

# cmd_replay and _build_session_from_api extracted to cli_replay.py

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

    # heatmap
    p = sub.add_parser("heatmap", help="GitHub-style activity heatmap (day-of-week Ö hour)")
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

    # -- config --
    register_config_parser(sub)

    # -- bottleneck --
    register_bottleneck_parser(sub)

    # -- triage --
    register_triage_parser(sub)

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

    # Apply persistent config defaults before dispatching
    if args.command != "config":
        apply_config_defaults(args)

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
        "budget": cmd_budget,
        "snapshot": cmd_snapshot,
        "alert": cmd_alert,
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
        "config": cmd_config,
        "bottleneck": cmd_bottleneck,
        "triage": cmd_triage,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
