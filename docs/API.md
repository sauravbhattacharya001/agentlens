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
- [Anomalies](#anomalies)
- [Budgets](#budgets)
- [Session Replay](#session-replay)
- [SLA Targets](#sla-targets)
- [Retention](#retention)
- [Health Check](#health-check)
- [Session Diff](#session-diff)
- [Cost Forecasting](#cost-forecasting)
- [Agent Scorecards](#agent-scorecards)

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

## Anomalies

Statistical outlier detection for agent sessions. Computes z-scores across multiple dimensions (tokens, duration, event count, errors) and flags sessions that exceed configurable thresholds.

### `GET /anomalies`

List detected anomalies across all sessions.

| Parameter | Type | Description |
|-----------|------|-------------|
| `threshold` | float | Z-score threshold for flagging (default: 2) |
| `agent` | string | Filter by agent name |
| `limit` | int | Max anomalies to return (default: 50) |

**Response:**

```json
{
  "anomalies": [
    {
      "session_id": "abc123",
      "agent_name": "my-agent",
      "severity": "high",
      "maxZScore": 3.45,
      "dimensions": {
        "totalTokens": { "value": 75000, "zScore": 3.45 }
      }
    }
  ],
  "baselines": {
    "totalTokens": { "mean": 500, "stddev": 120 },
    "duration_ms": { "mean": 30000, "stddev": 5000 },
    "eventCount": { "mean": 10, "stddev": 3 },
    "errorCount": { "mean": 0.5, "stddev": 0.8 },
    "sampleSize": 100
  },
  "total": 3
}
```

Severity levels: `low` (z ≥ 2), `medium` (z ≥ 2), `high` (z ≥ 3), `critical` (z ≥ 4).

### `GET /anomalies/stats`

Baseline statistics used for anomaly detection.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent` | string | Filter by agent name |

### `GET /anomalies/session/:id`

Anomaly report for a single session. Returns z-scores for all dimensions regardless of threshold.

**Response includes:** `session_id`, `isAnomaly` (bool), `dimensions` (with z-scores), `baselines`.

### `POST /anomalies/scan`

Trigger a full scan and return results.

**Body:**

```json
{
  "threshold": 2.5,
  "agent": "my-agent",
  "limit": 20
}
```

---

## Budgets

Set and track spending limits per agent or globally. Budgets have a period (daily/weekly/monthly/total) and a USD limit. Real-time spend is calculated using model pricing.

### `GET /budgets`

List all budgets with current spend and status.

### `GET /budgets/:scope`

Get budgets for a specific scope. Scope must be `"global"` or `"agent:<name>"`.

### `GET /budgets/check/:sessionId`

Check if a session's agent is over budget. Returns budget status for all applicable budgets (agent-specific and global).

**Response:**

```json
{
  "session_id": "abc123",
  "agent_name": "my-agent",
  "budgets": [
    {
      "scope": "agent:my-agent",
      "period": "monthly",
      "limit_usd": 50.0,
      "current_spend": 32.15,
      "usage_pct": 64.3,
      "status": "ok"
    }
  ],
  "any_exceeded": false,
  "any_warning": false
}
```

### `PUT /budgets`

Create or update a budget.

**Body:**

```json
{
  "scope": "agent:my-agent",
  "period": "monthly",
  "limit_usd": 50.0,
  "warn_pct": 80
}
```

| Field | Type | Description |
|-------|------|-------------|
| `scope` | string | `"global"` or `"agent:<name>"` |
| `period` | string | `"daily"`, `"weekly"`, `"monthly"`, or `"total"` |
| `limit_usd` | float | Budget limit in USD (must be positive) |
| `warn_pct` | float | Warning threshold percentage (0-100, default: 80) |

### `DELETE /budgets/:scope/:period`

Delete a specific budget by scope and period.

### `DELETE /budgets/:scope`

Delete all budgets for a scope.

---

## Session Replay

Step-by-step event playback for debugging agent sessions. Events are returned as timed frames with delay calculations for simulating real-time playback.

### `GET /replay/:sessionId`

Full replay with timing data and categorized frames.

| Parameter | Type | Description |
|-----------|------|-------------|
| `speed` | float | Playback speed multiplier (0.1-100, default: 1) |
| `maxDelay` | int | Maximum delay between frames in ms (default: 30000) |
| `from` | int | Start frame index (inclusive) |
| `to` | int | End frame index (exclusive) |

**Response:**

```json
{
  "session": {
    "session_id": "abc123",
    "agent_name": "my-agent",
    "started_at": "2026-03-01T10:00:00Z",
    "ended_at": "2026-03-01T10:05:00Z",
    "status": "completed"
  },
  "replay": {
    "speed": 1,
    "max_delay_ms": 30000,
    "total_frames": 15,
    "total_duration_ms": 45000,
    "speed_recommendation": "1x",
    "frames": [
      {
        "index": 0,
        "event_id": "evt1",
        "event_type": "llm_call",
        "category": "llm_call",
        "timestamp": "2026-03-01T10:00:01Z",
        "delay_ms": 0,
        "elapsed_ms": 0,
        "model": "gpt-4o",
        "tokens_in": 500,
        "tokens_out": 200,
        "duration_ms": 1500
      }
    ]
  }
}
```

Event categories: `llm_call`, `tool_use`, `error`, `decision`, `generic`.

### `GET /replay/:sessionId/frame/:index`

Random-access to a single replay frame.

**Response includes:** `frame` (the frame object), `total_frames`, `has_next`, `has_previous`.

### `GET /replay/:sessionId/summary`

Lightweight replay stats without full frame data.

**Response includes:** `total_frames`, `total_duration_ms`, `event_types`, `categories`, `models_used`, `total_tokens_in`, `total_tokens_out`, `avg_delay_ms`, `max_delay_ms`, `speed_recommendation`.

---

## SLA Targets

Define and monitor Service Level Agreement targets per agent. Supports latency percentiles, error rates, token averages, and throughput metrics.

### `GET /sla/targets`

List all SLA targets, optionally filtered by agent.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_name` | string | Filter by agent name |

### `PUT /sla/targets`

Create or update an SLA target.

**Body:**

```json
{
  "agent_name": "my-agent",
  "metric": "p95_latency_ms",
  "threshold": 5000,
  "comparison": "lte"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `agent_name` | string | Agent identifier (max 128 chars) |
| `metric` | string | One of: `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms`, `error_rate_pct`, `avg_tokens_in`, `avg_tokens_out`, `max_duration_ms`, `min_throughput` |
| `threshold` | float | Numeric threshold value |
| `comparison` | string | `lte`, `gte`, `lt`, `gt`, `eq` (default: `lte`) |

### `DELETE /sla/targets`

Delete an SLA target.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_name` | string | Agent name (required) |
| `metric` | string | Metric to remove (required) |

### `POST /sla/check`

Check SLA compliance for an agent over a time window.

**Body:**

```json
{
  "agent_name": "my-agent",
  "window_hours": 24
}
```

**Response:**

```json
{
  "agent_name": "my-agent",
  "window_start": "2026-03-13T10:00:00Z",
  "window_end": "2026-03-14T10:00:00Z",
  "metrics": {
    "p95_latency_ms": 3200,
    "error_rate_pct": 2.5
  },
  "violations": [
    {
      "metric": "error_rate_pct",
      "threshold": 1.0,
      "actual": 2.5,
      "comparison": "lte"
    }
  ],
  "compliance_pct": 50
}
```

### `POST /sla/snapshot`

Save a compliance snapshot for historical tracking.

**Body:** Same as `/sla/check`.

### `GET /sla/history`

Retrieve historical SLA snapshots.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_name` | string | Filter by agent name |
| `limit` | int | Max results (default: 50) |
| `offset` | int | Pagination offset |

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

---

## Session Diff

Compare two sessions side-by-side with event-level alignment using LCS (Longest Common Subsequence).

### `GET /diff?baseline=ID&candidate=ID`

Compute a structured diff between two sessions.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `baseline` | string | Yes | Session ID of the baseline session |
| `candidate` | string | Yes | Session ID of the candidate session |

**Response:**

```json
{
  "baseline": {
    "session_id": "abc123",
    "agent_name": "my-agent",
    "status": "completed",
    "event_count": 12,
    "tokens_in": 1500,
    "tokens_out": 800,
    "duration_ms": 4500
  },
  "candidate": {
    "session_id": "def456",
    "agent_name": "my-agent",
    "status": "completed",
    "event_count": 14,
    "tokens_in": 1800,
    "tokens_out": 900,
    "duration_ms": 5200
  },
  "deltas": {
    "tokens_in": 300,
    "tokens_out": 100,
    "tokens_total": 400,
    "duration_ms": 700,
    "event_count": 2
  },
  "tools": {
    "added": ["new_tool"],
    "removed": [],
    "common": ["search", "calculator"],
    "baseline_counts": { "search": 3, "calculator": 1 },
    "candidate_counts": { "search": 4, "calculator": 1, "new_tool": 2 }
  },
  "models": {
    "baseline": { "gpt-4": 8, "gpt-3.5-turbo": 4 },
    "candidate": { "gpt-4": 10, "gpt-3.5-turbo": 4 }
  },
  "event_types": {
    "added": [],
    "removed": []
  },
  "alignment": [
    {
      "label": "llm_call",
      "status": "matched",
      "changes": {}
    },
    {
      "label": "tool_call:search",
      "status": "modified",
      "changes": { "tokens_in": "120→150" }
    },
    {
      "label": "tool_call:new_tool",
      "status": "added",
      "changes": {}
    }
  ],
  "similarity": 0.85
}
```

**Alignment status values:** `matched` (identical), `modified` (same type but metrics changed), `added` (only in candidate), `removed` (only in baseline).

**Error Responses:**

| Status | Condition |
|--------|-----------|
| `400` | Missing baseline/candidate, invalid ID format, or same ID for both |
| `404` | Baseline or candidate session not found |

---

## Cost Forecasting

Project future costs based on historical usage patterns.

### `GET /forecast`

Get cost and usage forecasts with trend analysis.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Historical lookback window (1-365) |
| `forecastDays` | number | 7 | Number of days to forecast (1-90) |
| `agent` | string | — | Filter by agent name |
| `model` | string | — | Filter by model name |

**Response:**

```json
{
  "historical": [
    { "date": "2024-01-15", "tokens_in": 50000, "tokens_out": 20000, "tokens_total": 70000, "event_count": 150, "session_count": 12, "cost": 1.85 }
  ],
  "forecast": [
    { "date": "2024-02-15", "tokens_total": 72000, "cost": 1.92, "confidence": "medium" }
  ],
  "trend": {
    "direction": "increasing",
    "daily_avg_tokens": 68000,
    "daily_avg_cost": 1.80
  },
  "meta": {
    "lookback_days": 30,
    "forecast_days": 7,
    "agent": null,
    "model": null
  }
}
```

### `GET /forecast/budget`

Check current spending against budget limits.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window |
| `agent` | string | — | Filter by agent |

### `GET /forecast/spending-summary`

Get a spending summary with model-level cost breakdowns.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window |
| `agent` | string | — | Filter by agent |

---

## Agent Scorecards

Per-agent performance grading with composite scores, letter grades, and trend sparklines.

### `GET /scorecards`

List scorecards for all agents.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days (1-365) |

**Response:**

```json
{
  "scorecards": [
    {
      "agent_name": "my-agent",
      "composite_score": 87.5,
      "grade": "A-",
      "grade_color": "#22c55e",
      "metrics": {
        "total_sessions": 150,
        "completed": 140,
        "errors": 5,
        "success_rate": 93.33,
        "error_rate": 3.33,
        "avg_tokens": 12500,
        "total_tokens": 1875000,
        "avg_latency_ms": 850.5,
        "max_latency_ms": 4200.0
      },
      "first_seen": "2024-01-01T00:00:00.000Z",
      "last_seen": "2024-01-31T23:59:00.000Z",
      "trend": [
        { "week": "2024-03", "sessions": 35, "errorRate": 2.86 },
        { "week": "2024-04", "sessions": 42, "errorRate": 4.76 }
      ]
    }
  ],
  "meta": {
    "days": 30,
    "generated_at": "2024-02-01T00:00:00.000Z",
    "agent_count": 5
  }
}
```

**Composite score formula:** 40% success rate + 30% latency efficiency + 30% volume (log scale). Grades: A+ (≥95) through F (<50).

### `GET /scorecards/:agent`

Detailed scorecard for a single agent, including model usage breakdown and tool usage stats.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days (1-365) |

**Response:** Same structure as the list endpoint but for a single agent, with additional `models` and `tools` breakdowns.

**Error Responses:**

| Status | Condition |
|--------|-----------|
| `404` | No data for the specified agent in the given time range |
