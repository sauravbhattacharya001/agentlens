"""CLI audit command — view and export agent action audit trails."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


_SEVERITY_COLORS = {
    "critical": "\033[91m",
    "warning": "\033[93m",
    "info": "\033[94m",
    "debug": "\033[90m",
}
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _ts(iso: str) -> str:
    """Format an ISO timestamp to a human-friendly string."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso or "?"


def _truncate(text: str, max_len: int = 60) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _severity_badge(severity: str, no_color: bool = False) -> str:
    s = severity.upper()
    if no_color:
        return f"[{s}]"
    color = _SEVERITY_COLORS.get(severity.lower(), "")
    return f"{color}[{s}]{_RESET}"


def _format_table(entries: list[dict], no_color: bool = False) -> str:
    """Render audit entries as a formatted table."""
    if not entries:
        return "  No audit entries found."

    lines = []

    # Header
    hdr = (
        f"  {'Timestamp':<20} {'Severity':<12} {'Agent':<16} "
        f"{'Action':<20} {'Model':<14} {'Detail'}"
    )
    sep = (
        f"  {'─' * 20} {'─' * 12} {'─' * 16} "
        f"{'─' * 20} {'─' * 14} {'─' * 40}"
    )
    lines.append(hdr)
    lines.append(sep)

    for e in entries:
        ts = _ts(e.get("timestamp", ""))
        severity = _severity_badge(e.get("severity", "info"), no_color)
        agent = _truncate(e.get("agent_name", e.get("agent_id", "?")), 15)
        action = _truncate(e.get("action", "?"), 19)
        model = _truncate(e.get("model", "-"), 13)
        detail = _truncate(e.get("detail", e.get("message", "")), 40)

        lines.append(
            f"  {ts:<20} {severity:<12} {agent:<16} "
            f"{action:<20} {model:<14} {detail}"
        )

    return "\n".join(lines)


def _format_detail(entry: dict, no_color: bool = False) -> str:
    """Render a single audit entry in verbose detail mode."""
    lines = []
    b = "" if no_color else _BOLD
    r = "" if no_color else _RESET

    lines.append(f"{b}Audit Entry{r}")
    lines.append(f"  ID:        {entry.get('id', '?')}")
    lines.append(f"  Timestamp: {_ts(entry.get('timestamp', ''))}")
    lines.append(f"  Severity:  {_severity_badge(entry.get('severity', 'info'), no_color)}")
    lines.append(f"  Agent:     {entry.get('agent_name', entry.get('agent_id', '?'))}")
    lines.append(f"  Session:   {entry.get('session_id', '-')}")
    lines.append(f"  Action:    {entry.get('action', '?')}")
    lines.append(f"  Model:     {entry.get('model', '-')}")
    lines.append(f"  Tokens:    {entry.get('total_tokens', '-')}")
    lines.append(f"  Cost:      ${entry.get('cost_usd', 0):.4f}")

    detail = entry.get("detail", entry.get("message", ""))
    if detail:
        lines.append(f"  Detail:    {detail}")

    metadata = entry.get("metadata", {})
    if metadata:
        lines.append(f"  Metadata:  {json.dumps(metadata, indent=4)}")

    return "\n".join(lines)


def _summary_stats(entries: list[dict]) -> str:
    """Produce summary statistics for audit entries."""
    if not entries:
        return ""

    lines = ["\n  📊 Audit Summary"]

    total = len(entries)
    lines.append(f"  Total entries: {total}")

    # Severity breakdown
    sev_counts: dict[str, int] = {}
    for e in entries:
        s = e.get("severity", "info")
        sev_counts[s] = sev_counts.get(s, 0) + 1

    if sev_counts:
        parts = [f"{k}: {v}" for k, v in sorted(sev_counts.items(), key=lambda x: -x[1])]
        lines.append(f"  By severity: {', '.join(parts)}")

    # Action breakdown (top 5)
    action_counts: dict[str, int] = {}
    for e in entries:
        a = e.get("action", "unknown")
        action_counts[a] = action_counts.get(a, 0) + 1

    top_actions = sorted(action_counts.items(), key=lambda x: -x[1])[:5]
    if top_actions:
        parts = [f"{a}: {c}" for a, c in top_actions]
        lines.append(f"  Top actions: {', '.join(parts)}")

    # Agent breakdown (top 5)
    agent_counts: dict[str, int] = {}
    for e in entries:
        a = e.get("agent_name", e.get("agent_id", "unknown"))
        agent_counts[a] = agent_counts.get(a, 0) + 1

    top_agents = sorted(agent_counts.items(), key=lambda x: -x[1])[:5]
    if top_agents:
        parts = [f"{a}: {c}" for a, c in top_agents]
        lines.append(f"  Top agents: {', '.join(parts)}")

    # Time range
    timestamps = [e.get("timestamp", "") for e in entries if e.get("timestamp")]
    if timestamps:
        lines.append(f"  Time range: {_ts(min(timestamps))} → {_ts(max(timestamps))}")

    # Total cost
    total_cost = sum(e.get("cost_usd", 0) for e in entries)
    if total_cost > 0:
        lines.append(f"  Total cost: ${total_cost:.4f}")

    lines.append("")
    return "\n".join(lines)


def cmd_audit(args: Any) -> None:
    """Fetch and display the agent action audit trail."""
    import os
    import urllib.request

    endpoint = (
        getattr(args, "endpoint", None)
        or os.environ.get("AGENTLENS_ENDPOINT", "http://localhost:3000")
    ).rstrip("/")
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("AGENTLENS_API_KEY", "default")
    )
    headers = {"x-api-key": api_key}

    params = []
    if getattr(args, "agent", None):
        params.append(f"agent={args.agent}")
    if getattr(args, "action_filter", None):
        params.append(f"action={args.action_filter}")
    if getattr(args, "severity", None):
        params.append(f"severity={args.severity}")
    if getattr(args, "model", None):
        params.append(f"model={args.model}")
    if getattr(args, "session", None):
        params.append(f"session_id={args.session}")
    if getattr(args, "since", None):
        params.append(f"since_hours={args.since}")
    if getattr(args, "limit", None):
        params.append(f"limit={args.limit}")

    qs = ("?" + "&".join(params)) if params else ""
    url = f"{endpoint}/audit{qs}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    entries = data.get("entries", [])

    # JSON output
    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2))
        return

    # CSV export
    if getattr(args, "format", "table") == "csv":
        _export_csv(entries, getattr(args, "output", None))
        return

    no_color = getattr(args, "no_color", False)

    # Detail view for a single entry
    if getattr(args, "entry_id", None):
        if entries:
            print(_format_detail(entries[0], no_color))
        else:
            print("  Entry not found.")
        return

    # Table view
    title = "\n🔍 Agent Audit Trail"
    filters_active = []
    if getattr(args, "agent", None):
        filters_active.append(f"agent={args.agent}")
    if getattr(args, "severity", None):
        filters_active.append(f"severity={args.severity}")
    if getattr(args, "action_filter", None):
        filters_active.append(f"action={args.action_filter}")
    if getattr(args, "since", None):
        filters_active.append(f"last {args.since}h")

    if filters_active:
        title += f"  ({', '.join(filters_active)})"

    print(title)
    print()
    print(_format_table(entries, no_color))

    if getattr(args, "stats", False):
        print(_summary_stats(entries))

    # File output
    output = getattr(args, "output", None)
    if output and entries:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"\n  Saved to {output}")

    print()


def _export_csv(entries: list[dict], output: str | None) -> None:
    """Export audit entries as CSV."""
    import csv
    import io

    fields = [
        "timestamp", "severity", "agent_name", "agent_id", "session_id",
        "action", "model", "total_tokens", "cost_usd", "detail",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()

    for e in entries:
        row = {k: e.get(k, "") for k in fields}
        if not row["agent_name"]:
            row["agent_name"] = row["agent_id"]
        if not row["detail"]:
            row["detail"] = e.get("message", "")
        writer.writerow(row)

    csv_text = buf.getvalue()

    if output:
        with open(output, "w", encoding="utf-8", newline="") as f:
            f.write(csv_text)
        print(f"  Exported {len(entries)} entries to {output}")
    else:
        print(csv_text)


def register_audit_parser(subparsers: Any) -> None:
    """Register the audit subcommand."""
    p = subparsers.add_parser(
        "audit",
        help="View agent action audit trail with filtering and export",
    )
    p.add_argument("entry_id", nargs="?", default=None,
                    help="View a specific audit entry by ID")
    p.add_argument("--agent", help="Filter by agent name or ID")
    p.add_argument("--action", dest="action_filter",
                    help="Filter by action type (e.g., llm_call, tool_use, error)")
    p.add_argument("--severity",
                    choices=["debug", "info", "warning", "critical"],
                    help="Filter by minimum severity level")
    p.add_argument("--model", help="Filter by model name")
    p.add_argument("--session", help="Filter by session ID")
    p.add_argument("--since", type=int, default=24,
                    help="Show entries from last N hours (default: 24)")
    p.add_argument("--limit", type=int, default=50,
                    help="Maximum entries to return (default: 50)")
    p.add_argument("--format", choices=["table", "csv", "json"],
                    default="table", help="Output format")
    p.add_argument("--output", "-o", help="Save output to file")
    p.add_argument("--stats", action="store_true",
                    help="Show summary statistics")
    p.add_argument("--no-color", action="store_true",
                    help="Disable colored output")
    p.add_argument("--json", dest="json_output", action="store_true",
                    help="Output raw JSON")
    p.add_argument("--endpoint", help="AgentLens backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_audit)
