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

## Token Budgets

Enforce per-session or per-agent token and cost budgets with threshold callbacks.

```python
from agentlens import BudgetTracker, TokenBudget, BudgetStatus

tracker = BudgetTracker()

# Create a budget with token and/or cost caps
budget = tracker.create_budget(
    budget_id="agent-session-1",
    max_tokens=50000,
    max_cost=2.50,
    warn_threshold=0.8,   # Callback fires at 80% utilization
)

# Receive threshold notifications
def on_threshold(budget: TokenBudget, status: BudgetStatus):
    print(f"Budget '{budget.budget_id}' → {status.value}")

tracker.on_threshold(on_threshold)

# Record token usage (auto-checks budget)
tracker.record(
    budget_id="agent-session-1",
    tokens_in=1200,
    tokens_out=800,
    model="gpt-4",
)

# Check utilization
print(f"Used: {budget.utilization:.0%}")
print(f"Remaining tokens: {budget.remaining_tokens}")
print(f"Status: {budget.status.value}")

# Get a detailed report
report = tracker.report("agent-session-1")
print(report.summary())

# Link budgets to sessions
tracker.record_for_session(
    session_id="sess-abc",
    tokens_in=500,
    tokens_out=200,
    model="gpt-4",
)
session_report = tracker.report_for_session("sess-abc")
```

### Budget Statuses

| Status | Meaning |
|--------|---------|
| `OK` | Under warning threshold |
| `WARNING` | Approaching limit (≥ warn_threshold) |
| `EXCEEDED` | Over budget (raises `BudgetExceededError` when `enforce=True`) |

## Cost Forecasting

Predict future AI spending from historical usage with linear regression and EMA models.

```python
from agentlens import CostForecaster, UsageRecord
from datetime import datetime

forecaster = CostForecaster()

# Feed historical usage records
forecaster.add_records([
    UsageRecord(timestamp=datetime(2025, 1, d), tokens=1000 * d, cost=0.05 * d, model="gpt-4")
    for d in range(1, 31)
])

# Forecast the next 7 days
forecast = forecaster.forecast_daily(days=7, method="auto")
print(f"Predicted total cost: ${forecast.total_predicted_cost:.2f}")

for pred in forecast.predictions:
    print(f"  {pred.date}: ${pred.predicted_cost:.2f} "
          f"(±${pred.upper_bound - pred.predicted_cost:.2f})")

# Get spending summary
summary = forecaster.spending_summary()
print(f"Daily avg: ${summary.daily_average:.2f}")
print(f"Monthly projection: ${summary.monthly_projection:.2f}")
print(f"Trend: {summary.trend_direction}")

# Budget alerts
alerts = forecaster.budget_alerts(monthly_budget=100.0)
for alert in alerts:
    print(f"  [{alert.severity}] {alert.message}")
```

### Forecast Methods

| Method | Description |
|--------|-------------|
| `"linear"` | Linear regression on daily costs |
| `"ema"` | Exponential moving average |
| `"auto"` | Linear if ≥7 days of data, EMA otherwise |

## Compliance Checking

Validate agent behavior against configurable compliance policies.

```python
from agentlens import ComplianceChecker, CompliancePolicy, ComplianceRule, RuleKind

# Define rules
rules = [
    ComplianceRule(
        name="max-tokens-per-call",
        kind=RuleKind.MAX_TOKENS,
        threshold=4000,
        description="No single LLM call should exceed 4000 tokens",
    ),
    ComplianceRule(
        name="require-reasoning",
        kind=RuleKind.REQUIRE_REASONING,
        description="All decisions must include reasoning traces",
    ),
    ComplianceRule(
        name="max-error-rate",
        kind=RuleKind.MAX_ERROR_RATE,
        threshold=0.05,
        description="Error rate must stay below 5%",
    ),
]

policy = CompliancePolicy(name="production-policy", rules=rules)

# Or use built-in policies
from agentlens import strict_policy, permissive_policy
policy = strict_policy()

# Check compliance
checker = ComplianceChecker()
report = checker.check(events, policy)

print(f"Compliant: {report.compliant}")
print(f"Passed: {report.passed}/{report.total_rules}")
print(report.render())

# Inspect failures
for result in report.errors():
    print(f"  FAIL: {result.rule.name} — {result.message}")
```

### Rule Kinds

| Kind | What It Checks |
|------|----------------|
| `MAX_TOKENS` | Single-call token limit |
| `MAX_TOTAL_TOKENS` | Session-wide token limit |
| `MAX_ERROR_RATE` | Error rate threshold |
| `MAX_LATENCY` | Per-call latency ceiling |
| `REQUIRE_REASONING` | Decisions must have traces |
| `MAX_TOOL_ERRORS` | Tool failure count limit |
| `MIN_EVENTS` | Minimum event count |
| `MAX_EVENTS` | Maximum event count |
| `REQUIRE_SESSION_END` | Session must be properly closed |

## Drift Detection

Detect behavioral drift between baseline and current agent sessions.

```python
from agentlens import DriftDetector

detector = DriftDetector(drift_threshold=2.0)

# Add baseline sessions (known-good behavior)
for session in baseline_sessions:
    detector.add_baseline(session)

# Add current sessions to compare
for session in current_sessions:
    detector.add_current(session)

# Run detection
report = detector.detect()
print(report.format_report())

# Or compare two lists directly
report = DriftDetector.compare(
    baseline=baseline_sessions,
    current=current_sessions,
    drift_threshold=2.0,
)

# Inspect metric drifts
for drift in report.metric_drifts:
    print(f"  {drift.metric}: {drift.baseline_mean:.2f} → {drift.current_mean:.2f} "
          f"({drift.status.label}, {drift.direction.value})")

# Inspect tool usage changes
for tool_drift in report.tool_drifts:
    print(f"  {tool_drift.tool}: baseline={tool_drift.baseline_count}, "
          f"current={tool_drift.current_count}")
```

### Drift Statuses

| Status | Meaning |
|--------|---------|
| `STABLE` | Within normal variation |
| `DRIFT` | Statistically significant change |
| `INSUFFICIENT_DATA` | Not enough data to determine |

### Tracked Metrics

Latency (mean), token usage (mean), error rate, and per-tool usage frequency.

## SLA Evaluation

Evaluate agent sessions against Service Level Objectives (SLOs).

```python
from agentlens import SLAEvaluator, SLObjective, SLAPolicy

# Define objectives using factory methods
policy = SLAPolicy(
    name="production-sla",
    objectives=[
        SLObjective.latency_p95(target_ms=3000, slo_percent=99.0),
        SLObjective.error_rate(target_rate=0.01, slo_percent=99.5),
        SLObjective.token_budget(target_per_session=10000, slo_percent=95.0),
        SLObjective.tool_success_rate(target_rate=0.95),
        SLObjective.throughput(min_events=5, slo_percent=95.0),
    ],
)

# Or use built-in policies
from agentlens import production_policy, development_policy
policy = production_policy()

# Evaluate
evaluator = SLAEvaluator()
report = evaluator.evaluate(sessions, policy)

print(report.render())

# Inspect per-objective results
for result in report.results:
    print(f"  {result.objective.name}: {result.compliance_rate:.1%} "
          f"(target: {result.objective.slo_percent}%) — {result.status.value}")
    if result.violations:
        print(f"    {result.violation_count} violations")
```

### Objective Kinds

| Kind | Factory Method | What It Measures |
|------|---------------|-----------------|
| `LATENCY_P95` | `SLObjective.latency_p95()` | 95th percentile latency |
| `LATENCY_AVG` | `SLObjective.latency_avg()` | Average latency |
| `ERROR_RATE` | `SLObjective.error_rate()` | Fraction of error events |
| `TOKEN_BUDGET` | `SLObjective.token_budget()` | Mean tokens per session |
| `TOOL_SUCCESS` | `SLObjective.tool_success_rate()` | Tool call success ratio |
| `THROUGHPUT` | `SLObjective.throughput()` | Minimum events per session |

## Sampling Strategies

Control which events get sent to the backend to reduce costs and noise.

```python
from agentlens import (
    ProbabilisticSampler,
    RateLimitSampler,
    PrioritySampler,
    TailSampler,
    CompositeSampler,
    AlwaysSampler,
    NeverSampler,
    TraceContext,
)

# Sample 20% of events randomly
sampler = ProbabilisticSampler(sample_rate=0.2)

# Rate-limit to 100 events per minute
sampler = RateLimitSampler(max_per_second=100/60)

# Always sample errors and high-latency events
sampler = PrioritySampler(
    priority_event_types={"agent_error", "tool_error"},
    priority_rate=1.0,       # 100% of priority events
    default_rate=0.1,        # 10% of everything else
)

# Retroactively sample when errors occur (tail-based)
sampler = TailSampler(
    default_rate=0.05,
    error_rate=1.0,
    latency_threshold_ms=5000,
    high_latency_rate=1.0,
)

# Combine multiple strategies (all must agree)
sampler = CompositeSampler(samplers=[
    ProbabilisticSampler(sample_rate=0.5),
    PrioritySampler(priority_event_types={"agent_error"}),
])

# Make a sampling decision
ctx = TraceContext(trace_id="trace-1", event_type="llm_call")
decision = sampler.should_sample(ctx)
if decision.sampled:
    send_event(event)

# Check sampler statistics
stats = sampler.stats()
print(f"Sampled: {stats.sampled}/{stats.total} ({stats.sample_rate:.1%})")
```

## Timeline Visualization

Render agent session timelines as text, Markdown, or HTML.

```python
from agentlens import TimelineRenderer

renderer = TimelineRenderer(events=session_events, session=session_dict)

# Text timeline (for terminals)
print(renderer.render_text(width=80, show_tokens=True))

# Markdown timeline (for docs/notebooks)
md = renderer.render_markdown(show_reasoning=True)

# HTML timeline (for dashboards)
html = renderer.render_html(
    title="Agent Session Timeline",
    show_tokens=True,
    show_reasoning=True,
)
renderer.save("timeline.html")

# Analysis helpers
summary = renderer.get_summary()
print(f"Total events: {summary['total_events']}")
print(f"Duration: {summary['total_duration_ms']}ms")
print(f"Errors: {summary['error_count']}")

critical_path = renderer.get_critical_path()
slowest = renderer.get_slowest_events(n=3)
errors = renderer.get_error_events()

# Filter events
filtered = renderer.filter(event_types=["llm_call"], min_duration_ms=100)
```

## Spans

Structured tracing spans for fine-grained operation tracking.

```python
from agentlens import Span

# Create a span for an operation
span = Span(name="retrieve-context", kind="internal")
span.set_attribute("query", "latest sales data")
span.set_attribute("num_results", 5)

# ... do work ...

span.set_status("ok")
span_dict = span.to_dict()
# {"name": "retrieve-context", "kind": "internal", "status": "ok",
#  "attributes": {"query": "latest sales data", "num_results": 5}, ...}
```

Spans can be nested and attached to events for distributed tracing context.
