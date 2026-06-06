"""CLI tool-reliability command – tool reliability scorecard in the terminal.

Fetches tool-call events from the AgentLens backend and runs them through
:class:`~agentlens.tool_reliability_advisor.ToolReliabilityAdvisor` to produce
a terminal-rendered scorecard showing per-tool health grades, error rates,
latencies, and actionable playbook recommendations.

Usage:
    agentlens-cli tool-reliability [--agent AGENT] [--limit N] [--appetite cautious|balanced|aggressive]
        [--format table|json] [--output FILE] [--endpoint URL] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client_only
from agentlens.tool_reliability_advisor import (
    ToolReliabilityAdvisor,
    ToolReliabilityGrade,
    ToolVerdict,
)


# ── Display helpers ──────────────────────────────────────────────────────────


def _grade_icon(grade: ToolReliabilityGrade) -> str:
    return {
        ToolReliabilityGrade.A: "🟢",
        ToolReliabilityGrade.B: "🔵",
        ToolReliabilityGrade.C: "🟡",
        ToolReliabilityGrade.D: "🟠",
        ToolReliabilityGrade.F: "🔴",
    }.get(grade, "⚪")


def _verdict_icon(verdict: ToolVerdict) -> str:
    return {
        ToolVerdict.HEALTHY: "✅",
        ToolVerdict.WATCH: "👀",
        ToolVerdict.FLAKY: "⚡",
        ToolVerdict.DEGRADED: "⚠️",
        ToolVerdict.CIRCUIT_BREAK: "🚫",
        ToolVerdict.DEPRECATE_CANDIDATE: "🗑️",
        ToolVerdict.INSUFFICIENT_DATA: "❓",
    }.get(verdict, "•")


def _bar(value: float, max_val: float, width: int = 20) -> str:
    """Render a simple bar chart."""
    if max_val <= 0:
        return " " * width
    filled = int(min(value / max_val, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def _pct_color(pct: float) -> str:
    """Return severity label for error rate."""
    if pct >= 20:
        return "CRITICAL"
    if pct >= 10:
        return "HIGH"
    if pct >= 5:
        return "MEDIUM"
    return "LOW"


# ── Main command ─────────────────────────────────────────────────────────────


def cmd_tool_reliability(args: argparse.Namespace) -> None:
    client = get_client_only(args)
    agent_filter: str | None = getattr(args, "agent", None)
    limit: int = getattr(args, "limit", 100) or 100
    appetite: str = getattr(args, "appetite", "balanced") or "balanced"
    fmt: str = getattr(args, "format", "table") or "table"
    output: str | None = getattr(args, "output", None)

    # Fetch sessions
    params: dict[str, Any] = {"limit": limit}
    if agent_filter:
        params["agent"] = agent_filter

    try:
        resp = client.get("/api/sessions", params=params)
        resp.raise_for_status()
        sessions_data = resp.json()
        if isinstance(sessions_data, dict):
            sessions_data = sessions_data.get("sessions", [])
    except httpx.HTTPError as exc:
        print(f"Error fetching sessions: {exc}", file=sys.stderr)
        sys.exit(1)

    # Collect all tool events
    tool_events: list[dict[str, Any]] = []
    for sess in sessions_data:
        sid = sess.get("id") or sess.get("session_id", "")
        try:
            eresp = client.get(
                "/api/events",
                params={"session": sid, "limit": 500, "type": "tool_call"},
            )
            eresp.raise_for_status()
            events = eresp.json()
            if isinstance(events, dict):
                events = events.get("events", [])
        except httpx.HTTPError:
            continue

        for ev in events:
            ev.setdefault("session_id", sid)
            # Normalize: backend uses "type", advisor expects "event_type"
            if "event_type" not in ev and "type" in ev:
                ev["event_type"] = ev["type"]
            tool_events.append(ev)

    if not tool_events:
        print("No tool-call events found. Check your agent name or session limit.", file=sys.stderr)
        sys.exit(0)

    # Run through advisor
    advisor = ToolReliabilityAdvisor()
    report = advisor.analyze(tool_events, risk_appetite=appetite)

    if fmt == "json":
        out = report.to_json(indent=2)
    else:
        out = _render_table(report, len(sessions_data), len(tool_events))

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Output written to {output}", file=sys.stderr)
    else:
        print(out)


def _render_table(report: Any, session_count: int, event_count: int) -> str:
    lines: list[str] = []
    p = report.portfolio
    grade = report.grade

    # Header
    lines.append("")
    lines.append(f"  {_grade_icon(grade)} Tool Reliability Scorecard")
    lines.append(f"  {'─' * 60}")
    lines.append(f"  Grade: {grade.value}  │  Risk Appetite: {report.risk_appetite.value}")
    lines.append(f"  Tools: {p.total_tools}  │  Total Calls: {p.total_calls}  │  Sessions: {session_count}")
    lines.append(f"  Error Rate: {p.mean_error_rate:.1%}  │  Reliability: {p.reliability_score:.1f}/100  │  Band: {p.concentration_band.value}")
    lines.append(f"  Events analyzed: {event_count}")
    lines.append("")

    # Per-tool table
    if report.snapshots:
        max_calls = max(s.total_calls for s in report.snapshots) if report.snapshots else 1

        lines.append(f"  {'TOOL':<25} {'VERDICT':<14} {'SCORE':>6} {'ERR%':>7} {'P95 ms':>8} {'CALLS':>7} {'VOLUME':<22}")
        lines.append(f"  {'─' * 25} {'─' * 13} {'─' * 6} {'─' * 7} {'─' * 8} {'─' * 7} {'─' * 22}")

        # Sort by priority (worst first), then by score ascending
        sorted_snaps = sorted(
            report.snapshots,
            key=lambda s: (s.priority.value, s.reliability_score),
        )

        for s in sorted_snaps[:20]:
            icon = _verdict_icon(s.verdict)
            name = s.tool_name[:24]
            bar = _bar(s.total_calls, max_calls, 20)
            lines.append(
                f"  {name:<25} {icon} {s.verdict.value:<10} {s.reliability_score:>5.1f} "
                f"{s.error_rate:>6.1%} {s.p95_latency_ms:>7.0f} {s.total_calls:>7} {bar}"
            )

        lines.append("")
    else:
        lines.append("  No tools observed.")
        lines.append("")

    # Playbook
    if report.playbook:
        lines.append(f"  📋 Playbook ({len(report.playbook)} actions)")
        lines.append(f"  {'─' * 60}")
        for action in report.playbook[:8]:
            lines.append(
                f"  [{action.priority.value}] {action.id}"
            )
            lines.append(
                f"       {action.label}"
            )
            if action.tool_names:
                lines.append(
                    f"       Tools: {', '.join(action.tool_names[:5])}"
                )
        lines.append("")

    # Insights
    if report.insights:
        lines.append(f"  💡 Insights")
        lines.append(f"  {'─' * 60}")
        for insight in report.insights[:6]:
            lines.append(f"  • {insight}")
        lines.append("")

    return "\n".join(lines)


# ── Parser registration ──────────────────────────────────────────────────────


def register(sub: Any) -> None:
    p = sub.add_parser(
        "tool-reliability",
        help="Tool reliability scorecard — grades, error rates, latencies, and playbook",
        description=(
            "Analyze tool-call events and display a terminal scorecard showing "
            "per-tool reliability grades, error rates, p95 latencies, volume bars, "
            "and actionable playbook recommendations."
        ),
    )
    p.add_argument("--agent", help="Filter sessions by agent name")
    p.add_argument("--limit", type=int, default=100, help="Max sessions to analyze (default: 100)")
    p.add_argument(
        "--appetite",
        choices=["cautious", "balanced", "aggressive"],
        default="balanced",
        help="Risk appetite for grading (default: balanced)",
    )
    p.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    p.add_argument("--output", "-o", help="Write output to file")
