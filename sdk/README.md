# AgentLens Python SDK

Lightweight Python SDK for tracking AI agent behavior with full observability.

## Installation

```bash
pip install -e .
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
