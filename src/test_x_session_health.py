import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.x_session_health as health


class _FakeComposer:
    valid = True
    saved = False
    closed = False

    def __init__(self, *args, **kwargs):
        type(self).saved = False
        type(self).closed = False

    def _session_is_valid(self):
        return type(self).valid

    def _save_session(self):
        type(self).saved = True

    def close(self):
        type(self).closed = True


class XSessionHealthTests(unittest.TestCase):
    def setUp(self):
        self.old_session = health.SESSION
        _FakeComposer.valid = True
        _FakeComposer.saved = False
        _FakeComposer.closed = False

    def tearDown(self):
        health.SESSION = self.old_session

    def test_missing_session_fails_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            health.SESSION = Path(tmp) / "missing.json"
            with patch("src.x_session_health._WebComposer") as composer:
                with self.assertRaises(SystemExit) as caught:
                    health.main()
            self.assertIn("X SESSION EXPIRED", str(caught.exception))
            composer.assert_not_called()

    def test_valid_session_is_refreshed_and_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            health.SESSION = Path(tmp) / "session.json"
            health.SESSION.write_text("{}", encoding="utf-8")
            with patch("src.x_session_health._WebComposer", _FakeComposer):
                health.main()
            self.assertTrue(_FakeComposer.saved)
            self.assertTrue(_FakeComposer.closed)

    def test_invalid_session_fails_and_closes_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            health.SESSION = Path(tmp) / "session.json"
            health.SESSION.write_text("{}", encoding="utf-8")
            _FakeComposer.valid = False
            with patch("src.x_session_health._WebComposer", _FakeComposer):
                with self.assertRaises(SystemExit) as caught:
                    health.main()
            self.assertIn("X SESSION EXPIRED", str(caught.exception))
            self.assertFalse(_FakeComposer.saved)
            self.assertTrue(_FakeComposer.closed)


if __name__ == "__main__":
    unittest.main()
