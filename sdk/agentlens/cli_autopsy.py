"""CLI subcommand: agentlens autopsy — autonomous session investigation.

Runs all analysis engines (anomaly detection, health scoring, error
analysis, latency profiling, token analysis, tool analysis) on a session,
constructs causal evidence chains, and produces root-cause hypotheses
with a prioritized remediation playbook.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


PRIORITY_ICONS = {
    "P0": "\U0001f534",  # 🔴
    "P1": "\U0001f7e0",  # 🟠
    "P2": "\U0001f7e1",  # 🟡
    "P3": "\U0001f7e2",  # 🟢
    "P4": "\u2705",      # ✅
}

EFFORT_ICONS = {
    "quick_fix": "\u26a1",  # ⚡
    "small": "\U0001f527",  # 🔧
    "medium": "\U0001f6e0\ufe0f",  # 🛠️
    "large": "\U0001f3d7\ufe0f",   # 🏗️
}

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"


def _priority_icon(priority: str) -> str:
    return PRIORITY_ICONS.get(priority, "\u26aa")


def cmd_autopsy(args: argparse.Namespace) -> None:
    """Run autonomous investigation on a session."""
    client, endpoint = get_client(args)

    if not args.session_id:
        print("Error: provide a session ID to investigate.", file=sys.stderr)
        sys.exit(1)

    # Fetch session data
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()

    # Fetch baseline sessions for comparison
    baselines: list[dict] = []
    if not args.no_baseline:
        try:
            params: dict[str, Any] = {"limit": args.baseline_count}
            agent = session_data.get("agent_name")
            if agent:
                params["agent"] = agent
            base_resp = client.get("/sessions", params=params)
            base_resp.raise_for_status()
            base_data = base_resp.json()
            baselines = [s for s in base_data.get("sessions", [])
                         if s.get("session_id") != args.session_id]
        except Exception:
            pass  # Proceed without baselines

    # Run local autopsy using the SDK
    from types import SimpleNamespace
    from agentlens.autopsy import SessionAutopsy, AutopsyConfig

    config = AutopsyConfig(
        min_baseline_sessions=max(2, args.min_baselines),
    )
    autopsy = SessionAutopsy(config=config)

    # Convert baseline sessions to metric dicts
    for bs in baselines[:args.baseline_count]:
        metrics = _extract_metrics_from_api(bs)
        if metrics:
            autopsy.add_baseline_metrics(metrics)

    # Convert target session to a Session-like object
    target = _api_session_to_object(session_data)
    report = autopsy.investigate(target)

    if args.json_output:
        print_json(report.to_dict())
        return

    _render_autopsy_report(report.to_dict())


def _extract_metrics_from_api(session_data: dict) -> dict[str, float] | None:
    """Extract metrics from an API session response."""
    events = session_data.get("events", [])
    if not events:
        return None

    durations = []
    total_tokens = 0
    error_count = 0
    tool_count = 0
    tool_errors = 0

    for e in events:
        dur = e.get("duration_ms")
        if dur is not None:
            durations.append(dur)
        total_tokens += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
        et = e.get("event_type", "")
        if "error" in et.lower():
            error_count += 1
        tc = e.get("tool_call")
        if tc:
            tool_count += 1
            out = tc.get("tool_output")
            if isinstance(out, dict) and out.get("error"):
                tool_errors += 1

    n = len(events)
    avg_lat = sum(durations) / len(durations) if durations else 0
    sorted_d = sorted(durations)
    p95_idx = min(int(len(sorted_d) * 0.95), len(sorted_d) - 1) if sorted_d else 0
    p95 = sorted_d[p95_idx] if sorted_d else 0

    return {
        "event_count": float(n),
        "avg_latency_ms": avg_lat,
        "p95_latency_ms": float(p95),
        "total_tokens": float(total_tokens),
        "tokens_per_event": total_tokens / n if n else 0,
        "error_rate": error_count / n if n else 0,
        "tool_call_rate": tool_count / n if n else 0,
        "tool_failure_rate": tool_errors / tool_count if tool_count else 0,
    }


def _api_session_to_object(data: dict) -> Any:
    """Convert API session dict to a Session-like object for autopsy."""
    from types import SimpleNamespace
    events = []
    for e in data.get("events", []):
        tc = e.get("tool_call")
        if tc:
            tc = SimpleNamespace(**tc)
        events.append(SimpleNamespace(
            event_type=e.get("event_type", "generic"),
            duration_ms=e.get("duration_ms"),
            tokens_in=e.get("tokens_in", 0),
            tokens_out=e.get("tokens_out", 0),
            tool_call=tc,
            error_type=e.get("error_type"),
        ))
    return SimpleNamespace(
        session_id=data.get("session_id", "unknown"),
        events=events,
    )


def _render_autopsy_report(data: dict) -> None:
    """Render a full autopsy report to the terminal."""
    priority = data.get("priority", "P4")
    icon = _priority_icon(priority)

    print(f"\n{'='*65}")
    print(f"  {icon} SESSION AUTOPSY REPORT")
    print(f"{'='*65}")
    print(f"\n  Session:    {data.get('session_id', 'N/A')}")
    print(f"  Priority:   {icon} {priority} ({data.get('priority_label', '')})")
    print(f"  Health:     {data.get('health_score', 0):.1f}/100")
    print(f"  Anomalies:  {data.get('anomaly_count', 0)}")
    print(f"  Errors:     {data.get('error_count', 0)}")
    print(f"  Summary:    {data.get('summary', '')}")

    # Evidence
    evidence = data.get("evidence", [])
    if evidence:
        print(f"\n  {'\u2500'*55}")
        print(f"  EVIDENCE ({len(evidence)} findings)")
        print(f"  {'\u2500'*55}")
        for e in evidence:
            w = e.get("severity_weight", 0)
            sev = "\U0001f534" if w >= 0.7 else "\U0001f7e1" if w >= 0.4 else "\U0001f7e2"
            print(f"\n    {sev} [{e.get('source', '')}] {e.get('title', '')}")
            print(f"       {e.get('detail', '')}")

    # Causal links
    links = data.get("causal_links", [])
    if links:
        print(f"\n  {'\u2500'*55}")
        print(f"  CAUSAL CHAINS ({len(links)} links)")
        print(f"  {'\u2500'*55}")
        for link in links:
            rel = link.get("relation", "?")
            arrow = "\u2192" if rel == "causes" else "\u2194"
            print(f"    {link.get('from', '?')} {arrow} {link.get('to', '?')}")
            print(f"       ({rel}) {link.get('explanation', '')}")

    # Hypotheses
    hypotheses = data.get("hypotheses", [])
    if hypotheses:
        print(f"\n  {'\u2500'*55}")
        print(f"  ROOT-CAUSE HYPOTHESES ({len(hypotheses)})")
        print(f"  {'\u2500'*55}")
        for i, h in enumerate(hypotheses, 1):
            conf = h.get("confidence", 0)
            bar = "\u2588" * int(conf * 10) + "\u2591" * (10 - int(conf * 10))
            print(f"\n    #{i} [{conf:.0%}] {bar} {h.get('title', '')}")
            print(f"       {h.get('explanation', '')}")
            print(f"       Evidence: {h.get('evidence_count', 0)} items | "
                  f"Avg severity: {h.get('avg_severity', 0):.2f}")

    # Playbook
    playbook = data.get("playbook", [])
    if playbook:
        print(f"\n  {'\u2500'*55}")
        print(f"  REMEDIATION PLAYBOOK ({len(playbook)} actions)")
        print(f"  {'\u2500'*55}")
        for a in playbook:
            effort = a.get("effort", "?")
            eicon = EFFORT_ICONS.get(effort, "\u2022")
            print(f"\n    {eicon} P{a.get('priority', '?')}: {a.get('description', '')}")
            print(f"       Effort: {effort} | Impact: {a.get('expected_impact', '')}")

    # Engines
    engines = data.get("engines_run", [])
    if engines:
        print(f"\n  Engines: {', '.join(engines)}")

    print(f"{'='*65}\n")


def register_autopsy_parser(subparsers: Any) -> None:
    """Register the autopsy subcommand parser."""
    p = subparsers.add_parser(
        "autopsy",
        help="Autonomous session investigation — multi-engine root-cause analysis with remediation playbook",
    )
    p.add_argument("session_id", nargs="?", default=None, help="Session ID to investigate")
    p.add_argument("--no-baseline", action="store_true", help="Skip baseline comparison")
    p.add_argument("--baseline-count", type=int, default=20, help="Number of baseline sessions to fetch (default: 20)")
    p.add_argument("--min-baselines", type=int, default=3, help="Minimum baselines needed for anomaly detection (default: 3)")
    p.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")
