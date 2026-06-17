"""Static HTML template for the session flamegraph renderer.

This module holds the self-contained HTML/CSS/JS asset that
:meth:`agentlens.flamegraph.Flamegraph.render_html` fills with serialized
flamegraph data.  It is intentionally separated from ``flamegraph.py`` so the
Python data-transformation logic stays readable and is not buried under a few
hundred lines of front-end markup.  There is no Python logic here - only the
template string and its ``/* __DATA__ */`` injection placeholder.
"""

from __future__ import annotations

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
