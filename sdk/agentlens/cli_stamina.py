"""CLI subcommand: agentlens stamina — agent fatigue profiling.

Detects intra-session performance degradation by analyzing how metrics
evolve over the course of a session. Identifies latency creep, token
inflation, error rate increase, and tool success decay.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


STATUS_ICONS = {
    "fresh": "✅",
    "mild_fatigue": "🟡",
    "moderate_fatigue": "🟠",
    "severe_fatigue": "🔴",
    "exhausted": "💀",
}

SIGNAL_ICONS = {
    "latency_creep": "🐌",
    "token_inflation": "📈",
    "error_rate_increase": "💥",
    "tool_success_decay": "🔧",
    "output_shrinkage": "📉",
    "decision_hesitation": "🤔",
}


def cmd_stamina(args: argparse.Namespace) -> None:
    """Profile agent stamina for a session or batch."""
    from agentlens.stamina import StaminaProfiler, StaminaConfig

    if args.session_id:
        _profile_session(args)
    elif args.recent:
        _profile_batch(args)
    else:
        print("Error: provide --session-id or --recent N", file=sys.stderr)
        sys.exit(1)


def _profile_session(args: argparse.Namespace) -> None:
    """Profile a single session."""
    from agentlens.stamina import StaminaProfiler, StaminaConfig
    from agentlens.models import Session, AgentEvent

    client, endpoint = get_client(args)

    # Fetch session with events
    try:
        resp = client.get(f"{endpoint}/sessions/{args.session_id}")
        resp.raise_for_status()
        session_data = resp.json()
    except Exception as e:
        print(f"Error fetching session: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch events for this session
    try:
        resp = client.get(f"{endpoint}/sessions/{args.session_id}/events")
        resp.raise_for_status()
        events_data = resp.json()
    except Exception:
        events_data = []

    # Build session object
    session = Session(
        session_id=session_data.get("session_id", args.session_id),
        agent_name=session_data.get("agent_name", "unknown"),
    )
    for ev in events_data:
        session.events.append(AgentEvent(**{
            k: v for k, v in ev.items()
            if k in AgentEvent.model_fields
        }))

    # Configure profiler
    config = StaminaConfig()
    if args.window_size:
        config.window_size = args.window_size
    
    profiler = StaminaProfiler(config=config)
    report = profiler.profile(session)

    if args.json:
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _profile_batch(args: argparse.Namespace) -> None:
    """Profile recent sessions and show aggregate stamina."""
    from agentlens.stamina import StaminaProfiler, StaminaConfig
    from agentlens.models import Session, AgentEvent

    client, endpoint = get_client(args)

    # Fetch recent sessions
    try:
        resp = client.get(f"{endpoint}/sessions", params={"limit": args.recent})
        resp.raise_for_status()
        sessions_data = resp.json()
    except Exception as e:
        print(f"Error fetching sessions: {e}", file=sys.stderr)
        sys.exit(1)

    if not sessions_data:
        print("No sessions found.")
        return

    # Build session objects with events
    sessions = []
    for sd in sessions_data:
        sid = sd.get("session_id", "unknown")
        session = Session(
            session_id=sid,
            agent_name=sd.get("agent_name", "unknown"),
        )
        try:
            resp = client.get(f"{endpoint}/sessions/{sid}/events")
            resp.raise_for_status()
            for ev in resp.json():
                session.events.append(AgentEvent(**{
                    k: v for k, v in ev.items()
                    if k in AgentEvent.model_fields
                }))
        except Exception:
            pass
        sessions.append(session)

    # Configure and run
    config = StaminaConfig()
    if args.window_size:
        config.window_size = args.window_size
    
    profiler = StaminaProfiler(config=config)
    
    if args.json:
        aggregate = profiler.aggregate_stamina(sessions)
        print_json(aggregate)
    else:
        _render_batch(profiler, sessions)


def _render_batch(profiler: Any, sessions: list[Any]) -> None:
    """Render batch stamina report."""
    from agentlens.stamina import StaminaProfiler

    aggregate = profiler.aggregate_stamina(sessions)
    reports = [profiler.profile(s) for s in sessions]

    print("=" * 60)
    print("  AGENT STAMINA OVERVIEW")
    print("=" * 60)
    print(f"  Sessions Analyzed: {aggregate['sessions_analyzed']}")
    print(f"  Avg Stamina Score: {aggregate['avg_stamina_score']}/100")
    print(f"  Fatigue Rate:      {aggregate['fatigue_rate']:.0%}")
    print(f"  Score Range:       {aggregate['min_stamina_score']} – {aggregate['max_stamina_score']}")
    print()

    # Status distribution
    print("─" * 60)
    print("  STATUS DISTRIBUTION")
    print("─" * 60)
    for status, count in aggregate["status_distribution"].items():
        icon = STATUS_ICONS.get(status, "•")
        bar = "█" * count
        print(f"  {icon} {status:<20} {bar} ({count})")
    print()

    # Common fatigue signals
    if aggregate["common_signals"]:
        print("─" * 60)
        print("  COMMON FATIGUE SIGNALS")
        print("─" * 60)
        for signal, count in aggregate["common_signals"]:
            icon = SIGNAL_ICONS.get(signal, "•")
            print(f"  {icon} {signal:<25} ×{count}")
        print()

    # Length correlation
    lc = aggregate["length_correlation"]
    print("─" * 60)
    print("  SESSION LENGTH CORRELATION")
    print("─" * 60)
    print(f"  {lc['interpretation']}")
    print(f"  (slope={lc['slope']}, R²={lc['r_squared']})")
    print()

    # Per-session summary table
    print("─" * 60)
    print("  PER-SESSION STAMINA")
    print("─" * 60)
    print(f"  {'Session':<20} {'Events':<8} {'Score':<8} {'Status':<20} {'Signals'}")
    print(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*19} {'─'*20}")
    for r in sorted(reports, key=lambda x: x.stamina_score):
        icon = STATUS_ICONS.get(r.status.value, "•")
        sig_str = ", ".join(s.signal.value for s in r.signals[:2]) or "—"
        sid_short = r.session_id[:16] + "…" if len(r.session_id) > 16 else r.session_id
        print(f"  {sid_short:<20} {r.event_count:<8} {r.stamina_score:<8.0f} {icon} {r.status.label:<15} {sig_str}")
    print()
    print("=" * 60)


def register_stamina(subparsers: Any) -> None:
    """Register the stamina subcommand."""
    p = subparsers.add_parser(
        "stamina",
        help="Profile agent stamina and detect session fatigue",
        description=(
            "Analyze how agent performance degrades over the course of a session. "
            "Detects latency creep, token inflation, error rate increase, and tool "
            "success decay. Recommends intervention points."
        ),
    )
    p.add_argument("--session-id", "-s", help="Profile a specific session")
    p.add_argument("--recent", "-r", type=int, help="Profile N most recent sessions")
    p.add_argument("--window-size", "-w", type=int, help="Events per analysis window (default: 5)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_stamina)
