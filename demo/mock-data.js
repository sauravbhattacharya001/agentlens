/* Mock data for AgentLens demo dashboard */

const MOCK_SESSIONS = [
  {
    session_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    agent_name: "Research Assistant Agent",
    status: "completed",
    started_at: "2026-02-14T02:30:00Z",
    ended_at: "2026-02-14T02:32:45Z",
    total_tokens_in: 4280,
    total_tokens_out: 2150,
    metadata: { version: "1.2.0", environment: "production" },
    events: [
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T02:30:00Z",
        input_data: { query: "Find recent papers on transformer architecture improvements" },
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 12, model: null,
        decision_trace: null, tool_call: null
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T02:30:01Z",
        input_data: { messages: [{ role: "user", content: "Find recent papers on transformer architecture improvements" }] },
        output_data: { content: "I'll search for recent papers on transformer improvements. Let me use the search tool." },
        tokens_in: 850, tokens_out: 320, duration_ms: 1240, model: "gpt-4o",
        decision_trace: { reasoning: "User wants research papers — I should use the academic search tool to find recent publications on transformer architectures." },
        tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T02:30:03Z",
        input_data: null,
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 2100, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "arxiv_search",
          tool_input: { query: "transformer architecture improvements 2025-2026", max_results: 5 },
          tool_output: { papers: [
            { title: "FlashAttention-3: Fast and Exact Attention", authors: "Dao et al.", year: 2026 },
            { title: "Mixture of Depths: Adaptive Computation", authors: "Raposo et al.", year: 2025 },
            { title: "Ring Attention for Blockwise Parallel Transformers", authors: "Liu et al.", year: 2025 }
          ]}
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T02:30:06Z",
        input_data: { messages: [{ role: "system", content: "Summarize search results" }] },
        output_data: { content: "Here are the most relevant recent papers on transformer architecture improvements:\n\n1. **FlashAttention-3** — Introduces IO-aware exact attention with hardware-optimized kernels\n2. **Mixture of Depths** — Dynamic computation allocation across transformer layers\n3. **Ring Attention** — Enables near-infinite context through blockwise parallelism" },
        tokens_in: 1200, tokens_out: 680, duration_ms: 2800, model: "gpt-4o",
        decision_trace: { reasoning: "Got 3 relevant papers. Summarizing with key innovations highlighted for the user." },
        tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T02:30:10Z",
        input_data: null,
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 450, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "save_to_knowledge_base",
          tool_input: { category: "ML Research", items: 3 },
          tool_output: { status: "saved", id: "kb-2026-0214" }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T02:30:12Z",
        input_data: { messages: [{ role: "system", content: "Final response" }] },
        output_data: { content: "I found 3 key papers and saved them to your knowledge base. The most impactful is FlashAttention-3 which achieves 2x speedup over FA-2." },
        tokens_in: 980, tokens_out: 450, duration_ms: 1800, model: "gpt-4o",
        decision_trace: null,
        tool_call: null
      },
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T02:30:14Z",
        input_data: null,
        output_data: { result: "completed", papers_found: 3 },
        tokens_in: 0, tokens_out: 0, duration_ms: 0, model: null,
        decision_trace: null, tool_call: null
      }
    ]
  },
  {
    session_id: "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    agent_name: "Code Review Agent",
    status: "completed",
    started_at: "2026-02-14T01:15:00Z",
    ended_at: "2026-02-14T01:18:30Z",
    total_tokens_in: 6420,
    total_tokens_out: 3890,
    metadata: { version: "2.0.1", environment: "staging" },
    events: [
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T01:15:00Z",
        input_data: { pr_url: "https://github.com/example/repo/pull/42", review_depth: "thorough" },
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 5, model: null,
        decision_trace: null, tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T01:15:01Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 890, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "github_get_pr",
          tool_input: { owner: "example", repo: "repo", pr_number: 42 },
          tool_output: { title: "Add connection pooling", files_changed: 4, additions: 127, deletions: 23 }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T01:15:03Z",
        input_data: { messages: [{ role: "system", content: "Review PR diff for security and performance issues" }] },
        output_data: { content: "Found 2 issues: 1) SQL query uses string interpolation instead of parameterized queries (security risk), 2) Connection pool size is hardcoded — should be configurable via env var." },
        tokens_in: 3200, tokens_out: 1800, duration_ms: 4500, model: "claude-3.5-sonnet",
        decision_trace: { reasoning: "Diff shows database connection changes. Must check for SQL injection and resource management issues. Found string interpolation in query builder — critical security issue." },
        tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T01:15:08Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 650, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "github_create_review",
          tool_input: { pr_number: 42, event: "REQUEST_CHANGES", body: "Two issues found — see inline comments" },
          tool_output: { review_id: 98765, state: "changes_requested" }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T01:15:10Z",
        input_data: { messages: [{ role: "system", content: "Generate fix suggestions" }] },
        output_data: { content: "Suggested fixes:\n1. Use parameterized queries: `db.query('SELECT * FROM users WHERE id = $1', [userId])`\n2. Add `POOL_SIZE` env var with default of 10" },
        tokens_in: 1420, tokens_out: 890, duration_ms: 2100, model: "claude-3.5-sonnet",
        decision_trace: null,
        tool_call: null
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T01:15:14Z",
        input_data: { messages: [{ role: "system", content: "Summarize review" }] },
        output_data: { content: "Review complete: 1 critical (SQL injection), 1 moderate (hardcoded pool size). Fix suggestions provided." },
        tokens_in: 800, tokens_out: 400, duration_ms: 950, model: "gpt-4o-mini",
        decision_trace: null,
        tool_call: null
      },
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T01:15:16Z",
        input_data: null,
        output_data: { result: "changes_requested", issues: 2, severity: "critical" },
        tokens_in: 0, tokens_out: 0, duration_ms: 0, model: null,
        decision_trace: null, tool_call: null
      }
    ]
  },
  {
    session_id: "c3d4e5f6-a7b8-9012-cdef-123456789012",
    agent_name: "Data Pipeline Monitor",
    status: "active",
    started_at: "2026-02-14T03:00:00Z",
    ended_at: null,
    total_tokens_in: 1850,
    total_tokens_out: 920,
    metadata: { version: "1.0.0", environment: "production" },
    events: [
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T03:00:00Z",
        input_data: { pipeline: "etl-daily", check_type: "health" },
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 8, model: null,
        decision_trace: null, tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T03:00:01Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 340, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "check_pipeline_status",
          tool_input: { pipeline_id: "etl-daily" },
          tool_output: { status: "running", stage: "transform", progress: 67, rows_processed: 1420000 }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T03:00:02Z",
        input_data: { messages: [{ role: "system", content: "Analyze pipeline health metrics" }] },
        output_data: { content: "Pipeline etl-daily is running normally. Transform stage at 67% — on track for 03:45 completion. 1.42M rows processed, throughput is within expected range." },
        tokens_in: 950, tokens_out: 480, duration_ms: 1600, model: "gpt-4o-mini",
        decision_trace: { reasoning: "Pipeline progress (67%) and throughput (1.42M rows) are within normal parameters. No anomalies detected. Will continue monitoring." },
        tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T03:00:05Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 210, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "get_error_logs",
          tool_input: { pipeline_id: "etl-daily", since: "2026-02-14T02:00:00Z" },
          tool_output: { errors: [], warnings: [{ msg: "Slow partition detected: shard-7", ts: "2026-02-14T02:45:12Z" }] }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T03:00:06Z",
        input_data: { messages: [{ role: "system", content: "Evaluate warnings" }] },
        output_data: { content: "One warning: shard-7 running slower than peers (detected 15 min ago). Not critical yet but worth watching. Setting alert threshold." },
        tokens_in: 900, tokens_out: 440, duration_ms: 1200, model: "gpt-4o-mini",
        decision_trace: { reasoning: "Single shard slowdown is common and usually self-resolves. Setting a watch but not escalating." },
        tool_call: null
      }
    ]
  },
  {
    session_id: "d4e5f6a7-b8c9-0123-defa-234567890123",
    agent_name: "Customer Support Agent",
    status: "error",
    started_at: "2026-02-13T22:10:00Z",
    ended_at: "2026-02-13T22:10:45Z",
    total_tokens_in: 1200,
    total_tokens_out: 350,
    metadata: { version: "1.5.0", environment: "production" },
    events: [
      {
        event_type: "agent_call",
        timestamp: "2026-02-13T22:10:00Z",
        input_data: { ticket_id: "SUP-4521", priority: "high" },
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 6, model: null,
        decision_trace: null, tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-13T22:10:01Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 520, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "get_ticket",
          tool_input: { ticket_id: "SUP-4521" },
          tool_output: { subject: "Cannot access billing portal", customer: "Acme Corp", plan: "enterprise" }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-13T22:10:02Z",
        input_data: { messages: [{ role: "system", content: "Diagnose billing portal access issue" }] },
        output_data: { content: "Checking customer SSO configuration and billing portal permissions..." },
        tokens_in: 1200, tokens_out: 350, duration_ms: 1800, model: "gpt-4o",
        decision_trace: { reasoning: "Enterprise customer can't access billing — likely SSO or permission issue. Need to check auth service." },
        tool_call: null
      },
      {
        event_type: "tool_error",
        timestamp: "2026-02-13T22:10:05Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 5000, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "check_auth_service",
          tool_input: { customer_id: "acme-corp", service: "billing" },
          tool_output: { error: "ConnectionTimeout: Auth service unreachable after 5000ms" }
        }
      },
      {
        event_type: "agent_error",
        timestamp: "2026-02-13T22:10:10Z",
        input_data: null,
        output_data: { error: "Upstream auth service timeout — escalated to on-call engineer", ticket_updated: true },
        tokens_in: 0, tokens_out: 0, duration_ms: 0, model: null,
        decision_trace: { reasoning: "Auth service is down — this is an infrastructure issue, not a customer config issue. Escalating immediately." },
        tool_call: null
      }
    ]
  },
  {
    session_id: "e5f6a7b8-c9d0-1234-efab-345678901234",
    agent_name: "Content Writer Agent",
    status: "completed",
    started_at: "2026-02-14T00:00:00Z",
    ended_at: "2026-02-14T00:05:20Z",
    total_tokens_in: 3600,
    total_tokens_out: 5200,
    metadata: { version: "3.1.0", environment: "production" },
    events: [
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T00:00:00Z",
        input_data: { task: "Write blog post", topic: "AI Agent Observability Best Practices" },
        output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 10, model: null,
        decision_trace: null, tool_call: null
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T00:00:01Z",
        input_data: { messages: [{ role: "user", content: "Create an outline for a blog post about AI agent observability" }] },
        output_data: { content: "Outline: 1) Why observability matters for AI agents, 2) Key metrics to track, 3) Tool call tracing, 4) Token cost optimization, 5) Decision explainability, 6) Production monitoring patterns" },
        tokens_in: 600, tokens_out: 800, duration_ms: 2200, model: "claude-3.5-sonnet",
        decision_trace: { reasoning: "Creating a structured outline first. Focusing on practical best practices rather than theory. Six sections cover the full observability lifecycle." },
        tool_call: null
      },
      {
        event_type: "tool_call",
        timestamp: "2026-02-14T00:00:04Z",
        input_data: null, output_data: null,
        tokens_in: 0, tokens_out: 0, duration_ms: 1800, model: null,
        decision_trace: null,
        tool_call: {
          tool_name: "web_search",
          tool_input: { query: "AI agent observability tools 2026 comparison" },
          tool_output: { results: [{ title: "Top Agent Monitoring Tools", url: "https://example.com/tools" }] }
        }
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T00:00:07Z",
        input_data: { messages: [{ role: "system", content: "Write full blog post with research" }] },
        output_data: { content: "# AI Agent Observability Best Practices\n\nAs AI agents become more autonomous, understanding their behavior becomes critical..." },
        tokens_in: 1800, tokens_out: 3200, duration_ms: 8500, model: "claude-3.5-sonnet",
        decision_trace: null,
        tool_call: null
      },
      {
        event_type: "llm_call",
        timestamp: "2026-02-14T00:00:18Z",
        input_data: { messages: [{ role: "system", content: "Generate SEO metadata and social preview" }] },
        output_data: { content: "Title: AI Agent Observability: 6 Best Practices for Production | Meta: Learn how to monitor, trace, and optimize AI agents with practical observability patterns." },
        tokens_in: 1200, tokens_out: 1200, duration_ms: 2800, model: "gpt-4o-mini",
        decision_trace: null,
        tool_call: null
      },
      {
        event_type: "agent_call",
        timestamp: "2026-02-14T00:00:22Z",
        input_data: null,
        output_data: { result: "completed", word_count: 1850, seo_score: 92 },
        tokens_in: 0, tokens_out: 0, duration_ms: 0, model: null,
        decision_trace: null, tool_call: null
      }
    ]
  }
];

const MOCK_EXPLANATIONS = {
  "a1b2c3d4-e5f6-7890-abcd-ef1234567890": "## Research Assistant Session Analysis\n\n### Decision Flow\nThe agent followed a **search → analyze → save** pattern:\n\n1. **Intent Recognition** — Correctly identified the user's request as an academic paper search\n2. **Tool Selection** — Chose arxiv_search over web_search (better for academic papers)\n3. **Result Synthesis** — Summarized 3 papers with key innovations highlighted\n4. **Knowledge Persistence** — Proactively saved results to knowledge base\n\n### Key Decisions\n- **Why arxiv_search?** The agent's reasoning trace shows it prioritized academic sources over general web results\n- **Why 3 papers?** Balanced between breadth and depth — enough to show trends without overwhelming\n\n### Token Efficiency\n- Total: 6,430 tokens across 3 LLM calls\n- Most tokens spent on the synthesis step (56%) — appropriate for the task\n- Could optimize by using gpt-4o-mini for the final summary step",

  "b2c3d4e5-f6a7-8901-bcde-f12345678901": "## Code Review Session Analysis\n\n### Security Finding: SQL Injection\nThe agent correctly identified a **critical SQL injection vulnerability** in the PR:\n- String interpolation was used in query construction\n- Agent recommended parameterized queries as the fix\n\n### Architecture Observation\nThe agent also caught a **configuration issue** — hardcoded connection pool size should be env-configurable for production flexibility.\n\n### Multi-Model Strategy\nNotably, the agent used:\n- **Claude 3.5 Sonnet** for deep code analysis (higher reasoning capability)\n- **GPT-4o-mini** for the summary step (cost-effective for simple generation)\n\nThis is an efficient multi-model pattern that reduces costs by 40% compared to using a single premium model.",

  "c3d4e5f6-a7b8-9012-cdef-123456789012": "## Pipeline Monitor Session Analysis\n\n### Current Status\nThe monitoring agent is tracking the `etl-daily` pipeline which is currently at **67% progress** in the transform stage.\n\n### Anomaly Detection\n- One warning detected: **shard-7** showing slower processing\n- Agent correctly classified this as a **watch-level** concern rather than escalating\n- Decision reasoning shows understanding of typical shard behavior patterns\n\n### Monitoring Pattern\nThis session demonstrates the **observe → analyze → triage** pattern:\n1. Check pipeline status metrics\n2. Query error/warning logs\n3. Evaluate severity and decide on action\n\nThe agent's conservative approach to warnings is appropriate for production monitoring.",

  "d4e5f6a7-b8c9-0123-defa-234567890123": "## Customer Support Session Analysis — Error Case\n\n### What Happened\nThe agent was handling a high-priority support ticket (billing portal access) when the upstream **auth service became unreachable**.\n\n### Error Handling\n- **Good:** Agent correctly identified this as an infrastructure issue, not a customer config problem\n- **Good:** Immediately escalated to on-call engineer instead of retrying\n- **Good:** Updated the ticket with diagnostic info before failing\n\n### Improvement Opportunities\n- Could implement a **circuit breaker** pattern to fail faster\n- Should check service health *before* attempting diagnosis\n- Consider a **fallback path** that can provide basic troubleshooting without the auth service",

  "e5f6a7b8-c9d0-1234-efab-345678901234": "## Content Writer Session Analysis\n\n### Creative Process\nThe agent followed a structured **outline → research → write → optimize** workflow:\n\n1. Created a 6-section outline before writing\n2. Researched current tools and trends\n3. Generated the full blog post (1,850 words)\n4. Added SEO metadata and social preview\n\n### Multi-Model Usage\n- **Claude 3.5 Sonnet** for creative writing (outline + full post)\n- **GPT-4o-mini** for SEO metadata (template-based generation)\n\n### Quality Metrics\n- SEO score: 92/100\n- Word count: 1,850 (ideal for blog posts)\n- Token ratio: 1.44x output vs input — efficient creative generation"
};
