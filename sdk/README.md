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

## A/B Testing

Run controlled experiments comparing models, prompts, or configurations with statistical significance testing.

```python
from agentlens.ab_test import ABTestAnalyzer, SignificanceLevel

analyzer = ABTestAnalyzer()

# Create an experiment
exp = analyzer.create_experiment(
    "gpt4-vs-claude",
    hypothesis="GPT-4 has lower latency",
)
exp.add_variant("gpt4", description="OpenAI GPT-4")
exp.add_variant("claude", description="Anthropic Claude", is_control=True)

# Record observations
for latency in [230, 210, 250, 220, 240]:
    exp.record("gpt4", metric="latency_ms", value=latency)
for latency in [310, 290, 330, 300, 320]:
    exp.record("claude", metric="latency_ms", value=latency)

# Analyze results (Welch's t-test)
result = analyzer.analyze("gpt4-vs-claude", metric="latency_ms")
print(f"Winner: {result.winner}")        # "gpt4"
print(f"p-value: {result.p_value:.4f}")
print(f"Significant: {result.significant}")
print(f"Effect size: {result.effect_size}")  # Cohen's d interpretation

# Check sample size requirements
n = analyzer.required_sample_size(
    effect_size=0.5,
    alpha=0.05,
    power=0.8,
)

# Full experiment report
report = analyzer.report("gpt4-vs-claude")
```

### Statistical Methods

| Method | Description |
|--------|-------------|
| Welch's t-test | Default significance test (unequal variances) |
| Mann-Whitney U | Non-parametric alternative |
| Cohen's d | Effect size measurement |

### Effect Size Interpretation

| Classification | Cohen's d |
|---------------|-----------|
| Negligible | < 0.2 |
| Small | 0.2-0.5 |
| Medium | 0.5-0.8 |
| Large | 0.8-1.2 |
| Very Large | > 1.2 |

## Capacity Planning

Fleet capacity planning for AI agent deployments — predict resource needs, detect bottlenecks, and generate scaling recommendations.

```python
from agentlens.capacity import CapacityPlanner, WorkloadSample

planner = CapacityPlanner()

# Add workload samples (tokens/s, requests/s, etc.)
planner.add_samples([
    WorkloadSample(timestamp=t, tokens_per_sec=1200, requests_per_sec=15)
    for t in timestamps
])

# Detect bottlenecks
bottlenecks = planner.detect_bottlenecks()
for b in bottlenecks:
    print(f"  [{b.severity}] {b.resource}: {b.message}")

# Project future workload
projection = planner.project_workload(horizon_hours=24)
print(f"Projected peak: {projection.peak_tokens_per_sec} tok/s")

# Get scaling recommendations
report = planner.plan()
for rec in report.recommendations:
    print(f"  {rec.action}: {rec.reason}")

# Current utilization
util = planner.current_utilization()
peak = planner.peak_utilization()
```

### Resource Kinds

Tracks compute, memory, throughput, and concurrency resources. Bottleneck severities: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

## Cost Optimizer

Intelligent model selection — analyze task complexity and recommend cheaper model alternatives where premium models are overkill.

```python
from agentlens.cost_optimizer import CostOptimizer

optimizer = CostOptimizer()

# Analyze a session's events for optimization opportunities
report = optimizer.analyze(events)
print(f"Potential savings: {report.total_savings_pct:.1f}%")

for rec in report.recommendations:
    print(f"  Event {rec.event_id}: {rec.current_model} → {rec.suggested_model}")
    print(f"    Complexity: {rec.complexity.level.value}")
    print(f"    Savings: ${rec.savings:.4f}")

# Quick estimate for a single model switch
estimate = optimizer.quick_estimate(
    current_model="gpt-4o",
    tokens_in=1000,
    tokens_out=500,
)

# Get model suggestion based on task complexity
suggested = optimizer.suggest_model(complexity_level="simple")

# Register custom models
optimizer.register_model("my-model", tier="standard", cost_per_1m_in=1.0, cost_per_1m_out=3.0)

# Session-level analysis
report = optimizer.analyze_session_events(session_events)
```

### Model Tiers

| Tier | Examples | Use Case |
|------|----------|----------|
| Economy | GPT-3.5 Turbo, Claude Haiku | Simple tasks, classification |
| Standard | GPT-4o-mini | Moderate complexity |
| Premium | GPT-4o, Claude Sonnet | Complex reasoning |
| Flagship | GPT-4, Claude Opus | Maximum capability |

### Complexity Levels

| Level | Characteristics |
|-------|----------------|
| Simple | Low token count, no tool calls, short responses |
| Moderate | Medium tokens, some tool usage |
| Complex | High tokens, multiple tools, long chains |
| Critical | Error-prone, requires highest reliability |

## Prompt Version Tracker

Track prompt template evolution over time and correlate changes with performance metrics.

```python
from agentlens import PromptVersionTracker

tracker = PromptVersionTracker()

# Register prompt versions (auto-increments version numbers)
v1 = tracker.register("summarizer", "Summarize the following text: {text}")
v2 = tracker.register(
    "summarizer",
    "You are a concise summarizer. Summarize: {text}",
    tags=["concise"],
)

# Record performance outcomes for a version
tracker.record_outcome(v2.version_id, tokens=450, latency_ms=1200, quality_score=0.92)
tracker.record_outcome(v2.version_id, tokens=380, latency_ms=1050, quality_score=0.88)

# Diff two versions (unified diff format)
diff = tracker.diff("summarizer", v1.version_number, v2.version_number)
print(diff.diff_text)
print(f"Change kind: {diff.kind.value}")  # modified, added, removed

# Get the best performing version by quality score
report = tracker.report("summarizer")
print(f"Best version: v{report.best_version.version_number}")
print(f"Avg quality: {report.best_version.stats.avg_quality:.2f}")

# List all versions
versions = tracker.get_versions("summarizer")

# Export full history
data = tracker.export_json()
```

### Tracked Metrics Per Version

| Metric | Description |
|--------|-------------|
| `avg_tokens` | Average token usage |
| `avg_latency_ms` | Average response latency |
| `avg_quality` | Average quality score (0-1) |
| `outcome_count` | Number of recorded outcomes |

## Rate Limiter

Sliding-window rate limiting for LLM API calls — stay within provider limits and avoid 429 errors.

```python
from agentlens import RateLimiter, RateLimit, RateLimitPolicy

# Define rate limits (per-resource, per-window)
policy = RateLimitPolicy(limits=[
    RateLimit(resource="requests", limit=60, window_seconds=60),
    RateLimit(resource="tokens", limit=90_000, window_seconds=60),
    RateLimit(resource="tokens", limit=1_000_000, window_seconds=3600),
])

limiter = RateLimiter(policy)

# Check before making a call
result = limiter.check("tokens", estimated=1500)
if not result.allowed:
    print(f"Rate limited! Retry after {result.retry_after_ms}ms")
else:
    # Make the call, then record actual usage
    limiter.record("tokens", actual_tokens_used)
    limiter.record("requests", 1)

# Get utilization report
report = limiter.report()
for resource_report in report.resources:
    print(f"  {resource_report.resource}: {resource_report.utilization:.0%}")

# Use built-in provider policies
from agentlens.rate_limiter import openai_tier1_policy, anthropic_tier1_policy
limiter = RateLimiter(openai_tier1_policy())
```

### Rate Limit Actions

| Action | Behavior |
|--------|----------|
| `WARN` | Log a warning but allow the request |
| `BLOCK` | Reject the request until capacity frees up |

## Session Replayer

Step-by-step session replay for debugging agent runs — reconstruct timing, add breakpoints, filter events.

```python
from agentlens.replayer import SessionReplayer

replayer = SessionReplayer(session)

# Control replay speed
replayer.set_speed(2.0)  # 2x speed

# Filter to specific event types
replayer.add_filter("llm_call", "tool_call")

# Add breakpoints (pause on matching events)
replayer.add_breakpoint(lambda e: e.event_type == "agent_error")

# Play through the session
for frame in replayer.play():
    print(f"[{frame.progress_pct:.0f}%] {frame.event.event_type}")
    print(f"  Delay: {frame.wall_delay_ms:.0f}ms")
    if frame.is_breakpoint:
        input("Breakpoint hit — press Enter to continue")

# Export replay
text_output = replayer.to_text()
json_output = replayer.to_json()

# Replay statistics
stats = replayer.stats
print(f"Total frames: {stats.total_frames}")
print(f"Duration: {stats.total_duration_ms}ms")
```

### Replay Frame

Each frame contains:
- `index` / `total` — position in the replay
- `event` — the original `AgentEvent`
- `wall_delay_ms` — speed-adjusted delay since previous frame
- `elapsed_ms` — cumulative time in original timeline
- `is_breakpoint` — whether a breakpoint was triggered
- `annotations` — any attached notes

## Session Exporter

Offline export to JSON, CSV, and standalone HTML reports.

```python
from agentlens.exporter import SessionExporter

exporter = SessionExporter(session, events)

# Export as JSON (returns dict)
data = exporter.as_json()

# Write JSON to file
exporter.to_json("session_export.json")

# Export as CSV (returns string)
csv_text = exporter.as_csv()
exporter.to_csv("session_export.csv")

# Export as standalone HTML report
html = exporter.as_html()
exporter.to_html("session_report.html")
```

The HTML export creates a self-contained report with embedded CSS — no external dependencies needed. Includes session metadata, event timeline, and summary statistics.

## Postmortem Generator

Automated incident postmortem reports for sessions that experienced errors — root cause analysis, timeline, impact assessment, and remediation suggestions.

```python
from agentlens.postmortem import PostmortemGenerator, PostmortemConfig

# Configure analysis depth
config = PostmortemConfig(
    min_error_count=1,           # Minimum errors to trigger postmortem
    include_recommendations=True,
    include_timeline=True,
)

generator = PostmortemGenerator(config=config)

# Generate a postmortem from a session's events
report = generator.generate(events, session_id="session-123")

# Render as Markdown
print(report.to_markdown())

# Access structured data
print(f"Root cause: {report.root_cause.description}")
print(f"Impact: {report.impact.severity}")
for phase in report.timeline:
    print(f"  [{phase.timestamp}] {phase.description}")
for remediation in report.remediations:
    print(f"  → [{remediation.category.value}] {remediation.description}")
for lesson in report.lessons_learned:
    print(f"  💡 {lesson.insight}")

# Export to dict
data = report.to_dict()
```

### Remediation Categories

| Category | Description |
|----------|-------------|
| Prompt Engineering | Improve prompt templates |
| Model Selection | Switch to more appropriate model |
| Tool Configuration | Fix tool setup or parameters |
| Rate Limiting | Adjust rate limits or retry logic |
| Monitoring | Add alerts or observability |
| Architecture | Structural agent design changes |

### Incident Phases

Postmortem timelines are broken into phases: `DETECTION`, `INVESTIGATION`, `MITIGATION`, `RESOLUTION`, `POST_INCIDENT`.
