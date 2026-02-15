"""Tests for agentlens.transport — batched HTTP transport with retry logic."""

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentlens.transport import Transport, _MAX_BUFFER_SIZE


class TestTransportInit:
    def test_defaults(self):
        t = Transport(endpoint="http://test:3000", api_key="key")
        try:
            assert t.endpoint == "http://test:3000"
            assert t.api_key == "key"
            assert t.batch_size == 10
            assert t.flush_interval == 5.0
            assert t.max_retries == 3
        finally:
            t.close()

    def test_strips_trailing_slash(self):
        t = Transport(endpoint="http://test:3000/")
        try:
            assert t.endpoint == "http://test:3000"
        finally:
            t.close()


class TestSendEvents:
    def test_buffers_below_batch_size(self):
        t = Transport(endpoint="http://test:3000", batch_size=5)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "test"}])
                mock_send.assert_not_called()
                assert len(t._buffer) == 1
        finally:
            t._running = False

    def test_flushes_at_batch_size(self):
        t = Transport(endpoint="http://test:3000", batch_size=2)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.send_events([{"type": "a"}, {"type": "b"}])
                mock_send.assert_called_once()
                assert len(t._buffer) == 0
        finally:
            t._running = False

    def test_caps_buffer_at_max(self):
        t = Transport(endpoint="http://test:3000", batch_size=_MAX_BUFFER_SIZE + 100)
        try:
            with patch.object(t, "_send_batch"):
                # Add more than max buffer
                events = [{"type": f"e{i}"} for i in range(_MAX_BUFFER_SIZE + 50)]
                t.send_events(events)
                assert len(t._buffer) == _MAX_BUFFER_SIZE
        finally:
            t._running = False


class TestFlush:
    def test_flush_sends_all_buffered(self):
        t = Transport(endpoint="http://test:3000", batch_size=100)
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t._buffer = [{"type": "a"}, {"type": "b"}]
                t.flush()
                mock_send.assert_called_once_with([{"type": "a"}, {"type": "b"}])
                assert len(t._buffer) == 0
        finally:
            t._running = False

    def test_flush_empty_buffer_noop(self):
        t = Transport(endpoint="http://test:3000")
        try:
            with patch.object(t, "_send_batch") as mock_send:
                t.flush()
                mock_send.assert_not_called()
        finally:
            t._running = False


class TestSendBatch:
    def test_successful_send_resets_failures(self):
        t = Transport(endpoint="http://test:3000")
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            with patch.object(t._client, "post", return_value=mock_response):
                t._consecutive_failures = 2
                t._send_batch([{"type": "test"}])
                assert t._consecutive_failures == 0
        finally:
            t._running = False

    def test_failed_send_requeues_events(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            with patch.object(t._client, "post", return_value=mock_response):
                events = [{"type": "test"}]
                t._send_batch(events)
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 1  # requeued
        finally:
            t._running = False

    def test_drops_after_max_retries(self):
        t = Transport(endpoint="http://test:3000", max_retries=2)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Error"
            with patch.object(t._client, "post", return_value=mock_response):
                t._consecutive_failures = 2  # already at max
                t._send_batch([{"type": "test"}])
                # Should drop, not requeue
                assert len(t._buffer) == 0
                assert t._consecutive_failures == 0  # reset after drop
        finally:
            t._running = False

    def test_http_error_triggers_retry(self):
        t = Transport(endpoint="http://test:3000", max_retries=3)
        try:
            with patch.object(
                t._client, "post", side_effect=httpx.ConnectError("Connection refused")
            ):
                t._send_batch([{"type": "test"}])
                assert t._consecutive_failures == 1
                assert len(t._buffer) == 1
        finally:
            t._running = False

    def test_empty_batch_noop(self):
        t = Transport(endpoint="http://test:3000")
        try:
            with patch.object(t._client, "post") as mock_post:
                t._send_batch([])
                mock_post.assert_not_called()
        finally:
            t._running = False


class TestClose:
    def test_close_stops_thread_and_flushes(self):
        t = Transport(endpoint="http://test:3000")
        with patch.object(t, "flush") as mock_flush:
            t.close()
            assert t._running is False
            mock_flush.assert_called_once()
