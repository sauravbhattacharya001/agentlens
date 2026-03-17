"""Token Usage Heatmap — calendar-style heatmap of token consumption.

Aggregates agent session data into hourly/daily buckets and renders an
interactive HTML heatmap so users can spot peak usage periods, compare
models, and optimise costs.

Usage (Python API)::

    from agentlens.heatmap import HeatmapBuilder
    hb = HeatmapBuilder()
    hb.add_session(session)           # add Session objects
    hb.add_event(event)               # or individual AgentEvents
    html = hb.render()                # full HTML page string
    hb.save("heatmap.html")           # write to file
    data = hb.to_dict()               # raw bucket data (JSON-friendly)

Usage (CLI)::

    agentlens heatmap --granularity day --metric tokens_total
"""

from __future__ import annotations

import html as _html
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from agentlens.models import AgentEvent, Session


GranularityType = Literal["hour", "day", "week"]
MetricType = Literal["tokens_in", "tokens_out", "tokens_total", "event_count", "cost"]


def _iso_key(dt: datetime, granularity: GranularityType) -> str:
    """Return a bucket key string for the given datetime."""
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if granularity == "hour":
        return utc.strftime("%Y-%m-%dT%H:00Z")
    elif granularity == "week":
        # ISO week start (Monday)
        monday = utc - timedelta(days=utc.weekday())
        return monday.strftime("%Y-%m-%d")
    else:
        return utc.strftime("%Y-%m-%d")


def _color_scale(value: float, max_val: float) -> str:
    """Map 0‥max_val → green intensity hex color."""
    if max_val <= 0 or value <= 0:
        return "#ebedf0"
    ratio = min(value / max_val, 1.0)
    # 5-level scale matching GitHub contribution graph
    if ratio == 0:
        return "#ebedf0"
    if ratio < 0.25:
        return "#9be9a8"
    if ratio < 0.50:
        return "#40c463"
    if ratio < 0.75:
        return "#30a14e"
    return "#216e39"


class HeatmapBucket:
    """Accumulator for a single time bucket."""

    __slots__ = ("key", "tokens_in", "tokens_out", "event_count",
                 "models", "sessions", "cost")

    def __init__(self, key: str) -> None:
        self.key = key
        self.tokens_in: int = 0
        self.tokens_out: int = 0
        self.event_count: int = 0
        self.cost: float = 0.0
        self.models: dict[str, int] = defaultdict(int)
        self.sessions: set[str] = set()

    def add_event(self, event: AgentEvent) -> None:
        self.tokens_in += event.tokens_in
        self.tokens_out += event.tokens_out
        self.event_count += 1
        if event.model:
            self.models[event.model] += 1
        if event.session_id:
            self.sessions.add(event.session_id)

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def metric(self, name: MetricType) -> float:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "event_count": self.event_count,
            "cost": self.cost,
        }[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "event_count": self.event_count,
            "cost": round(self.cost, 6),
            "models": dict(self.models),
            "session_count": len(self.sessions),
        }


class HeatmapBuilder:
    """Build token-usage heatmaps from sessions and events."""

    # Simple default cost rates (per 1K tokens) — users can override
    DEFAULT_COSTS: dict[str, tuple[float, float]] = {
        "gpt-4": (0.03, 0.06),
        "gpt-4o": (0.005, 0.015),
        "gpt-3.5-turbo": (0.0005, 0.0015),
        "claude-3-opus": (0.015, 0.075),
        "claude-3-sonnet": (0.003, 0.015),
        "claude-3-haiku": (0.00025, 0.00125),
    }

    def __init__(
        self,
        granularity: GranularityType = "day",
        metric: MetricType = "tokens_total",
        cost_rates: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.granularity = granularity
        self.metric = metric
        self.cost_rates = cost_rates or dict(self.DEFAULT_COSTS)
        self._buckets: dict[str, HeatmapBucket] = {}
        self._events_added: int = 0

    def _bucket(self, key: str) -> HeatmapBucket:
        if key not in self._buckets:
            self._buckets[key] = HeatmapBucket(key)
        return self._buckets[key]

    def _estimate_cost(self, event: AgentEvent) -> float:
        if not event.model:
            return 0.0
        model = event.model.lower()
        for prefix, (cin, cout) in self.cost_rates.items():
            if prefix in model:
                return (event.tokens_in * cin + event.tokens_out * cout) / 1000
        return 0.0

    def add_event(self, event: AgentEvent) -> None:
        """Add a single event to the heatmap."""
        key = _iso_key(event.timestamp, self.granularity)
        bucket = self._bucket(key)
        bucket.add_event(event)
        bucket.cost += self._estimate_cost(event)
        self._events_added += 1

    def add_session(self, session: Session) -> None:
        """Add all events from a session."""
        for event in session.events:
            if not event.session_id:
                event.session_id = session.session_id
            self.add_event(event)

    def sorted_buckets(self) -> list[HeatmapBucket]:
        return sorted(self._buckets.values(), key=lambda b: b.key)

    def summary(self) -> dict[str, Any]:
        """Aggregate stats across all buckets."""
        buckets = self.sorted_buckets()
        if not buckets:
            return {"total_events": 0, "total_tokens": 0, "buckets": 0}
        total_in = sum(b.tokens_in for b in buckets)
        total_out = sum(b.tokens_out for b in buckets)
        total_cost = sum(b.cost for b in buckets)
        all_models: dict[str, int] = defaultdict(int)
        all_sessions: set[str] = set()
        for b in buckets:
            for m, c in b.models.items():
                all_models[m] += c
            all_sessions |= b.sessions
        peak = max(buckets, key=lambda b: b.metric(self.metric))
        return {
            "total_events": self._events_added,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "total_tokens": total_in + total_out,
            "total_cost": round(total_cost, 4),
            "buckets": len(buckets),
            "sessions": len(all_sessions),
            "models": dict(all_models),
            "peak_bucket": peak.key,
            "peak_value": peak.metric(self.metric),
            "date_range": (buckets[0].key, buckets[-1].key),
            "metric": self.metric,
            "granularity": self.granularity,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "buckets": [b.to_dict() for b in self.sorted_buckets()],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def render(self, title: str = "Token Usage Heatmap") -> str:
        """Render a self-contained interactive HTML heatmap page."""
        buckets = self.sorted_buckets()
        summary = self.summary()
        bucket_data = json.dumps([b.to_dict() for b in buckets])
        summary_data = json.dumps(summary)
        title_esc = _html.escape(title)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#0d1117;color:#c9d1d9;padding:24px;min-height:100vh}}
h1{{font-size:1.6rem;margin-bottom:8px}}
.subtitle{{color:#8b949e;margin-bottom:20px}}
.stats{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;min-width:140px}}
.stat .label{{font-size:.75rem;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}}
.stat .value{{font-size:1.4rem;font-weight:600;margin-top:4px}}
.controls{{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
.controls select,.controls button{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;
  border-radius:6px;padding:6px 12px;cursor:pointer;font-size:.85rem}}
.controls select:hover,.controls button:hover{{border-color:#58a6ff}}
.heatmap-container{{overflow-x:auto;margin-bottom:24px}}
.heatmap{{display:flex;gap:3px}}
.week-col{{display:flex;flex-direction:column;gap:3px}}
.cell{{width:15px;height:15px;border-radius:3px;cursor:pointer;transition:transform .1s}}
.cell:hover{{transform:scale(1.4);outline:2px solid #58a6ff}}
.legend{{display:flex;align-items:center;gap:4px;margin-bottom:24px;font-size:.8rem;color:#8b949e}}
.legend .cell{{cursor:default;width:13px;height:13px}}
.tooltip{{position:fixed;background:#1c2128;border:1px solid #30363d;border-radius:8px;
  padding:10px 14px;font-size:.8rem;pointer-events:none;z-index:100;display:none;
  box-shadow:0 4px 12px rgba(0,0,0,.4)}}
.tooltip .tt-key{{font-weight:600;color:#58a6ff;margin-bottom:4px}}
.tooltip .tt-row{{display:flex;justify-content:space-between;gap:16px}}
.tooltip .tt-label{{color:#8b949e}}
.bar-section{{margin-top:24px}}
.bar-section h2{{font-size:1.1rem;margin-bottom:12px}}
.bar-chart{{display:flex;flex-direction:column;gap:6px;max-width:600px}}
.bar-row{{display:flex;align-items:center;gap:8px}}
.bar-row .bar-label{{width:120px;font-size:.8rem;text-align:right;color:#8b949e;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar-row .bar{{height:20px;border-radius:4px;background:#238636;transition:width .3s}}
.bar-row .bar-val{{font-size:.75rem;color:#8b949e;min-width:50px}}
</style>
</head>
<body>
<h1>📊 {title_esc}</h1>
<p class="subtitle">AgentLens &mdash; {_html.escape(self.granularity)}-level &middot; {_html.escape(self.metric)}</p>

<div class="stats" id="stats"></div>

<div class="controls">
  <select id="metricSel">
    <option value="tokens_total">Total Tokens</option>
    <option value="tokens_in">Input Tokens</option>
    <option value="tokens_out">Output Tokens</option>
    <option value="event_count">Event Count</option>
    <option value="cost">Estimated Cost</option>
  </select>
  <button onclick="exportJSON()">Export JSON</button>
</div>

<div class="legend" id="legend"></div>
<div class="heatmap-container"><div class="heatmap" id="heatmap"></div></div>

<div class="bar-section" id="modelSection">
  <h2>Token Usage by Model</h2>
  <div class="bar-chart" id="modelBars"></div>
</div>

<div class="tooltip" id="tip"></div>

<script>
const buckets={bucket_data};
const summary={summary_data};
let currentMetric='{self.metric}';

function fmt(n){{if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return n.toFixed?n<1?n.toFixed(4):n.toFixed(0):n}}
function colors(v,mx){{if(mx<=0||v<=0)return'#161b22';const r=v/mx;if(r<.25)return'#0e4429';if(r<.5)return'#006d32';if(r<.75)return'#26a641';return'#39d353'}}

function renderStats(){{
  const el=document.getElementById('stats');
  const items=[
    ['Events',fmt(summary.total_events)],
    ['Tokens In',fmt(summary.total_tokens_in)],
    ['Tokens Out',fmt(summary.total_tokens_out)],
    ['Est. Cost','$'+summary.total_cost.toFixed(2)],
    ['Sessions',summary.sessions],
    ['Peak',summary.peak_bucket],
  ];
  el.innerHTML=items.map(([l,v])=>`<div class="stat"><div class="label">${{l}}</div><div class="value">${{v}}</div></div>`).join('');
}}

function renderHeatmap(){{
  const el=document.getElementById('heatmap');
  const vals=buckets.map(b=>b[currentMetric]||0);
  const mx=Math.max(...vals,1);
  // Group by week columns (7 rows)
  const weeks=[];let col=[];
  buckets.forEach((b,i)=>{{
    col.push(b);
    if(col.length===7){{weeks.push(col);col=[]}}
  }});
  if(col.length)weeks.push(col);
  el.innerHTML=weeks.map(w=>
    '<div class="week-col">'+w.map(b=>{{
      const v=b[currentMetric]||0;
      return `<div class="cell" style="background:${{colors(v,mx)}}" data-key="${{b.key}}" data-val="${{v}}" data-tin="${{b.tokens_in}}" data-tout="${{b.tokens_out}}" data-ev="${{b.event_count}}" data-cost="${{b.cost}}" data-sc="${{b.session_count}}"></div>`;
    }}).join('')+'</div>'
  ).join('');

  // Legend
  document.getElementById('legend').innerHTML='Less '+[0,.25,.5,.75,1].map(r=>
    `<div class="cell" style="background:${{colors(r,1)}}"></div>`
  ).join('')+' More';
}}

function renderModels(){{
  const el=document.getElementById('modelBars');
  const models=summary.models||{{}};
  const entries=Object.entries(models).sort((a,b)=>b[1]-a[1]);
  const mx=entries.length?entries[0][1]:1;
  el.innerHTML=entries.map(([m,c])=>
    `<div class="bar-row"><span class="bar-label" title="${{m}}">${{m}}</span><div class="bar" style="width:${{Math.max(c/mx*100,2)}}%"></div><span class="bar-val">${{fmt(c)}}</span></div>`
  ).join('')||'<p style="color:#8b949e">No model data</p>';
}}

function exportJSON(){{
  const blob=new Blob([JSON.stringify({{summary,buckets}},null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='agentlens-heatmap.json';a.click();
}}

// Tooltip
const tip=document.getElementById('tip');
document.getElementById('heatmap').addEventListener('mousemove',e=>{{
  const c=e.target.closest('.cell');
  if(!c){{tip.style.display='none';return}}
  tip.style.display='block';
  tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  tip.innerHTML=`<div class="tt-key">${{c.dataset.key}}</div>
    <div class="tt-row"><span class="tt-label">Tokens In</span><span>${{fmt(+c.dataset.tin)}}</span></div>
    <div class="tt-row"><span class="tt-label">Tokens Out</span><span>${{fmt(+c.dataset.tout)}}</span></div>
    <div class="tt-row"><span class="tt-label">Events</span><span>${{c.dataset.ev}}</span></div>
    <div class="tt-row"><span class="tt-label">Cost</span><span>$${{(+c.dataset.cost).toFixed(4)}}</span></div>
    <div class="tt-row"><span class="tt-label">Sessions</span><span>${{c.dataset.sc}}</span></div>`;
}});
document.getElementById('heatmap').addEventListener('mouseleave',()=>tip.style.display='none');

document.getElementById('metricSel').addEventListener('change',e=>{{
  currentMetric=e.target.value;renderHeatmap();
}});
document.getElementById('metricSel').value=currentMetric;

renderStats();renderHeatmap();renderModels();
</script>
</body>
</html>"""

    def save(self, path: str) -> str:
        """Render and write HTML to *path*. Returns the path."""
        content = self.render()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path
