import json
import tempfile
import unittest
from pathlib import Path

from src.session_crypto import SessionCryptoError, decrypt_session, encrypt_session


class SessionCryptoTests(unittest.TestCase):
    def _session(self):
        return {
            "cookies": [
                {
                    "name": "auth_token",
                    "value": "test-only-cookie",
                    "domain": ".x.com",
                    "path": "/",
                }
            ],
            "origins": [],
        }

    def test_round_trip_and_skip_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "session.json"
            encrypted = root / "session.enc"
            restored = root / "restored.json"
            plain.write_text(json.dumps(self._session()), encoding="utf-8")

            changed = encrypt_session(plain, encrypted, "a" * 32)
            self.assertTrue(changed)
            first_ciphertext = encrypted.read_bytes()

            changed = encrypt_session(plain, encrypted, "a" * 32)
            self.assertFalse(changed)
            self.assertEqual(first_ciphertext, encrypted.read_bytes())

            decrypt_session(encrypted, restored, "a" * 32)
            self.assertEqual(json.loads(restored.read_text()), self._session())

    def test_wrong_key_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "session.json"
            encrypted = root / "session.enc"
            restored = root / "restored.json"
            plain.write_text(json.dumps(self._session()), encoding="utf-8")
            encrypt_session(plain, encrypted, "a" * 32)

            with self.assertRaises(SessionCryptoError):
                decrypt_session(encrypted, restored, "b" * 32)

    def test_invalid_plain_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "session.json"
            encrypted = root / "session.enc"
            plain.write_text(json.dumps({"cookies": []}), encoding="utf-8")

            with self.assertRaises(SessionCryptoError):
                encrypt_session(plain, encrypted, "a" * 32)

    def test_tampered_ciphertext_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "session.json"
            encrypted = root / "session.enc"
            restored = root / "restored.json"
            plain.write_text(json.dumps(self._session()), encoding="utf-8")
            encrypt_session(plain, encrypted, "a" * 32)

            envelope = json.loads(encrypted.read_text())
            ciphertext = envelope["ciphertext"]
            envelope["ciphertext"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
            encrypted.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaises(SessionCryptoError):
                decrypt_session(encrypted, restored, "a" * 32)

    def test_short_encryption_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "session.json"
            encrypted = root / "session.enc"
            plain.write_text(json.dumps(self._session()), encoding="utf-8")

            with self.assertRaises(SessionCryptoError):
                encrypt_session(plain, encrypted, "too-short")


if __name__ == "__main__":
    unittest.main()
