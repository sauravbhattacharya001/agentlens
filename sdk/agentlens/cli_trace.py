"""agentlens trace — render a session's events as a terminal waterfall timeline."""

from __future__ import annotations

import argparse
import sys

from agentlens.cli_common import get_client, print_json, format_duration


def cmd_trace(args: argparse.Namespace) -> None:
    """Render a session's events as a terminal waterfall/timeline with timing bars."""
    client, endpoint = get_client(args)
    use_color = not getattr(args, "no_color", False) and sys.stdout.isatty()
    output_json = getattr(args, "json", False)
    type_filter = getattr(args, "type", None)
    min_ms = getattr(args, "min_ms", None)

    # ANSI helpers
    RESET = "\033[0m" if use_color else ""
    BOLD = "\033[1m" if use_color else ""
    DIM = "\033[2m" if use_color else ""
    type_colors = {
        "llm_call": "\033[38;5;117m" if use_color else "",
        "tool_call": "\033[38;5;114m" if use_color else "",
        "decision": "\033[38;5;179m" if use_color else "",
        "error": "\033[38;5;203m" if use_color else "",
        "generic": "\033[38;5;145m" if use_color else "",
    }

    # Fetch session
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()

    # Fetch events
    resp = client.get("/events", params={"session_id": args.session_id, "limit": 5000})
    resp.raise_for_status()
    raw = resp.json()
    events = raw if isinstance(raw, list) else raw.get("events", [raw])

    # Sort by timestamp
    events.sort(key=lambda e: e.get("timestamp", ""))

    # Apply filters
    if type_filter:
        events = [e for e in events if e.get("event_type", e.get("type", "")) == type_filter]
    if min_ms is not None:
        events = [e for e in events if (e.get("duration_ms") or 0) >= min_ms]

    if not events:
        print(f"No events found for session {args.session_id}")
        return

    if output_json:
        trace_data = {
            "session_id": args.session_id,
            "agent": session_data.get("agent_name", "unknown"),
            "event_count": len(events),
            "events": [
                {
                    "event_id": e.get("event_id", e.get("id", "")),
                    "type": e.get("event_type", e.get("type", "")),
                    "model": e.get("model"),
                    "tokens_in": e.get("tokens_in", 0),
                    "tokens_out": e.get("tokens_out", 0),
                    "duration_ms": e.get("duration_ms"),
                    "timestamp": e.get("timestamp", ""),
                }
                for e in events
            ],
        }
        print_json(trace_data)
        return

    # Determine time span for bar scaling
    durations = [e.get("duration_ms") or 0 for e in events]
    max_dur = max(durations) if durations else 1
    if max_dur == 0:
        max_dur = 1
    total_dur = sum(durations)
    total_tokens_in = sum(e.get("tokens_in", 0) or 0 for e in events)
    total_tokens_out = sum(e.get("tokens_out", 0) or 0 for e in events)
    error_count = sum(1 for e in events if e.get("event_type", e.get("type", "")) == "error")

    agent = session_data.get("agent_name", "unknown")
    status = session_data.get("status", "?")

    # Header
    print(f"\n{BOLD}\U0001f50e Session Trace: {args.session_id}{RESET}")
    print(f"   Agent: {agent}  Status: {status}  Events: {len(events)}")
    print(f"   Total duration: {format_duration(total_dur)}  Tokens: {total_tokens_in:,}\u2192{total_tokens_out:,}", end="")
    if error_count:
        err_color = type_colors.get("error", "")
        print(f"  {err_color}Errors: {error_count}{RESET}")
    else:
        print()
    print()

    # Column header
    BAR_WIDTH = 30
    print(f"   {'TYPE':<12} {'MODEL':<20} {'TOKENS':>12} {'DURATION':>10}  {'WATERFALL':<{BAR_WIDTH}}")
    print(f"   {'\u2500' * 12} {'\u2500' * 20} {'\u2500' * 12} {'\u2500' * 10}  {'\u2500' * BAR_WIDTH}")

    # Render each event
    for ev in events:
        etype = ev.get("event_type", ev.get("type", "generic"))
        model = ev.get("model", "") or ""
        tok_in = ev.get("tokens_in", 0) or 0
        tok_out = ev.get("tokens_out", 0) or 0
        dur = ev.get("duration_ms") or 0
        tool = ev.get("tool_call", {})
        tool_name = ""
        if isinstance(tool, dict):
            tool_name = tool.get("tool_name", "")

        color = type_colors.get(etype, type_colors["generic"])
        tokens_str = f"{tok_in}\u2192{tok_out}" if tok_in or tok_out else "\u2014"
        dur_str = format_duration(dur) if dur else "\u2014"

        # Model or tool name display
        name_display = model[:20] if model else tool_name[:20] if tool_name else ""

        # Waterfall bar
        bar_len = int(round(dur / max_dur * BAR_WIDTH)) if dur > 0 else 0
        bar_len = max(bar_len, 1) if dur > 0 else 0

        if etype == "error":
            bar_char = "\u2593"
        elif etype == "llm_call":
            bar_char = "\u2588"
        elif etype == "tool_call":
            bar_char = "\u2592"
        else:
            bar_char = "\u2591"

        bar = bar_char * bar_len + " " * (BAR_WIDTH - bar_len)
        err_mark = " \u2717" if etype == "error" else ""

        print(f"   {color}{etype:<12}{RESET} {DIM}{name_display:<20}{RESET} {tokens_str:>12} {dur_str:>10}  {color}{bar}{RESET}{err_mark}")

    # Summary footer
    print(f"\n   {'\u2500' * (12 + 20 + 12 + 10 + BAR_WIDTH + 6)}")
    type_counts: dict[str, int] = {}
    type_durations: dict[str, float] = {}
    for ev in events:
        et = ev.get("event_type", ev.get("type", "generic"))
        type_counts[et] = type_counts.get(et, 0) + 1
        type_durations[et] = type_durations.get(et, 0) + (ev.get("duration_ms") or 0)

    print(f"   {BOLD}Breakdown:{RESET}")
    for et in sorted(type_counts, key=lambda k: type_durations.get(k, 0), reverse=True):
        color = type_colors.get(et, type_colors["generic"])
        pct = type_durations[et] / total_dur * 100 if total_dur > 0 else 0
        print(f"     {color}{et:<15}{RESET} {type_counts[et]:>4} events  {format_duration(type_durations[et]):>8}  ({pct:.1f}%)")

    print()
