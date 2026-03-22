# Architecture Overview

AgentLens is a self-hosted observability platform for AI agents. It captures agent events (LLM calls, tool calls, decisions, errors), stores them locally, and provides analytics, alerting, cost tracking, and debugging tools through a web dashboard and Python SDK.

## Components

```
┌──────────────────────┐     HTTP/REST     ┌──────────────────────┐
│   Python SDK         │ ────────────────► │   Node.js Backend    │
│   (your agent code)  │   POST /events    │   (Express server)   │
│                      │   GET /sessions   │                      │
│   pip install        │   etc.            │   npm start          │
│   agentlens          │                   │   :3000              │
└──────────────────────┘                   └──────────┬───────────┘
                                                      │
                                           ┌──────────▼───────────┐
                                           │   SQLite Database    │
                                           │   (better-sqlite3)   │
                                           │                      │
                                           │   WAL mode, mmap,    │
                                           │   8 MB page cache    │
                                           └──────────────────────┘
                                                      ▲
                                           ┌──────────┴───────────┐
                                           │   Web Dashboard      │
                                           │   (static HTML/JS)   │
                                           │                      │
                                           │   Served by Express  │
                                           │   at /               │
                                           └──────────────────────┘
```

### Backend (`backend/`)

- **Runtime:** Node.js + Express
- **Database:** SQLite via `better-sqlite3` (file: `agentlens.db`)
- **Auth:** API key via `x-api-key` header (constant-time comparison with SHA-256 hashing)
- **Security:** Helmet headers, CORS allowlist, rate limiting (120 req/min API, 60 req/min ingest)
- **Graceful shutdown:** SIGTERM/SIGINT handlers drain connections and checkpoint WAL

Key files:
| File | Purpose |
|------|---------|
| `server.js` | App bootstrap, route mounting, middleware wiring |
| `db.js` | SQLite connection, schema initialization, WAL config |
| `middleware.js` | Helmet, CORS, rate limiting, API key auth |
| `routes/` | One file per API domain (sessions, events, alerts, etc.) |
| `lib/` | Shared utilities (pricing, stats, validation, caching) |
| `tests/` | Jest test suite for all routes and libraries |

### Python SDK (`sdk/`)

A pip-installable client library that wraps the REST API. Features:

- **Core:** Event tracking, session management, decorators (`@track_agent`, `@track_tool_call`)
- **Analysis (local):** Health scoring, anomaly detection, compliance checking, drift detection, A/B testing, SLA evaluation
- **Operational:** Alerts, budgets, cost forecasting, rate limiting, sampling strategies
- **Debugging:** Session replay, timeline visualization, postmortem generation, session correlation
- **CLI:** `agentlens` command for analytics, budgets, alerts, dependency maps, and more

The SDK modules run locally (no backend calls) for analysis classes, and use HTTP for data ingestion/retrieval.

### Dashboard (`dashboard/`)

Static HTML/CSS/JS served by the Express backend at `/`. Pages:

| Page | Purpose |
|------|---------|
| `index.html` | Session list and detail view |
| `costs.html` | Cost tracking and forecasting |
| `errors.html` | Error analytics dashboard |
| `scorecards.html` | Agent performance scorecards |
| `sla.html` | SLA target monitoring |
| `diff.html` | Session comparison |
| `waterfall.html` | Event waterfall/timeline view |

## Database Schema

Core tables:

| Table | Purpose |
|-------|---------|
| `sessions` | Agent sessions with status, agent name, token totals |
| `events` | Individual events (LLM calls, tool calls, etc.) with foreign key to sessions |
| `model_pricing` | Per-model cost configuration (USD per 1M tokens) |
| `session_tags` | Many-to-many tags on sessions |
| `cost_budgets` | Spending limits per agent or global, by period |
| `session_bookmarks` | Starred sessions with notes |

SQLite is configured for read-heavy analytics:
- **WAL mode** for concurrent reads during writes
- **8 MB page cache** (4x default)
- **256 MB mmap** for faster sequential reads
- **Foreign keys enabled** for referential integrity

## Deployment

### Local Development

```bash
# Backend
cd backend
cp .env.example .env
npm install
npm run seed  # optional: populate sample data
npm start     # http://localhost:3000

# SDK
cd sdk
pip install -e ".[dev]"
```

### Production

**Environment variables:**

| Variable | Required | Description |
|----------|----------|-------------|
| `AGENTLENS_API_KEY` | Yes | API key for client authentication |
| `CORS_ORIGINS` | Yes | Comma-separated allowed origins |
| `PORT` | No | Server port (default: 3000) |
| `DB_PATH` | No | SQLite database file path (default: `./agentlens.db`) |

**Docker:**

A `Dockerfile` is included. The database file should be mounted as a volume for persistence:

```bash
docker run -d \
  -p 3000:3000 \
  -e AGENTLENS_API_KEY=your-secret-key \
  -e CORS_ORIGINS=https://your-dashboard.example.com \
  -v agentlens-data:/app/backend/agentlens.db \
  agentlens
```

**Process managers:** The server handles SIGTERM gracefully, so it works with PM2, systemd, Docker, and Kubernetes without special configuration.

### Scaling Considerations

AgentLens uses SQLite, which means:
- **Single-writer:** Only one process can write at a time (WAL mode allows concurrent reads)
- **Single-node:** No built-in clustering or replication
- **Suitable for:** Small-to-medium deployments (thousands of sessions/day)
- **Not suitable for:** High-throughput multi-node deployments (consider migrating to PostgreSQL)

For higher throughput, run behind a reverse proxy (nginx) and consider read replicas via SQLite's WAL file.

## Data Flow

1. **Ingestion:** SDK calls `POST /events` with batched events → backend validates, assigns IDs, inserts into SQLite
2. **Session lifecycle:** `start_session()` creates a row in `sessions`; events reference it; `end_session()` updates status and computes token totals
3. **Analytics:** Dashboard and SDK query aggregate endpoints (`/analytics`, `/scorecards`, `/errors`) which run SQL aggregations on the fly
4. **Alerting:** Rules are evaluated on-demand via `POST /alerts/evaluate` or by the correlation scheduler; results stored as alert events
5. **Cost tracking:** Token counts from events × model pricing table = per-session costs, computed at query time

## Security Model

- **API key auth:** Single shared key (constant-time comparison). No per-user auth — designed for internal/team use.
- **Rate limiting:** Per-IP, separate limits for API reads vs. event ingestion.
- **Helmet:** Sets security headers (CSP, X-Frame-Options, etc.)
- **No auth in dev mode:** If `AGENTLENS_API_KEY` is unset, all endpoints are open (logged as warning on startup).
