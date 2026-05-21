"""CLI subcommand for Agent Prompt Injection Detector."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client_only
from agentlens.prompt_injection import PromptInjectionDetector


def register_prompt_injection_parser(subparsers: Any) -> None:
    """Register the prompt-injection subcommand."""
    p = subparsers.add_parser(
        "prompt-injection",
        help="Detect prompt injection attempts in agent sessions",
        description="Analyze agent sessions for prompt injection, jailbreak, and adversarial manipulation patterns.",
    )
    p.add_argument("session_id", help="Session ID to analyze")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
    p.add_argument("--verbose", action="store_true", help="Show detailed signal-by-signal analysis")
    p.add_argument("--min-confidence", type=float, default=0.5, help="Minimum confidence threshold (default: 0.5)")
    p.add_argument("--no-tool-outputs", action="store_true", help="Skip scanning tool outputs (indirect injection)")
    p.add_argument("--endpoint", help="Backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_prompt_injection)


def cmd_prompt_injection(args: argparse.Namespace) -> None:
    """Execute prompt injection analysis."""
    client = get_client_only(args)

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
    scan_tool_outputs = not getattr(args, "no_tool_outputs", False)
    detector = PromptInjectionDetector(
        min_confidence=args.min_confidence,
        scan_tool_outputs=scan_tool_outputs,
    )
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
            print(f"    Threat level: {s.threat_level.value}")
            print(f"    Source: {s.source_field}")
            if s.description:
                print(f"    Description: {s.description}")
            if s.evidence:
                print(f"    Evidence: {s.evidence}")
    else:
        print(report.format_report())
