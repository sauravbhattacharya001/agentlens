"""Tests for agentlens.heatmap — Token Usage Heatmap."""

import json
from datetime import datetime, timezone, timedelta

import pytest

from agentlens.heatmap import (
    HeatmapBuilder,
    HeatmapBucket,
    _iso_key,
    _color_scale,
)
from agentlens.models import AgentEvent, Session


def _event(ts: datetime, tokens_in: int = 100, tokens_out: int = 50,
           model: str = "gpt-4o", session_id: str = "s1") -> AgentEvent:
    return AgentEvent(
        timestamp=ts, tokens_in=tokens_in, tokens_out=tokens_out,
        model=model, session_id=session_id, event_type="llm_call",
    )


# --- _iso_key ---

def test_iso_key_hour():
    dt = datetime(2026, 3, 15, 14, 37, 0, tzinfo=timezone.utc)
    assert _iso_key(dt, "hour") == "2026-03-15T14:00Z"


def test_iso_key_day():
    dt = datetime(2026, 3, 15, 14, 37, 0, tzinfo=timezone.utc)
    assert _iso_key(dt, "day") == "2026-03-15"


def test_iso_key_week():
    # 2026-03-15 is a Sunday; Monday was 2026-03-09
    dt = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    assert _iso_key(dt, "week") == "2026-03-09"


# --- _color_scale ---

def test_color_scale_zero():
    assert _color_scale(0, 100) == "#ebedf0"


def test_color_scale_max():
    assert _color_scale(100, 100) == "#216e39"


def test_color_scale_mid():
    c = _color_scale(50, 100)
    assert c in ("#40c463", "#30a14e")


def test_color_scale_negative_max():
    assert _color_scale(10, 0) == "#ebedf0"


# --- HeatmapBucket ---

def test_bucket_add_event():
    b = HeatmapBucket("2026-03-15")
    ev = _event(datetime(2026, 3, 15, tzinfo=timezone.utc))
    b.add_event(ev)
    assert b.tokens_in == 100
    assert b.tokens_out == 50
    assert b.tokens_total == 150
    assert b.event_count == 1
    assert "gpt-4o" in b.models
    assert "s1" in b.sessions


def test_bucket_to_dict():
    b = HeatmapBucket("2026-03-15")
    ev = _event(datetime(2026, 3, 15, tzinfo=timezone.utc))
    b.add_event(ev)
    d = b.to_dict()
    assert d["key"] == "2026-03-15"
    assert d["tokens_total"] == 150
    assert d["session_count"] == 1


def test_bucket_metric():
    b = HeatmapBucket("k")
    b.tokens_in = 200
    b.tokens_out = 100
    b.event_count = 5
    b.cost = 0.05
    assert b.metric("tokens_total") == 300
    assert b.metric("event_count") == 5
    assert b.metric("cost") == 0.05


# --- HeatmapBuilder basic ---

def test_builder_add_event():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    assert hb._events_added == 1
    assert len(hb._buckets) == 1


def test_builder_add_session():
    s = Session(session_id="sess1", agent_name="bot")
    for i in range(5):
        s.add_event(_event(datetime(2026, 3, 15, i, 0, tzinfo=timezone.utc)))
    hb = HeatmapBuilder()
    hb.add_session(s)
    assert hb._events_added == 5


def test_builder_sorted_buckets():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 17, tzinfo=timezone.utc)))
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    hb.add_event(_event(datetime(2026, 3, 16, tzinfo=timezone.utc)))
    keys = [b.key for b in hb.sorted_buckets()]
    assert keys == ["2026-03-15", "2026-03-16", "2026-03-17"]


# --- Summary ---

def test_summary_empty():
    hb = HeatmapBuilder()
    s = hb.summary()
    assert s["total_events"] == 0


def test_summary_nonempty():
    hb = HeatmapBuilder()
    for d in range(1, 4):
        hb.add_event(_event(datetime(2026, 3, d, tzinfo=timezone.utc), tokens_in=d * 100, tokens_out=d * 50))
    s = hb.summary()
    assert s["total_events"] == 3
    assert s["buckets"] == 3
    assert s["peak_bucket"] == "2026-03-03"


# --- Cost estimation ---

def test_cost_estimation_known_model():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc),
                        tokens_in=1000, tokens_out=500, model="gpt-4o"))
    bucket = hb.sorted_buckets()[0]
    assert bucket.cost > 0


def test_cost_estimation_unknown_model():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), model="unknown-model"))
    bucket = hb.sorted_buckets()[0]
    assert bucket.cost == 0.0


# --- Granularity ---

def test_hourly_granularity():
    hb = HeatmapBuilder(granularity="hour")
    hb.add_event(_event(datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)))
    hb.add_event(_event(datetime(2026, 3, 15, 10, 45, tzinfo=timezone.utc)))
    hb.add_event(_event(datetime(2026, 3, 15, 11, 5, tzinfo=timezone.utc)))
    assert len(hb._buckets) == 2  # 10:00 and 11:00


def test_weekly_granularity():
    hb = HeatmapBuilder(granularity="week")
    # Same week
    hb.add_event(_event(datetime(2026, 3, 9, tzinfo=timezone.utc)))   # Mon
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))  # Sun
    assert len(hb._buckets) == 1


# --- JSON export ---

def test_to_json():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    j = json.loads(hb.to_json())
    assert "summary" in j
    assert "buckets" in j
    assert len(j["buckets"]) == 1


def test_to_dict():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    d = hb.to_dict()
    assert d["summary"]["total_events"] == 1


# --- HTML rendering ---

def test_render_html():
    hb = HeatmapBuilder()
    for d in range(1, 8):
        hb.add_event(_event(datetime(2026, 3, d, tzinfo=timezone.utc)))
    html = hb.render()
    assert "<!DOCTYPE html>" in html
    assert "Token Usage Heatmap" in html
    assert "heatmap" in html


def test_render_custom_title():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    html = hb.render(title="My Custom Heatmap")
    assert "My Custom Heatmap" in html


def test_save(tmp_path):
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    out = hb.save(str(tmp_path / "test.html"))
    assert out.endswith("test.html")
    content = (tmp_path / "test.html").read_text()
    assert "<!DOCTYPE html>" in content


# --- Multiple models ---

def test_multiple_models():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), model="gpt-4o"))
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), model="claude-3-sonnet"))
    s = hb.summary()
    assert len(s["models"]) == 2


# --- Multiple sessions ---

def test_multiple_sessions():
    hb = HeatmapBuilder()
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), session_id="a"))
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), session_id="b"))
    bucket = hb.sorted_buckets()[0]
    assert bucket.to_dict()["session_count"] == 2


# --- Metric switching ---

def test_metric_tokens_in():
    hb = HeatmapBuilder(metric="tokens_in")
    hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc), tokens_in=500, tokens_out=100))
    s = hb.summary()
    assert s["peak_value"] == 500


def test_metric_event_count():
    hb = HeatmapBuilder(metric="event_count")
    for _ in range(3):
        hb.add_event(_event(datetime(2026, 3, 15, tzinfo=timezone.utc)))
    s = hb.summary()
    assert s["peak_value"] == 3
