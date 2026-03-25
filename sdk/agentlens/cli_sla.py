"""CLI sla command — evaluate sessions against SLA policies and show compliance."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client_only as _get_client
from agentlens.sla import (
    ComplianceStatus,
    SLAEvaluator,
    SLAPolicy,
    SLObjective,
    development_policy,
    production_policy,
)


def _status_icon(status: ComplianceStatus) -> str:
    return {
        ComplianceStatus.COMPLIANT: "✅",
        ComplianceStatus.AT_RISK: "⚠️",
        ComplianceStatus.VIOLATED: "❌",
    }.get(status, "?")


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}\033[0m"


def _status_color(status: ComplianceStatus) -> str:
    label = status.value.upper().replace("_", " ")
    colors = {
        ComplianceStatus.COMPLIANT: "\033[32m",
        ComplianceStatus.AT_RISK: "\033[33m",
        ComplianceStatus.VIOLATED: "\033[31m",
    }
    return _color(label, colors.get(status, ""))


def _progress_bar(percent: float, width: int = 20) -> str:
    """Render a mini progress bar for error budget remaining."""
    filled = int(percent / 100 * width)
    filled = max(0, min(width, filled))
    empty = width - filled
    if percent > 50:
        bar_color = "\033[32m" if sys.stdout.isatty() else ""
    elif percent > 20:
        bar_color = "\033[33m" if sys.stdout.isatty() else ""
    else:
        bar_color = "\033[31m" if sys.stdout.isatty() else ""
    reset = "\033[0m" if sys.stdout.isatty() else ""
    return f"{bar_color}{'█' * filled}{'░' * empty}{reset} {percent:.0f}%"


def _build_custom_policy(args: argparse.Namespace) -> SLAPolicy | None:
    """Build a custom policy from --latency, --error-rate, --token-budget flags."""
    objectives: list[SLObjective] = []

    latency = getattr(args, "latency", None)
    error_rate = getattr(args, "error_rate_target", None)
    token_budget = getattr(args, "token_budget", None)
    slo = getattr(args, "slo", 99.0) or 99.0

    if latency is not None:
        objectives.append(SLObjective.latency_p95(target_ms=latency, slo_percent=slo))
    if error_rate is not None:
        objectives.append(SLObjective.error_rate(target_rate=error_rate / 100.0, slo_percent=slo))
    if token_budget is not None:
        objectives.append(SLObjective.token_budget(target_per_session=int(token_budget), slo_percent=slo))

    if not objectives:
        return None

    return SLAPolicy(name="custom", description="Custom CLI policy", objectives=objectives)


def _fetch_sessions_with_events(
    client: httpx.Client, limit: int, agent_filter: str | None
) -> list[dict[str, Any]]:
    """Fetch sessions and their events from the backend."""
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    raw = resp.json()
    sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    if agent_filter:
        sessions = [
            s for s in sessions
            if agent_filter.lower() in (s.get("agent_name", "") or "").lower()
        ]

    result: list[dict[str, Any]] = []
    for s in sessions:
        sid = s.get("session_id") or s.get("id")
        if not sid:
            continue
        try:
            ev_resp = client.get(f"/sessions/{sid}/events", params={"limit": 500})
            ev_resp.raise_for_status()
            ev_raw = ev_resp.json()
            events = ev_raw if isinstance(ev_raw, list) else ev_raw.get("events", [])
        except (httpx.HTTPError, Exception):
            events = []
        result.append({"session_id": sid, "events": events, **s})

    return result


def cmd_sla(args: argparse.Namespace) -> None:
    """Evaluate sessions against SLA policies and display compliance report."""
    client = _get_client(args)
    limit = getattr(args, "limit", 100) or 100
    output_json = getattr(args, "json_output", False)
    agent_filter = getattr(args, "agent", None)
    preset = getattr(args, "policy", "production") or "production"
    verbose = getattr(args, "verbose", False)

    BOLD = "\033[1m" if sys.stdout.isatty() else ""
    DIM = "\033[2m" if sys.stdout.isatty() else ""
    RESET = "\033[0m" if sys.stdout.isatty() else ""
    CYAN = "\033[36m" if sys.stdout.isatty() else ""

    # Determine policy
    custom = _build_custom_policy(args)
    if custom:
        policy = custom
    elif preset == "development":
        policy = development_policy()
    else:
        policy = production_policy()

    if not output_json:
        print(f"\n{BOLD}⚖️  AgentLens SLA Compliance — {policy.name}{RESET}")
        if policy.description:
            print(f"{DIM}   {policy.description}{RESET}")
        if agent_filter:
            print(f"{DIM}   Agent filter: {agent_filter}{RESET}")
        print(f"{DIM}   Fetching up to {limit} sessions...{RESET}")
        print()

    # Fetch sessions
    sessions = _fetch_sessions_with_events(client, limit, agent_filter)

    if not sessions:
        if output_json:
            print(json.dumps({"error": "No sessions found"}, indent=2))
        else:
            print("   No sessions found. Nothing to evaluate.")
        return

    # Evaluate
    evaluator = SLAEvaluator()
    try:
        report = evaluator.evaluate(sessions, policy)
    except ValueError as e:
        if output_json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"   Error: {e}")
        return

    if output_json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    # Header
    icon = _status_icon(report.overall_status)
    status_str = _status_color(report.overall_status)
    print(f"   {BOLD}Overall:{RESET} {icon} {status_str}")
    print(
        f"   {DIM}Sessions evaluated: {report.total_sessions}  |  "
        f"Objectives: {report.compliant_objectives} pass, "
        f"{report.at_risk_objectives} at-risk, "
        f"{report.violated_objectives} fail{RESET}"
    )
    print()

    # Per-objective details
    for r in report.results:
        icon = _status_icon(r.status)
        status_str = _status_color(r.status)

        print(f"   {icon} {BOLD}{r.objective.name}{RESET}")
        print(
            f"      SLO target: {r.objective.slo_percent:.1f}%  │  "
            f"Actual: {CYAN}{r.compliance_percent:.2f}%{RESET}  │  "
            f"Status: {status_str}"
        )
        print(
            f"      Compliant: {r.compliant_sessions}/{r.total_sessions}  │  "
            f"Violations: {r.violation_count}"
        )
        print(
            f"      Error budget: {_progress_bar(r.error_budget_percent)}  "
            f"{DIM}({r.error_budget_remaining:.1f}/{r.error_budget_total:.1f} remaining){RESET}"
        )

        if verbose and r.violations:
            shown = r.violations[:10]
            print(f"      {DIM}Violating sessions: {', '.join(shown)}")
            if len(r.violations) > 10:
                print(f"      ... and {len(r.violations) - 10} more{RESET}")
            else:
                print(f"{RESET}", end="")

        if verbose and r.measured_values:
            vals = r.measured_values
            avg = sum(vals) / len(vals) if vals else 0
            mn, mx = min(vals), max(vals)
            print(
                f"      {DIM}Stats: avg={avg:.1f}  min={mn:.1f}  max={mx:.1f}{RESET}"
            )

        print()

    # Summary recommendation
    if report.overall_status == ComplianceStatus.VIOLATED:
        print(
            f"   {_color('⚡ Action needed:', chr(27) + '[1;31m')} "
            f"{report.violated_objectives} objective(s) breached. "
            f"Review violations with --verbose."
        )
    elif report.overall_status == ComplianceStatus.AT_RISK:
        print(
            f"   {_color('⚡ Watch closely:', chr(27) + '[1;33m')} "
            f"{report.at_risk_objectives} objective(s) approaching SLO threshold."
        )
    else:
        print(f"   {_color('All objectives met.', chr(27) + '[32m')} No action needed.")
    print()
