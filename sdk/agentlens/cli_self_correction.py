"""CLI subcommand for Agent Self-Correction Tracker."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client, print_json
from agentlens.self_correction import SelfCorrectionTracker


def register_self_correction_parser(subparsers: Any) -> None:
    """Register the self-correction subcommand."""
    p = subparsers.add_parser(
        "self-correction",
        help="Analyze agent self-correction patterns in a session",
        description="Detect and analyze when agents catch and correct their own mistakes.",
    )
    p.add_argument("session_id", help="Session ID to analyze")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
    p.add_argument("--verbose", action="store_true", help="Show detailed correction-by-correction analysis")
    p.add_argument("--min-confidence", type=float, default=0.5, help="Minimum confidence threshold (default: 0.5)")
    p.add_argument("--endpoint", help="Backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_self_correction)


def cmd_self_correction(args: argparse.Namespace) -> None:
    """Execute self-correction analysis."""
    client = get_client(args)

    # Fetch session
    try:
        resp = client.get(f"/sessions/{args.session_id}")
        resp.raise_for_status()
        session = resp.json()
    except httpx.HTTPStatusError as e:
        print(f"Error fetching session: {e.response.status_code}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Run analysis
    tracker = SelfCorrectionTracker(min_confidence=args.min_confidence)
    report = tracker.analyze(session)

    # Output
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.verbose:
        print(report.format_report())
        print()
        print("=" * 60)
        print("  DETAILED CORRECTIONS")
        print("=" * 60)
        for i, c in enumerate(report.correction_timeline, 1):
            print(f"\n  #{i} [{c.category.value}]")
            print(f"    Trigger (event {c.trigger_event_index}): {c.trigger_summary}")
            print(f"    Correction (event {c.correction_event_index}): {c.correction_summary}")
            print(f"    Latency: {c.latency_events} events")
            print(f"    Effectiveness: {c.effectiveness:.0%}")
            print(f"    Confidence: {c.confidence:.0%}")
            if c.description:
                print(f"    Note: {c.description}")
    else:
        print(report.format_report())
