/**
 * Seed script — populates the database with realistic demo data.
 * Run: node seed.js
 */

const { getDb } = require("./db");
const { v4: uuidv4 } = require("uuid");

function shortId() {
  return uuidv4().replace(/-/g, "").slice(0, 16);
}

function isoDate(offsetMinutes = 0) {
  const d = new Date();
  d.setMinutes(d.getMinutes() - offsetMinutes);
  return d.toISOString();
}

function seed() {
  const db = getDb();

  // Clear existing data
  db.exec("DELETE FROM events; DELETE FROM sessions;");

  console.log("🌱 Seeding demo data...");

  // ── Session 1: Research Agent ──────────────────────────────────────
  const s1 = shortId();
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)`).run(
    s1, "research-agent-v2",
    isoDate(30), isoDate(28),
    JSON.stringify({ version: "2.1.0", environment: "production" }),
    267, 132, "completed"
  );

  const s1Events = [
    {
      id: shortId(), session: s1, type: "llm_call", offset: 30,
      input: { prompt: "Analyze this query: What are the latest trends in AI agent frameworks?" },
      output: { response: "I'll search for recent developments in AI agent frameworks, focusing on LangChain, CrewAI, and AutoGen." },
      model: "gpt-4-turbo", tokIn: 45, tokOut: 28,
      trace: { reasoning: "User asked about current trends. Need to search for up-to-date information rather than relying on training data.", step: 1 },
      duration: 1250
    },
    {
      id: shortId(), session: s1, type: "tool_call", offset: 29.5,
      input: { prompt: "Searching web..." },
      output: { results: "Found 5 relevant articles" },
      model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "web_search", tool_input: { query: "AI agent frameworks 2026 trends" }, tool_output: { results: [{ title: "The Rise of Multi-Agent Systems", snippet: "LangGraph and CrewAI lead the charge..." }, { title: "AutoGen 2.0 Released", snippet: "Microsoft's framework now supports..." }] } },
      duration: 890
    },
    {
      id: shortId(), session: s1, type: "tool_call", offset: 29,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "web_search", tool_input: { query: "LangChain vs CrewAI comparison 2026" }, tool_output: { results: [{ title: "Framework Showdown", snippet: "LangChain offers more flexibility while CrewAI excels at..." }] } },
      duration: 720
    },
    {
      id: shortId(), session: s1, type: "llm_call", offset: 28.5,
      input: { prompt: "Synthesize search results into a comprehensive answer about AI agent framework trends" },
      output: { response: "Based on my research, the AI agent framework landscape in 2026 is dominated by three major players: LangChain/LangGraph for flexible agent orchestration, CrewAI for multi-agent collaboration, and Microsoft's AutoGen 2.0 for enterprise deployments. Key trends include: 1) Multi-agent architectures becoming standard, 2) Better observability tools (like AgentOps!), 3) Shift from single-model to multi-model agent systems." },
      model: "gpt-4-turbo", tokIn: 180, tokOut: 95,
      trace: { reasoning: "I have enough information from both web searches. Synthesizing multiple sources for a balanced, comprehensive answer. Including specific framework names and concrete trends.", step: 4 },
      duration: 2100
    },
    {
      id: shortId(), session: s1, type: "llm_call", offset: 28.2,
      input: { prompt: "Format the final response with proper structure and citations" },
      output: { response: "Here's a structured overview of AI agent framework trends in 2026:\n\n## Top Frameworks\n1. **LangChain/LangGraph** - Flexible orchestration\n2. **CrewAI** - Multi-agent collaboration\n3. **AutoGen 2.0** - Enterprise-grade\n\n## Key Trends\n- Multi-agent architectures\n- Improved observability\n- Multi-model systems" },
      model: "gpt-4-turbo", tokIn: 42, tokOut: 9,
      trace: { reasoning: "Final formatting step for better readability. Adding structure and emphasis.", step: 5 },
      duration: 800
    },
  ];

  // ── Session 2: Code Agent ─────────────────────────────────────────
  const s2 = shortId();
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)`).run(
    s2, "code-agent-v1",
    isoDate(25), isoDate(23),
    JSON.stringify({ version: "1.0.0", language: "python", environment: "development" }),
    185, 220, "completed"
  );

  const s2Events = [
    {
      id: shortId(), session: s2, type: "llm_call", offset: 25,
      input: { prompt: "Implement a rate limiter class in Python using the token bucket algorithm" },
      output: { response: "I'll implement a thread-safe token bucket rate limiter with configurable rate and burst size." },
      model: "claude-3.5-sonnet", tokIn: 25, tokOut: 30,
      trace: { reasoning: "Token bucket is the right algorithm for this use case — it handles bursty traffic well and is straightforward to implement.", step: 1 },
      duration: 950
    },
    {
      id: shortId(), session: s2, type: "tool_call", offset: 24.5,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "file_reader", tool_input: { path: "existing_code/utils.py" }, tool_output: { content: "# Existing utility functions\nimport threading\nimport time\n..." } },
      duration: 45
    },
    {
      id: shortId(), session: s2, type: "llm_call", offset: 24,
      input: { prompt: "Write the TokenBucket class with thread safety, configurable rate, and burst handling" },
      output: { response: "class TokenBucket:\n    def __init__(self, rate, capacity):\n        self.rate = rate\n        self.capacity = capacity\n        self._tokens = capacity\n        self._lock = threading.Lock()\n        self._last_refill = time.monotonic()\n\n    def consume(self, tokens=1):\n        with self._lock:\n            self._refill()\n            if self._tokens >= tokens:\n                self._tokens -= tokens\n                return True\n            return False\n\n    def _refill(self):\n        now = time.monotonic()\n        elapsed = now - self._last_refill\n        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)\n        self._last_refill = now" },
      model: "claude-3.5-sonnet", tokIn: 60, tokOut: 120,
      trace: { reasoning: "Using threading.Lock for thread safety. time.monotonic() for reliable timing. Min-capping tokens at capacity to prevent overflow.", step: 2 },
      duration: 1800
    },
    {
      id: shortId(), session: s2, type: "llm_call", offset: 23.5,
      input: { prompt: "Write unit tests for the TokenBucket class" },
      output: { response: "def test_basic_consume():\n    bucket = TokenBucket(rate=10, capacity=10)\n    assert bucket.consume(5) == True\n    assert bucket.consume(5) == True\n    assert bucket.consume(1) == False\n\ndef test_refill():\n    bucket = TokenBucket(rate=10, capacity=10)\n    bucket.consume(10)\n    time.sleep(0.5)\n    assert bucket.consume(4) == True" },
      model: "claude-3.5-sonnet", tokIn: 50, tokOut: 45,
      trace: { reasoning: "Testing core scenarios: basic consumption, capacity limits, and refill over time. Sleep-based test for refill timing.", step: 3 },
      duration: 1200
    },
    {
      id: shortId(), session: s2, type: "llm_call", offset: 23.2,
      input: { prompt: "Review the implementation for edge cases and potential issues" },
      output: { response: "Code review passed. The implementation correctly handles: thread safety via Lock, monotonic time for reliable intervals, capacity capping. One suggestion: add a wait_for_token() method with timeout for blocking use cases." },
      model: "claude-3.5-sonnet", tokIn: 50, tokOut: 25,
      trace: { reasoning: "Self-review step. The core implementation is solid. Suggesting an enhancement for blocking scenarios, but the current implementation is complete for the basic requirement.", step: 4 },
      duration: 900
    },
  ];

  // ── Session 3: Customer Support Agent (with error) ────────────────
  const s3 = shortId();
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)`).run(
    s3, "support-agent-v3",
    isoDate(15), isoDate(12),
    JSON.stringify({ version: "3.0.1", environment: "staging", customer_id: "C-4821" }),
    312, 178, "completed"
  );

  const s3Events = [
    {
      id: shortId(), session: s3, type: "llm_call", offset: 15,
      input: { prompt: "Customer message: 'I can't login to my account. I've tried resetting my password 3 times.'" },
      output: { response: "I'll help troubleshoot. Let me check the customer's account status and recent login attempts." },
      model: "gpt-4o", tokIn: 40, tokOut: 25,
      trace: { reasoning: "Customer is frustrated (3 failed attempts). Need to check account status immediately rather than asking more questions.", step: 1 },
      duration: 800
    },
    {
      id: shortId(), session: s3, type: "tool_call", offset: 14.5,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "crm_lookup", tool_input: { customer_id: "C-4821", fields: ["account_status", "last_login", "failed_attempts"] }, tool_output: { account_status: "locked", last_login: "2026-02-10", failed_attempts: 5, lock_reason: "too_many_failures" } },
      duration: 230
    },
    {
      id: shortId(), session: s3, type: "tool_call", offset: 14,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "email_search", tool_input: { customer_id: "C-4821", subject: "password reset" }, tool_output: { emails_found: 3, latest: { sent_at: "2026-02-13T10:00:00Z", status: "delivered" } } },
      duration: 340
    },
    {
      id: shortId(), session: s3, type: "llm_call", offset: 13.5,
      input: { prompt: "Account is locked due to 5 failed attempts. Password reset emails were delivered. Diagnose the issue." },
      output: { response: "The account is locked (not just wrong password). Reset emails are being delivered, but the lock prevents login even with correct credentials. Need to unlock the account first." },
      model: "gpt-4o", tokIn: 82, tokOut: 48,
      trace: { reasoning: "Root cause identified: account lock, not password issue. The customer's resets won't help because the lock is independent. Need admin action to unlock.", step: 3 },
      duration: 1100
    },
    {
      id: shortId(), session: s3, type: "tool_call", offset: 13,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "account_admin", tool_input: { action: "unlock", customer_id: "C-4821", reason: "Support ticket - customer verified" }, tool_output: { success: true, new_status: "active" } },
      duration: 180
    },
    {
      id: shortId(), session: s3, type: "llm_call", offset: 12.5,
      input: { prompt: "Account unlocked. Compose a helpful response to the customer." },
      output: { response: "Hi there! I found the issue — your account was temporarily locked after multiple login attempts. I've unlocked it for you. You should be able to log in now with your most recent password reset. If you're still having trouble, try clearing your browser cache or using an incognito window. Let me know if you need anything else!" },
      model: "gpt-4o", tokIn: 90, tokOut: 65,
      trace: { reasoning: "Account is now unlocked. Being transparent about the issue, providing the fix, and offering additional troubleshooting tips proactively. Keeping the tone friendly given customer frustration.", step: 5 },
      duration: 1400
    },
    {
      id: shortId(), session: s3, type: "llm_call", offset: 12.2,
      input: { prompt: "Categorize this ticket and add internal notes" },
      output: { response: "Category: Account Access > Account Locked\nPriority: Medium\nResolution: Account unlocked manually\nNote: Consider implementing auto-unlock after password reset to prevent this recurring pattern." },
      model: "gpt-4o", tokIn: 100, tokOut: 40,
      trace: { reasoning: "Adding categorization and noting a process improvement suggestion. This pattern (lock + reset not unlocking) should be fixed in the product.", step: 6 },
      duration: 650
    },
  ];

  // ── Session 4: Data Analysis Agent (active/running) ───────────────
  const s4 = shortId();
  db.prepare(`INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)`).run(
    s4, "data-analyst-v1",
    isoDate(5), null,
    JSON.stringify({ version: "1.2.0", dataset: "sales_q4_2025.csv", environment: "production" }),
    156, 89, "active"
  );

  const s4Events = [
    {
      id: shortId(), session: s4, type: "llm_call", offset: 5,
      input: { prompt: "Analyze the Q4 2025 sales dataset and identify top trends" },
      output: { response: "I'll load the dataset and run exploratory analysis, focusing on revenue trends, top products, and regional performance." },
      model: "gpt-4-turbo", tokIn: 30, tokOut: 25,
      trace: { reasoning: "Standard EDA workflow: load data, check shape, identify key metrics, then drill into trends.", step: 1 },
      duration: 700
    },
    {
      id: shortId(), session: s4, type: "tool_call", offset: 4.5,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "python_exec", tool_input: { code: "import pandas as pd\ndf = pd.read_csv('sales_q4_2025.csv')\nprint(df.shape, df.columns.tolist())" }, tool_output: { stdout: "(15420, 12) ['date', 'product', 'region', 'revenue', 'units', 'cost', 'margin', 'category', 'channel', 'customer_segment', 'discount', 'return_rate']" } },
      duration: 1200
    },
    {
      id: shortId(), session: s4, type: "tool_call", offset: 4,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "python_exec", tool_input: { code: "print(df.groupby('region')['revenue'].sum().sort_values(ascending=False))" }, tool_output: { stdout: "region\nNorth America    4521000\nEurope          3890000\nAsia Pacific    2750000\nLatin America   1230000" } },
      duration: 340
    },
    {
      id: shortId(), session: s4, type: "llm_call", offset: 3.5,
      input: { prompt: "Interpret the regional revenue data and identify patterns" },
      output: { response: "North America leads with $4.5M, followed by Europe at $3.9M. Asia Pacific shows strong growth potential at $2.7M. Latin America underperforms — needs investigation into channel strategy." },
      model: "gpt-4-turbo", tokIn: 76, tokOut: 44,
      trace: { reasoning: "Regional breakdown shows expected NA/EU dominance. APAC is interesting — relatively high for Q4. LATAM gap suggests a strategic issue worth flagging.", step: 3 },
      duration: 950
    },
    {
      id: shortId(), session: s4, type: "tool_call", offset: 3,
      input: null, output: null, model: null, tokIn: 0, tokOut: 0,
      tool: { tool_name: "chart_generator", tool_input: { type: "bar", data: { labels: ["NA", "EU", "APAC", "LATAM"], values: [4521000, 3890000, 2750000, 1230000] }, title: "Q4 2025 Revenue by Region" }, tool_output: { chart_url: "/charts/revenue_by_region.png" } },
      duration: 560
    },
    {
      id: shortId(), session: s4, type: "llm_call", offset: 2.5,
      input: { prompt: "Generate executive summary with key insights and recommendations" },
      output: { response: "Q4 2025 Executive Summary: Total revenue $12.4M across 15,420 transactions. Key insights: 1) NA remains strongest market, 2) EU growth +12% YoY, 3) APAC emerging as growth engine, 4) LATAM needs channel optimization. Recommendation: Increase APAC investment and restructure LATAM go-to-market strategy." },
      model: "gpt-4-turbo", tokIn: 50, tokOut: 20,
      trace: { reasoning: "Condensing analysis into executive-friendly format. Leading with the number that matters ($12.4M), then key insights with actionable recommendations.", step: 5 },
      duration: 1100
    },
  ];

  // Insert all events
  const insertEvent = db.prepare(`
    INSERT INTO events (event_id, session_id, event_type, timestamp, input_data, output_data, model, tokens_in, tokens_out, tool_call, decision_trace, duration_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const allEvents = [...s1Events, ...s2Events, ...s3Events, ...s4Events];

  const insertAll = db.transaction((events) => {
    for (const e of events) {
      insertEvent.run(
        e.id,
        e.session,
        e.type,
        isoDate(e.offset),
        e.input ? JSON.stringify(e.input) : null,
        e.output ? JSON.stringify(e.output) : null,
        e.model || null,
        e.tokIn || 0,
        e.tokOut || 0,
        e.tool ? JSON.stringify(e.tool) : null,
        e.trace ? JSON.stringify(e.trace) : null,
        e.duration || null
      );
    }
  });

  insertAll(allEvents);

  const sessionCount = db.prepare("SELECT COUNT(*) as c FROM sessions").get().c;
  const eventCount = db.prepare("SELECT COUNT(*) as c FROM events").get().c;

  console.log(`✅ Seeded ${sessionCount} sessions and ${eventCount} events`);
  console.log("   Sessions:");
  console.log(`   - ${s1}: research-agent-v2 (completed)`);
  console.log(`   - ${s2}: code-agent-v1 (completed)`);
  console.log(`   - ${s3}: support-agent-v3 (completed)`);
  console.log(`   - ${s4}: data-analyst-v1 (active)`);
}

seed();
