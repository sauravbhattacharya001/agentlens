"""CLI subcommand: agentlens hallucination — agent hallucination detection.

Analyzes agent sessions for hallucination signals: self-contradictions,
fabricated references, confidence-reality gaps, and more.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List

from agentlens.cli_common import print_json


TIER_ICONS = {
    "excellent": "🧠",
    "good": "✅",
    "questionable": "⚠️",
    "poor": "🔻",
    "unreliable": "❌",
}

TYPE_ICONS = {
    "self_contradiction": "🔄",
    "fabricated_reference": "📚",
    "confidence_reality_gap": "🎯",
    "unverifiable_assertion": "❓",
    "consistency_drift": "🌊",
    "phantom_knowledge": "👻",
    "specificity_escalation": "📈",
    "hedging_collapse": "🛡️",
}


def register_hallucination_parser(subparsers: Any) -> None:
    """Register the hallucination subcommand."""
    p = subparsers.add_parser(
        "hallucination",
        help="Detect hallucination signals in agent sessions",
        description="Autonomous hallucination detection: self-contradictions, "
        "fabricated references, confidence-reality gaps, and more.",
    )
    p.add_argument("--demo", action="store_true", help="Run with demo data")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum confidence threshold (default: 0.5)",
    )
    p.set_defaults(func=cmd_hallucination)


def cmd_hallucination(args: argparse.Namespace) -> None:
    """Run hallucination detection."""
    from agentlens.hallucination import HallucinationDetector

    if args.demo:
        _run_demo(args)
        return

    # Load session from stdin (JSON)
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        print("Pipe a JSON session to stdin or use --demo.")
        print('Format: {"id":"sess-001","events":[{"type":"assistant","content":"..."},…]}')
        sys.exit(1)

    detector = HallucinationDetector(min_confidence=args.min_confidence)
    report = detector.analyze(data)

    if args.json:
        print_json(report.to_dict())
    else:
        print(report.format_report())


def _run_demo(args: argparse.Namespace) -> None:
    """Run with demo data showcasing hallucination patterns."""
    from agentlens.hallucination import HallucinationDetector

    events = _build_demo_events()

    detector = HallucinationDetector(min_confidence=args.min_confidence)
    report = detector.analyze({"id": "demo-hallucination-001", "events": events})

    if getattr(args, "json", False):
        print_json(report.to_dict())
    else:
        print(report.format_report())
        print()
        print(f"  Demo: {len(events)} events analyzed")
        icon = TIER_ICONS.get(report.tier, "")
        print(f"  Tier: {icon} {report.tier.title()}")
        print(f"  Signals: {report.signals_detected}")
        print()


def _build_demo_events() -> List[dict]:
    """Build realistic demo events that trigger hallucination detectors."""
    events: List[dict] = []

    # 1. User asks about a topic
    events.append({"type": "user", "content": "What are the best practices for API rate limiting?"})

    # 2. Agent responds with hedging (good)
    events.append({
        "type": "assistant",
        "content": (
            "Rate limiting is probably one of the most important aspects of API design. "
            "I think there are several approaches you might consider, including token bucket "
            "and sliding window algorithms. Perhaps the best choice depends on your use case."
        ),
    })

    # 3. User follow-up
    events.append({"type": "user", "content": "Can you give me specific numbers?"})

    # 4. Agent: hedging collapses + specificity escalates + fabricated reference
    events.append({
        "type": "assistant",
        "content": (
            "Absolutely! The industry standard is definitely 1000 requests per minute for public APIs. "
            "According to the seminal paper by Chen & Roberts (2019), 'Optimal Rate Limiting in "
            "Distributed Systems', the ideal ratio is exactly 2.7 requests per second per user. "
            "This was confirmed in a study showing 94.3% of production APIs use this exact threshold."
        ),
    })

    # 5. Tool call fails
    events.append({
        "type": "tool_call",
        "tool_name": "search_docs",
        "content": "Searching for rate limiting standards",
        "success": False,
        "tool_result": "Error: API documentation not found",
    })

    # 6. Agent continues confidently despite failure
    events.append({
        "type": "assistant",
        "content": (
            "As I mentioned earlier, the standard is certainly 1000 RPM. Additionally, "
            "Google's API guidelines v3.2.1 recommend using exponential backoff with a "
            "base delay of exactly 1.5 seconds, which has been proven to reduce errors by 87.2%."
        ),
    })

    # 7. User asks about a different aspect
    events.append({"type": "user", "content": "What about authentication for the rate limiter?"})

    # 8. Agent contradicts earlier claim
    events.append({
        "type": "assistant",
        "content": (
            "The rate limit should never be more than 500 requests per minute for authenticated users. "
            "This is a well-known security principle. JWT tokens are always the best choice for "
            "authentication — there is no doubt about this. The standard timeout is 3600 seconds."
        ),
    })

    # 9. Agent references things not discussed
    events.append({
        "type": "assistant",
        "content": (
            "As you mentioned in your earlier question about database sharding, the connection "
            "pooling strategy directly affects rate limiting. According to the output from our "
            "previous analysis, the optimal pool size is 25 connections."
        ),
    })

    # 10. More specificity without new info
    events.append({
        "type": "assistant",
        "content": (
            "Specifically, you should configure Redis with exactly 16 shards, each handling "
            "precisely 62.5 requests per second. The Lua script should use atomic counters with "
            "a TTL of exactly 60.001 seconds to avoid race conditions. Version 7.0.11 of Redis "
            "introduced the RATELIMIT command which handles this with 99.97% accuracy."
        ),
    })

    # 11-15: More events to give enough data
    events.append({"type": "user", "content": "Should I use a library or build my own?"})

    events.append({
        "type": "assistant",
        "content": (
            "According to the benchmark published on March 15, 2024 by the Cloud Native Computing "
            "Foundation, the top 3 rate limiting libraries are: RateLimiter.js v4.2.0, "
            "throttle-pro v2.8.3, and limit-express v6.1.0. Each has been downloaded over "
            "15 million times. The study tested 47 different scenarios across 12 cloud providers."
        ),
    })

    events.append({
        "type": "tool_call",
        "tool_name": "npm_search",
        "content": "rate limiting",
        "success": True,
        "tool_result": "Found: express-rate-limit, rate-limiter-flexible, bottleneck",
    })

    events.append({
        "type": "assistant",
        "content": (
            "The tool results confirm what I said — express-rate-limit is the most popular choice. "
            "It handles 50,000 concurrent connections with only 2.3ms latency overhead, as documented "
            "in their benchmark at https://github.com/express-rate-limit/benchmarks/results/2024/q2/detailed-report.json."
        ),
    })

    # 15: Agent now contradicts RPM claim again
    events.append({
        "type": "assistant",
        "content": (
            "To summarize: the industry standard rate limit is 2000 requests per minute, "
            "which every major API gateway supports out of the box. This has been true since "
            "REST APIs were first standardized in January 15, 2008."
        ),
    })

    # Add more padding events
    for j in range(16, 40):
        if j % 3 == 0:
            events.append({"type": "user", "content": f"Tell me more about aspect {j}."})
        elif j % 3 == 1:
            events.append({
                "type": "assistant",
                "content": (
                    f"Certainly, aspect {j} is definitely important. According to the "
                    f"documentation, the recommended value is always {j * 17} units. "
                    f"This was established in version {j}.{j+1}.{j+2} of the specification."
                ),
            })
        else:
            events.append({
                "type": "assistant",
                "content": (
                    f"As I mentioned previously, the value should be exactly {j * 23}. "
                    f"The study by Anderson et al. ({2020 + j % 5}) confirmed this with "
                    f"a sample size of {j * 1000} participants across {j} countries."
                ),
            })

    return events
