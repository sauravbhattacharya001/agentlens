"""CLI subcommand: agentlens delegation — agent delegation analysis.

Analyzes how agents delegate work: chain depth, bottlenecks,
over-delegation, circular patterns, and health scoring.
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


def cmd_delegation(args: argparse.Namespace) -> None:
    """Analyze delegation patterns."""
    from agentlens.delegation import DelegationAnalyzer, DelegationConfig

    if args.demo:
        _run_demo(args)
        return

    # Load events from stdin (JSON lines)
    events = _load_events_stdin()
    if not events:
        print("No delegation events provided. Pipe JSON lines to stdin or use --demo.")
        print("Each line: {\"session_id\":...,\"parent_agent_id\":...,\"child_agent_id\":...,\"success\":true,...}")
        sys.exit(1)

    config = DelegationConfig()
    if args.max_depth:
        config.max_healthy_depth = args.max_depth
    if args.bottleneck_threshold:
        config.bottleneck_fan_in_threshold = args.bottleneck_threshold
    if args.over_delegation_threshold:
        config.over_delegation_threshold = args.over_delegation_threshold

    analyzer = DelegationAnalyzer(config=config)
    analyzer.add_events(events)
    report = analyzer.analyze()

    if args.json:
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _run_demo(args: argparse.Namespace) -> None:
    """Run with demo data to showcase the analyzer."""
    from agentlens.delegation import DelegationAnalyzer

    events = _build_demo_events()

    analyzer = DelegationAnalyzer()
    analyzer.add_events(events)
    report = analyzer.analyze()

    if getattr(args, "json", False):
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _build_demo_events() -> list:
    """Build demo delegation events showing various patterns."""
    from agentlens.delegation import DelegationEvent
    import random

    random.seed(42)
    events = []

    # Coordinator delegates to 5 sub-agents (healthy pattern)
    for i in range(25):
        sid = f"sess-{i // 5 + 1:03d}"
        child = f"worker-{(i % 5) + 1}"
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="coordinator",
            child_agent_id=child, task_description=f"Task {i+1}",
            delegation_type="sub_agent", success=random.random() > 0.15,
            latency_ms=500 + random.random() * 2000,
            tokens_consumed=200 + int(random.random() * 500),
            timestamp_ms=i * 60000.0, depth=1,
        ))

    # Rubber-stamper: receives work and immediately re-delegates
    for i in range(12):
        sid = f"sess-{i + 6:03d}"
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="coordinator",
            child_agent_id="rubber-stamper", task_description=f"Complex task {i+1}",
            delegation_type="sub_agent", success=True,
            latency_ms=100 + random.random() * 200,
            tokens_consumed=50, timestamp_ms=(25 + i) * 60000.0,
            depth=1, was_re_delegated=True,
        ))
        # Rubber-stamper re-delegates to actual worker
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="rubber-stamper",
            child_agent_id=f"worker-{(i % 3) + 1}", task_description=f"Re-delegated: Complex task {i+1}",
            delegation_type="sub_agent", success=random.random() > 0.2,
            latency_ms=800 + random.random() * 1500,
            tokens_consumed=300 + int(random.random() * 400),
            timestamp_ms=(25 + i) * 60000.0 + 5000, depth=2,
        ))

    # Deep chain: coordinator -> planner -> executor -> validator -> reporter
    chain_agents = ["coordinator", "planner", "executor", "validator", "reporter"]
    for i in range(8):
        sid = f"sess-deep-{i+1:03d}"
        for d in range(len(chain_agents) - 1):
            events.append(DelegationEvent(
                session_id=sid, parent_agent_id=chain_agents[d],
                child_agent_id=chain_agents[d + 1],
                task_description=f"Deep chain step {d+1}",
                delegation_type="sub_agent",
                success=random.random() > 0.1,
                latency_ms=300 + random.random() * 500,
                tokens_consumed=150 + int(random.random() * 200),
                timestamp_ms=(50 + i * 4 + d) * 60000.0,
                depth=d + 1,
            ))

    # Bottleneck: many agents delegate to "shared-db-agent"
    for i in range(15):
        parent = f"service-{(i % 6) + 1}"
        sid = f"sess-bot-{i+1:03d}"
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id=parent,
            child_agent_id="shared-db-agent",
            task_description=f"DB query {i+1}",
            delegation_type="tool_call",
            success=random.random() > 0.3,
            latency_ms=200 + random.random() * 800,
            tokens_consumed=100 + int(random.random() * 150),
            timestamp_ms=(80 + i) * 60000.0, depth=1,
        ))

    # Circular delegation: reviewer <-> editor
    for i in range(6):
        sid = f"sess-circ-{i+1:03d}"
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="reviewer",
            child_agent_id="editor", task_description="Fix issues",
            delegation_type="sub_agent", success=True,
            latency_ms=400 + random.random() * 300,
            tokens_consumed=200, timestamp_ms=(100 + i * 2) * 60000.0, depth=1,
        ))
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="editor",
            child_agent_id="reviewer", task_description="Re-review",
            delegation_type="sub_agent", success=True,
            latency_ms=300 + random.random() * 200,
            tokens_consumed=150, timestamp_ms=(101 + i * 2) * 60000.0, depth=2,
        ))

    # Accountability gap: agent delegates to flaky-service with high failure rate
    for i in range(10):
        sid = f"sess-gap-{i+1:03d}"
        events.append(DelegationEvent(
            session_id=sid, parent_agent_id="orchestrator",
            child_agent_id="flaky-service",
            task_description=f"Unreliable task {i+1}",
            delegation_type="tool_call",
            success=random.random() > 0.7,  # 70% failure rate
            latency_ms=1000 + random.random() * 3000,
            tokens_consumed=50, timestamp_ms=(120 + i) * 60000.0, depth=1,
        ))

    return events


def _load_events_stdin() -> list:
    """Load DelegationEvent objects from stdin JSON lines."""
    from agentlens.delegation import DelegationEvent

    if sys.stdin.isatty():
        return []

    events = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            events.append(DelegationEvent(
                session_id=data.get("session_id", ""),
                parent_agent_id=data.get("parent_agent_id", ""),
                child_agent_id=data.get("child_agent_id", ""),
                task_description=data.get("task_description", ""),
                delegation_type=data.get("delegation_type", "sub_agent"),
                success=data.get("success", True),
                latency_ms=float(data.get("latency_ms", 0)),
                tokens_consumed=int(data.get("tokens_consumed", 0)),
                timestamp_ms=float(data.get("timestamp_ms", 0)),
                depth=int(data.get("depth", 1)),
                was_re_delegated=data.get("was_re_delegated", False),
                error_message=data.get("error_message", ""),
            ))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return events


def register_delegation_parser(subparsers: Any) -> None:
    """Register the 'delegation' subcommand."""
    p = subparsers.add_parser(
        "delegation",
        help="Analyze agent delegation patterns and detect anti-patterns",
        description="Agent Delegation Analyzer — autonomous analysis of delegation chains, "
                    "bottlenecks, over-delegation, circular patterns, and health scoring.",
    )
    p.add_argument("--demo", action="store_true", help="Run with built-in demo data")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--max-depth", type=int, default=None,
                   help="Max healthy chain depth (default: 3)")
    p.add_argument("--bottleneck-threshold", type=int, default=None,
                   help="Fan-in count to flag as bottleneck (default: 10)")
    p.add_argument("--over-delegation-threshold", type=float, default=None,
                   help="Delegation ratio threshold (default: 0.80)")
    p.set_defaults(func=cmd_delegation)
