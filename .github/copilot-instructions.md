# Copilot Instructions — AgentLens

## Project Overview

AgentLens is an observability and explainability platform for AI agents — "Datadog for AI agents." It tracks sessions, events, tool calls, token usage, decision reasoning, costs, SLAs, anomalies, and more.

## Architecture

Three main components plus a CLI:

### Python SDK (`sdk/`)
- **Language:** Python 3.9+
- **Dependencies:** pydantic ≥2.0, httpx ≥0.24
- **Install:** `cd sdk && pip install -e ".[dev]"`
- **Tests:** `cd sdk && pytest`

**Core modules** (`sdk/agentlens/`):
- `__init__.py` — Public API (init, track, explain, start_session, end_session)
- `models.py` — Pydantic v2 data models (AgentEvent, ToolCall, DecisionTrace, Session)
- `tracker.py` — AgentTracker class (session/event lifecycle management)
- `transport.py` — Batched HTTP transport with background flush thread and retry logic
- `decorators.py` — @track_agent and @track_tool_call decorators (sync + async)
- `span.py` — Span-based tracing support

**Analytics & monitoring modules:**
- `alert_rules.py`, `alerts.py`, `tracker_alerts.py` — Alerting rules engine and tracker integration
- `anomaly.py` — Anomaly detection for agent behavior patterns
- `budget.py` — Cost budget tracking and enforcement
- `capacity.py` — Capacity planning and resource estimation
- `compliance.py` — Compliance checking and policy enforcement
- `correlation.py` — Cross-session event correlation analysis
- `cost_optimizer.py` — Token usage and cost optimization recommendations
- `drift.py` — Behavioral drift detection across sessions
- `error_fingerprint.py` — Error deduplication and fingerprinting
- `evaluation.py` — Agent evaluation and scoring framework
- `exporter.py` — Data export (JSON, CSV, OpenTelemetry formats)
- `flamegraph.py` — Flamegraph generation for session execution
- `forecast.py` — Cost/usage forecasting with statistical models
- `group_analyzer.py` — Group-level session analysis
- `guardrails.py` — Safety guardrails and boundary checking
- `health.py` — Agent health scoring and diagnostics
- `heatmap.py` — Activity heatmap generation
- `latency.py` — Latency profiling and percentile analysis
- `narrative.py` — Human-readable session narrative generation
- `postmortem.py` — Automated incident postmortem generation
- `prompt_tracker.py` — Prompt versioning and performance tracking
- `quota.py` — Usage quota management
- `rate_limiter.py` — Client-side rate limiting
- `replayer.py` — Session replay for debugging
- `retry_tracker.py` — Retry pattern analysis
- `sampling.py` — Event sampling strategies
- `session_diff.py` — Session comparison/diff
- `sla.py` — SLA definition, tracking, and reporting
- `timeline.py` — Visual timeline generation
- `tracker_annotations.py`, `tracker_retention.py`, `tracker_tags.py` — Tracker mixins

**CLI modules** (`sdk/agentlens/cli*.py`):
- `cli.py` — Main CLI entry point with subcommands
- `cli_alert.py`, `cli_analytics.py`, `cli_audit.py`, `cli_baseline.py`, `cli_budget.py`, `cli_capacity.py`, `cli_config.py`, `cli_correlate.py`, `cli_depmap.py`, `cli_diff.py`, `cli_digest.py`, `cli_forecast.py`, `cli_funnel.py`, `cli_gantt.py`, `cli_heatmap.py`, `cli_leaderboard.py`, `cli_profile.py`, `cli_retention.py`, `cli_scatter.py`, `cli_sla.py`, `cli_snapshot.py`, `cli_trace.py`, `cli_trends.py`, `cli_watch.py`
- `cli_common.py` — Shared CLI utilities

### Backend API (`backend/`)
- **Language:** Node.js (Express.js)
- **Dependencies:** express, better-sqlite3, cors, helmet, express-rate-limit
- **Install:** `cd backend && npm install`
- **Tests:** `cd backend && npm test` (Jest)
- **Run:** `node backend/server.js` (port 3000)
- **Database:** SQLite with WAL mode (auto-created at `backend/agentlens.db`)

**Routes** (`backend/routes/`):
- `sessions.js` — CRUD for agent sessions, export, compare
- `events.js` — Batched event ingestion with transactions
- `analytics.js` — Aggregate statistics, model usage, time series
- `pricing.js` — Model pricing management and cost calculation
- `alerts.js` — Alert rule CRUD and alert history
- `annotations.js` — Session/event annotation management
- `anomalies.js` — Anomaly detection and reporting
- `baselines.js` — Performance baseline management
- `bookmarks.js` — Session bookmarking
- `budgets.js` — Cost budget tracking
- `correlations.js`, `correlation-scheduler.js` — Cross-session correlation
- `dependencies.js` — Dependency tracking between agents
- `diff.js` — Session diff/comparison API
- `errors.js` — Error aggregation and fingerprinting
- `forecast.js` — Cost/usage forecasting
- `leaderboard.js` — Agent performance leaderboard
- `postmortem.js` — Automated postmortem generation
- `replay.js` — Session replay API
- `retention.js` — Data retention policy management
- `scorecards.js` — Agent scorecards
- `sla.js` — SLA tracking and reporting
- `tags.js` — Tag management for sessions/events
- `webhooks.js` — Webhook configuration and delivery

**Libraries** (`backend/lib/`):
- `validation.js` — Input sanitization, session ID validation, type checkers
- `explain.js` — Human-readable markdown explanation generator
- `csv-export.js` — CSV export utilities
- `dependency-map.js` — Agent dependency graph
- `pricing.js` — Pricing calculation helpers
- `request-helpers.js` — Pagination, sorting, filtering helpers
- `response-cache.js` — In-memory response caching
- `session-metrics.js` — Session metric aggregation
- `stats.js` — Statistical computation utilities
- `tag-statements.js` — Tag-based query building

### Dashboard (`dashboard/`)
- **Language:** Vanilla HTML/CSS/JS (no build step)
- **Served by:** Express static middleware in the backend
- No npm/webpack needed

### Demo (`demo/`)
- Interactive demo showcasing AgentLens features

### Docs (`docs/`)
- Static HTML documentation site (deployed via GitHub Pages)
- Includes: API reference, architecture guide, getting-started, deployment, SDK reference

### Other
- `FeedReader/` — Swift utility (ArticleReadLaterReminder)
- `test/` — Root-level integration tests (challenge-replay-guard)
- `tests/` — Root-level dashboard/UI tests (costs, diff, errors, scorecards, SLA, waterfall)

## How to Test

```bash
# Backend tests (Jest)
cd backend && npm install && npm test

# Backend with coverage
cd backend && npm run test:coverage

# SDK tests (pytest)
cd sdk && pip install -e ".[dev]" && pytest

# SDK with coverage
cd sdk && pytest --cov=agentlens --cov-report=term

# Start backend for manual testing
cd backend && node seed.js && node server.js
# Visit http://localhost:3000

# Run the demo agent
cd sdk/examples && python mock_agent.py
```

## Key Design Decisions

1. **SQLite over Postgres:** Zero-config, embedded, WAL mode for concurrent reads
2. **Batched transport:** Never block the agent's main thread; buffer events and flush async
3. **Pydantic v2:** Strict validation, fast serialization, `model_config`/`.model_dump()`/`field_validator` only
4. **No dashboard framework:** Vanilla JS for zero build friction
5. **Decorators are safe everywhere:** If SDK not initialized, decorators silently no-op
6. **Modular analytics:** Each analytics feature (SLA, alerts, budgets, etc.) is self-contained in its own module

## Important Constraints

- **Never break decorator safety:** `@track_agent` and `@track_tool_call` must silently no-op if `init()` hasn't been called
- **Thread safety in transport:** Background daemon thread for periodic flushing — use locks where needed
- **SQLite WAL mode:** Don't switch to other journal modes
- **Pydantic v2 only:** No v1 patterns (`class Config`, `.dict()`, `validator`)
- **Backend tests use Jest:** `backend/tests/`. SDK tests use pytest in `sdk/tests/`
- **CommonJS in backend:** Use `require()`, not `import`
- **Express router pattern:** One router per feature in `backend/routes/`
- **Type hints everywhere** in Python code
- **IDs:** 16-char hex UUIDs (`uuid.uuid4().hex[:16]`)
- **Timestamps:** Always UTC, ISO 8601 format
- **JSON storage:** Complex fields stored as JSON strings in SQLite, parsed on read

## Common Tasks

- **Add a new API endpoint:** Create route in `backend/routes/`, register in `server.js`
- **Add SDK functionality:** Update `tracker.py` for logic, `__init__.py` for public API
- **Add a CLI subcommand:** Create `cli_*.py`, register in `cli.py`
- **Add a new data model:** Define in `models.py` with Pydantic BaseModel
- **Update database schema:** Modify `initSchema()` in `db.js` (add migration for existing data)
- **Add analytics feature:** Create module in `sdk/agentlens/`, route in `backend/routes/`, tests in both
