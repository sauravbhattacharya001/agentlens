"""CLI snapshot command — capture a point-in-time system snapshot for before/after comparisons.

Usage:
    agentlens-cli snapshot [--label LABEL] [--output FILE] [--limit N] [--format json|table] [--endpoint URL] [--api-key KEY]
    agentlens-cli snapshot diff <file_a> <file_b> [--format table|json]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any

import httpx

from agentlens.cli_common import get_client_only as _get_client


def _safe_get(client: httpx.Client, path: str, params: dict | None = None) -> Any:
    """GET with graceful error handling — returns None on failure."""
    try:
        resp = client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _capture_snapshot(client: httpx.Client, limit: int, label: str | None) -> dict:
    """Capture system state: sessions summary, costs, alerts, health."""
    now = datetime.datetime.now(datetime.UTC).isoformat() + "Z"

    # Sessions summary
    sessions_raw = _safe_get(client, "/sessions", {"limit": limit}) or []
    sessions = sessions_raw if isinstance(sessions_raw, list) else sessions_raw.get("sessions", [])

    total_sessions = len(sessions)
    total_cost = 0.0
    total_tokens = 0
    total_events = 0
    total_errors = 0
    models_seen: set[str] = set()
    session_summaries = []

    for s in sessions:
        sid = s.get("session_id", s.get("id", "unknown"))
        cost = float(s.get("total_cost", 0) or 0)
        tokens = int(s.get("total_tokens", 0) or 0)
        events = int(s.get("event_count", 0) or 0)
        errors = int(s.get("error_count", 0) or 0)
        model = s.get("model", "unknown")

        total_cost += cost
        total_tokens += tokens
        total_events += events
        total_errors += errors
        if model:
            models_seen.add(model)

        session_summaries.append({
            "session_id": sid,
            "model": model,
            "cost": round(cost, 6),
            "tokens": tokens,
            "events": events,
            "errors": errors,
        })

    # Alerts
    alerts_raw = _safe_get(client, "/alerts") or []
    alerts = alerts_raw if isinstance(alerts_raw, list) else alerts_raw.get("alerts", [])
    alert_count = len(alerts)
    alert_by_severity: dict[str, int] = {}
    for a in alerts:
        sev = a.get("severity", "unknown")
        alert_by_severity[sev] = alert_by_severity.get(sev, 0) + 1

    # Health (best-effort from first few sessions)
    health_scores = []
    for s in sessions[:5]:
        sid = s.get("session_id", s.get("id"))
        if not sid:
            continue
        h = _safe_get(client, f"/sessions/{sid}/health")
        if h and "score" in h:
            health_scores.append(h["score"])

    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else None

    snapshot = {
        "timestamp": now,
        "label": label or "",
        "summary": {
            "sessions": total_sessions,
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "total_events": total_events,
            "total_errors": total_errors,
            "unique_models": sorted(models_seen),
            "avg_health_score": avg_health,
        },
        "alerts": {
            "total": alert_count,
            "by_severity": alert_by_severity,
        },
        "sessions": session_summaries,
    }
    return snapshot


def _print_snapshot_table(snap: dict) -> None:
    """Pretty-print a snapshot to the terminal."""
    s = snap["summary"]
    a = snap["alerts"]

    print(f"\n{'=' * 60}")
    print(f"  AgentLens Snapshot")
    print(f"  Timestamp: {snap['timestamp']}")
    if snap.get("label"):
        print(f"  Label:     {snap['label']}")
    print(f"{'=' * 60}")

    print(f"\n  Sessions:       {s['sessions']}")
    print(f"  Total Cost:     ${s['total_cost_usd']:.4f}")
    print(f"  Total Tokens:   {s['total_tokens']:,}")
    print(f"  Total Events:   {s['total_events']:,}")
    print(f"  Total Errors:   {s['total_errors']:,}")
    print(f"  Unique Models:  {', '.join(s['unique_models']) or 'n/a'}")
    if s["avg_health_score"] is not None:
        print(f"  Avg Health:     {s['avg_health_score']}/100")
    else:
        print(f"  Avg Health:     n/a")

    print(f"\n  Alerts: {a['total']}", end="")
    if a["by_severity"]:
        parts = [f"{sev}={cnt}" for sev, cnt in sorted(a["by_severity"].items())]
        print(f"  ({', '.join(parts)})")
    else:
        print()

    if snap["sessions"]:
        print(f"\n  {'Session ID':<36} {'Model':<16} {'Cost':>10} {'Tokens':>10} {'Errors':>6}")
        print(f"  {'-'*36} {'-'*16} {'-'*10} {'-'*10} {'-'*6}")
        for ss in snap["sessions"]:
            sid = ss["session_id"][:36]
            print(f"  {sid:<36} {ss['model']:<16} ${ss['cost']:>9.4f} {ss['tokens']:>10,} {ss['errors']:>6}")

    print()


def _diff_snapshots(a: dict, b: dict, fmt: str) -> None:
    """Compare two snapshots and show deltas."""
    sa, sb = a["summary"], b["summary"]

    def delta(va: Any, vb: Any) -> str:
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d = vb - va
            sign = "+" if d >= 0 else ""
            if isinstance(d, float):
                return f"{sign}{d:.4f}"
            return f"{sign}{d:,}"
        return f"{va} → {vb}"

    diff = {
        "from": {"timestamp": a["timestamp"], "label": a.get("label", "")},
        "to": {"timestamp": b["timestamp"], "label": b.get("label", "")},
        "deltas": {
            "sessions": delta(sa["sessions"], sb["sessions"]),
            "total_cost_usd": delta(sa["total_cost_usd"], sb["total_cost_usd"]),
            "total_tokens": delta(sa["total_tokens"], sb["total_tokens"]),
            "total_events": delta(sa["total_events"], sb["total_events"]),
            "total_errors": delta(sa["total_errors"], sb["total_errors"]),
            "alerts": delta(a["alerts"]["total"], b["alerts"]["total"]),
        },
    }

    if sa["avg_health_score"] is not None and sb["avg_health_score"] is not None:
        diff["deltas"]["avg_health_score"] = delta(sa["avg_health_score"], sb["avg_health_score"])

    if fmt == "json":
        print(json.dumps(diff, indent=2))
        return

    print(f"\n{'=' * 60}")
    print(f"  Snapshot Diff")
    print(f"  From: {a['timestamp']}" + (f"  ({a['label']})" if a.get("label") else ""))
    print(f"  To:   {b['timestamp']}" + (f"  ({b['label']})" if b.get("label") else ""))
    print(f"{'=' * 60}")
    for key, val in diff["deltas"].items():
        label = key.replace("_", " ").title()
        print(f"  {label:<25} {val}")
    print()


def cmd_snapshot(args: argparse.Namespace) -> None:
    """Handle the snapshot command and its diff sub-command."""
    # Diff mode
    if getattr(args, "snapshot_action", None) == "diff":
        file_a = args.file_a
        file_b = args.file_b
        fmt = getattr(args, "format", "table") or "table"
        try:
            with open(file_a) as f:
                snap_a = json.load(f)
            with open(file_b) as f:
                snap_b = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error reading snapshot files: {e}", file=sys.stderr)
            sys.exit(1)
        _diff_snapshots(snap_a, snap_b, fmt)
        return

    # Capture mode
    client = _get_client(args)
    limit = getattr(args, "limit", 20) or 20
    label = getattr(args, "label", None)
    fmt = getattr(args, "format", "table") or "table"
    output = getattr(args, "output", None)

    snapshot = _capture_snapshot(client, limit, label)

    if output:
        with open(output, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"Snapshot saved to {output}")
    elif fmt == "json":
        print(json.dumps(snapshot, indent=2))
    else:
        _print_snapshot_table(snapshot)

    # Always save to default location if no explicit output
    if not output:
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        default_dir = os.path.expanduser("~/.agentlens/snapshots")
        os.makedirs(default_dir, exist_ok=True)
        default_path = os.path.join(default_dir, f"snapshot-{ts}.json")
        with open(default_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"(Auto-saved to {default_path})")
