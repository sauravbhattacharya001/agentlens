"""Tests for agentlens.tracker — session management and event tracking."""

from unittest.mock import MagicMock, patch

import pytest

from agentlens.models import AgentEvent, Session
from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture
def mock_transport():
    """Transport with all network calls mocked."""
    t = MagicMock(spec=Transport)
    t.endpoint = "http://test:3000"
    t.api_key = "test-key"
    t._client = MagicMock()
    return t


@pytest.fixture
def tracker(mock_transport):
    return AgentTracker(transport=mock_transport)


class TestStartSession:
    def test_creates_session(self, tracker, mock_transport):
        session = tracker.start_session(agent_name="test-agent")

        assert session.agent_name == "test-agent"
        assert session.status == "active"
        assert session.session_id in tracker.sessions
        assert tracker._current_session_id == session.session_id
        mock_transport.send_events.assert_called_once()

    def test_sends_session_start_event(self, tracker, mock_transport):
        tracker.start_session(agent_name="my-agent", metadata={"version": "1.0"})

        call_args = mock_transport.send_events.call_args[0][0]
        assert len(call_args) == 1
        event = call_args[0]
        assert event["event_type"] == "session_start"
        assert event["agent_name"] == "my-agent"
        assert event["metadata"] == {"version": "1.0"}

    def test_multiple_sessions(self, tracker, mock_transport):
        s1 = tracker.start_session(agent_name="agent-1")
        s2 = tracker.start_session(agent_name="agent-2")

        assert len(tracker.sessions) == 2
        assert tracker._current_session_id == s2.session_id


class TestEndSession:
    def test_ends_current_session(self, tracker, mock_transport):
        session = tracker.start_session()
        tracker.end_session()

        assert session.status == "completed"
        assert session.ended_at is not None
        assert tracker._current_session_id is None
        mock_transport.flush.assert_called_once()

    def test_ends_specific_session(self, tracker, mock_transport):
        s1 = tracker.start_session(agent_name="a")
        s2 = tracker.start_session(agent_name="b")

        tracker.end_session(session_id=s1.session_id)

        assert s1.status == "completed"
        # Current session should still be s2 (we ended s1 explicitly)
        assert tracker._current_session_id == s2.session_id

    def test_end_nonexistent_session_noop(self, tracker, mock_transport):
        # Should not raise
        tracker.end_session(session_id="nonexistent")

    def test_end_with_no_session_noop(self, tracker, mock_transport):
        tracker.end_session()


class TestTrack:
    def test_track_generic_event(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track(
            event_type="generic",
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
        )

        assert event.event_type == "generic"
        assert event.input_data == {"prompt": "hello"}
        mock_transport.send_events.call_count == 2  # start + track

    def test_track_llm_call(self, tracker, mock_transport):
        session = tracker.start_session()
        event = tracker.track(
            event_type="llm_call",
            model="gpt-4",
            tokens_in=100,
            tokens_out=50,
        )

        assert event.event_type == "llm_call"
        assert event.model == "gpt-4"
        assert session.total_tokens_in == 100
        assert session.total_tokens_out == 50

    def test_track_with_tool_call(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track(
            event_type="tool_call",
            tool_name="web_search",
            tool_input={"query": "test"},
            tool_output={"results": []},
            duration_ms=150.0,
        )

        assert event.tool_call is not None
        assert event.tool_call.tool_name == "web_search"
        assert event.tool_call.tool_input == {"query": "test"}
        assert event.tool_call.duration_ms == 150.0

    def test_track_with_reasoning(self, tracker, mock_transport):
        session = tracker.start_session()
        event = tracker.track(
            event_type="agent_call",
            reasoning="Chose search because the question is factual",
        )

        assert event.decision_trace is not None
        assert event.decision_trace.reasoning == "Chose search because the question is factual"
        assert event.decision_trace.step == 1  # first event

    def test_track_without_session(self, mock_transport):
        tracker = AgentTracker(transport=mock_transport)
        # Should not raise — just tracks with empty session_id
        event = tracker.track(event_type="generic")
        assert event.session_id == ""

    def test_track_tool_convenience(self, tracker, mock_transport):
        tracker.start_session()
        event = tracker.track_tool(
            tool_name="calculator",
            tool_input={"expr": "2+2"},
            tool_output={"result": 4},
            duration_ms=0.5,
        )

        assert event.event_type == "tool_call"
        assert event.tool_call.tool_name == "calculator"


class TestCurrentSession:
    def test_no_session(self, tracker):
        assert tracker.current_session is None

    def test_with_session(self, tracker, mock_transport):
        s = tracker.start_session()
        assert tracker.current_session is s

    def test_after_end(self, tracker, mock_transport):
        tracker.start_session()
        tracker.end_session()
        assert tracker.current_session is None


class TestExplain:
    def test_explain_no_session(self, tracker):
        result = tracker.explain()
        assert result == "No active session."

    def test_explain_nonexistent_session(self, tracker):
        result = tracker.explain(session_id="nope")
        assert "not found" in result

    def test_explain_with_events(self, tracker, mock_transport):
        tracker.start_session(agent_name="explainer")
        tracker.track(
            event_type="llm_call",
            model="gpt-4",
            tokens_in=10,
            tokens_out=5,
            input_data={"prompt": "hello"},
            output_data={"response": "hi"},
        )
        tracker.track(
            event_type="tool_call",
            tool_name="search",
            tool_input={"q": "test"},
        )

        explanation = tracker.explain()
        assert "explainer" in explanation
        assert "llm_call" in explanation
        assert "tool_call" in explanation
        assert "search" in explanation


# ── Helper to set up mock HTTP responses ─────────────────────────

def _mock_response(json_data=None, text_data=""):
    """Create a MagicMock response with json() and text."""
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.text = text_data
    resp.raise_for_status = MagicMock()
    return resp


# ── Compare Sessions ─────────────────────────────────────────────

class TestCompareSessions:
    def test_compare_sessions_calls_backend(self, tracker, mock_transport):
        resp = _mock_response({"deltas": {}})
        mock_transport.post.return_value = resp

        tracker.compare_sessions("sid-a", "sid-b")

        mock_transport.post.assert_called_once()
        call_args = mock_transport.post.call_args
        assert "/sessions/compare" in call_args[0][0]
        assert call_args[1]["json"] == {"session_a": "sid-a", "session_b": "sid-b"}

    def test_compare_sessions_empty_id_raises_ValueError(self, tracker):
        with pytest.raises(ValueError, match="required"):
            tracker.compare_sessions("", "sid-b")
        with pytest.raises(ValueError, match="required"):
            tracker.compare_sessions("sid-a", "")

    def test_compare_sessions_same_id_raises_ValueError(self, tracker):
        with pytest.raises(ValueError, match="itself"):
            tracker.compare_sessions("same", "same")

    def test_compare_sessions_returns_response_json(self, tracker, mock_transport):
        expected = {"session_a": {}, "session_b": {}, "deltas": {}, "shared": {}}
        mock_transport.post.return_value = _mock_response(expected)

        result = tracker.compare_sessions("a", "b")
        assert result == expected


# ── Export Session ───────────────────────────────────────────────

class TestExportSession:
    def test_export_json_format(self, tracker, mock_transport):
        tracker.start_session()
        expected = {"events": [], "summary": {}}
        mock_transport.get.return_value = _mock_response(expected)

        result = tracker.export_session(format="json")

        assert result == expected
        call_args = mock_transport.get.call_args
        assert call_args[1]["params"]["format"] == "json"

    def test_export_csv_format(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response(text_data="col1,col2\na,b")

        result = tracker.export_session(format="csv")

        assert result == "col1,col2\na,b"
        call_args = mock_transport.get.call_args
        assert call_args[1]["params"]["format"] == "csv"

    def test_export_invalid_format_raises_ValueError(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="Invalid format"):
            tracker.export_session(format="xml")

    def test_export_no_session_raises_RuntimeError(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.export_session()

    def test_export_specific_session_id(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response({"ok": True})

        result = tracker.export_session(session_id="custom-sid")

        call_args = mock_transport.get.call_args
        assert "custom-sid" in call_args[0][0]


# ── Costs & Pricing ─────────────────────────────────────────────

class TestCosts:
    def test_get_costs_calls_backend(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"total_cost": 0.42})

        result = tracker.get_costs()

        assert result["total_cost"] == 0.42
        call_args = mock_transport.get.call_args
        assert "/pricing/costs/" in call_args[0][0]

    def test_get_costs_no_session_raises_RuntimeError(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.get_costs()

    def test_get_pricing_calls_backend(self, tracker, mock_transport):
        expected = {"pricing": {"gpt-4": {"input_cost_per_1m": 30}}, "defaults": {}}
        mock_transport.get.return_value = _mock_response(expected)

        result = tracker.get_pricing()

        assert result == expected
        call_args = mock_transport.get.call_args
        assert "/pricing" in call_args[0][0]

    def test_set_pricing_calls_backend(self, tracker, mock_transport):
        pricing = {"gpt-4": {"input_cost_per_1m": 30, "output_cost_per_1m": 60}}
        mock_transport.put.return_value = _mock_response({"status": "ok", "updated": 1})

        result = tracker.set_pricing(pricing)

        assert result["status"] == "ok"
        call_args = mock_transport.put.call_args
        assert "/pricing" in call_args[0][0]

    def test_set_pricing_payload_structure(self, tracker, mock_transport):
        pricing = {"claude-3": {"input_cost_per_1m": 15, "output_cost_per_1m": 75}}
        mock_transport.put.return_value = _mock_response({"status": "ok", "updated": 1})

        tracker.set_pricing(pricing)

        call_args = mock_transport.put.call_args
        assert call_args[1]["json"] == {"pricing": pricing}


# ── Search Events ────────────────────────────────────────────────

class TestSearchEvents:
    def test_search_basic_query(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": [], "matched": 0})

        tracker.search_events(q="hello")

        call_args = mock_transport.get.call_args
        assert call_args[1]["params"]["q"] == "hello"

    def test_search_no_session_raises_RuntimeError(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.search_events()

    def test_search_all_filters(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events(
            q="test",
            event_type="llm_call",
            model="gpt-4",
            min_tokens=10,
            max_tokens=500,
            min_duration_ms=100.0,
            has_tools=True,
            has_reasoning=True,
            errors=True,
            after="2025-01-01T00:00:00Z",
            before="2025-12-31T23:59:59Z",
        )

        params = mock_transport.get.call_args[1]["params"]
        assert params["q"] == "test"
        assert params["type"] == "llm_call"
        assert params["model"] == "gpt-4"
        assert params["min_tokens"] == 10
        assert params["max_tokens"] == 500
        assert params["min_duration_ms"] == 100.0
        assert params["has_tools"] == "true"
        assert params["has_reasoning"] == "true"
        assert params["errors"] == "true"
        assert params["after"] == "2025-01-01T00:00:00Z"
        assert params["before"] == "2025-12-31T23:59:59Z"

    def test_search_limit_capped_at_500(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events(limit=9999)

        params = mock_transport.get.call_args[1]["params"]
        assert params["limit"] == 500

    def test_search_offset_floored_at_0(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events(offset=-50)

        params = mock_transport.get.call_args[1]["params"]
        assert params["offset"] == 0

    def test_search_empty_query_omitted(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events(q=None)

        params = mock_transport.get.call_args[1]["params"]
        assert "q" not in params

    def test_search_default_limit_100(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events()

        params = mock_transport.get.call_args[1]["params"]
        assert params["limit"] == 100

    def test_search_boolean_filters_as_strings(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.search_events(has_tools=True, has_reasoning=True, errors=True)

        params = mock_transport.get.call_args[1]["params"]
        assert params["has_tools"] == "true"
        assert params["has_reasoning"] == "true"
        assert params["errors"] == "true"


# ── Alert Rules ──────────────────────────────────────────────────

class TestAlertRules:
    def test_list_alert_rules_no_filter(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response({"rules": []})

        tracker.list_alert_rules()

        call_args = mock_transport.get.call_args
        assert "/alerts/rules" in call_args[0][0]
        assert call_args[1]["params"] == {}

    def test_list_alert_rules_enabled_filter(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response({"rules": []})

        tracker.list_alert_rules(enabled=True)

        params = mock_transport.get.call_args[1]["params"]
        assert params["enabled"] == "true"

    def test_create_alert_rule_payload(self, tracker, mock_transport):
        mock_transport.post.return_value = _mock_response({"rule_id": "r1"})

        tracker.create_alert_rule(
            name="High tokens",
            metric="total_tokens",
            operator=">",
            threshold=10000,
            window_minutes=30,
        )

        call_args = mock_transport.post.call_args
        payload = call_args[1]["json"]
        assert payload["name"] == "High tokens"
        assert payload["metric"] == "total_tokens"
        assert payload["operator"] == ">"
        assert payload["threshold"] == 10000
        assert payload["window_minutes"] == 30

    def test_create_alert_rule_with_agent_filter(self, tracker, mock_transport):
        mock_transport.post.return_value = _mock_response({"rule_id": "r2"})

        tracker.create_alert_rule(
            name="Test",
            metric="error_rate",
            operator=">=",
            threshold=0.1,
            agent_filter="my-agent",
        )

        payload = mock_transport.post.call_args[1]["json"]
        assert payload["agent_filter"] == "my-agent"

    def test_update_alert_rule_calls_put(self, tracker, mock_transport):
        mock_transport.put.return_value = _mock_response({"updated": True})

        tracker.update_alert_rule("rule-123", threshold=5000, name="Updated")

        call_args = mock_transport.put.call_args
        assert "rule-123" in call_args[0][0]
        assert call_args[1]["json"] == {"threshold": 5000, "name": "Updated"}

    def test_delete_alert_rule_calls_delete(self, tracker, mock_transport):
        mock_transport.delete.return_value = _mock_response({"deleted": True})

        tracker.delete_alert_rule("rule-456")

        call_args = mock_transport.delete.call_args
        assert "rule-456" in call_args[0][0]

    def test_evaluate_alerts_calls_post(self, tracker, mock_transport):
        mock_transport.post.return_value = _mock_response({"triggered": 2})

        result = tracker.evaluate_alerts()

        assert result["triggered"] == 2
        call_args = mock_transport.post.call_args
        assert "/alerts/evaluate" in call_args[0][0]

    def test_get_alert_events_params(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response({"events": []})

        tracker.get_alert_events(rule_id="r1", acknowledged=False, limit=25)

        params = mock_transport.get.call_args[1]["params"]
        assert params["rule_id"] == "r1"
        assert params["acknowledged"] == "false"
        assert params["limit"] == 25


# ── Tags ─────────────────────────────────────────────────────────

class TestTags:
    def test_add_tags_no_session_raises(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.add_tags(["prod"])

    def test_add_tags_empty_list_raises(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="non-empty"):
            tracker.add_tags([])

    def test_add_tags_calls_post(self, tracker, mock_transport):
        session = tracker.start_session()
        mock_transport.post.return_value = _mock_response(
            {"session_id": session.session_id, "added": 2, "tags": ["a", "b"]}
        )

        result = tracker.add_tags(["a", "b"])

        assert result["added"] == 2
        call_args = mock_transport.post.call_args
        assert "/tags" in call_args[0][0]
        assert call_args[1]["json"] == {"tags": ["a", "b"]}

    def test_remove_tags_with_list(self, tracker, mock_transport):
        session = tracker.start_session()
        mock_transport.delete.return_value = _mock_response(
            {"session_id": session.session_id, "removed": 1, "tags": []}
        )

        result = tracker.remove_tags(["old-tag"])

        call_args = mock_transport.delete.call_args
        assert f"/sessions/{session.session_id}/tags" in call_args[0][0]
        assert call_args[1]["json"] == {"tags": ["old-tag"]}

    def test_remove_tags_all(self, tracker, mock_transport):
        session = tracker.start_session()
        mock_transport.delete.return_value = _mock_response(
            {"session_id": session.session_id, "removed": 3, "tags": []}
        )

        result = tracker.remove_tags()

        call_args = mock_transport.delete.call_args
        assert call_args[1]["json"] == {}

    def test_get_tags_returns_list(self, tracker, mock_transport):
        session = tracker.start_session()
        mock_transport.get.return_value = _mock_response(
            {"tags": ["prod", "v2"]}
        )

        result = tracker.get_tags()

        assert result == ["prod", "v2"]

    def test_list_all_tags_calls_get(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response(
            {"tags": [{"tag": "prod", "session_count": 5}]}
        )

        result = tracker.list_all_tags()

        assert len(result) == 1
        assert result[0]["tag"] == "prod"
        call_args = mock_transport.get.call_args
        assert "/sessions/tags" in call_args[0][0]

    def test_list_sessions_by_tag_empty_tag_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty"):
            tracker.list_sessions_by_tag("")


# ── Annotations ──────────────────────────────────────────────────

class TestAnnotations:
    def test_annotate_no_session_raises(self, tracker):
        with pytest.raises(RuntimeError, match="No session"):
            tracker.annotate("test note")

    def test_annotate_empty_text_raises(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="non-empty"):
            tracker.annotate("")

    def test_annotate_sends_payload(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.post.return_value = _mock_response({"annotation_id": "ann-1"})

        result = tracker.annotate("Bug found", annotation_type="bug", author="tester")

        payload = mock_transport.post.call_args[1]["json"]
        assert payload["text"] == "Bug found"
        assert payload["type"] == "bug"
        assert payload["author"] == "tester"
        assert "event_id" not in payload

    def test_annotate_with_event_id(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.post.return_value = _mock_response({"annotation_id": "ann-2"})

        tracker.annotate("Note", event_id="evt-42")

        payload = mock_transport.post.call_args[1]["json"]
        assert payload["event_id"] == "evt-42"

    def test_get_annotations_params(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.get.return_value = _mock_response({"annotations": []})

        tracker.get_annotations(annotation_type="bug", author="sdk", limit=50, offset=10)

        params = mock_transport.get.call_args[1]["params"]
        assert params["type"] == "bug"
        assert params["author"] == "sdk"
        assert params["limit"] == 50
        assert params["offset"] == 10

    def test_update_annotation_no_fields_raises(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="At least one field"):
            tracker.update_annotation("ann-1")

    def test_delete_annotation_empty_id_raises(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="annotation_id"):
            tracker.delete_annotation("")

    def test_list_recent_annotations_params(self, tracker, mock_transport):
        mock_transport.get.return_value = _mock_response(
            {"total": 5, "annotations": []}
        )

        tracker.list_recent_annotations(annotation_type="warning", limit=20)

        params = mock_transport.get.call_args[1]["params"]
        assert params["type"] == "warning"
        assert params["limit"] == 20


# ── Retention ────────────────────────────────────────────────────

class TestRetention:
    def test_get_retention_config(self, tracker, mock_transport):
        expected = {"config": {"max_age_days": 90, "auto_purge": True}}
        mock_transport.get.return_value = _mock_response(expected)

        result = tracker.get_retention_config()

        assert result == expected
        call_args = mock_transport.get.call_args
        assert "/retention/config" in call_args[0][0]

    def test_set_retention_config_payload(self, tracker, mock_transport):
        mock_transport.put.return_value = _mock_response(
            {"config": {"max_age_days": 30}, "updated": 1}
        )

        tracker.set_retention_config(max_age_days=30, exempt_tags=["prod"])

        payload = mock_transport.put.call_args[1]["json"]
        assert payload["max_age_days"] == 30
        assert payload["exempt_tags"] == ["prod"]

    def test_set_retention_config_no_fields_raises(self, tracker):
        with pytest.raises(ValueError, match="At least one"):
            tracker.set_retention_config()

    def test_get_retention_stats(self, tracker, mock_transport):
        expected = {"sessions": 100, "events": 5000, "eligible_for_purge": 10}
        mock_transport.get.return_value = _mock_response(expected)

        result = tracker.get_retention_stats()

        assert result == expected
        assert "/retention/stats" in mock_transport.get.call_args[0][0]

    def test_purge_dry_run_param(self, tracker, mock_transport):
        mock_transport.post.return_value = _mock_response(
            {"dry_run": True, "would_purge_sessions": 5}
        )

        result = tracker.purge(dry_run=True)

        assert result["dry_run"] is True
        params = mock_transport.post.call_args[1]["params"]
        assert params["dry_run"] == "true"


# ── Health Score ─────────────────────────────────────────────────

class TestHealthScore:
    def test_health_score_no_session_raises(self, tracker):
        with pytest.raises(RuntimeError, match="Session not found"):
            tracker.health_score()

    def test_health_score_returns_report(self, tracker, mock_transport):
        session = tracker.start_session(agent_name="test")
        tracker.track(event_type="llm_call", tokens_in=100, tokens_out=50, duration_ms=200.0)

        report = tracker.health_score()

        assert report.session_id == session.session_id
        assert 0 <= report.overall_score <= 100
        assert report.grade is not None
        assert len(report.metrics) > 0

    def test_health_score_custom_thresholds(self, tracker, mock_transport):
        from agentlens.health import HealthThresholds

        tracker.start_session()
        tracker.track(event_type="llm_call", tokens_in=100, tokens_out=50, duration_ms=200.0)

        custom = HealthThresholds(max_error_rate=0.01, max_avg_latency_ms=1000.0)
        report = tracker.health_score(thresholds=custom)

        assert report.session_id is not None
        assert isinstance(report.overall_score, float)


# ── Span context manager ────────────────────────────────────────────────

class TestSpan:
    def test_span_creates_span(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("test-span") as s:
            assert s.name == "test-span"
            assert s.status == "active"
        assert s.status == "completed"

    def test_span_sets_duration(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("timed") as s:
            pass
        assert s.duration_ms is not None
        assert s.duration_ms >= 0

    def test_span_sends_start_and_end_events(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("test-span"):
            pass
        all_events = []
        for call in mock_transport.send_events.call_args_list:
            for ev in call[0][0]:
                all_events.append(ev)
        span_starts = [e for e in all_events if e.get("event_type") == "span_start"]
        span_ends = [e for e in all_events if e.get("event_type") == "span_end"]
        assert len(span_starts) == 1
        assert len(span_ends) == 1
        assert span_starts[0]["span_name"] == "test-span"
        assert span_ends[0]["span_name"] == "test-span"

    def test_span_nested(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("outer") as outer:
            with tracker.span("inner") as inner:
                assert inner.parent_id == outer.span_id

    def test_span_error_sets_error_status(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError):
            with tracker.span("failing") as s:
                raise ValueError("test error")
        assert s.status == "error"
        assert s.error == "test error"

    def test_span_increments_event_count(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("counting") as s:
            tracker.track(event_type="llm_call")
            tracker.track(event_type="llm_call")
        assert s.event_count == 2

    def test_span_with_attributes(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("attributed", attributes={"key": "value"}) as s:
            pass
        assert s.attributes.get("key") == "value"

    def test_span_set_attribute_during(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("dynamic") as s:
            s.set_attribute("result", 42)
        assert s.attributes.get("result") == 42

    def test_span_children_tracked(self, tracker, mock_transport):
        tracker.start_session()
        with tracker.span("parent") as parent:
            with tracker.span("child1"):
                pass
            with tracker.span("child2"):
                pass
        assert len(parent.children) == 2


# ── Timeline ────────────────────────────────────────────────────────────

class TestTimeline:
    def test_timeline_no_session_raises(self, tracker):
        with pytest.raises(RuntimeError):
            tracker.timeline()

    def test_timeline_returns_renderer(self, tracker, mock_transport):
        from agentlens.timeline import TimelineRenderer
        tracker.start_session()
        tracker.track(event_type="llm_call", tokens_in=50, tokens_out=25, duration_ms=100.0)
        renderer = tracker.timeline()
        assert isinstance(renderer, TimelineRenderer)

    def test_timeline_specific_session(self, tracker, mock_transport):
        from agentlens.timeline import TimelineRenderer
        session = tracker.start_session()
        tracker.track(event_type="llm_call", tokens_in=50)
        renderer = tracker.timeline(session_id=session.session_id)
        assert isinstance(renderer, TimelineRenderer)

    def test_timeline_with_filter_kwargs(self, tracker, mock_transport):
        from agentlens.timeline import TimelineRenderer
        tracker.start_session()
        tracker.track(event_type="llm_call", tokens_in=50, duration_ms=100.0)
        tracker.track(event_type="tool_call", tool_name="search", duration_ms=200.0)
        renderer = tracker.timeline(event_types=["llm_call"])
        assert isinstance(renderer, TimelineRenderer)


# ── Heatmap ─────────────────────────────────────────────────────────────

class TestHeatmap:
    def test_heatmap_calls_backend(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"matrix": [], "peak": None})
        )
        result = tracker.heatmap()
        mock_transport.get.assert_called_with(
            "/analytics/heatmap",
            params={"metric": "events", "days": 30},
        )

    def test_heatmap_custom_metric(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"matrix": []})
        )
        tracker.heatmap(metric="tokens", days=7)
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["metric"] == "tokens"
        assert call_params["days"] == 7

    def test_heatmap_invalid_metric_raises(self, tracker):
        with pytest.raises(ValueError, match="Invalid metric"):
            tracker.heatmap(metric="invalid")

    def test_heatmap_days_clamped(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"matrix": []})
        )
        tracker.heatmap(days=999)
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["days"] == 365

    def test_heatmap_days_min_clamped(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"matrix": []})
        )
        tracker.heatmap(days=0)
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["days"] == 1


# ── Search Sessions ─────────────────────────────────────────────────────

class TestSearchSessions:
    def test_search_sessions_basic(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"sessions": [], "total": 0})
        )
        tracker.search_sessions()
        call_args = mock_transport.get.call_args
        assert call_args[0][0] == "/sessions/search"

    def test_search_sessions_all_filters(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"sessions": [], "total": 0})
        )
        tracker.search_sessions(
            q="test",
            agent="planner",
            status="completed",
            after="2026-01-01T00:00:00Z",
            before="2026-12-31T23:59:59Z",
            min_tokens=100,
            max_tokens=5000,
            tags=["production", "v2"],
            sort="total_tokens",
            order="asc",
            limit=20,
            offset=10,
        )
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["q"] == "test"
        assert call_params["agent"] == "planner"
        assert call_params["status"] == "completed"
        assert call_params["tags"] == "production,v2"
        assert call_params["sort"] == "total_tokens"
        assert call_params["order"] == "asc"
        assert call_params["limit"] == 20
        assert call_params["offset"] == 10

    def test_search_sessions_limit_capped(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"sessions": []})
        )
        tracker.search_sessions(limit=999)
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["limit"] == 200

    def test_search_sessions_zero_tokens_omitted(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"sessions": []})
        )
        tracker.search_sessions(min_tokens=0, max_tokens=0)
        call_params = mock_transport.get.call_args[1]["params"]
        assert "min_tokens" not in call_params
        assert "max_tokens" not in call_params


# ── Alert edge cases ────────────────────────────────────────────────────

class TestAlertEdgeCases:
    def test_acknowledge_alert_calls_put(self, tracker, mock_transport):
        mock_transport.put.return_value = MagicMock(
            json=MagicMock(return_value={"acknowledged": True})
        )
        tracker.acknowledge_alert("alert-123")
        mock_transport.put.assert_called_once_with(
            "/alerts/events/alert-123/acknowledge",
        )

    def test_get_alert_metrics_calls_get(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"metrics": []})
        )
        tracker.get_alert_metrics()
        mock_transport.get.assert_called_once_with("/alerts/metrics")

    def test_get_alert_events_with_filters(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"events": []})
        )
        tracker.get_alert_events(rule_id="r1", acknowledged=False, limit=10)
        call_params = mock_transport.get.call_args[1]["params"]
        assert call_params["rule_id"] == "r1"
        assert call_params["acknowledged"] == "false"
        assert call_params["limit"] == 10


# ── Tag happy paths ─────────────────────────────────────────────────────

class TestTagHappyPaths:
    def test_list_sessions_by_tag_calls_backend(self, tracker, mock_transport):
        mock_transport.get.return_value = MagicMock(
            json=MagicMock(return_value={"sessions": [], "total": 0})
        )
        tracker.list_sessions_by_tag("production", limit=25, offset=5)
        call_args = mock_transport.get.call_args
        assert "/sessions/by-tag/production" in call_args[0][0]
        assert call_args[1]["params"]["limit"] == 25
        assert call_args[1]["params"]["offset"] == 5


# ── Annotation happy paths ──────────────────────────────────────────────

class TestAnnotationHappyPaths:
    def test_update_annotation_sends_payload(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.put.return_value = MagicMock(
            json=MagicMock(return_value={"annotation_id": "a1", "text": "updated"})
        )
        tracker.update_annotation("a1", text="updated", annotation_type="bug")
        call_args = mock_transport.put.call_args
        assert "a1" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["text"] == "updated"
        assert payload["type"] == "bug"

    def test_delete_annotation_calls_delete(self, tracker, mock_transport):
        tracker.start_session()
        mock_transport.delete.return_value = MagicMock(
            json=MagicMock(return_value={"deleted": True, "annotation_id": "a1"})
        )
        tracker.delete_annotation("a1")
        call_args = mock_transport.delete.call_args
        assert "a1" in call_args[0][0]

    def test_update_annotation_empty_id_raises(self, tracker, mock_transport):
        tracker.start_session()
        with pytest.raises(ValueError, match="annotation_id"):
            tracker.update_annotation("", text="test")
