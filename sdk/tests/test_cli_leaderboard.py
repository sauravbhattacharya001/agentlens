"""Tests for cli_leaderboard command."""

import json
import types
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock

from agentlens.cli_leaderboard import cmd_leaderboard, _medal, _bar, _fmt_duration, _fmt_cost


class TestHelpers(unittest.TestCase):
    def test_medal(self):
        self.assertEqual(_medal(1), "\U0001f947")
        self.assertEqual(_medal(2), "\U0001f948")
        self.assertEqual(_medal(3), "\U0001f949")
        self.assertEqual(_medal(4), "#4")

    def test_bar(self):
        self.assertEqual(len(_bar(50, 100, 20)), 20)
        self.assertEqual(_bar(0, 100, 10), "\u2591" * 10)
        self.assertEqual(_bar(100, 100, 10), "\u2588" * 10)
        self.assertEqual(_bar(5, 0, 10), " " * 10)

    def test_fmt_duration(self):
        self.assertEqual(_fmt_duration(500), "500ms")
        self.assertEqual(_fmt_duration(2500), "2.5s")
        self.assertEqual(_fmt_duration(90000), "1.5m")

    def test_fmt_cost(self):
        self.assertIn("$", _fmt_cost(0.001))
        self.assertIn("$", _fmt_cost(0.5))
        self.assertIn("$", _fmt_cost(5.0))


class TestCmdLeaderboard(unittest.TestCase):
    def _make_args(self, **kwargs):
        defaults = {
            "endpoint": "http://localhost:3000",
            "api_key": "test",
            "sort": "efficiency",
            "days": 30,
            "limit": 20,
            "min_sessions": 2,
            "order": None,
            "json_output": False,
        }
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def _mock_response(self, data):
        """Build a mock ``httpx.Response``-shaped object."""
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    def _patch_client(self, response_data):
        """Patch ``cli_common.get_client`` to return a mock httpx client.

        ``cmd_leaderboard`` calls ``client.get('/leaderboard', params=...)``,
        so the mock client only needs a ``.get`` that returns the canned
        response.  Returns the patcher so the caller can ``.stop()`` it.
        """
        mock_client = MagicMock()
        mock_client.get.return_value = self._mock_response(response_data)
        return patch(
            "agentlens.cli_leaderboard.get_client",
            return_value=(mock_client, "http://localhost:3000"),
        )

    def test_empty_leaderboard(self):
        data = {
            "period_days": 30, "sort": "efficiency", "order": "desc",
            "min_sessions": 2, "total_qualifying_agents": 0, "agents": [],
        }
        with self._patch_client(data), patch("sys.stdout", new_callable=StringIO) as out:
            cmd_leaderboard(self._make_args())
            self.assertIn("No qualifying agents", out.getvalue())

    def test_json_output(self):
        data = {
            "period_days": 30, "sort": "efficiency", "order": "desc",
            "min_sessions": 2, "total_qualifying_agents": 1,
            "agents": [{
                "rank": 1, "agent_name": "test-agent", "total_sessions": 10,
                "success_rate": 90, "avg_session_duration_ms": 5000,
                "cost_per_session_usd": 0.05, "efficiency_ratio": 2.5,
                "total_cost_usd": 0.5,
            }],
        }
        with self._patch_client(data), patch("sys.stdout", new_callable=StringIO) as out:
            cmd_leaderboard(self._make_args(json_output=True))
            parsed = json.loads(out.getvalue())
            self.assertEqual(len(parsed["agents"]), 1)

    def test_table_output(self):
        data = {
            "period_days": 30, "sort": "reliability", "order": "desc",
            "min_sessions": 2, "total_qualifying_agents": 2,
            "agents": [
                {"rank": 1, "agent_name": "alpha", "total_sessions": 20,
                 "success_rate": 95, "avg_session_duration_ms": 3000,
                 "cost_per_session_usd": 0.02, "efficiency_ratio": 1.5,
                 "total_cost_usd": 0.4},
                {"rank": 2, "agent_name": "beta", "total_sessions": 15,
                 "success_rate": 80, "avg_session_duration_ms": 8000,
                 "cost_per_session_usd": 0.1, "efficiency_ratio": 0.8,
                 "total_cost_usd": 1.5},
            ],
        }
        with self._patch_client(data), patch("sys.stdout", new_callable=StringIO) as out:
            cmd_leaderboard(self._make_args(sort="reliability"))
            output = out.getvalue()
            self.assertIn("Leaderboard", output)
            self.assertIn("alpha", output)
            self.assertIn("beta", output)
            self.assertIn("95%", output)


if __name__ == "__main__":
    unittest.main()
