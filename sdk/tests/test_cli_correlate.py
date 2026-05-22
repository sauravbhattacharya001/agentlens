"""Tests for ``agentlens.cli_correlate`` — Pearson-correlation CLI.

This module was previously uncovered by a dedicated test file, even
though it ships as a public ``agentlens-cli correlate`` sub-command.
The tests below cover the pure helpers (``_pearson``,
``_extract_metric``, ``_strength_label``, ``_format_table``,
``_format_csv``) as well as the end-to-end ``run`` orchestration via
monkey-patched ``get_client`` / ``fetch_sessions``.

Pearson reference values were computed independently to guard against
regressions in the in-house implementation.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agentlens import cli_correlate
from agentlens.cli_correlate import (
    ALL_METRICS,
    _extract_metric,
    _format_csv,
    _format_table,
    _pearson,
    _strength_label,
    run,
    setup_parser,
)


# ---------------------------------------------------------------------------
# _pearson
# ---------------------------------------------------------------------------

class TestPearson:
    def test_perfect_positive(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        r = _pearson(xs, ys)
        assert r is not None
        assert math.isclose(r, 1.0, rel_tol=1e-9)

    def test_perfect_negative(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [10.0, 8.0, 6.0, 4.0, 2.0]
        r = _pearson(xs, ys)
        assert r is not None
        assert math.isclose(r, -1.0, rel_tol=1e-9)

    def test_uncorrelated(self) -> None:
        # Symmetric V-shape around the mean: cov(x, y) sums to 0.
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [4.0, 2.0, 1.0, 2.0, 4.0]
        r = _pearson(xs, ys)
        assert r is not None
        assert abs(r) < 1e-9

    def test_too_few_points_returns_none(self) -> None:
        assert _pearson([], []) is None
        assert _pearson([1.0], [2.0]) is None
        assert _pearson([1.0, 2.0], [3.0, 4.0]) is None

    def test_constant_series_returns_none(self) -> None:
        # Zero variance in xs ⇒ undefined Pearson.
        assert _pearson([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0]) is None
        # Zero variance in ys ⇒ undefined Pearson.
        assert _pearson([1.0, 2.0, 3.0, 4.0], [7.0, 7.0, 7.0, 7.0]) is None

    def test_known_value(self) -> None:
        # Reference computed independently with numpy.corrcoef.
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        ys = [2.1, 2.9, 3.6, 5.5, 5.0, 7.2]
        r = _pearson(xs, ys)
        assert r is not None
        assert math.isclose(r, 0.96044, abs_tol=1e-4)


# ---------------------------------------------------------------------------
# _extract_metric
# ---------------------------------------------------------------------------

class TestExtractMetric:
    def test_cost_prefers_total_cost(self) -> None:
        assert _extract_metric({"total_cost": 1.23, "cost": 9.99}, "cost") == 1.23

    def test_cost_falls_back_to_cost(self) -> None:
        assert _extract_metric({"cost": 0.50}, "cost") == 0.50

    def test_cost_defaults_to_zero(self) -> None:
        assert _extract_metric({}, "cost") == 0.0

    def test_tokens_prefers_total_tokens(self) -> None:
        assert _extract_metric({"total_tokens": 4096}, "tokens") == 4096

    def test_tokens_falls_back_to_prompt_plus_completion(self) -> None:
        assert (
            _extract_metric({"prompt_tokens": 100, "completion_tokens": 50}, "tokens")
            == 150
        )

    def test_duration_iso_timestamps(self) -> None:
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        end = (
            datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=90)
        ).isoformat()
        assert _extract_metric({"started_at": start, "ended_at": end}, "duration") == 90.0

    def test_duration_falls_back_to_duration_ms(self) -> None:
        assert _extract_metric({"duration_ms": 2500}, "duration") == 2.5

    def test_duration_missing_returns_none(self) -> None:
        assert _extract_metric({}, "duration") is None

    def test_duration_unparseable_timestamps_falls_back_to_ms(self) -> None:
        # Bad ISO ⇒ both parses return None ⇒ tries duration_ms fallback.
        assert _extract_metric(
            {"started_at": "garbage", "ended_at": "more-garbage", "duration_ms": 4000},
            "duration",
        ) == 4.0

    def test_events(self) -> None:
        assert _extract_metric({"event_count": 7}, "events") == 7
        assert _extract_metric({"total_events": 9}, "events") == 9
        assert _extract_metric({}, "events") == 0

    def test_errors(self) -> None:
        assert _extract_metric({"error_count": 3}, "errors") == 3
        assert _extract_metric({"errors": 11}, "errors") == 11
        assert _extract_metric({}, "errors") == 0

    def test_tool_calls(self) -> None:
        assert _extract_metric({"tool_call_count": 4}, "tool_calls") == 4
        assert _extract_metric({"tool_calls": 6}, "tool_calls") == 6
        assert _extract_metric({}, "tool_calls") == 0

    def test_models_list_returns_length(self) -> None:
        assert _extract_metric({"models": ["gpt-4o", "claude"]}, "models") == 2

    def test_models_non_list_truthy_returns_one(self) -> None:
        assert _extract_metric({"models": "gpt-4o"}, "models") == 1

    def test_models_missing_returns_zero(self) -> None:
        assert _extract_metric({}, "models") == 0

    def test_unknown_metric_returns_none(self) -> None:
        assert _extract_metric({"cost": 1.0}, "unknown") is None


# ---------------------------------------------------------------------------
# _strength_label
# ---------------------------------------------------------------------------

class TestStrengthLabel:
    @pytest.mark.parametrize(
        "r,label",
        [
            (0.9, "strong"),
            (-0.85, "strong"),
            (0.8, "strong"),
            (0.55, "moderate"),
            (-0.5, "moderate"),
            (0.4, "weak"),
            (-0.3, "weak"),
            (0.1, "negligible"),
            (0.0, "negligible"),
            (-0.29, "negligible"),
        ],
    )
    def test_thresholds(self, r: float, label: str) -> None:
        assert _strength_label(r) == label


# ---------------------------------------------------------------------------
# _format_table / _format_csv
# ---------------------------------------------------------------------------

class TestFormatters:
    @pytest.fixture
    def sample_rows(self) -> list[dict]:
        return [
            {"metric_a": "cost", "metric_b": "tokens", "r": 0.95, "strength": "strong", "n": 100},
            {"metric_a": "cost", "metric_b": "errors", "r": -0.42, "strength": "weak", "n": 100},
            {"metric_a": "errors", "metric_b": "tokens", "r": None, "strength": "N/A", "n": 0},
        ]

    def test_table_includes_header_and_rows(self, sample_rows: list[dict]) -> None:
        text = _format_table(sample_rows)
        assert "Metric A" in text
        assert "Metric B" in text
        assert "Strength" in text
        # 3 data rows + header + divider = 5 lines.
        assert len(text.splitlines()) == 5
        assert "+0.9500" in text
        assert "-0.4200" in text
        # None correlation renders as "N/A".
        assert "N/A" in text

    def test_table_empty(self) -> None:
        text = _format_table([])
        # Header + divider only.
        assert len(text.splitlines()) == 2

    def test_csv_round_trip(self, sample_rows: list[dict]) -> None:
        text = _format_csv(sample_rows)
        reader = list(csv.DictReader(io.StringIO(text)))
        assert len(reader) == 3
        assert reader[0]["metric_a"] == "cost"
        assert reader[0]["metric_b"] == "tokens"
        assert reader[0]["r"] == "0.95"
        # CSV serializes None as empty string.
        assert reader[2]["r"] == ""


# ---------------------------------------------------------------------------
# run() — end-to-end via monkey-patched cli_common dependencies
# ---------------------------------------------------------------------------

def _make_session(i: int) -> dict[str, Any]:
    """Build a session with cost & tokens linearly related (perfect r=1)."""
    return {
        "total_cost": float(i),
        "total_tokens": float(i * 10),
        "event_count": i,
        "error_count": 0,
        "tool_call_count": i // 2,
        "models": ["gpt-4o"] if i else [],
    }


@pytest.fixture
def patched_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub get_client + fetch_sessions to feed deterministic data."""
    sessions = [_make_session(i) for i in range(1, 21)]  # 20 sessions

    def fake_get_client(args: Any) -> tuple[object, str]:
        return object(), "http://localhost"

    def fake_fetch_sessions(client: Any, limit: int = 200) -> list[dict]:
        return sessions[:limit]

    monkeypatch.setattr(cli_correlate, "get_client", fake_get_client)
    monkeypatch.setattr(cli_correlate, "fetch_sessions", fake_fetch_sessions)


def _ns(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "metrics": "cost,tokens,errors",
        "limit": 200,
        "min_sessions": 5,
        "format": "json",
        "output": None,
        "endpoint": None,
        "api_key": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRun:
    def test_json_output_shape(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(_ns())
        out = capsys.readouterr().out
        results = json.loads(out)
        # Three metrics → C(3,2) = 3 pairs.
        assert len(results) == 3
        pairs = {(r["metric_a"], r["metric_b"]) for r in results}
        assert pairs == {("cost", "tokens"), ("cost", "errors"), ("tokens", "errors")}
        # cost ~ tokens is a perfect linear relationship.
        cost_tokens = next(
            r for r in results if (r["metric_a"], r["metric_b"]) == ("cost", "tokens")
        )
        assert math.isclose(cost_tokens["r"], 1.0, abs_tol=1e-4)
        assert cost_tokens["strength"] == "strong"
        # errors is constant ⇒ undefined ⇒ r is None.
        cost_errors = next(
            r for r in results if (r["metric_a"], r["metric_b"]) == ("cost", "errors")
        )
        assert cost_errors["r"] is None
        assert cost_errors["strength"] == "N/A"

    def test_table_output(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(_ns(format="table"))
        out = capsys.readouterr().out
        assert "Correlation Matrix" in out
        assert "Metric A" in out
        assert "cost" in out and "tokens" in out

    def test_csv_output(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(_ns(format="csv"))
        out = capsys.readouterr().out
        rows = list(csv.DictReader(io.StringIO(out)))
        assert len(rows) == 3
        assert {"metric_a", "metric_b", "r", "strength", "n"} <= rows[0].keys()

    def test_output_to_file(
        self, patched_io: None, tmp_path: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "out.json"
        run(_ns(format="json", output=str(target)))
        captured = capsys.readouterr().out
        assert "Written to" in captured
        data = json.loads(target.read_text(encoding="utf-8"))
        assert isinstance(data, list) and len(data) == 3

    def test_unknown_metrics_filtered_then_error(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            run(_ns(metrics="bogus,alsobogus"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "at least 2 metrics" in err

    def test_default_metrics_used_when_blank(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(_ns(metrics=None))
        results = json.loads(capsys.readouterr().out)
        # C(7, 2) = 21 pairs for the full ALL_METRICS list.
        assert len(results) == 21

    def test_min_sessions_threshold(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            run(_ns(min_sessions=999))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "minimum" in err.lower() or "min-sessions" in err

    def test_results_sorted_by_abs_correlation(
        self, patched_io: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run(_ns(metrics="cost,tokens,events,errors"))
        results = json.loads(capsys.readouterr().out)
        # Filter out N/A rows, then verify monotonic non-increasing abs(r).
        rs = [abs(r["r"]) for r in results if r["r"] is not None]
        assert rs == sorted(rs, reverse=True)


# ---------------------------------------------------------------------------
# setup_parser smoke
# ---------------------------------------------------------------------------

def test_setup_parser_registers_correlate() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    setup_parser(sub)
    args = parser.parse_args(
        [
            "correlate",
            "--metrics",
            "cost,tokens",
            "--limit",
            "50",
            "--format",
            "csv",
            "--min-sessions",
            "5",
        ]
    )
    assert args.cmd == "correlate"
    assert args.metrics == "cost,tokens"
    assert args.limit == 50
    assert args.format == "csv"
    assert args.min_sessions == 5


def test_all_metrics_constant() -> None:
    # Guard against accidental reordering — public-ish constant other
    # CLIs may rely on.
    assert set(ALL_METRICS) == {
        "cost",
        "tokens",
        "duration",
        "events",
        "errors",
        "tool_calls",
        "models",
    }
