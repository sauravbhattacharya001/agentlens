"""CLI ``scatter`` command — terminal scatter plot of two session metrics.

Usage:
    agentlens-cli scatter [--x METRIC] [--y METRIC] [--limit N] [--width W]
                          [--height H] [--agent NAME] [--no-trend]
                          [--format ascii|json] [--output FILE]
                          [--endpoint URL] [--api-key KEY]

Fetches sessions and renders a Unicode scatter plot directly in the terminal,
letting you visually spot clusters, outliers, and trends across any pair of
numeric metrics (cost, tokens, duration, events, errors, tool_calls).

Examples:
    agentlens-cli scatter                            # cost vs tokens (default)
    agentlens-cli scatter --x duration --y cost      # duration vs cost
    agentlens-cli scatter --x tokens --y errors --agent my-agent
    agentlens-cli scatter --width 100 --height 30 --no-trend
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from typing import Any

from agentlens.cli_common import get_client, fetch_sessions

METRICS = ["cost", "tokens", "duration", "events", "errors", "tool_calls"]

# Braille-based dot characters for scatter rendering
_DOT = "·"
_POINT = "●"
_TREND = "─"


def _extract_metric(session: dict, metric: str) -> float | None:
    """Extract a numeric metric from a session dict."""
    if metric == "cost":
        return session.get("total_cost") or session.get("cost") or 0.0
    if metric == "tokens":
        return (
            session.get("total_tokens")
            or session.get("tokens")
            or (session.get("prompt_tokens", 0) + session.get("completion_tokens", 0))
        )
    if metric == "duration":
        start = session.get("started_at") or session.get("created_at")
        end = session.get("ended_at") or session.get("updated_at")
        if start and end:
            try:
                t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                return (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass
        return session.get("duration_ms", 0) / 1000.0 if session.get("duration_ms") else None
    if metric == "events":
        return session.get("event_count") or session.get("total_events") or 0
    if metric == "errors":
        return session.get("error_count") or session.get("errors") or 0
    if metric == "tool_calls":
        return session.get("tool_call_count") or session.get("tool_calls") or 0
    return None


def _format_axis_value(v: float) -> str:
    """Format a numeric value for axis labels."""
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    if abs(v) < 0.01 and v != 0:
        return f"{v:.2e}"
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Simple OLS. Returns (slope, intercept) or None."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def render_scatter(
    xs: list[float],
    ys: list[float],
    x_label: str,
    y_label: str,
    width: int = 60,
    height: int = 20,
    show_trend: bool = True,
) -> str:
    """Render a Unicode scatter plot to a string."""
    if not xs or not ys:
        return "(no data to plot)"

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # Avoid zero-range
    if x_max == x_min:
        x_max = x_min + 1
    if y_max == y_min:
        y_max = y_min + 1

    # Pad ranges by 5%
    x_pad = (x_max - x_min) * 0.05
    y_pad = (y_max - y_min) * 0.05
    x_min -= x_pad
    x_max += x_pad
    y_min -= y_pad
    y_max += y_pad

    # Build grid
    grid: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]

    # Plot trend line first (so points overwrite)
    regression = _linear_regression(xs, ys) if show_trend else None
    if regression:
        slope, intercept = regression
        for col in range(width):
            x_val = x_min + (col / (width - 1)) * (x_max - x_min)
            y_val = slope * x_val + intercept
            row = int((1 - (y_val - y_min) / (y_max - y_min)) * (height - 1))
            if 0 <= row < height:
                grid[row][col] = "╌"

    # Plot points (count density)
    density: dict[tuple[int, int], int] = {}
    for x, y in zip(xs, ys):
        col = int((x - x_min) / (x_max - x_min) * (width - 1))
        row = int((1 - (y - y_min) / (y_max - y_min)) * (height - 1))
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 1, row))
        density[(row, col)] = density.get((row, col), 0) + 1

    for (row, col), count in density.items():
        if count >= 5:
            grid[row][col] = "█"
        elif count >= 3:
            grid[row][col] = "◆"
        elif count >= 2:
            grid[row][col] = "●"
        else:
            grid[row][col] = "•"

    # Build output
    lines: list[str] = []
    y_label_pad = 10  # width for y-axis labels

    # Title
    r = _pearson(xs, ys)
    title = f"  Scatter: {x_label} vs {y_label}  (n={len(xs)}"
    if r is not None:
        title += f", r={r:+.3f}"
    title += ")"
    lines.append(title)
    lines.append("")

    # Y-axis labels at top, middle, bottom
    y_labels = {
        0: _format_axis_value(y_max),
        height // 2: _format_axis_value((y_min + y_max) / 2),
        height - 1: _format_axis_value(y_min),
    }

    for row_idx in range(height):
        label = y_labels.get(row_idx, "")
        prefix = f"{label:>{y_label_pad}} │" if row_idx in y_labels else f"{'':>{y_label_pad}} │"
        lines.append(prefix + "".join(grid[row_idx]))

    # X-axis
    lines.append(f"{'':>{y_label_pad}} └{'─' * width}")

    # X-axis labels
    x_left = _format_axis_value(x_min)
    x_mid = _format_axis_value((x_min + x_max) / 2)
    x_right = _format_axis_value(x_max)
    x_axis_line = f"{'':>{y_label_pad}}  {x_left}"
    mid_pos = width // 2 - len(x_mid) // 2
    right_pos = width - len(x_right)
    x_positions = list(f"{'':>{y_label_pad}}  " + " " * width)
    for i, ch in enumerate(x_left):
        pos = y_label_pad + 2 + i
        if pos < len(x_positions):
            x_positions[pos] = ch
    for i, ch in enumerate(x_mid):
        pos = y_label_pad + 2 + mid_pos + i
        if pos < len(x_positions):
            x_positions[pos] = ch
    for i, ch in enumerate(x_right):
        pos = y_label_pad + 2 + right_pos + i
        if pos < len(x_positions):
            x_positions[pos] = ch
    lines.append("".join(x_positions))

    # Axis names
    lines.append(f"{'':>{y_label_pad}}   {'↑ ' + y_label + ' (vertical)':<{width}}")
    lines.append(f"{'':>{y_label_pad}}   {'→ ' + x_label + ' (horizontal)':<{width}}")

    # Legend
    lines.append("")
    legend = "  Legend: • = 1 point  ● = 2  ◆ = 3-4  █ = 5+"
    if show_trend and regression:
        legend += "  ╌ = trend line"
    lines.append(legend)

    # Stats summary
    lines.append("")
    lines.append(f"  {x_label}: min={_format_axis_value(min(xs))}  max={_format_axis_value(max(xs))}  "
                 f"mean={_format_axis_value(sum(xs) / len(xs))}")
    lines.append(f"  {y_label}: min={_format_axis_value(min(ys))}  max={_format_axis_value(max(ys))}  "
                 f"mean={_format_axis_value(sum(ys) / len(ys))}")

    return "\n".join(lines)


def cmd_scatter(args: argparse.Namespace) -> None:
    """Execute the ``scatter`` sub-command."""
    client, _ = get_client(args)

    x_metric = getattr(args, "x", "cost") or "cost"
    y_metric = getattr(args, "y", "tokens") or "tokens"
    limit = getattr(args, "limit", 200) or 200
    width = getattr(args, "width", 60) or 60
    height = getattr(args, "height", 20) or 20
    agent = getattr(args, "agent", None)
    show_trend = not getattr(args, "no_trend", False)
    fmt = getattr(args, "format", "ascii") or "ascii"
    output = getattr(args, "output", None)

    # Validate metrics
    for m in (x_metric, y_metric):
        if m not in METRICS:
            print(f"Error: unknown metric '{m}'. Choose from: {', '.join(METRICS)}", file=sys.stderr)
            sys.exit(1)

    if x_metric == y_metric:
        print("Error: --x and --y must be different metrics.", file=sys.stderr)
        sys.exit(1)

    # Fetch sessions
    sessions = fetch_sessions(client, limit=limit)

    # Filter by agent if requested
    if agent:
        sessions = [
            s for s in sessions
            if (s.get("agent_name") or s.get("agent") or "") == agent
        ]

    if not sessions:
        print("No sessions found.", file=sys.stderr)
        sys.exit(1)

    # Extract paired metric values
    xs: list[float] = []
    ys: list[float] = []
    data_points: list[dict] = []

    for s in sessions:
        xv = _extract_metric(s, x_metric)
        yv = _extract_metric(s, y_metric)
        if xv is not None and yv is not None:
            xs.append(xv)
            ys.append(yv)
            data_points.append({
                "session_id": s.get("id", "?"),
                x_metric: xv,
                y_metric: yv,
            })

    if len(xs) < 2:
        print(f"Need at least 2 sessions with both {x_metric} and {y_metric}. "
              f"Found {len(xs)}.", file=sys.stderr)
        sys.exit(1)

    if fmt == "json":
        r = _pearson(xs, ys)
        reg = _linear_regression(xs, ys)
        result = {
            "x_metric": x_metric,
            "y_metric": y_metric,
            "count": len(xs),
            "correlation": round(r, 4) if r is not None else None,
            "regression": {"slope": reg[0], "intercept": reg[1]} if reg else None,
            "stats": {
                x_metric: {"min": min(xs), "max": max(xs), "mean": sum(xs) / len(xs)},
                y_metric: {"min": min(ys), "max": max(ys), "mean": sum(ys) / len(ys)},
            },
            "points": data_points,
        }
        text = json.dumps(result, indent=2)
    else:
        text = render_scatter(xs, ys, x_metric, y_metric, width, height, show_trend)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Written to {output}")
    else:
        print(text)


def register_scatter_parser(sub: Any) -> None:
    """Register the ``scatter`` sub-command."""
    p = sub.add_parser("scatter", help="Terminal scatter plot of two session metrics")
    p.add_argument("--x", default="cost", choices=METRICS, help="X-axis metric (default: cost)")
    p.add_argument("--y", default="tokens", choices=METRICS, help="Y-axis metric (default: tokens)")
    p.add_argument("--limit", type=int, default=200, help="Max sessions to fetch (default: 200)")
    p.add_argument("--width", type=int, default=60, help="Plot width in characters (default: 60)")
    p.add_argument("--height", type=int, default=20, help="Plot height in rows (default: 20)")
    p.add_argument("--agent", help="Filter by agent name")
    p.add_argument("--no-trend", action="store_true", help="Hide trend line")
    p.add_argument("--format", choices=["ascii", "json"], default="ascii", help="Output format")
    p.add_argument("--output", "-o", help="Write output to file")
    p.add_argument("--endpoint", help="AgentLens API endpoint")
    p.add_argument("--api-key", help="API key")
