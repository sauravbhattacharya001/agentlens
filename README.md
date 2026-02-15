<div align="center">

# 🔍 AgentLens

**Observability and Explainability for AI Agents**

*Datadog meets Chain-of-Thought — for autonomous agents*

[![CI](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml)
[![CodeQL](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/codeql.yml)
[![Coverage](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/coverage.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
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
cd sdk
pip install -e .
```

### 4. Instrument Your Agent

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

### Data Models

| Model | Description |
|-------|-------------|
| `AgentEvent` | A single observable event (LLM call, tool use, decision) |
| `ToolCall` | A tool/function invocation with input and output |
| `DecisionTrace` | The reasoning behind an agent's decision |
| `Session` | A collection of events for one agent run |

## 📊 Dashboard

The dashboard provides a real-time view of your agent sessions:

- **Sessions List** — Filter by status (active, completed, error)
- **Session Comparison** — Select two sessions and compare side-by-side with visual diffs
- **Analytics Overview** — Click 📈 Analytics to see aggregate stats, model usage, hourly activity, and top agents
- **Timeline View** — Interactive timeline of every event in a session
- **Token Charts** — Per-event and cumulative token usage visualization
- **Explain Tab** — Human-readable behavior summaries

The dashboard is a lightweight HTML/CSS/JS app served directly by the backend — no build step required.

## 🔌 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Health check |
| `GET /sessions` | GET | List all sessions |
| `GET /sessions/:id` | GET | Get session details with events |
| `GET /sessions/:id/export` | GET | Export session data as JSON or CSV |
| `POST /sessions/compare` | POST | Compare two sessions side-by-side |
| `GET /analytics` | GET | Aggregate statistics across all sessions |
| `POST /events` | POST | Record a new event |
| `GET /events?session_id=...` | GET | Get events for a session |

## 🛠️ Tech Stack

- **Python SDK**: Pydantic for data validation, httpx for async HTTP
- **Backend**: Express.js with better-sqlite3 for zero-config persistence
- **Dashboard**: Vanilla JS with Canvas-based charts (no framework dependencies)
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
