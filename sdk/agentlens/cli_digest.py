"""AgentLens CLI digest command — daily/weekly summary digest.

Generates an email-style digest with:
- KPI summary (sessions, cost, tokens, errors) with period-over-period change
- Top sessions by cost
- Notable alerts
- Model usage breakdown
- Recommended actions

Output formats: text (terminal), markdown, html, json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


def _get_client(args: argparse.Namespace) -> tuple[httpx.Client, str]:
    endpoint = (
        getattr(args, "endpoint", None)
        or os.environ.get("AGENTLENS_ENDPOINT", "http://localhost:3000")
    ).rstrip("/")
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("AGENTLENS_API_KEY", "default")
    )
    client = httpx.Client(
        base_url=endpoint,
        headers={"x-api-key": api_key},
        timeout=15.0,
    )
    return client, endpoint


def _fetch_sessions(client: httpx.Client, limit: int = 200) -> list[dict]:
    resp = client.get("/api/sessions", params={"limit": limit})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("sessions", [])


def _fetch_alerts(client: httpx.Client) -> list[dict]:
    try:
        resp = client.get("/api/alerts")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("alerts", [])
    except Exception:
        return []


def _parse_ts(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val / 1000 if val > 1e12 else val, tz=timezone.utc)
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _filter_by_window(sessions: list[dict], days: int) -> tuple[list[dict], list[dict]]:
    """Return (current_period, previous_period) sessions."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    prev_cutoff = cutoff - timedelta(days=days)

    current, previous = [], []
    for s in sessions:
        ts = _parse_ts(s.get("created_at") or s.get("createdAt") or s.get("start_time"))
        if ts is None:
            continue
        if ts >= cutoff:
            current.append(s)
        elif ts >= prev_cutoff:
            previous.append(s)
    return current, previous


def _sum_metric(sessions: list[dict], key: str) -> float:
    total = 0.0
    for s in sessions:
        val = s.get(key)
        if val is not None:
            total += float(val)
        # Also check nested cost/tokens
        if key == "cost" and val is None:
            total += float(s.get("total_cost", 0) or 0)
        if key == "tokens" and val is None:
            total += float(s.get("total_tokens", 0) or 0)
    return total


def _count_errors(sessions: list[dict]) -> int:
    count = 0
    for s in sessions:
        count += int(s.get("error_count", 0) or 0)
        if s.get("status") in ("error", "failed"):
            count += 1
    return count


def _pct_change(current: float, previous: float) -> str:
    if previous == 0:
        return "+∞" if current > 0 else "—"
    pct = ((current - previous) / previous) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _arrow(current: float, previous: float, lower_is_better: bool = False) -> str:
    if current > previous:
        return "🔴 ↑" if lower_is_better else "🟢 ↑"
    elif current < previous:
        return "🟢 ↓" if lower_is_better else "🔴 ↓"
    return "⚪ →"


def _model_breakdown(sessions: list[dict]) -> dict[str, int]:
    models: dict[str, int] = {}
    for s in sessions:
        model = s.get("model") or s.get("model_name") or "unknown"
        models[model] = models.get(model, 0) + 1
    return dict(sorted(models.items(), key=lambda x: -x[1]))


def _top_sessions(sessions: list[dict], n: int = 5) -> list[dict]:
    def cost(s: dict) -> float:
        return float(s.get("cost", 0) or s.get("total_cost", 0) or 0)
    return sorted(sessions, key=cost, reverse=True)[:n]


def _build_digest(args: argparse.Namespace) -> dict[str, Any]:
    client, endpoint = _get_client(args)
    days = {"day": 1, "week": 7, "month": 30}.get(args.period, 1)

    sessions = _fetch_sessions(client, limit=500)
    alerts = _fetch_alerts(client)
    current, previous = _filter_by_window(sessions, days)

    cur_count = len(current)
    prev_count = len(previous)
    cur_cost = _sum_metric(current, "cost")
    prev_cost = _sum_metric(previous, "cost")
    cur_tokens = _sum_metric(current, "tokens")
    prev_tokens = _sum_metric(previous, "tokens")
    cur_errors = _count_errors(current)
    prev_errors = _count_errors(previous)

    # Filter recent alerts
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    recent_alerts = []
    for a in alerts:
        ts = _parse_ts(a.get("created_at") or a.get("timestamp"))
        if ts and ts >= cutoff:
            recent_alerts.append(a)

    top = _top_sessions(current, n=getattr(args, "top", 5))
    models = _model_breakdown(current)

    # Recommendations
    recs = []
    if cur_errors > prev_errors and cur_errors > 5:
        recs.append(f"Error count increased to {cur_errors} — investigate failing sessions")
    if cur_cost > prev_cost * 1.5 and prev_cost > 0:
        recs.append(f"Cost jumped {_pct_change(cur_cost, prev_cost)} — review expensive sessions")
    if cur_count == 0:
        recs.append("No sessions in this period — verify agents are running")
    if len(recent_alerts) > 10:
        recs.append(f"{len(recent_alerts)} alerts triggered — check alert rules for noise")

    return {
        "period": args.period,
        "days": days,
        "generated_at": now.isoformat(),
        "kpis": {
            "sessions": {"current": cur_count, "previous": prev_count, "change": _pct_change(cur_count, prev_count)},
            "cost": {"current": round(cur_cost, 4), "previous": round(prev_cost, 4), "change": _pct_change(cur_cost, prev_cost)},
            "tokens": {"current": int(cur_tokens), "previous": int(prev_tokens), "change": _pct_change(cur_tokens, prev_tokens)},
            "errors": {"current": cur_errors, "previous": prev_errors, "change": _pct_change(cur_errors, prev_errors)},
        },
        "top_sessions": [
            {
                "id": s.get("session_id") or s.get("id", "?"),
                "model": s.get("model") or s.get("model_name", "?"),
                "cost": round(float(s.get("cost", 0) or s.get("total_cost", 0) or 0), 4),
                "status": s.get("status", "?"),
            }
            for s in top
        ],
        "model_breakdown": models,
        "alerts_count": len(recent_alerts),
        "alerts_sample": [
            {"type": a.get("type", "?"), "message": a.get("message", "?")[:80]}
            for a in recent_alerts[:5]
        ],
        "recommendations": recs,
    }


def _render_text(digest: dict) -> str:
    lines = []
    period = digest["period"]
    kpis = digest["kpis"]

    lines.append(f"{'=' * 60}")
    lines.append(f"  AgentLens Digest — {period.title()} Summary")
    lines.append(f"  Generated: {digest['generated_at'][:19]}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    lines.append("📊 Key Metrics (current vs previous period):")
    lines.append(f"  Sessions : {kpis['sessions']['current']:>8}  ({kpis['sessions']['change']})")
    lines.append(f"  Cost     : ${kpis['cost']['current']:>8.4f}  ({kpis['cost']['change']})")
    lines.append(f"  Tokens   : {kpis['tokens']['current']:>8,}  ({kpis['tokens']['change']})")
    lines.append(f"  Errors   : {kpis['errors']['current']:>8}  ({kpis['errors']['change']})")
    lines.append("")

    if digest["top_sessions"]:
        lines.append("🏆 Top Sessions by Cost:")
        for i, s in enumerate(digest["top_sessions"], 1):
            lines.append(f"  {i}. {s['id'][:20]:<20}  ${s['cost']:.4f}  [{s['model']}]  {s['status']}")
        lines.append("")

    if digest["model_breakdown"]:
        lines.append("🤖 Model Usage:")
        for model, count in list(digest["model_breakdown"].items())[:8]:
            bar = "█" * min(count, 30)
            lines.append(f"  {model:<25} {count:>4}  {bar}")
        lines.append("")

    if digest["alerts_count"] > 0:
        lines.append(f"🚨 Alerts: {digest['alerts_count']} triggered")
        for a in digest["alerts_sample"]:
            lines.append(f"  • [{a['type']}] {a['message']}")
        lines.append("")

    if digest["recommendations"]:
        lines.append("💡 Recommendations:")
        for r in digest["recommendations"]:
            lines.append(f"  → {r}")
        lines.append("")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def _render_markdown(digest: dict) -> str:
    lines = []
    kpis = digest["kpis"]
    period = digest["period"]

    lines.append(f"# AgentLens Digest — {period.title()} Summary")
    lines.append(f"*Generated: {digest['generated_at'][:19]}*")
    lines.append("")

    lines.append("## 📊 Key Metrics")
    lines.append("| Metric | Current | Previous | Change |")
    lines.append("|--------|--------:|----------:|--------|")
    lines.append(f"| Sessions | {kpis['sessions']['current']} | {kpis['sessions']['previous']} | {kpis['sessions']['change']} |")
    lines.append(f"| Cost | ${kpis['cost']['current']:.4f} | ${kpis['cost']['previous']:.4f} | {kpis['cost']['change']} |")
    lines.append(f"| Tokens | {kpis['tokens']['current']:,} | {kpis['tokens']['previous']:,} | {kpis['tokens']['change']} |")
    lines.append(f"| Errors | {kpis['errors']['current']} | {kpis['errors']['previous']} | {kpis['errors']['change']} |")
    lines.append("")

    if digest["top_sessions"]:
        lines.append("## 🏆 Top Sessions by Cost")
        lines.append("| # | Session ID | Cost | Model | Status |")
        lines.append("|---|-----------|-----:|-------|--------|")
        for i, s in enumerate(digest["top_sessions"], 1):
            lines.append(f"| {i} | `{s['id'][:20]}` | ${s['cost']:.4f} | {s['model']} | {s['status']} |")
        lines.append("")

    if digest["model_breakdown"]:
        lines.append("## 🤖 Model Usage")
        for model, count in list(digest["model_breakdown"].items())[:8]:
            lines.append(f"- **{model}**: {count} sessions")
        lines.append("")

    if digest["alerts_count"] > 0:
        lines.append(f"## 🚨 Alerts ({digest['alerts_count']})")
        for a in digest["alerts_sample"]:
            lines.append(f"- **{a['type']}**: {a['message']}")
        lines.append("")

    if digest["recommendations"]:
        lines.append("## 💡 Recommendations")
        for r in digest["recommendations"]:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)


def _render_html(digest: dict) -> str:
    kpis = digest["kpis"]
    period = digest["period"]

    def _kpi_card(label: str, current: Any, change: str, prefix: str = "", fmt: str = "") -> str:
        val = f"{prefix}{current:{fmt}}" if fmt else f"{prefix}{current}"
        color = "#27ae60" if change.startswith("+") else "#e74c3c" if change.startswith("-") else "#7f8c8d"
        return f"""<div style="background:#f8f9fa;border-radius:12px;padding:20px;text-align:center;min-width:140px">
            <div style="font-size:13px;color:#7f8c8d;text-transform:uppercase;letter-spacing:1px">{label}</div>
            <div style="font-size:28px;font-weight:700;margin:8px 0">{val}</div>
            <div style="font-size:14px;color:{color}">{change} vs prev</div>
        </div>"""

    top_rows = ""
    for i, s in enumerate(digest["top_sessions"], 1):
        top_rows += f"<tr><td>{i}</td><td><code>{s['id'][:20]}</code></td><td>${s['cost']:.4f}</td><td>{s['model']}</td><td>{s['status']}</td></tr>\n"

    model_items = ""
    for model, count in list(digest["model_breakdown"].items())[:8]:
        model_items += f"<li><strong>{model}</strong>: {count} sessions</li>\n"

    alert_items = ""
    for a in digest["alerts_sample"]:
        alert_items += f"<li><strong>{a['type']}</strong>: {a['message']}</li>\n"

    rec_items = ""
    for r in digest["recommendations"]:
        rec_items += f"<li>{r}</li>\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentLens Digest — {period.title()}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 700px; margin: 0 auto; padding: 24px; background: #fff; color: #2c3e50; }}
  h1 {{ font-size: 24px; border-bottom: 2px solid #3498db; padding-bottom: 12px; }}
  h2 {{ font-size: 18px; margin-top: 28px; color: #2c3e50; }}
  .kpis {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ecf0f1; font-size: 14px; }}
  th {{ background: #f8f9fa; font-weight: 600; }}
  code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #ecf0f1; font-size: 12px; color: #95a5a6; }}
</style>
</head>
<body>
<h1>📋 AgentLens Digest — {period.title()} Summary</h1>
<p style="color:#7f8c8d">Generated: {digest['generated_at'][:19]}</p>

<h2>📊 Key Metrics</h2>
<div class="kpis">
  {_kpi_card("Sessions", kpis['sessions']['current'], kpis['sessions']['change'])}
  {_kpi_card("Cost", kpis['cost']['current'], kpis['cost']['change'], prefix="$", fmt=".4f")}
  {_kpi_card("Tokens", f"{kpis['tokens']['current']:,}", kpis['tokens']['change'])}
  {_kpi_card("Errors", kpis['errors']['current'], kpis['errors']['change'])}
</div>

{"<h2>🏆 Top Sessions by Cost</h2><table><tr><th>#</th><th>Session</th><th>Cost</th><th>Model</th><th>Status</th></tr>" + top_rows + "</table>" if top_rows else ""}

{"<h2>🤖 Model Usage</h2><ul>" + model_items + "</ul>" if model_items else ""}

{"<h2>🚨 Alerts (" + str(digest['alerts_count']) + ")</h2><ul>" + alert_items + "</ul>" if alert_items else ""}

{"<h2>💡 Recommendations</h2><ul>" + rec_items + "</ul>" if rec_items else ""}

<div class="footer">AgentLens Digest · Auto-generated</div>
</body>
</html>"""


def cmd_digest(args: argparse.Namespace) -> None:
    """Generate a periodic digest summary."""
    try:
        digest = _build_digest(args)
    except httpx.ConnectError:
        print("Error: cannot connect to AgentLens backend", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"Error: {exc.response.status_code} from backend", file=sys.stderr)
        sys.exit(1)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        output = json.dumps(digest, indent=2, default=str)
    elif fmt == "markdown":
        output = _render_markdown(digest)
    elif fmt == "html":
        output = _render_html(digest)
    else:
        output = _render_text(digest)

    out_file = getattr(args, "output", None)
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Digest written to {out_file}")
        if fmt == "html" and getattr(args, "open", False):
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(out_file)}")
    else:
        print(output)
