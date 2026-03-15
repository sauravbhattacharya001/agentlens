"""Additional tests for agentlens.transport — concurrency, retry logic, edge cases."""

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from agentlens.transport import Transport, _MAX_BUFFER_SIZE


class TestSendEventsEdgeCases:
    def test_single_event_buffers(self):
        t = Transport(endpoint="http://test:3000", batch_size=10)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}])
                mock_send.assert_not_called()
                assert t._buffer == [{"type": "a"}]
        finally:
            t._running = False

    def test_exact_batch_size_flushes(self):
        t = Transport(endpoint="http://test:3000", batch_size=3)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}, {"type": "b"}, {"type": "c"}])
                mock_send.assert_called_once()
                assert len(mock_send.call_args[0][0]) == 3
        finally:
            t._running = False

    def test_above_batch_size_flushes(self):
        t = Transport(endpoint="http://test:3000", batch_size=2)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}, {"type": "b"}, {"type": "c"}])
                mock_send.assert_called_once()
                # All 3 events drained since buffer >= batch_size
                assert len(mock_send.call_args[0][0]) == 3
        finally:
            t._running = False

    def test_empty_event_list(self):
        t = Transport(endpoint="http://test:3000", batch_size=10)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([])
                mock_send.assert_not_called()
                assert t._buffer == []
        finally:
            t._running = False

    def test_multiple_send_accumulate(self):
        t = Transport(endpoint="http://test:3000", batch_size=5)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}])
                t.send_events([{"type": "b"}])
                t.send_events([{"type": "c"}])
                mock_send.assert_not_called()
                assert len(t._buffer) == 3
        finally:
            t._running = False

    def test_accumulate_then_flush_at_threshold(self):
        t = Transport(endpoint="http://test:3000", batch_size=3)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}])
                t.send_events([{"type": "b"}])
                mock_send.assert_not_called()
                t.send_events([{"type": "c"}])
                mock_send.assert_called_once()
                assert len(mock_send.call_args[0][0]) == 3
        finally:
            t._running = False

    def test_buffer_cap_drops_oldest(self):
        t = Transport(endpoint="http://test:3000", batch_size=_MAX_BUFFER_SIZE + 100)
        try:
            with patch.object(t, "_send_batch"):
                events = [{"id": i} for i in range(_MAX_BUFFER_SIZE + 10)]
                t.send_events(events)
                assert len(t._buffer) == _MAX_BUFFER_SIZE
                # The first 10 should have been dropped
                assert t._buffer[0]["id"] == 10
        finally:
            t._running = False


class TestSendBatchRetryLogic:
    def test_requeues_on_first_failure(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_resp.text = "Service Unavailable"
            with patch.object(t._client, "post", return_value=mock_resp):
                t._send_batch([{"type": "a"}, {"type": "b"}])
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 2
        finally:
            t._running = False

    def test_requeues_preserve_order_with_new_events(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            # Pre-populate buffer with a new event that arrived during the HTTP call
            t._buffer = [{"type": "new"}]
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Error"
            with patch.object(t._client, "post", return_value=mock_resp):
                t._send_batch([{"type": "old1"}, {"type": "old2"}])
                # Failed events should be prepended before new ones
                assert t._buffer[0] == {"type": "old1"}
                assert t._buffer[1] == {"type": "old2"}
                assert t._buffer[2] == {"type": "new"}
        finally:
            t._running = False

    def test_consecutive_failures_increment(self):
        t = Transport(endpoint="http://test:3000", max_retries=5)
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Error"
            with patch.object(t._client, "post", return_value=mock_resp):
                t._send_batch([{"type": "a"}])
                assert t._consecutive_failures == 1
                t._buffer.clear()
                t._send_batch([{"type": "b"}])
                assert t._consecutive_failures == 2
        finally:
            t._running = False

    def test_success_resets_failure_count(self):
        t = Transport(endpoint="http://test:3000", max_retries=5)
        try:
            t._consecutive_failures = 3
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch.object(t._client, "post", return_value=mock_resp):
                t._send_batch([{"type": "a"}])
                assert t._consecutive_failures == 0
        finally:
            t._running = False

    def test_drops_and_resets_after_max_retries(self):
        t = Transport(endpoint="http://test:3000", max_retries=2)
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Error"
            with patch.object(t._client, "post", return_value=mock_resp):
                t._consecutive_failures = 2
                t._send_batch([{"type": "a"}])
                assert len(t._buffer) == 0  # dropped, not requeued
                assert t._consecutive_failures == 0  # reset
        finally:
            t._running = False

    def test_connect_error_requeues(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            with patch.object(t._client, "post",
                              side_effect=httpx.ConnectError("refused")):
                t._send_batch([{"type": "a"}])
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 1
        finally:
            t._running = False

    def test_timeout_error_requeues(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            with patch.object(t._client, "post",
                              side_effect=httpx.ReadTimeout("timeout")):
                t._send_batch([{"type": "a"}])
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 1
        finally:
            t._running = False

    def test_max_retries_one(self):
        t = Transport(endpoint="http://test:3000", max_retries=1)
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Error"
            with patch.object(t._client, "post", return_value=mock_resp):
                # First failure: requeues (failures becomes 1, <= max_retries=1)
                t._send_batch([{"type": "a"}])
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 1
                t._buffer.clear()
                # Second failure: drops (failures becomes 2, > max_retries=1)
                t._send_batch([{"type": "b"}])
                assert t._consecutive_failures == 0
                assert len(t._buffer) == 0
        finally:
            t._running = False


class TestSendBatchHTTPRequest:
    def test_posts_to_correct_url(self):
        t = Transport(endpoint="http://api.example.com:3000", api_key="secret")
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch.object(t._client, "post", return_value=mock_resp) as mock_post:
                t._send_batch([{"type": "test"}])
                mock_post.assert_called_once()
                url = mock_post.call_args[0][0]
                assert url == "http://api.example.com:3000/events"
        finally:
            t._running = False

    def test_sends_correct_headers(self):
        t = Transport(endpoint="http://test:3000", api_key="my-key")
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch.object(t._client, "post", return_value=mock_resp) as mock_post:
                t._send_batch([{"type": "test"}])
                headers = mock_post.call_args[1]["headers"]
                assert headers["Content-Type"] == "application/json"
                assert headers["X-API-Key"] == "my-key"
        finally:
            t._running = False

    def test_sends_events_in_json_body(self):
        t = Transport(endpoint="http://test:3000")
        try:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch.object(t._client, "post", return_value=mock_resp) as mock_post:
                events = [{"type": "a"}, {"type": "b"}]
                t._send_batch(events)
                body = mock_post.call_args[1]["json"]
                assert body == {"events": [{"type": "a"}, {"type": "b"}]}
        finally:
            t._running = False

    def test_non_200_status_is_failure(self):
        for status in [201, 400, 401, 403, 404, 429, 500, 502, 503]:
            t = Transport(endpoint="http://test:3000", max_retries=3)
            try:
                mock_resp = MagicMock()
                mock_resp.status_code = status
                mock_resp.text = f"Status {status}"
                with patch.object(t._client, "post", return_value=mock_resp):
                    t._send_batch([{"type": "test"}])
                    assert t._consecutive_failures == 1, f"Expected failure for HTTP {status}"
            finally:
                t._running = False


class TestFlushLoop:
    def test_flush_thread_is_daemon(self):
        t = Transport(endpoint="http://test:3000")
        try:
            assert t._flush_thread.daemon is True
        finally:
            t._running = False

    def test_flush_thread_is_alive(self):
        t = Transport(endpoint="http://test:3000")
        try:
            assert t._flush_thread.is_alive()
        finally:
            t._running = False


class TestCloseExtended:
    def test_close_flushes_remaining(self):
        t = Transport(endpoint="http://test:3000", batch_size=100)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(t._client, "post", return_value=mock_resp) as mock_post:
            t.send_events([{"type": "unflushed"}])
            assert len(t._buffer) == 1
            t.close()
            assert mock_post.called
            # Buffer should be empty after close
            assert len(t._buffer) == 0

    def test_close_stops_running(self):
        t = Transport(endpoint="http://test:3000")
        assert not t._stop_event.is_set()
        t.close()
        assert t._stop_event.is_set()
