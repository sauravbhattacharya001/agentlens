# Copilot Instructions — AgentLens

## Project Overview

AgentLens is an observability and explainability platform for AI agents. Think "Datadog for AI agents" — it tracks sessions, events, tool calls, token usage, and decision reasoning.

## Architecture

The project has three components:

### Python SDK (`sdk/`)
- **Language:** Python 3.9+
- **Dependencies:** pydantic ≥2.0, httpx ≥0.24
- **Key modules:**
  - `agentlens/__init__.py` — Public API (init, track, explain, start_session, end_session)
  - `agentlens/models.py` — Pydantic data models (AgentEvent, ToolCall, DecisionTrace, Session)
  - `agentlens/tracker.py` — AgentTracker class that manages sessions and events
  - `agentlens/transport.py` — Batched HTTP transport with background flush thread and retry logic
  - `agentlens/decorators.py` — @track_agent and @track_tool_call decorators (sync + async)
- **Install:** `cd sdk && pip install -e ".[dev]"`
- **Tests:** `cd sdk && pytest` (add tests in `sdk/tests/`)
- **Example:** `sdk/examples/mock_agent.py`

### Backend API (`backend/`)
- **Language:** Node.js (Express.js)
- **Dependencies:** express, better-sqlite3, cors, uuid
- **Key files:**
  - `server.js` — Express app setup, middleware, static serving
  - `db.js` — SQLite connection with WAL mode, schema initialization
  - `middleware.js` — Helmet (security headers), CORS, rate limiters, API key auth
  - `lib/validation.js` — Input sanitization, session ID validation, JSON helpers, type checkers
  - `lib/explain.js` — Human-readable markdown explanation generator for agent sessions
  - `routes/events.js` — POST /events (batched event ingestion with transactions)
  - `routes/sessions.js` — GET /sessions, GET /sessions/:id, GET /sessions/:id/explain, /export, /compare
  - `routes/analytics.js` — GET /analytics (aggregate statistics, model usage, time series)
  - `routes/pricing.js` — GET/PUT /pricing, GET /pricing/costs/:sessionId, DELETE /pricing/:model
  - `seed.js` — Demo data seeder
- **Tests:** `cd backend && npm test` (Jest — tests in `backend/tests/`)
  - `tests/validation.test.js` — All validation/sanitization functions (48 tests)
  - `tests/explain.test.js` — Explanation generator, truncation, duration formatting (27 tests)
  - `tests/middleware.test.js` — API key auth, middleware factories, CORS config (10 tests)
- **Install:** `cd backend && npm install`
- **Run:** `node backend/server.js` (starts on port 3000)
- **Database:** SQLite (auto-created at `backend/agentlens.db`)

### Dashboard (`dashboard/`)
- **Language:** Vanilla HTML/CSS/JS (no build step)
- **Files:** index.html, styles.css, app.js
- **Served by:** Express static middleware in the backend

## Conventions

- **Python style:** PEP 8, type hints everywhere, Pydantic v2 models
- **Node style:** CommonJS require, Express router pattern
- **Error handling:** SDK decorators silently skip tracking if init() not called (never break user code)
- **Threading:** Transport uses a background daemon thread for periodic flushing — be thread-safe
- **IDs:** 16-char hex UUIDs (uuid.uuid4().hex[:16])
- **Timestamps:** Always UTC, ISO 8601 format
- **JSON storage:** Complex fields (input_data, output_data, tool_call, decision_trace, metadata) stored as JSON strings in SQLite, parsed on read

## How to Test

```bash
# Backend — run unit tests
cd backend && npm install && npm test

# Backend — start server and hit health endpoint
node server.js &
curl http://localhost:3000/health

# SDK — install and run tests
cd sdk && pip install -e ".[dev]"
pytest

# Integration — run the demo agent
cd sdk/examples && python mock_agent.py
# Then check http://localhost:3000 for sessions
```

## Key Design Decisions

1. **SQLite over Postgres:** Zero-config, embedded, good enough for single-server observability
2. **Batched transport:** Never block the agent's main thread; buffer events and flush async
3. **Pydantic v2:** Strict validation, fast serialization, modern Python typing
4. **No dashboard framework:** Vanilla JS for zero build friction
5. **Decorators are safe everywhere:** If SDK not initialized, decorators silently no-op

## Common Tasks

- **Add a new API endpoint:** Create route in `backend/routes/`, register in `server.js`
- **Add SDK functionality:** Update `tracker.py` for logic, `__init__.py` for public API
- **Add a new data model:** Define in `models.py` with Pydantic BaseModel
- **Update database schema:** Modify `initSchema()` in `db.js` (add migration for existing data)
