"""Tests for Usage Quota Manager."""

import pytest
from datetime import datetime, timezone, timedelta
from agentlens.quota import (
    QuotaManager, QuotaPolicy, QuotaCheck, QuotaReport,
    SharedPool, FleetReport, QuotaScope, QuotaWindow, QuotaAction,
)


@pytest.fixture
def now():
    return datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def qm(now):
    return QuotaManager(now_fn=lambda: now)


def test_create_quota(qm):
    p = qm.create_quota("agent-1", max_tokens=10000, window="daily")
    assert p.entity_id == "agent-1"
    assert p.max_tokens == 10000
    assert p.scope == QuotaScope.AGENT
    assert p.window == QuotaWindow.DAILY


def test_get_quota(qm):
    qm.create_quota("a1", max_tokens=5000)
    assert qm.get_quota("a1").max_tokens == 5000
    assert qm.get_quota("nonexistent") is None


def test_update_quota(qm):
    qm.create_quota("a1", max_tokens=5000)
    qm.update_quota("a1", max_tokens=10000, warn_at=0.9)
    p = qm.get_quota("a1")
    assert p.max_tokens == 10000
    assert p.warn_at == 0.9


def test_update_nonexistent_raises(qm):
    with pytest.raises(KeyError):
        qm.update_quota("nope", max_tokens=1)


def test_delete_quota(qm):
    qm.create_quota("a1", max_tokens=5000)
    assert qm.delete_quota("a1") is True
    assert qm.delete_quota("a1") is False


def test_list_quotas(qm):
    qm.create_quota("a1", scope="agent", max_tokens=1000)
    qm.create_quota("m1", scope="model", max_tokens=2000)
    qm.create_quota("t1", scope="team", max_tokens=3000)
    assert len(qm.list_quotas()) == 3
    assert len(qm.list_quotas(scope="model")) == 1


def test_disable_enable(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.disable_quota("a1")
    assert not qm.get_quota("a1").enabled
    check = qm.record_usage("a1", tokens=999)
    assert check.allowed
    qm.enable_quota("a1")
    assert qm.get_quota("a1").enabled


def test_record_within_limit(qm):
    qm.create_quota("a1", max_tokens=10000)
    check = qm.record_usage("a1", tokens=500)
    assert check.allowed
    assert check.remaining_tokens == 9500
    assert check.utilization_tokens == pytest.approx(0.05)


def test_record_exceeds_limit(qm):
    qm.create_quota("a1", max_tokens=1000, window="daily")
    qm.record_usage("a1", tokens=800)
    check = qm.record_usage("a1", tokens=300)
    assert not check.allowed
    assert "exceeded" in check.reason.lower()


def test_no_quota_always_allows(qm):
    check = qm.record_usage("unknown-agent", tokens=999999)
    assert check.allowed


def test_warning_threshold(qm):
    qm.create_quota("a1", max_tokens=1000, warn_at=0.8)
    check = qm.record_usage("a1", tokens=850)
    assert check.allowed
    assert len(check.warnings) > 0


def test_cost_quota(qm):
    qm.create_quota("a1", max_cost_usd=10.0)
    qm.record_usage("a1", cost_usd=8.0)
    check = qm.record_usage("a1", cost_usd=3.0)
    assert not check.allowed
    assert "cost" in check.reason.lower()


def test_cost_warning(qm):
    qm.create_quota("a1", max_cost_usd=10.0, warn_at=0.7)
    check = qm.record_usage("a1", cost_usd=7.5)
    assert check.allowed
    assert any("cost" in w.lower() for w in check.warnings)


def test_request_quota(qm):
    qm.create_quota("a1", max_requests=3)
    qm.record_usage("a1", tokens=1)
    qm.record_usage("a1", tokens=1)
    qm.record_usage("a1", tokens=1)
    check = qm.record_usage("a1", tokens=1)
    assert not check.allowed
    assert "request" in check.reason.lower()


def test_burst_allows_over_limit(qm):
    qm.create_quota("a1", max_tokens=1000, burst_multiplier=1.5)
    qm.record_usage("a1", tokens=900)
    check = qm.record_usage("a1", tokens=200)
    assert check.allowed
    assert check.burst_active


def test_burst_exceeded(qm):
    qm.create_quota("a1", max_tokens=1000, burst_multiplier=1.2)
    qm.record_usage("a1", tokens=1000)
    check = qm.record_usage("a1", tokens=300)
    assert not check.allowed


def test_throttle_mode(qm):
    qm.create_quota("a1", max_tokens=100, action_on_exceed="throttle")
    qm.record_usage("a1", tokens=90)
    check = qm.record_usage("a1", tokens=20)
    assert check.allowed
    assert "throttled" in check.reason


def test_warn_mode(qm):
    qm.create_quota("a1", max_tokens=100, action_on_exceed="warn")
    qm.record_usage("a1", tokens=90)
    check = qm.record_usage("a1", tokens=20)
    assert check.allowed
    assert any("QUOTA EXCEEDED" in w for w in check.warnings)


def test_create_pool(qm):
    pool = qm.create_pool("team-pool", members=["a1", "a2"], pool_tokens=5000)
    assert pool.name == "team-pool"
    assert len(pool.members) == 2


def test_pool_borrow(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.create_pool("shared", members=["a1"], pool_tokens=500)
    qm.record_usage("a1", tokens=90)
    check = qm.record_usage("a1", tokens=20)
    assert check.allowed
    assert check.pool_contribution > 0


def test_pool_exhausted(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.create_pool("shared", members=["a1"], pool_tokens=10)
    qm.record_usage("a1", tokens=90)
    check = qm.record_usage("a1", tokens=20)
    assert not check.allowed


def test_add_remove_pool_member(qm):
    qm.create_pool("p1", members=["a1"])
    qm.add_pool_member("p1", "a2")
    assert "a2" in qm.get_pool("p1").members
    qm.remove_pool_member("p1", "a1")
    assert "a1" not in qm.get_pool("p1").members


def test_delete_pool(qm):
    qm.create_pool("p1", members=["a1"])
    assert qm.delete_pool("p1") is True
    assert qm.delete_pool("p1") is False


def test_check_usage_dry_run(qm):
    qm.create_quota("a1", max_tokens=1000)
    qm.record_usage("a1", tokens=800)
    check = qm.check_usage("a1", tokens=300)
    assert not check.allowed
    report = qm.report("a1")
    assert report.tokens_used == 800


def test_check_usage_no_quota(qm):
    check = qm.check_usage("nobody", tokens=999)
    assert check.allowed


def test_hourly_window(now):
    qm = QuotaManager(now_fn=lambda: now)
    qm.create_quota("a1", max_tokens=100, window="hourly")
    check = qm.record_usage("a1", tokens=50)
    assert check.allowed
    assert check.remaining_tokens == 50


def test_weekly_window(now):
    qm = QuotaManager(now_fn=lambda: now)
    qm.create_quota("a1", max_tokens=1000, window="weekly")
    check = qm.record_usage("a1", tokens=500)
    assert check.utilization_tokens == pytest.approx(0.5)


def test_monthly_window(now):
    qm = QuotaManager(now_fn=lambda: now)
    qm.create_quota("a1", max_tokens=10000, window="monthly")
    check = qm.record_usage("a1", tokens=3000)
    assert check.utilization_tokens == pytest.approx(0.3)


def test_report_basic(qm):
    qm.create_quota("a1", max_tokens=1000, max_cost_usd=5.0)
    qm.record_usage("a1", tokens=300, cost_usd=1.5, model="gpt-4o")
    qm.record_usage("a1", tokens=200, cost_usd=0.5, model="gpt-3.5")
    report = qm.report("a1")
    assert report.tokens_used == 500
    assert report.cost_used == pytest.approx(2.0)
    assert report.requests_used == 2
    assert report.status == "ok"
    assert len(report.top_models) == 2


def test_report_exceeded(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.record_usage("a1", tokens=150)
    report = qm.report("a1")
    assert report.status == "exceeded"


def test_report_warning(qm):
    qm.create_quota("a1", max_tokens=100, warn_at=0.8)
    qm.record_usage("a1", tokens=85)
    report = qm.report("a1")
    assert report.status == "warning"
    assert len(report.warnings) > 0


def test_report_disabled(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.disable_quota("a1")
    report = qm.report("a1")
    assert report.status == "disabled"


def test_report_no_quota(qm):
    report = qm.report("nobody")
    assert report.status == "no_quota"


def test_report_render(qm):
    qm.create_quota("a1", max_tokens=1000, max_cost_usd=5.0)
    qm.record_usage("a1", tokens=500, cost_usd=2.0, model="gpt-4o")
    text = qm.report("a1").render()
    assert "a1" in text
    assert "Tokens" in text


def test_report_burst_info(qm):
    qm.create_quota("a1", max_tokens=100, burst_multiplier=2.0)
    qm.record_usage("a1", tokens=150)
    report = qm.report("a1")
    assert report.burst_active
    assert report.burst_tokens_remaining == 50


def test_fleet_report(qm):
    qm.create_quota("a1", max_tokens=1000)
    qm.create_quota("a2", max_tokens=2000)
    qm.record_usage("a1", tokens=500)
    qm.record_usage("a2", tokens=300)
    fleet = qm.fleet_report()
    assert fleet.total_entities == 2
    assert fleet.total_tokens == 800
    assert fleet.entities_ok == 2


def test_fleet_report_with_pools(qm):
    qm.create_quota("a1", max_tokens=100)
    qm.create_pool("shared", members=["a1"], pool_tokens=500)
    fleet = qm.fleet_report()
    assert len(fleet.pool_reports) == 1


def test_fleet_report_render(qm):
    qm.create_quota("a1", max_tokens=1000)
    qm.record_usage("a1", tokens=500)
    text = qm.fleet_report().render()
    assert "Fleet" in text


def test_on_check_callback(qm):
    events = []
    qm.on_check(lambda eid, check: events.append((eid, check.allowed)))
    qm.create_quota("a1", max_tokens=100)
    qm.record_usage("a1", tokens=50)
    assert len(events) == 1
    assert events[0] == ("a1", True)


def test_reset_usage(qm):
    qm.create_quota("a1", max_tokens=1000)
    qm.record_usage("a1", tokens=500)
    qm.record_usage("a1", tokens=300)
    removed = qm.reset_usage("a1")
    assert removed == 2
    assert qm.report("a1").tokens_used == 0


def test_export_import(qm):
    qm.create_quota("a1", max_tokens=1000, scope="team")
    qm.create_pool("p1", members=["a1", "a2"], pool_tokens=5000)
    state = qm.export_state()
    qm2 = QuotaManager()
    qm2.import_state(state)
    assert qm2.get_quota("a1").max_tokens == 1000
    assert qm2.get_pool("p1").pool_tokens == 5000


def test_combined_token_and_cost(qm):
    qm.create_quota("a1", max_tokens=1000, max_cost_usd=5.0)
    check = qm.record_usage("a1", tokens=500, cost_usd=6.0)
    assert not check.allowed
    assert "cost" in check.reason.lower()


def test_model_scope_quota(qm):
    qm.create_quota("gpt-4o", scope="model", max_tokens=50000)
    check = qm.record_usage("gpt-4o", tokens=30000, model="gpt-4o")
    assert check.allowed
    assert check.utilization_tokens == pytest.approx(0.6)


def test_organization_scope(qm):
    qm.create_quota("acme-corp", scope="organization", max_cost_usd=1000.0, window="monthly")
    check = qm.record_usage("acme-corp", cost_usd=200.0)
    assert check.allowed


def test_zero_limit(qm):
    qm.create_quota("a1", max_tokens=0)
    check = qm.record_usage("a1", tokens=1)
    assert not check.allowed


def test_metadata(qm):
    p = qm.create_quota("a1", max_tokens=100, metadata={"team": "ml"})
    assert p.metadata["team"] == "ml"


def test_session_tracking_in_report(qm):
    qm.create_quota("a1", max_tokens=10000)
    qm.record_usage("a1", tokens=100, session_id="s1")
    qm.record_usage("a1", tokens=200, session_id="s2")
    qm.record_usage("a1", tokens=300, session_id="s1")
    report = qm.report("a1")
    assert report.requests_used == 3
    assert len(report.top_sessions) == 2
