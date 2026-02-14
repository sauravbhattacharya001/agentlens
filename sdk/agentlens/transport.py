"""HTTP transport for sending events to the AgentLens backend."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger("agentlens.transport")

# Hard cap to prevent unbounded memory growth if the backend is down
_MAX_BUFFER_SIZE = 5000


class Transport:
    """Batched HTTP transport for sending events to the AgentLens API.

    Events are buffered in memory and flushed either when *batch_size* events
    accumulate or every *flush_interval* seconds (whichever comes first).

    Failed flushes are retried up to *max_retries* times.  After that the
    events are dropped and a warning is logged.  The internal buffer is also
    capped at ``_MAX_BUFFER_SIZE`` to prevent unbounded memory growth when the
    backend is unreachable for an extended period.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:3000",
        api_key: str = "default",
        batch_size: int = 10,
        flush_interval: float = 5.0,
        max_retries: int = 3,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries

        self._buffer: list[dict[str, Any]] = []
        self._consecutive_failures: int = 0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=10.0)

        # Start background flush thread
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def send_events(self, events: list[dict[str, Any]]) -> None:
        """Add events to the buffer. Flushes when batch_size is reached."""
        with self._lock:
            self._buffer.extend(events)
            # Drop oldest events if the buffer grows beyond the hard cap
            if len(self._buffer) > _MAX_BUFFER_SIZE:
                dropped = len(self._buffer) - _MAX_BUFFER_SIZE
                self._buffer = self._buffer[dropped:]
                logger.warning(
                    "Event buffer exceeded %d entries; dropped %d oldest events",
                    _MAX_BUFFER_SIZE,
                    dropped,
                )
            if len(self._buffer) >= self.batch_size:
                self._do_flush()

    def flush(self) -> None:
        """Force-flush all buffered events."""
        with self._lock:
            self._do_flush()

    def _do_flush(self) -> None:
        """Internal flush — must be called with ``self._lock`` held.

        On failure the events are *prepended* back into the buffer (preserving
        any events that arrived during the HTTP call) and a consecutive failure
        counter is incremented.  After ``max_retries`` consecutive failures the
        events are dropped to prevent infinite retry loops.  The counter resets
        on any successful flush.
        """
        if not self._buffer:
            return

        events = self._buffer[:]
        self._buffer.clear()

        try:
            response = self._client.post(
                f"{self.endpoint}/events",
                json={"events": events},
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key,
                },
            )
            if response.status_code == 200:
                # Success — reset consecutive failure counter
                self._consecutive_failures = 0
                return

            logger.warning(
                "Failed to send %d events: HTTP %d — %s",
                len(events),
                response.status_code,
                response.text[:200],
            )
        except httpx.HTTPError as e:
            logger.warning("Failed to send %d events: %s", len(events), e)

        # --- Retry logic ---
        self._consecutive_failures += 1

        if self._consecutive_failures <= self.max_retries:
            # Prepend failed events *before* anything new that arrived
            self._buffer[0:0] = events
            logger.info(
                "Queued %d events for retry (attempt %d/%d)",
                len(events),
                self._consecutive_failures,
                self.max_retries,
            )
        else:
            logger.error(
                "Dropping %d events after %d consecutive failures",
                len(events),
                self._consecutive_failures,
            )
            self._consecutive_failures = 0

    def _flush_loop(self) -> None:
        """Background thread that periodically flushes the buffer."""
        while self._running:
            time.sleep(self.flush_interval)
            self.flush()

    def close(self) -> None:
        """Flush remaining events and stop the background thread."""
        self._running = False
        self.flush()
        self._client.close()
