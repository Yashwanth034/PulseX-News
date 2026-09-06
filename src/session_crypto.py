import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


AAD = b"pulsex-x-session-v1"
FORMAT_VERSION = 1
ALGORITHM = "AES-256-GCM"


class SessionCryptoError(Exception):
    pass


def _derive_key(secret):
    value = (secret or "").encode("utf-8")
    if len(value) < 24:
        raise SessionCryptoError(
            "X_SESSION_ENCRYPTION_KEY is missing or too short; use at least 24 characters"
        )
    return hashlib.sha256(value).digest()


def _validate_session_bytes(data):
    try:
        parsed = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise SessionCryptoError(f"Saved X session is not valid JSON: {exc}")

    cookies = parsed.get("cookies") if isinstance(parsed, dict) else None
    if not isinstance(cookies, list) or not cookies:
        raise SessionCryptoError("Saved X session does not contain browser cookies")

    return parsed


def encrypt_session(input_path, output_path, secret):
    input_path = Path(input_path)
    output_path = Path(output_path)
    plaintext = input_path.read_bytes()
    _validate_session_bytes(plaintext)

    key = _derive_key(secret)

    # Avoid a new state-branch commit when X did not refresh anything.
    if output_path.exists():
        try:
            if decrypt_bytes(output_path.read_bytes(), secret) == plaintext:
                return False
        except SessionCryptoError:
            pass

    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, AAD)
    envelope = {
        "version": FORMAT_VERSION,
        "algorithm": ALGORITHM,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")
    output_path.chmod(0o600)
    return True


def decrypt_bytes(encrypted, secret):
    try:
        envelope = json.loads(encrypted.decode("utf-8"))
    except Exception as exc:
        raise SessionCryptoError(f"Encrypted X session envelope is invalid: {exc}")

    if not isinstance(envelope, dict):
        raise SessionCryptoError("Encrypted X session envelope is invalid")
    if envelope.get("version") != FORMAT_VERSION:
        raise SessionCryptoError("Unsupported encrypted X session version")
    if envelope.get("algorithm") != ALGORITHM:
        raise SessionCryptoError("Unsupported encrypted X session algorithm")

    try:
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except Exception as exc:
        raise SessionCryptoError(f"Encrypted X session encoding is invalid: {exc}")

    if len(nonce) != 12:
        raise SessionCryptoError("Encrypted X session nonce is invalid")

    try:
        plaintext = AESGCM(_derive_key(secret)).decrypt(nonce, ciphertext, AAD)
    except Exception as exc:
        raise SessionCryptoError(
            "Unable to decrypt X session. Check X_SESSION_ENCRYPTION_KEY."
        ) from exc

    _validate_session_bytes(plaintext)
    return plaintext


def decrypt_session(input_path, output_path, secret):
    input_path = Path(input_path)
    output_path = Path(output_path)
    plaintext = decrypt_bytes(input_path.read_bytes(), secret)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(plaintext)
    output_path.chmod(0o600)


def _secret_from_env():
    return os.getenv("X_SESSION_ENCRYPTION_KEY", "").strip()


def main():
    parser = argparse.ArgumentParser(description="Encrypt/decrypt PulseX saved X browser sessions")
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encrypt")
    enc.add_argument("input")
    enc.add_argument("output")

    dec = sub.add_parser("decrypt")
    dec.add_argument("input")
    dec.add_argument("output")

    args = parser.parse_args()
    secret = _secret_from_env()

    try:
        if args.command == "encrypt":
            changed = encrypt_session(args.input, args.output, secret)
            print("Encrypted X session updated." if changed else "Encrypted X session unchanged.")
        else:
            decrypt_session(args.input, args.output, secret)
            print("Encrypted X session restored.")
    except (OSError, SessionCryptoError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
