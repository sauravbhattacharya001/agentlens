"""Data retention and cleanup mixin for AgentTracker."""

from __future__ import annotations

from typing import Any


class RetentionMixin:
    """Mixin providing data retention configuration and purge operations.

    Requires ``self.transport`` (a ``Transport`` instance).
    """

    def get_retention_config(self) -> dict[str, Any]:
        """Get current data retention configuration.

        Returns:
            A dict with ``config`` containing:
            - ``max_age_days``: Sessions older than this are eligible
              for purge (0 = disabled, default 90).
            - ``max_sessions``: Maximum sessions to keep (0 = unlimited).
            - ``exempt_tags``: Tags that protect sessions from purging.
            - ``auto_purge``: Whether automatic cleanup is enabled.

        Example::

            config = tracker.get_retention_config()
            print(f"Max age: {config['config']['max_age_days']} days")
        """
        return self.transport.get("/retention/config").json()

    def set_retention_config(
        self,
        *,
        max_age_days: int | None = None,
        max_sessions: int | None = None,
        exempt_tags: list[str] | None = None,
        auto_purge: bool | None = None,
    ) -> dict[str, Any]:
        """Update data retention configuration.

        Only specified fields are updated; others retain their current
        values. Changes take effect on the next purge (manual or auto).

        Args:
            max_age_days: Sessions older than this (in days) are eligible
                for purge. 0 disables age-based cleanup. Max 3650 (~10 years).
            max_sessions: Maximum sessions to keep. 0 = unlimited.
                When exceeded, oldest sessions are purged first.
            exempt_tags: List of tags that protect sessions from purging.
                Sessions with any exempt tag are never purged, regardless
                of age or count limits.
            auto_purge: Enable or disable automatic cleanup.

        Returns:
            A dict with ``config`` (updated settings) and ``updated`` count.

        Example::

            tracker.set_retention_config(max_age_days=30, exempt_tags=["production", "important"])
        """
        payload: dict[str, Any] = {}
        if max_age_days is not None:
            payload["max_age_days"] = max_age_days
        if max_sessions is not None:
            payload["max_sessions"] = max_sessions
        if exempt_tags is not None:
            payload["exempt_tags"] = exempt_tags
        if auto_purge is not None:
            payload["auto_purge"] = auto_purge

        if not payload:
            raise ValueError("At least one config field must be specified.")

        return self.transport.put("/retention/config", json=payload).json()

    def get_retention_stats(self) -> dict[str, Any]:
        """Get database statistics and retention status.

        Returns a comprehensive overview of database size, session ages,
        and how many sessions are currently eligible for purging under
        the active retention policy.

        Returns:
            A dict containing:
            - ``sessions``: Total session count.
            - ``events``: Total event count.
            - ``avg_events_per_session``: Average events per session.
            - ``oldest_session``: ISO timestamp of oldest session.
            - ``newest_session``: ISO timestamp of newest session.
            - ``age_breakdown``: Session counts by age bucket
              (last_24h, last_7d, last_30d, last_90d, older).
            - ``status_breakdown``: Session counts by status.
            - ``eligible_for_purge``: Number of sessions matching
              current retention policy for deletion.
            - ``config``: Current retention configuration.

        Example::

            stats = tracker.get_retention_stats()
            print(f"{stats['sessions']} sessions, {stats['events']} events")
            print(f"{stats['eligible_for_purge']} eligible for purge")
        """
        return self.transport.get("/retention/stats").json()

    def purge(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Manually purge sessions matching the retention policy.

        Deletes sessions (and their events, tags, annotations) that are
        eligible under the current retention configuration. Sessions
        with exempt tags are always preserved.

        Args:
            dry_run: If True, returns what *would* be purged without
                actually deleting anything. Useful for previewing the
                impact before committing.

        Returns:
            A dict containing:
            - ``dry_run``: Whether this was a dry run.
            - ``purged_sessions`` / ``would_purge_sessions``: Count.
            - ``purged_events`` / ``would_purge_events``: Count.
            - ``details``: Per-session breakdown with session ID, reason,
              and event count.
            - ``message``: Human-readable summary.

        Example::

            # Preview first
            preview = tracker.purge(dry_run=True)
            print(preview["message"])

            # Then actually purge
            result = tracker.purge()
            print(f"Purged {result['purged_sessions']} sessions")
        """
        params = {}
        if dry_run:
            params["dry_run"] = "true"

        return self.transport.post(
            "/retention/purge", params=params, json={},
        ).json()
