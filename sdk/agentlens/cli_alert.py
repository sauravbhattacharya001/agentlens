"""CLI alert management for AgentLens.

Provides rich alert operations: listing alert history with filters,
viewing/managing alert rules, testing rules against sessions,
acknowledging alerts, silencing noisy rules, and alert statistics.

Usage (from main CLI):
    agentlens-cli alert history [--severity LEVEL] [--since HOURS] [--limit N] [--ack|--unack] [--format table|json]
    agentlens-cli alert rules [--format table|json]
    agentlens-cli alert test <rule_id> <session_id>
    agentlens-cli alert ack <alert_id> [--note TEXT]
    agentlens-cli alert silence <rule_id> [--duration MINUTES]
    agentlens-cli alert unsilence <rule_id>
    agentlens-cli alert stats [--period day|week|month] [--format table|json]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

from agentlens.cli_common import get_client_only, print_json as _print_json


# ── Severity helpers ───────────────────────────────────────────────────

_SEV_ORDER = {"info": 0, "warning": 1, "critical": 2}
_SEV_COLORS = {"info": "\033[36m", "warning": "\033[33m", "critical": "\033[31m"}
_RESET = "\033[0m"


def _severity_icon(sev: str) -> str:
    return {"info": "ℹ️ ", "warning": "⚠️ ", "critical": "🔴"}.get(sev, "  ")


def _colorize(text: str, severity: str) -> str:
    color = _SEV_COLORS.get(severity, "")
    return f"{color}{text}{_RESET}" if color else text


# ── Table / JSON output helpers ────────────────────────────────────────


def _print_table(rows: list[dict], columns: list[str], *, max_width: int = 50) -> None:
    if not rows:
        print("(no results)")
        return
    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            val = str(r.get(c, ""))
            if len(val) > max_width:
                val = val[: max_width - 1] + "…"
            widths[c] = max(widths[c], len(val))
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    print(header)
    print(sep)
    for r in rows:
        vals = []
        for c in columns:
            val = str(r.get(c, ""))
            if len(val) > max_width:
                val = val[: max_width - 1] + "…"
            vals.append(val.ljust(widths[c]))
        print(" | ".join(vals))


# ── Sub-commands ───────────────────────────────────────────────────────

def _cmd_history(client, args: argparse.Namespace) -> None:
    """List alert history with filtering."""
    params: dict[str, Any] = {}
    if args.limit:
        params["limit"] = args.limit
    if args.severity:
        params["severity"] = args.severity
    if args.since:
        since = datetime.now(timezone.utc) - timedelta(hours=args.since)
        params["since"] = since.isoformat()
    if args.ack:
        params["acknowledged"] = "true"
    elif args.unack:
        params["acknowledged"] = "false"

    resp = client.get("/alerts", params=params)
    resp.raise_for_status()
    data = resp.json()
    alerts = data if isinstance(data, list) else data.get("alerts", [data])

    if args.format == "json":
        _print_json(alerts)
        return

    if not alerts:
        print("No alerts found matching filters.")
        return

    # Enrich display
    display_rows = []
    for a in alerts:
        sev = a.get("severity", "info")
        display_rows.append({
            "": _severity_icon(sev),
            "id": str(a.get("id", ""))[:12],
            "severity": _colorize(sev.upper(), sev),
            "rule": a.get("rule_id", a.get("rule_name", "")),
            "message": a.get("message", ""),
            "ack": "✓" if a.get("acknowledged") else "",
            "time": a.get("created_at", ""),
        })
    _print_table(display_rows, ["", "id", "severity", "rule", "message", "ack", "time"])
    print(f"\n  Total: {len(alerts)} alert(s)")


def _cmd_rules(client, args: argparse.Namespace) -> None:
    """List configured alert rules."""
    resp = client.get("/alert-rules")
    resp.raise_for_status()
    data = resp.json()
    rules = data if isinstance(data, list) else data.get("rules", [data])

    if args.format == "json":
        _print_json(rules)
        return

    if not rules:
        print("No alert rules configured.")
        return

    display_rows = []
    for r in rules:
        silenced = r.get("silenced_until", "")
        status = "🔇 silenced" if silenced else "✅ active"
        display_rows.append({
            "id": str(r.get("id", r.get("name", "")))[:20],
            "metric": r.get("metric", ""),
            "condition": r.get("condition", ""),
            "threshold": str(r.get("threshold", "")),
            "severity": r.get("severity", ""),
            "status": status,
        })
    _print_table(display_rows, ["id", "metric", "condition", "threshold", "severity", "status"])
    print(f"\n  Total: {len(rules)} rule(s)")


def _cmd_test(client, args: argparse.Namespace) -> None:
    """Test a rule against a specific session to see if it would fire."""
    resp = client.post(
        f"/alert-rules/{args.rule_id}/test",
        json={"session_id": args.session_id},
    )
    resp.raise_for_status()
    result = resp.json()

    would_fire = result.get("would_fire", result.get("triggered", False))
    icon = "🔴 WOULD FIRE" if would_fire else "✅ Would NOT fire"
    print(f"\n  Rule: {args.rule_id}")
    print(f"  Session: {args.session_id}")
    print(f"  Result: {icon}")

    if result.get("metric_value") is not None:
        print(f"  Metric value: {result['metric_value']}")
    if result.get("threshold") is not None:
        print(f"  Threshold: {result['threshold']}")
    if result.get("details"):
        print(f"  Details: {result['details']}")
    print()


def _cmd_ack(client, args: argparse.Namespace) -> None:
    """Acknowledge an alert."""
    body: dict[str, Any] = {}
    if args.note:
        body["note"] = args.note
    resp = client.post(f"/alerts/{args.alert_id}/acknowledge", json=body)
    resp.raise_for_status()
    print(f"  ✓ Alert {args.alert_id} acknowledged.")
    if args.note:
        print(f"    Note: {args.note}")


def _cmd_silence(client, args: argparse.Namespace) -> None:
    """Silence a rule for a given duration."""
    duration = args.duration or 60
    resp = client.post(
        f"/alert-rules/{args.rule_id}/silence",
        json={"duration_minutes": duration},
    )
    resp.raise_for_status()
    print(f"  🔇 Rule {args.rule_id} silenced for {duration} minutes.")


def _cmd_unsilence(client, args: argparse.Namespace) -> None:
    """Unsilence a previously silenced rule."""
    resp = client.delete(f"/alert-rules/{args.rule_id}/silence")
    resp.raise_for_status()
    print(f"  🔊 Rule {args.rule_id} unsilenced.")


def _cmd_stats(client, args: argparse.Namespace) -> None:
    """Show alert statistics for a given period."""
    params: dict[str, Any] = {}
    if args.period:
        params["period"] = args.period

    resp = client.get("/alerts/stats", params=params)
    resp.raise_for_status()
    stats = resp.json()

    if args.format == "json":
        _print_json(stats)
        return

    period = args.period or "day"
    print(f"\n  📊 Alert Statistics ({period})")
    print(f"  {'─' * 40}")

    total = stats.get("total", 0)
    acked = stats.get("acknowledged", 0)
    unacked = total - acked

    print(f"  Total alerts:        {total}")
    print(f"  Acknowledged:        {acked}")
    print(f"  Unacknowledged:      {unacked}")

    by_severity = stats.get("by_severity", {})
    if by_severity:
        print(f"\n  By severity:")
        for sev in ["critical", "warning", "info"]:
            count = by_severity.get(sev, 0)
            if count > 0:
                print(f"    {_severity_icon(sev)} {sev}: {count}")

    by_rule = stats.get("by_rule", {})
    if by_rule:
        print(f"\n  Top rules:")
        sorted_rules = sorted(by_rule.items(), key=lambda x: x[1], reverse=True)
        for rule, count in sorted_rules[:10]:
            print(f"    {rule}: {count}")

    mttr = stats.get("mean_time_to_ack_minutes")
    if mttr is not None:
        print(f"\n  Mean time to ack:    {mttr:.1f} min")
    print()


# ── Entry point (called from main CLI) ─────────────────────────────────

def cmd_alert(args: argparse.Namespace) -> None:
    """Dispatch alert sub-commands."""
    client = get_client_only(args)
    sub = getattr(args, "alert_sub", None)
    dispatch = {
        "history": _cmd_history,
        "rules": _cmd_rules,
        "test": _cmd_test,
        "ack": _cmd_ack,
        "silence": _cmd_silence,
        "unsilence": _cmd_unsilence,
        "stats": _cmd_stats,
    }
    if sub not in dispatch:
        print("Usage: agentlens-cli alert {history|rules|test|ack|silence|unsilence|stats}")
        print("Run 'agentlens-cli alert <command> --help' for details.")
        sys.exit(1)
    dispatch[sub](client, args)


def register_alert_parser(subparsers) -> None:
    """Register the 'alert' command and its sub-commands on the argparse subparsers."""
    alert_parser = subparsers.add_parser("alert", help="Alert management & operations")
    alert_subs = alert_parser.add_subparsers(dest="alert_sub")

    # history
    p = alert_subs.add_parser("history", help="List alert history with filters")
    p.add_argument("--severity", choices=["info", "warning", "critical"])
    p.add_argument("--since", type=float, metavar="HOURS", help="Show alerts from last N hours")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--ack", action="store_true", help="Show only acknowledged")
    p.add_argument("--unack", action="store_true", help="Show only unacknowledged")
    p.add_argument("--format", choices=["table", "json"], default="table")

    # rules
    p = alert_subs.add_parser("rules", help="List configured alert rules")
    p.add_argument("--format", choices=["table", "json"], default="table")

    # test
    p = alert_subs.add_parser("test", help="Test a rule against a session")
    p.add_argument("rule_id", help="Rule ID to test")
    p.add_argument("session_id", help="Session ID to test against")

    # ack
    p = alert_subs.add_parser("ack", help="Acknowledge an alert")
    p.add_argument("alert_id", help="Alert ID to acknowledge")
    p.add_argument("--note", help="Acknowledgment note")

    # silence
    p = alert_subs.add_parser("silence", help="Silence a rule temporarily")
    p.add_argument("rule_id", help="Rule ID to silence")
    p.add_argument("--duration", type=int, default=60, metavar="MINUTES", help="Silence duration (default: 60)")

    # unsilence
    p = alert_subs.add_parser("unsilence", help="Unsilence a rule")
    p.add_argument("rule_id", help="Rule ID to unsilence")

    # stats
    p = alert_subs.add_parser("stats", help="Alert statistics summary")
    p.add_argument("--period", choices=["day", "week", "month"], default="day")
    p.add_argument("--format", choices=["table", "json"], default="table")
