"""CLI subcommand: agentlens triage — unified session diagnostics.

Runs health scoring, anomaly detection, baseline drift, error analysis,
and cost analysis in a single call and renders a prioritized triage report.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


SEVERITY_ICONS = {
    "critical": "\U0001f534",  # 🔴
    "high": "\U0001f7e0",      # 🟠
    "medium": "\U0001f7e1",    # 🟡
    "low": "\U0001f7e2",       # 🟢
    "healthy": "\u2705",       # ✅
}

GRADE_COLORS = {
    "A": "\033[92m",  # green
    "B": "\033[96m",  # cyan
    "C": "\033[93m",  # yellow
    "D": "\033[91m",  # red
    "F": "\033[31m",  # dark red
}
RESET = "\033[0m"


def _severity_icon(severity: str) -> str:
    return SEVERITY_ICONS.get(severity, "\u26aa")


def _colorize_grade(grade: str) -> str:
    color = GRADE_COLORS.get(grade, "")
    return f"{color}{grade}{RESET}" if color else grade


def _format_metric(name: str, value: Any, unit: str) -> str:
    if unit == "USD":
        return f"${value:.4f}"
    if unit == "ms":
        return f"{value:,.0f}ms"
    if unit == "%":
        return f"{value:.1f}%"
    return f"{value:,}" if isinstance(value, (int, float)) else str(value)


def cmd_triage(args: argparse.Namespace) -> None:
    """Run auto-triage on a session or batch of recent sessions."""
    client, endpoint = get_client(args)

    if args.recent:
        _triage_batch(client, args)
        return

    if not args.session_id:
        print("Error: provide a session ID or use --recent for batch triage.", file=sys.stderr)
        sys.exit(1)

    resp = client.get(f"/triage/{args.session_id}")
    resp.raise_for_status()
    data = resp.json()

    if args.json_output:
        print_json(data)
        return

    _render_triage_report(data)


def _triage_batch(client: Any, args: argparse.Namespace) -> None:
    """Triage multiple recent sessions."""
    params: dict[str, Any] = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    if args.severity:
        params["severity"] = args.severity

    resp = client.get("/triage/batch", params=params)
    resp.raise_for_status()
    data = resp.json()

    if args.json_output:
        print_json(data)
        return

    results = data.get("triaged", [])
    if not results:
        print("\u2705 All recent sessions look healthy!")
        return

    print(f"\n{'='*65}")
    print(f"  \U0001f50d AUTO-TRIAGE — {len(results)} session(s)")
    print(f"{'='*65}\n")

    for r in results:
        icon = _severity_icon(r.get("overall_severity", "healthy"))
        grade = _colorize_grade(r.get("health_grade", "?"))
        sid = r.get("session_id", "")[:12]
        agent = r.get("agent_name", "unknown")
        findings = r.get("finding_count", 0)
        top = r.get("top_finding", "No issues")

        print(f"  {icon} {sid}  {agent:<20} Grade: {grade}  Findings: {findings}")
        if findings > 0:
            print(f"     \u2514\u2500 {top}")

    print(f"\n  Triaged at: {data.get('triaged_at', 'N/A')}\n")


def _render_triage_report(data: dict) -> None:
    """Render a full triage report to the terminal."""
    severity = data.get("overall_severity", "healthy")
    icon = _severity_icon(severity)
    grade = _colorize_grade(data.get("health_grade", "?"))
    score = data.get("health_score", 0)

    print(f"\n{'='*65}")
    print(f"  {icon} AUTO-TRIAGE REPORT")
    print(f"{'='*65}")
    print(f"\n  Session:   {data.get('session_id', 'N/A')}")
    print(f"  Agent:     {data.get('agent_name', 'N/A')}")
    print(f"  Severity:  {icon} {severity.upper()}")
    print(f"  Grade:     {grade} ({score}/100)")
    print(f"  Summary:   {data.get('summary', '')}")

    # Key metrics
    metrics = data.get("metrics", {})
    if metrics:
        print(f"\n  {'\u2500'*55}")
        print("  KEY METRICS")
        print(f"  {'\u2500'*55}")
        print(f"    Events:      {metrics.get('event_count', 0):,}")
        print(f"    Errors:      {metrics.get('error_count', 0):,}")
        print(f"    Tokens:      {metrics.get('total_tokens', 0):,} ({metrics.get('tokens_in', 0):,} in / {metrics.get('tokens_out', 0):,} out)")
        cost = metrics.get("total_cost", 0)
        print(f"    Cost:        ${cost:.4f}")
        dur = metrics.get("duration_ms")
        if dur is not None:
            print(f"    Duration:    {dur:,.0f}ms")
        avg_dur = metrics.get("avg_event_duration_ms", 0)
        print(f"    Avg latency: {avg_dur:,.0f}ms")
        models = metrics.get("models_used", [])
        if models:
            print(f"    Models:      {', '.join(models)}")
        tools = metrics.get("tools_used", [])
        if tools:
            print(f"    Tools:       {', '.join(tools)}")

    # Findings
    findings = data.get("findings", [])
    if findings:
        print(f"\n  {'\u2500'*55}")
        print(f"  FINDINGS ({len(findings)})")
        print(f"  {'\u2500'*55}")
        for i, f in enumerate(findings, 1):
            ficon = _severity_icon(f.get("severity", "low"))
            cat = f.get("category", "").upper()
            print(f"\n    {ficon} #{i} [{cat}] {f.get('title', '')}")
            print(f"       {f.get('detail', '')}")
            metric = f.get("metric", {})
            if metric:
                val = _format_metric(metric.get("name", ""), metric.get("value", 0), metric.get("unit", ""))
                thresh = _format_metric(metric.get("name", ""), metric.get("threshold", 0), metric.get("unit", ""))
                print(f"       Metric: {val} (threshold: {thresh})")
            print(f"       \U0001f4a1 {f.get('remediation', '')}")
    else:
        print(f"\n  \u2705 No findings — session looks healthy!")

    # Anomaly report
    anomaly = data.get("anomaly_report")
    if anomaly and anomaly.get("isAnomaly"):
        print(f"\n  {'\u2500'*55}")
        print("  ANOMALY DETAILS")
        print(f"  {'\u2500'*55}")
        print(f"    Max Z-score: {anomaly.get('maxZScore', 0)}")
        for key, dim in anomaly.get("dimensions", {}).items():
            z = dim.get("zScore", 0)
            marker = " \u26a0\ufe0f" if abs(z) >= 2 else ""
            print(f"    {key}: z={z}{marker} (value={dim.get('value', 0)}, baseline={dim.get('baseline_mean', 0)})")

    # Baseline comparison
    baseline = data.get("baseline_comparison")
    if baseline:
        print(f"\n  {'\u2500'*55}")
        print(f"  BASELINE DRIFT (verdict: {baseline.get('verdict', 'N/A')}, samples: {baseline.get('samples', 0)})")
        print(f"  {'\u2500'*55}")
        for name, check in baseline.get("checks", {}).items():
            status = check.get("status", "normal")
            delta = check.get("delta_pct", 0)
            arrow = "\u2191" if delta > 0 else ("\u2193" if delta < 0 else "\u2192")
            marker = ""
            if status == "regression":
                marker = " \U0001f534"
            elif status == "warning":
                marker = " \U0001f7e1"
            elif status == "improvement":
                marker = " \U0001f7e2"
            print(f"    {name}: {check.get('actual', 0)} vs baseline {check.get('baseline', 0)} ({arrow}{delta:+.1f}%){marker}")

    print(f"\n  Triaged at: {data.get('triage_at', 'N/A')}")
    print(f"{'='*65}\n")


def register_triage_parser(subparsers: Any) -> None:
    """Register the triage subcommand parser."""
    p = subparsers.add_parser("triage", help="Auto-triage: unified session diagnostics with findings and remediations")
    p.add_argument("session_id", nargs="?", default=None, help="Session ID to triage")
    p.add_argument("--recent", action="store_true", help="Triage recent sessions (batch mode)")
    p.add_argument("--limit", type=int, default=10, help="Max sessions for batch mode (default: 10)")
    p.add_argument("--agent", help="Filter by agent name (batch mode)")
    p.add_argument("--severity", choices=["critical", "high", "medium", "low"], help="Min severity filter (batch mode)")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
