# Changelog

All notable changes to AgentLens will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
