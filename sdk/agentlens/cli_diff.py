"""AgentLens CLI – diff command: side-by-side comparison of two sessions."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client, print_json


def _fetch_session(client: Any, sid: str) -> dict:
    resp = client.get(f"/sessions/{sid}")
    resp.raise_for_status()
    return resp.json()


def _fetch_costs(client: Any, sid: str) -> dict:
    try:
        resp = client.get(f"/sessions/{sid}/costs")
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _fetch_events(client: Any, sid: str, limit: int = 500) -> list[dict]:
    try:
        resp = client.get("/events", params={"session_id": sid, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("events", [])
    except Exception:
        return []


def _safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return default


def _pct_change(a: float | int, b: float | int) -> str:
    if a == 0 and b == 0:
        return "—"
    if a == 0:
        return "+∞"
    change = ((b - a) / abs(a)) * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f}%"


def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def _direction_indicator(a: float, b: float, lower_is_better: bool = True) -> str:
    """Return colored arrow indicating if change is good/bad."""
    if a == b:
        return "="
    improved = (b < a) if lower_is_better else (b > a)
    arrow = "↓" if b < a else "↑"
    return _color(arrow, "32") if improved else _color(arrow, "31")


def cmd_diff(args: argparse.Namespace) -> None:
    """Compare two sessions side-by-side with metric deltas."""
    client, _ = get_client(args)

    session_a = _fetch_session(client, args.session_a)
    session_b = _fetch_session(client, args.session_b)
    costs_a = _fetch_costs(client, args.session_a)
    costs_b = _fetch_costs(client, args.session_b)
    events_a = _fetch_events(client, args.session_a)
    events_b = _fetch_events(client, args.session_b)

    # Build event type breakdown
    def _event_type_counts(events: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in events:
            t = e.get("event_type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        return counts

    def _model_counts(events: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in events:
            m = e.get("model")
            if m:
                counts[m] = counts.get(m, 0) + 1
        return counts

    et_a = _event_type_counts(events_a)
    et_b = _event_type_counts(events_b)
    models_a = _model_counts(events_a)
    models_b = _model_counts(events_b)

    # Extract key metrics
    def _metric(session: dict, costs: dict, events: list) -> dict:
        return {
            "agent": _safe_get(session, "agent_name", "agent", default="—"),
            "status": _safe_get(session, "status", default="—"),
            "events": _safe_get(session, "event_count", default=len(events)),
            "total_tokens": _safe_get(session, "total_tokens", default=0),
            "tokens_in": _safe_get(session, "tokens_in", "total_input_tokens", default=0),
            "tokens_out": _safe_get(session, "tokens_out", "total_output_tokens", default=0),
            "total_cost": _safe_get(costs, "total_cost", default=0),
            "duration_ms": _safe_get(session, "duration_ms", "total_duration_ms", default=0),
            "errors": sum(1 for e in events if e.get("event_type") in ("error", "exception")),
        }

    ma = _metric(session_a, costs_a, events_a)
    mb = _metric(session_b, costs_b, events_b)

    if args.json_output:
        result = {
            "session_a": args.session_a,
            "session_b": args.session_b,
            "metrics_a": ma,
            "metrics_b": mb,
            "event_types_a": et_a,
            "event_types_b": et_b,
            "models_a": models_a,
            "models_b": models_b,
        }
        print_json(result)
        return

    no_color = getattr(args, "no_color", False)

    def _c(text: str, code: str) -> str:
        return text if no_color else _color(text, code)

    # Print header
    label_a = getattr(args, "label_a", None) or args.session_a[:12]
    label_b = getattr(args, "label_b", None) or args.session_b[:12]

    print()
    print(_c("Session Diff", "1;36"))
    print(_c(f"  A: {args.session_a}", "33") + f"  ({ma['agent']}, {ma['status']})")
    print(_c(f"  B: {args.session_b}", "33") + f"  ({mb['agent']}, {mb['status']})")
    print()

    # Metric comparison table
    rows = [
        ("Events", ma["events"], mb["events"], True),
        ("Total Tokens", ma["total_tokens"], mb["total_tokens"], True),
        ("Input Tokens", ma["tokens_in"], mb["tokens_in"], True),
        ("Output Tokens", ma["tokens_out"], mb["tokens_out"], True),
        ("Total Cost ($)", f"{ma['total_cost']:.6f}", f"{mb['total_cost']:.6f}", True),
        ("Duration (ms)", ma["duration_ms"], mb["duration_ms"], True),
        ("Errors", ma["errors"], mb["errors"], True),
    ]

    header = f"{'Metric':<18} {label_a:>14} {label_b:>14} {'Change':>10} {'':>3}"
    print(_c(header, "1"))
    print("─" * len(header))

    for label, va, vb, lower_better in rows:
        fa = float(va) if not isinstance(va, str) else float(va)
        fb = float(vb) if not isinstance(vb, str) else float(vb)
        pct = _pct_change(fa, fb)
        indicator = "=" if no_color else _direction_indicator(fa, fb, lower_better)
        sa = str(va).rjust(14)
        sb = str(vb).rjust(14)
        print(f"{label:<18} {sa} {sb} {pct:>10} {indicator:>3}")

    # Event type breakdown
    all_types = sorted(set(et_a) | set(et_b))
    if all_types:
        print()
        print(_c("Event Type Breakdown", "1;36"))
        header2 = f"{'Type':<24} {label_a:>8} {label_b:>8} {'Change':>10}"
        print(_c(header2, "1"))
        print("─" * len(header2))
        for t in all_types:
            ca = et_a.get(t, 0)
            cb = et_b.get(t, 0)
            pct = _pct_change(ca, cb)
            print(f"{t:<24} {ca:>8} {cb:>8} {pct:>10}")

    # Model breakdown
    all_models = sorted(set(models_a) | set(models_b))
    if all_models:
        print()
        print(_c("Model Usage", "1;36"))
        header3 = f"{'Model':<30} {label_a:>8} {label_b:>8} {'Change':>10}"
        print(_c(header3, "1"))
        print("─" * len(header3))
        for m in all_models:
            ca = models_a.get(m, 0)
            cb = models_b.get(m, 0)
            pct = _pct_change(ca, cb)
            print(f"{m:<30} {ca:>8} {cb:>8} {pct:>10}")

    print()
