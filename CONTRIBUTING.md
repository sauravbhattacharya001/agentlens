# Contributing to AgentLens

Thanks for your interest in AgentLens! This guide covers setup, development, and contribution workflow for the backend, Python SDK, and dashboard.

## Table of Contents

- [Project Structure](#project-structure)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Making Changes](#making-changes)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Coding Conventions](#coding-conventions)
- [Security Vulnerabilities](#security-vulnerabilities)

## Project Structure

```
agentlens/
├── backend/          # Node.js (Express) API server
│   ├── routes/       # API endpoints
│   ├── lib/          # Core services (DB, cost estimation, etc.)
│   ├── tests/        # Jest tests
│   └── db.js         # SQLite database layer
├── sdk/              # Python SDK (published to PyPI)
│   ├── agentlens/    # Package source
│   ├── tests/        # pytest tests
│   └── pyproject.toml
├── dashboard/        # Web dashboard (frontend)
├── demo/             # Demo scripts and examples
└── docs/             # GitHub Pages documentation
```

## Development Setup

### Backend (Node.js)

```bash
cd backend
npm install
cp .env.example .env    # configure environment
npm start               # starts on port 3000
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
npm test                # all tests
npm test -- --verbose   # with details
```

### SDK

```bash
cd sdk
pytest tests/ -v
```

### Full Suite

The CI workflow runs both backend and SDK tests across Node 18/20 and Python 3.9-3.12.

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

## Submitting a Pull Request

1. Fork the repository and create your branch
2. Make changes with tests
3. Ensure all tests pass (`npm test` in backend, `pytest` in sdk)
4. Push and open a PR against `master`
5. Fill out the PR template

### What We Look For

- **Tests**: New features need tests; bug fixes need regression tests
- **Both components**: If a change spans backend + SDK, test both
- **No regressions**: All existing tests must pass
- **Clean diff**: One concern per PR

## Coding Conventions

### Backend (JavaScript)

- Node.js 18+ features (ES modules where used, modern syntax)
- Express middleware patterns
- SQLite via `better-sqlite3` with prepared statements
- Jest for testing

### SDK (Python)

- Python 3.9+ compatibility
- Type hints on public API functions
- pytest for testing
- Published to PyPI — follow semantic versioning

### General

- Keep dependencies minimal
- Document public APIs
- Handle errors explicitly (no silent swallows)

## Security Vulnerabilities

**Do not open a public issue for security vulnerabilities.**

Use the [Security Advisory](https://github.com/sauravbhattacharya001/agentlens/security/advisories/new) form or email the maintainer directly.

## Questions?

Open a GitHub issue with the relevant template, or check existing issues and discussions.

Thank you for helping make AI agent observability better! 🔍
