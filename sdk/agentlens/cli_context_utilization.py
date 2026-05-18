"""CLI subcommand for Agent Context Utilization Analyzer."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client
from agentlens.context_utilization import ContextUtilizationAnalyzer


def register_context_utilization_parser(subparsers: Any) -> None:
    """Register the context-utilization subcommand."""
    p = subparsers.add_parser(
        "context-utilization",
        help="Analyze context window utilization efficiency for agent sessions",
        description="Measure how efficiently agents use their context windows.",
    )
    p.add_argument("session_id", help="Session ID to analyze")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
    p.add_argument("--verbose", action="store_true", help="Show detailed per-engine analysis")
    p.add_argument("--context-limit", type=int, default=128000, help="Context window token limit (default: 128000)")
    p.add_argument("--endpoint", help="Backend URL")
    p.add_argument("--api-key", help="API key")
    p.set_defaults(func=cmd_context_utilization)


def cmd_context_utilization(args: argparse.Namespace) -> None:
    """Execute context utilization analysis."""
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
    analyzer = ContextUtilizationAnalyzer(context_limit_tokens=args.context_limit)
    report = analyzer.analyze(session)

    # Output
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.verbose:
        print(report.format_report())
        print()
        print("=" * 60)
        print("  DETAILED ENGINE ANALYSIS")
        print("=" * 60)

        print("\n  Token Density:")
        print(f"    Density ratio: {report.density.density_ratio:.4f}")
        print(f"    Filler %: {report.density.filler_pct:.1%}")
        print(f"    Unique concepts: {report.density.unique_concept_count}")

        if report.pollution_events:
            print(f"\n  Pollution Events ({len(report.pollution_events)}):")
            for p in report.pollution_events[:10]:
                print(f"    [{p.pollution_type.value}] event {p.event_index}: {p.description}")

        if report.working_memory_snapshots:
            last_wm = report.working_memory_snapshots[-1]
            print("\n  Working Memory (final):")
            print(f"    Active: {last_wm.active_tokens} tokens")
            print(f"    Dead weight: {last_wm.dead_weight_tokens} tokens")
            print(f"    Efficiency: {last_wm.efficiency_ratio:.1%}")

        print(f"\n  Prompt Overhead: {report.overhead_pct:.1%}")
        print(f"  Verbose Tool Outputs: {report.tool_output_verbose_count}")
        print(f"  Tool Output Waste: {report.tool_output_total_waste} tokens")

        if report.pressure_points:
            peak = max(report.pressure_points, key=lambda p: p.usage_pct)
            print(f"\n  Peak Pressure: {peak.usage_pct:.1%} at event {peak.event_index}")

        if report.redundant_fetches:
            print(f"\n  Redundant Fetches ({len(report.redundant_fetches)}):")
            for r in report.redundant_fetches[:10]:
                print(f"    {r.info_key}: event {r.original_index} → {r.refetch_index} ({r.waste_tokens} tokens)")
    else:
        print(report.format_report())
