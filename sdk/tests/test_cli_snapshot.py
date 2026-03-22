"""Tests for the CLI snapshot command."""

import json
import os
import tempfile

from agentlens.cli_snapshot import _capture_snapshot, _diff_snapshots, cmd_snapshot


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses=None):
        self._responses = responses or {}

    def get(self, path, params=None):
        for prefix, data in self._responses.items():
            if path.startswith(prefix):
                return FakeResponse(data)
        return FakeResponse([])


def test_capture_snapshot_basic():
    client = FakeClient({
        "/sessions": [
            {"session_id": "s1", "model": "gpt-4", "total_cost": 0.05, "total_tokens": 1000, "event_count": 10, "error_count": 1},
            {"session_id": "s2", "model": "gpt-4", "total_cost": 0.03, "total_tokens": 500, "event_count": 5, "error_count": 0},
        ],
        "/alerts": [
            {"severity": "warning"},
            {"severity": "critical"},
            {"severity": "warning"},
        ],
        "/sessions/s1/health": {"score": 85},
        "/sessions/s2/health": {"score": 95},
    })
    snap = _capture_snapshot(client, limit=10, label="test-run")

    assert snap["label"] == "test-run"
    assert snap["summary"]["sessions"] == 2
    assert snap["summary"]["total_cost_usd"] == 0.08
    assert snap["summary"]["total_tokens"] == 1500
    assert snap["summary"]["total_errors"] == 1
    assert snap["alerts"]["total"] == 3
    assert snap["alerts"]["by_severity"]["warning"] == 2
    assert snap["alerts"]["by_severity"]["critical"] == 1
    assert len(snap["sessions"]) == 2


def test_capture_snapshot_empty():
    client = FakeClient({"/sessions": [], "/alerts": []})
    snap = _capture_snapshot(client, limit=5, label=None)
    assert snap["summary"]["sessions"] == 0
    assert snap["summary"]["avg_health_score"] is None


def test_diff_snapshots(capsys):
    snap_a = {
        "timestamp": "2026-01-01T00:00:00Z",
        "label": "before",
        "summary": {
            "sessions": 10, "total_cost_usd": 1.0, "total_tokens": 5000,
            "total_events": 100, "total_errors": 5, "unique_models": ["gpt-4"],
            "avg_health_score": 80.0,
        },
        "alerts": {"total": 3, "by_severity": {"warning": 3}},
        "sessions": [],
    }
    snap_b = {
        "timestamp": "2026-01-02T00:00:00Z",
        "label": "after",
        "summary": {
            "sessions": 12, "total_cost_usd": 1.5, "total_tokens": 6000,
            "total_events": 120, "total_errors": 3, "unique_models": ["gpt-4"],
            "avg_health_score": 90.0,
        },
        "alerts": {"total": 1, "by_severity": {"warning": 1}},
        "sessions": [],
    }
    _diff_snapshots(snap_a, snap_b, "table")
    out = capsys.readouterr().out
    assert "Snapshot Diff" in out
    assert "+2" in out  # sessions delta


def test_diff_json_output(capsys):
    snap_a = {
        "timestamp": "2026-01-01T00:00:00Z", "label": "",
        "summary": {"sessions": 5, "total_cost_usd": 0.5, "total_tokens": 2000,
                     "total_events": 50, "total_errors": 2, "unique_models": [],
                     "avg_health_score": None},
        "alerts": {"total": 0, "by_severity": {}}, "sessions": [],
    }
    snap_b = dict(snap_a)
    snap_b["timestamp"] = "2026-01-02T00:00:00Z"
    _diff_snapshots(snap_a, snap_b, "json")
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "deltas" in data
