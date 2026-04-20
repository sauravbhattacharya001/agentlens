"""AgentLens CLI — session replay in the terminal.

Extracted from cli.py to keep the main CLI dispatcher lean.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from agentlens.cli_common import get_client as _get_client
from agentlens.models import AgentEvent, Session, ToolCall


def _parse_ts(val: Any) -> datetime:
    """Parse a timestamp value into a datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        val = val.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def build_session_from_api(session_data: dict, events_data: list[dict]) -> Session:
    """Construct a Session + AgentEvent tree from raw API dicts.

    This is a public helper so other modules (tests, scripts) can reuse
    the same reconstruction logic without importing private functions.
    """
    events: list[AgentEvent] = []
    for raw in events_data:
        tc = None
        if raw.get("tool_call"):
            tc_raw = raw["tool_call"]
            tc = ToolCall(
                tool_call_id=tc_raw.get("tool_call_id", ""),
                tool_name=tc_raw.get("tool_name", "unknown"),
                tool_input=tc_raw.get("tool_input", {}),
                tool_output=tc_raw.get("tool_output"),
                duration_ms=tc_raw.get("duration_ms"),
            )
        events.append(AgentEvent(
            event_id=raw.get("event_id", raw.get("id", "")),
            session_id=raw.get("session_id", session_data.get("session_id", "")),
            event_type=raw.get("event_type", raw.get("type", "generic")),
            timestamp=_parse_ts(raw.get("timestamp")),
            model=raw.get("model"),
            tokens_in=raw.get("tokens_in", 0),
            tokens_out=raw.get("tokens_out", 0),
            tool_call=tc,
            duration_ms=raw.get("duration_ms"),
        ))

    session = Session(
        session_id=session_data.get("session_id", session_data.get("id", "unknown")),
        agent_name=session_data.get("agent_name", "unknown"),
        started_at=_parse_ts(session_data.get("started_at", session_data.get("created_at"))),
        ended_at=_parse_ts(session_data["ended_at"]) if session_data.get("ended_at") else None,
        status=session_data.get("status", "completed"),
        events=events,
        total_tokens_in=sum(e.tokens_in for e in events),
        total_tokens_out=sum(e.tokens_out for e in events),
    )
    return session


def cmd_replay(args: argparse.Namespace) -> None:
    """Replay a session event-by-event in the terminal.

    Fetches session data and events from the API, then uses
    SessionReplayer to produce a formatted chronological replay
    with optional speed control, type filtering, and multiple
    output formats.
    """
    import time as _time
    from agentlens.replayer import SessionReplayer

    client, _ = _get_client(args)

    # Fetch session metadata
    resp = client.get(f"/sessions/{args.session_id}")
    resp.raise_for_status()
    session_data = resp.json()

    # Fetch events
    resp = client.get("/events", params={"session_id": args.session_id, "limit": 10000})
    resp.raise_for_status()
    events_raw = resp.json()
    if isinstance(events_raw, dict):
        events_raw = events_raw.get("events", [events_raw])

    if not events_raw:
        print(f"No events found for session {args.session_id}")
        return

    session = build_session_from_api(session_data, events_raw)
    replayer = SessionReplayer(session, speed=args.speed)

    # Apply type filters
    if args.type:
        replayer.add_filter(*[t.strip() for t in args.type.split(",")])
    if args.exclude:
        replayer.exclude(*[t.strip() for t in args.exclude.split(",")])

    # Non-live output formats
    fmt = args.format or "text"
    if fmt == "json":
        output = replayer.to_json()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\u2705 JSON replay written to {args.output}")
        else:
            print(output)
        return

    if fmt == "markdown":
        output = replayer.to_markdown()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\u2705 Markdown replay written to {args.output}")
        else:
            print(output)
        return

    # Live text mode
    if args.live:
        _replay_live(session, replayer, args)
    else:
        output = replayer.to_text()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"\u2705 Text replay written to {args.output}")
        else:
            print(output)


def _replay_live(
    session: Session,
    replayer: Any,
    args: argparse.Namespace,
) -> None:
    """Stream replay frames to the terminal with real-time delays."""
    import time as _time

    print(
        f"\u25b6 Replaying session {session.session_id}"
        f"  agent={session.agent_name}  speed={args.speed}x"
        f"  events={len(replayer.filtered_events)}"
    )
    print()

    use_color = not getattr(args, "no_color", False)

    _TYPE_COLORS = {
        "llm_call": "\033[36m",     # cyan
        "tool_call": "\033[33m",    # yellow
        "error": "\033[31m",        # red
        "decision": "\033[35m",     # magenta
        "guardrail": "\033[32m",    # green
    }
    _RESET = "\033[0m"

    for frame in replayer.play():
        if frame.wall_delay_ms > 0 and frame.index > 0:
            _time.sleep(frame.wall_delay_ms / 1000.0)

        e = frame.event
        idx_str = f"[{frame.index + 1:>3}/{frame.total}]"
        pct_str = f"{frame.progress_pct:5.1f}%"

        type_str = e.event_type
        if use_color:
            color = _TYPE_COLORS.get(e.event_type, "\033[37m")
            type_str = f"{color}{e.event_type}{_RESET}"

        parts = [idx_str, type_str]
        if e.model:
            parts.append(f"model={e.model}")
        if e.tool_call:
            parts.append(f"tool={e.tool_call.tool_name}")
        if e.duration_ms is not None:
            parts.append(f"dur={e.duration_ms:.0f}ms")
        if e.tokens_in or e.tokens_out:
            parts.append(f"tok={e.tokens_in}\u2192{e.tokens_out}")
        if frame.is_breakpoint:
            bp_marker = "\033[31;1m\u23f8 BREAK\033[0m" if use_color else "\u23f8 BREAK"
            parts.append(bp_marker)

        bar_width = 20
        filled = int(frame.progress * bar_width)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
        parts.append(f"[{bar}] {pct_str}")

        print(" | ".join(parts))

    print()
    print(replayer.stats.summary())
