"""Tests for agentlens.budget -- Token Budget Tracker."""

import pytest
from agentlens.budget import (
    TokenBudget,
    BudgetTracker,
    BudgetReport,
    BudgetStatus,
    BudgetExceededError,
    BudgetEntry,
    estimate_cost,
    MODEL_PRICING,
)


# -- estimate_cost ------------------------------------------------


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost(1_000_000, 1_000_000, "gpt-4o")
        assert cost == pytest.approx(12.50)

    def test_small_usage(self):
        cost = estimate_cost(1000, 500, "gpt-4o")
        assert cost == pytest.approx(0.0075)

    def test_unknown_model_returns_zero(self):
        assert estimate_cost(1000, 1000, "nonexistent-model") == 0.0

    def test_none_model_returns_zero(self):
        assert estimate_cost(1000, 1000, None) == 0.0

    def test_zero_tokens(self):
        assert estimate_cost(0, 0, "gpt-4o") == 0.0

    def test_all_known_models_have_pricing(self):
        for model in MODEL_PRICING:
            cost = estimate_cost(1000, 1000, model)
            assert cost >= 0


# -- TokenBudget properties ----------------------------------------


class TestTokenBudget:
    def test_default_status_active(self):
        b = TokenBudget(max_tokens=1000)
        assert b.status == BudgetStatus.ACTIVE

    def test_token_utilization_none_when_no_limit(self):
        b = TokenBudget()
        assert b.token_utilization is None

    def test_token_utilization_computed(self):
        b = TokenBudget(max_tokens=1000, total_tokens=300)
        assert b.token_utilization == pytest.approx(0.3)

    def test_cost_utilization_none_when_no_limit(self):
        b = TokenBudget()
        assert b.cost_utilization is None

    def test_cost_utilization_computed(self):
        b = TokenBudget(max_cost_usd=10.0, total_cost_usd=2.5)
        assert b.cost_utilization == pytest.approx(0.25)

    def test_utilization_takes_max(self):
        b = TokenBudget(
            max_tokens=1000, total_tokens=900,
            max_cost_usd=10.0, total_cost_usd=5.0,
        )
        assert b.utilization == pytest.approx(0.9)

    def test_utilization_zero_with_no_limits(self):
        b = TokenBudget()
        assert b.utilization == 0.0

    def test_remaining_tokens(self):
        b = TokenBudget(max_tokens=5000, total_tokens=3200)
        assert b.remaining_tokens == 1800

    def test_remaining_tokens_none_when_no_limit(self):
        b = TokenBudget()
        assert b.remaining_tokens is None

    def test_remaining_tokens_floors_at_zero(self):
        b = TokenBudget(max_tokens=100, total_tokens=200)
        assert b.remaining_tokens == 0

    def test_remaining_cost(self):
        b = TokenBudget(max_cost_usd=5.0, total_cost_usd=1.25)
        assert b.remaining_cost == pytest.approx(3.75)

    def test_remaining_cost_none_when_no_limit(self):
        b = TokenBudget()
        assert b.remaining_cost is None

    def test_status_warning(self):
        b = TokenBudget(max_tokens=1000, total_tokens=850, warn_at=0.8)
        assert b.status == BudgetStatus.WARNING

    def test_status_exceeded_no_hard_limit(self):
        b = TokenBudget(max_tokens=1000, total_tokens=1100, hard_limit=False)
        assert b.status == BudgetStatus.EXCEEDED

    def test_status_exhausted_with_hard_limit(self):
        b = TokenBudget(max_tokens=1000, total_tokens=1100, hard_limit=True)
        assert b.status == BudgetStatus.EXHAUSTED

    def test_status_at_exact_limit(self):
        b = TokenBudget(max_tokens=1000, total_tokens=1000)
        assert b.status == BudgetStatus.EXCEEDED

    def test_cost_drives_status(self):
        b = TokenBudget(max_cost_usd=1.0, total_cost_usd=0.95, warn_at=0.9)
        assert b.status == BudgetStatus.WARNING


# -- BudgetTracker -------------------------------------------------


class TestBudgetTracker:
    def test_create_budget(self):
        tracker = BudgetTracker()
        budget = tracker.create_budget("sess-1", max_tokens=5000)
        assert budget.session_id == "sess-1"
        assert budget.max_tokens == 5000
        assert budget.total_tokens == 0

    def test_create_budget_validates_warn_at(self):
        tracker = BudgetTracker()
        with pytest.raises(ValueError, match="warn_at"):
            tracker.create_budget("s", max_tokens=100, warn_at=0)
        with pytest.raises(ValueError, match="warn_at"):
            tracker.create_budget("s", max_tokens=100, warn_at=1.5)

    def test_create_budget_validates_max_tokens(self):
        tracker = BudgetTracker()
        with pytest.raises(ValueError, match="max_tokens"):
            tracker.create_budget("s", max_tokens=-1)

    def test_create_budget_validates_max_cost(self):
        tracker = BudgetTracker()
        with pytest.raises(ValueError, match="max_cost_usd"):
            tracker.create_budget("s", max_cost_usd=0)

    def test_record_updates_totals(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=10000)
        tracker.record(b.budget_id, tokens_in=500, tokens_out=200)
        assert b.total_tokens == 700
        assert b.total_tokens_in == 500
        assert b.total_tokens_out == 200
        assert len(b.entries) == 1

    def test_record_accumulates(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=10000)
        tracker.record(b.budget_id, tokens_in=100, tokens_out=50)
        tracker.record(b.budget_id, tokens_in=200, tokens_out=100)
        assert b.total_tokens == 450
        assert len(b.entries) == 2

    def test_record_returns_status(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=1000, warn_at=0.5)
        status = tracker.record(b.budget_id, tokens_in=200, tokens_out=100)
        assert status == BudgetStatus.ACTIVE
        status = tracker.record(b.budget_id, tokens_in=300, tokens_out=200)
        assert status == BudgetStatus.WARNING

    def test_record_unknown_budget_raises(self):
        tracker = BudgetTracker()
        with pytest.raises(KeyError):
            tracker.record("nonexistent", tokens_in=100)

    def test_hard_limit_raises(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=500, hard_limit=True)
        tracker.record(b.budget_id, tokens_in=400)
        with pytest.raises(BudgetExceededError) as exc_info:
            tracker.record(b.budget_id, tokens_in=200)
        assert exc_info.value.attempted_tokens == 200
        assert exc_info.value.budget is b
        assert b.total_tokens == 400

    def test_hard_limit_cost_check(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_cost_usd=0.01, hard_limit=True, model="gpt-4o")
        tracker.record(b.budget_id, tokens_in=3000)
        with pytest.raises(BudgetExceededError):
            tracker.record(b.budget_id, tokens_in=5000)

    def test_soft_limit_allows_exceed(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=500, hard_limit=False)
        tracker.record(b.budget_id, tokens_in=400)
        status = tracker.record(b.budget_id, tokens_in=300)
        assert status == BudgetStatus.EXCEEDED
        assert b.total_tokens == 700

    def test_record_for_session(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("sess-abc", max_tokens=5000)
        status = tracker.record_for_session("sess-abc", tokens_in=100)
        assert status == BudgetStatus.ACTIVE
        assert b.total_tokens == 100

    def test_record_for_session_unknown(self):
        tracker = BudgetTracker()
        result = tracker.record_for_session("unknown-sess", tokens_in=100)
        assert result is None

    def test_record_with_model_cost(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=100000, model="gpt-4o")
        tracker.record(b.budget_id, tokens_in=1000, tokens_out=500)
        assert b.total_cost_usd == pytest.approx(0.0075)

    def test_record_model_override(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=100000, model="gpt-4o-mini")
        tracker.record(b.budget_id, tokens_in=1000, tokens_out=500, model="gpt-4o")
        assert b.total_cost_usd == pytest.approx(0.0075)

    def test_record_event_id(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=10000)
        tracker.record(b.budget_id, tokens_in=100, event_id="evt-123")
        assert b.entries[0].event_id == "evt-123"


# -- Callbacks -----------------------------------------------------


class TestCallbacks:
    def test_callback_on_warning(self):
        tracker = BudgetTracker()
        events = []
        tracker.on_threshold(lambda b, s: events.append((b.budget_id, s)))

        b = tracker.create_budget("s1", max_tokens=1000, warn_at=0.8)
        tracker.record(b.budget_id, tokens_in=500)
        assert len(events) == 0
        tracker.record(b.budget_id, tokens_in=400)
        assert len(events) == 1
        assert events[0] == (b.budget_id, BudgetStatus.WARNING)

    def test_callback_on_exceeded(self):
        tracker = BudgetTracker()
        events = []
        tracker.on_threshold(lambda b, s: events.append(s))

        b = tracker.create_budget("s1", max_tokens=100, warn_at=0.9)
        tracker.record(b.budget_id, tokens_in=95)
        tracker.record(b.budget_id, tokens_in=10)
        assert BudgetStatus.WARNING in events
        assert BudgetStatus.EXCEEDED in events

    def test_no_callback_when_status_unchanged(self):
        tracker = BudgetTracker()
        called = []
        tracker.on_threshold(lambda b, s: called.append(s))

        b = tracker.create_budget("s1", max_tokens=1000, warn_at=0.5)
        tracker.record(b.budget_id, tokens_in=100)
        tracker.record(b.budget_id, tokens_in=100)
        tracker.record(b.budget_id, tokens_in=100)
        assert len(called) == 0

    def test_multiple_callbacks(self):
        tracker = BudgetTracker()
        log1, log2 = [], []
        tracker.on_threshold(lambda b, s: log1.append(s))
        tracker.on_threshold(lambda b, s: log2.append(s))

        b = tracker.create_budget("s1", max_tokens=100, warn_at=0.5)
        tracker.record(b.budget_id, tokens_in=60)
        assert len(log1) == 1
        assert len(log2) == 1


# -- Reports -------------------------------------------------------


class TestReports:
    def test_report_snapshot(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=10000, agent_name="test-agent", model="gpt-4o")
        tracker.record(b.budget_id, tokens_in=1000, tokens_out=500)

        report = tracker.report(b.budget_id)
        assert isinstance(report, BudgetReport)
        assert report.total_tokens == 1500
        assert report.total_tokens_in == 1000
        assert report.total_tokens_out == 500
        assert report.max_tokens == 10000
        assert report.token_utilization == pytest.approx(0.15)
        assert report.status == BudgetStatus.ACTIVE
        assert report.agent_name == "test-agent"
        assert report.entry_count == 1
        assert report.model == "gpt-4o"

    def test_report_unknown_raises(self):
        tracker = BudgetTracker()
        with pytest.raises(KeyError):
            tracker.report("nonexistent")

    def test_report_for_session(self):
        tracker = BudgetTracker()
        tracker.create_budget("sess-x", max_tokens=5000)
        report = tracker.report_for_session("sess-x")
        assert report is not None
        assert report.session_id == "sess-x"

    def test_report_for_unknown_session(self):
        tracker = BudgetTracker()
        assert tracker.report_for_session("unknown") is None

    def test_all_reports(self):
        tracker = BudgetTracker()
        tracker.create_budget("s1", max_tokens=1000)
        tracker.create_budget("s2", max_tokens=2000)
        reports = tracker.all_reports()
        assert len(reports) == 2

    def test_report_to_dict(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=1000, model="gpt-4o")
        tracker.record(b.budget_id, tokens_in=100)
        report = tracker.report(b.budget_id)
        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["budget_id"] == b.budget_id
        assert d["status"] == "active"
        assert d["total_tokens"] == 100
        assert d["max_tokens"] == 1000

    def test_report_summary(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=10000)
        tracker.record(b.budget_id, tokens_in=5000)
        report = tracker.report(b.budget_id)
        summary = report.summary
        assert "ACTIVE" in summary
        assert "5,000" in summary
        assert "10,000" in summary
        assert "50.0%" in summary

    def test_report_summary_no_limit(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1")
        tracker.record(b.budget_id, tokens_in=100)
        report = tracker.report(b.budget_id)
        assert "no limit" in report.summary

    def test_report_summary_with_cost(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_cost_usd=10.0, model="gpt-4o")
        tracker.record(b.budget_id, tokens_in=1000)
        report = tracker.report(b.budget_id)
        assert "$" in report.summary


# -- Budget management ---------------------------------------------


class TestBudgetManagement:
    def test_get_budget(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=1000)
        found = tracker.get_budget(b.budget_id)
        assert found is b

    def test_get_budget_unknown(self):
        tracker = BudgetTracker()
        assert tracker.get_budget("nope") is None

    def test_remove_budget(self):
        tracker = BudgetTracker()
        b = tracker.create_budget("s1", max_tokens=1000)
        assert tracker.remove_budget(b.budget_id) is True
        assert tracker.get_budget(b.budget_id) is None
        assert tracker.report_for_session("s1") is None

    def test_remove_nonexistent(self):
        tracker = BudgetTracker()
        assert tracker.remove_budget("nope") is False
