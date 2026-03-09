"""Alert management mixin for AgentTracker."""

from __future__ import annotations

from typing import Any


class AlertMixin:
    """Mixin providing alert rule CRUD and evaluation methods.

    Requires ``self.transport`` (a ``Transport`` instance).
    """

    def list_alert_rules(self, enabled: bool | None = None) -> dict:
        """List all alert rules, optionally filtered by enabled status."""
        params = {}
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        return self.transport.get("/alerts/rules", params=params).json()

    def create_alert_rule(
        self,
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        window_minutes: int = 60,
        agent_filter: str | None = None,
        cooldown_minutes: int = 15,
    ) -> dict:
        """Create a new alert rule.

        Args:
            name: Human-readable rule name
            metric: Metric to monitor (total_tokens, error_rate, avg_duration_ms, etc.)
            operator: Comparison operator (<, >, <=, >=, ==, !=)
            threshold: Threshold value to compare against
            window_minutes: Time window to evaluate (default 60)
            agent_filter: Optional agent name filter
            cooldown_minutes: Min minutes between alerts for same rule (default 15)
        """
        payload = {
            "name": name,
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "window_minutes": window_minutes,
            "cooldown_minutes": cooldown_minutes,
        }
        if agent_filter:
            payload["agent_filter"] = agent_filter
        return self.transport.post("/alerts/rules", json=payload).json()

    def update_alert_rule(self, rule_id: str, **kwargs) -> dict:
        """Update an existing alert rule. Pass any field to update."""
        return self.transport.put(
            f"/alerts/rules/{rule_id}", json=kwargs,
        ).json()

    def delete_alert_rule(self, rule_id: str) -> dict:
        """Delete an alert rule."""
        return self.transport.delete(f"/alerts/rules/{rule_id}").json()

    def evaluate_alerts(self) -> dict:
        """Evaluate all enabled alert rules against current data."""
        return self.transport.post("/alerts/evaluate").json()

    def get_alert_events(
        self,
        rule_id: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 50,
    ) -> dict:
        """Get triggered alert events."""
        params: dict[str, Any] = {"limit": limit}
        if rule_id:
            params["rule_id"] = rule_id
        if acknowledged is not None:
            params["acknowledged"] = "true" if acknowledged else "false"
        return self.transport.get("/alerts/events", params=params).json()

    def acknowledge_alert(self, alert_id: str) -> dict:
        """Acknowledge a triggered alert event."""
        return self.transport.put(
            f"/alerts/events/{alert_id}/acknowledge",
        ).json()

    def get_alert_metrics(self) -> dict:
        """Get list of available metrics for alert rules."""
        return self.transport.get("/alerts/metrics").json()
