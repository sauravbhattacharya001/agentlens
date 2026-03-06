# Changelog

All notable changes to AgentLens will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-03-06

### Added

- **Incident Postmortem Generator** — Generate post-incident reports from session data (SDK + backend)
- **Trace Correlation Rules Engine** — Define rules for auto-correlating related traces with scheduled auto-correlation, SSE streaming, and deduplication
- **Response Quality Evaluator** — Score agent output quality across multiple dimensions
- **Service Dependency Map** — Visualize tool/API usage patterns and service relationships
- **Trace Sampling & Rate Limiting** — Production-ready sampling policies and rate control
- **Activity Heatmap** — Day-of-week × hour-of-day interaction matrix visualization
- **SLA Monitor** — Service-level compliance tracking and alerting
- **Behavioral Drift Detection** — Detect changes in agent behavior patterns over time
- **Compliance Checker** — Policy-based session validation and audit
- **Cost Forecaster** — Predict future AI costs from historical usage trends
- **Session Search** — Full-text search, filter, and sort across sessions

### Fixed

- **BudgetTracker session collision** — Multiple budgets per session no longer overwrite each other (#35)
- **CSV formula injection** — Harden CSV export against spreadsheet injection attacks
- **OOM on large sessions** — Paginate eventsBySession queries to prevent memory exhaustion
- **Pricing model match** — Replace bidirectional substring match with delimiter-aware longest prefix
- **N+1 tag filtering** — Eliminate per-session tag queries in retention exempt filtering
- **P95 formula** — Correct percentile calculation in analytics
- **AnomalyDetector variance** — Use Bessel's correction (sample variance) for small datasets (#22)
- **AlertManager cooldown** — Fix race condition in alert evaluate cooldown tracking
- **sessionsOverTime** — Return most recent 90 days instead of oldest (#19)
- **Deprecated asyncio** — Replace `get_event_loop()` with `asyncio.run()` (#30)

### Performance

- Replace correlated subqueries with JOIN aggregation in error analytics
- Batch retention purge into single transaction, eliminate N+1 queries
- Push analytics/performance aggregation and event search filters to SQL
- Compute retention age distribution in SQL instead of JS
- Cache prepared statements and add database indexes
- Eliminate N+1 tag queries in `/sessions/by-tag/:tag`

### Security

- Constant-time comparison for API key authentication (prevent timing side-channel)
- Mask API key in `repr` output, validate webhook ID parameters
- Input bounds validation for webhook configuration
- SSRF protection for outbound webhooks
- Replace `Math.random` IDs with `crypto.randomBytes`

### Refactored

- Extract shared pagination, session-ID validation, and error-handling helpers
- Extract session tag routes into dedicated `tags.js` module
- Extract Transport HTTP helpers and `_resolve_session` in tracker
- Extract statistical utilities into shared stats module
- Extract session metrics computation into shared module
- Adopt request-helpers across all route files

### Tests

- 32 new Transport convenience HTTP method tests
- 58 sessions test suite
- `node:test` compatible unit tests for `db.js` schema init
- Converted db and webhook tests from `node:test` to Jest

### Documentation

- SDK documentation for 8 previously undocumented modules
- Sampling & rate limiting documentation page
- JSDoc added to all 15 route handlers in `sessions.js`
- SDK analysis modules documentation

## [1.1.0] - 2026-02-19

### Added

- **Cost Estimation** — Full cost tracking across sessions and events
  - `model_pricing` DB table with default pricing for 14 popular models (GPT-4/4o/3.5, Claude 3/3.5/4, Gemini Pro/Flash)
  - `GET /pricing` — List all model pricing configuration
  - `PUT /pricing` — Update pricing for one or more models
  - `DELETE /pricing/:model` — Remove custom pricing
  - `GET /pricing/costs/:sessionId` — Calculate per-event and per-model costs with fuzzy model matching
  - Dashboard **💲 Costs tab** with cost overview cards, per-event cost bar chart, cumulative cost line chart, cost-by-model table, top costliest events list, and inline pricing editor
  - SDK methods: `get_costs()`, `get_pricing()`, `set_pricing()` with full module-level API
  - 12 new SDK tests (82 total)

## [1.0.0] - 2026-02-14

### 🎉 Initial Stable Release

AgentLens v1.0.0 — Observability and explainability for AI agents. Track agent sessions, tool calls, LLM interactions, and costs in real-time with a lightweight Python SDK and Node.js dashboard.

### Added

- **Python SDK** (`agentlens` package)
  - `@track_agent` and `@track_tool_call` decorators with full async support
  - Pydantic-based data models (`AgentEvent`, `ToolCallEvent`, `LLMEvent`, `Session`)
  - Batched HTTP transport with automatic retry and backpressure handling
  - Configurable `AgentTracker` with API key authentication and custom endpoints
  - LangChain integration support

- **Backend API** (Node.js + Express)
  - RESTful endpoints for session and event ingestion
  - SQLite-backed persistence via `better-sqlite3`
  - CORS-enabled for cross-origin dashboard access
  - Seed script for demo data generation

- **Dashboard** (Vanilla JS SPA)
  - Real-time session list with status indicators
  - Event timeline visualization per session
  - Tool call and LLM interaction detail views

- **Documentation Site** (12 pages)
  - Getting started guide and quickstart tutorial
  - Full SDK reference and API documentation
  - Architecture overview and deployment guide
  - Decorator reference, transport internals, and database schema docs

- **DevOps & Tooling**
  - CodeQL security scanning (JavaScript + Python)
  - Dependabot configuration (pip, npm, GitHub Actions)
  - Issue and PR templates
  - GitHub Copilot coding agent setup (setup-steps + instructions)

### Fixed

- Unbounded buffer growth and event loss in SDK transport layer
- Batch-length retry key replaced with consecutive failure counter
- Duplicate license section in README

### Changed

- Rebranded from AgentOps to AgentLens

[1.2.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.2.0
[1.1.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.1.0
[1.0.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.0.0
