# AgentLens CLI Reference

The `agentlens` CLI provides command-line access to your AgentLens backend for querying sessions, analyzing costs, debugging agents, and managing operations.

## Global Options

Most commands accept these options for talking to the backend:

| Option | Env Variable | Default | Description |
|--------|-------------|---------|-------------|
| `--endpoint URL` | `AGENTLENS_ENDPOINT` | `http://localhost:3000` | Backend URL |
| `--api-key KEY` | `AGENTLENS_API_KEY` | `default` | API authentication key |

Resolution order for each option is: explicit CLI flag -> environment
variable -> persistent config file (see below) -> built-in default. This
means you can set `AGENTLENS_ENDPOINT` once in your shell profile and
omit `--endpoint` from every invocation.

## Configuration

AgentLens persists CLI defaults in a small JSON config file so you do not
have to retype `--endpoint` / `--api-key` for every command.

```bash
agentlens config show              # Show current config
agentlens config set <key> <value> # Set a config value
agentlens config unset <key>       # Remove a config value
agentlens config reset             # Reset to defaults
agentlens config path              # Print config file path
```

Set `endpoint` and `api_key` to avoid passing them on every command:

```bash
agentlens config set endpoint http://localhost:3000
agentlens config set api_key your-secret-key
```

**Config file location.** Defaults to `~/.agentlens/config.json` on Linux
and macOS, and `%USERPROFILE%\.agentlens\config.json` on Windows. Run
`agentlens config path` to print the exact path on your system.

**Supported keys.** Any global option name is accepted; the two most
useful are `endpoint` and `api_key`. Unknown keys are stored but ignored
by the CLI, so it's safe to add notes for yourself.

## Session Commands

### List Sessions

```bash
agentlens sessions [--limit N]
```

### Session Detail

```bash
agentlens session <session_id>
```

### Session Costs

```bash
agentlens costs <session_id>
```

### List Events

```bash
agentlens events [--session SESSION] [--type TYPE] [--model MODEL] [--limit N]
```

### Export Session

```bash
agentlens export <session_id> [--format json|csv] [--output FILE]
```

## Analysis Commands

### Analytics Overview

```bash
agentlens analytics
```

### Health Score

```bash
agentlens health <session_id>
```

### Compare Sessions

```bash
agentlens compare <session_a> <session_b>
```

### Session Diff

```bash
agentlens diff <session_a> <session_b> [--label-a LABEL] [--label-b LABEL] [--no-color] [--json]
```

### Agent Profile

```bash
agentlens profile <agent_name> [--days N] [--json]
```

### Trends

```bash
agentlens trends [--period day|week|month] [--metric METRIC|all] [--agent NAME] [--limit N] [--json]
```

### Correlation Analysis

```bash
agentlens correlate [--metrics METRICS] [--limit N] [--min-sessions N] [--format table|json|csv] [--output FILE]
```

### Leaderboard

```bash
agentlens leaderboard [--sort efficiency|speed|reliability|cost|volume] [--days N] [--limit N] [--min-sessions N] [--order asc|desc] [--json]
```

### Bottleneck Detection

```bash
agentlens bottleneck [--by agent|model|type] [--metric latency|cost|errors] [--limit N] [--min-sessions N] [--format table|json] [--output FILE]
```

### Outlier Detection

```bash
agentlens outlier [--metric cost|tokens|duration|errors|all] [--limit N] [--threshold F] [--format table|json] [--top N]
```

## Visualization Commands

### Flamegraph

```bash
agentlens flamegraph <session_id> [--output FILE] [--open] [--stats]
```

Generates an interactive HTML flamegraph of a session's event hierarchy.

### Gantt Chart

```bash
agentlens gantt <session_id> [--output FILE] [--open] [--format html|json|ascii]
```

### Heatmap

```bash
agentlens heatmap [--metric sessions|cost|tokens|events] [--weeks N] [--limit N]
```

### Scatter Plot

```bash
agentlens scatter [--x METRIC] [--y METRIC] [--limit N] [--width W] [--height H] [--agent NAME] [--no-trend] [--format ascii|json] [--output FILE]
```

### Trace View

```bash
agentlens trace <session_id> [--no-color] [--json] [--type TYPE] [--min-ms N]
```

### Session Replay

```bash
agentlens replay <session_id> [--speed N] [--type TYPES] [--exclude TYPES] [--format text|json|markdown] [--live] [--no-color] [--output FILE]
```

### Event Funnel

```bash
agentlens funnel [--stages TYPES] [--limit N] [--format table|json|html] [--output FILE] [--open]
```

### Dependency Map

```bash
agentlens depmap [--limit N] [--format ascii|json|html] [--output FILE] [--open]
```

## Monitoring Commands

### Live Tail

```bash
agentlens tail [--session SESSION] [--type TYPE] [--interval SECS]
```

Streams events in real-time (like `tail -f`).

### Top (Live Dashboard)

```bash
agentlens top [--sort cost|tokens|events] [--limit N] [--interval SECS]
```

Live-updating view of the most active/expensive sessions.

### Watch

```bash
agentlens watch [--interval SECS] [--metric METRIC] [--agent NAME] [--alert-threshold N] [--compact] [--no-spark] [--duration MINS]
```

Continuous metric monitoring with spark lines and threshold alerts.

### Status

```bash
agentlens status
```

Check backend connectivity and database stats.

## Cost & Budget Commands

### Cost Forecast

```bash
agentlens forecast [--days N] [--metric cost|tokens|sessions] [--model MODEL] [--format table|json|chart] [--output FILE]
```

### Budget Management

```bash
agentlens budget list [--json]
agentlens budget set <scope> <period> <limit_usd> [--warn-pct N]
agentlens budget check <session_id> [--json]
agentlens budget delete <scope> [<period>]
```

**Scope** can be `global` or an agent name. **Period** is `daily`, `weekly`, or `monthly`.

## Alert Commands

```bash
agentlens alert history [--severity LEVEL] [--since HOURS] [--limit N] [--ack|--unack] [--format table|json]
agentlens alert rules [--format table|json]
agentlens alert test <rule_id> <session_id>
agentlens alert ack <alert_id> [--note TEXT]
agentlens alert silence <rule_id> [--duration MINUTES]
agentlens alert unsilence <rule_id>
agentlens alert stats [--period day|week|month] [--format table|json]
```

## SLA Commands

```bash
agentlens sla [--policy production|development] [--latency MS] [--error-rate PCT] [--token-budget N] [--slo PCT] [--agent NAME] [--limit N] [--verbose] [--json]
```

Built-in policies: `production` (strict) and `development` (relaxed). Override individual thresholds with flags.

## Reporting Commands

### Summary Report

```bash
agentlens report [--period day|week|month] [--format table|json|markdown] [--output FILE]
```

### Digest

```bash
agentlens digest [--period day|week|month] [--format text|markdown|html|json] [--output FILE] [--open] [--top N]
```

### Dashboard (HTML)

```bash
agentlens dashboard [--limit N] [--output FILE] [--open]
```

Generates a standalone HTML dashboard.

## Operational Commands

### Audit Log

```bash
agentlens audit [ENTRY_ID] [--agent NAME] [--action TYPE] [--severity LEVEL] [--model MODEL] [--session ID] [--since HOURS] [--limit N] [--format table|csv|json] [--output FILE] [--stats] [--no-color]
```

### Postmortem

```bash
agentlens postmortem <session_id>
agentlens postmortem candidates [--min-errors N] [--limit N]
```

### Snapshots

```bash
agentlens snapshot [--label LABEL] [--output FILE] [--limit N] [--format json|table]
agentlens snapshot diff <file_a> <file_b> [--format table|json]
```

### Baseline Management

```bash
agentlens baseline list [--json]
agentlens baseline show <agent_name> [--json]
agentlens baseline record <session_id>
agentlens baseline check <session_id> [--json]
agentlens baseline delete <agent_name>
```

### Data Retention

```bash
agentlens retention [--limit N] [--format table|json|chart] [--output FILE] [--open]
agentlens retention policy [--keep-days N] [--dry-run] [--json]
agentlens retention purge --older-than DAYS [--dry-run] [--yes]
```

## Examples

```bash
# Quick health check
agentlens status

# See today's most expensive sessions
agentlens leaderboard --sort cost --days 1

# Debug a slow session
agentlens trace sess_abc123 --min-ms 500
agentlens flamegraph sess_abc123 --open

# Set a daily budget and check it
agentlens budget set global daily 50.00 --warn-pct 80
agentlens budget check sess_abc123

# Monitor agents in real-time
agentlens watch --metric cost --alert-threshold 10

# Generate a weekly report
agentlens digest --period week --format markdown --output weekly.md

# One-shot triage of a problem session
agentlens triage sess_abc123 --json

# Full root-cause autopsy with anomaly detection
agentlens autopsy sess_abc123 --baseline-count 30

# Scan a session for prompt-injection signals
agentlens prompt-injection sess_abc123 --verbose

# Fleet capacity planning over the last 30 days
agentlens capacity --days 30
```

## Diagnostics & Triage

These commands provide higher-level, opinionated analysis built on top of
the raw session data. They are designed for incident response and ongoing
quality monitoring.

### Auto-Triage

```bash
agentlens triage [SESSION_ID] [--recent] [--limit N] [--agent NAME] \
                 [--severity critical|high|medium|low] [--json]
```

Unified diagnostics that consolidate findings from multiple analyzers
(cost, errors, bias, hallucination, ...) into a single ranked report with
remediation hints. Pass a session ID for a single-session report, or use
`--recent --limit N` to triage the most recent sessions in batch mode.

### Session Autopsy

```bash
agentlens autopsy [SESSION_ID] [--no-baseline] [--baseline-count N] \
                  [--min-baselines N] [--json]
```

Deep root-cause analysis for a single session. By default it pulls a
rolling baseline (`--baseline-count 20`) of comparable sessions and
highlights anomalies. Use `--no-baseline` to skip the comparison when you
are investigating the first run of a new agent.

### Capacity Planning

```bash
agentlens capacity [--days N] [--limit N] [--format table|json]
```

Fleet-wide capacity and bottleneck planning - identifies saturated
resources, projects sizing requirements, and flags scaling candidates.

## Safety & Quality Detectors

Each detector targets a specific failure mode. All of them accept the
standard global `--endpoint` / `--api-key` options in addition to the
flags shown below.

### Hallucination Detection

```bash
agentlens hallucination [SESSION_ID] [--demo] [--json] [--verbose]
```

Scans LLM outputs in a session for unsupported claims and contradictions
between retrieved context and generated text. `--demo` runs against a
built-in synthetic session, which is handy for trying the detector
without a live backend.

### Prompt Injection Detection

```bash
agentlens prompt-injection <session_id> [--json] [--verbose] \
                            [--min-confidence F] [--no-tool-outputs]
```

Detects direct and indirect prompt-injection attempts. By default it
scans both user inputs and tool outputs (the most common vector for
indirect injection); pass `--no-tool-outputs` to limit the scan to user
messages only. `--min-confidence` (0.0-1.0, default `0.5`) tunes recall
vs. precision.

### Cognitive Bias Detection

```bash
agentlens cognitive-bias <session_id> [--json] [--verbose] \
                          [--min-confidence F]
```

Identifies common reasoning biases (anchoring, confirmation,
availability, ...) in agent decision traces.

### Reward Hacking Detection

```bash
agentlens reward-hacking [--demo] [--json] [--verbose]
```

Flags sessions where the agent appears to satisfy its objective metric
while circumventing the intended task (specification gaming, metric
over-optimization, ...).

### Self-Correction Tracking

```bash
agentlens self-correction <session_id> [--json] [--verbose] \
                           [--min-confidence F]
```

Measures how often, and how effectively, the agent revises its own
intermediate outputs. Useful for evaluating reflection/critique loops.

### Delegation Analysis

```bash
agentlens delegation [--demo] [--json] [--max-depth N] \
                      [--bottleneck-threshold N] \
                      [--over-delegation-threshold F]
```

Analyzes multi-agent delegation graphs to surface over-delegation,
bottleneck sub-agents, and pathologically deep call chains.

### Tool Usage Profiling

```bash
agentlens tool-usage [--demo] [--json] \
                      [--overreliance-threshold F] [--failure-threshold F]
```

Profiles tool invocations across sessions: identifies tools the agent
over-relies on, tools that fail too often, and unused tools that may be
dead code.

### Context Utilization

```bash
agentlens context-utilization <session_id> [--json] [--verbose] \
                                [--context-limit N]
```

Reports how efficiently each LLM call uses its context window. The
`--context-limit` flag (default `128000`) lets you scope the analysis to
the model you actually deployed.
