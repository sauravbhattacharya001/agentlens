<div align="center">

# 🔍 AgentLens

**Observability and Explainability for AI Agents**

*Datadog meets Chain-of-Thought — for autonomous agents*

[![CI](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml)
[![CodeQL](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml)
[![Coverage](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml)
[![codecov](https://codecov.io/gh/sauravbhattacharya001/agentlens/graph/badge.svg)](https://codecov.io/gh/sauravbhattacharya001/agentlens)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/agentlens?logo=pypi&logoColor=white)](https://pypi.org/project/agentlens/)
[![npm version](https://img.shields.io/npm/v/agentlens-backend?logo=npm&logoColor=white)](https://www.npmjs.com/package/agentlens-backend)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org)
[![GitHub repo size](https://img.shields.io/github/repo-size/sauravbhattacharya001/agentlens)](https://github.com/sauravbhattacharya001/agentlens)
[![GitHub last commit](https://img.shields.io/github/last-commit/sauravbhattacharya001/agentlens)](https://github.com/sauravbhattacharya001/agentlens/commits)
[![GitHub issues](https://img.shields.io/github/issues/sauravbhattacharya001/agentlens)](https://github.com/sauravbhattacharya001/agentlens/issues)
[![GitHub stars](https://img.shields.io/github/stars/sauravbhattacharya001/agentlens?style=social)](https://github.com/sauravbhattacharya001/agentlens)

[Getting Started](#-getting-started) · [Features](#-features) · [SDK Reference](#-sdk-reference) · [Dashboard](#-dashboard) · [Architecture](#-architecture) · [Contributing](#-contributing) · [📖 Full Docs](https://sauravbhattacharya001.github.io/agentlens/) · [🎯 Live Demo](https://sauravbhattacharya001.github.io/agentlens/demo/)

</div>

---

## 🎯 What is AgentLens?

AgentLens gives you full visibility into what your AI agents are doing, why they're doing it, and how much it costs. As AI agents become more autonomous — making decisions, calling tools, chaining actions — you need to **see inside the black box**.

AgentLens provides:
- **Session-level tracing** for every agent run
- **Token and cost tracking** across models and calls
- **Decision traces** capturing *why* an agent made each choice
- **Human-readable explanations** of agent behavior
- **A real-time dashboard** to monitor everything visually

## 🤔 Why AgentLens?

| | LangSmith | Helicone | Weights & Biases | **AgentLens** |
|---|:---:|:---:|:---:|:---:|
| Self-hosted | ❌ | ❌ | ❌ | ✅ |
| Zero external dependencies | ❌ | ❌ | ❌ | ✅ |
| Decision-level explainability | ❌ | ❌ | ❌ | ✅ |
| Built-in anomaly detection | ❌ | ❌ | ❌ | ✅ |
| Session comparison & diff | ❌ | ❌ | ❌ | ✅ |
| Cost forecasting | ❌ | Partial | ❌ | ✅ |
| No vendor lock-in | ❌ | ❌ | ❌ | ✅ |
| Free & open source | ❌ | Partial | ❌ | ✅ |

AgentLens runs entirely on your infrastructure — SQLite for storage, no cloud dependencies, no data leaving your network.

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📊 **Session Tracking** | Group agent actions into sessions with full execution traces |
| 🛠️ **Tool Call Capture** | Record every tool invocation with inputs, outputs, and duration |
| 💰 **Token Usage** | Track token consumption and costs across models |
| 🧠 **Decision Traces** | Capture the reasoning behind each agent decision |
| 📈 **Visual Timeline** | Interactive timeline view of agent actions in the dashboard |
| 💡 **Explainability** | Generate human-readable summaries of agent behavior |
| 🎨 **Decorators** | Zero-config instrumentation with Python decorators |
| 📈 **Analytics Dashboard** | Aggregate stats, model usage, hourly activity heatmap, sessions-over-time |
| ⚖️ **Session Comparison** | Compare two sessions side-by-side — token deltas, event breakdowns, tool usage diffs |
| 💲 **Cost Estimation** | Configurable model pricing, per-session/event cost tracking, cost breakdown dashboard |
| 🔔 **Alert Rules** | Configurable alert rules with metric thresholds and event triggers |
| 🏷️ **Session Tags** | Tag sessions for filtering, organization, and retention exemption |
| 📝 **Annotations** | Timestamped notes on sessions and events for auditing |
| 🗄️ **Data Retention** | Configurable retention policies with auto-purge and exempt tags |
| 🔍 **Event Search** | Rich filtering across sessions — by type, model, tokens, duration |
| 🔬 **Anomaly Detection** | Z-score statistical analysis to detect latency spikes, token surges, error bursts |
| 🏥 **Health Scoring** | Grade sessions A–F based on error rates, latency, tool failures |
| 💸 **Cost Budgets** | Per-agent and global spending limits with real-time tracking, warnings, and overage detection |
| 📖 **Session Narratives** | Auto-generate human-readable summaries of agent session behavior |
| 🏆 **Agent Scorecards** | Per-agent performance grading with composite scores and letter grades |
| 🔮 **Cost Forecasting** | Budget projections with what-if simulator and model breakdown |
| 📊 **Token Heatmap** | Calendar-style visualization of token consumption patterns |
| ⏱️ **Trace Waterfall** | Interactive Gantt-style event visualization for session traces |
| 🔄 **Session Diff** | Side-by-side visual comparison of two agent sessions |
| ❌ **Error Analytics** | Error grouping by type, agent, and model with trend analysis |
| 🎯 **Command Center** | Unified activity feed aggregating alerts, anomalies, budget warnings, and health signals |
| 📋 **SLA Compliance** | Track SLA targets with compliance rings, violation alerts, and history |
| 🩺 **Auto-Triage** | Unified session diagnostics — runs health, anomaly, drift, error, and cost analysis in one call with prioritized findings |
| 📐 **Capacity Planning** | Fleet capacity planning — workload projection, resource sizing, scaling recommendations, bottleneck detection |
| 🛡️ **Session Guardrails** | Constraint validation — token budgets, duration limits, tool allowlists, model restrictions, custom predicates |
| 💡 **Cost Optimizer** | Intelligent model selection — task complexity scoring, cheaper-model recommendations, 30-70% savings estimation |
| 📉 **Drift Detection** | Detect behavioral drift from baselines — metric shift analysis with significance scoring |
| 📝 **Prompt Tracking** | Track prompt versions, A/B test variants, and measure prompt performance across sessions |
| 🔄 **Retry Tracking** | Monitor retry patterns, detect retry storms, backoff strategy analysis |
| 🚦 **Rate Limiting** | Client-side rate limiting with token bucket, sliding window, and adaptive strategies |
| 🎲 **Sampling** | Probabilistic event sampling — head-based, tail-based, and priority sampling strategies |
| ⏪ **Session Replay** | Replay recorded sessions step-by-step with configurable speed and event filtering |

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

| Component | Directory | Tech Stack |
|-----------|-----------|------------|
| **Python SDK** | `sdk/` | Python 3.9+, Pydantic, httpx |
| **Backend API** | `backend/` | Node.js, Express, better-sqlite3 |
| **Dashboard** | `dashboard/` | Vanilla HTML/CSS/JS (no build step) |

## 🚀 Getting Started

### Prerequisites

- **Python 3.9+** (for the SDK)
- **Node.js 18+** (for the backend)
- **npm** (comes with Node.js)

### 1. Clone the repo

```bash
git clone https://github.com/sauravbhattacharya001/agentlens.git
cd agentlens
```

### 2. Start the Backend

```bash
cd backend
npm install
node seed.js      # Load demo data (optional)
node server.js    # Starts on http://localhost:3000
```

The dashboard is served automatically at [http://localhost:3000](http://localhost:3000).

### 3. Install the Python SDK

```bash
pip install agentlens
```

Or install from source for development:

```bash
cd sdk
pip install -e .
```

### 4. Use the CLI

After installing the SDK, you get the `agentlens` command:

```bash
# Check backend connectivity
agentlens status

# List recent sessions
agentlens sessions --limit 10

# View cost breakdown for a session
agentlens costs <session_id>

# Search events by type or model
agentlens events --type llm_call --model gpt-4

# Export a session to JSON or CSV
agentlens export <session_id> --format csv -o report.csv

# Health score for a session (A–F grading)
agentlens health <session_id>

# Compare two sessions side-by-side
agentlens compare <session_a> <session_b>

# View aggregate analytics
agentlens analytics

# List recent alerts
agentlens alerts

# Generate incident postmortem for a session
agentlens postmortem <session_id>

# List sessions eligible for postmortem analysis
agentlens postmortem --candidates --min-errors 3

# Live session leaderboard
agentlens top

# Live-follow session events
agentlens tail <session_id>

# Generate time-range summary report
agentlens report --from 2024-01-01 --to 2024-01-31

# Generate interactive HTML flamegraph for a session
agentlens flamegraph <session_id> -o profile.html --open

# Print flamegraph statistics without generating HTML
agentlens flamegraph <session_id> --stats

# Generate self-contained HTML dashboard with interactive charts
agentlens dashboard --limit 200 -o dashboard.html --open

# Evaluate sessions against SLA policies
agentlens sla --policy production --limit 100

# Custom SLA targets with verbose output
agentlens sla --latency 2000 --error-rate 5 --token-budget 8000 --slo 95 --verbose

# SLA compliance as JSON for CI/CD pipelines
agentlens sla --policy production --json

# Auto-triage a session — unified diagnostics with prioritized findings
agentlens triage <session_id>
agentlens triage <session_id> --severity high --json

# Capacity planning — workload projection and resource sizing
agentlens capacity --hours 72
agentlens capacity --target-rpm 500 --target-latency 300

# Cost optimization recommendations
agentlens optimize <session_id>
agentlens optimize <session_id> --budget 0.50

# Replay a recorded session step-by-step
agentlens replay <session_id> --speed 2x

# Drift detection — compare current behavior to baselines
agentlens baseline create <agent_name> --sessions 100
agentlens baseline drift <agent_name> --window 24h
```

> 📖 **Full CLI reference:** See [docs/CLI.md](docs/CLI.md) for all 50+ commands with options and examples.

Configure via environment variables:
```bash
export AGENTLENS_ENDPOINT=http://localhost:3000
export AGENTLENS_API_KEY=your-key
```

Or pass `--endpoint` and `--api-key` flags to any command.

### 5. Instrument Your Agent

```python
import agentlens

# Initialize the SDK
agentlens.init(api_key="your-key", endpoint="http://localhost:3000")

# Start a tracking session
session = agentlens.start_session(agent_name="my-agent")

# Track events manually
agentlens.track(
    event_type="llm_call",
    input_data={"prompt": "What is 2+2?"},
    output_data={"response": "4"},
    model="gpt-4",
    tokens_in=12,
    tokens_out=3,
    reasoning="Simple arithmetic question, answered directly",
)

# Get a human-readable explanation
print(agentlens.explain())

# End the session
agentlens.end_session()
```

### 5. Run the Demo

```bash
cd sdk/examples
python mock_agent.py
# Then open http://localhost:3000 to see the results
```

## 📖 SDK Reference

### Initialization

```python
import agentlens

# Connect to your AgentLens backend
tracker = agentlens.init(
    api_key="your-key",           # API key for authentication
    endpoint="http://localhost:3000"  # Backend URL
)
```

### Session Management

```python
# Start a session
session = agentlens.start_session(
    agent_name="my-agent",        # Name of the agent
    metadata={"env": "prod"}      # Optional metadata
)

# End the session (flushes all pending events)
agentlens.end_session()
```

### Manual Event Tracking

```python
event = agentlens.track(
    event_type="llm_call",        # Event type: llm_call, tool_call, generic
    input_data={"prompt": "..."},  # Input to the operation
    output_data={"text": "..."},   # Output from the operation
    model="gpt-4",                # Model used (if applicable)
    tokens_in=100,                # Input tokens
    tokens_out=50,                # Output tokens
    reasoning="...",              # Why the agent made this decision
    tool_name="search",           # Tool name (for tool calls)
    tool_input={"query": "..."},  # Tool input
    tool_output={"results": []},  # Tool output
    duration_ms=1500.0,           # Execution duration in ms
)
```

### Decorators (Zero-Config)

```python
from agentlens import track_agent, track_tool_call

@track_agent(model="gpt-4")
def my_agent(prompt):
    """Automatically tracked — captures input, output, and timing."""
    return call_llm(prompt)

@track_tool_call(tool_name="web_search")
def search(query):
    """Automatically tracked — captures tool input/output."""
    return do_search(query)
```

### Explainability

```python
# Get a human-readable explanation of agent behavior
explanation = agentlens.explain()
print(explanation)
# Output: "The agent received a question about arithmetic.
#          It called GPT-4 which responded with '4'.
#          Total tokens used: 15 (12 in, 3 out)."
```

### Session Comparison

```python
# Compare two sessions side-by-side
result = agentlens.compare_sessions(
    session_a="abc123",
    session_b="def456",
)

# Result includes metrics, deltas, and shared breakdowns
print(f"Token delta: {result['deltas']['total_tokens']['percent']}%")
print(f"Session A events: {result['session_a']['event_count']}")
print(f"Session B events: {result['session_b']['event_count']}")
print(f"Shared tools: {result['shared']['tools']}")
```

### Cost Estimation

```python
# Get cost breakdown for the current session
costs = agentlens.get_costs()
print(f"Total cost: ${costs['total_cost']:.4f}")
print(f"Input cost: ${costs['total_input_cost']:.4f}")
print(f"Output cost: ${costs['total_output_cost']:.4f}")

# Per-model breakdown
for model, mc in costs['model_costs'].items():
    print(f"  {model}: ${mc['total_cost']:.4f} ({mc['calls']} calls)")

# View/update model pricing (per 1M tokens, USD)
pricing = agentlens.get_pricing()
print(pricing['pricing'])  # Current pricing config

# Set custom pricing
agentlens.set_pricing({
    "my-custom-model": {
        "input_cost_per_1m": 5.00,
        "output_cost_per_1m": 15.00,
    }
})
```

### Event Search

```python
# Search events with rich filtering
results = tracker.search_events(
    q="error",                    # Full-text search
    event_type="tool_call",       # Filter by type
    model="gpt-4",               # Filter by model
    min_tokens=100,               # Minimum token count
    has_tools=True,               # Only events with tool calls
    after="2024-01-01T00:00:00Z", # Date range
    limit=50,                     # Max results
)
for event in results["events"]:
    print(f"{event['event_type']}: {event.get('model', 'N/A')}")
```

### Session Tags

```python
# Add tags to the current session
tracker.add_tags(["production", "v2.0", "critical"])

# Remove specific tags
tracker.remove_tags(["v2.0"])

# Get tags for a session
tags = tracker.get_tags()

# List all tags across sessions
all_tags = tracker.list_all_tags()

# Find sessions by tag
sessions = tracker.list_sessions_by_tag("production")
```

### Annotations

```python
# Annotate a session with timestamped notes
tracker.annotate(
    "Latency spike detected at step 5",
    annotation_type="warning",
    author="monitoring-bot",
)
tracker.annotate(
    "Reached goal state",
    annotation_type="milestone",
)

# Retrieve annotations
annotations = tracker.get_annotations(annotation_type="warning")
for ann in annotations["annotations"]:
    print(f"[{ann['type']}] {ann['text']}")

# Update or delete annotations
tracker.update_annotation("ann-id-123", text="Updated note")
tracker.delete_annotation("ann-id-456")
```

### Alert Rules

```python
# Create an alert rule
tracker.create_alert_rule(
    name="High Error Rate",
    metric="error_rate",
    condition="gt",
    threshold=0.1,
    description="Fires when error rate exceeds 10%",
)

# List and evaluate rules
rules = tracker.list_alert_rules()
alerts = tracker.evaluate_alerts()  # Check all rules against recent data
alert_events = tracker.get_alert_events(limit=20)
```

### Anomaly Detection

```python
from agentlens import AnomalyDetector, AnomalyDetectorConfig

config = AnomalyDetectorConfig(
    warning_threshold=2.0,   # 2σ = warning
    critical_threshold=3.0,  # 3σ = critical
)
detector = AnomalyDetector(config)

# Analyze a session for anomalies
report = detector.analyze(session_events)
print(f"Found {len(report.anomalies)} anomalies")
for anomaly in report.anomalies:
    print(f"  [{anomaly.severity.value}] {anomaly.kind.value}: {anomaly.description}")
```

### Health Scoring

```python
from agentlens import HealthScorer, HealthThresholds

scorer = HealthScorer()
report = scorer.score(session_events)

print(f"Overall: {report.overall_grade.value} ({report.overall_score:.0f}/100)")
for metric in report.metrics:
    print(f"  {metric.name}: {metric.grade.value} ({metric.score:.0f}/100)")
```

### Data Retention

```python
# Configure retention policy
tracker.set_retention_config(
    max_age_days=30,              # Delete sessions older than 30 days
    max_sessions=10000,           # Keep max 10k sessions
    exempt_tags=["production"],   # Never delete production sessions
    auto_purge=True,              # Enable automatic cleanup
)

# Preview what would be purged
preview = tracker.purge(dry_run=True)
print(preview["message"])

# Actually purge
result = tracker.purge()
print(f"Purged {result['purged_sessions']} sessions")
```

### Auto-Triage

```python
# One-call unified diagnostics — health, anomalies, drift, errors, and cost analysis
import httpx

resp = httpx.get(f"{endpoint}/api/triage/{session_id}")
report = resp.json()

print(f"Overall: {report['grade']} ({report['score']}/100)")
for finding in report['findings']:
    print(f"  [{finding['severity']}] {finding['category']}: {finding['message']}")
    for fix in finding.get('remediations', []):
        print(f"    → {fix}")
```

### Capacity Planning

```python
from agentlens import CapacityPlanner, WorkloadSample
from datetime import datetime, timedelta

planner = CapacityPlanner()
planner.add_sample(WorkloadSample(
    timestamp=datetime.now() - timedelta(hours=2),
    active_sessions=50, requests_per_minute=120,
    avg_latency_ms=450, token_throughput=8000,
    error_rate=0.02, cpu_utilization=0.65, memory_utilization=0.55
))

projection = planner.project_workload(horizon_hours=72)
sizing = planner.size_resources(target_rpm=500, target_latency_ms=300)
bottlenecks = planner.detect_bottlenecks()
report = planner.report()
```

### Session Guardrails

```python
from agentlens.guardrails import Guardrails

g = (Guardrails("production-budget")
     .max_total_tokens(50_000)
     .max_duration_seconds(300)
     .require_tools(["search", "calculator"])
     .block_tools(["shell_exec"])
     .allow_models(["gpt-4", "gpt-4-turbo"])
     .max_errors(2)
     .add_predicate("no_pii", lambda events: not any("SSN" in str(e) for e in events)))

result = g.validate(session_events)
print(f"Pass: {result.passed}, Violations: {len(result.violations)}")
for v in result.violations:
    print(f"  {v.rule}: {v.message}")
```

### Cost Optimizer

```python
from agentlens.cost_optimizer import CostOptimizer

optimizer = CostOptimizer()
recs = optimizer.analyze(session_events)

print(f"Current: ${recs['current_cost']:.4f} → Optimized: ${recs['optimized_cost']:.4f}")
print(f"Potential savings: {recs['savings_percent']:.0f}%")
for rec in recs['recommendations']:
    print(f"  {rec['current_model']} → {rec['suggested_model']} ({rec['reason']})")
```

### Data Models

| Model | Description |
|-------|-------------|
| `AgentEvent` | A single observable event (LLM call, tool use, decision) |
| `ToolCall` | A tool/function invocation with input and output |
| `DecisionTrace` | The reasoning behind an agent's decision |
| `Session` | A collection of events for one agent run |
| `AlertRule` | A configurable alert rule with metric and threshold |
| `Anomaly` | A detected statistical anomaly in session metrics |
| `HealthReport` | Graded health assessment of a session (A–F) |
| `CapacityReport` | Fleet capacity assessment with projections and sizing |
| `GuardrailResult` | Constraint validation result with pass/fail and violations |
| `TriageReport` | Unified diagnostics with prioritized findings and remediations |
| `WorkloadSample` | Historical workload data point for capacity planning |

## 📊 Dashboard

The dashboard provides a real-time view of your agent sessions:

- **Sessions List** — Filter by status (active, completed, error)
- **Session Comparison** — Select two sessions and compare side-by-side with visual diffs
- **Analytics Overview** — Click 📈 Analytics to see aggregate stats, model usage, hourly activity, and top agents
- **Timeline View** — Interactive timeline of every event in a session
- **Token Charts** — Per-event and cumulative token usage visualization
- **Explain Tab** — Human-readable behavior summaries
- **Costs Tab** — Per-event and per-model cost breakdowns, cumulative cost chart, configurable model pricing
- **Cost Forecast** — Budget projections with what-if simulator and model breakdown
- **Agent Scorecards** — Per-agent performance grading with composite scores, letter grades, and sparkline trends
- **Token Heatmap** — Calendar-style visualization of daily token consumption
- **Trace Waterfall** — Gantt-style visualization of event timing within a session
- **Session Diff Viewer** — Side-by-side comparison of two sessions with event-level diffs
- **Error Analytics** — Error grouping by type, agent, and model with trends
- **SLA Compliance** — Compliance rings, violation alerts, and history charts

The dashboard is a lightweight HTML/CSS/JS app served directly by the backend — no build step required.

## 🔌 API Endpoints

The backend exposes a comprehensive REST API with **90+ endpoints** across 21 route groups:

| Route Group | Endpoints | Description |
|-------------|-----------|-------------|
| **Sessions** | 8 | CRUD, search, explain, export, compare |
| **Events** | 1 | Batch event ingestion (up to 500/call) |
| **Analytics** | 4 | Aggregate stats, performance, heatmaps, cache |
| **Pricing & Costs** | 4 | Model pricing config, per-session cost calculation |
| **Alerts** | 8 | Alert rules CRUD, evaluation, acknowledgment |
| **Webhooks** | 6 | Webhook CRUD, test delivery, delivery history |
| **Correlations** | 10 | Correlation rules, groups, event correlations |
| **Correlation Scheduler** | 6 | SSE stream, schedule management, scheduler control |
| **Tags** | 5 | Session tagging, tag-based filtering |
| **Bookmarks** | 4 | Session bookmarking |
| **Annotations** | 5 | Timestamped notes on sessions and events |
| **Baselines** | 5 | Agent performance baselines and drift detection |
| **Error Analysis** | 5 | Error grouping by type, agent, model with trends |
| **Dependencies** | 5 | Service dependency graph, co-occurrence, critical paths |
| **Leaderboard** | 1 | Agent performance ranking |
| **Postmortem** | 2 | Incident report generation and candidate listing |
| **Retention** | 4 | Retention config, stats, manual purge |
| **Triage** | 3 | Auto-triage engine, finding details, remediation suggestions |
| **Profiler** | 4 | Session profiling, flamegraph data, bottleneck analysis |
| **Replay** | 4 | Session replay, step-by-step playback, event filtering |
| **Scorecards** | 3 | Per-agent performance grading with composite scores |
| **Forecast** | 4 | Cost forecasting, budget projections, what-if simulation |
| **Health** | 1 | Health check |

> 📖 **Full API reference with request/response examples:** [docs/API.md](docs/API.md)

## 🛠️ Tech Stack

- **Python SDK**: Pydantic for data validation, httpx for async HTTP, 70+ modules
- **Backend**: Express.js with better-sqlite3 for zero-config persistence, 90+ API endpoints
- **Dashboard**: Vanilla JS with Canvas-based charts (no framework dependencies)
- **CLI**: 50+ subcommands — triage, capacity, optimize, replay, sla, forecast, and more
- **Database**: SQLite (embedded, no external DB setup needed)

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests: `cd sdk && pytest`
5. Commit (`git commit -m 'Add amazing feature'`)
6. Push (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Setup

```bash
# Backend (with auto-reload)
cd backend && npm install && node server.js

# SDK (editable install with dev deps)
cd sdk && pip install -e ".[dev]"

# Run SDK tests
cd sdk && pytest
```

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by [Saurav Bhattacharya](https://github.com/sauravbhattacharya001)**

*Because if you can't see what your agents are doing, you can't trust them.*

</div>
