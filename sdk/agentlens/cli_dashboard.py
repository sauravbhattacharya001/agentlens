"""AgentLens CLI — interactive HTML dashboard generation.

Extracted from cli.py to keep the main CLI dispatcher lean.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentlens.cli_common import get_client as _get_client, print_json as _print_json


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Generate a self-contained HTML dashboard with interactive charts."""
    import webbrowser as _webbrowser
    from datetime import datetime, timezone

    client, endpoint = _get_client(args)
    limit = getattr(args, "limit", 100) or 100
    output = getattr(args, "output", None)

    # Fetch data
    print(f"\U0001f4ca Fetching data from {endpoint} ...")
    resp = client.get("/sessions", params={"limit": limit})
    resp.raise_for_status()
    raw = resp.json()
    sessions = raw if isinstance(raw, list) else raw.get("sessions", [raw])

    try:
        resp2 = client.get("/analytics")
        resp2.raise_for_status()
        analytics = resp2.json()
    except Exception:
        analytics = {}

    summary = _aggregate_sessions(sessions)
    html = _render_html(sessions, summary, endpoint)

    fname = output or "agentlens-dashboard.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\u2705 Dashboard written to {fname}")

    if getattr(args, "open", False):
        _webbrowser.open(fname)
        print("\U0001f310 Opened in browser")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _aggregate_sessions(sessions: list[dict]) -> dict[str, Any]:
    """Compute aggregate metrics from a list of session dicts."""
    model_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    daily_sessions: dict[str, int] = {}
    daily_costs: dict[str, float] = {}
    total_cost = 0.0
    total_tokens = 0
    total_events = 0
    error_count = 0
    rows: list[dict[str, Any]] = []

    for s in sessions:
        sid = s.get("id", "?")
        agent = s.get("agent_name", "unknown")
        status = s.get("status", "unknown")
        tokens = int(s.get("total_tokens", 0) or 0)
        events = int(s.get("event_count", 0) or 0)
        cost = float(s.get("total_cost", 0) or 0)
        created = s.get("created_at", "")

        rows.append({
            "id": sid, "agent": agent, "status": status,
            "tokens": tokens, "events": events, "cost": cost,
            "created": created,
        })

        status_counts[status] = status_counts.get(status, 0) + 1
        total_cost += cost
        total_tokens += tokens
        total_events += events
        if status in ("error", "failed"):
            error_count += 1

        day = created[:10] if len(created) >= 10 else "unknown"
        daily_sessions[day] = daily_sessions.get(day, 0) + 1
        daily_costs[day] = daily_costs.get(day, 0) + cost

        for ev in s.get("events", []):
            m = ev.get("model", "")
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1

    return {
        "rows": rows,
        "model_counts": model_counts,
        "status_counts": status_counts,
        "daily_sessions": daily_sessions,
        "daily_costs": daily_costs,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "total_events": total_events,
        "error_count": error_count,
    }


def _render_html(sessions: list[dict], summary: dict[str, Any], endpoint: str) -> str:
    """Build the self-contained HTML dashboard string."""
    from datetime import datetime, timezone

    daily_sessions = summary["daily_sessions"]
    daily_costs = summary["daily_costs"]
    status_counts = summary["status_counts"]
    model_counts = summary["model_counts"]
    rows = summary["rows"]
    total_cost = summary["total_cost"]
    total_tokens = summary["total_tokens"]
    total_events = summary["total_events"]
    error_count = summary["error_count"]

    sorted_days = sorted(daily_sessions.keys())
    day_labels = json.dumps(sorted_days)
    day_session_data = json.dumps([daily_sessions.get(d, 0) for d in sorted_days])
    day_cost_data = json.dumps([round(daily_costs.get(d, 0), 4) for d in sorted_days])

    status_labels = json.dumps(list(status_counts.keys()))
    status_data = json.dumps(list(status_counts.values()))

    top_by_cost = sorted(rows, key=lambda r: r["cost"], reverse=True)[:10]
    top_labels = json.dumps([r["id"][:12] for r in top_by_cost])
    top_data = json.dumps([round(r["cost"], 4) for r in top_by_cost])

    table_html = ""
    for r in rows[:50]:
        css = ' class="error"' if r["status"] in ("error", "failed") else ""
        table_html += (
            f'<tr{css}><td>{r["id"][:16]}</td><td>{r["agent"]}</td>'
            f'<td>{r["status"]}</td><td>{r["events"]}</td>'
            f'<td>{r["tokens"]:,}</td><td>${r["cost"]:.4f}</td>'
            f'<td>{r["created"][:19]}</td></tr>\n'
        )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    error_rate = (error_count / len(sessions) * 100) if sessions else 0

    return _DASHBOARD_TEMPLATE.format(
        now_str=now_str,
        endpoint=endpoint,
        session_count=len(sessions),
        total_events=f"{total_events:,}",
        total_tokens=f"{total_tokens:,}",
        total_cost=f"${total_cost:.4f}",
        error_rate=f"{error_rate:.1f}%",
        model_count=len(model_counts),
        day_labels=day_labels,
        day_session_data=day_session_data,
        day_cost_data=day_cost_data,
        status_labels=status_labels,
        status_data=status_data,
        top_labels=top_labels,
        top_data=top_data,
        table_html=table_html,
        table_limit=min(50, len(sessions)),
    )


_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentLens Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}}
h1{{font-size:1.8rem;margin-bottom:4px}}
.subtitle{{color:#94a3b8;margin-bottom:24px;font-size:.9rem}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}}
.kpi{{background:#1e293b;border-radius:12px;padding:20px;text-align:center}}
.kpi .value{{font-size:2rem;font-weight:700;color:#38bdf8}}
.kpi .label{{font-size:.8rem;color:#94a3b8;margin-top:4px}}
.kpi.error .value{{color:#f87171}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:24px;margin-bottom:32px}}
.chart-card{{background:#1e293b;border-radius:12px;padding:20px}}
.chart-card h3{{margin-bottom:12px;font-size:1rem;color:#cbd5e1}}
canvas{{max-height:280px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #334155}}
th{{background:#1e293b;color:#94a3b8;position:sticky;top:0}}
tr:hover{{background:#1e293b}}
tr.error td{{color:#f87171}}
.table-wrap{{background:#1e293b;border-radius:12px;padding:20px;max-height:500px;overflow:auto}}
.table-wrap h3{{margin-bottom:12px;color:#cbd5e1}}
</style>
</head>
<body>
<h1>\U0001f50d AgentLens Dashboard</h1>
<p class="subtitle">Generated {now_str} \u00b7 {session_count} sessions from {endpoint}</p>

<div class="kpi-grid">
<div class="kpi"><div class="value">{session_count}</div><div class="label">Sessions</div></div>
<div class="kpi"><div class="value">{total_events}</div><div class="label">Total Events</div></div>
<div class="kpi"><div class="value">{total_tokens}</div><div class="label">Total Tokens</div></div>
<div class="kpi"><div class="value">{total_cost}</div><div class="label">Total Cost</div></div>
<div class="kpi error"><div class="value">{error_rate}</div><div class="label">Error Rate</div></div>
<div class="kpi"><div class="value">{model_count}</div><div class="label">Models Used</div></div>
</div>

<div class="charts">
<div class="chart-card"><h3>Sessions per Day</h3><canvas id="dailyChart"></canvas></div>
<div class="chart-card"><h3>Daily Cost ($)</h3><canvas id="costChart"></canvas></div>
<div class="chart-card"><h3>Session Status</h3><canvas id="statusChart"></canvas></div>
<div class="chart-card"><h3>Top 10 Sessions by Cost</h3><canvas id="topChart"></canvas></div>
</div>

<div class="table-wrap">
<h3>Sessions (latest {table_limit})</h3>
<table>
<thead><tr><th>ID</th><th>Agent</th><th>Status</th><th>Events</th><th>Tokens</th><th>Cost</th><th>Created</th></tr></thead>
<tbody>{table_html}</tbody>
</table>
</div>

<script>
const colors = ['#38bdf8','#a78bfa','#34d399','#fbbf24','#f87171','#fb923c','#e879f9','#22d3ee'];
const doughnutColors = ['#34d399','#f87171','#fbbf24','#38bdf8','#a78bfa','#fb923c'];

new Chart(document.getElementById('dailyChart'),{{
  type:'bar',
  data:{{labels:{day_labels},datasets:[{{label:'Sessions',data:{day_session_data},backgroundColor:'#38bdf8',borderRadius:4}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},x:{{ticks:{{color:'#94a3b8',maxRotation:45}}}}}}}}
}});

new Chart(document.getElementById('costChart'),{{
  type:'line',
  data:{{labels:{day_labels},datasets:[{{label:'Cost ($)',data:{day_cost_data},borderColor:'#a78bfa',backgroundColor:'rgba(167,139,250,0.1)',fill:true,tension:0.3}}]}},
  options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{y:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},x:{{ticks:{{color:'#94a3b8',maxRotation:45}}}}}}}}
}});

new Chart(document.getElementById('statusChart'),{{
  type:'doughnut',
  data:{{labels:{status_labels},datasets:[{{data:{status_data},backgroundColor:doughnutColors}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:'bottom',labels:{{color:'#cbd5e1'}}}}}}}}
}});

new Chart(document.getElementById('topChart'),{{
  type:'bar',
  data:{{labels:{top_labels},datasets:[{{label:'Cost ($)',data:{top_data},backgroundColor:'#fbbf24',borderRadius:4}}]}},
  options:{{responsive:true,indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:{{beginAtZero:true,ticks:{{color:'#94a3b8'}}}},y:{{ticks:{{color:'#94a3b8'}}}}}}}}
}});
</script>
</body>
</html>"""
