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
    agentlens-cli tail [--session SESSION] [--type TYPE] [--interval SECS] [--endpoint URL] [--api-key KEY]
    agentlens-cli top [--sort cost|tokens|events] [--limit N] [--interval SECS] [--endpoint URL] [--api-key KEY]
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
        "tail": cmd_tail,
        "top": cmd_top,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
