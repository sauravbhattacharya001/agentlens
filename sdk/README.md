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

@track_tool_call(tool_name="web_search")
def search(query):
    return do_search(query)
```

## Models

- `AgentEvent` — A single observable event
- `ToolCall` — A tool/function invocation
- `DecisionTrace` — Reasoning behind a decision
- `Session` — A collection of events for one agent run
