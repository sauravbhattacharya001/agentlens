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
- [Agent Profiler](#agent-profiler)
- [Command Center](#command-center)
- [Collaboration](#collaboration)
- [Competency Map](#competency-map)
- [Operational Tempo](#operational-tempo)
- [Auto-Triage](#auto-triage)

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

### `GET /analytics/costs`

Aggregate cost breakdown by model and over time. Joins event token data with `model_pricing` to compute estimated costs across all sessions.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Look-back window in days (1–365) |

**Response:**

```json
{
  "model_costs": [
    {
      "model": "gpt-4",
      "call_count": 120,
      "total_tokens_in": 50000,
      "total_tokens_out": 30000,
      "input_cost": 0.25,
      "output_cost": 0.45,
      "total_cost": 0.70
    }
  ],
  "daily_costs": [
    { "day": "2026-03-20", "input_cost": 0.10, "output_cost": 0.15, "total": 0.25, "calls": 40 }
  ],
  "totals": {
    "total_cost": 1.50,
    "input_cost": 0.60,
    "output_cost": 0.90,
    "total_calls": 200
  },
  "unmatched_models": ["custom-model"],
  "days": 30
}
```

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

### `GET /sla/summary`

High-level SLA compliance summary across all agents. Returns each agent's target count and latest compliance check.

**Response:**

```json
{
  "agents": [
    {
      "agent_name": "my-agent",
      "target_count": 3,
      "latest_check": {
        "compliance_pct": 100,
        "violation_count": 0,
        "checked_at": "2026-03-20T10:00:00.000Z",
        "window_start": "2026-03-13T10:00:00.000Z",
        "window_end": "2026-03-20T10:00:00.000Z"
      }
    }
  ]
}
```

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

---

## Agent Profiler

Builds behavioral fingerprints for agents and detects drift from established patterns. Tracks tool-call distribution, response-time patterns, error affinity, token-usage shape, and event-type mix. Compares recent windows against historical baselines using Jensen-Shannon divergence.

**Base path:** `/profiler`

### List All Agent Profiles

```
GET /profiler
```

Returns behavioral profiles for all agents with drift severity classification.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Historical lookback window (max 90) |
| `recent` | number | 7 | Recent window for drift comparison (max `days`) |

**Response:**

```json
{
  "profiles": [
    {
      "agent": "my-agent",
      "status": "medium",
      "overallDrift": 0.1523,
      "dimensions": {
        "eventMix": { "drift": 0.0812, "severity": "stable" },
        "toolUsage": { "drift": 0.2341, "severity": "medium" },
        "tokenUsage": { "drift": 0.1205, "severity": "medium" },
        "duration": { "drift": 0.0543, "severity": "stable" },
        "errorRate": { "drift": 0.0100, "severity": "stable" }
      },
      "sessionCount": 150,
      "recentSessionCount": 28,
      "baseline": { "avgTokens": 4200, "avgDuration": 12500, "errorRate": 0.0200 },
      "recent": { "avgTokens": 4800, "avgDuration": 11800, "errorRate": 0.0350 }
    }
  ],
  "meta": { "days": 30, "recentDays": 7, "agentCount": 5 }
}
```

**Drift Severity Levels:**

| Level | JSD Threshold | Meaning |
|-------|--------------|----------|
| `stable` | < 0.10 | Behavior consistent with baseline |
| `medium` | 0.10 – 0.25 | Notable behavioral shift |
| `high` | 0.25 – 0.40 | Significant drift, investigate |
| `critical` | ≥ 0.40 | Major behavioral change |

### Get Agent Profile Detail

```
GET /profiler/:agent
```

Returns a detailed behavioral profile for one agent, including daily breakdowns.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Historical lookback window (max 90) |

**Response:**

```json
{
  "agent": "my-agent",
  "profile": {
    "sessionCount": 150,
    "avgTokens": 4500,
    "avgDuration": 12000,
    "errorRate": 0.025,
    "eventTypeDist": { "llm_call": 0.6, "tool_call": 0.3, "error": 0.1 },
    "toolCallDist": { "web_search": 0.5, "file_read": 0.3, "code_exec": 0.2 },
    "p50Duration": 10000,
    "p95Duration": 35000,
    "p50Tokens": 3800,
    "p95Tokens": 12000
  },
  "daily": [
    { "date": "2026-04-15", "sessionCount": 12, "avgTokens": 4300, "..." : "..." }
  ],
  "meta": { "days": 30, "totalSessions": 150 }
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------||
| `400` | Invalid agent name format |
| `404` | Agent not found or no sessions in range |

### Get Drift Timeline

```
GET /profiler/:agent/drift
```

Returns a time series of drift scores using a sliding window compared against the historical baseline.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Historical lookback window (max 90) |
| `window` | number | 3 | Sliding window size in days (1-14) |

**Response:**

```json
{
  "agent": "my-agent",
  "baseline": { "avgTokens": 4200, "avgDuration": 12500 },
  "timeline": [
    { "date": "2026-04-10", "eventDrift": 0.0523, "toolDrift": 0.0812, "severity": "stable", "windowSize": 15 },
    { "date": "2026-04-11", "eventDrift": 0.1205, "toolDrift": 0.2341, "severity": "medium", "windowSize": 18 }
  ]
}
```

### Force Profile Snapshot

```
POST /profiler/snapshot
```

Forces an immediate profile snapshot for all agents (last 30 days).

**Response:**

```json
{
  "snapshots": [
    { "agent": "my-agent", "profile": { "..." : "..." }, "timestamp": "2026-04-18T17:00:00.000Z" }
  ],
  "count": 5
}
```

---

## Command Center

Unified activity feed aggregating alerts, anomalies, budget warnings, and session health into a single prioritized stream.

**Base path:** `/command-center`

### Activity Feed

```
GET /command-center/feed
```

Returns a prioritized stream of recent activity across all subsystems.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | number | 50 | Maximum items to return |
| `days` | number | 7 | Lookback window in days |

**Response:**

```json
{
  "feed": [
    {
      "type": "alert",
      "severity": "high",
      "title": "Error rate spike",
      "timestamp": "2026-04-18T16:30:00.000Z",
      "details": { "..." : "..." }
    }
  ],
  "meta": { "total": 23, "days": 7 }
}
```

### Summary

```
GET /command-center/summary
```

Returns a quick stats overview of system health.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 7 | Lookback window in days |

**Response:**

```json
{
  "activeSessions": 12,
  "totalAlerts": 3,
  "unacknowledgedAlerts": 1,
  "anomalyCount": 2,
  "budgetWarnings": 0,
  "errorRate": 0.035,
  "period": { "days": 7, "from": "2026-04-11", "to": "2026-04-18" }
}
```

---

## Collaboration

Multi-agent collaboration analysis — detects teamwork patterns, handoff quality, communication bottlenecks, delegation chains, workload balance, and collective intelligence across sessions with 2+ agents.

### `GET /collaboration`

List multi-agent sessions with collaboration scores.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days |
| `limit` | number | 50 | Max sessions to return (max 200) |

**Response:**

```json
[
  {
    "session_id": "sess-abc123",
    "agent_count": 3,
    "event_count": 47,
    "teamwork_score": 82.5,
    "grade": "strong",
    "collaboration_pattern": "orchestrated",
    "first_event": "2026-04-10T08:00:00Z",
    "last_event": "2026-04-10T08:15:00Z"
  }
]
```

**Collaboration Patterns:** `solo`, `orchestrated`, `pipeline`, `hierarchical`, `swarm`, `peer_to_peer`

**Grades:** `elite` (≥90), `strong` (≥75), `functional` (≥60), `struggling` (≥40), `dysfunctional` (<40)

### `GET /collaboration/:session_id`

Detailed collaboration analysis for a single session. Returns the full breakdown including all 6 scoring engines.

**Response:**

```json
{
  "session_id": "sess-abc123",
  "agent_count": 3,
  "event_count": 47,
  "teamwork_score": 82.5,
  "grade": "strong",
  "collaboration_pattern": "orchestrated",
  "gini_coefficient": 0.1523,
  "coordination_overhead_pct": 34.2,
  "abandoned_delegations": 0,
  "handoffs": [
    {
      "source_agent": "planner",
      "target_agent": "coder",
      "timestamp": "2026-04-10T08:02:00Z",
      "latency_ms": 230,
      "context_loss": 0.02,
      "verdict": "clean",
      "quality": 100
    }
  ],
  "bottlenecks": [],
  "workload": [
    { "agent_id": "planner", "event_count": 15, "load_fraction": 0.32, "status": "balanced" },
    { "agent_id": "coder", "event_count": 20, "load_fraction": 0.43, "status": "balanced" },
    { "agent_id": "reviewer", "event_count": 12, "load_fraction": 0.25, "status": "balanced" }
  ],
  "engines": [
    { "engine": "Handoff Quality", "score": 95.0 },
    { "engine": "Communication Bottleneck", "score": 100.0 },
    { "engine": "Delegation Chain", "score": 100.0 },
    { "engine": "Workload Balance", "score": 84.8 },
    { "engine": "Teamwork Rhythm", "score": 55.0 },
    { "engine": "Collective Intelligence", "score": 75.0 }
  ]
}
```

**Scoring Engines (weighted composite):**
- **Handoff Quality** (20%) — latency and context loss at each agent-to-agent handoff
- **Communication Bottleneck** (15%) — fan-in congestion detection
- **Delegation Chain** (15%) — abandoned delegation penalty
- **Workload Balance** (20%) — Gini coefficient of event distribution
- **Teamwork Rhythm** (15%) — coordination overhead vs productive work
- **Collective Intelligence** (15%) — cross-agent error correction rate

### `GET /collaboration/:session_id/handoffs`

Handoff timeline and quality analysis for a session.

**Response:**

```json
{
  "session_id": "sess-abc123",
  "handoff_count": 4,
  "handoff_quality_score": 95.0,
  "handoffs": [
    {
      "source_agent": "planner",
      "target_agent": "coder",
      "timestamp": "2026-04-10T08:02:00Z",
      "latency_ms": 230,
      "context_loss": 0.02,
      "verdict": "clean",
      "quality": 100
    }
  ]
}
```

**Handoff Verdicts:** `clean` (loss ≤5%), `acceptable` (loss ≤20%), `lossy` (loss ≤50% or latency >5s), `failed` (loss >50% or latency >10s)

### `GET /collaboration/:session_id/bottlenecks`

Communication bottleneck analysis — identifies agents with high fan-in that may be blocking other agents.

**Response:**

```json
{
  "session_id": "sess-abc123",
  "bottleneck_count": 1,
  "bottleneck_score": 80.0,
  "bottlenecks": [
    {
      "agent_id": "reviewer",
      "fan_in": 4,
      "fan_out": 1,
      "severity": "moderate",
      "waiting_agents": ["coder-1", "coder-2", "coder-3", "tester"]
    }
  ]
}
```

### `POST /collaboration/analyze`

Trigger collaboration analysis for a session by ID, or supply events directly.

**Body (by session ID):**

```json
{ "session_id": "sess-abc123" }
```

**Body (inline events):**

```json
{
  "events": [
    { "timestamp": "...", "agent_id": "planner", "event_type": "handoff", "target_agent": "coder" }
  ]
}
```

---

## Competency Map

Autonomous skill profiling for AI agents. Analyzes behavioral data across 6 dimensions (reliability, speed, efficiency, tool mastery, error recovery, consistency) to build skill profiles, identify strengths/weaknesses, and generate optimal task-routing recommendations.

### `GET /competency`

Fleet-wide competency overview — all agents ranked with dimension scores and task routing suggestions.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days |

**Response:**

```json
{
  "competency_map": [
    {
      "agent_name": "claude-3.5-sonnet",
      "competency_score": 87.3,
      "grade": "A-",
      "grade_color": "#22c55e",
      "dimensions": {
        "reliability": { "score": 92.1, "percentile": 85.0 },
        "speed": { "score": 78.4, "percentile": 60.0 },
        "efficiency": { "score": 88.2, "percentile": 90.0 },
        "tool_mastery": { "score": 81.5, "percentile": 75.0 },
        "error_recovery": { "score": 80.0, "percentile": 50.0 },
        "consistency": { "score": 91.0, "percentile": 95.0 }
      },
      "strengths": ["reliability", "consistency"],
      "weaknesses": ["speed", "error_recovery"],
      "recommended_tasks": ["long-running critical workflows", "steady-state monitoring"],
      "session_count": 250,
      "last_active": "2026-04-18T12:00:00Z"
    }
  ],
  "routing_suggestions": [
    {
      "task_type": "long-running critical workflows",
      "best_agent": "claude-3.5-sonnet",
      "confidence": 87.3,
      "reason": "Highest reliability (92.1) and consistency (91.0)",
      "alternatives": ["gpt-4o", "gemini-pro"]
    }
  ],
  "meta": { "days": 30, "generated_at": "2026-04-18T12:00:00Z", "agent_count": 5 }
}
```

**Dimensions (weighted composite score):**
- **Reliability** (25%) — success rate, volume-adjusted
- **Speed** (15%) — inverse latency, log-normalized (200ms=100, 10s=0)
- **Efficiency** (20%) — completed sessions per million tokens
- **Tool Mastery** (15%) — breadth and volume of tool usage
- **Error Recovery** (10%) — sessions with errors that still completed
- **Consistency** (15%) — inverse coefficient of variation of daily success rates

**Grades:** `A+` (≥95), `A` (≥90), `A-` (≥85), `B+` (≥80), `B` (≥75), `B-` (≥70), `C+` (≥65), `C` (≥60), `C-` (≥55), `D` (≥50), `F` (<50)

### `GET /competency/routing`

Autonomous routing recommendations — which agent should handle which task types.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days |

**Response:**

```json
{
  "routing_table": [
    {
      "task_pattern": "long-running critical workflows",
      "recommended_agent": "claude-3.5-sonnet",
      "confidence": 87.3,
      "fallback_agents": ["gpt-4o"],
      "reason": "Highest competency score (87.3) for this task type"
    }
  ],
  "coverage_score": 71.4,
  "meta": { "days": 30, "generated_at": "2026-04-18T12:00:00Z", "agent_count": 5 }
}
```

**Task Profiles (used for routing):**
- Long-running critical workflows (reliability ≥80, consistency ≥75)
- High-throughput batch processing (speed ≥80, efficiency ≥75)
- Complex multi-tool orchestrations (tool mastery ≥75, reliability ≥70)
- Error-prone exploratory tasks (error recovery ≥75)
- Token-sensitive operations (efficiency ≥85)
- Latency-critical real-time tasks (speed ≥85, reliability ≥70)
- Steady-state monitoring (consistency ≥80, reliability ≥70)

### `GET /competency/:agent`

Detailed competency profile for a single agent — includes growth trajectory, tool breakdown, model affinity, and peer comparison.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 30 | Lookback window in days |

**Response:**

```json
{
  "agent_name": "claude-3.5-sonnet",
  "competency_score": 87.3,
  "grade": "A-",
  "grade_color": "#22c55e",
  "dimensions": {
    "reliability": { "score": 92.1, "peer_rank": { "rank": 1, "of": 5, "score": 92.1 } }
  },
  "strengths": ["reliability", "consistency"],
  "weaknesses": ["speed", "error_recovery"],
  "recommended_tasks": ["long-running critical workflows"],
  "growth_trajectory": {
    "success_rate_trend": 0.35,
    "token_usage_trend": -12.5,
    "direction": "improving",
    "weeks_analyzed": 8
  },
  "tools": [
    { "tool": "code_search", "calls": 150, "avg_latency_ms": 340.5, "min_latency_ms": 50.0, "max_latency_ms": 2100.0 }
  ],
  "model_affinity": [
    { "model": "claude-3.5-sonnet", "calls": 200, "tokens_in": 50000, "tokens_out": 30000, "avg_latency_ms": 420.0, "efficiency_score": 78.5 }
  ],
  "peer_comparison": {
    "reliability": { "rank": 1, "of": 5, "score": 92.1 }
  },
  "weekly_trend": [
    { "week": "2026-15", "sessions": 30, "success_rate": 93.3, "avg_tokens": 4500 }
  ],
  "metrics": {
    "total_sessions": 250,
    "completed": 232,
    "errors": 8,
    "active": 3,
    "avg_tokens_in": 2500,
    "avg_tokens_out": 1800,
    "total_tokens": 1075000
  },
  "first_seen": "2026-01-15T00:00:00Z",
  "last_seen": "2026-04-18T12:00:00Z",
  "meta": { "days": 30, "generated_at": "2026-04-18T12:00:00Z" }
}
```

---

## Operational Tempo

Agent pace and rhythm analysis — measures operational cadence, detects rushing/stalling, identifies optimal tempos, and generates pace recommendations.

7 analysis engines: Cadence Profiler, Rush Detector, Stall Detector, Task Tempo Optimizer, Rhythm Regularity Scorer, Tempo Drift Tracker, and Pace Recommendation Engine.

### `GET /tempo`

Fleet-wide tempo overview — tempo score, rhythm classification, and rush/stall counts per agent.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 7 | Lookback window in days |

**Response:**

```json
{
  "period": { "days": 7, "cutoff": "2026-04-11T00:00:00Z" },
  "fleetTempoScore": 78,
  "fleetTempoHealth": "good",
  "agentCount": 3,
  "agents": [
    {
      "agent": "claude-3.5-sonnet",
      "sessionCount": 45,
      "eventCount": 380,
      "tempoScore": 85,
      "tempoHealth": "excellent",
      "cadenceCategory": "moderate",
      "rhythmScore": 72,
      "rhythmClass": "steady",
      "rushEpisodeCount": 1,
      "stallEpisodeCount": 0
    }
  ],
  "summary": { "rushing": 1, "stalling": 0, "chaotic": 0, "healthy": 2 }
}
```

**Tempo Health:** `excellent` (≥85), `good` (≥70), `fair` (≥50), `poor` (≥30), `critical` (<30)

**Cadence Categories:** `hyper-fast` (<500ms), `fast` (<2s), `moderate` (<10s), `deliberate` (<30s), `slow` (<2m), `stalled` (≥2m)

**Rhythm Classifications:** `metronome` (≥80), `steady` (≥60), `variable` (≥40), `erratic` (≥20), `chaotic` (<20)

### `GET /tempo/:agent`

Detailed tempo profile for a single agent — cadence, rhythm, rush/stall episodes, optimal tempos, and pace recommendations.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 14 | Lookback window in days |

**Response:**

```json
{
  "agent": "claude-3.5-sonnet",
  "period": { "days": 14, "cutoff": "2026-04-04T00:00:00Z" },
  "tempoScore": 85,
  "tempoHealth": "excellent",
  "cadence": {
    "eventCount": 380,
    "intervalCount": 379,
    "medianIntervalMs": 4200,
    "meanIntervalMs": 5800,
    "stddevMs": 3100,
    "p10Ms": 800,
    "p90Ms": 12000,
    "minMs": 120,
    "maxMs": 45000,
    "coeffOfVariation": 0.534,
    "tempoCategory": "moderate"
  },
  "rhythm": {
    "score": 72,
    "classification": "steady",
    "coeffOfVariation": 0.534,
    "autocorrelation": 0.312,
    "burstRatio": 0.05,
    "interpretation": "Agent maintains a reasonably steady operational rhythm"
  },
  "optimalTempos": {
    "tool-use": {
      "sampleSize": 20,
      "successRate": 85,
      "optimalPaceMs": 3500,
      "riskyFastMs": 500,
      "riskySlowMs": 15000,
      "failedMeanPaceMs": 800,
      "recommendation": "slow-down"
    }
  },
  "rushEpisodes": [],
  "stallEpisodes": [],
  "recommendations": [
    {
      "type": "all-clear",
      "priority": "low",
      "message": "Operational tempo appears healthy — no pace issues detected",
      "action": "Continue current operational patterns",
      "confidence": 0.9
    }
  ],
  "summary": { "totalEvents": 380, "totalSessions": 45, "rushCount": 0, "stallCount": 0 }
}
```

**Recommendation Types:** `pace-warning`, `loop-detection`, `rhythm-coaching`, `consistency`, `tempo-optimization`, `all-clear`

### `GET /tempo/:agent/rhythm`

Rhythm regularity timeline — windowed tempo drift tracking showing how an agent's pace changes over time.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 14 | Lookback window in days |
| `window` | number | 1 | Window size in days for drift calculation |

**Response:**

```json
{
  "agent": "claude-3.5-sonnet",
  "period": { "days": 14, "cutoff": "2026-04-04T00:00:00Z", "windowDays": 1 },
  "timeline": [
    {
      "windowStart": "2026-04-10T00:00:00Z",
      "windowEnd": "2026-04-11T00:00:00Z",
      "eventCount": 50,
      "medianPaceMs": 4200,
      "meanPaceMs": 5100,
      "rhythm": { "score": 68, "classification": "steady" },
      "driftPct": -8,
      "driftDirection": "accelerating"
    }
  ],
  "overallTrend": "stable"
}
```

**Overall Trends:** `accelerating` (avg drift <-20%), `decelerating` (avg drift >+20%), `stable`, `insufficient-data`

### `GET /tempo/:agent/anomalies`

Rushing and stalling anomaly episodes for an agent.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `days` | number | 14 | Lookback window in days |

**Response:**

```json
{
  "agent": "claude-3.5-sonnet",
  "period": { "days": 14, "cutoff": "2026-04-04T00:00:00Z" },
  "anomalies": {
    "rushing": {
      "count": 2,
      "episodes": [
        {
          "startTime": "2026-04-12T09:00:00Z",
          "endTime": "2026-04-12T09:01:00Z",
          "durationMs": 12000,
          "meanIntervalMs": 150,
          "severity": "medium"
        }
      ],
      "bySeverity": { "critical": 0, "high": 0, "medium": 1, "low": 1 }
    },
    "stalling": {
      "count": 1,
      "episodes": [
        {
          "gapMs": 180000,
          "gapFormatted": "3.0m",
          "beforeEvent": "tool_call",
          "afterEvent": "error",
          "timestamp": "2026-04-13T14:22:00Z",
          "severity": "medium",
          "possibleCause": "external-dependency"
        }
      ],
      "bySeverity": { "critical": 0, "high": 0, "medium": 1, "low": 0 },
      "byCause": { "external-dependency": 1 }
    }
  }
}
```

**Rush Severity:** `critical` (<5% of median), `high` (<10%), `medium` (<20%), `low` (≥20%)

**Stall Severity:** `critical` (>50× median), `high` (>20×), `medium` (>10×), `low` (≥5×)

**Stall Causes:** `error-recovery`, `possible-loop`, `session-timeout`, `external-dependency`, `processing-delay`

### `POST /tempo/analyze`

On-demand tempo analysis — supply your own event array for analysis without stored data.

**Body:**

```json
{
  "events": [
    { "timestamp": "2026-04-10T08:00:00Z", "event_type": "llm_call" },
    { "timestamp": "2026-04-10T08:00:05Z", "event_type": "tool_call" },
    { "timestamp": "2026-04-10T08:00:08Z", "event_type": "llm_call" }
  ]
}
```

Requires at least 3 events. Returns `tempoScore`, `tempoHealth`, `cadence`, `rhythm`, `rushEpisodes`, `stallEpisodes`, and `recommendations`.

---

## Auto-Triage

Unified session diagnostics — runs health scoring, anomaly detection, baseline drift analysis, error grouping, and cost analysis in a single call. Returns a prioritized triage report with severity-ranked findings and automated remediation suggestions.

### `GET /triage/:sessionId`

Full auto-triage for a single session.

**Response:**

```json
{
  "session_id": "sess-abc123",
  "agent_name": "claude-3.5-sonnet",
  "triage_at": "2026-04-18T12:00:00Z",
  "overall_severity": "high",
  "health_grade": "C",
  "health_score": 62,
  "summary": "Session has 1 high-severity findings that should be investigated.",
  "findings": [
    {
      "severity": "high",
      "category": "anomaly",
      "title": "Token usage is anomalous (3.2σ above baseline)",
      "detail": "Actual: 45000, Baseline mean: 12500, Z-score: 3.2",
      "metric": { "name": "totalTokens", "value": 45000, "threshold": 12500, "unit": "count" },
      "remediation": "Review prompt sizes and consider using smaller models for simple tasks. Check for unnecessary context in prompts."
    }
  ],
  "metrics": {
    "total_tokens": 45000,
    "tokens_in": 30000,
    "tokens_out": 15000,
    "total_cost": 0.135,
    "error_count": 3,
    "event_count": 28,
    "duration_ms": 45000,
    "avg_event_duration_ms": 1607,
    "models_used": ["claude-3.5-sonnet"],
    "tools_used": ["code_search", "file_write"]
  },
  "anomaly_report": {
    "isAnomaly": true,
    "maxZScore": 3.2,
    "dimensions": {
      "totalTokens": { "value": 45000, "zScore": 3.2, "baseline_mean": 12500 },
      "duration_ms": { "value": 45000, "zScore": 1.1, "baseline_mean": 30000 },
      "eventCount": { "value": 28, "zScore": 0.5, "baseline_mean": 22 },
      "errorCount": { "value": 3, "zScore": 1.8, "baseline_mean": 1.2 }
    }
  },
  "baseline_comparison": {
    "samples": 200,
    "verdict": "regression",
    "checks": {
      "total_tokens": { "baseline": 12500, "actual": 45000, "delta_pct": 260, "status": "regression" }
    }
  },
  "error_analysis": {
    "count": 3,
    "rate": 10.71,
    "groups": [
      { "type": "tool_error", "count": 2, "examples": ["Timeout waiting for response"] },
      { "type": "error", "count": 1, "examples": ["Rate limit exceeded"] }
    ]
  },
  "cost_analysis": {
    "total_cost": 0.135,
    "model_breakdown": { "claude-3.5-sonnet": { "cost": 0.135, "calls": 12 } },
    "above_average": true,
    "avg_cost_reference": 0.045
  },
  "health_details": {
    "score": 62,
    "grade": "C",
    "components": {
      "error_rate": { "score": 60, "value": 10.71, "weight": 0.4 },
      "latency": { "score": 80, "value": 1607, "weight": 0.35 },
      "tool_reliability": { "score": 60, "value": 12.5, "weight": 0.25 }
    }
  }
}
```

**Finding Severities:** `critical`, `high`, `medium`, `low`

**Finding Categories:** `errors`, `anomaly`, `drift`, `cost`, `latency`

**Health Grades:** `A` (≥90), `B` (≥80), `C` (≥70), `D` (≥60), `F` (<60)

### `GET /triage/batch`

Triage multiple recent sessions — returns a summary view for quick fleet-wide diagnostics.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | number | 10 | Max sessions to triage (max 50) |
| `agent` | string | — | Filter by agent name |
| `severity` | string | — | Minimum severity to include (`critical`, `high`, `medium`, `low`) |

**Response:**

```json
{
  "triaged": [
    {
      "session_id": "sess-abc123",
      "agent_name": "claude-3.5-sonnet",
      "status": "completed",
      "started_at": "2026-04-18T10:00:00Z",
      "overall_severity": "high",
      "health_grade": "C",
      "health_score": 62,
      "finding_count": 3,
      "top_finding": "Token usage is anomalous (3.2σ above baseline)"
    }
  ],
  "count": 1,
  "triaged_at": "2026-04-18T12:00:00Z"
}
```
