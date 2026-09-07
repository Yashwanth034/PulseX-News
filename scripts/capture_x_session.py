#!/usr/bin/env python3
"""Capture a fresh X browser session for PulseX without storing credentials."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.x_web_publisher import _WebComposer  # noqa: E402

SESSION_FILE = ROOT / "data" / "web_session.json"
LOGIN_URL = "https://x.com/login"


def main():
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.unlink(missing_ok=True)

    # _WebComposer reads X_BROWSER_CHANNEL; Chrome is the supported capture
    # browser because the GitHub runner also publishes through system Chrome.
    os.environ.setdefault("X_BROWSER_CHANNEL", "chrome")

    composer = _WebComposer(
        "",
        "",
        SESSION_FILE,
        headless=False,
    )

    try:
        composer.page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        print("Browser opened. Log in to X manually; PulseX will save the session once the compose screen is authenticated.")

        for _ in range(120):
            composer.page.wait_for_timeout(5000)
            current = composer.page.url or ""

            # Do not interrupt X while a login/security/onboarding flow is active.
            if (
                "x.com/login" in current
                or "i/flow" in current
                or "onboarding" in current
            ):
                continue

            if composer._session_is_valid():
                composer._save_session()
                SESSION_FILE.chmod(0o600)
                print("X session captured and validated: data/web_session.json")
                return 0

        print("Timed out waiting for an authenticated X session.", file=sys.stderr)
        return 1
    finally:
        composer.close()


if __name__ == "__main__":
    raise SystemExit(main())
