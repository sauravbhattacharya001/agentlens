"""CLI sub-command: agentlens failure-forecast.

Predicts upcoming agent session failures based on leading indicators.

Usage::

    agentlens failure-forecast --help
    agentlens failure-forecast predict --session <id>
    agentlens failure-forecast fleet --min-risk elevated
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List

from agentlens.cli_common import add_common_args, get_client_config
from agentlens.failure_forecast import (
    FailureForecaster,
    ForecastReport,
    SessionSnapshot,
    RiskLevel,
)


def register(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the failure-forecast sub-command."""
    parser = subparsers.add_parser(
        "failure-forecast",
        aliases=["ff"],
        help="Predict upcoming session failures from leading indicators",
        description="Analyzes session health metrics to forecast failures before they happen.",
    )
    add_common_args(parser)

    sub = parser.add_subparsers(dest="ff_action")

    # predict sub-command
    predict_p = sub.add_parser("predict", help="Generate failure predictions")
    predict_p.add_argument("--session", "-s", help="Specific session ID to analyze")
    predict_p.add_argument("--agent", "-a", help="Filter by agent ID")
    predict_p.add_argument(
        "--min-risk",
        choices=["nominal", "elevated", "high", "critical", "imminent"],
        default="elevated",
        help="Minimum risk level to show (default: elevated)",
    )
    predict_p.add_argument("--json", dest="json_output", action="store_true",
                           help="Output as JSON")
    predict_p.add_argument("--min-snapshots", type=int, default=3,
                           help="Minimum snapshots required per session")

    # fleet sub-command
    fleet_p = sub.add_parser("fleet", help="Show fleet-wide health overview")
    fleet_p.add_argument("--json", dest="json_output", action="store_true",
                         help="Output as JSON")

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> None:
    """Execute the failure-forecast command."""
    action = getattr(args, "ff_action", None)

    if action == "predict":
        _run_predict(args)
    elif action == "fleet":
        _run_fleet(args)
    else:
        # Default: show fleet overview
        _run_fleet(args)


def _run_predict(args: argparse.Namespace) -> None:
    """Run failure prediction analysis."""
    snapshots = _load_snapshots(args)
    if not snapshots:
        print("No session snapshots available for analysis.")
        sys.exit(0)

    forecaster = FailureForecaster(
        min_snapshots=getattr(args, "min_snapshots", 3)
    )
    forecaster.add_snapshots(snapshots)

    session_id = getattr(args, "session", None)
    if session_id:
        pred = forecaster.predict_session(session_id)
        if pred is None:
            print(f"No prediction available for session '{session_id}' "
                  "(insufficient data or healthy).")
            sys.exit(0)
        report = ForecastReport(
            predictions=[pred],
            total_sessions_analyzed=1,
            sessions_at_risk=1 if pred.risk_level.severity >= 1 else 0,
            fleet_health_score=100.0 * (1 - pred.failure_probability),
        )
    else:
        report = forecaster.predict()

    # Filter by risk level
    min_risk = getattr(args, "min_risk", "elevated")
    risk_threshold = RiskLevel(min_risk).severity
    report.predictions = [
        p for p in report.predictions if p.risk_level.severity >= risk_threshold
    ]

    if getattr(args, "json_output", False):
        print(report.to_json())
    else:
        print(report.format_report())


def _run_fleet(args: argparse.Namespace) -> None:
    """Show fleet-wide failure forecast overview."""
    snapshots = _load_snapshots(args)
    if not snapshots:
        print("No session snapshots available.")
        sys.exit(0)

    forecaster = FailureForecaster()
    forecaster.add_snapshots(snapshots)
    report = forecaster.predict()

    if getattr(args, "json_output", False):
        print(report.to_json())
    else:
        print(report.format_report())


def _load_snapshots(args: argparse.Namespace) -> List[SessionSnapshot]:
    """Load snapshots from stdin (JSON lines) or demo data."""
    snapshots: List[SessionSnapshot] = []

    if not sys.stdin.isatty():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                snap = SessionSnapshot(
                    session_id=data.get("session_id", "unknown"),
                    agent_id=data.get("agent_id", "unknown"),
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    error_count=data.get("error_count", 0),
                    total_events=data.get("total_events", 0),
                    avg_latency_ms=data.get("avg_latency_ms", 0.0),
                    retry_count=data.get("retry_count", 0),
                    tool_failures=data.get("tool_failures", 0),
                    tool_calls=data.get("tool_calls", 0),
                    tokens_used=data.get("tokens_used", 0),
                    token_budget=data.get("token_budget", 0),
                    response_quality_score=data.get("response_quality_score", 1.0),
                    consecutive_errors=data.get("consecutive_errors", 0),
                    event_rate_per_min=data.get("event_rate_per_min", 0.0),
                )
                snapshots.append(snap)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    return snapshots
