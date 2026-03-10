"""Tests for tracker mixin modules: annotations, retention, alerts."""

import pytest
from unittest.mock import MagicMock

from agentlens.tracker import AgentTracker
from agentlens.transport import Transport


@pytest.fixture
def tracker():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    t = AgentTracker(transport=transport)
    t._current_session_id = "sess-001"
    return t


@pytest.fixture
def tracker_no_session():
    transport = MagicMock(spec=Transport)
    transport.endpoint = "http://localhost:3000"
    transport.api_key = "test-key"
    transport._client = MagicMock()
    return AgentTracker(transport=transport)


# ── AnnotationMixin ──────────────────────────────────────


class TestAnnotate:
    def test_annotate_default_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "annotation_id": "ann-1",
            "session_id": "sess-001",
            "text": "Bug spotted",
            "author": "sdk",
            "type": "note",
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.annotate("Bug spotted")
        assert result["annotation_id"] == "ann-1"
        assert result["text"] == "Bug spotted"
        call_args = tracker.transport.post.call_args
        assert "/sessions/sess-001/annotations" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["text"] == "Bug spotted"
        assert payload["author"] == "sdk"
        assert payload["type"] == "note"

    def test_annotate_custom_type_and_author(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-2"}
        tracker.transport.post.return_value = mock_resp

        tracker.annotate(
            "Latency spike",
            annotation_type="warning",
            author="ci-pipeline",
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["type"] == "warning"
        assert payload["author"] == "ci-pipeline"

    def test_annotate_with_event_id(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-3"}
        tracker.transport.post.return_value = mock_resp

        tracker.annotate("Step failure", event_id="evt-42")
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["event_id"] == "evt-42"

    def test_annotate_specific_session(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-4"}
        tracker.transport.post.return_value = mock_resp

        tracker.annotate("Note", session_id="sess-other")
        url = tracker.transport.post.call_args[0][0]
        assert "sess-other" in url

    def test_annotate_empty_text_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty"):
            tracker.annotate("")

    def test_annotate_whitespace_text_raises(self, tracker):
        with pytest.raises(ValueError, match="non-empty"):
            tracker.annotate("   ")

    def test_annotate_none_text_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.annotate(None)

    def test_annotate_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError):
            tracker_no_session.annotate("hello")


class TestGetAnnotations:
    def test_get_annotations_default(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session_id": "sess-001",
            "total": 3,
            "returned": 3,
            "annotations": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_annotations()
        assert result["total"] == 3
        assert len(result["annotations"]) == 3
        url = tracker.transport.get.call_args[0][0]
        assert "/sessions/sess-001/annotations" in url

    def test_get_annotations_with_filters(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 1, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(annotation_type="bug", author="dev")
        params = tracker.transport.get.call_args[1]["params"]
        assert params["type"] == "bug"
        assert params["author"] == "dev"

    def test_get_annotations_clamps_limit(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(limit=9999)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 500

    def test_get_annotations_clamps_min_limit(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_annotations(limit=-5)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 1

    def test_get_annotations_no_session_raises(self, tracker_no_session):
        with pytest.raises(RuntimeError):
            tracker_no_session.get_annotations()


class TestUpdateAnnotation:
    def test_update_text(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-1", "text": "updated"}
        tracker.transport.put.return_value = mock_resp

        result = tracker.update_annotation("ann-1", text="updated")
        assert result["text"] == "updated"
        url = tracker.transport.put.call_args[0][0]
        assert "/annotations/ann-1" in url

    def test_update_type_and_author(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"annotation_id": "ann-1"}
        tracker.transport.put.return_value = mock_resp

        tracker.update_annotation("ann-1", annotation_type="bug", author="qa")
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["type"] == "bug"
        assert payload["author"] == "qa"

    def test_update_no_fields_raises(self, tracker):
        with pytest.raises(ValueError, match="At least one"):
            tracker.update_annotation("ann-1")

    def test_update_empty_id_raises(self, tracker):
        with pytest.raises(ValueError, match="annotation_id"):
            tracker.update_annotation("", text="x")


class TestDeleteAnnotation:
    def test_delete_annotation(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": True, "annotation_id": "ann-1"}
        tracker.transport.delete.return_value = mock_resp

        result = tracker.delete_annotation("ann-1")
        assert result["deleted"] is True
        url = tracker.transport.delete.call_args[0][0]
        assert "/annotations/ann-1" in url

    def test_delete_empty_id_raises(self, tracker):
        with pytest.raises(ValueError, match="annotation_id"):
            tracker.delete_annotation("")


class TestListRecentAnnotations:
    def test_list_recent_default(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 2, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_recent_annotations()
        assert result["total"] == 2
        url = tracker.transport.get.call_args[0][0]
        assert "/annotations" in url

    def test_list_recent_with_type_filter(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_recent_annotations(annotation_type="milestone")
        params = tracker.transport.get.call_args[1]["params"]
        assert params["type"] == "milestone"

    def test_list_recent_clamps_limit(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "annotations": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_recent_annotations(limit=999)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["limit"] == 200


# ── RetentionMixin ──────────────────────────────────────


class TestGetRetentionConfig:
    def test_get_config(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "config": {
                "max_age_days": 90,
                "max_sessions": 0,
                "exempt_tags": [],
                "auto_purge": False,
            }
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_retention_config()
        assert result["config"]["max_age_days"] == 90
        url = tracker.transport.get.call_args[0][0]
        assert "/retention/config" in url


class TestSetRetentionConfig:
    def test_set_max_age(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"max_age_days": 30}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        result = tracker.set_retention_config(max_age_days=30)
        assert result["config"]["max_age_days"] == 30
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["max_age_days"] == 30

    def test_set_exempt_tags(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"exempt_tags": ["prod"]}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(exempt_tags=["prod"])
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["exempt_tags"] == ["prod"]

    def test_set_auto_purge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {"auto_purge": True}, "updated": 1}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(auto_purge=True)
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["auto_purge"] is True

    def test_set_multiple_fields(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"config": {}, "updated": 3}
        tracker.transport.put.return_value = mock_resp

        tracker.set_retention_config(
            max_age_days=7, max_sessions=1000, auto_purge=True,
        )
        payload = tracker.transport.put.call_args[1]["json"]
        assert payload["max_age_days"] == 7
        assert payload["max_sessions"] == 1000
        assert payload["auto_purge"] is True

    def test_set_no_fields_raises(self, tracker):
        with pytest.raises(ValueError, match="At least one"):
            tracker.set_retention_config()


class TestGetRetentionStats:
    def test_get_stats(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "sessions": 150,
            "events": 4500,
            "eligible_for_purge": 12,
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_retention_stats()
        assert result["sessions"] == 150
        assert result["eligible_for_purge"] == 12
        url = tracker.transport.get.call_args[0][0]
        assert "/retention/stats" in url


class TestPurge:
    def test_purge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "dry_run": False,
            "purged_sessions": 5,
            "purged_events": 120,
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.purge()
        assert result["purged_sessions"] == 5
        assert result["dry_run"] is False
        call_args = tracker.transport.post.call_args
        assert "/retention/purge" in call_args[0][0]

    def test_purge_dry_run(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "dry_run": True,
            "would_purge_sessions": 5,
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.purge(dry_run=True)
        assert result["dry_run"] is True
        params = tracker.transport.post.call_args[1].get("params", {})
        assert params.get("dry_run") == "true"


# ── AlertMixin ──────────────────────────────────────────


class TestListAlertRules:
    def test_list_all(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": [{"name": "high-tokens"}]}
        tracker.transport.get.return_value = mock_resp

        result = tracker.list_alert_rules()
        assert len(result["rules"]) == 1
        url = tracker.transport.get.call_args[0][0]
        assert "/alerts/rules" in url

    def test_list_enabled_only(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_alert_rules(enabled=True)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["enabled"] == "true"

    def test_list_disabled_only(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rules": []}
        tracker.transport.get.return_value = mock_resp

        tracker.list_alert_rules(enabled=False)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["enabled"] == "false"


class TestCreateAlertRule:
    def test_create_basic(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "rule_id": "rule-1",
            "name": "High tokens",
            "metric": "total_tokens",
            "operator": ">",
            "threshold": 10000,
        }
        tracker.transport.post.return_value = mock_resp

        result = tracker.create_alert_rule(
            name="High tokens",
            metric="total_tokens",
            operator=">",
            threshold=10000,
        )
        assert result["rule_id"] == "rule-1"
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["name"] == "High tokens"
        assert payload["metric"] == "total_tokens"
        assert payload["operator"] == ">"
        assert payload["threshold"] == 10000
        assert payload["window_minutes"] == 60
        assert payload["cooldown_minutes"] == 15

    def test_create_with_agent_filter(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule_id": "rule-2"}
        tracker.transport.post.return_value = mock_resp

        tracker.create_alert_rule(
            name="r", metric="error_rate", operator=">=",
            threshold=0.5, agent_filter="planner",
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["agent_filter"] == "planner"

    def test_create_custom_window_and_cooldown(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule_id": "rule-3"}
        tracker.transport.post.return_value = mock_resp

        tracker.create_alert_rule(
            name="r", metric="avg_duration_ms", operator=">",
            threshold=5000, window_minutes=15, cooldown_minutes=60,
        )
        payload = tracker.transport.post.call_args[1]["json"]
        assert payload["window_minutes"] == 15
        assert payload["cooldown_minutes"] == 60


class TestUpdateAlertRule:
    def test_update(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rule_id": "rule-1", "threshold": 20000}
        tracker.transport.put.return_value = mock_resp

        result = tracker.update_alert_rule("rule-1", threshold=20000)
        assert result["threshold"] == 20000
        url = tracker.transport.put.call_args[0][0]
        assert "/alerts/rules/rule-1" in url


class TestDeleteAlertRule:
    def test_delete(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"deleted": True}
        tracker.transport.delete.return_value = mock_resp

        result = tracker.delete_alert_rule("rule-1")
        assert result["deleted"] is True
        url = tracker.transport.delete.call_args[0][0]
        assert "/alerts/rules/rule-1" in url


class TestEvaluateAlerts:
    def test_evaluate(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"evaluated": 5, "triggered": 1}
        tracker.transport.post.return_value = mock_resp

        result = tracker.evaluate_alerts()
        assert result["evaluated"] == 5
        assert result["triggered"] == 1


class TestGetAlertEvents:
    def test_get_all(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [{"alert_id": "a1"}]}
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_alert_events()
        assert len(result["events"]) == 1

    def test_filter_by_rule_and_acknowledged(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": []}
        tracker.transport.get.return_value = mock_resp

        tracker.get_alert_events(rule_id="rule-1", acknowledged=False)
        params = tracker.transport.get.call_args[1]["params"]
        assert params["rule_id"] == "rule-1"
        assert params["acknowledged"] == "false"


class TestAcknowledgeAlert:
    def test_acknowledge(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"acknowledged": True}
        tracker.transport.put.return_value = mock_resp

        result = tracker.acknowledge_alert("alert-1")
        assert result["acknowledged"] is True
        url = tracker.transport.put.call_args[0][0]
        assert "/alerts/events/alert-1/acknowledge" in url


class TestGetAlertMetrics:
    def test_get_metrics(self, tracker):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "metrics": ["total_tokens", "error_rate", "avg_duration_ms"],
        }
        tracker.transport.get.return_value = mock_resp

        result = tracker.get_alert_metrics()
        assert "total_tokens" in result["metrics"]
