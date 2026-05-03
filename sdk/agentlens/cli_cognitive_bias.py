"""CLI subcommand for Agent Cognitive Bias Detector."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client
from agentlens.cognitive_bias import CognitiveBiasDetector


def register_cognitive_bias_parser(subparsers: Any) -> None:
    """Register the cognitive-bias subcommand."""
    p = subparsers.add_parser(
        "cognitive-bias",
        help="Detect cognitive biases in agent session reasoning",
        description="Analyze agent sessions for systematic reasoning biases.",
    )
    p.add_argument("session_id", help="Session ID to analyze")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
    p.add_argument("--verbose", action="store_true", help="Show detailed signal-by-signal analysis")
    p.add_argument("--min-confidence", type=float, default=0.5, help="Minimum confidence threshold (default: 0.5)")
    p.add_argument("--endpoint", help="Backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_cognitive_bias)


def cmd_cognitive_bias(args: argparse.Namespace) -> None:
    """Execute cognitive bias analysis."""
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
    detector = CognitiveBiasDetector(min_confidence=args.min_confidence)
    report = detector.analyze(session)

    # Output
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.verbose:
        print(report.format_report())
        print()
        print("=" * 60)
        print("  DETAILED SIGNALS")
        print("=" * 60)
        for i, s in enumerate(report.signal_timeline, 1):
            print(f"\n  #{i} [{s.category.value}]")
            print(f"    Event index: {s.event_index}")
            print(f"    Confidence: {s.confidence:.0%}")
            print(f"    Severity: {s.severity.value}")
            if s.description:
                print(f"    Description: {s.description}")
            if s.evidence:
                print(f"    Evidence: {s.evidence}")
    else:
        print(report.format_report())
