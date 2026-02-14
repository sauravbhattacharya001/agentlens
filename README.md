# AgentLens 🔍

**Observability and Explainability for AI Agents**

AgentLens gives you full visibility into what your AI agents are doing, why they're doing it, and how much it costs. Think of it as Datadog meets Chain-of-Thought — for agents.

## Architecture

```
┌─────────────┐     HTTP POST      ┌─────────────────┐     SQLite      ┌──────────┐
│  Your Agent  │ ──────────────────▶│  AgentLens API  │ ──────────────▶│    DB    │
│  + SDK       │    /events         │  (Express.js)   │                 └──────────┘
└─────────────┘                     └────────┬────────┘
                                             │ REST API
                                    ┌────────▼────────┐
                                    │   Dashboard      │
                                    │  (HTML/CSS/JS)   │
                                    └─────────────────┘
```

### Components

| Component | Directory | Tech |
|-----------|-----------|------|
| Python SDK | `sdk/` | Python 3.9+, Pydantic, httpx |
| Backend API | `backend/` | Node.js, Express, better-sqlite3 |
| Dashboard | `dashboard/` | Vanilla HTML/CSS/JS |

## Quickstart

### 1. Start the Backend

```bash
cd backend
npm install
node seed.js      # Load demo data
node server.js    # Starts on http://localhost:3000
```

### 2. Open the Dashboard

```bash
# Served by the backend at:
open http://localhost:3000
```

### 3. Instrument Your Agent

```bash
cd sdk
pip install -e .
```

```python
import agentlens

agentlens.init(api_key="your-key", endpoint="http://localhost:3000")
session = agentlens.start_session(agent_name="my-agent")

# Automatic tracking with decorators
@agentlens.track_agent
def my_agent(prompt):
    response = call_llm(prompt)
    return response

# Or manual tracking
agentlens.track(
    event_type="llm_call",
    input_data={"prompt": "Hello"},
    output_data={"response": "Hi there!"},
    model="gpt-4",
    tokens_in=5,
    tokens_out=10,
)

# Get human-readable explanation of agent behavior
explanation = agentlens.explain()
print(explanation)

session.end()
```

### 4. Run the Demo

```bash
cd sdk/examples
python mock_agent.py
```

## Features

- 📊 **Session tracking** — Group agent actions into sessions with full traces
- 🔧 **Tool call capture** — See every tool invocation with inputs/outputs
- 💰 **Token usage** — Track costs across models and calls
- 🧠 **Decision traces** — Capture *why* an agent made each choice
- 📈 **Visual timeline** — See agent actions on an interactive timeline
- 💡 **Explainability** — Human-readable summaries of agent behavior

## License

MIT — see [LICENSE](LICENSE)
