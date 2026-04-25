"""CLI retention command – analyse session data retention & plan cleanup.

Usage:
    agentlens-cli retention [--days N] [--format table|json|chart] [--output FILE] [--open]
    agentlens-cli retention policy [--keep-days N] [--dry-run] [--json]
    agentlens-cli retention purge --older-than DAYS [--dry-run] [--yes]

Helps users understand how old their data is, how storage breaks down by age
bucket, and safely plan or execute cleanup of stale sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import webbrowser
from datetime import datetime, timezone
from typing import Any

from agentlens._utils import parse_iso_or_epoch as _parse_ts
from agentlens.cli_common import get_client, fetch_sessions, print_json


# ── Age buckets ──────────────────────────────────────────────────────────────

_BUCKETS = [
    ("< 1 day", 1),
    ("1–7 days", 7),
    ("7–30 days", 30),
    ("30–90 days", 90),
    ("90–180 days", 180),
    ("180–365 days", 365),
    ("> 1 year", None),
]


def _bucket_label(age_days: float) -> str:
    for label, upper in _BUCKETS:
        if upper is None or age_days < upper:
            return label
    return _BUCKETS[-1][0]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _session_tokens(s: dict) -> int:
    return int(s.get("total_tokens") or s.get("tokens") or 0)


def _session_cost(s: dict) -> float:
    return float(s.get("total_cost") or s.get("cost") or 0)


def _bar(fraction: float, width: int = 30) -> str:
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


# ── Analysis ─────────────────────────────────────────────────────────────────

def _analyse(sessions: list[dict], now: datetime) -> dict:
    buckets: dict[str, dict] = {}
    for label, _ in _BUCKETS:
        buckets[label] = {"count": 0, "tokens": 0, "cost": 0.0, "events": 0}

    total = {"count": 0, "tokens": 0, "cost": 0.0, "events": 0, "oldest": None, "newest": None}
    ages: list[float] = []

    for s in sessions:
        created = _parse_ts(s.get("created_at") or s.get("start_time"))
        if created is None:
            continue
        age = (now - created).total_seconds() / 86400
        ages.append(age)
        label = _bucket_label(age)

        tokens = _session_tokens(s)
        cost = _session_cost(s)
        events = int(s.get("event_count") or s.get("events") or 0)

        buckets[label]["count"] += 1
        buckets[label]["tokens"] += tokens
        buckets[label]["cost"] += cost
        buckets[label]["events"] += events

        total["count"] += 1
        total["tokens"] += tokens
        total["cost"] += cost
        total["events"] += events

        if total["oldest"] is None or created < total["oldest"]:
            total["oldest"] = created
        if total["newest"] is None or created > total["newest"]:
            total["newest"] = created

    median_age = sorted(ages)[len(ages) // 2] if ages else 0
    total["median_age_days"] = round(median_age, 1)
    total["max_age_days"] = round(max(ages), 1) if ages else 0

    return {"buckets": buckets, "total": total}


# ── Table output ─────────────────────────────────────────────────────────────

def _print_table(analysis: dict) -> None:
    total = analysis["total"]
    buckets = analysis["buckets"]

    print("═══ Data Retention Summary ═══\n")
    print(f"  Total sessions : {total['count']:,}")
    print(f"  Total tokens   : {total['tokens']:,}")
    print(f"  Total cost     : ${total['cost']:.4f}")
    print(f"  Median age     : {total['median_age_days']} days")
    print(f"  Oldest session : {total['max_age_days']} days")
    if total["oldest"]:
        print(f"  Date range     : {total['oldest']:%Y-%m-%d} → {total['newest']:%Y-%m-%d}")
    print()

    # Table
    header = f"{'Age Bucket':<16} {'Sessions':>8} {'Tokens':>12} {'Cost':>10} {'Share':>7}  Distribution"
    print(header)
    print("─" * len(header) + "──────────────────────────────────")
    for label, _ in _BUCKETS:
        b = buckets[label]
        if b["count"] == 0:
            continue
        share = b["count"] / total["count"] if total["count"] else 0
        bar = _bar(share, 20)
        print(
            f"{label:<16} {b['count']:>8,} {b['tokens']:>12,} "
            f"${b['cost']:>9.4f} {share:>6.1%}  {bar}"
        )
    print()


# ── Chart (HTML) ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>AgentLens – Data Retention</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; }}
  h1 {{ color: #58a6ff; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; max-width: 1000px; }}
  canvas {{ background: #161b22; border-radius: 8px; padding: 1rem; }}
  .stats {{ background: #161b22; border-radius: 8px; padding: 1.5rem; }}
  .stats dt {{ color: #8b949e; font-size: .85rem; }} .stats dd {{ font-size: 1.4rem; margin: 0 0 1rem; }}
</style></head><body>
<h1>🗄️ Data Retention Analysis</h1>
<div class="grid">
  <div><canvas id="pie"></canvas></div>
  <div><canvas id="bar"></canvas></div>
</div>
<div class="stats" style="max-width:1000px;margin-top:2rem">
  <dl style="display:grid;grid-template-columns:repeat(4,1fr)">
    <div><dt>Sessions</dt><dd>{total_sessions}</dd></div>
    <div><dt>Total tokens</dt><dd>{total_tokens}</dd></div>
    <div><dt>Total cost</dt><dd>${total_cost}</dd></div>
    <div><dt>Median age</dt><dd>{median_age} days</dd></div>
  </dl>
</div>
<script>
const labels = {labels_json};
const counts = {counts_json};
const tokens = {tokens_json};
const colors = ['#58a6ff','#3fb950','#d29922','#f0883e','#f85149','#bc8cff','#8b949e'];
new Chart(document.getElementById('pie'), {{
  type: 'doughnut',
  data: {{ labels, datasets: [{{ data: counts, backgroundColor: colors }}] }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Sessions by Age', color: '#c9d1d9' }} }} }}
}});
new Chart(document.getElementById('bar'), {{
  type: 'bar',
  data: {{ labels, datasets: [{{ label: 'Tokens', data: tokens, backgroundColor: colors }}] }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Token Usage by Age', color: '#c9d1d9' }} }},
             scales: {{ y: {{ ticks: {{ color: '#8b949e' }} }}, x: {{ ticks: {{ color: '#8b949e' }} }} }} }}
}});
</script></body></html>"""


def _write_chart(analysis: dict, output: str | None, do_open: bool) -> None:
    buckets = analysis["buckets"]
    total = analysis["total"]
    labels = [l for l, _ in _BUCKETS if buckets[l]["count"] > 0]
    counts = [buckets[l]["count"] for l in labels]
    tokens = [buckets[l]["tokens"] for l in labels]

    html = _HTML_TEMPLATE.format(
        labels_json=json.dumps(labels),
        counts_json=json.dumps(counts),
        tokens_json=json.dumps(tokens),
        total_sessions=f"{total['count']:,}",
        total_tokens=f"{total['tokens']:,}",
        total_cost=f"{total['cost']:.4f}",
        median_age=total["median_age_days"],
    )
    path = output or "agentlens-retention.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Chart written to {path}")
    if do_open:
        webbrowser.open(f"file://{os.path.abspath(path)}")


# ── Policy recommendation ───────────────────────────────────────────────────

def _cmd_policy(client, args) -> None:
    keep_days = getattr(args, "keep_days", 90) or 90
    sessions = fetch_sessions(client, limit=500)
    now = datetime.now(timezone.utc)

    would_delete = []
    would_keep = []
    for s in sessions:
        created = _parse_ts(s.get("created_at") or s.get("start_time"))
        if created is None:
            would_keep.append(s)
            continue
        age = (now - created).total_seconds() / 86400
        if age > keep_days:
            would_delete.append(s)
        else:
            would_keep.append(s)

    del_tokens = sum(_session_tokens(s) for s in would_delete)
    del_cost = sum(_session_cost(s) for s in would_delete)

    if getattr(args, "json", False):
        print_json({
            "keep_days": keep_days,
            "would_delete": len(would_delete),
            "would_keep": len(would_keep),
            "freed_tokens": del_tokens,
            "freed_cost": del_cost,
        })
        return

    print(f"═══ Retention Policy Preview (keep {keep_days} days) ═══\n")
    print(f"  Sessions to keep   : {len(would_keep):,}")
    print(f"  Sessions to remove : {len(would_delete):,}")
    print(f"  Tokens freed       : {del_tokens:,}")
    print(f"  Cost freed         : ${del_cost:.4f}")
    print()
    if would_delete:
        print("  Oldest candidates:")
        for s in sorted(would_delete, key=lambda s: s.get("created_at", ""))[:5]:
            sid = s.get("id", "?")
            age_d = (now - _parse_ts(s.get("created_at") or s.get("start_time"))).days  # type: ignore[union-attr]
            print(f"    {sid}  ({age_d}d old, {_session_tokens(s):,} tokens)")
    else:
        print("  ✅ No sessions older than the retention window.")
    print()


# ── Purge ────────────────────────────────────────────────────────────────────

def _cmd_purge(client, args) -> None:
    older_than = args.older_than
    dry_run = getattr(args, "dry_run", False)
    auto_yes = getattr(args, "yes", False)

    sessions = fetch_sessions(client, limit=500)
    now = datetime.now(timezone.utc)

    targets = []
    for s in sessions:
        created = _parse_ts(s.get("created_at") or s.get("start_time"))
        if created and (now - created).total_seconds() / 86400 > older_than:
            targets.append(s)

    if not targets:
        print(f"No sessions older than {older_than} days. Nothing to purge.")
        return

    print(f"Found {len(targets)} session(s) older than {older_than} days.")
    if dry_run:
        print("[dry-run] Would delete:")
        for s in targets[:20]:
            print(f"  {s.get('id', '?')}")
        if len(targets) > 20:
            print(f"  … and {len(targets) - 20} more")
        return

    if not auto_yes:
        answer = input(f"Delete {len(targets)} session(s)? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    deleted = 0
    errors = 0
    for s in targets:
        sid = s.get("id")
        if not sid:
            continue
        try:
            resp = client.delete(f"/api/sessions/{sid}")
            if resp.status_code < 300:
                deleted += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    print(f"\nPurged {deleted} session(s). Errors: {errors}.")


# ── Entry point ──────────────────────────────────────────────────────────────

def cmd_retention(args: argparse.Namespace) -> None:
    client, _ = get_client(args)
    sub = getattr(args, "retention_action", None)

    if sub == "policy":
        _cmd_policy(client, args)
        return
    if sub == "purge":
        _cmd_purge(client, args)
        return

    # Default: analyse age distribution
    limit = getattr(args, "limit", 200) or 200
    sessions = fetch_sessions(client, limit=limit)
    now = datetime.now(timezone.utc)
    analysis = _analyse(sessions, now)
    fmt = getattr(args, "format", "table") or "table"

    if fmt == "json":
        # Serialise datetimes
        total = dict(analysis["total"])
        for k in ("oldest", "newest"):
            if total.get(k):
                total[k] = total[k].isoformat()
        print_json({"buckets": analysis["buckets"], "total": total})
    elif fmt == "chart":
        _write_chart(analysis, getattr(args, "output", None), getattr(args, "open", False))
    else:
        _print_table(analysis)


def register_retention_parser(sub: Any) -> None:
    """Register the ``retention`` subcommand and its sub-actions."""
    p = sub.add_parser("retention", help="Analyse data retention & plan cleanup of stale sessions")
    p.add_argument("--limit", type=int, default=200, help="Max sessions to fetch (default: 200)")
    p.add_argument("--format", choices=["table", "json", "chart"], default="table")
    p.add_argument("--output", "-o", help="Write chart HTML to file")
    p.add_argument("--open", action="store_true", help="Open HTML chart in browser")

    rsub = p.add_subparsers(dest="retention_action")

    pp = rsub.add_parser("policy", help="Preview a retention policy (what would be deleted)")
    pp.add_argument("--keep-days", type=int, default=90, help="Days to keep (default: 90)")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--json", action="store_true")

    pg = rsub.add_parser("purge", help="Delete sessions older than N days")
    pg.add_argument("--older-than", type=int, required=True, help="Delete sessions older than N days")
    pg.add_argument("--dry-run", action="store_true", help="Show what would be deleted without acting")
    pg.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
