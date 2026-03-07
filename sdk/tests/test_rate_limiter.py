"""Tests for agentlens.rate_limiter module."""

import time
import threading
import pytest

from agentlens.rate_limiter import (
    RateLimit,
    RateLimitAction,
    RateLimitPolicy,
    RateLimiter,
    CheckResult,
    RateLimitReport,
    openai_tier1_policy,
    anthropic_tier1_policy,
    conservative_policy,
)


class TestRateLimit:
    def test_basic_creation(self):
        rl = RateLimit("tokens", 1000, 60)
        assert rl.resource == "tokens"
        assert rl.limit == 1000
        assert rl.window_seconds == 60
        assert rl.action == RateLimitAction.BLOCK

    def test_invalid_limit(self):
        with pytest.raises(ValueError):
            RateLimit("tokens", 0, 60)

    def test_invalid_window(self):
        with pytest.raises(ValueError):
            RateLimit("tokens", 100, 0)

    def test_warn_action(self):
        rl = RateLimit("tokens", 100, 60, action=RateLimitAction.WARN)
        assert rl.action == RateLimitAction.WARN


class TestRateLimitPolicy:
    def test_fluent_add(self):
        policy = RateLimitPolicy()
        result = policy.add("tokens", 1000, 60).add("requests", 10, 60)
        assert result is policy
        assert len(policy.limits) == 2

    def test_warn_threshold(self):
        policy = RateLimitPolicy(warn_at_pct=90.0)
        assert policy.warn_at_pct == 90.0


class TestRateLimiter:
    def _make_limiter(self, limit=100, window=60):
        policy = RateLimitPolicy(limits=[
            RateLimit("tokens", limit, window),
        ])
        return RateLimiter(policy)

    def test_check_empty(self):
        limiter = self._make_limiter()
        result = limiter.check("tokens", estimated=10)
        assert result.allowed is True
        assert result.utilization_pct == 0.0
        assert result.retry_after_ms == 0

    def test_record_and_check(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 50)
        result = limiter.check("tokens", estimated=10)
        assert result.allowed is True
        assert result.current_usage == 50

    def test_block_when_exceeded(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 95)
        result = limiter.check("tokens", estimated=10)
        assert result.allowed is False
        assert len(result.violated_limits) > 0

    def test_warn_action_allows(self):
        policy = RateLimitPolicy(limits=[
            RateLimit("tokens", 100, 60, action=RateLimitAction.WARN),
        ])
        limiter = RateLimiter(policy)
        limiter.record("tokens", 95)
        result = limiter.check("tokens", estimated=10)
        # WARN action should still allow
        assert result.allowed is True

    def test_warning_callback(self):
        warnings = []
        policy = RateLimitPolicy(
            limits=[RateLimit("tokens", 100, 60)],
            warn_at_pct=50.0,
        )
        limiter = RateLimiter(policy, on_warning=lambda r, m: warnings.append(m))
        limiter.record("tokens", 60)
        limiter.check("tokens", estimated=1)
        assert len(warnings) == 1
        assert "60%" in warnings[0]

    def test_multiple_resources(self):
        policy = RateLimitPolicy(limits=[
            RateLimit("tokens", 1000, 60),
            RateLimit("requests", 10, 60),
        ])
        limiter = RateLimiter(policy)
        limiter.record("tokens", 500)
        limiter.record("requests", 5)

        tok_result = limiter.check("tokens", estimated=100)
        assert tok_result.allowed is True

        req_result = limiter.check("requests", estimated=1)
        assert req_result.allowed is True

    def test_multiple_windows_same_resource(self):
        policy = RateLimitPolicy(limits=[
            RateLimit("tokens", 100, 1, label="per-sec"),
            RateLimit("tokens", 1000, 60, label="per-min"),
        ])
        limiter = RateLimiter(policy)
        limiter.record("tokens", 90)
        result = limiter.check("tokens", estimated=20)
        # Per-second limit exceeded
        assert result.allowed is False
        assert "per-sec" in result.violated_limits

    def test_report(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 30)
        report = limiter.report()
        assert isinstance(report, RateLimitReport)
        assert len(report.windows) == 1
        assert report.windows[0].current_usage == 30
        assert report.windows[0].remaining == 70
        assert report.total_recorded["tokens"] == 30

    def test_report_healthy(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 10)
        assert limiter.report().healthy is True

    def test_report_unhealthy(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 95)
        assert limiter.report().healthy is False

    def test_report_to_dict(self):
        limiter = self._make_limiter()
        d = limiter.report().to_dict()
        assert "healthy" in d
        assert "windows" in d
        assert isinstance(d["windows"], list)

    def test_reset(self):
        limiter = self._make_limiter(limit=100)
        limiter.record("tokens", 90)
        limiter.reset()
        result = limiter.check("tokens", estimated=50)
        assert result.allowed is True
        assert result.current_usage == 0

    def test_unknown_resource_check(self):
        limiter = self._make_limiter()
        result = limiter.check("unknown", estimated=1)
        # No rules for this resource → allowed
        assert result.allowed is True

    def test_thread_safety(self):
        limiter = self._make_limiter(limit=10000)
        errors = []

        def record_many():
            try:
                for _ in range(100):
                    limiter.record("tokens", 1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        report = limiter.report()
        assert report.windows[0].current_usage == 1000


class TestPresetPolicies:
    def test_openai_tier1(self):
        policy = openai_tier1_policy()
        assert len(policy.limits) == 3
        resources = {rl.resource for rl in policy.limits}
        assert "tokens" in resources
        assert "requests" in resources

    def test_anthropic_tier1(self):
        policy = anthropic_tier1_policy()
        assert len(policy.limits) == 3

    def test_conservative(self):
        policy = conservative_policy()
        assert len(policy.limits) == 2
        assert policy.limits[0].limit == 10  # requests
