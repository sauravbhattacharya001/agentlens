"""CLI baseline command – manage agent performance baselines.

Subcommands:
    agentlens-cli baseline list [--json]
    agentlens-cli baseline show <agent_name> [--json]
    agentlens-cli baseline record <session_id>
    agentlens-cli baseline check <session_id> [--json]
    agentlens-cli baseline delete <agent_name>
"""

from __future__ import annotations

import argparse
import sys

import httpx

from agentlens.cli_common import get_client, print_json


# ── Status symbols ───────────────────────────────────────────────────

_STATUS_ICONS = {
    "normal": "✅",
    "improvement": "🟢",
    "warning": "⚠️",
    "regression": "🔴",
}

_VERDICT_ICONS = {
    "healthy": "✅ healthy",
    "improved": "🟢 improved",
    "warning": "⚠️  warning",
    "regression": "🔴 regression",
}


# ── Helpers ──────────────────────────────────────────────────────────

def _fmt_num(val: float | None) -> str:
    if val is None:
        return "—"
    if abs(val) >= 1000:
        return f"{val:,.0f}"
    return f"{val:.1f}"


def _print_baseline_table(baselines: list[dict]) -> None:
    if not baselines:
        print("No baselines recorded yet.")
        return

    cols = ["agent_name", "samples", "avg_total_tokens", "avg_event_count",
            "avg_error_count", "avg_processing_ms", "updated_at"]
    labels = {
        "agent_name": "Agent",
        "samples": "Samples",
        "avg_total_tokens": "Avg Tokens",
        "avg_event_count": "Avg Events",
        "avg_error_count": "Avg Errors",
        "avg_processing_ms": "Avg Proc (ms)",
        "updated_at": "Updated",
    }

    def cell(row: dict, col: str) -> str:
        v = row.get(col)
        if col == "updated_at" and v:
            return v[:10]  # date only
        if isinstance(v, float):
            return _fmt_num(v)
        return str(v) if v is not None else "—"

    widths = {c: max(len(labels[c]), *(len(cell(r, c)) for r in baselines)) for c in cols}
    header = " │ ".join(labels[c].ljust(widths[c]) for c in cols)
    sep = "─┼─".join("─" * widths[c] for c in cols)
    print(header)
    print(sep)
    for row in baselines:
        line = " │ ".join(cell(row, c).ljust(widths[c]) for c in cols)
        print(line)
    print(f"\n({len(baselines)} baseline(s))")


def _print_check_result(data: dict) -> None:
    verdict = data.get("verdict", "unknown")
    print(f"Agent:    {data.get('agent_name', '?')}")
    print(f"Session:  {data.get('session_id', '?')}")
    print(f"Samples:  {data.get('baseline_samples', '?')}")
    print(f"Verdict:  {_VERDICT_ICONS.get(verdict, verdict)}")
    print()

    checks = data.get("checks", {})
    if not checks:
        return

    labels = {
        "total_tokens": "Total Tokens",
        "tokens_in": "Tokens In",
        "tokens_out": "Tokens Out",
        "event_count": "Event Count",
        "error_count": "Error Count",
        "processing_ms": "Processing (ms)",
    }

    name_w = max(len(v) for v in labels.values())
    print(f"  {'Metric'.ljust(name_w)}  {'Baseline':>12}  {'Actual':>12}  {'Delta':>8}  Status")
    print(f"  {'─' * name_w}  {'─' * 12}  {'─' * 12}  {'─' * 8}  ──────")
    for key, label in labels.items():
        c = checks.get(key)
        if not c:
            continue
        delta = c.get("delta_pct")
        delta_str = f"{delta:+.1f}%" if delta is not None else "—"
        icon = _STATUS_ICONS.get(c.get("status", ""), "")
        print(f"  {label.ljust(name_w)}  {_fmt_num(c.get('baseline')):>12}  "
              f"{_fmt_num(c.get('actual')):>12}  {delta_str:>8}  {icon} {c.get('status', '')}")


def _print_baseline_detail(data: dict) -> None:
    print(f"Agent:       {data.get('agent_name', '?')}")
    print(f"Samples:     {data.get('samples', 0)}")
    print(f"Updated:     {data.get('updated_at', '?')}")
    print()
    metrics = [
        ("Avg Tokens In", data.get("avg_tokens_in")),
        ("Avg Tokens Out", data.get("avg_tokens_out")),
        ("Avg Total Tokens", data.get("avg_total_tokens")),
        ("Avg Events", data.get("avg_event_count")),
        ("Avg Errors", data.get("avg_error_count")),
        ("Avg Processing (ms)", data.get("avg_processing_ms")),
        ("Avg Duration (ms)", data.get("avg_duration_ms")),
        ("P95 Tokens", data.get("p95_total_tokens")),
        ("P95 Processing (ms)", data.get("p95_processing_ms")),
    ]
    label_w = max(len(m[0]) for m in metrics)
    for label, val in metrics:
        print(f"  {label.ljust(label_w)}  {_fmt_num(val)}")

    recent = data.get("recent_session_ids", [])
    if recent:
        print(f"\nRecent sessions ({len(recent)}):")
        for sid in recent[-5:]:
            print(f"  • {sid}")
        if len(recent) > 5:
            print(f"  … and {len(recent) - 5} more")


# ── Subcommand handlers ─────────────────────────────────────────────

def _cmd_list(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.get("/baselines")
    resp.raise_for_status()
    data = resp.json()
    baselines = data.get("baselines", [])
    if getattr(args, "json", False):
        print_json(data)
    else:
        _print_baseline_table(baselines)


def _cmd_show(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.get(f"/baselines/{args.agent_name}")
    if resp.status_code == 404:
        print(f"No baseline found for agent '{args.agent_name}'.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
    if getattr(args, "json", False):
        print_json(data)
    else:
        _print_baseline_detail(data)


def _cmd_record(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.post("/baselines/record", json={"session_id": args.session_id})
    if resp.status_code in (400, 404):
        print(resp.json().get("error", "Unknown error"), file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
    print(f"✅ {data.get('message', 'Baseline updated')}")
    print(f"   Agent: {data.get('agent_name', '?')}  |  Samples: {data.get('samples', '?')}")


def _cmd_check(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.post("/baselines/check", json={"session_id": args.session_id})
    if resp.status_code in (400, 404):
        print(resp.json().get("error", "Unknown error"), file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
    if getattr(args, "json", False):
        print_json(data)
    else:
        _print_check_result(data)


def _cmd_delete(client: httpx.Client, args: argparse.Namespace) -> None:
    resp = client.delete(f"/baselines/{args.agent_name}")
    if resp.status_code == 404:
        print(f"No baseline found for agent '{args.agent_name}'.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
    print(f"🗑️  {data.get('message', 'Baseline deleted')}")


# ── Public API ───────────────────────────────────────────────────────

def register_baseline_parser(sub: argparse._SubParsersAction) -> None:
    """Register the 'baseline' subcommand with its own subcommands."""
    p = sub.add_parser("baseline", help="Manage agent performance baselines (record, check, compare)")
    bsub = p.add_subparsers(dest="baseline_action")

    lp = bsub.add_parser("list", help="List all agent baselines")
    lp.add_argument("--json", action="store_true", help="Output as JSON")

    sp = bsub.add_parser("show", help="Show detailed baseline for an agent")
    sp.add_argument("agent_name", help="Agent name")
    sp.add_argument("--json", action="store_true", help="Output as JSON")

    rp = bsub.add_parser("record", help="Record a session into its agent's baseline")
    rp.add_argument("session_id", help="Session ID to record")

    cp = bsub.add_parser("check", help="Check a session against its agent's baseline")
    cp.add_argument("session_id", help="Session ID to check")
    cp.add_argument("--json", action="store_true", help="Output as JSON")

    dp = bsub.add_parser("delete", help="Delete baseline for an agent")
    dp.add_argument("agent_name", help="Agent name whose baseline to delete")


def cmd_baseline(args: argparse.Namespace) -> None:
    """Dispatch baseline subcommands."""
    client, _ = get_client(args)
    action = getattr(args, "baseline_action", None)
    if not action:
        print("Usage: agentlens-cli baseline {list|show|record|check|delete}", file=sys.stderr)
        sys.exit(1)
    dispatch = {
        "list": _cmd_list,
        "show": _cmd_show,
        "record": _cmd_record,
        "check": _cmd_check,
        "delete": _cmd_delete,
    }
    dispatch[action](client, args)
