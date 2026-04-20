"""agentlens depmap – Dependency map showing agent-to-tool call relationships.

Analyses recent sessions and builds a directed graph of which models/agents
invoke which tools, then renders the graph as an ASCII diagram, JSON, or
a self-contained interactive HTML visualisation.
"""

from __future__ import annotations

import argparse
import collections
import html as html_mod
import json
import os
import sys
import webbrowser

import httpx

from agentlens.cli_common import get_client as _get_client


# ── data collection ────────────────────────────────────────────────────

Edge = tuple[str, str]  # (caller, callee)


def _collect_edges(client: httpx.Client, limit: int) -> dict[Edge, int]:
    """Fetch sessions + events and build an edge-weight map."""
    edges: dict[Edge, int] = collections.Counter()

    resp = client.get("/api/sessions", params={"limit": limit})
    resp.raise_for_status()
    sessions = resp.json()
    if isinstance(sessions, dict):
        sessions = sessions.get("sessions", sessions.get("data", []))

    for sess in sessions:
        sid = sess.get("id") or sess.get("session_id", "")
        try:
            eresp = client.get("/api/events", params={"session": sid, "limit": 500})
            eresp.raise_for_status()
            events = eresp.json()
            if isinstance(events, dict):
                events = events.get("events", events.get("data", []))
        except Exception:
            continue

        for ev in events:
            etype = ev.get("type", "")
            model = ev.get("model") or ev.get("metadata", {}).get("model", "unknown")
            tool = ev.get("tool") or ev.get("metadata", {}).get("tool")

            if etype in ("llm_call", "completion"):
                caller = f"model:{model}"
            elif etype == "tool_call" and tool:
                # tool call was likely initiated by the session's primary model
                caller = f"model:{model}" if model != "unknown" else "agent"
                callee = f"tool:{tool}"
                edges[(caller, callee)] += 1
                continue
            else:
                continue

            # For llm_call edges link agent → model
            edges[("agent", caller)] += 1

    return edges


# ── ASCII rendering ────────────────────────────────────────────────────

def _render_ascii(edges: dict[Edge, int]) -> str:
    """Render a simple ASCII dependency graph."""
    if not edges:
        return "(no edges found)"

    # group by caller
    groups: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for (src, dst), weight in sorted(edges.items(), key=lambda x: -x[1]):
        groups[src].append((dst, weight))

    lines: list[str] = []
    lines.append("╔══════════════════════════════════════════════╗")
    lines.append("║         Agent Dependency Map                 ║")
    lines.append("╚══════════════════════════════════════════════╝")
    lines.append("")

    for caller in sorted(groups):
        lines.append(f"  [{caller}]")
        for callee, w in sorted(groups[caller], key=lambda x: -x[1]):
            bar = "█" * min(w, 30)
            lines.append(f"    ├─({w:>4}×)─▶ {callee}  {bar}")
        lines.append("")

    # summary
    callers = set(e[0] for e in edges)
    callees = set(e[1] for e in edges)
    total = sum(edges.values())
    lines.append(f"  Nodes: {len(callers | callees)}  |  Edges: {len(edges)}  |  Total calls: {total}")
    return "\n".join(lines)


# ── HTML rendering ─────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentLens Dependency Map</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: system-ui, sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }
  h1 { margin-bottom:16px; font-size:1.5rem; color:#58a6ff; }
  .graph { display:flex; flex-wrap:wrap; gap:24px; }
  .node { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; min-width:280px; }
  .node h2 { font-size:1rem; color:#f0883e; margin-bottom:8px; }
  .edge { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:0.85rem; }
  .edge .bar { height:12px; background:#238636; border-radius:2px; min-width:4px; }
  .edge .target { color:#58a6ff; }
  .edge .count { color:#8b949e; font-variant-numeric:tabular-nums; }
  .summary { margin-top:20px; color:#8b949e; font-size:0.9rem; }
</style>
</head>
<body>
<h1>🔗 Agent Dependency Map</h1>
<div class="graph">
%%NODES%%
</div>
<p class="summary">%%SUMMARY%%</p>
</body>
</html>
"""


def _render_html(edges: dict[Edge, int]) -> str:
    groups: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    max_w = 1
    for (src, dst), weight in edges.items():
        groups[src].append((dst, weight))
        max_w = max(max_w, weight)

    nodes_html = []
    for caller in sorted(groups):
        items = sorted(groups[caller], key=lambda x: -x[1])
        edge_divs = []
        for callee, w in items:
            pct = max(4, int(w / max_w * 200))
            edge_divs.append(
                f'<div class="edge">'
                f'<span class="count">{w}×</span>'
                f'<div class="bar" style="width:{pct}px"></div>'
                f'<span class="target">{html_mod.escape(callee)}</span>'
                f'</div>'
            )
        nodes_html.append(
            f'<div class="node"><h2>{html_mod.escape(caller)}</h2>'
            + "\n".join(edge_divs)
            + "</div>"
        )

    callers = set(e[0] for e in edges)
    callees = set(e[1] for e in edges)
    total = sum(edges.values())
    summary = f"Nodes: {len(callers | callees)} &nbsp;|&nbsp; Edges: {len(edges)} &nbsp;|&nbsp; Total calls: {total}"

    return _HTML_TEMPLATE.replace("%%NODES%%", "\n".join(nodes_html)).replace("%%SUMMARY%%", summary)


# ── command entry point ────────────────────────────────────────────────

def cmd_depmap(args: argparse.Namespace) -> None:
    """Entry point for ``agentlens depmap``."""
    client, endpoint = _get_client(args)
    fmt = getattr(args, "format", "ascii")
    limit = getattr(args, "limit", 50)
    output = getattr(args, "output", None)
    open_browser = getattr(args, "open", False)

    print(f"Scanning up to {limit} sessions on {endpoint} …", file=sys.stderr)
    edges = _collect_edges(client, limit)

    if not edges:
        print("No dependency edges found. Make sure sessions have events with model/tool metadata.")
        return

    if fmt == "json":
        data = [
            {"source": src, "target": dst, "weight": w}
            for (src, dst), w in sorted(edges.items(), key=lambda x: -x[1])
        ]
        text = json.dumps(data, indent=2)
    elif fmt == "html":
        text = _render_html(edges)
    else:
        text = _render_ascii(edges)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Written to {output}", file=sys.stderr)
        if open_browser and fmt == "html":
            webbrowser.open(f"file://{os.path.abspath(output)}")
    else:
        print(text)
