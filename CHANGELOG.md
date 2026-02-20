# Changelog

All notable changes to AgentLens will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.0.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.0.0
