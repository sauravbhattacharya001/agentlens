# AgentLens API Reference

Complete REST API documentation for the AgentLens backend server.

All endpoints require API key authentication via the `x-api-key` header unless otherwise noted.

**Base URL:** `http://localhost:3000` (default)

---

## Table of Contents

- [Events](#events)
- [Sessions](#sessions)
- [Analytics](#analytics)
- [Pricing & Costs](#pricing--costs)
- [Alert Rules](#alert-rules)
- [Annotations](#annotations)
- [Tags](#tags)
- [Bookmarks](#bookmarks)
- [Error Analytics](#error-analytics)
- [Dependencies](#dependencies)
- [Correlations](#correlations)
- [Correlation Scheduler](#correlation-scheduler)
- [Baselines](#baselines)
- [Leaderboard](#leaderboard)
- [Postmortem](#postmortem)
- [Webhooks](#webhooks)
- [Retention](#retention)
- [Health Check](#health-check)

---

## Events

### `POST /events`

Ingest one or more agent events (batched).

**Body:**

```json
{
  "session_id": "abc123",
  "events": [
    {
      "event_type": "llm_call",
      "input_data": { "prompt": "..." },
      "output_data": { "response": "..." },
      "model": "gpt-4",
      "tokens_in": 100,
      "tokens_out": 50,
      "reasoning": "Answered user question directly",
      "duration_ms": 1500.0
    }
  ]
}
```

**Response:** `201 Created` with event IDs.

---

## Sessions

### `GET /sessions`

List all sessions. Supports pagination.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max sessions to return (default: 50) |
| `offset` | int | Pagination offset |

### `GET /sessions/search`

Search sessions with rich filtering.

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Full-text search query |
| `agent_name` | string | Filter by agent name |
| `status` | string | Filter by status (active, completed, error) |
| `after` | string | ISO-8601 start date |
| `before` | string | ISO-8601 end date |
| `limit` | int | Max results |

### `GET /sessions/:id`

Get a single session with its events.

### `GET /sessions/:id/events`

Get all events for a session.

### `GET /sessions/:id/events/search`

Search events within a specific session.

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Full-text search |
| `event_type` | string | Filter by type (llm_call, tool_call, generic) |
| `model` | string | Filter by model |
| `min_tokens` | int | Minimum token count |
| `has_tools` | bool | Only events with tool calls |

### `GET /sessions/:id/export`

Export session data as JSON or CSV.

| Parameter | Type | Description |
|-----------|------|-------------|
| `format` | string | Export format: `json` or `csv` (default: json) |

### `GET /sessions/:id/explain`

Generate a human-readable explanation of agent behavior for a session.

### `POST /sessions/compare`

Compare two sessions side-by-side.

**Body:**

```json
{
  "session_a": "abc123",
  "session_b": "def456"
}
```

**Response:** Metrics deltas, event breakdowns, tool usage diffs, and shared patterns.

---

## Analytics

### `GET /analytics`

Aggregate statistics across all sessions. Returns totals, averages, and breakdowns by model and event type.

### `GET /analytics/performance`

Performance analytics with latency and throughput stats.

### `GET /analytics/heatmap`

Day-of-week × hour-of-day activity matrix for visualizing usage patterns.

### `GET /analytics/cache`

Cache statistics for monitoring backend performance.

---

## Pricing & Costs

### `GET /pricing`

List all configured model pricing (per 1M tokens, USD).

### `PUT /pricing`

Update pricing for one or more models.

**Body:**

```json
{
  "model-name": {
    "input_cost_per_1m": 5.00,
    "output_cost_per_1m": 15.00
  }
}
```

### `DELETE /pricing/:model`

Remove custom pricing for a specific model (reverts to defaults).

### `GET /pricing/costs/:sessionId`

Calculate detailed cost breakdown for a session, including per-event and per-model costs.

---

## Alert Rules

### `GET /alerts/rules`

List all alert rules.

### `POST /alerts/rules`

Create a new alert rule.

**Body:**

```json
{
  "name": "High Error Rate",
  "metric": "error_rate",
  "condition": "gt",
  "threshold": 0.1,
  "description": "Fires when error rate exceeds 10%",
  "enabled": true
}
```

### `PUT /alerts/rules/:ruleId`

Update an existing alert rule.

### `DELETE /alerts/rules/:ruleId`

Delete an alert rule.

### `POST /alerts/evaluate`

Evaluate all enabled rules against recent data and generate alert events.

### `GET /alerts/events`

List triggered alert events.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max events to return |
| `acknowledged` | bool | Filter by acknowledgment status |

### `PUT /alerts/events/:alertId/acknowledge`

Acknowledge a triggered alert.

### `GET /alerts/metrics`

List available metrics that can be used in alert rules.

---

## Annotations

### `POST /sessions/:id/annotations`

Add a timestamped annotation to a session.

**Body:**

```json
{
  "text": "Latency spike detected at step 5",
  "type": "warning",
  "author": "monitoring-bot"
}
```

### `GET /sessions/:id/annotations`

List all annotations for a session.

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Filter by annotation type |

### `PUT /sessions/:id/annotations/:annId`

Update an annotation.

### `DELETE /sessions/:id/annotations/:annId`

Delete an annotation.

### `GET /annotations`

List recent annotations across all sessions.

---

## Tags

### `GET /sessions/tags`

List all unique tags across all sessions.

### `GET /sessions/by-tag/:tag`

List sessions that have a specific tag.

### `GET /sessions/:id/tags`

Get tags for a specific session.

### `POST /sessions/:id/tags`

Add tags to a session.

**Body:**

```json
{
  "tags": ["production", "v2.0", "critical"]
}
```

### `DELETE /sessions/:id/tags`

Remove tags from a session.

**Body:**

```json
{
  "tags": ["v2.0"]
}
```

---

## Bookmarks

### `GET /bookmarks`

List all bookmarked sessions.

### `GET /bookmarks/:sessionId`

Check if a specific session is bookmarked.

### `PUT /bookmarks/:sessionId`

Add or update a bookmark for a session.

**Body:**

```json
{
  "note": "Interesting edge case worth reviewing"
}
```

### `DELETE /bookmarks/:sessionId`

Remove a bookmark from a session.

---

## Error Analytics

### `GET /errors`

Full error analytics dashboard data. Returns error rates, trends, and breakdowns.

### `GET /errors/summary`

Lightweight error summary with counts and rates.

### `GET /errors/by-type`

Error breakdown by event type (llm_call, tool_call, etc.).

### `GET /errors/by-model`

Error breakdown by model.

### `GET /errors/by-agent`

Error breakdown by agent name.

---

## Dependencies

### `GET /dependencies`

Full service dependency map showing which tools/services each agent uses.

### `GET /dependencies/critical`

Identify critical dependencies (high usage, single points of failure).

### `GET /dependencies/agents`

Per-agent dependency profiles.

### `GET /dependencies/co-occurrence`

Service co-occurrence patterns — which services are commonly used together.

### `GET /dependencies/trend/:service`

Usage trend over time for a specific service.

---

## Correlations

### `POST /correlations/rules`

Create a correlation rule for grouping related events.

### `GET /correlations/rules`

List all correlation rules.

### `GET /correlations/rules/:ruleId`

Get a specific correlation rule.

### `PATCH /correlations/rules/:ruleId`

Update a correlation rule.

### `DELETE /correlations/rules/:ruleId`

Delete a correlation rule.

### `POST /correlations/rules/:ruleId/run`

Execute a correlation rule against existing data to generate groups.

### `GET /correlations/groups`

List all correlation groups.

### `GET /correlations/groups/:groupId`

Get details of a specific correlation group.

### `DELETE /correlations/groups/:groupId`

Delete a correlation group.

### `GET /correlations/stats`

Correlation statistics (total rules, groups, recent activity).

### `GET /correlations/event/:eventId`

Get all correlation groups that include a specific event.

---

## Correlation Scheduler

### `GET /correlations/stream`

Server-Sent Events (SSE) stream for real-time correlation updates.

### `POST /correlations/schedules`

Create a scheduled correlation rule execution.

### `GET /correlations/schedules`

List all scheduled correlation executions.

### `DELETE /correlations/schedules/:ruleId`

Remove a scheduled correlation.

### `POST /correlations/scheduler/start`

Start the correlation scheduler.

### `POST /correlations/scheduler/stop`

Stop the correlation scheduler.

### `GET /correlations/scheduler/status`

Get the current scheduler status (running/stopped, next execution time).

---

## Baselines

### `GET /baselines`

List all agent baselines.

### `GET /baselines/:agentName`

Get the recorded baseline for a specific agent.

### `POST /baselines/record`

Record a new baseline from a session.

**Body:**

```json
{
  "session_id": "abc123"
}
```

### `POST /baselines/check`

Compare a session against its agent's baseline. Returns per-metric deltas with classification (normal, warning, regression, improvement).

**Body:**

```json
{
  "session_id": "def456"
}
```

### `DELETE /baselines/:agentName`

Reset the baseline for an agent.

---

## Leaderboard

### `GET /leaderboard`

Agent performance leaderboard ranked by configurable metrics.

| Parameter | Type | Description |
|-----------|------|-------------|
| `sort_by` | string | Metric to rank by |
| `min_sessions` | int | Minimum sessions to qualify (default: 2) |
| `limit` | int | Max agents to return |

---

## Postmortem

### `POST /postmortem/:sessionId`

Generate a postmortem report for a session that experienced errors. Includes timeline, root cause analysis, and recommendations.

### `GET /postmortem/candidates`

List sessions with enough errors to warrant a postmortem analysis.

---

## Webhooks

### `GET /webhooks`

List all configured webhooks.

### `POST /webhooks`

Create a new webhook.

**Body:**

```json
{
  "url": "https://example.com/webhook",
  "events": ["session.completed", "alert.triggered"],
  "secret": "optional-signing-secret"
}
```

### `PUT /webhooks/:webhookId`

Update a webhook configuration.

### `DELETE /webhooks/:webhookId`

Delete a webhook.

### `POST /webhooks/:webhookId/test`

Send a test payload to a webhook to verify connectivity.

### `GET /webhooks/:webhookId/deliveries`

View delivery history for a webhook (success/failure, response codes).

---

## Retention

### `GET /retention/config`

Get current data retention settings.

### `PUT /retention/config`

Update retention settings.

**Body:**

```json
{
  "max_age_days": 30,
  "max_sessions": 10000,
  "exempt_tags": ["production"],
  "auto_purge": true
}
```

### `GET /retention/stats`

Database size and age statistics.

### `POST /retention/purge`

Manually purge old data based on retention policy.

| Parameter | Type | Description |
|-----------|------|-------------|
| `dry_run` | bool | Preview what would be purged without deleting |

---

## Health Check

### `GET /health`

Health check endpoint. Returns server status, uptime, and version.

**Response:**

```json
{
  "status": "ok",
  "uptime": 3600,
  "version": "1.0.0"
}
```

---

## Authentication

All endpoints require an API key passed via the `x-api-key` header:

```bash
curl -H "x-api-key: your-key" http://localhost:3000/sessions
```

## Rate Limiting

The server applies rate limiting per IP:

- **API endpoints** (sessions, analytics, pricing, etc.): Standard rate limit
- **Event ingestion** (`POST /events`): Higher rate limit for burst ingestion

## Error Responses

All errors follow a consistent format:

```json
{
  "error": "Description of what went wrong"
}
```

| Status Code | Description |
|-------------|-------------|
| `400` | Bad request (invalid parameters) |
| `401` | Unauthorized (missing or invalid API key) |
| `404` | Resource not found |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
