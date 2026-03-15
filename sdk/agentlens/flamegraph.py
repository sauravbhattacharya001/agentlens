"""Session Flamegraph — interactive HTML flame chart for agent sessions.

Transforms session events and spans into an interactive flamegraph
visualization.  Each bar represents an event or span; bar width is
proportional to duration.  Nested spans stack vertically.  Useful for:

- Spotting where latency lives (which tool call is slow?)
- Comparing relative cost of LLM calls vs tool calls
- Understanding agent execution flow at a glance
- Sharing performance profiles without backend access

The output is a single self-contained HTML file with zero external
dependencies — just open it in a browser.

Usage (API)::

    from agentlens.flamegraph import Flamegraph

    fg = Flamegraph(session.events, spans=my_spans)
    html = fg.render_html()
    Path("flamegraph.html").write_text(html)

    # Or use a session directly
    fg = Flamegraph.from_session(session)
    fg.save("flamegraph.html")

    # Quick one-liner
    from agentlens.flamegraph import flamegraph_html
    html = flamegraph_html(events, spans)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from agentlens.models import AgentEvent, Session
from agentlens.span import Span


# ---------------------------------------------------------------------------
# Data transformation
# ---------------------------------------------------------------------------

@dataclass
class _FGNode:
    """Internal flamegraph node."""
    name: str
    start_ms: float  # offset from session start
    duration_ms: float
    depth: int
    event_type: str
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tool_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[_FGNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "start": round(self.start_ms, 2),
            "duration": round(self.duration_ms, 2),
            "depth": self.depth,
            "type": self.event_type,
        }
        if self.model:
            d["model"] = self.model
        if self.tokens_in or self.tokens_out:
            d["tokensIn"] = self.tokens_in
            d["tokensOut"] = self.tokens_out
        if self.tool_name:
            d["tool"] = self.tool_name
        if self.attributes:
            d["attrs"] = self.attributes
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


def _parse_ts(ts: datetime | str | None) -> float | None:
    """Convert timestamp to epoch ms."""
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.timestamp() * 1000


def _event_label(event: AgentEvent) -> str:
    """Generate a readable label for an event."""
    if event.tool_call:
        return f"tool: {event.tool_call.tool_name}"
    if event.event_type == "llm_call":
        return f"llm: {event.model or 'unknown'}"
    if event.event_type == "decision":
        return "decision"
    if event.event_type == "error":
        return "error"
    return event.event_type


class Flamegraph:
    """Build an interactive flamegraph from session events and spans.

    Parameters
    ----------
    events : sequence of AgentEvent
        Session events to visualise.
    spans : sequence of Span, optional
        Hierarchical spans to overlay.  If spans are provided, events
        are nested inside their parent spans.
    session_name : str, optional
        Label shown in the header.
    """

    def __init__(
        self,
        events: Sequence[AgentEvent],
        spans: Sequence[Span] | None = None,
        session_name: str = "Agent Session",
    ) -> None:
        self.events = list(events)
        self.spans = list(spans) if spans else []
        self.session_name = session_name
        self._nodes: list[_FGNode] = []
        self._total_ms: float = 0
        self._build()

    @classmethod
    def from_session(
        cls,
        session: Session,
        spans: Sequence[Span] | None = None,
    ) -> Flamegraph:
        """Create a flamegraph from a :class:`Session` object."""
        return cls(
            events=session.events,
            spans=spans,
            session_name=session.agent_name,
        )

    # ----- internal build logic -----

    def _build(self) -> None:
        """Convert events + spans into flamegraph nodes."""
        if not self.events and not self.spans:
            return

        # Find session time bounds
        all_times: list[float] = []
        for e in self.events:
            t = _parse_ts(e.timestamp)
            if t is not None:
                all_times.append(t)
                if e.duration_ms:
                    all_times.append(t + e.duration_ms)
        for s in self.spans:
            t = _parse_ts(s.started_at)
            if t is not None:
                all_times.append(t)
            te = _parse_ts(s.ended_at)
            if te is not None:
                all_times.append(te)

        if not all_times:
            return

        origin = min(all_times)
        end = max(all_times)
        self._total_ms = end - origin if end > origin else 1.0

        # Build span tree (span_id → span)
        span_map: dict[str, Span] = {s.span_id: s for s in self.spans}

        # Build span nodes (top-level first, then children)
        root_spans = [s for s in self.spans if not s.parent_id]
        span_nodes: dict[str, _FGNode] = {}

        def _build_span_node(span: Span, depth: int) -> _FGNode:
            st = _parse_ts(span.started_at)
            dur = span.duration_ms or 0
            if st is None:
                st = origin
            node = _FGNode(
                name=f"span: {span.name}",
                start_ms=st - origin,
                duration_ms=dur,
                depth=depth,
                event_type="span",
                attributes=dict(span.attributes),
            )
            span_nodes[span.span_id] = node
            # Find children by parent_id
            children = [s for s in self.spans if s.parent_id == span.span_id]
            for child in children:
                child_node = _build_span_node(child, depth + 1)
                node.children.append(child_node)
            return node

        top_nodes: list[_FGNode] = []
        for rs in root_spans:
            top_nodes.append(_build_span_node(rs, 0))

        # Place events: if spans exist, try to nest under spans
        # Otherwise, place at depth 0
        event_nodes: list[_FGNode] = []
        for e in self.events:
            et = _parse_ts(e.timestamp)
            if et is None:
                continue
            node = _FGNode(
                name=_event_label(e),
                start_ms=et - origin,
                duration_ms=e.duration_ms or max(self._total_ms * 0.01, 1),
                depth=0,
                event_type=e.event_type,
                model=e.model,
                tokens_in=e.tokens_in,
                tokens_out=e.tokens_out,
                tool_name=e.tool_call.tool_name if e.tool_call else None,
            )
            event_nodes.append(node)

        if top_nodes:
            # Nest events under matching spans by time overlap
            for enode in event_nodes:
                placed = False
                for snode in self._all_nodes(top_nodes):
                    if (enode.start_ms >= snode.start_ms and
                            enode.start_ms < snode.start_ms + snode.duration_ms):
                        enode.depth = snode.depth + 1
                        snode.children.append(enode)
                        placed = True
                        break
                if not placed:
                    top_nodes.append(enode)
            self._nodes = top_nodes
        else:
            # No spans — auto-assign depths by overlapping time ranges
            placed: list[_FGNode] = []
            for enode in sorted(event_nodes, key=lambda n: n.start_ms):
                depth = 0
                for p in placed:
                    if (enode.start_ms < p.start_ms + p.duration_ms and
                            enode.start_ms + enode.duration_ms > p.start_ms):
                        depth = max(depth, p.depth + 1)
                enode.depth = depth
                placed.append(enode)
            self._nodes = event_nodes

    def _all_nodes(self, nodes: list[_FGNode]) -> list[_FGNode]:
        """Flatten node tree."""
        result: list[_FGNode] = []
        for n in nodes:
            result.append(n)
            result.extend(self._all_nodes(n.children))
        return result

    # ----- output -----

    def to_data(self) -> dict[str, Any]:
        """Return serializable flamegraph data."""
        all_nodes = self._all_nodes(self._nodes)
        return {
            "session": self.session_name,
            "totalMs": round(self._total_ms, 2),
            "nodeCount": len(all_nodes),
            "maxDepth": max((n.depth for n in all_nodes), default=0),
            "nodes": [n.to_dict() for n in all_nodes],
        }

    def render_html(self) -> str:
        """Render a self-contained HTML flamegraph page."""
        data = self.to_data()
        data_json = json.dumps(data, indent=None)
        return _HTML_TEMPLATE.replace("/* __DATA__ */", f"const DATA = {data_json};")

    def save(self, path: str) -> None:
        """Write flamegraph HTML to a file."""
        html = self.render_html()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics about the flamegraph."""
        all_nodes = self._all_nodes(self._nodes)
        by_type: dict[str, float] = {}
        total_tokens = 0
        for n in all_nodes:
            by_type[n.event_type] = by_type.get(n.event_type, 0) + n.duration_ms
            total_tokens += n.tokens_in + n.tokens_out

        slowest = sorted(all_nodes, key=lambda n: n.duration_ms, reverse=True)[:5]
        return {
            "total_ms": round(self._total_ms, 2),
            "node_count": len(all_nodes),
            "max_depth": max((n.depth for n in all_nodes), default=0),
            "time_by_type": {k: round(v, 2) for k, v in sorted(by_type.items(), key=lambda x: -x[1])},
            "total_tokens": total_tokens,
            "slowest_events": [
                {"name": n.name, "duration_ms": round(n.duration_ms, 2)}
                for n in slowest
            ],
        }


def flamegraph_html(
    events: Sequence[AgentEvent],
    spans: Sequence[Span] | None = None,
    session_name: str = "Agent Session",
) -> str:
    """One-liner to generate flamegraph HTML.

    Parameters
    ----------
    events : sequence of AgentEvent
        Session events.
    spans : sequence of Span, optional
        Hierarchical spans.
    session_name : str
        Header label.

    Returns
    -------
    str
        Self-contained HTML page.
    """
    return Flamegraph(events, spans, session_name).render_html()


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentLens Flamegraph</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--text); overflow-x: hidden;
}
header {
  padding: 12px 20px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
}
header h1 { font-size: 16px; font-weight: 600; }
header h1 span { color: var(--accent); }
.stats {
  display: flex; gap: 20px; font-size: 12px; color: var(--muted);
}
.stats .val { color: var(--text); font-weight: 600; }
.controls {
  margin-left: auto; display: flex; gap: 8px; align-items: center;
}
input[type=text] {
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  padding: 4px 8px; border-radius: 4px; font-size: 12px; width: 140px;
}
.btn {
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.btn:hover { border-color: var(--accent); }
.chart-wrap {
  padding: 16px 20px; overflow-x: auto; overflow-y: auto;
  height: calc(100vh - 52px);
}
.tooltip {
  position: fixed; background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 12px; pointer-events: none;
  font-size: 11px; z-index: 100; max-width: 320px; display: none;
  box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}
.tooltip .tt-label { font-weight: 600; margin-bottom: 3px; color: var(--text); }
.tooltip .tt-row { color: var(--muted); padding: 1px 0; }
canvas { display: block; }
.legend {
  display: flex; gap: 14px; padding: 8px 20px; border-top: 1px solid var(--border);
  font-size: 11px; color: var(--muted); flex-wrap: wrap;
}
.legend-item { display: flex; align-items: center; gap: 4px; }
.legend-dot { width: 10px; height: 10px; border-radius: 2px; }
</style>
</head>
<body>
<header>
  <h1>🔥 <span>Flamegraph</span></h1>
  <div class="stats" id="stats"></div>
  <div class="controls">
    <input type="text" id="search" placeholder="Filter…">
    <button class="btn" id="resetBtn">Reset Zoom</button>
    <button class="btn" id="exportBtn">📷 PNG</button>
  </div>
</header>
<div class="chart-wrap">
  <canvas id="canvas"></canvas>
</div>
<div class="legend" id="legend"></div>
<div class="tooltip" id="tooltip"></div>
<script>
/* __DATA__ */

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');
const searchInput = document.getElementById('search');
const statsEl = document.getElementById('stats');
const legendEl = document.getElementById('legend');

const BAR_H = 22;
const BAR_GAP = 2;
const PAD_L = 16;
const PAD_R = 16;
const PAD_T = 40; // room for time axis
let bars = []; // rendered bar metadata for hit-testing
let zoomStart = 0;
let zoomEnd = DATA.totalMs;
let searchTerm = '';

const TYPE_COLORS = {
  llm_call: '#8b5cf6',
  tool_call: '#f59e0b',
  span: '#3b82f6',
  decision: '#10b981',
  error: '#ef4444',
  session_start: '#22c55e',
  session_end: '#6b7280',
  generic: '#6b7280',
};

function barColor(type, highlight) {
  const base = TYPE_COLORS[type] || '#6b7280';
  return highlight ? '#ffffff' : base;
}

// Stats
function renderStats() {
  const dur = DATA.totalMs >= 1000 ? (DATA.totalMs / 1000).toFixed(2) + 's' : DATA.totalMs.toFixed(0) + 'ms';
  let tok = 0;
  DATA.nodes.forEach(n => { tok += (n.tokensIn || 0) + (n.tokensOut || 0); });
  statsEl.innerHTML = `
    <div>${DATA.session} · <span class="val">${dur}</span></div>
    <div>Events: <span class="val">${DATA.nodeCount}</span></div>
    <div>Max Depth: <span class="val">${DATA.maxDepth}</span></div>
    ${tok ? `<div>Tokens: <span class="val">${tok.toLocaleString()}</span></div>` : ''}
  `;
}

// Legend
function renderLegend() {
  const types = new Set(DATA.nodes.map(n => n.type));
  legendEl.innerHTML = [...types].map(t =>
    `<div class="legend-item"><span class="legend-dot" style="background:${TYPE_COLORS[t] || '#6b7280'}"></span>${t}</div>`
  ).join('');
}

// Time axis
function drawTimeAxis(width) {
  const range = zoomEnd - zoomStart;
  const ticks = Math.min(10, Math.max(3, Math.floor(width / 100)));
  ctx.fillStyle = '#8b949e';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  for (let i = 0; i <= ticks; i++) {
    const t = zoomStart + (range / ticks) * i;
    const x = PAD_L + ((t - zoomStart) / range) * (width - PAD_L - PAD_R);
    const label = t >= 1000 ? (t / 1000).toFixed(2) + 's' : t.toFixed(0) + 'ms';
    ctx.fillText(label, x, PAD_T - 8);
    ctx.beginPath();
    ctx.strokeStyle = '#21262d';
    ctx.moveTo(x, PAD_T - 2);
    ctx.lineTo(x, canvas.height / (window.devicePixelRatio || 1));
    ctx.stroke();
  }
}

function render() {
  const dpr = window.devicePixelRatio || 1;
  const wrap = canvas.parentElement;
  const W = wrap.clientWidth - 8;
  const maxDepth = DATA.maxDepth + 1;
  const H = PAD_T + maxDepth * (BAR_H + BAR_GAP) + 40;

  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  ctx.clearRect(0, 0, W, H);
  bars = [];

  const range = zoomEnd - zoomStart;
  if (range <= 0) return;
  const chartW = W - PAD_L - PAD_R;

  drawTimeAxis(W);

  // Draw bars
  for (const node of DATA.nodes) {
    const start = node.start;
    const dur = node.duration;
    // Skip if out of view
    if (start + dur < zoomStart || start > zoomEnd) continue;

    const x = PAD_L + Math.max(0, ((start - zoomStart) / range)) * chartW;
    const w = Math.max(1, (dur / range) * chartW);
    const y = PAD_T + node.depth * (BAR_H + BAR_GAP);
    const highlight = searchTerm && node.name.toLowerCase().includes(searchTerm);
    const dimmed = searchTerm && !highlight;

    ctx.fillStyle = barColor(node.type, highlight);
    ctx.globalAlpha = dimmed ? 0.2 : 0.85;
    ctx.fillRect(x, y, w, BAR_H);
    ctx.globalAlpha = 1;

    // Border
    ctx.strokeStyle = '#0d1117';
    ctx.lineWidth = 0.5;
    ctx.strokeRect(x, y, w, BAR_H);

    // Label
    if (w > 30) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x + 3, y, w - 6, BAR_H);
      ctx.clip();
      ctx.fillStyle = dimmed ? '#444' : '#e6edf3';
      ctx.font = '11px sans-serif';
      ctx.textBaseline = 'middle';
      const label = dur >= 1000 ? `${node.name} (${(dur/1000).toFixed(1)}s)` : `${node.name} (${dur.toFixed(0)}ms)`;
      ctx.fillText(label, x + 4, y + BAR_H / 2);
      ctx.restore();
    }

    bars.push({ node, x, y, w, h: BAR_H });
  }
}

// Hit test
function hitTest(mx, my) {
  for (let i = bars.length - 1; i >= 0; i--) {
    const b = bars[i];
    if (mx >= b.x && mx <= b.x + b.w && my >= b.y && my <= b.y + b.h) return b;
  }
  return null;
}

canvas.addEventListener('mousemove', (e) => {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const hit = hitTest(mx, my);
  if (hit) {
    const n = hit.node;
    let html = `<div class="tt-label">${n.name}</div>`;
    const dur = n.duration >= 1000 ? (n.duration / 1000).toFixed(2) + 's' : n.duration.toFixed(1) + 'ms';
    html += `<div class="tt-row">Duration: ${dur}</div>`;
    html += `<div class="tt-row">Start: +${n.start >= 1000 ? (n.start / 1000).toFixed(2) + 's' : n.start.toFixed(0) + 'ms'}</div>`;
    html += `<div class="tt-row">Type: ${n.type}</div>`;
    if (n.model) html += `<div class="tt-row">Model: ${n.model}</div>`;
    if (n.tokensIn || n.tokensOut) html += `<div class="tt-row">Tokens: ${n.tokensIn || 0} in / ${n.tokensOut || 0} out</div>`;
    if (n.tool) html += `<div class="tt-row">Tool: ${n.tool}</div>`;
    if (n.attrs) {
      for (const [k, v] of Object.entries(n.attrs)) {
        html += `<div class="tt-row">${k}: ${v}</div>`;
      }
    }
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.style.left = Math.min(e.clientX + 12, window.innerWidth - 340) + 'px';
    tooltip.style.top = (e.clientY + 12) + 'px';
    canvas.style.cursor = 'pointer';
  } else {
    tooltip.style.display = 'none';
    canvas.style.cursor = 'default';
  }
});

canvas.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });

// Click to zoom into a bar
canvas.addEventListener('click', (e) => {
  const rect = canvas.getBoundingClientRect();
  const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
  if (hit) {
    const n = hit.node;
    const pad = n.duration * 0.1;
    zoomStart = Math.max(0, n.start - pad);
    zoomEnd = Math.min(DATA.totalMs, n.start + n.duration + pad);
    render();
  }
});

// Double-click to reset
canvas.addEventListener('dblclick', () => {
  zoomStart = 0;
  zoomEnd = DATA.totalMs;
  render();
});

document.getElementById('resetBtn').addEventListener('click', () => {
  zoomStart = 0;
  zoomEnd = DATA.totalMs;
  render();
});

document.getElementById('exportBtn').addEventListener('click', () => {
  const link = document.createElement('a');
  link.download = 'flamegraph.png';
  link.href = canvas.toDataURL('image/png');
  link.click();
});

searchInput.addEventListener('input', () => {
  searchTerm = searchInput.value.toLowerCase();
  render();
});

window.addEventListener('resize', render);
renderStats();
renderLegend();
render();
</script>
</body>
</html>
"""
