# AgentLens Python SDK

Lightweight Python SDK for tracking AI agent behavior with full observability.

## Installation

```bash
pip install agentlens
```

Or install from source for development:

```bash
cd sdk
pip install -e ".[dev]"
```

## Quick Start

```python
import agentlens

# Initialize
agentlens.init(api_key="your-key", endpoint="http://localhost:3000")

# Start a session
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

# Track tool calls
agentlens.track(
    event_type="tool_call",
    tool_name="calculator",
    tool_input={"expression": "2+2"},
    tool_output={"result": "4"},
)

# Get explanation
print(agentlens.explain())

# End session
agentlens.end_session()
```

## Decorators

```python
from agentlens import track_agent, track_tool_call

@track_agent(model="gpt-4")
def my_agent(prompt):
    return call_llm(prompt)

@track_agent  # model is optional
async def async_agent(prompt):
    return await call_llm_async(prompt)

@track_tool_call(tool_name="web_search")
def search(query):
    return do_search(query)

@track_tool_call  # tool_name defaults to function name
def calculator(expression):
    return eval(expression)
```

Both decorators support sync and async functions. On success, they emit an `agent_call` / `tool_call` event; on exception, they emit `agent_error` / `tool_error` and re-raise.

## Session Export

Export a session's full data for offline analysis or archival:

```python
# JSON export (returns a dict)
data = agentlens.export_session(format="json")
print(data["summary"]["total_tokens"])

# CSV export (returns a string)
csv_text = agentlens.export_session(format="csv")
with open("session.csv", "w") as f:
    f.write(csv_text)

# Export a specific session (not the current one)
data = agentlens.export_session(session_id="abc123", format="json")
```

The JSON export includes the session metadata, all events, and a summary with total tokens, models used, event types, and total duration.

## Session Comparison

Compare two sessions side-by-side to evaluate prompt changes, model swaps, or configuration tweaks:

```python
result = agentlens.compare_sessions(
    session_a="baseline-session-id",
    session_b="experiment-session-id",
)

# Token usage delta
print(result["deltas"]["total_tokens"])
# {"absolute": -200, "percent": -12.9}

# Per-model breakdown for each session
print(result["session_a"]["models"])
print(result["session_b"]["models"])

# Shared tools and event types
print(result["shared"]["tools"])
```

Deltas are computed as **B relative to A** — negative values mean session B used fewer resources.

## Models

| Model | Description |
|-------|-------------|
| `AgentEvent` | A single observable event (LLM call, tool call, decision, error) |
| `ToolCall` | A tool/function invocation with input, output, and timing |
| `DecisionTrace` | Reasoning behind a decision, with step number and confidence |
| `Session` | A collection of events for one agent run, with token aggregation |

## API Reference

### `agentlens.init(api_key, endpoint)`

Initialize the SDK. Must be called before any other function. Safe to call multiple times (previous transport is closed automatically).

### `agentlens.start_session(agent_name, metadata=None)`

Start a new tracking session. Returns a `Session` object.

### `agentlens.end_session(session_id=None)`

End the current (or specified) session and flush all buffered events to the backend.

### `agentlens.track(...)`

Track a single event. Key parameters:
- `event_type` — `"llm_call"`, `"tool_call"`, `"decision"`, `"generic"`, etc.
- `input_data` / `output_data` — Arbitrary dicts
- `model` — LLM model name
- `tokens_in` / `tokens_out` — Token counts
- `reasoning` — Human-readable reasoning (creates a `DecisionTrace`)
- `tool_name` / `tool_input` / `tool_output` — Tool call details
- `duration_ms` — Execution time

### `agentlens.explain(session_id=None)`

Get a Markdown-formatted explanation of the agent's behavior in the current or specified session.

### `agentlens.export_session(session_id=None, format="json")`

Export session data. Returns a dict (JSON) or string (CSV).

### `agentlens.compare_sessions(session_a, session_b)`

Compare two sessions. Returns metrics, deltas, and shared breakdowns.

## Cost Tracking

Built-in cost estimation with configurable per-model pricing.

```python
# Get cost breakdown for the current session
costs = agentlens.get_costs()
print(f"Total: ${costs['total_cost']:.4f}")
print(f"Input: ${costs['total_input_cost']:.4f}")
print(f"Output: ${costs['total_output_cost']:.4f}")

# Per-model breakdown
for model, breakdown in costs["model_costs"].items():
    print(f"  {model}: ${breakdown['cost']:.4f}")

# Get costs for a specific session
costs = agentlens.get_costs(session_id="abc123")

# View current pricing configuration
pricing = agentlens.get_pricing()
print(pricing["defaults"])  # Built-in model prices

# Override pricing for custom/fine-tuned models
agentlens.set_pricing({
    "my-custom-model": {
        "input_cost_per_1m": 5.00,
        "output_cost_per_1m": 15.00,
    }
})
```

Cost tracking uses per-1M-token pricing. Built-in defaults cover GPT-4, GPT-3.5, Claude, and other common models. Unrecognized models are listed in `costs["unmatched_models"]`.

## Alerts

Real-time alerting on agent metrics with configurable rules, severity levels, and callbacks.

```python
from agentlens import AlertRule, AlertManager, Severity, Condition

# Define alert rules
rules = [
    AlertRule(
        name="high-error-rate",
        metric="error_rate",
        condition=Condition.GREATER_THAN,
        threshold=0.1,
        severity=Severity.CRITICAL,
    ),
    AlertRule(
        name="slow-responses",
        metric="p95_latency_ms",
        condition=Condition.GREATER_THAN,
        threshold=5000,
        severity=Severity.WARNING,
    ),
]

# Create alert manager (window_seconds controls the evaluation window)
manager = AlertManager(rules=rules, default_window=300)

# Register a callback
def on_alert(alert):
    print(f"[{alert.severity}] {alert.rule_name}: {alert.message}")

manager.on_alert(on_alert)

# Feed events — alerts fire automatically when thresholds are breached
manager.record(event)
alerts = manager.evaluate()

# Manage rules dynamically
manager.add_rule(new_rule)
manager.remove_rule("high-error-rate")
```

### Severity Levels

| Level | Use Case |
|-------|----------|
| `Severity.INFO` | Informational threshold crossed |
| `Severity.WARNING` | Degraded performance |
| `Severity.CRITICAL` | Failure requiring immediate attention |

### Available Conditions

`GREATER_THAN`, `LESS_THAN`, `GREATER_THAN_OR_EQUAL`, `LESS_THAN_OR_EQUAL`, `EQUAL`

### MetricAggregator

The `MetricAggregator` computes metrics over a sliding time window:

```python
from agentlens import MetricAggregator

agg = MetricAggregator(window_seconds=300)
agg.record(event_dict)

# Available metrics: error_rate, avg_latency_ms, p95_latency_ms,
# total_tokens, avg_tokens, tool_error_rate, events_per_minute
value = agg.get_metric("error_rate")
value = agg.get_metric("avg_latency_ms", agent_filter="my-agent")
```

## Health Scoring

Score agent sessions on 6 dimensions with letter grades and actionable recommendations.

```python
from agentlens import HealthScorer, HealthThresholds

# Use default thresholds or customize
scorer = HealthScorer()

# Score a list of events
report = scorer.score(events, session_id="session-123")
print(report.render())  # Human-readable report

# Or score a Session object directly
report = scorer.score_session(session)

# Inspect results
print(f"Grade: {report.grade.value}")   # A, B, C, D, F
print(f"Score: {report.overall_score}") # 0-100
for metric in report.metrics:
    print(f"  {metric.name}: {metric.score}/100 ({metric.grade.value})")

# Get recommendations
for rec in report.recommendations:
    print(f"  → {rec}")

# Custom thresholds
thresholds = HealthThresholds(
    error_rate_warn=0.05,
    error_rate_critical=0.15,
    latency_warn_ms=2000,
    latency_critical_ms=8000,
)
scorer = HealthScorer(thresholds=thresholds)
```

### Scored Dimensions

| Metric | What It Measures |
|--------|-----------------|
| Error Rate | Fraction of events that are errors |
| Avg Latency | Mean response time across events |
| P95 Latency | 95th percentile latency |
| Tool Success | Ratio of successful tool calls |
| Token Efficiency | Tokens used relative to event count |
| Event Volume | Whether event count is in a healthy range |

### Health Grades

| Grade | Score Range |
|-------|------------|
| A | 90-100 |
| B | 80-89 |
| C | 70-79 |
| D | 60-69 |
| F | 0-59 |

## Anomaly Detection

Statistical anomaly detection using z-scores and configurable baselines.

```python
from agentlens import AnomalyDetector, AnomalyDetectorConfig

# Configure detection sensitivity
config = AnomalyDetectorConfig(
    z_score_threshold=2.5,        # Standard deviations for anomaly
    min_baseline_events=10,       # Minimum events before detection starts
    latency_weight=1.0,           # Weight for latency anomalies
    token_weight=1.0,             # Weight for token anomalies
    error_weight=1.5,             # Weight for error anomalies
)

detector = AnomalyDetector(config=config)

# Build a baseline from historical events
detector.train(historical_events)

# Analyze new events for anomalies
report = detector.analyze(new_events)

print(f"Anomalies found: {report.anomaly_count}")
print(f"Has anomalies: {report.has_anomalies}")
print(f"Max severity: {report.max_severity}")
print(report.summary())

# Inspect individual anomalies
for anomaly in report.anomalies:
    print(f"  [{anomaly.severity.label}] {anomaly.kind.value}: {anomaly.message}")
    print(f"    z-score: {anomaly.z_score:.2f}")

# Group by kind or severity
by_kind = report.by_kind()       # dict[AnomalyKind, list[Anomaly]]
by_sev = report.by_severity()    # dict[AnomalySeverity, list[Anomaly]]
critical = report.critical_count()
warnings = report.warning_count()

# Export to dict for logging/storage
data = report.to_dict()
```

### Anomaly Kinds

| Kind | Description |
|------|-------------|
| `LATENCY_SPIKE` | Response time significantly above baseline |
| `TOKEN_SPIKE` | Token usage significantly above baseline |
| `ERROR_BURST` | Error rate significantly above baseline |
| `LATENCY_DROP` | Unusually fast responses (possible short-circuits) |
| `TOKEN_DROP` | Unusually low token usage |

### Anomaly Severities

| Severity | z-score Range |
|----------|--------------|
| `LOW` | 2.0-2.5σ |
| `MEDIUM` | 2.5-3.0σ |
| `HIGH` | 3.0-4.0σ |
| `CRITICAL` | > 4.0σ |
