"""Tests for ``agentlens.cli_heatmap``.

The ``cmd_heatmap`` command renders a GitHub-style activity heatmap and
previously had only ~5% line coverage. This module covers the major
branches: empty data, every supported ``metric`` mode, malformed
timestamps, the cutoff window, and the rendering output (legend,
summary stats, peak/busiest lines).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_heatmap import cmd_heatmap


def _mk_args(**overrides):
    args = MagicMock()
    args.metric = "sessions"
    args.weeks = 12
    args.limit = 500
    args.endpoint = None
    args.api_key = None
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _mock_get_client(sessions_payload):
    """Patch ``get_client`` to return a (client, endpoint) tuple."""
    client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = sessions_payload
    client.get.return_value = resp
    return patch(
        "agentlens.cli_heatmap.get_client",
        return_value=(client, "http://localhost:3000"),
    )


def _iso(dt: datetime) -> str:
    """ISO 8601 timestamp with trailing Z (matches backend serializer)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Empty / no-data paths
# ---------------------------------------------------------------------------


class TestNoData:
    def test_empty_session_list_prints_no_data_warning(self, capsys):
        with _mock_get_client([]):
            cmd_heatmap(_mk_args())
        out = capsys.readouterr().out
        assert "No session data" in out

    def test_all_sessions_outside_cutoff(self, capsys):
        ancient = datetime.now(timezone.utc) - timedelta(weeks=200)
        sessions = [{"id": "s1", "created_at": _iso(ancient)}]
        with _mock_get_client(sessions):
            cmd_heatmap(_mk_args(weeks=4))
        out = capsys.readouterr().out
        assert "No session data" in out

    def test_dict_payload_with_sessions_key(self, capsys):
        sessions = [
            {"id": "s1", "created_at": _iso(datetime.now(timezone.utc) - timedelta(days=1))}
        ]
        with _mock_get_client({"sessions": sessions}):
            cmd_heatmap(_mk_args())
        out = capsys.readouterr().out
        assert "Activity Heatmap" in out


# ---------------------------------------------------------------------------
# Malformed inputs are skipped, not fatal
# ---------------------------------------------------------------------------


class TestMalformedInputs:
    def test_missing_created_at_is_skipped(self, capsys):
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        sessions = [
            {"id": "s1"},  # no created_at
            {"id": "s2", "created_at": ""},  # empty
            {"id": "s3", "created_at": "not-a-date"},  # invalid
            {"id": "s4", "created_at": _iso(now)},  # valid
        ]
        with _mock_get_client(sessions):
            cmd_heatmap(_mk_args())
        out = capsys.readouterr().out
        # The single valid row keeps the total at 1.0
        assert "Total: 1" in out


# ---------------------------------------------------------------------------
# Metric modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric,field,value,expected_label",
    [
        ("sessions", None, None, "Sessions"),
        ("cost", "total_cost", 2.5, "Cost ($)"),
        ("tokens", "total_tokens", 1234, "Tokens"),
        ("events", "event_count", 7, "Events"),
    ],
)
def test_metric_modes_render(metric, field, value, expected_label, capsys):
    now = datetime.now(timezone.utc) - timedelta(hours=3)
    session = {"id": "s1", "created_at": _iso(now)}
    if field is not None:
        session[field] = value
    with _mock_get_client([session]):
        cmd_heatmap(_mk_args(metric=metric))
    out = capsys.readouterr().out
    assert expected_label in out
    assert "Activity Heatmap" in out
    # Day-of-week labels rendered
    for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert name in out


# ---------------------------------------------------------------------------
# Defaults / fallbacks for missing args
# ---------------------------------------------------------------------------


class TestArgumentDefaults:
    def test_falsy_args_fall_back_to_defaults(self, capsys):
        """If args.metric / args.weeks / args.limit are 0/None, defaults apply."""
        now = datetime.now(timezone.utc) - timedelta(hours=1)
        sessions = [{"id": "s1", "created_at": _iso(now)}]
        args = _mk_args(metric=None, weeks=0, limit=0)
        with _mock_get_client(sessions):
            cmd_heatmap(args)
        out = capsys.readouterr().out
        # Default metric is "sessions" -> "Sessions" label
        assert "Sessions" in out
        # Default weeks=12 -> appears in header
        assert "12 weeks" in out


# ---------------------------------------------------------------------------
# Summary stats / busiest day & hour
# ---------------------------------------------------------------------------


class TestSummaryStats:
    def test_peak_and_busiest_reported(self, capsys):
        """Build a dataset with a clear peak slot and verify it's reported."""
        # Anchor on a Wednesday at 14:00 UTC, with multiple hits there
        base = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)  # Wed
        assert base.weekday() == 2  # sanity: Wednesday
        sessions = []
        # 5 sessions in the peak slot
        for i in range(5):
            sessions.append(
                {"id": f"peak{i}", "created_at": _iso(base + timedelta(minutes=i))}
            )
        # 1 elsewhere
        sessions.append(
            {"id": "other", "created_at": _iso(base + timedelta(days=1, hours=2))}
        )

        # Make cutoff far enough back that our fixed dates aren't dropped
        # by faking "now" via patching datetime would be heavy; just use
        # a large weeks window so today - 9999w includes our anchor.
        with _mock_get_client(sessions):
            cmd_heatmap(_mk_args(weeks=9999))
        out = capsys.readouterr().out
        assert "Peak: Wed 14:00" in out
        assert "Busiest day: Wed" in out
        assert "Busiest hour: 14:00" in out
        # Total rendered (5 peak + 1 other = 6.0)
        assert "Total: 6" in out


# ---------------------------------------------------------------------------
# Z-suffix vs explicit offset both parse
# ---------------------------------------------------------------------------


class TestTimestampFormats:
    def test_z_suffix_parses(self, capsys):
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        sessions = [{"id": "s1", "created_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z")}]
        with _mock_get_client(sessions):
            cmd_heatmap(_mk_args())
        out = capsys.readouterr().out
        assert "Activity Heatmap" in out

    def test_offset_suffix_parses(self, capsys):
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        sessions = [{"id": "s1", "created_at": now.isoformat()}]  # has +00:00
        with _mock_get_client(sessions):
            cmd_heatmap(_mk_args())
        out = capsys.readouterr().out
        assert "Activity Heatmap" in out
