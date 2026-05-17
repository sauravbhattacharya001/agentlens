"""CLI subcommand: agentlens tool-usage — agent tool usage profiling.

Analyzes how agents use their tools: call frequency, success rates,
coupling patterns, anti-patterns, and health scoring.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import print_json


TIER_ICONS = {
    "excellent": "🟢",
    "healthy": "🔵",
    "concerning": "🟡",
    "unhealthy": "🟠",
    "critical": "🔴",
}

PATTERN_ICONS = {
    "overreliance": "🎯",
    "spray_and_pray": "🔫",
    "retry_storm": "🌪️",
    "tool_avoidance": "🚫",
    "sequential_lock": "⛓️",
    "latency_blindness": "🐌",
    "failure_ignorance": "🙈",
    "token_waste": "🪙",
}


def cmd_tool_usage(args: argparse.Namespace) -> None:
    """Profile tool usage patterns."""
    from agentlens.tool_usage import ToolUsageProfiler, ToolUsageConfig

    if args.demo:
        _run_demo(args)
        return

    # Load events from stdin (JSON lines)
    events = _load_events_stdin()
    if not events:
        print("No tool events provided. Pipe JSON lines to stdin or use --demo.")
        print("Each line: {\"session_id\":...,\"agent_id\":...,\"tool_name\":...,\"success\":true,...}")
        sys.exit(1)

    config = ToolUsageConfig()
    if args.overreliance_threshold:
        config.overreliance_threshold = args.overreliance_threshold
    if args.failure_threshold:
        config.failure_rate_warning = args.failure_threshold

    profiler = ToolUsageProfiler(config=config)
    profiler.add_events(events)
    report = profiler.profile()

    if args.json:
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _run_demo(args: argparse.Namespace) -> None:
    """Run with demo data to showcase the profiler."""
    from agentlens.tool_usage import ToolUsageProfiler

    # Build realistic demo events
    events = _build_demo_events()

    profiler = ToolUsageProfiler()
    profiler.add_events(events)
    report = profiler.profile()

    if getattr(args, "json", False):
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _build_demo_events() -> list:
    """Build demo tool events showing various patterns."""
    from agentlens.tool_usage import ToolEvent
    import random

    random.seed(42)
    events = []

    # Agent Alpha: Heavy web_search user (overreliance)
    for i in range(30):
        sid = f"sess-{i // 10 + 1:03d}"
        events.append(ToolEvent(
            session_id=sid, agent_id="agent-alpha",
            tool_name="web_search", success=random.random() > 0.1,
            latency_ms=200 + random.random() * 300,
            tokens_consumed=100 + int(random.random() * 200),
            timestamp_ms=float(i * 1000),
        ))
    # Alpha also uses file_read occasionally
    for i in range(5):
        sid = f"sess-{i // 3 + 1:03d}"
        events.append(ToolEvent(
            session_id=sid, agent_id="agent-alpha",
            tool_name="file_read", success=True,
            latency_ms=10 + random.random() * 30,
            tokens_consumed=50, timestamp_ms=float(30000 + i * 1000),
        ))

    # Agent Beta: Balanced user, some failures on code_exec
    for tool, count, success_rate, lat in [
        ("web_search", 8, 0.9, 250),
        ("file_read", 10, 1.0, 20),
        ("code_exec", 12, 0.5, 1500),
        ("calculator", 6, 1.0, 10),
    ]:
        for i in range(count):
            sid = f"sess-{100 + i // 5:03d}"
            events.append(ToolEvent(
                session_id=sid, agent_id="agent-beta",
                tool_name=tool,
                success=random.random() < success_rate,
                latency_ms=lat + random.random() * lat * 0.5,
                tokens_consumed=int(100 + random.random() * 300),
                timestamp_ms=float(50000 + i * 1000),
                retry_of=tool if tool == "code_exec" and random.random() > 0.6 else "",
            ))

    # Agent Gamma: Token waster on summarize
    for i in range(15):
        sid = f"sess-{200 + i // 5:03d}"
        events.append(ToolEvent(
            session_id=sid, agent_id="agent-gamma",
            tool_name="summarize", success=True,
            latency_ms=800 + random.random() * 400,
            tokens_consumed=800 + int(random.random() * 500),
            timestamp_ms=float(70000 + i * 1000),
        ))
    for i in range(5):
        sid = f"sess-{200 + i:03d}"
        events.append(ToolEvent(
            session_id=sid, agent_id="agent-gamma",
            tool_name="web_search", success=True,
            latency_ms=200, tokens_consumed=100,
            timestamp_ms=float(85000 + i * 1000),
        ))

    return events


def _load_events_stdin() -> list:
    """Load ToolEvent objects from stdin JSON lines."""
    from agentlens.tool_usage import ToolEvent

    if sys.stdin.isatty():
        return []

    events = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            events.append(ToolEvent(
                session_id=data.get("session_id", "unknown"),
                agent_id=data.get("agent_id", "unknown"),
                tool_name=data.get("tool_name", "unknown"),
                success=data.get("success", True),
                latency_ms=data.get("latency_ms", 0.0),
                tokens_consumed=data.get("tokens_consumed", 0),
                error_message=data.get("error_message", ""),
                timestamp_ms=data.get("timestamp_ms", 0.0),
                retry_of=data.get("retry_of", ""),
                context=data.get("context", ""),
            ))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    return events


def register_tool_usage_parser(subparsers: Any) -> None:
    """Register the tool-usage subcommand."""
    p = subparsers.add_parser(
        "tool-usage",
        help="Profile agent tool usage patterns and detect anti-patterns",
        description=(
            "Analyze how agents use their tools across sessions. Detects "
            "overreliance, coupling patterns, retry storms, token waste, "
            "and more. Provides health scoring and actionable recommendations."
        ),
    )
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--demo", action="store_true",
                   help="Run with built-in demo data")
    p.add_argument("--overreliance-threshold", type=float,
                   help="Overreliance threshold 0-1 (default: 0.60)")
    p.add_argument("--failure-threshold", type=float,
                   help="Failure rate warning threshold 0-1 (default: 0.20)")
    p.set_defaults(func=cmd_tool_usage)
