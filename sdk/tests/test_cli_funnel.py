"""Tests for ``agentlens.cli_funnel``.

Covers the pure helpers (``_build_funnel``, ``_render_table``, ``_render_html``)
and the ``cmd_funnel`` command in table/json/html output modes using mocked
HTTP clients. Before this module was added, ``cli_funnel`` sat at ~11% line
coverage despite being a fully exercised user-facing CLI command.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from agentlens.cli_funnel import (
    DEFAULT_STAGES,
    _build_funnel,
    _fetch_events,
    _fetch_sessions,
    _render_html,
    _render_table,
    cmd_funnel,
)


# ---------------------------------------------------------------------------
# _build_funnel
# ---------------------------------------------------------------------------


class TestBuildFunnel:
    def test_empty_sessions_returns_empty_funnel(self):
        assert _build_funnel([], {}, ["plan", "result"]) == []

    def test_single_session_all_stages_reached(self):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}, {"event_type": "result"}]}
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        assert len(funnel) == 2
        assert [row["stage"] for row in funnel] == ["plan", "result"]
        assert all(row["count"] == 1 for row in funnel)
        assert funnel[0]["pct_total"] == 100.0
        assert funnel[1]["pct_total"] == 100.0
        assert funnel[1]["pct_prev"] == 100.0
        assert funnel[1]["drop"] == 0

    def test_drop_off_between_stages(self):
        sessions = [{"id": f"s{i}"} for i in range(10)]
        events = {
            "s0": [{"event_type": "plan"}, {"event_type": "result"}],
            "s1": [{"event_type": "plan"}, {"event_type": "result"}],
            "s2": [{"event_type": "plan"}],
            "s3": [{"event_type": "plan"}],
            "s4": [{"event_type": "plan"}],
            "s5": [],
            "s6": [],
            "s7": [],
            "s8": [],
            "s9": [],
        }
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        assert funnel[0]["count"] == 5
        assert funnel[0]["pct_total"] == 50.0
        assert funnel[1]["count"] == 2
        assert funnel[1]["drop"] == 3
        # 2/5 = 40% drop-through from plan -> result
        assert funnel[1]["pct_prev"] == 40.0

    def test_sequential_pipeline_enforced(self):
        """A session must reach stage i-1 to count at stage i.

        s_skip has only ``result`` events (no ``plan``). It should NOT be
        counted at ``result`` because it never passed through ``plan``.
        """
        sessions = [
            {"id": "s_normal"},
            {"id": "s_skip"},
        ]
        events = {
            "s_normal": [{"event_type": "plan"}, {"event_type": "result"}],
            "s_skip": [{"event_type": "result"}],
        }
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        assert funnel[0]["count"] == 1  # only s_normal reached plan
        assert funnel[1]["count"] == 1  # only s_normal flows through

    def test_event_type_fallback_to_type_key(self):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"type": "plan"}]}  # uses ``type`` not ``event_type``
        funnel = _build_funnel(sessions, events, ["plan"])
        assert funnel[0]["count"] == 1

    def test_session_id_fallback(self):
        sessions = [{"session_id": "s1"}]
        events = {"s1": [{"event_type": "plan"}]}
        funnel = _build_funnel(sessions, events, ["plan"])
        assert funnel[0]["count"] == 1

    def test_zero_count_first_stage_yields_zero_drop_through(self):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "other"}]}
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        assert funnel[0]["count"] == 0
        # pct_prev is computed against prev_count==0 -> guarded to 0
        assert funnel[1]["pct_prev"] == 0
        assert funnel[1]["count"] == 0


# ---------------------------------------------------------------------------
# _render_table / _render_html
# ---------------------------------------------------------------------------


class TestRenderTable:
    def test_includes_header_and_stage_rows(self):
        sessions = [{"id": "s1"}, {"id": "s2"}]
        events = {
            "s1": [{"event_type": "plan"}, {"event_type": "result"}],
            "s2": [{"event_type": "plan"}],
        }
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        out = _render_table(funnel, total=2, stages=["plan", "result"])
        assert "Agent Workflow Funnel" in out
        assert "plan" in out
        assert "result" in out
        assert "Stage" in out
        assert "Overall conversion" in out
        # 50% conversion plan -> result (1 / 2)
        assert "50.0%" in out

    def test_biggest_dropoff_summary_appears(self):
        sessions = [{"id": f"s{i}"} for i in range(10)]
        events = {f"s{i}": [{"event_type": "plan"}] for i in range(10)}
        events["s0"] += [{"event_type": "result"}]
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        out = _render_table(funnel, total=10, stages=["plan", "result"])
        assert "Biggest drop-off" in out
        assert "lost 9 sessions" in out


class TestRenderHtml:
    def test_html_well_formed(self):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}, {"event_type": "result"}]}
        funnel = _build_funnel(sessions, events, ["plan", "result"])
        html = _render_html(funnel, total=1)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "<title>AgentLens" in html
        assert "plan" in html
        assert "result" in html

    def test_html_escapes_stage_names(self):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "<script>"}]}
        funnel = _build_funnel(sessions, events, ["<script>"])
        html = _render_html(funnel, total=1)
        assert "<script>" not in html  # raw tag must be escaped
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# _fetch_sessions / _fetch_events
# ---------------------------------------------------------------------------


class TestFetchHelpers:
    def test_fetch_sessions_list_payload(self):
        client = MagicMock()
        client.get.return_value.json.return_value = [{"id": "s1"}]
        client.get.return_value.raise_for_status = lambda: None
        out = _fetch_sessions(client, 50)
        assert out == [{"id": "s1"}]
        client.get.assert_called_once_with("/sessions", params={"limit": 50})

    def test_fetch_sessions_dict_payload(self):
        client = MagicMock()
        client.get.return_value.json.return_value = {"sessions": [{"id": "s1"}]}
        client.get.return_value.raise_for_status = lambda: None
        out = _fetch_sessions(client, 10)
        assert out == [{"id": "s1"}]

    def test_fetch_events_list_payload(self):
        client = MagicMock()
        client.get.return_value.json.return_value = [{"event_type": "plan"}]
        client.get.return_value.raise_for_status = lambda: None
        out = _fetch_events(client, "sess-1")
        assert out == [{"event_type": "plan"}]
        client.get.assert_called_once_with("/events", params={"session_id": "sess-1", "limit": 5000})


# ---------------------------------------------------------------------------
# cmd_funnel — integration with mocked client
# ---------------------------------------------------------------------------


def _mk_args(**overrides):
    args = MagicMock()
    args.stages = None
    args.limit = None
    args.format = None
    args.output = None
    args.open = False
    args.endpoint = None
    args.api_key = None
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _mock_client_with(sessions, events_by_sid):
    """Return a MagicMock client whose .get() dispatches /sessions vs /events."""
    client = MagicMock()

    def fake_get(path, params=None):
        params = params or {}
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if path == "/sessions":
            resp.json.return_value = sessions
        elif path == "/events":
            sid = params.get("session_id")
            resp.json.return_value = events_by_sid.get(sid, [])
        else:  # pragma: no cover — safety net
            resp.json.return_value = []
        return resp

    client.get.side_effect = fake_get
    return client


class TestCmdFunnel:
    def test_no_sessions_short_circuits(self, capsys):
        client = _mock_client_with([], {})
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="table"))
        out = capsys.readouterr().out
        assert "No sessions found." in out

    def test_default_stages_used_when_none_specified(self, capsys):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": s} for s in DEFAULT_STAGES]}
        client = _mock_client_with(sessions, events)
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="table"))
        out = capsys.readouterr().out
        for stage in DEFAULT_STAGES:
            assert stage in out

    def test_json_format_prints_valid_json(self, capsys):
        sessions = [{"id": "s1"}, {"id": "s2"}]
        events = {
            "s1": [{"event_type": "plan"}, {"event_type": "result"}],
            "s2": [{"event_type": "plan"}],
        }
        client = _mock_client_with(sessions, events)
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="json", stages="plan,result"))
        out = capsys.readouterr().out
        # Strip leading "Fetching..." progress lines
        json_start = out.index("{")
        payload = json.loads(out[json_start:])
        assert payload["total_sessions"] == 2
        assert payload["stages"] == ["plan", "result"]
        assert payload["funnel"][0]["stage"] == "plan"
        assert payload["funnel"][1]["stage"] == "result"

    def test_json_format_writes_to_file(self, tmp_path):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}]}
        client = _mock_client_with(sessions, events)
        out_file = tmp_path / "funnel.json"
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="json", stages="plan", output=str(out_file)))
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        assert payload["total_sessions"] == 1

    def test_html_format_writes_to_file_and_does_not_open(self, tmp_path):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}]}
        client = _mock_client_with(sessions, events)
        out_file = tmp_path / "funnel.html"
        with patch("agentlens.cli_funnel._get_client", return_value=client), \
             patch("agentlens.cli_funnel.webbrowser.open") as mock_open:
            cmd_funnel(_mk_args(format="html", stages="plan", output=str(out_file)))
        body = out_file.read_text(encoding="utf-8")
        assert body.startswith("<!DOCTYPE html>")
        mock_open.assert_not_called()

    def test_html_format_open_invokes_webbrowser(self, tmp_path, monkeypatch):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}]}
        client = _mock_client_with(sessions, events)
        monkeypatch.chdir(tmp_path)
        with patch("agentlens.cli_funnel._get_client", return_value=client), \
             patch("agentlens.cli_funnel.webbrowser.open") as mock_open:
            cmd_funnel(_mk_args(format="html", stages="plan", open=True))
        assert mock_open.call_count == 1
        url_arg = mock_open.call_args[0][0]
        assert url_arg.startswith("file://")
        assert os.path.exists(tmp_path / "agentlens-funnel.html")

    def test_table_format_writes_to_file(self, tmp_path):
        sessions = [{"id": "s1"}]
        events = {"s1": [{"event_type": "plan"}]}
        client = _mock_client_with(sessions, events)
        out_file = tmp_path / "funnel.txt"
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="table", stages="plan", output=str(out_file)))
        body = out_file.read_text(encoding="utf-8")
        assert "Agent Workflow Funnel" in body

    def test_event_fetch_failure_is_swallowed(self, capsys):
        """If /events 500s for a session, the funnel still proceeds with empty events."""
        sessions = [{"id": "s1"}, {"id": "s2"}]
        client = MagicMock()

        def fake_get(path, params=None):
            params = params or {}
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if path == "/sessions":
                resp.json.return_value = sessions
            else:  # /events
                if params.get("session_id") == "s1":
                    raise RuntimeError("backend on fire")
                resp.json.return_value = [{"event_type": "plan"}]
            return resp

        client.get.side_effect = fake_get
        with patch("agentlens.cli_funnel._get_client", return_value=client):
            cmd_funnel(_mk_args(format="json", stages="plan"))
        out = capsys.readouterr().out
        payload = json.loads(out[out.index("{"):])
        # only s2 reached plan
        assert payload["funnel"][0]["count"] == 1
