# Contributing to AgentLens

Thanks for your interest in AgentLens! This guide covers architecture, setup, development workflow, and contribution standards for the backend, Python SDK, and dashboard.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Module Catalog](#module-catalog)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [CI/CD Pipeline](#cicd-pipeline)
- [Making Changes](#making-changes)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Coding Conventions](#coding-conventions)
- [Performance Guidelines](#performance-guidelines)
- [Common Pitfalls](#common-pitfalls)
- [Security Vulnerabilities](#security-vulnerabilities)

## Architecture Overview

AgentLens is a **multi-component observability platform** for AI agents:

```
┌──────────────┐     ┌──────────────────┐     ┌───────────────┐
│  Python SDK  │────▶│  Backend (API)   │◀────│   Dashboard   │
│  (agentlens) │     │  Express/SQLite  │     │   (Web UI)    │
└──────────────┘     └──────────────────┘     └───────────────┘
       │                      │
       ▼                      ▼
 Agent code               SQLite DB
 instrumentation          (better-sqlite3)
```

- **SDK** — lightweight Python library (PyPI: `agentlens`) that instruments agent code, tracks sessions/spans/events, and ships telemetry to the backend.
- **Backend** — Node.js/Express REST API with SQLite persistence via `better-sqlite3` prepared statements. Handles analytics, alerting, forecasting, cost estimation, and more.
- **Dashboard** — Web frontend for visualizing agent sessions, costs, errors, and performance.
- **Docs** — GitHub Pages site generated from `docs/`.

## Project Structure

```
agentlens/
├── backend/              # Node.js (Express) API server
│   ├── routes/           # 14 API route modules
│   ├── lib/              # Core services (pricing, stats, caching, etc.)
│   ├── tests/            # 29 Jest suites (+ 1 Node suite via test:node)
│   ├── db.js             # SQLite database layer
│   ├── middleware.js     # Express middleware (auth, rate limiting)
│   ├── migrations.js     # Schema migration runner
│   └── server.js         # Application entry point
├── sdk/                  # Python SDK (PyPI package)
│   ├── agentlens/        # 28 modules — core library
│   ├── tests/            # pytest test suites
│   └── pyproject.toml    # Package metadata & dependencies
├── dashboard/            # Web dashboard (frontend)
├── demo/                 # Demo scripts and examples
├── docs/                 # GitHub Pages documentation
├── .github/workflows/    # 10 CI/CD workflows
├── Dockerfile            # Multi-stage production build
└── CHANGELOG.md          # Release history
```

## Module Catalog

### Backend Routes (14 modules)

Each row maps to a file in `backend/routes/` mounted in `server.js`.

| Domain | Routes |
|--------|--------|
| **Core** | `sessions`, `events`, `errors`, `tags` |
| **Analytics** | `analytics`, `leaderboard` |
| **Cost** | `pricing` |
| **Alerting** | `alerts`, `webhooks` |
| **Collaboration** | `annotations`, `bookmarks` |
| **Operations** | `replay`, `diff`, `retention` |

### Backend Libraries (12 modules)

`csv-export` · `explain` · `lazy-statements` · `pdf-export` · `pricing` · `request-helpers` · `response-cache` · `session-metrics` · `statement-cache` · `stats` · `tag-statements` · `validation`

### SDK Modules (28 modules)

The SDK mirrors the three product pillars: **Capture** (tracking), **Transport**
(shipping events to the collector), and **Inspect** (timeline, narrative, replay,
flamegraph, transcript/cost export, health). Render-only seams live in sibling
`*_render` / `*_format` / `*_types` modules next to the feature they back.

| Category | Modules |
|----------|---------|
| **Capture** | `tracker`, `tracker_alerts`, `tracker_annotations`, `tracker_queries`, `tracker_retention`, `tracker_tags`, `decorators`, `span`, `models` |
| **Transport** | `transport` |
| **Inspect** | `timeline` (+ `timeline_render`, `timeline_format`), `narrative` (+ `narrative_render`, `narrative_types`), `replayer` (+ `replayer_types`), `flamegraph` (+ `flamegraph_template`), `exporter` (+ `exporter_format`), `transcript` (+ `transcript_format`), `health` (+ `health_types`) |
| **Internal** | `_utils` |

## Development Setup

### Prerequisites

- **Node.js 18+** (backend)
- **Python 3.9+** (SDK)
- **Git** with conventional commits

### Backend (Node.js)

```bash
cd backend
npm install
cp .env.example .env    # configure environment
npm start               # starts on port 3000
```

The backend uses SQLite — no external database required. Migrations run automatically on startup.

To seed sample data:

```bash
npm run seed
```

### Python SDK

```bash
cd sdk
pip install -e ".[dev]"   # editable install with dev dependencies
```

### Dashboard

See `dashboard/` for frontend setup instructions.

## Running Tests

### Backend

```bash
cd backend
npm test                    # 29 Jest suites + 1 Node suite (via test:node)
npm test -- --verbose       # with details
npm run test:coverage       # with coverage report
```

Tests use Jest with in-memory SQLite. Each test suite is isolated — no shared state between files.

### SDK

```bash
cd sdk
pytest tests/ -v
pytest tests/ -v --cov=agentlens   # with coverage
```

### Full Suite

CI runs both backend and SDK tests across Node 18/20 and Python 3.9–3.12.

## CI/CD Pipeline

The project uses 10 GitHub Actions workflows:

| Workflow | Purpose |
|----------|---------|
| `ci.yml` | Build + test (backend Jest, SDK pytest) on push/PR |
| `coverage.yml` | Code coverage reporting via Codecov |
| `codeql.yml` | Security scanning (JavaScript + Python) |
| `docker.yml` | Docker image build and push |
| `pages.yml` | Deploy docs to GitHub Pages |
| `publish-npm.yml` | Publish backend to npm |
| `publish-pypi.yml` | Publish SDK to PyPI |
| `release-please.yml` | Automated release PRs + changelogs |
| `auto-label.yml` | Auto-label issues/PRs by path |
| `stale.yml` | Close stale issues/PRs |

## Making Changes

### Branch Naming

- `feat/description` — new features
- `fix/description` — bug fixes
- `docs/description` — documentation
- `refactor/description` — code improvements
- `test/description` — test additions/fixes

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(backend): add alert rule evaluation endpoint
fix(sdk): handle empty event payload gracefully
test(backend): add cost estimation edge cases
docs(readme): update SDK installation instructions
```

Scope with the component: `backend`, `sdk`, `dashboard`, `docs`, `ci`.

### Adding a New Backend Route

1. Create `routes/your-feature.js` exporting an Express router
2. Register in `server.js`
3. Add prepared statements in the route or via `lib/lazy-statements.js`
4. Write tests in `tests/your-feature.test.js`
5. Update this doc's module catalog

### Adding a New SDK Module

1. Create `sdk/agentlens/your_module.py`
2. Export public API in `__init__.py` if user-facing
3. Add type hints on all public functions
4. Write tests in `sdk/tests/test_your_module.py`

## Submitting a Pull Request

1. Fork the repository and create your branch
2. Make changes with tests
3. Ensure all tests pass (`npm test` in backend, `pytest` in SDK)
4. Push and open a PR against `master`
5. Fill out the PR template

### What We Look For

- **Tests**: New features need tests; bug fixes need regression tests
- **Both components**: If a change spans backend + SDK, test both
- **No regressions**: All existing tests must pass
- **Clean diff**: One concern per PR
- **Type safety**: Python code must pass `mypy` without errors on public APIs

## Coding Conventions

### Backend (JavaScript)

- Node.js 18+ features (modern syntax, optional chaining, nullish coalescing)
- Express middleware patterns — keep routes thin, logic in `lib/`
- SQLite via `better-sqlite3` with **prepared statements** (never string-interpolate SQL)
- Use `lib/lazy-statements.js` for statement caching across hot paths
- Use `lib/response-cache.js` for cacheable GET endpoints
- Jest for testing — each test file is self-contained

### SDK (Python)

- Python 3.9+ compatibility (no 3.10+ syntax like `match`)
- Type hints on all public API functions and class attributes
- `pydantic` models for data validation
- `httpx` for HTTP transport (async-compatible)
- Keep external dependencies minimal — core must only need `pydantic` + `httpx`
- pytest for testing with fixtures for common setup

### General

- Keep dependencies minimal — justify any new addition
- Document public APIs with docstrings (Python) or JSDoc (JavaScript)
- Handle errors explicitly — no silent swallows, no bare `except:`
- Prefer pure functions over stateful classes where feasible
- Log at appropriate levels (don't pollute stdout in library code)

## Performance Guidelines

- **Backend**: Use prepared statements (never re-prepare in request handlers). Cache expensive computations via `response-cache.js`. Keep SQLite queries indexed.
- **SDK**: Minimize overhead in the hot path (decorators, span creation). Batch network calls. Use sampling for high-volume agents.
- **Both**: Profile before optimizing. Measure with real workloads, not micro-benchmarks.

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| Forgetting to register a new route in `server.js` | Add `app.use('/api/...', require('./routes/...'))` |
| Breaking Python 3.9 compat with `X \| Y` union syntax | Use `Union[X, Y]` from `typing` |
| Mutating shared state in test suites | Each test should set up/tear down its own DB |
| Adding heavy deps to the SDK | Keep the core light — optional deps go in `[extras]` |
| String-interpolating user input into SQL | Always use `?` placeholders with prepared statements |

## Security Vulnerabilities

**Do not open a public issue for security vulnerabilities.**

Use the [Security Advisory](https://github.com/sauravbhattacharya001/agentlens/security/advisories/new) form or email the maintainer directly. See `SECURITY.md` for the full policy.

## Questions?

Open a GitHub issue with the relevant template, or check existing issues and discussions.

Thank you for helping make AI agent observability better! 🔍
