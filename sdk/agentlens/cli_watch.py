"""CLI watch command — real-time streaming metric monitor with live dashboard.

Continuously polls the AgentLens backend and displays a live-updating
dashboard of key metrics (active sessions, cost rate, token throughput,
error rate) with configurable refresh interval and optional alerts.

Usage:
    agentlens-cli watch [--interval SECS] [--metric METRIC] [--agent NAME]
                        [--alert-threshold N] [--compact] [--no-spark]
                        [--duration MINS] [--endpoint URL] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

from agentlens.cli_common import get_client_only as _get_client


# Sparkline characters for inline trend visualization
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """Render a list of numbers as a compact Unicode sparkline."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    spread = hi - lo if hi != lo else 1
    return "".join(
        _SPARK_CHARS[min(len(_SPARK_CHARS) - 1, int((v - lo) / spread * (len(_SPARK_CHARS) - 1)))]
        for v in values
    )


def _format_cost(cost: float) -> str:
    """Format a cost value with dollar sign and appropriate precision."""
    if cost < 0.01:
        return f"${cost:.4f}"
    if cost < 1:
        return f"${cost:.3f}"
    return f"${cost:.2f}"


def _rate_indicator(current: float, previous: float) -> str:
    """Show directional indicator comparing current to previous value."""
    if previous == 0:
        return "→" if current == 0 else "↑"
    pct = (current - previous) / previous * 100
    if pct > 10:
        return f"↑ +{pct:.0f}%"
    elif pct < -10:
        return f"↓ {pct:.0f}%"
    return "→"


def _clear_screen() -> None:
    """Clear terminal screen using ANSI escape codes."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _fetch_snapshot(client: httpx.Client) -> dict[str, Any]:
    """Fetch current metrics snapshot from the backend."""
    snapshot: dict[str, Any] = {
        "sessions": 0,
        "total_cost": 0.0,
        "total_tokens": 0,
        "total_events": 0,
        "errors": 0,
        "agents": {},
        "models": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = client.get("/api/analytics")
        if resp.status_code == 200:
            data = resp.json()
            snapshot["sessions"] = data.get("total_sessions", 0)
            snapshot["total_cost"] = data.get("total_cost", 0.0)
            snapshot["total_tokens"] = data.get("total_tokens", 0)
            snapshot["total_events"] = data.get("total_events", 0)
            # Extract per-model and per-agent breakdowns if available
            for model_stat in data.get("by_model", []):
                name = model_stat.get("model", "unknown")
                snapshot["models"][name] = {
                    "cost": model_stat.get("cost", 0),
                    "tokens": model_stat.get("tokens", 0),
                }
    except Exception:
        pass

    try:
        resp = client.get("/api/sessions", params={"limit": 100})
        if resp.status_code == 200:
            sessions = resp.json()
            if isinstance(sessions, list):
                snapshot["sessions"] = len(sessions)
                for s in sessions:
                    agent = s.get("agent_name", "unknown")
                    if agent not in snapshot["agents"]:
                        snapshot["agents"][agent] = {"sessions": 0, "cost": 0, "errors": 0}
                    snapshot["agents"][agent]["sessions"] += 1
                    snapshot["agents"][agent]["cost"] += s.get("total_cost", 0)
                    snapshot["agents"][agent]["errors"] += s.get("error_count", 0)
                    snapshot["errors"] += s.get("error_count", 0)
                    snapshot["total_cost"] += s.get("total_cost", 0)
    except Exception:
        pass

    return snapshot


def _render_dashboard(
    snapshot: dict[str, Any],
    history: deque[dict[str, Any]],
    tick: int,
    interval: int,
    show_spark: bool,
    compact: bool,
    alert_threshold: float | None,
    agent_filter: str | None,
    metric_filter: str | None,
) -> str:
    """Render the live dashboard as a string."""
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    elapsed = tick * interval
    elapsed_str = f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"

    lines.append(f"╔══════════════════════════════════════════════════════════╗")
    lines.append(f"║  🔭 AgentLens Watch — {now}  (elapsed: {elapsed_str:<8}) ║")
    lines.append(f"╠══════════════════════════════════════════════════════════╣")

    # Core metrics
    sessions = snapshot.get("sessions", 0)
    cost = snapshot.get("total_cost", 0)
    tokens = snapshot.get("total_tokens", 0)
    errors = snapshot.get("errors", 0)

    # Compute rates if we have history
    prev = history[-2] if len(history) >= 2 else None
    sess_rate = _rate_indicator(sessions, prev["sessions"]) if prev else "—"
    cost_rate = _rate_indicator(cost, prev["total_cost"]) if prev else "—"
    err_rate = _rate_indicator(errors, prev["errors"]) if prev else "—"

    # Sparklines from history
    sess_spark = _sparkline([h["sessions"] for h in history]) if show_spark and len(history) > 1 else ""
    cost_spark = _sparkline([h["total_cost"] for h in history]) if show_spark and len(history) > 1 else ""
    err_spark = _sparkline([h["errors"] for h in history]) if show_spark and len(history) > 1 else ""

    should_show = lambda m: metric_filter is None or metric_filter == m

    if should_show("sessions"):
        lines.append(f"║  Sessions:  {sessions:<8} {sess_rate:<12} {sess_spark}")
    if should_show("cost"):
        lines.append(f"║  Cost:      {_format_cost(cost):<8} {cost_rate:<12} {cost_spark}")
    if should_show("tokens"):
        tok_spark = _sparkline([h["total_tokens"] for h in history]) if show_spark and len(history) > 1 else ""
        lines.append(f"║  Tokens:    {tokens:<8} {'':12} {tok_spark}")
    if should_show("errors"):
        lines.append(f"║  Errors:    {errors:<8} {err_rate:<12} {err_spark}")

    # Alert check
    alerts: list[str] = []
    if alert_threshold is not None:
        if cost > alert_threshold:
            alerts.append(f"  ⚠️  Cost ${cost:.2f} exceeds threshold ${alert_threshold:.2f}")
        error_pct = (errors / max(1, snapshot.get("total_events", 1))) * 100
        if error_pct > 10:
            alerts.append(f"  ⚠️  Error rate {error_pct:.1f}% is high")

    if not compact:
        # Per-agent breakdown
        agents = snapshot.get("agents", {})
        if agents:
            filtered = agents.items()
            if agent_filter:
                filtered = [(k, v) for k, v in filtered if agent_filter.lower() in k.lower()]
            if filtered:
                lines.append(f"╠══════════════════════════════════════════════════════════╣")
                lines.append(f"║  Agent              Sessions   Cost       Errors        ║")
                lines.append(f"║  ─────────────────── ────────── ────────── ──────        ║")
                for agent_name, stats in sorted(filtered, key=lambda x: x[1]["cost"], reverse=True)[:10]:
                    name = agent_name[:19].ljust(19)
                    s = str(stats["sessions"]).ljust(10)
                    c = _format_cost(stats["cost"]).ljust(10)
                    e = str(stats["errors"]).ljust(6)
                    lines.append(f"║  {name} {s} {c} {e}        ║")

        # Model breakdown
        models = snapshot.get("models", {})
        if models:
            lines.append(f"╠══════════════════════════════════════════════════════════╣")
            lines.append(f"║  Model                         Cost       Tokens        ║")
            lines.append(f"║  ────────────────────────────── ────────── ──────        ║")
            for model_name, stats in sorted(models.items(), key=lambda x: x[1]["cost"], reverse=True)[:5]:
                name = model_name[:30].ljust(30)
                c = _format_cost(stats["cost"]).ljust(10)
                t = str(stats["tokens"]).ljust(6)
                lines.append(f"║  {name} {c} {t}        ║")

    if alerts:
        lines.append(f"╠══════════════════════════════════════════════════════════╣")
        for a in alerts:
            lines.append(f"║{a:<58}║")

    lines.append(f"╚══════════════════════════════════════════════════════════╝")
    lines.append(f"  Press Ctrl+C to stop")

    return "\n".join(lines)


def cmd_watch(args: argparse.Namespace) -> None:
    """Run the live watch dashboard."""
    client = _get_client(args)
    interval = getattr(args, "interval", 5)
    metric = getattr(args, "metric", None)
    agent = getattr(args, "agent", None)
    alert_threshold = getattr(args, "alert_threshold", None)
    compact = getattr(args, "compact", False)
    show_spark = not getattr(args, "no_spark", False)
    duration = getattr(args, "duration", None)

    history: deque[dict[str, Any]] = deque(maxlen=60)  # Keep last 60 readings
    tick = 0
    max_ticks = (duration * 60 // interval) if duration else None

    print("🔭 Starting AgentLens watch... (Ctrl+C to stop)")

    try:
        while True:
            snapshot = _fetch_snapshot(client)
            history.append(snapshot)

            _clear_screen()
            dashboard = _render_dashboard(
                snapshot, history, tick, interval,
                show_spark, compact, alert_threshold, agent, metric,
            )
            print(dashboard)

            tick += 1
            if max_ticks and tick >= max_ticks:
                print(f"\n⏱ Duration limit reached ({duration} minutes). Stopping.")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n\n👋 Watch stopped after {tick} refreshes ({tick * interval}s)")
        if history:
            first, last = history[0], history[-1]
            cost_delta = last["total_cost"] - first["total_cost"]
            print(f"   Cost delta during watch: {_format_cost(cost_delta)}")
            print(f"   Final sessions: {last['sessions']}, errors: {last['errors']}")
