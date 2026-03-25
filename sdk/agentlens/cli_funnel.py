"""AgentLens CLI – funnel command.

Analyse agent workflow funnels: track how sessions progress through sequential
event stages and identify where drop-off occurs.  Useful for understanding
multi-step agent pipelines (e.g. plan → tool_call → llm_call → result).

Usage:
    agentlens-cli funnel [--stages TYPES] [--limit N] [--format table|json|html]
                         [--output FILE] [--open] [--endpoint URL] [--api-key KEY]

Stages default to: plan,tool_call,llm_call,result,error
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import webbrowser
from collections import Counter
from typing import Any

import httpx

from agentlens.cli_common import get_client_only as _get_client

DEFAULT_STAGES = ["plan", "tool_call", "llm_call", "result", "error"]


# ── Data helpers ─────────────────────────────────────────────────────────


def _fetch_sessions(client: httpx.Client, limit: int) -> list[dict]:
    """Fetch sessions — uses /sessions (not /api/sessions) for funnel data."""
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("sessions", [data])


def _fetch_events(client: httpx.Client, session_id: str) -> list[dict]:
    resp = client.get("/events", params={"session_id": session_id, "limit": 5000})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("events", [data])


def _build_funnel(
    sessions: list[dict],
    events_by_session: dict[str, list[dict]],
    stages: list[str],
) -> list[dict]:
    """Build funnel metrics for *stages*.

    For each stage, count how many sessions contain at least one event of that
    type.  The stages are treated as an ordered pipeline — a session is counted
    at stage *i* only if it also reached stage *i-1* (or it's the first stage).
    """
    total = len(sessions)
    if total == 0:
        return []

    # For each session, figure out which stages it reached
    session_stages: dict[str, set[str]] = {}
    for sid, evts in events_by_session.items():
        session_stages[sid] = {e.get("event_type", e.get("type", "")) for e in evts}

    funnel: list[dict] = []
    surviving = set(s.get("id", s.get("session_id", "")) for s in sessions)

    for i, stage in enumerate(stages):
        reached = {sid for sid in surviving if stage in session_stages.get(sid, set())}
        count = len(reached)
        pct_total = (count / total * 100) if total else 0
        prev_count = funnel[i - 1]["count"] if i > 0 else total
        pct_prev = (count / prev_count * 100) if prev_count else 0
        drop = prev_count - count

        funnel.append({
            "stage": stage,
            "count": count,
            "pct_total": round(pct_total, 1),
            "pct_prev": round(pct_prev, 1),
            "drop": drop,
        })
        surviving = reached  # only sessions that reached this stage proceed

    return funnel


# ── Renderers ────────────────────────────────────────────────────────────


_BAR_CHAR = "█"


def _render_table(funnel: list[dict], total: int, stages: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"\n🔻  Agent Workflow Funnel  ({total} sessions analysed)\n")
    lines.append(f"{'Stage':<16} {'Count':>7} {'% Total':>9} {'% Prev':>9} {'Drop':>6}  Bar")
    lines.append("─" * 72)

    max_count = funnel[0]["count"] if funnel else 1
    bar_width = 30

    for row in funnel:
        bar_len = int(row["count"] / max_count * bar_width) if max_count else 0
        bar = _BAR_CHAR * bar_len
        lines.append(
            f"{row['stage']:<16} {row['count']:>7} {row['pct_total']:>8.1f}% "
            f"{row['pct_prev']:>8.1f}% {row['drop']:>5}  {bar}"
        )

    # Summary
    if len(funnel) >= 2:
        first, last = funnel[0], funnel[-1]
        overall = (last["count"] / first["count"] * 100) if first["count"] else 0
        biggest_drop_stage = max(funnel[1:], key=lambda r: r["drop"]) if len(funnel) > 1 else None
        lines.append("")
        lines.append(f"📊  Overall conversion: {first['stage']} → {last['stage']}: {overall:.1f}%")
        if biggest_drop_stage and biggest_drop_stage["drop"] > 0:
            lines.append(f"⚠️   Biggest drop-off: {biggest_drop_stage['stage']} (lost {biggest_drop_stage['drop']} sessions, "
                         f"{100 - biggest_drop_stage['pct_prev']:.1f}% drop from previous stage)")

    return "\n".join(lines)


def _render_html(funnel: list[dict], total: int) -> str:
    rows_html = ""
    max_count = funnel[0]["count"] if funnel else 1
    for row in funnel:
        pct = (row["count"] / max_count * 100) if max_count else 0
        rows_html += f"""
        <tr>
          <td class="stage">{html_mod.escape(row['stage'])}</td>
          <td class="num">{row['count']}</td>
          <td class="num">{row['pct_total']}%</td>
          <td class="num">{row['pct_prev']}%</td>
          <td class="num">{row['drop']}</td>
          <td class="bar-cell"><div class="bar" style="width:{pct:.1f}%"></div></td>
        </tr>"""

    summary = ""
    if len(funnel) >= 2:
        first, last = funnel[0], funnel[-1]
        overall = (last["count"] / first["count"] * 100) if first["count"] else 0
        summary = f"<p><strong>Overall conversion:</strong> {html_mod.escape(first['stage'])} → {html_mod.escape(last['stage'])}: {overall:.1f}%</p>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>AgentLens – Workflow Funnel</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,sans-serif; background:#0d1117; color:#c9d1d9; padding:2rem; }}
  h1 {{ color:#58a6ff; margin-bottom:.5rem; }}
  .meta {{ color:#8b949e; margin-bottom:1.5rem; }}
  table {{ border-collapse:collapse; width:100%; max-width:900px; }}
  th, td {{ padding:.6rem 1rem; text-align:left; border-bottom:1px solid #21262d; }}
  th {{ color:#8b949e; font-weight:600; font-size:.85rem; text-transform:uppercase; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .stage {{ font-weight:600; color:#f0f6fc; }}
  .bar-cell {{ width:30%; padding-right:1.5rem; }}
  .bar {{ height:22px; background:linear-gradient(90deg,#238636,#2ea043); border-radius:3px; min-width:2px; transition:width .3s; }}
  .summary {{ margin-top:1.5rem; padding:1rem; background:#161b22; border-radius:8px; border:1px solid #30363d; max-width:900px; }}
  .summary p {{ margin:.3rem 0; }}
</style></head><body>
<h1>🔻 Agent Workflow Funnel</h1>
<p class="meta">{total} sessions analysed</p>
<table>
  <tr><th>Stage</th><th class="num">Count</th><th class="num">% Total</th><th class="num">% Prev</th><th class="num">Drop</th><th>Bar</th></tr>
  {rows_html}
</table>
<div class="summary">{summary}</div>
</body></html>"""


# ── Main command ─────────────────────────────────────────────────────────


def cmd_funnel(args: argparse.Namespace) -> None:
    client = _get_client(args)
    stages = [s.strip() for s in (args.stages or ",".join(DEFAULT_STAGES)).split(",") if s.strip()]
    limit = args.limit or 200
    fmt = args.format or "table"

    print(f"Fetching up to {limit} sessions...")
    sessions = _fetch_sessions(client, limit)
    if not sessions:
        print("No sessions found.")
        return

    print(f"Fetched {len(sessions)} sessions. Analysing events for funnel stages: {', '.join(stages)}")

    events_by_session: dict[str, list[dict]] = {}
    for s in sessions:
        sid = s.get("id", s.get("session_id", ""))
        if sid:
            try:
                events_by_session[sid] = _fetch_events(client, sid)
            except Exception:
                events_by_session[sid] = []

    funnel = _build_funnel(sessions, events_by_session, stages)

    if fmt == "json":
        output = json.dumps({"total_sessions": len(sessions), "stages": stages, "funnel": funnel}, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Written to {args.output}")
        else:
            print(output)
    elif fmt == "html":
        html_content = _render_html(funnel, len(sessions))
        out_path = args.output or "agentlens-funnel.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Funnel report written to {out_path}")
        if args.open:
            webbrowser.open(f"file://{os.path.abspath(out_path)}")
    else:
        text = _render_table(funnel, len(sessions), stages)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Written to {args.output}")
        else:
            print(text)
