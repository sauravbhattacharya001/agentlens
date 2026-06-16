<div align="center">

# AgentLens

**Observability and tracing for AI agents**

Capture every model call and tool step your agents take, then inspect the run
through a timeline, a plain-language narrative, a step-by-step replay, or a
flamegraph — all self-hosted.

[![CI](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/agentlens/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/agentlens?logo=pypi&logoColor=white)](https://pypi.org/project/agentlens/)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org)

[Getting Started](#getting-started) · [Quickstart](#quickstart) · [SDK API](#sdk-api) · [Backend &amp; Dashboard](#backend--dashboard) · [License](#license)

</div>

---

## What is AgentLens?

As agents call models and chain tools autonomously, it gets hard to see what
actually happened on any given run. AgentLens is a lightweight, self-hosted
toolkit that records each step of an agent run — LLM calls, tool calls,
decisions, tokens, timing, and cost — and lets you replay and explain it
afterwards. A Python SDK captures the trace and ships it to a small Node
collector backed by SQLite; a no-build dashboard renders sessions in the
browser. Everything runs on your own infrastructure, with no data leaving your
network.

## Features

| Feature | Description |
|---------|-------------|
| **Session &amp; span capture** | Group an agent run into a session and record every event — LLM calls, tool calls, decisions, errors — with input/output, tokens, and duration. |
| **`@track` decorators** | Wrap a function with `@track_agent` or `@track_tool_call` for zero-boilerplate instrumentation (sync and async). |
| **Manual tracking** | Call `track(...)` to log any event yourself when you want fine-grained control. |
| **Transport to a collector** | Buffered, background HTTP transport ships events to the AgentLens backend; flushed automatically on session end. |
| **Transcript &amp; cost export** | Export a session as JSON/CSV, render an evidence-backed Markdown transcript, and break down spend by model with configurable pricing. |
| **Timeline** | Render a session as a text, Markdown, or HTML timeline with critical-path and slowest-event helpers. |
| **Narrative** | Generate a human-readable summary of what the agent did over a session. |
| **Replay** | Step through a recorded session frame by frame, with adjustable speed and event filtering. |
| **Flamegraph** | Turn a session into a flamegraph (data or standalone HTML) to spot where time went. |
| **Health scoring** | Grade a session A–F across error rate, latency, tool success, and token efficiency, with recommendations. |
| **Session comparison** | Diff two sessions — token deltas, event breakdowns, shared tools. |
| **Node backend** | Express + better-sqlite3 collector with a REST API and zero-config SQLite storage. |
| **Dashboard** | Vanilla HTML/CSS/JS UI served by the backend — no build step. |

## Architecture

```
┌──────────────┐    HTTP POST     ┌──────────────────┐    SQLite     ┌──────────┐
│  Your Agent  │ ───────────────► │  AgentLens API   │ ────────────► │    DB    │
│   + SDK      │    /events       │   (Express.js)   │               └──────────┘
└──────────────┘                  └────────┬─────────┘
                                           │ REST API
                                  ┌────────┴─────────┐
                                  │    Dashboard      │
                                  │  (HTML/CSS/JS)    │
                                  └──────────────────┘
```

| Component | Directory | Stack |
|-----------|-----------|-------|
| **Python SDK** | `sdk/` | Python 3.9+, Pydantic, httpx |
| **Backend API** | `backend/` | Node.js, Express, better-sqlite3 |
| **Dashboard** | `dashboard/` | Vanilla HTML/CSS/JS (no build step) |

## Getting Started

### Prerequisites

- **Python 3.9+** (for the SDK)
- **Node.js 18+** and **npm** (for the backend)

### 1. Start the backend

```bash
git clone https://github.com/sauravbhattacharya001/agentlens.git
cd agentlens/backend
npm install
node seed.js      # optional: load demo data
node server.js    # starts on http://localhost:3000
```

The dashboard is served automatically at <http://localhost:3000>.

### 2. Install the SDK

```bash
pip install agentlens
```

Or from source for development:

```bash
cd sdk
pip install -e ".[dev]"
```

## Quickstart

```python
import agentlens
from agentlens import track_agent, track_tool_call

# Point the SDK at your backend.
agentlens.init(api_key="your-key", endpoint="http://localhost:3000")

# Instrument with decorators — input, output, and timing are captured for you.
@track_tool_call(tool_name="web_search")
def web_search(query: str) -> dict:
    return {"results": [...]}

@track_agent(model="gpt-4")
def research_agent(question: str) -> str:
    web_search(question)
    # Or log an event manually for full control:
    agentlens.track(
        event_type="llm_call",
        input_data={"prompt": question},
        output_data={"response": "..."},
        model="gpt-4",
        tokens_in=120,
        tokens_out=80,
        reasoning="Synthesized the search results into an answer.",
    )
    return "done"

# Record a run.
session = agentlens.start_session(agent_name="research-agent")
research_agent("What is the weather in SF?")
agentlens.end_session()   # flushes buffered events to the backend

# Inspect it.
print(agentlens.explain())                       # plain-language summary
print(agentlens.get_costs()["total_cost"])       # spend for the session
```

Then open <http://localhost:3000> to browse the session, or run the bundled
example end-to-end:

```bash
cd sdk/examples
python mock_agent.py
```

## SDK API

Top-level functions (all require `agentlens.init(...)` first):

| Function | Purpose |
|----------|---------|
| `init(api_key, endpoint)` | Initialize the SDK and its transport. |
| `start_session(agent_name, metadata=None)` | Begin a session; returns a `Session`. |
| `end_session(session_id=None)` | End a session and flush buffered events. |
| `track(...)` | Record a single event (LLM call, tool call, decision, generic). |
| `explain(session_id=None)` | Get a human-readable summary of a session. |
| `export_session(session_id=None, format="json")` | Export a session as a dict (JSON) or CSV string. |
| `export_transcript(...)` / `export_run_metadata(...)` | Render a `transcript-contract@v1` Markdown transcript and its ground-truth run metadata. |
| `compare_sessions(session_a, session_b)` | Diff two sessions (token deltas, shared tools, event breakdowns). |
| `get_costs(session_id=None)` | Cost breakdown using configured pricing. |
| `get_pricing()` / `set_pricing(pricing)` | View or override per-model pricing (per 1M tokens). |

Decorators and classes:

```python
from agentlens import (
    track_agent, track_tool_call,          # decorators
    AgentTracker, Transport,               # core
    AgentEvent, ToolCall, DecisionTrace, Session,   # models
    TimelineRenderer, NarrativeGenerator,  # views
    SessionReplayer, Flamegraph, flamegraph_html,
    SessionExporter, Span,
    HealthScorer, HealthReport, HealthThresholds,
)
```

### Visualize, replay, and score a session

```python
from agentlens import (
    HealthScorer, TimelineRenderer, NarrativeGenerator,
    SessionReplayer, Flamegraph,
)

# Pull a session (with events) back from the backend.
data = agentlens.export_session(session_id="abc123", format="json")
events = data["events"]

# Health grade (A–F) with recommendations.
report = HealthScorer().score(events, session_id="abc123")
print(f"{report.grade.value}  {report.overall_score:.0f}/100")
for rec in report.recommendations:
    print("  ->", rec)

# Text / Markdown / HTML timeline.
timeline = TimelineRenderer(events=events, session=data)
print(timeline.render_text(width=80, show_tokens=True))

# Plain-language narrative (needs a Session object).
session = agentlens.start_session(agent_name="replay")  # or build from your data
narrative = NarrativeGenerator().generate(session)
print(narrative.to_markdown())

# Step-by-step replay.
for frame in SessionReplayer(session, speed=2.0).play():
    print(f"[{frame.index}/{frame.total}] {frame.event.event_type}")

# Flamegraph as standalone HTML.
Flamegraph.from_session(session).save("session_flame.html")
```

The `AgentTracker` returned by `init()` also exposes convenience methods —
`tracker.timeline(...)`, `tracker.health_score(...)`, `tracker.span(...)`,
`tracker.heatmap(...)`, `tracker.search_events(...)`, and tag/annotation/alert/
retention helpers backed by the REST API.

> A note on direction: AgentLens deliberately stays at the **output level** —
> it records what an agent did so a run can be audited from outside the agent's
> own control surface. Output-level traces are where failure signatures (drift,
> ungrounded confidence, inconsistent reasoning) become observable after the
> fact, without touching model internals.

## Backend &amp; Dashboard

The backend (`backend/`) is an Express server with a `better-sqlite3` store:

```bash
cd backend
npm install
node seed.js      # optional demo data
node server.js    # http://localhost:3000  (npm start / npm run dev also work)
```

It ingests batched events at `POST /events` and serves a REST API over sessions,
analytics, pricing/costs, and health. The dashboard (`dashboard/`) is a
no-build HTML/CSS/JS app served from the same origin: browse sessions, view
timelines and token charts, read the explanation, see cost breakdowns, and
compare two sessions side by side.

See [`docs/API.md`](docs/API.md) for the REST reference and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system overview.

Run the SDK tests with `cd sdk && pip install -e ".[dev]" && pytest`.

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by [Saurav Bhattacharya](https://github.com/sauravbhattacharya001)**

*If you can't see what your agents are doing, you can't trust them.*

</div>
