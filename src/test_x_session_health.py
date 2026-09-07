import json
import tempfile
import unittest
from pathlib import Path

import src.x_session_health as health


class XSessionHealthTests(unittest.TestCase):
    def _write_session(self, path, *, expires=4102444800, include_auth=True):
        cookies = [
            {
                "name": "ct0",
                "value": "test-csrf",
                "domain": ".x.com",
                "path": "/",
                "expires": expires,
            }
        ]
        if include_auth:
            cookies.append(
                {
                    "name": "auth_token",
                    "value": "test-auth",
                    "domain": ".x.com",
                    "path": "/",
                    "expires": expires,
                }
            )
        path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")

    def test_missing_session_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            with self.assertRaises(health.XSessionHealthError) as caught:
                health.validate_saved_session(path, now=1000)
            self.assertIn("saved session is missing", str(caught.exception))

    def test_valid_auth_cookie_passes_without_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            self._write_session(path, expires=2000)
            result = health.validate_saved_session(path, now=1000)
            self.assertEqual(result["cookie_count"], 2)
            self.assertEqual(result["auth_cookie_expires"], 2000.0)

    def test_session_cookie_without_fixed_expiry_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            self._write_session(path, expires=-1)
            result = health.validate_saved_session(path, now=1000)
            self.assertEqual(result["auth_cookie_expires"], -1.0)

    def test_expired_auth_cookie_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            self._write_session(path, expires=999)
            with self.assertRaises(health.XSessionHealthError) as caught:
                health.validate_saved_session(path, now=1000)
            self.assertIn("past its expiry time", str(caught.exception))

    def test_missing_auth_cookie_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            self._write_session(path, include_auth=False)
            with self.assertRaises(health.XSessionHealthError) as caught:
                health.validate_saved_session(path, now=1000)
            self.assertIn("auth_token cookie is missing", str(caught.exception))

    def test_malformed_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(health.XSessionHealthError) as caught:
                health.validate_saved_session(path, now=1000)
            self.assertIn("malformed saved session", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
