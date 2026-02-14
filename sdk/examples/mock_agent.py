"""Example: Mock AI agent instrumented with AgentLens SDK.

This demonstrates a realistic agent workflow with:
- LLM calls with token tracking
- Tool calls (web search, calculator)
- Decision traces
- Session management

Run with: python mock_agent.py
Make sure the AgentLens backend is running on http://localhost:3000
"""

import random
import time
import sys
import os

# Add SDK to path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agentlens
from agentlens import track_agent, track_tool_call


# ── Tool definitions ──────────────────────────────────────────────────

@track_tool_call(tool_name="web_search")
def web_search(query: str) -> dict:
    """Simulated web search tool."""
    time.sleep(random.uniform(0.1, 0.3))  # Simulate latency
    results = {
        "What is the weather in SF?": [
            {"title": "SF Weather Today", "snippet": "Partly cloudy, 62°F, 15mph winds"},
            {"title": "SF 10-Day Forecast", "snippet": "Mild temperatures expected through the week"},
        ],
        "Python async best practices": [
            {"title": "Real Python: Async IO", "snippet": "Use asyncio.gather for concurrent tasks"},
            {"title": "PEP 492", "snippet": "Coroutines with async and await syntax"},
        ],
    }
    return results.get(query, [{"title": "No results", "snippet": "Try a different query"}])


@track_tool_call(tool_name="calculator")
def calculator(expression: str) -> str:
    """Simulated calculator tool."""
    time.sleep(random.uniform(0.05, 0.1))
    try:
        # Safe eval for simple math
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception:
        return "Error: invalid expression"


@track_tool_call(tool_name="file_reader")
def read_file(path: str) -> str:
    """Simulated file reader."""
    time.sleep(random.uniform(0.05, 0.15))
    return f"[Contents of {path}]: Lorem ipsum dolor sit amet, consectetur adipiscing elit."


# ── Agent logic ───────────────────────────────────────────────────────

@track_agent(model="gpt-4-turbo")
def research_agent(user_query: str) -> str:
    """A mock research agent that uses tools to answer questions."""
    
    # Step 1: Analyze the query
    agentlens.track(
        event_type="llm_call",
        input_data={"prompt": f"Analyze this query and decide what tools to use: {user_query}"},
        output_data={"response": "I'll search the web for current information, then synthesize."},
        model="gpt-4-turbo",
        tokens_in=45,
        tokens_out=22,
        reasoning="User asked a factual question. I need to search for up-to-date information rather than relying on training data.",
    )
    time.sleep(0.2)
    
    # Step 2: Use tools
    search_results = web_search(user_query)
    
    # Step 3: Maybe use calculator if math-related
    if any(word in user_query.lower() for word in ["calculate", "math", "compute", "how much"]):
        calc_result = calculator("42 * 1.15")
        agentlens.track(
            event_type="llm_call",
            input_data={"prompt": "Interpret the calculation result", "calc_result": calc_result},
            output_data={"response": f"The calculation gives us {calc_result}"},
            model="gpt-4-turbo",
            tokens_in=30,
            tokens_out=15,
            reasoning="Math was involved, so I used the calculator for accuracy instead of mental math.",
        )
    
    # Step 4: Read supplementary data
    file_data = read_file("knowledge_base/topic.md")
    
    # Step 5: Synthesize final answer
    agentlens.track(
        event_type="llm_call",
        input_data={
            "prompt": "Synthesize the search results and file data into a final answer",
            "search_results": str(search_results),
            "file_data": file_data[:100],
        },
        output_data={
            "response": f"Based on my research: The answer to '{user_query}' involves multiple factors. "
                        f"According to recent sources, {search_results[0]['snippet'] if isinstance(search_results, list) else 'information is available'}. "
                        f"I've cross-referenced this with our knowledge base for accuracy."
        },
        model="gpt-4-turbo",
        tokens_in=180,
        tokens_out=95,
        reasoning="I have enough information from the web search and knowledge base to provide a comprehensive answer. I'm synthesizing multiple sources for accuracy.",
    )
    
    return f"Research complete for: {user_query}"


@track_agent(model="claude-3.5-sonnet")
def code_agent(task: str) -> str:
    """A mock coding agent."""
    
    agentlens.track(
        event_type="llm_call",
        input_data={"prompt": f"Write code for: {task}"},
        output_data={"response": "def solution():\n    return 'implemented'"},
        model="claude-3.5-sonnet",
        tokens_in=25,
        tokens_out=40,
        reasoning="Straightforward coding task. Using a simple function structure.",
    )
    time.sleep(0.15)
    
    agentlens.track(
        event_type="llm_call",
        input_data={"prompt": "Review and test the code"},
        output_data={"response": "Code looks correct. All edge cases handled."},
        model="claude-3.5-sonnet",
        tokens_in=60,
        tokens_out=20,
        reasoning="Self-review step to catch bugs before returning to user.",
    )
    
    return f"Code task completed: {task}"


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("🔍 AgentLens Demo — Mock Agent Example")
    print("=" * 50)
    
    # Initialize AgentLens
    agentlens.init(api_key="demo-key-001", endpoint="http://localhost:3000")
    
    # Session 1: Research agent
    print("\n📊 Starting research agent session...")
    session1 = agentlens.start_session(
        agent_name="research-agent-v2",
        metadata={"version": "2.1.0", "environment": "demo"},
    )
    print(f"   Session ID: {session1.session_id}")
    
    result1 = research_agent("What is the weather in SF?")
    print(f"   Result: {result1}")
    
    # Print explanation
    explanation = agentlens.explain()
    print(f"\n💡 Explanation:\n{explanation}")
    
    agentlens.end_session()
    print("   ✅ Session ended")
    
    # Session 2: Code agent
    print("\n📊 Starting code agent session...")
    session2 = agentlens.start_session(
        agent_name="code-agent-v1",
        metadata={"version": "1.0.0", "language": "python"},
    )
    print(f"   Session ID: {session2.session_id}")
    
    result2 = code_agent("Implement a binary search function")
    print(f"   Result: {result2}")
    
    explanation2 = agentlens.explain()
    print(f"\n💡 Explanation:\n{explanation2}")
    
    agentlens.end_session()
    print("   ✅ Session ended")
    
    print("\n🎉 Demo complete! Check the dashboard at http://localhost:3000")


if __name__ == "__main__":
    main()
