# Changelog

All notable changes to AgentLens will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Transcript export (agent-eval bridge)** - Render an AgentLens session as a
  contract-compliant transcript for [agent-eval](https://github.com/sauravbhattacharya001/agent-eval).
  - `agentlens.export_transcript(session=..., session_id=..., timezone_label=...)`
    and the `TranscriptExporter` class produce markdown conforming to
    `transcript-contract@v1`.
  - Every section is *evidence-backed* from captured trace data: `## Actions Taken`
    from recorded tool calls, `## Outcome` from the trusted session status
    (`completed`->pass, `error`->fail, `active`->IN-PROGRESS), `## Duration` from
    the recorded start/end - not the agent's self-report.
  - Accepts a `Session` object or a backend session dict (e.g. `export_session`).
  - Output validates with `agent-eval validate` (verified end-to-end).
  - 17 new SDK tests.
- **Run metadata export (self-verifying loop)** - `agentlens.export_run_metadata()`
  and `TranscriptExporter.to_run_metadata()` extract agent-eval `RunMetadata`
  (ground truth) from a session: `exitStatus` mapped from the recorded session
  status (`completed`->ok, `error`->error, `active`->running), plus
  `startedAt`/`endedAt`/`durationMs` from the recorded wall-clock.
  - Pairs with `export_transcript`: the transcript is the agent's *claim*, this
    is the *truth* agent-eval's `verification` check grades it against - so the
    AgentLens -> agent-eval path is self-verifying.
  - Verified end-to-end: an optimistic transcript claiming `pass` over a session
    recorded as `error` is caught by the verification check using this metadata.
  - 9 new SDK tests.

## [1.65.0] - 2026-06-11

### Added

- **AdvisorOrchestrator** — unified fleet-health scorecard that runs the agentic advisor suite and rolls the individual verdicts up into a single health view.
- **CostAttributionAdvisor** — per-tag spend attribution with a chargeback playbook for splitting agent cost across teams/features.
- **DataLeakAdvisor** — PII/secret leak detection over event traces (the 13th agentic advisor sibling).
- **EvalRegressionAdvisor** — baseline-vs-current evaluation regression detector.
- **PromptDriftAdvisor** — agentic baseline-vs-current prompt drift detector.
- **CacheabilityAdvisor** — prompt-cache opportunity analysis.
- **ToolDependencyAdvisor** — agentic tool-coupling analysis surfacing implicit dependencies between tools.
- **ToolReliabilityAdvisor** — per-tool reliability/health auditor with a verdict ladder and remediation playbook.
- **`drift` CLI command** — behavioral drift detection for agent sessions from the terminal.
- **`tool-reliability` CLI command** — terminal scorecard for per-tool health.
- **PDF export** for session reports (new output format in the exporter).

### Changed

- **Sparkline rendering centralized.** `cli_trends`, `cli_watch` and `stamina` each shipped their own copy-pasted `_sparkline` helper with the same glyph table (`▁▂▃▄▅▆▇█`) and the same algorithm. All three now re-export `agentlens.cli_common.sparkline` so there is a single source of truth — behaviour is byte-for-byte identical (one glyph per input value) and no call sites needed changes.
- **Duplicated `_percentile` / `_parse_ts` helpers consolidated** across the advisor modules.
- **`narrative.generate()`** split from a 147-line function into focused helpers.

### Fixed

- **PyPI publishing unblocked (Python 3.9 compatibility).** Releases from v1.x onward never reached PyPI because the publish workflow's Python 3.9 test job crashed at import time — leaving the public package stranded at an early `0.1.x`. Three 3.9-incompatible patterns were the cause and are now fixed:
  - `agentlens/__init__.py` evaluated a module-level `AgentTracker | None` annotation at import time (PEP 604 `|` on a class is a runtime `TypeError` before Python 3.10); it now uses `from __future__ import annotations`.
  - Pydantic models in `agentlens/models.py` use PEP 604 / PEP 585 field annotations that Pydantic resolves at model-build time; `eval_type_backport` is now declared as a dependency for `python_version < "3.10"` so those annotations resolve on 3.9.
  - `cli_snapshot.py` used `datetime.UTC` (added in Python 3.11); it now uses `datetime.timezone.utc` to match the rest of the codebase.
  Five test modules that used PEP 604 syntax in module-level helper signatures (`test_drift`, `test_flamegraph`, `test_retry_tracker`, `test_session_diff`, `test_sla`) also gained `from __future__ import annotations`. The full SDK suite (3820 tests) now passes on Python 3.9, 3.11 and 3.12.
- **`cli_common.sparkline(width=...)` is no longer silently ignored.** The `width` parameter existed in the signature but did nothing; supplying it now down-samples the series into at most `width` buckets. Default behaviour (no `width` argument) is unchanged.
- **`cli_failure_forecast`** was broken on import (missing `add_common_args`).
- **Five CLI subcommands** crashed at startup because `get_client` returns a tuple.
- **`heatmap`** cost rate for `gpt-4o` corrected via longest-prefix match.

### Performance

- **`anomaly`** baseline computation now uses the Welford online algorithm (O(1) per update).
- **`heapq.nlargest`** replaces `sorted(..., reverse=True)[:k]` across 7 hot paths.
- **`correlation.trace_error_propagation`** halves the number of session-pair iterations.

### Testing

- **`agentlens._metrics`** — new `tests/test_metrics.py` (18 cases) covering the single-pass session-event scan used by both anomaly and drift detection: empty/None sessions, latency p95 at small and large N, `None` token fields, error-substring matching, tool detection via both the `tool_call` attribute and event-type substring, and the tool-failure rate's div-by-zero guard.
- **`agentlens.cli_common`** — new `tests/test_cli_common.py` (26 cases) covering env-var/flag resolution for `get_client`, JSON pretty-printing, dict-vs-list session payloads from `fetch_sessions`, `percentile` interpolation, `(xs, ys)` ordering of `linear_regression`, `sparkline` constant-input and down-sampling, and `bar_chart` zero-max / overflow clamping.
- Extensive new advisor/CLI coverage (AdvisorOrchestrator, ToolReliabilityAdvisor, `cli_alert`, `cli_correlate`, `cli_funnel`, `cli_heatmap`, `cli_diff`, `cli_baseline`, and others).

## [1.64.0] - 2026-05-18

### Added

- **AgentLoopDetector** — agentic in-flight loop / thrash / error-storm advisor that watches active sessions and flags pathological control flow before the user notices.
- **TraceCompletionAdvisor** — agentic trace liveness/completion analyzer; classifies in-flight traces as healthy, stalled, abandoned, or completed with reasoning.
- **SLOBurnRateAdvisor** — multi-window error-budget burn-rate analyzer with short/long window fast/slow burn classification.
- **ModelMigrationAdvisor** — agentic per-site model migration planner that scores incumbent vs. candidate models against your live workload mix and emits a ranked migration plan.

### Changed

- **uuid / ISO timestamp helpers** centralized into `agentlens._utils` and adopted across `prompt_tracker`, `quota`, and `alert_rule_synthesizer` (eliminates drift between bespoke ID/clock fragments).
- **SDK ruff sweep** across 33 modules (F, UP, SIM, B rule families) — modernized typing imports, removed unused vars, simplified branches.

### CI / Tooling

- **Dockerfile hadolint** workflow + post-push image smoke test guards Dockerfile regressions and verifies the published image actually boots.

### Testing

- **`agentlens._utils`** — comprehensive unit tests (93% coverage).
- **`cli_audit`** — comprehensive `test_cli_audit.py`.
- **`sampling_advisor`** — coverage raised **76% → 99%** (+54 tests) covering serialization helpers, constructor validation, dict-event ingestion, `output_data` error markers, `tool_call.duration_ms` fallback, `metadata.priority` fallback, numeric/string timestamps, recommend()-branch coverage (target_keep_pct, mandatory>target clamp, max_fallback cap, slow-threshold floor).

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
