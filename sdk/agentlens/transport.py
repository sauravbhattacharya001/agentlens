"""HTTP transport for sending events to the AgentLens backend."""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("agentlens.transport")

_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _is_plaintext_remote(endpoint: str) -> bool:
    """Return True if *endpoint* sends traffic over plaintext HTTP to a
    non-localhost host, which would expose the API key on the network."""
    parsed = urlparse(endpoint)
    if parsed.scheme == "https":
        return False
    host = (parsed.hostname or "").lower()
    return host not in _LOCALHOST_HOSTS

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
        self._api_key = api_key

        # Warn when API credentials would be sent in cleartext over the
        # network.  Localhost is exempt (dev/testing), but any remote
        # endpoint should use HTTPS to protect the API key in transit.
        if api_key != "default" and _is_plaintext_remote(self.endpoint):
            warnings.warn(
                f"AgentLens API key is being sent over plaintext HTTP to "
                f"{self.endpoint}. This exposes your credentials on the "
                f"network. Use HTTPS for non-localhost endpoints.",
                stacklevel=2,
            )
            logger.warning(
                "API key sent over plaintext HTTP to %s — use HTTPS",
                self.endpoint,
            )

        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries

        self._buffer: list[dict[str, Any]] = []
        self._pending_batch: list[dict[str, Any]] | None = None
        self._consecutive_failures: int = 0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=10.0)

        # Start background flush thread
        self._stop_event = threading.Event()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    @property
    def api_key(self) -> str:
        """Return the API key.  Kept as a property so the public name stays
        the same while the underlying attribute is private (``_api_key``),
        preventing accidental exposure via ``__dict__`` or ``vars()``."""
        return self._api_key

    def __repr__(self) -> str:
        masked = (
            self._api_key[:4] + "****"
            if len(self._api_key) > 4
            else "****"
        )
        return (
            f"Transport(endpoint={self.endpoint!r}, api_key={masked!r}, "
            f"batch_size={self.batch_size}, buffer={len(self._buffer)})"
        )

    def _buffer_and_maybe_flush(self) -> None:
        """Check buffer limits and flush if ready.  Must be called with lock held.

        Drops oldest events when the buffer exceeds ``_MAX_BUFFER_SIZE``
        and returns a batch to send if ``batch_size`` is reached.  The
        caller must call ``_send_batch`` on the returned batch (if any)
        **after** releasing the lock.

        This method mutates ``self._pending_batch`` — an internal slot
        used to pass the batch out of the locked section without returning
        it (keeping the call ergonomic for callers that hold the lock in
        a ``with`` block).
        """
        if len(self._buffer) > _MAX_BUFFER_SIZE:
            dropped = len(self._buffer) - _MAX_BUFFER_SIZE
            self._buffer = self._buffer[dropped:]
            logger.warning(
                "Event buffer exceeded %d entries; dropped %d oldest events",
                _MAX_BUFFER_SIZE,
                dropped,
            )
        if len(self._buffer) >= self.batch_size:
            self._pending_batch = self._drain_buffer()
        else:
            self._pending_batch = None

    def send_event(self, event: dict[str, Any]) -> None:
        """Add a single event to the buffer. Flushes when batch_size is reached.

        This is more efficient than ``send_events([event])`` as it avoids
        creating and unpacking a single-element list.
        """
        with self._lock:
            self._buffer.append(event)
            self._buffer_and_maybe_flush()
            batch_to_send = self._pending_batch

        if batch_to_send is not None:
            self._send_batch(batch_to_send)

    def send_events(self, events: list[dict[str, Any]]) -> None:
        """Add events to the buffer. Flushes when batch_size is reached."""
        if not events:
            return
        # Fast path for single-event lists (common case from tracker)
        if len(events) == 1:
            self.send_event(events[0])
            return
        with self._lock:
            self._buffer.extend(events)
            self._buffer_and_maybe_flush()
            batch_to_send = self._pending_batch

        if batch_to_send is not None:
            self._send_batch(batch_to_send)

    def flush(self) -> None:
        """Force-flush all buffered events."""
        with self._lock:
            batch = self._drain_buffer()
        if batch:
            self._send_batch(batch)

    def _drain_buffer(self) -> list[dict[str, Any]]:
        """Drain and return buffer contents.  Must be called with lock held.

        Uses reference swap instead of copy+clear for O(1) drain regardless
        of buffer size.
        """
        events = self._buffer
        self._buffer = []
        return events

    def _send_batch(self, events: list[dict[str, Any]]) -> None:
        """Send a batch of events to the backend.

        The HTTP call runs **outside** the buffer lock so that
        ``send_events`` and the background flush thread do not block
        each other during network I/O.

        On failure the events are re-queued into the buffer (preserving
        any events that arrived during the HTTP call) and a consecutive
        failure counter is incremented.  After ``max_retries`` consecutive
        failures the events are dropped to prevent infinite retry loops.
        The counter resets on any successful flush.
        """
        if not events:
            return

        try:
            response = self._client.post(
                f"{self.endpoint}/events",
                json={"events": events},
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key,
                },
            )
            if 200 <= response.status_code < 300:
                # Success — reset consecutive failure counter
                with self._lock:
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
        with self._lock:
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

    # ── Convenience HTTP methods ───────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Return headers with API key authentication."""
        return {"X-API-Key": self.api_key}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an authenticated HTTP request and raise on error.

        All keyword arguments are forwarded to ``httpx.Client.request``.
        The ``X-API-Key`` header is injected automatically and merged with
        any caller-supplied headers.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            path: URL path appended to the configured endpoint.

        Returns:
            The ``httpx.Response`` after raising for non-2xx status codes.
        """
        headers = {**self._auth_headers(), **kwargs.pop("headers", {})}
        response = self._client.request(
            method, f"{self.endpoint}{path}", headers=headers, **kwargs,
        )
        response.raise_for_status()
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated GET request to *endpoint/path*."""
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated POST request to *endpoint/path*."""
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated PUT request to *endpoint/path*."""
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated DELETE request to *endpoint/path*."""
        return self._request("DELETE", path, **kwargs)

    def _flush_loop(self) -> None:
        """Background thread that periodically flushes the buffer."""
        while not self._stop_event.wait(timeout=self.flush_interval):
            self.flush()

    def close(self) -> None:
        """Flush remaining events and stop the background thread."""
        self._stop_event.set()
        self.flush()
        self._flush_thread.join(timeout=10.0)
        if self._flush_thread.is_alive():
            logger.warning("Flush thread did not exit within timeout")
        self._client.close()
