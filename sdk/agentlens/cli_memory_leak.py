"""CLI subcommand: agentlens memory-leak — autonomous memory leak detection.

Detects growing context/memory accumulation patterns in agent sessions,
identifying token bloat, context snowballing, tool output hoarding,
repetition, dead references, and payload inflation that lead to context
window exhaustion.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


SEVERITY_ICONS = {
    "none": "✅",
    "low": "🟡",
    "moderate": "🟠",
    "high": "🔴",
    "critical": "💀",
}

CATEGORY_ICONS = {
    "token_growth": "📈",
    "context_snowball": "⛄",
    "tool_output_hoarding": "🗃️",
    "repetition_bloat": "🔁",
    "dead_reference_retention": "💀",
    "unbounded_history": "📜",
    "payload_inflation": "🎈",
}


def cmd_memory_leak(args: argparse.Namespace) -> None:
    """Detect memory leaks in agent sessions."""
    from agentlens.memory_leak import MemoryLeakDetector, LeakDetectorConfig

    if args.session_id:
        _analyze_session(args)
    elif args.recent:
        _analyze_batch(args)
    else:
        print("Error: provide --session-id or --recent N", file=sys.stderr)
        sys.exit(1)


def _analyze_session(args: argparse.Namespace) -> None:
    """Analyze a single session for memory leaks."""
    from agentlens.memory_leak import MemoryLeakDetector, LeakDetectorConfig

    client = get_client()
    session = client.get_session(args.session_id)
    if not session:
        print(f"Session not found: {args.session_id}", file=sys.stderr)
        sys.exit(1)

    config = LeakDetectorConfig()
    if args.context_window:
        config.context_window_tokens = args.context_window
    if args.sensitivity:
        # Map sensitivity to thresholds
        sens = float(args.sensitivity)
        config.growth_significance_threshold = max(0.3, 0.8 - sens * 0.2)
        config.monotonic_run_threshold = max(2, 5 - int(sens))

    detector = MemoryLeakDetector(config)
    report = detector.analyze(session)

    if args.json:
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _analyze_batch(args: argparse.Namespace) -> None:
    """Analyze multiple recent sessions for memory leaks."""
    from agentlens.memory_leak import MemoryLeakDetector, LeakDetectorConfig

    client = get_client()
    sessions = client.list_sessions(limit=args.recent)
    if not sessions:
        print("No sessions found.", file=sys.stderr)
        sys.exit(1)

    config = LeakDetectorConfig()
    if args.context_window:
        config.context_window_tokens = args.context_window

    detector = MemoryLeakDetector(config)

    results = []
    for session in sessions:
        full_session = client.get_session(session.session_id)
        if full_session:
            report = detector.analyze(full_session)
            results.append(report)

    if args.json:
        print_json([r.to_dict() for r in results])
    else:
        print(f"\n{'═' * 60}")
        print(f"  MEMORY LEAK SCAN — {len(results)} sessions analyzed")
        print(f"{'═' * 60}\n")

        # Summary table
        leaky_count = sum(1 for r in results if r.leak_score > 20)
        critical_count = sum(1 for r in results if r.severity.value in ("high", "critical"))

        print(f"  Sessions with leaks:  {leaky_count}/{len(results)}")
        print(f"  Critical/High:        {critical_count}")
        print()

        # Per-session summary
        for r in sorted(results, key=lambda x: x.leak_score, reverse=True):
            icon = SEVERITY_ICONS.get(r.severity.value, "?")
            cats = ", ".join(CATEGORY_ICONS.get(s.category.value, "?") for s in r.leak_signals[:3])
            print(f"  {icon} {r.session_id[:12]}  Score: {r.leak_score:5.1f}  Signals: {len(r.leak_signals)}  {cats}")

        print()
        if critical_count > 0:
            print("  ⚠️  Run with --session-id for detailed analysis of critical sessions")


def register_subcommand(subparsers: Any) -> None:
    """Register the memory-leak subcommand."""
    parser = subparsers.add_parser(
        "memory-leak",
        help="Detect memory/context leaks in agent sessions",
        description="Autonomous detection of growing context accumulation patterns "
                    "that lead to context window exhaustion.",
    )
    parser.add_argument("--session-id", "-s", help="Analyze a specific session")
    parser.add_argument("--recent", "-r", type=int, help="Analyze N most recent sessions")
    parser.add_argument("--context-window", "-w", type=int,
                        help="Context window size in tokens (default: 128000)")
    parser.add_argument("--sensitivity", type=float, default=1.0,
                        help="Detection sensitivity 0.0-2.0 (default: 1.0)")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output as JSON")
    parser.set_defaults(func=cmd_memory_leak)
