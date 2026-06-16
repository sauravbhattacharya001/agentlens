# AgentLens Python SDK

Lightweight Python SDK for tracing AI agent runs — capture every model call and
tool step, ship it to a self-hosted collector, then replay, explain, and score
the session.

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

# Initialize (points at your AgentLens backend).
agentlens.init(api_key="your-key", endpoint="http://localhost:3000")

# Start a session.
session = agentlens.start_session(agent_name="my-agent")

# Track an LLM call.
agentlens.track(
    event_type="llm_call",
    input_data={"prompt": "What is 2+2?"},
    output_data={"response": "4"},
    model="gpt-4",
    tokens_in=12,
    tokens_out=3,
    reasoning="Simple arithmetic question, answered directly",
)

# Track a tool call.
agentlens.track(
    event_type="tool_call",
    tool_name="calculator",
    tool_input={"expression": "2+2"},
    tool_output={"result": "4"},
)

# Get a plain-language explanation.
print(agentlens.explain())

# End the session (flushes buffered events to the backend).
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

@track_tool_call  # tool_name defaults to the function name
def calculator(expression):
    return eval(expression)
```

Both decorators support sync and async functions. On success they emit an
`agent_call` / `tool_call` event; on exception they emit `agent_error` /
`tool_error` and re-raise.

## Session Export

```python
# JSON export (returns a dict).
data = agentlens.export_session(format="json")
print(data["summary"]["total_tokens"])

# CSV export (returns a string).
csv_text = agentlens.export_session(format="csv")
with open("session.csv", "w") as f:
    f.write(csv_text)

# Export a specific session.
data = agentlens.export_session(session_id="abc123", format="json")
```

The JSON export includes session metadata, all events, and a summary (total
tokens, models used, event types, total duration).

## Transcript Export

Render a tracked session as a Markdown **transcript** conforming to
`transcript-contract@v1`. Every section is backed by recorded evidence (tool
calls, timeline, token usage), so it reflects what the agent *did*, not what it
*says* it did:

```python
md = agentlens.export_transcript()                 # current session
md = agentlens.export_transcript(session_id="abc123")

with open("transcript.md", "w", encoding="utf-8") as f:
    f.write(md)
```

The output includes the standard sections (`## Task`, `## Actions Taken`,
`## Key Outputs`, `## Outcome`, `## Duration`). The `## Outcome` token is derived
from the session status (`completed` -> `pass`, `error`/`failed` -> `fail`,
unfinished -> `IN-PROGRESS`).

### Ground-truth run metadata

`export_transcript` carries the agent's self-reported claim; its companion
`export_run_metadata` extracts the **ground truth** (recorded status and
wall-clock, independent of anything the agent reported):

```python
meta = agentlens.export_run_metadata()             # current session
# -> {"exitStatus": "error", "startedAt": "...", "endedAt": "...", "durationMs": 5520000.0}
```

`RunMetadata` fields map from the session as follows:

| `RunMetadata` field | Source |
| --- | --- |
| `exitStatus` | session status (`completed`->`ok`, `error`/`failed`->`error`, `timeout`->`timeout`, `killed`->`killed`, `active`->`running`) |
| `startedAt` / `endedAt` | recorded wall-clock (ISO-8601) |
| `durationMs` | recorded `duration_ms`, or derived from start/end |

## Session Comparison

```python
result = agentlens.compare_sessions(
    session_a="baseline-session-id",
    session_b="experiment-session-id",
)

print(result["deltas"]["total_tokens"])   # {"absolute": -200, "percent": -12.9}
print(result["session_a"]["models"])
print(result["shared"]["tools"])
```

Deltas are computed as **B relative to A** — negative values mean session B used
fewer resources.

## Cost Tracking

```python
costs = agentlens.get_costs()
print(f"Total:  ${costs['total_cost']:.4f}")
print(f"Input:  ${costs['total_input_cost']:.4f}")
print(f"Output: ${costs['total_output_cost']:.4f}")

for model, breakdown in costs["model_costs"].items():
    print(f"  {model}: ${breakdown['total_cost']:.4f}")

# View current pricing (per 1M tokens).
pricing = agentlens.get_pricing()
print(pricing["defaults"])

# Override pricing for custom / fine-tuned models.
agentlens.set_pricing({
    "my-custom-model": {"input_cost_per_1m": 5.00, "output_cost_per_1m": 15.00},
})
```

Built-in defaults cover common models. Unrecognized models are listed in
`costs["unmatched_models"]`.

## Health Scoring

Grade a session A–F with per-metric breakdowns and recommendations.

```python
from agentlens import HealthScorer, HealthThresholds

scorer = HealthScorer()

# Score raw event dicts...
report = scorer.score(events, session_id="session-123")
# ...or a Session model directly.
report = scorer.score_session(session)

print(report.render())
print(f"Grade: {report.grade.value}")    # A, B, C, D, F
print(f"Score: {report.overall_score}")  # 0-100
for metric in report.metrics:
    print(f"  {metric.name}: {metric.score}/100 ({metric.grade.value})")
for rec in report.recommendations:
    print(f"  -> {rec}")

# Custom thresholds.
scorer = HealthScorer(thresholds=HealthThresholds(
    error_rate_warn=0.05,
    error_rate_critical=0.15,
    latency_warn_ms=2000,
    latency_critical_ms=8000,
))
```

Scored dimensions include error rate, average and P95 latency, tool success
ratio, token efficiency, and event volume.

## Timeline Visualization

```python
from agentlens import TimelineRenderer

renderer = TimelineRenderer(events=session_events, session=session_dict)

print(renderer.render_text(width=80, show_tokens=True))   # terminal
md = renderer.render_markdown(show_reasoning=True)          # docs/notebooks
renderer.save("timeline.html")                              # HTML

summary = renderer.get_summary()
print(f"Events: {summary['total_events']}, errors: {summary['error_count']}")
critical_path = renderer.get_critical_path()
slowest = renderer.get_slowest_events(n=3)
```

## Narratives

Generate a human-readable summary of a session.

```python
from agentlens import NarrativeGenerator, NarrativeConfig, NarrativeStyle

gen = NarrativeGenerator()
narrative = gen.generate(session, config=NarrativeConfig(style=NarrativeStyle.DETAILED))
print(narrative.to_markdown())

# Compare two sessions in prose.
print(gen.compare(session_a, session_b))
```

## Session Replayer

Step through a recorded session frame by frame.

```python
from agentlens import SessionReplayer

replayer = SessionReplayer(session, speed=2.0)   # 2x speed

for frame in replayer.play():
    print(f"[{frame.index}/{frame.total}] {frame.event.event_type}")
    print(f"  delay: {frame.wall_delay_ms:.0f}ms")

text_output = replayer.to_text()
json_output = replayer.to_json()
```

Each `ReplayFrame` carries `index` / `total`, the original `event`,
`wall_delay_ms` (speed-adjusted), and cumulative `elapsed_ms`.

## Flamegraph

Turn a session into a flamegraph to see where time went.

```python
from agentlens import Flamegraph, flamegraph_html

fg = Flamegraph.from_session(session)
data = fg.to_data()                  # dict for custom rendering
fg.save("session_flame.html")        # standalone HTML
print(fg.get_stats())

# One-shot HTML helper.
html = flamegraph_html(session)
```

## Session Exporter

Offline export to JSON, CSV, and a standalone HTML report.

```python
from agentlens import SessionExporter

exporter = SessionExporter(session, events)

data = exporter.as_json();  exporter.to_json("session_export.json")
csv_text = exporter.as_csv(); exporter.to_csv("session_export.csv")
html = exporter.as_html();   exporter.to_html("session_report.html")
```

The HTML export is self-contained (embedded CSS) — session metadata, event
timeline, and summary statistics, no external dependencies.

## Spans

Structured tracing spans for fine-grained operation tracking.

```python
from agentlens import Span

span = Span(name="retrieve-context", kind="internal")
span.set_attribute("query", "latest sales data")
span.set_attribute("num_results", 5)
# ... do work ...
span.set_status("ok")
span_dict = span.to_dict()
```

Spans can be nested and attached to a flamegraph for fine-grained timing.

## Models

| Model | Description |
|-------|-------------|
| `AgentEvent` | A single observable event (LLM call, tool call, decision, error) |
| `ToolCall` | A tool/function invocation with input, output, and timing |
| `DecisionTrace` | Reasoning behind a decision, with step number and confidence |
| `Session` | A collection of events for one agent run, with token aggregation |

## API Reference

### `agentlens.init(api_key, endpoint)`

Initialize the SDK. Must be called before any other function. Safe to call
multiple times (the previous transport is closed automatically). Returns the
global `AgentTracker`.

### `agentlens.start_session(agent_name, metadata=None)`

Start a new tracking session. Returns a `Session`.

### `agentlens.end_session(session_id=None)`

End the current (or specified) session and flush buffered events to the backend.

### `agentlens.track(...)`

Track a single event. Key parameters: `event_type`, `input_data` / `output_data`,
`model`, `tokens_in` / `tokens_out`, `reasoning`, `tool_name` / `tool_input` /
`tool_output`, `duration_ms`.

### `agentlens.explain(session_id=None)`

Markdown-formatted explanation of a session.

### `agentlens.export_session(session_id=None, format="json")`

Export session data — dict (JSON) or string (CSV).

### `agentlens.export_transcript(...)` / `agentlens.export_run_metadata(...)`

Render a `transcript-contract@v1` transcript and its ground-truth run metadata.

### `agentlens.compare_sessions(session_a, session_b)`

Compare two sessions. Returns metrics, deltas, and shared breakdowns.

### `agentlens.get_costs(...)` / `get_pricing()` / `set_pricing(...)`

Cost breakdown and per-model pricing configuration.

The `AgentTracker` returned by `init()` also exposes REST-backed helpers —
`search_events`, `heatmap`, tags, annotations, alert rules, and retention.
