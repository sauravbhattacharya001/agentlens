"""Regression tests for HeatmapBuilder._estimate_cost prefix matching.

Previously, ``_estimate_cost`` iterated ``cost_rates`` in dict-insertion
order and returned the first prefix that was a substring of the model
name. Because ``DEFAULT_COSTS`` defines ``"gpt-4"`` before ``"gpt-4o"``,
events with ``model="gpt-4o"`` were billed at the gpt-4 rate -- roughly
3x the actual price -- producing wildly wrong cost attributions in
heatmaps. Same shape of bug would hit any ``gpt-4-<suffix>`` model.

The fix matches the longest qualifying prefix. These tests pin that
behaviour so insertion-order regressions can't sneak back in.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentlens.heatmap import HeatmapBuilder
from agentlens.models import AgentEvent


def _event(model: str, tokens_in: int = 1000, tokens_out: int = 1000) -> AgentEvent:
    return AgentEvent(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        event_type="llm_call",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        session_id="s1",
    )


class TestLongestPrefixCostMatch:
    """Cost lookup must prefer the most specific (longest) matching prefix."""

    def test_gpt_4o_uses_4o_rate_not_gpt4_rate(self):
        """The original regression: gpt-4o was billed at gpt-4 rates (3x too high)."""
        hb = HeatmapBuilder()
        cost = hb._estimate_cost(_event("gpt-4o", 1000, 1000))
        # gpt-4o = (0.005, 0.015) per 1k tokens
        expected = (1000 * 0.005 + 1000 * 0.015) / 1000
        assert cost == pytest.approx(expected)
        # And explicitly NOT the gpt-4 rate:
        gpt4_rate = (1000 * 0.03 + 1000 * 0.06) / 1000
        assert cost != pytest.approx(gpt4_rate)

    def test_gpt_4_still_billed_at_gpt_4_rate(self):
        hb = HeatmapBuilder()
        cost = hb._estimate_cost(_event("gpt-4", 1000, 1000))
        expected = (1000 * 0.03 + 1000 * 0.06) / 1000
        assert cost == pytest.approx(expected)

    def test_claude_3_haiku_uses_haiku_rate_not_claude_3(self):
        """Defensive: simulate an ambiguous registry to lock in longest-prefix wins."""
        hb = HeatmapBuilder(cost_rates={
            "claude-3": (1.0, 1.0),
            "claude-3-haiku": (0.00025, 0.00125),
        })
        cost = hb._estimate_cost(_event("claude-3-haiku", 1000, 1000))
        expected = (1000 * 0.00025 + 1000 * 0.00125) / 1000
        assert cost == pytest.approx(expected)

    def test_longest_prefix_wins_regardless_of_insertion_order(self):
        """Same registry, different insertion orders -> identical cost."""
        rates_a = {
            "gpt-4": (0.03, 0.06),
            "gpt-4o": (0.005, 0.015),
            "gpt-4-turbo": (0.01, 0.03),
        }
        rates_b = {
            "gpt-4o": (0.005, 0.015),
            "gpt-4-turbo": (0.01, 0.03),
            "gpt-4": (0.03, 0.06),
        }
        for model in ("gpt-4", "gpt-4o", "gpt-4-turbo"):
            ev = _event(model, 1000, 1000)
            assert (
                HeatmapBuilder(cost_rates=rates_a)._estimate_cost(ev)
                == HeatmapBuilder(cost_rates=rates_b)._estimate_cost(ev)
            ), f"insertion order leaked into cost for model={model!r}"

    def test_unknown_model_returns_zero(self):
        hb = HeatmapBuilder()
        assert hb._estimate_cost(_event("mystery-model-9000")) == 0.0

    def test_empty_model_returns_zero(self):
        hb = HeatmapBuilder()
        assert hb._estimate_cost(_event("")) == 0.0

    def test_case_insensitive_match(self):
        hb = HeatmapBuilder()
        # Models are lowercased before matching; "GPT-4O" must still bill as gpt-4o.
        cost = hb._estimate_cost(_event("GPT-4O", 1000, 1000))
        expected = (1000 * 0.005 + 1000 * 0.015) / 1000
        assert cost == pytest.approx(expected)


class TestHeatmapBucketCostIntegration:
    """End-to-end: the bucket cost reflects the correct per-model rates."""

    def test_bucket_cost_for_gpt4o_session(self):
        hb = HeatmapBuilder(granularity="day")
        for _ in range(5):
            hb.add_event(_event("gpt-4o", 1000, 1000))
        bucket = next(iter(hb._buckets.values()))
        expected_per_event = (1000 * 0.005 + 1000 * 0.015) / 1000
        assert bucket.cost == pytest.approx(5 * expected_per_event)
