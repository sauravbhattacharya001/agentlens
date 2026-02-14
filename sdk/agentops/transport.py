"""HTTP transport for sending events to the AgentOps backend."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger("agentops.transport")


class Transport:
    """Batched HTTP transport for sending events to the AgentOps API."""

    def __init__(
        self,
        endpoint: str = "http://localhost:3000",
        api_key: str = "default",
        batch_size: int = 10,
        flush_interval: float = 5.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._buffer: list[dict[str, Any]] = []
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
            if len(self._buffer) >= self.batch_size:
                self._do_flush()

    def flush(self) -> None:
        """Force-flush all buffered events."""
        with self._lock:
            self._do_flush()

    def _do_flush(self) -> None:
        """Internal flush — must be called with self._lock held."""
        if not self._buffer:
            return

        events = self._buffer.copy()
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
            if response.status_code != 200:
                logger.warning(
                    "Failed to send events: HTTP %d — %s",
                    response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as e:
            logger.warning("Failed to send events: %s", e)
            # Put events back in buffer for retry
            self._buffer = events + self._buffer

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
