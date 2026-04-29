"""CLI subcommand: agentlens collaboration — multi-agent teamwork analysis.

Analyzes multi-agent sessions to detect collaboration patterns, handoff
quality, communication bottlenecks, delegation chains, and collective
intelligence.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


GRADE_ICONS = {
    "elite": "🏆",
    "strong": "💪",
    "functional": "✅",
    "struggling": "⚠️",
    "dysfunctional": "🔴",
}

PATTERN_ICONS = {
    "orchestrated": "🎯",
    "peer_to_peer": "🤝",
    "pipeline": "➡️",
    "swarm": "🐝",
    "hierarchical": "🏛️",
    "solo": "👤",
}


def cmd_collaboration(args: argparse.Namespace) -> None:
    """Analyze multi-agent collaboration for a session."""
    if args.session_id:
        _analyze_session(args)
    elif args.list:
        _list_sessions(args)
    else:
        print("Error: provide --session-id or --list", file=sys.stderr)
        sys.exit(1)


def _analyze_session(args: argparse.Namespace) -> None:
    """Analyze collaboration for a single session."""
    client, endpoint = get_client(args)

    try:
        resp = client.get(f"{endpoint}/collaboration/{args.session_id}")
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print_json(data)
    else:
        _render_analysis(data)


def _list_sessions(args: argparse.Namespace) -> None:
    """List multi-agent sessions with collaboration scores."""
    client, endpoint = get_client(args)
    params = {}
    if args.limit:
        params["limit"] = args.limit

    try:
        resp = client.get(f"{endpoint}/collaboration", params=params)
        resp.raise_for_status()
        sessions = resp.json()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print_json(sessions)
        return

    if not sessions:
        print("No multi-agent sessions found.")
        return

    print("=" * 70)
    print("  MULTI-AGENT COLLABORATION SESSIONS")
    print("=" * 70)
    print(f"  {'Session':<24} {'Agents':<8} {'Score':<8} {'Grade':<15} {'Pattern'}")
    print(f"  {'─'*23} {'─'*7} {'─'*7} {'─'*14} {'─'*18}")

    for s in sessions:
        grade_icon = GRADE_ICONS.get(s.get("grade", ""), "•")
        pattern_icon = PATTERN_ICONS.get(s.get("collaboration_pattern", ""), "•")
        sid = s.get("session_id", "?")
        sid_short = sid[:20] + "…" if len(sid) > 20 else sid
        print(
            f"  {sid_short:<24} {s.get('agent_count', 0):<8} "
            f"{s.get('teamwork_score', 0):<8.0f} "
            f"{grade_icon} {s.get('grade', '?'):<12} "
            f"{pattern_icon} {s.get('collaboration_pattern', '?')}"
        )

    print()
    print("=" * 70)


def _render_analysis(data: dict) -> None:
    """Render detailed collaboration analysis."""
    print("=" * 62)
    print("  AGENT COLLABORATION ANALYSIS")
    print("=" * 62)
    print(f"  Session:    {data.get('session_id', '?')}")
    print(f"  Agents:     {data.get('agent_count', 0)}")
    print(f"  Events:     {data.get('event_count', 0)}")

    pattern = data.get("collaboration_pattern", "unknown")
    grade = data.get("grade", "unknown")
    score = data.get("teamwork_score", 0)
    p_icon = PATTERN_ICONS.get(pattern, "•")
    g_icon = GRADE_ICONS.get(grade, "•")

    print(f"  Pattern:    {p_icon} {pattern}")
    print(f"  Grade:      {g_icon} {grade} ({score:.0f}/100)")
    print()

    # Engine scores
    engines = data.get("engines", [])
    if engines:
        print("─" * 62)
        print("  ENGINE SCORES")
        print("─" * 62)
        for eng in engines:
            s = eng.get("score", 0)
            bar_len = int(s / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  {eng.get('engine', '?'):<30} {bar} {s:.0f}")
        print()

    # Handoffs
    handoffs = data.get("handoffs", [])
    if handoffs:
        print("─" * 62)
        print(f"  HANDOFFS ({len(handoffs)} total)")
        print("─" * 62)
        for h in handoffs[:10]:
            v_icon = {"clean": "✅", "acceptable": "🟡", "lossy": "🟠", "failed": "🔴"}.get(
                h.get("verdict", ""), "•")
            print(f"  {v_icon} {h.get('source_agent', '?')} → {h.get('target_agent', '?')} "
                  f"[{h.get('verdict', '?')}] latency={h.get('latency_ms', 0):.0f}ms")
        print()

    # Bottlenecks
    bottlenecks = data.get("bottlenecks", [])
    if bottlenecks:
        print("─" * 62)
        print(f"  BOTTLENECKS ({len(bottlenecks)} detected)")
        print("─" * 62)
        for b in bottlenecks:
            print(f"  ⚠ {b.get('agent_id', '?')} [{b.get('severity', '?')}] "
                  f"fan-in={b.get('fan_in', 0)} fan-out={b.get('fan_out', 0)}")
        print()

    # Workload
    workload = data.get("workload", [])
    if workload:
        print("─" * 62)
        gini = data.get("gini_coefficient", 0)
        print(f"  WORKLOAD BALANCE (Gini={gini:.3f})")
        print("─" * 62)
        for w in workload:
            frac = w.get("load_fraction", 0)
            bar_len = int(frac * 40)
            bar = "█" * max(bar_len, 1)
            status_icon = {"overloaded": "🔴", "balanced": "✅", "idle": "💤"}.get(
                w.get("status", ""), "•")
            print(f"  {w.get('agent_id', '?'):<20} {bar} {w.get('event_count', 0)} events "
                  f"{status_icon} {w.get('status', '?')}")
        print()

    print("=" * 62)


def register_collaboration(subparsers: Any) -> None:
    """Register the collaboration subcommand."""
    p = subparsers.add_parser(
        "collaboration",
        help="Analyze multi-agent collaboration and teamwork",
        description=(
            "Analyze multi-agent sessions for collaboration patterns, handoff "
            "quality, communication bottlenecks, delegation chains, workload "
            "balance, and collective intelligence."
        ),
    )
    p.add_argument("--session-id", "-s", help="Analyze a specific session")
    p.add_argument("--list", "-l", action="store_true", help="List multi-agent sessions")
    p.add_argument("--limit", type=int, help="Max sessions to list")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_collaboration)
