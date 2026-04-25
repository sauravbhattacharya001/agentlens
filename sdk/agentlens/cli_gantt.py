"""CLI gantt command – generate an interactive HTML Gantt chart for a session.

Fetches all events for a session, lays them out as horizontal bars on a
timeline, and produces a self-contained HTML file with a Gantt-style
visualisation.  Useful for understanding parallelism and sequencing of
agent steps (LLM calls, tool invocations, planning phases, etc.).

Usage:
    agentlens-cli gantt <session_id> [--output FILE] [--open] [--format html|json|ascii]
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from typing import Any

from agentlens._utils import parse_iso


def _fetch_events(client: Any, session_id: str) -> list[dict]:
    """Fetch events for the session, sorted by timestamp."""
    resp = client.get(f"/api/sessions/{session_id}/events")
    resp.raise_for_status()
    events = resp.json()
    if isinstance(events, dict):
        events = events.get("events", events.get("data", []))
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    dt = parse_iso(ts)
    return dt.timestamp() if dt else None


def _build_bars(events: list[dict]) -> list[dict]:
    """Convert events into gantt bars with start/end/label/type."""
    bars: list[dict] = []
    pending: dict[str, dict] = {}  # keyed by event id or span_id

    for ev in events:
        etype = ev.get("type", "unknown")
        ts = _parse_ts(ev.get("timestamp"))
        if ts is None:
            continue
        duration_ms = ev.get("duration_ms") or ev.get("latency_ms")
        span_id = ev.get("span_id") or ev.get("id") or id(ev)

        if duration_ms is not None:
            bars.append({
                "label": _bar_label(ev),
                "type": etype,
                "start": ts,
                "end": ts + float(duration_ms) / 1000.0,
                "meta": _bar_meta(ev),
            })
        else:
            # Treat as instant (1-pixel bar)
            bars.append({
                "label": _bar_label(ev),
                "type": etype,
                "start": ts,
                "end": ts + 0.05,  # 50ms minimum width
                "meta": _bar_meta(ev),
            })

    bars.sort(key=lambda b: (b["start"], -b["end"]))
    return bars


def _bar_label(ev: dict) -> str:
    etype = ev.get("type", "event")
    model = ev.get("model", "")
    tool = ev.get("tool_name", "") or ev.get("tool", "")
    if tool:
        return f"{etype}: {tool}"
    if model:
        return f"{etype}: {model}"
    return etype


def _bar_meta(ev: dict) -> dict:
    meta: dict = {}
    for key in ("model", "tool_name", "tool", "tokens", "cost", "error", "duration_ms", "latency_ms"):
        if ev.get(key) is not None:
            meta[key] = ev[key]
    return meta


# ── Color palette for event types ──

_COLORS = {
    "llm_call": "#4A90D9",
    "tool_call": "#50C878",
    "plan": "#F5A623",
    "result": "#7B68EE",
    "error": "#E74C3C",
    "guardrail": "#E67E22",
    "human_input": "#1ABC9C",
}
_DEFAULT_COLOR = "#95A5A6"


def _color_for(etype: str) -> str:
    return _COLORS.get(etype, _DEFAULT_COLOR)


# ── ASCII output ──

def _render_ascii(bars: list[dict], width: int = 80) -> str:
    if not bars:
        return "(no events)"
    t_min = min(b["start"] for b in bars)
    t_max = max(b["end"] for b in bars)
    span = t_max - t_min if t_max > t_min else 1.0
    label_w = min(30, max(len(b["label"]) for b in bars))
    bar_w = width - label_w - 4

    lines: list[str] = []
    for b in bars:
        lbl = b["label"][:label_w].ljust(label_w)
        s = int((b["start"] - t_min) / span * bar_w)
        e = max(s + 1, int((b["end"] - t_min) / span * bar_w))
        bar_line = "." * s + "█" * (e - s) + "." * (bar_w - e)
        dur = b["end"] - b["start"]
        lines.append(f"{lbl}  |{bar_line}| {dur:.2f}s")
    return "\n".join(lines)


# ── HTML output ──

def _render_html(bars: list[dict], session_id: str) -> str:
    if not bars:
        return "<html><body><p>No events found.</p></body></html>"

    t_min = min(b["start"] for b in bars)
    t_max = max(b["end"] for b in bars)
    span = t_max - t_min if t_max > t_min else 1.0

    rows_html = []
    for i, b in enumerate(bars):
        left_pct = (b["start"] - t_min) / span * 100
        width_pct = max(0.5, (b["end"] - b["start"]) / span * 100)
        color = _color_for(b["type"])
        dur = b["end"] - b["start"]
        meta_str = html_mod.escape(json.dumps(b["meta"], default=str)) if b["meta"] else ""
        label = html_mod.escape(b["label"])
        rows_html.append(
            f'<div class="row">'
            f'<div class="label" title="{label}">{label}</div>'
            f'<div class="track">'
            f'<div class="bar" style="left:{left_pct:.2f}%;width:{width_pct:.2f}%;background:{color}" '
            f'title="{label} ({dur:.3f}s)\n{meta_str}">'
            f'</div></div>'
            f'<div class="dur">{dur:.2f}s</div>'
            f'</div>'
        )

    # Time axis labels
    n_ticks = 6
    ticks_html = []
    for i in range(n_ticks + 1):
        t = t_min + span * i / n_ticks
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        lbl = dt.strftime("%H:%M:%S")
        pct = i / n_ticks * 100
        ticks_html.append(f'<span style="left:{pct:.1f}%">{lbl}</span>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentLens Gantt – {html_mod.escape(session_id)}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background:#1a1a2e; color:#e0e0e0; padding:24px; }}
  h1 {{ font-size:1.3rem; margin-bottom:6px; color:#fff; }}
  .subtitle {{ color:#888; font-size:0.85rem; margin-bottom:20px; }}
  .gantt {{ display:flex; flex-direction:column; gap:2px; }}
  .row {{ display:flex; align-items:center; height:28px; }}
  .label {{ width:200px; min-width:200px; font-size:0.78rem; padding-right:8px;
            text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .track {{ flex:1; position:relative; height:22px; background:#2a2a3e; border-radius:3px; }}
  .bar {{ position:absolute; top:2px; height:18px; border-radius:3px; opacity:0.9;
          transition: opacity 0.15s; cursor:pointer; }}
  .bar:hover {{ opacity:1; filter:brightness(1.2); }}
  .dur {{ width:70px; min-width:70px; text-align:right; font-size:0.75rem; color:#888; padding-left:6px; }}
  .axis {{ display:flex; position:relative; height:20px; margin-left:200px; margin-right:70px; margin-top:8px; }}
  .axis span {{ position:absolute; transform:translateX(-50%); font-size:0.7rem; color:#666; }}
  .legend {{ display:flex; gap:16px; margin-top:16px; flex-wrap:wrap; }}
  .legend-item {{ display:flex; align-items:center; gap:4px; font-size:0.75rem; }}
  .legend-swatch {{ width:14px; height:14px; border-radius:2px; }}
  .total {{ color:#888; font-size:0.82rem; margin-top:6px; }}
</style>
</head>
<body>
<h1>🔍 Session Gantt Chart</h1>
<div class="subtitle">Session: {html_mod.escape(session_id)} &middot; {len(bars)} events &middot; {span:.2f}s total</div>
<div class="gantt">
{''.join(rows_html)}
</div>
<div class="axis">{''.join(ticks_html)}</div>
<div class="legend">
{''.join(f'<div class="legend-item"><div class="legend-swatch" style="background:{c}"></div>{html_mod.escape(t)}</div>' for t, c in _COLORS.items())}
</div>
<div class="total">Generated by AgentLens CLI</div>
</body>
</html>"""


def cmd_gantt(args: argparse.Namespace) -> None:
    """Execute the gantt command."""
    from agentlens.cli_common import get_client

    client, _ = get_client(args)
    session_id: str = args.session_id
    fmt: str = getattr(args, "format", "html")
    output: str | None = getattr(args, "output", None)
    do_open: bool = getattr(args, "open", False)

    try:
        events = _fetch_events(client, session_id)
    except Exception as exc:
        print(f"Error fetching events: {exc}", file=sys.stderr)
        sys.exit(1)

    bars = _build_bars(events)

    if fmt == "json":
        result = json.dumps(bars, indent=2, default=str)
    elif fmt == "ascii":
        result = _render_ascii(bars)
    else:
        result = _render_html(bars, session_id)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Gantt chart written to {output}")
        if do_open and fmt == "html":
            webbrowser.open(f"file://{os.path.abspath(output)}")
    else:
        print(result)
