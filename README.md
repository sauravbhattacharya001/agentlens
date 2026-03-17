<div align="center">

# 🔍 AgentLens

**Observability and Explainability for AI Agents**

*Datadog meets Chain-of-Thought — for autonomous agents*

[![CI](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml)
[![CodeQL](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml)
[![Coverage](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/agentlens?logo=pypi&logoColor=white)](https://pypi.org/project/agentlens/)
[![npm version](https://img.shields.io/npm/v/agentlens-backend?logo=npm&logoColor=white)](https://www.npmjs.com/package/agentlens-backend)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org)

[Quick Start](#-quick-start) · [Features](#-features) · [SDK Guide](#-sdk-guide) · [Dashboard](#-dashboard) · [Architecture](#-architecture) · [API Reference](#-api-reference) · [📖 Full Docs](https://sauravbhattacharya001.github.io/agentlens/) · [🎯 Live Demo](https://sauravbhattacharya001.github.io/agentlens/demo/)

</div>

---

## 🎯 What is AgentLens?

As AI agents become more autonomous — making decisions, calling tools, chaining actions — you need to **see inside the black box**. AgentLens gives you full visibility into what your agents are doing, why they're doing it, and how much it costs.

**In 30 seconds:**

```python
import agentlens

agentlens.init(endpoint="http://localhost:3000")
session = agentlens.start_session(agent_name="my-agent")

agentlens.track(
    event_type="llm_call",
    input_data={"prompt": "What is 2+2?"},
    output_data={"response": "4"},
    model="gpt-4",
    tokens_in=12, tokens_out=3,
    reasoning="Simple arithmetic — answered directly",
)

print(agentlens.explain())  # Human-readable summary of what happened
agentlens.end_session()
```

Then open `http://localhost:3000` to see session traces, token charts, cost breakdowns, and decision timelines.

## ✨ Features

| Category | Capabilities |
|----------|-------------|
| **Tracing** | Session-level execution traces, interactive timelines, tool call capture with I/O and duration |
| **Cost & Tokens** | Per-model token tracking, configurable pricing, per-session cost breakdowns, budget limits with overage alerts |
| **Explainability** | Decision traces capturing agent reasoning, human-readable behavior summaries |
| **Analysis** | Anomaly detection (z-score), health scoring (A–F grades), session comparison, error analysis by type/agent/model |
| **Ops** | Alert rules with metric thresholds, webhooks, data retention policies, correlation rules with scheduling |
| **Organization** | Session tags, bookmarks, annotations, dependency graphing, agent leaderboard, postmortem reports |

## 🏗️ Architecture

```
┌──────────────┐     HTTP POST      ┌──────────────────┐     SQLite      ┌──────────┐
│  Your Agent  │ ──────────────────► │  AgentLens API   │ ──────────────► │    DB    │
│  + SDK       │    /events          │  (Express.js)    │                 └──────────┘
└──────────────┘                     └────────┬─────────┘
                                              │ REST API
                                     ┌────────┴─────────┐
                                     │    Dashboard      │
                                     │  (HTML/CSS/JS)    │
                                     └──────────────────┘
```

| Component | Directory | Stack |
|-----------|-----------|-------|
| **Python SDK** | `sdk/` | Python 3.9+, Pydantic, httpx |
| **Backend API** | `backend/` | Node.js 18+, Express, better-sqlite3 |
| **Dashboard** | `dashboard/` | Vanilla HTML/CSS/JS (no build step) |

## 🚀 Quick Start

### Prerequisites

- **Python 3.9+** (SDK) and **Node.js 18+** (backend)

### 1. Start the backend

```bash
git clone https://github.com/sauravbhattacharya001/agentlens.git
cd agentlens/backend
npm install
node seed.js      # Optional: load demo data
node server.js    # http://localhost:3000
```

The dashboard is served automatically at [http://localhost:3000](http://localhost:3000).

### 2. Install the SDK

```bash
pip install agentlens
```

Or from source: `cd sdk && pip install -e .`

### 3. Instrument your agent

```python
import agentlens

agentlens.init(api_key="your-key", endpoint="http://localhost:3000")
session = agentlens.start_session(agent_name="my-agent")

# Track events manually...
agentlens.track(event_type="llm_call", model="gpt-4", ...)

# ...or use decorators for zero-config instrumentation
@agentlens.track_agent(model="gpt-4")
def my_agent(prompt):
    return call_llm(prompt)

@agentlens.track_tool_call(tool_name="web_search")
def search(query):
    return do_search(query)

agentlens.end_session()
```

### 4. Run the demo

```bash
cd sdk/examples && python mock_agent.py
# Then open http://localhost:3000
```

### 5. Use the CLI

```bash
agentlens status                              # Check backend connectivity
agentlens sessions --limit 10                 # List recent sessions
agentlens costs <session_id>                  # Cost breakdown
agentlens health <session_id>                 # Health score (A–F)
agentlens compare <session_a> <session_b>     # Side-by-side comparison
agentlens events --type llm_call --model gpt-4  # Search events
agentlens export <session_id> --format csv -o report.csv
agentlens analytics                           # Aggregate stats
agentlens alerts                              # Recent alerts
```

Configure via environment variables (`AGENTLENS_ENDPOINT`, `AGENTLENS_API_KEY`) or `--endpoint`/`--api-key` flags.

## 📖 SDK Guide

### Session Management

```python
session = agentlens.start_session(
    agent_name="my-agent",
    metadata={"env": "prod"}
)
# ... track events ...
agentlens.end_session()
```

### Event Tracking

```python
agentlens.track(
    event_type="llm_call",         # llm_call | tool_call | generic
    input_data={"prompt": "..."},
    output_data={"text": "..."},
    model="gpt-4",
    tokens_in=100, tokens_out=50,
    reasoning="...",               # Decision trace
    tool_name="search",            # For tool_call events
    tool_input={"query": "..."},
    tool_output={"results": []},
    duration_ms=1500.0,
)
```

### Decorators

```python
from agentlens import track_agent, track_tool_call

@track_agent(model="gpt-4")
def my_agent(prompt):
    """Automatically captures input, output, timing, and tokens."""
    return call_llm(prompt)

@track_tool_call(tool_name="web_search")
def search(query):
    """Automatically captures tool I/O."""
    return do_search(query)
```

### Explainability

```python
explanation = agentlens.explain()
# "The agent received a question about arithmetic.
#  It called GPT-4 which responded with '4'.
#  Total tokens used: 15 (12 in, 3 out)."
```

### Cost Tracking

```python
costs = agentlens.get_costs()
print(f"Total: ${costs['total_cost']:.4f}")

# Configure custom pricing (per 1M tokens)
agentlens.set_pricing({
    "my-model": {"input_cost_per_1m": 5.00, "output_cost_per_1m": 15.00}
})
```

### Session Comparison

```python
result = agentlens.compare_sessions("session-a", "session-b")
print(f"Token delta: {result['deltas']['total_tokens']['percent']}%")
```

### Anomaly Detection

```python
from agentlens import AnomalyDetector, AnomalyDetectorConfig

detector = AnomalyDetector(AnomalyDetectorConfig(
    warning_threshold=2.0,    # 2σ warning
    critical_threshold=3.0,   # 3σ critical
))
report = detector.analyze(session_events)
for anomaly in report.anomalies:
    print(f"[{anomaly.severity.value}] {anomaly.kind.value}: {anomaly.description}")
```

### Health Scoring

```python
from agentlens import HealthScorer

report = HealthScorer().score(session_events)
print(f"Overall: {report.overall_grade.value} ({report.overall_score:.0f}/100)")
```

### Tags, Annotations & Alerts

```python
# Tags
tracker.add_tags(["production", "v2.0"])
tracker.list_sessions_by_tag("production")

# Annotations
tracker.annotate("Latency spike at step 5", annotation_type="warning", author="bot")

# Alert rules
tracker.create_alert_rule(
    name="High Error Rate", metric="error_rate",
    condition="gt", threshold=0.1,
)
alerts = tracker.evaluate_alerts()
```

### Data Retention

```python
tracker.set_retention_config(
    max_age_days=30, max_sessions=10000,
    exempt_tags=["production"], auto_purge=True,
)
preview = tracker.purge(dry_run=True)
```

## 📊 Dashboard

The dashboard is a lightweight HTML/CSS/JS app served directly by the backend — no build step required.

- **Sessions List** — Browse and filter by status, search across sessions
- **Timeline View** — Interactive timeline of every event in a session
- **Token & Cost Charts** — Per-event and cumulative usage visualization
- **Explain Tab** — Human-readable behavior summaries
- **Session Comparison** — Side-by-side diffs with visual deltas
- **Analytics Overview** — Aggregate stats, model usage, hourly activity heatmap

## 🔌 API Reference

The backend exposes a comprehensive REST API. Key endpoint groups:

| Group | Base Path | Description |
|-------|-----------|-------------|
| **Sessions** | `/sessions` | List, search, get details, export, compare |
| **Events** | `/events` | Ingest events (batched, up to 500/call) |
| **Analytics** | `/analytics` | Aggregate stats, performance metrics, heatmap |
| **Pricing & Costs** | `/pricing` | Model pricing config, per-session cost calculation |
| **Alerts** | `/alerts` | Alert rules, evaluation, event history |
| **Webhooks** | `/webhooks` | Webhook management, test deliveries, delivery history |
| **Correlations** | `/correlations` | Correlation rules, groups, scheduling |
| **Tags** | `/tags` | Session tagging and tag-based queries |
| **Bookmarks** | `/bookmarks` | Session bookmarking |
| **Annotations** | `/annotations` | Timestamped notes on sessions/events |
| **Baselines** | `/baselines` | Agent performance baselines and checks |
| **Errors** | `/errors` | Error analysis by type, agent, and model |
| **Dependencies** | `/dependencies` | Service dependency graphs and trends |
| **Leaderboard** | `/leaderboard` | Top-performing agents ranking |
| **Postmortem** | `/postmortem` | Auto-generated postmortem reports |
| **Retention** | `/retention` | Data retention config and manual purge |
| **Health** | `/health` | Health check |

For full endpoint details, see the [API documentation](https://sauravbhattacharya001.github.io/agentlens/).

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Quick dev setup
cd backend && npm install && node server.js    # Backend with auto-reload
cd sdk && pip install -e ".[dev]" && pytest     # SDK with tests
```

## 📄 License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

**Built by [Saurav Bhattacharya](https://github.com/sauravbhattacharya001)**

*Because if you can't see what your agents are doing, you can't trust them.*

</div>
