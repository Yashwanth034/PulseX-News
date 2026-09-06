import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.production_run as production_run
from src.x_publisher import XPublisherError


class _FailingPublisher:
    calls = 0

    def publish(self, item):
        type(self).calls += 1
        raise XPublisherError("simulated X session failure")


class _SuccessfulPublisher:
    calls = 0

    def publish(self, item):
        type(self).calls += 1
        return [{"web": True, "text": item.get("post", "")}]


class ProductionRunTests(unittest.TestCase):
    def setUp(self):
        self.old_queue = production_run.QUEUE
        self.old_log = production_run.LOG
        self.old_env = os.environ.copy()
        _FailingPublisher.calls = 0
        _SuccessfulPublisher.calls = 0

    def tearDown(self):
        production_run.QUEUE = self.old_queue
        production_run.LOG = self.old_log
        os.environ.clear()
        os.environ.update(self.old_env)

    def _configure_temp_files(self, root, stories):
        production_run.QUEUE = root / "queue.json"
        production_run.LOG = root / "publish_log.json"
        production_run.QUEUE.write_text(
            json.dumps({"stories": stories}),
            encoding="utf-8",
        )

    def _decision(self, capacity=1):
        return {
            "allowed": True,
            "publish_capacity": capacity,
        }

    def test_publish_error_fails_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._configure_temp_files(
                root,
                [
                    {"id": "1", "title": "One", "format": "single", "post": "hello"},
                    {"id": "2", "title": "Two", "format": "single", "post": "world"},
                ],
            )
            os.environ["X_POST_METHOD"] = "web"
            os.environ["X_REQUIRE_HUMAN_REVIEW"] = "false"

            with patch("src.production_run.controller", return_value=self._decision()), patch(
                "src.production_run.load", return_value={"reviews": {}}
            ), patch("src.x_web_publisher.XWebPublisher", _FailingPublisher):
                with self.assertRaises(SystemExit) as caught:
                    production_run.main()

            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(_FailingPublisher.calls, 1)
            log = json.loads(production_run.LOG.read_text())
            self.assertEqual(log[0]["error"], "simulated X session failure")

    def test_success_consumes_capacity_and_blocks_second_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._configure_temp_files(
                root,
                [
                    {"id": "1", "title": "One", "format": "single", "post": "first"},
                    {"id": "2", "title": "Two", "format": "single", "post": "second"},
                ],
            )
            os.environ["X_POST_METHOD"] = "web"
            os.environ["X_REQUIRE_HUMAN_REVIEW"] = "false"

            with patch("src.production_run.controller", return_value=self._decision(capacity=1)), patch(
                "src.production_run.load", return_value={"reviews": {}}
            ), patch("src.x_web_publisher.XWebPublisher", _SuccessfulPublisher):
                production_run.main()

            self.assertEqual(_SuccessfulPublisher.calls, 1)
            log = json.loads(production_run.LOG.read_text())
            self.assertIn("result", log[0])
            self.assertEqual(log[1]["blocked"], "current publishing capacity exhausted")
            self.assertEqual(log[1]["remaining_capacity"], 0)


if __name__ == "__main__":
    unittest.main()
