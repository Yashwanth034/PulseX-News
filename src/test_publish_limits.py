import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import src.production_controller as production_controller
from src.x_publisher import XPublisher


class PublisherLimitTests(unittest.TestCase):
    def _publisher(self):
        with patch.dict(os.environ, {}, clear=True):
            publisher = XPublisher()
        self.assertEqual(publisher.daily_limit, 48)
        self.assertEqual(publisher.half_hour_limit, 1)
        self.assertEqual(publisher.hourly_limit, 2)
        return publisher

    def _state(self, ages_minutes, daily_count=0):
        now = datetime.now(timezone.utc)
        return {
            "live_enabled": True,
            "kill_switch": False,
            "daily_post_count": daily_count,
            "last_reset_date": now.date().isoformat(),
            "recent_post_times": [
                (now - timedelta(minutes=age)).isoformat()
                for age in ages_minutes
            ],
        }

    def test_default_limits_are_48_1_2(self):
        self._publisher()

    def test_recent_post_blocks_30_minute_window(self):
        publisher = self._publisher()
        self.assertEqual(
            publisher._available_capacity(self._state([10])),
            0,
        )

    def test_one_older_post_allows_next_half_hour_slot(self):
        publisher = self._publisher()
        self.assertEqual(
            publisher._available_capacity(self._state([40])),
            1,
        )

    def test_two_posts_inside_hour_block_hourly_window(self):
        publisher = self._publisher()
        self.assertEqual(
            publisher._available_capacity(self._state([35, 50])),
            0,
        )

    def test_posts_older_than_one_hour_are_pruned(self):
        publisher = self._publisher()
        state = publisher._prepare_state(self._state([61]))
        self.assertEqual(state["recent_post_times"], [])
        self.assertEqual(publisher._available_capacity(state), 1)

    def test_daily_limit_blocks_at_48(self):
        publisher = self._publisher()
        self.assertEqual(
            publisher._available_capacity(self._state([], daily_count=48)),
            0,
        )

    def test_daily_reset_keeps_rolling_window(self):
        publisher = self._publisher()
        state = self._state([10], daily_count=12)
        state["last_reset_date"] = "2000-01-01"
        prepared = publisher._prepare_state(state)
        self.assertEqual(prepared["daily_post_count"], 0)
        self.assertEqual(len(prepared["recent_post_times"]), 1)
        self.assertEqual(publisher._available_capacity(prepared), 0)


class ControllerLimitTests(unittest.TestCase):
    def _run_controller(self, ages_minutes, daily_count=0, stale_reset=False):
        now = datetime.now(timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health = root / "health.json"
            queue = root / "queue.json"
            state = root / "production_state.json"

            health.write_text(json.dumps({"status": "GREEN"}))
            queue.write_text(json.dumps({"count": 5, "stories": [{"id": "a"}]}))
            state.write_text(
                json.dumps(
                    {
                        "live_enabled": True,
                        "kill_switch": False,
                        "daily_post_count": daily_count,
                        "last_reset_date": (
                            "2000-01-01"
                            if stale_reset
                            else now.date().isoformat()
                        ),
                        "recent_post_times": [
                            (now - timedelta(minutes=age)).isoformat()
                            for age in ages_minutes
                        ],
                    }
                )
            )

            env = {
                "X_PUBLISH_ENABLED": "true",
                "X_KILL_SWITCH": "false",
            }

            with (
                patch.object(production_controller, "HEALTH", health),
                patch.object(production_controller, "QUEUE", queue),
                patch.object(production_controller, "STATE", state),
                patch.dict(os.environ, env, clear=True),
                redirect_stdout(io.StringIO()),
            ):
                return production_controller.controller()

    def test_controller_allows_one_slot_after_30_minutes(self):
        result = self._run_controller([40])
        self.assertEqual(result["daily_limit"], 48)
        self.assertEqual(result["half_hour_limit"], 1)
        self.assertEqual(result["hourly_limit"], 2)
        self.assertEqual(result["half_hour_post_count"], 0)
        self.assertEqual(result["hourly_post_count"], 1)
        self.assertEqual(result["publish_capacity"], 1)
        self.assertTrue(result["allowed"])

    def test_controller_blocks_second_post_inside_30_minutes(self):
        result = self._run_controller([10])
        self.assertEqual(result["publish_capacity"], 0)
        self.assertIn("30-minute post limit reached", result["reasons"])
        self.assertFalse(result["allowed"])

    def test_controller_blocks_third_post_inside_one_hour(self):
        result = self._run_controller([35, 50])
        self.assertEqual(result["half_hour_post_count"], 0)
        self.assertEqual(result["hourly_post_count"], 2)
        self.assertEqual(result["publish_capacity"], 0)
        self.assertIn("1-hour post limit reached", result["reasons"])
        self.assertFalse(result["allowed"])

    def test_controller_blocks_after_48_posts_in_day(self):
        result = self._run_controller([], daily_count=48)
        self.assertEqual(result["publish_capacity"], 0)
        self.assertIn("daily post limit reached", result["reasons"])
        self.assertFalse(result["allowed"])

    def test_controller_daily_reset_keeps_rolling_window(self):
        result = self._run_controller(
            [10],
            daily_count=12,
            stale_reset=True,
        )
        self.assertEqual(result["daily_post_count"], 0)
        self.assertEqual(result["half_hour_post_count"], 1)
        self.assertEqual(result["hourly_post_count"], 1)
        self.assertEqual(result["publish_capacity"], 0)
        self.assertIn("30-minute post limit reached", result["reasons"])
        self.assertFalse(result["allowed"])


if __name__ == "__main__":
    unittest.main()
