"""Tests for agentlens.sampling - trace sampling and rate limiting."""

from __future__ import annotations

import pytest

from agentlens.sampling import (
    AlwaysSampler,
    CompositeSampler,
    NeverSampler,
    PrioritySampler,
    ProbabilisticSampler,
    RateLimitSampler,
    SamplerStats,
    SamplingDecision,
    SamplingReason,
    TailSampler,
    TraceContext,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def normal_ctx():
    return TraceContext(trace_id="abc123", session_id="s1", duration_ms=500)


@pytest.fixture
def error_ctx():
    return TraceContext(trace_id="err1", has_error=True, error_type="ValueError")


@pytest.fixture
def slow_ctx():
    return TraceContext(trace_id="slow1", duration_ms=10000)


@pytest.fixture
def high_priority_ctx():
    return TraceContext(trace_id="hp1", priority=10)


# ── SamplingDecision ────────────────────────────────────────────────

class TestSamplingDecision:
    def test_bool_true(self):
        d = SamplingDecision(True, SamplingReason.PROBABILISTIC)
        assert bool(d) is True

    def test_bool_false(self):
        d = SamplingDecision(False, SamplingReason.PROBABILISTIC)
        assert bool(d) is False

    def test_defaults(self):
        d = SamplingDecision(True, SamplingReason.FORCED_KEEP)
        assert d.strategy == ""
        assert d.rate == 1.0
        assert d.metadata == {}


# ── SamplerStats ────────────────────────────────────────────────────

class TestSamplerStats:
    def test_effective_rate_zero(self):
        s = SamplerStats()
        assert s.effective_rate == 0.0

    def test_effective_rate(self):
        s = SamplerStats(total_decisions=100, sampled_count=25)
        assert s.effective_rate == 0.25

    def test_to_dict(self):
        s = SamplerStats(total_decisions=10, sampled_count=3, dropped_count=7)
        d = s.to_dict()
        assert d["total_decisions"] == 10
        assert d["sampled"] == 3
        assert d["effective_rate"] == 0.3


# ── TraceContext ────────────────────────────────────────────────────

class TestTraceContext:
    def test_defaults(self):
        ctx = TraceContext()
        assert ctx.trace_id == ""
        assert ctx.has_error is False
        assert ctx.priority == 0
        assert ctx.tags == {}

    def test_custom(self):
        ctx = TraceContext(trace_id="t1", has_error=True, priority=5)
        assert ctx.trace_id == "t1"
        assert ctx.has_error is True


# ── ProbabilisticSampler ────────────────────────────────────────────

class TestProbabilisticSampler:
    def test_rate_1_always_samples(self, normal_ctx):
        s = ProbabilisticSampler(rate=1.0)
        assert s.should_sample(normal_ctx).sampled is True

    def test_rate_0_never_samples(self, normal_ctx):
        s = ProbabilisticSampler(rate=0.0)
        assert s.should_sample(normal_ctx).sampled is False

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError, match="rate must be"):
            ProbabilisticSampler(rate=1.5)
        with pytest.raises(ValueError, match="rate must be"):
            ProbabilisticSampler(rate=-0.1)

    def test_deterministic_consistency(self):
        s = ProbabilisticSampler(rate=0.5, deterministic=True)
        ctx = TraceContext(trace_id="consistent-id")
        results = [s.should_sample(ctx).sampled for _ in range(20)]
        assert len(set(results)) == 1  # all same

    def test_name(self):
        assert ProbabilisticSampler().name == "probabilistic"

    def test_rate_property(self):
        s = ProbabilisticSampler(rate=0.42)
        assert s.rate == 0.42

    def test_stats_tracking(self, normal_ctx):
        s = ProbabilisticSampler(rate=1.0)
        s.should_sample(normal_ctx)
        s.should_sample(normal_ctx)
        assert s.stats.total_decisions == 2
        assert s.stats.sampled_count == 2

    def test_reason_is_probabilistic(self, normal_ctx):
        s = ProbabilisticSampler(rate=1.0)
        d = s.should_sample(normal_ctx)
        assert d.reason == SamplingReason.PROBABILISTIC

    def test_non_deterministic_uses_rng(self):
        s = ProbabilisticSampler(rate=0.5, deterministic=False, seed=42)
        ctx = TraceContext()  # no trace_id
        results = [s.should_sample(ctx).sampled for _ in range(100)]
        assert True in results and False in results  # both should appear

    def test_approximate_rate(self):
        s = ProbabilisticSampler(rate=0.3, deterministic=False, seed=123)
        n = 1000
        sampled = sum(
            1 for i in range(n)
            if s.should_sample(TraceContext(trace_id=str(i))).sampled
        )
        assert 200 < sampled < 400  # roughly 30%

    def test_reset_stats(self, normal_ctx):
        s = ProbabilisticSampler(rate=1.0)
        s.should_sample(normal_ctx)
        s.reset_stats()
        assert s.stats.total_decisions == 0


# ── RateLimitSampler ────────────────────────────────────────────────

class TestRateLimitSampler:
    def test_allows_under_limit(self, normal_ctx):
        s = RateLimitSampler(max_traces=10, window_seconds=60)
        d = s.should_sample(normal_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.RATE_ALLOWED

    def test_blocks_over_limit(self, normal_ctx):
        s = RateLimitSampler(max_traces=3, window_seconds=60)
        for _ in range(3):
            s.should_sample(normal_ctx)
        d = s.should_sample(normal_ctx)
        assert d.sampled is False
        assert d.reason == SamplingReason.RATE_LIMITED

    def test_name(self):
        assert RateLimitSampler().name == "rate_limit"

    def test_properties(self):
        s = RateLimitSampler(max_traces=50, window_seconds=30)
        assert s.max_traces == 50
        assert s.window_seconds == 30.0

    def test_current_count(self, normal_ctx):
        s = RateLimitSampler(max_traces=100, window_seconds=60)
        assert s.current_count() == 0
        s.should_sample(normal_ctx)
        assert s.current_count() == 1

    def test_invalid_max_traces(self):
        with pytest.raises(ValueError):
            RateLimitSampler(max_traces=-1)

    def test_invalid_window(self):
        with pytest.raises(ValueError):
            RateLimitSampler(window_seconds=0)

    def test_metadata_includes_counts(self, normal_ctx):
        s = RateLimitSampler(max_traces=5, window_seconds=60)
        d = s.should_sample(normal_ctx)
        assert "window_count" in d.metadata
        assert "max_traces" in d.metadata


# ── PrioritySampler ─────────────────────────────────────────────────

class TestPrioritySampler:
    def test_keeps_errors(self, error_ctx):
        s = PrioritySampler(error_always_keep=True, fallback_rate=0.0, seed=0)
        d = s.should_sample(error_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.PRIORITY_ERROR

    def test_keeps_slow(self, slow_ctx):
        s = PrioritySampler(slow_threshold_ms=5000, fallback_rate=0.0, seed=0)
        d = s.should_sample(slow_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.PRIORITY_SLOW

    def test_keeps_high_priority(self, high_priority_ctx):
        s = PrioritySampler(priority_threshold=5, fallback_rate=0.0, seed=0)
        d = s.should_sample(high_priority_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.PRIORITY_IMPORTANT

    def test_keeps_important_tags(self):
        s = PrioritySampler(
            important_tags={"env": "production"},
            fallback_rate=0.0,
            seed=0,
        )
        ctx = TraceContext(tags={"env": "production"})
        d = s.should_sample(ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.PRIORITY_IMPORTANT

    def test_fallback_rate_zero_drops(self, normal_ctx):
        s = PrioritySampler(fallback_rate=0.0, seed=0)
        d = s.should_sample(normal_ctx)
        assert d.sampled is False
        assert d.reason == SamplingReason.PRIORITY_FALLBACK

    def test_fallback_rate_one_keeps(self, normal_ctx):
        s = PrioritySampler(fallback_rate=1.0, seed=0)
        d = s.should_sample(normal_ctx)
        assert d.sampled is True

    def test_name(self):
        assert PrioritySampler().name == "priority"

    def test_error_disabled(self, error_ctx):
        s = PrioritySampler(error_always_keep=False, fallback_rate=0.0, seed=0)
        d = s.should_sample(error_ctx)
        assert d.sampled is False

    def test_slow_disabled(self, slow_ctx):
        s = PrioritySampler(slow_threshold_ms=None, fallback_rate=0.0, seed=0)
        d = s.should_sample(slow_ctx)
        assert d.sampled is False

    def test_invalid_fallback(self):
        with pytest.raises(ValueError):
            PrioritySampler(fallback_rate=2.0)


# ── TailSampler ─────────────────────────────────────────────────────

class TestTailSampler:
    def test_keeps_errors(self, error_ctx):
        s = TailSampler(fallback_rate=0.0, seed=0)
        d = s.should_sample(error_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.TAIL_ERROR

    def test_keeps_slow(self, slow_ctx):
        s = TailSampler(slow_threshold_ms=5000, fallback_rate=0.0, seed=0)
        d = s.should_sample(slow_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.TAIL_SLOW

    def test_keeps_complex_traces(self):
        s = TailSampler(min_spans=10, fallback_rate=0.0, seed=0)
        ctx = TraceContext(span_count=15)
        d = s.should_sample(ctx)
        assert d.sampled is True

    def test_drops_normal(self, normal_ctx):
        s = TailSampler(fallback_rate=0.0, seed=0)
        d = s.should_sample(normal_ctx)
        assert d.sampled is False
        assert d.reason == SamplingReason.TAIL_NORMAL

    def test_name(self):
        assert TailSampler().name == "tail"

    def test_invalid_fallback(self):
        with pytest.raises(ValueError):
            TailSampler(fallback_rate=-0.1)


# ── CompositeSampler ───────────────────────────────────────────────

class TestCompositeSampler:
    def test_all_mode_requires_all(self, normal_ctx):
        s = CompositeSampler(
            strategies=[AlwaysSampler(), NeverSampler()],
            mode="all",
        )
        d = s.should_sample(normal_ctx)
        assert d.sampled is False

    def test_any_mode_accepts_any(self, normal_ctx):
        s = CompositeSampler(
            strategies=[AlwaysSampler(), NeverSampler()],
            mode="any",
        )
        d = s.should_sample(normal_ctx)
        assert d.sampled is True

    def test_all_accept(self, normal_ctx):
        s = CompositeSampler(
            strategies=[AlwaysSampler(), AlwaysSampler()],
            mode="all",
        )
        d = s.should_sample(normal_ctx)
        assert d.sampled is True

    def test_none_accept(self, normal_ctx):
        s = CompositeSampler(
            strategies=[NeverSampler(), NeverSampler()],
            mode="any",
        )
        d = s.should_sample(normal_ctx)
        assert d.sampled is False

    def test_reason_is_composite(self, normal_ctx):
        s = CompositeSampler([AlwaysSampler()], mode="all")
        d = s.should_sample(normal_ctx)
        assert d.reason == SamplingReason.COMPOSITE

    def test_metadata_has_sub_decisions(self, normal_ctx):
        s = CompositeSampler([AlwaysSampler(), NeverSampler()], mode="all")
        d = s.should_sample(normal_ctx)
        assert "sub_decisions" in d.metadata
        assert len(d.metadata["sub_decisions"]) == 2

    def test_name_includes_strategies(self):
        s = CompositeSampler([AlwaysSampler(), NeverSampler()], mode="all")
        assert "always" in s.name
        assert "never" in s.name
        assert "all" in s.name

    def test_empty_strategies_raises(self):
        with pytest.raises(ValueError, match="strategies must not be empty"):
            CompositeSampler(strategies=[], mode="all")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be"):
            CompositeSampler([AlwaysSampler()], mode="xor")

    def test_priority_and_rate_limit(self, error_ctx):
        """Production pattern: priority keeps errors, rate limit caps volume."""
        s = CompositeSampler(
            strategies=[
                PrioritySampler(error_always_keep=True, fallback_rate=1.0),
                RateLimitSampler(max_traces=1000, window_seconds=60),
            ],
            mode="all",
        )
        d = s.should_sample(error_ctx)
        assert d.sampled is True


# ── AlwaysSampler / NeverSampler ────────────────────────────────────

class TestAlwaysNeverSamplers:
    def test_always_keeps(self, normal_ctx):
        d = AlwaysSampler().should_sample(normal_ctx)
        assert d.sampled is True
        assert d.reason == SamplingReason.FORCED_KEEP

    def test_never_drops(self, normal_ctx):
        d = NeverSampler().should_sample(normal_ctx)
        assert d.sampled is False
        assert d.reason == SamplingReason.FORCED_DROP

    def test_always_name(self):
        assert AlwaysSampler().name == "always"

    def test_never_name(self):
        assert NeverSampler().name == "never"

    def test_always_stats_forced_keep(self, normal_ctx):
        s = AlwaysSampler()
        s.should_sample(normal_ctx)
        assert s.stats.forced_keep == 1

    def test_never_stats_forced_drop(self, normal_ctx):
        s = NeverSampler()
        s.should_sample(normal_ctx)
        assert s.stats.forced_drop == 1


# ── Integration / realistic scenarios ──────────────────────────────

class TestIntegrationScenarios:
    def test_production_setup(self):
        """Simulate a production sampling pipeline."""
        sampler = CompositeSampler(
            strategies=[
                PrioritySampler(
                    error_always_keep=True,
                    slow_threshold_ms=3000,
                    fallback_rate=0.2,
                    seed=42,
                ),
                RateLimitSampler(max_traces=50, window_seconds=60),
            ],
            mode="all",
        )

        error_ctx = TraceContext(trace_id="e1", has_error=True)
        d = sampler.should_sample(error_ctx)
        assert d.sampled is True

        slow_ctx = TraceContext(trace_id="s1", duration_ms=5000)
        d = sampler.should_sample(slow_ctx)
        assert d.sampled is True

    def test_rate_limit_enforced_on_priority(self):
        """Rate limit should cap even priority traces when mode=all."""
        sampler = CompositeSampler(
            strategies=[
                AlwaysSampler(),  # always accept
                RateLimitSampler(max_traces=2, window_seconds=60),
            ],
            mode="all",
        )
        ctx = TraceContext(trace_id="t1")
        assert sampler.should_sample(ctx).sampled is True
        assert sampler.should_sample(ctx).sampled is True
        assert sampler.should_sample(ctx).sampled is False  # rate limited

    def test_or_mode_rescues_from_rate_limit(self):
        """In 'any' mode, priority can override rate limit."""
        sampler = CompositeSampler(
            strategies=[
                PrioritySampler(error_always_keep=True, fallback_rate=0.0, seed=0),
                RateLimitSampler(max_traces=0, window_seconds=60),  # always limited
            ],
            mode="any",
        )
        error = TraceContext(has_error=True)
        d = sampler.should_sample(error)
        assert d.sampled is True  # priority rescued it

    def test_sampling_decision_in_metadata(self):
        s = ProbabilisticSampler(rate=0.5, deterministic=True)
        ctx = TraceContext(trace_id="test-trace-1")
        d = s.should_sample(ctx)
        assert "roll" in d.metadata
