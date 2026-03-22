"""CLI budget command — manage and monitor cost budgets from the terminal.

Subcommands:
    agentlens budget list                         Show all budgets with status
    agentlens budget set <scope> <period> <limit> Create/update a budget
    agentlens budget check <session_id>           Check if a session is over budget
    agentlens budget delete <scope> [<period>]    Remove a budget
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _bar(pct: float, width: int = 20) -> str:
    """Render a percentage as a colored progress bar."""
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    if pct >= 100:
        return f"\033[31m{bar}\033[0m"  # red
    elif pct >= 80:
        return f"\033[33m{bar}\033[0m"  # yellow
    else:
        return f"\033[32m{bar}\033[0m"  # green


def _status_icon(status: str) -> str:
    return {"ok": "✅", "warning": "⚠️", "exceeded": "🚨"}.get(status, "❓")


def cmd_budget(args: argparse.Namespace, client: httpx.Client) -> None:
    """Dispatch budget subcommands."""
    sub = getattr(args, "budget_action", None)
    if sub == "list":
        _budget_list(args, client)
    elif sub == "set":
        _budget_set(args, client)
    elif sub == "check":
        _budget_check(args, client)
    elif sub == "delete":
        _budget_delete(args, client)
    else:
        print("Usage: agentlens budget {list|set|check|delete}", file=sys.stderr)
        sys.exit(1)


def _budget_list(args: argparse.Namespace, client: httpx.Client) -> None:
    """List all budgets with real-time spend and status bars."""
    resp = client.get("/budgets")
    resp.raise_for_status()
    data = resp.json()
    budgets = data.get("budgets", [])

    output_json = getattr(args, "json", False)
    if output_json:
        print(json.dumps(budgets, indent=2, default=str))
        return

    if not budgets:
        print("No budgets configured. Use 'agentlens budget set' to create one.")
        return

    print(f"\n{'─' * 80}")
    print(f"  💰 COST BUDGETS")
    print(f"{'─' * 80}\n")

    # Group by scope
    scopes: dict[str, list] = {}
    for b in budgets:
        scope = b.get("scope", "unknown")
        scopes.setdefault(scope, []).append(b)

    for scope, scope_budgets in scopes.items():
        scope_label = "🌐 Global" if scope == "global" else f"🤖 {scope}"
        print(f"  {scope_label}")
        print()

        for b in scope_budgets:
            period = b.get("period", "?")
            limit = b.get("limit_usd", 0)
            spend = b.get("current_spend", 0)
            pct = b.get("usage_pct", 0)
            remaining = b.get("remaining", 0)
            status = b.get("status", "ok")
            icon = _status_icon(status)
            bar = _bar(pct)

            print(f"    {icon} {period.upper():<10} ${spend:.4f} / ${limit:.4f}  [{bar}] {pct:.1f}%")
            print(f"       Remaining: ${remaining:.4f}")

            # Model breakdown
            breakdown = b.get("model_breakdown", {})
            if breakdown:
                top_models = sorted(breakdown.items(), key=lambda x: x[1].get("cost", 0), reverse=True)[:3]
                parts = []
                for model, info in top_models:
                    cost = info.get("cost", 0)
                    if cost > 0:
                        parts.append(f"{model}: ${cost:.4f}")
                if parts:
                    print(f"       Top models: {' | '.join(parts)}")
            print()

    # Summary
    total_limit = sum(b.get("limit_usd", 0) for b in budgets)
    total_spend = sum(b.get("current_spend", 0) for b in budgets)
    exceeded = sum(1 for b in budgets if b.get("status") == "exceeded")
    warnings = sum(1 for b in budgets if b.get("status") == "warning")

    print(f"{'─' * 80}")
    print(f"  Budgets: {len(budgets)} | Total limits: ${total_limit:.4f} | Total spend: ${total_spend:.4f}")
    if exceeded:
        print(f"  🚨 {exceeded} budget(s) EXCEEDED")
    if warnings:
        print(f"  ⚠️  {warnings} budget(s) in WARNING")
    print()


def _budget_set(args: argparse.Namespace, client: httpx.Client) -> None:
    """Create or update a budget."""
    scope = args.scope
    period = args.period
    limit_usd = args.limit_usd
    warn_pct = getattr(args, "warn_pct", 80)

    resp = client.put("/budgets", json={
        "scope": scope,
        "period": period,
        "limit_usd": limit_usd,
        "warn_pct": warn_pct,
    })
    resp.raise_for_status()
    data = resp.json()
    budget = data.get("budget", {})

    scope_label = "Global" if scope == "global" else scope
    status = budget.get("budget_status", "ok")
    icon = _status_icon(status)
    spend = budget.get("current_spend", 0)
    pct = budget.get("usage_pct", 0)

    print(f"\n  {icon} Budget set: {scope_label} / {period}")
    print(f"     Limit:    ${limit_usd:.4f}")
    print(f"     Warn at:  {warn_pct}%")
    print(f"     Spend:    ${spend:.4f} ({pct:.1f}%)")
    print(f"     Status:   {status}")
    print()


def _budget_check(args: argparse.Namespace, client: httpx.Client) -> None:
    """Check budget status for a specific session."""
    resp = client.get(f"/budgets/check/{args.session_id}")
    resp.raise_for_status()
    data = resp.json()

    output_json = getattr(args, "json", False)
    if output_json:
        print(json.dumps(data, indent=2, default=str))
        return

    agent = data.get("agent_name", "unknown")
    budgets = data.get("budgets", [])
    any_exceeded = data.get("any_exceeded", False)
    any_warning = data.get("any_warning", False)

    print(f"\n  🔍 Budget check for session {args.session_id}")
    print(f"     Agent: {agent}")
    print()

    if not budgets:
        print("     No budgets apply to this session.")
        print()
        return

    for b in budgets:
        scope = b.get("scope", "?")
        period = b.get("period", "?")
        limit = b.get("limit_usd", 0)
        spend = b.get("current_spend", 0)
        pct = b.get("usage_pct", 0)
        status = b.get("status", "ok")
        icon = _status_icon(status)
        bar = _bar(pct)

        scope_label = "Global" if scope == "global" else scope
        print(f"     {icon} {scope_label} / {period}: ${spend:.4f} / ${limit:.4f}  [{bar}] {pct:.1f}%")

    print()
    if any_exceeded:
        print("     🚨 BUDGET EXCEEDED — this agent is over limit!")
    elif any_warning:
        print("     ⚠️  Budget warning — approaching limit")
    else:
        print("     ✅ All budgets OK")
    print()


def _budget_delete(args: argparse.Namespace, client: httpx.Client) -> None:
    """Delete a budget by scope and optionally period."""
    scope = args.scope
    period = getattr(args, "period", None)

    if period:
        resp = client.delete(f"/budgets/{scope}/{period}")
    else:
        resp = client.delete(f"/budgets/{scope}")

    resp.raise_for_status()
    data = resp.json()

    if period:
        print(f"\n  🗑️  Deleted budget: {scope} / {period}")
    else:
        deleted = data.get("deleted", 0)
        print(f"\n  🗑️  Deleted {deleted} budget(s) for scope: {scope}")
    print()
