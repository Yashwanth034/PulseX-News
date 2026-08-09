import os
import sys
from pathlib import Path

from src.x_web_publisher import _WebComposer

ROOT = Path(__file__).resolve().parent


def get_credentials():
    username = os.getenv("X_USERNAME", "").strip()
    password = os.getenv("X_PASSWORD", "").strip()
    if not username or not password:
        print(
            "Missing credentials.\n"
            "Run with:\n"
            '  X_USERNAME="you@mail.com" X_PASSWORD="pass" '
            '.venv/bin/python test_x_web.py login'
        )
        sys.exit(1)
    return username, password


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "login"
    username, password = get_credentials()
    headless = os.getenv("X_HEADLESS", "false").lower() == "true"
    otp = os.getenv("X_OTP", "").strip()

    composer = _WebComposer(
        username,
        password,
        ROOT / "data" / "web_session.json",
        headless=headless,
        otp=otp,
    )
    try:
        if mode == "manual":
            composer.page.goto(
                "https://x.com/login",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            print(
                "BROWSER OPENED - please log in manually "
                "in the browser window.\n"
                "The script saves the session automatically "
                "once it detects you are logged in."
            )
            saved = False
            for _ in range(120):
                composer.page.wait_for_timeout(5000)
                try:
                    if composer._is_logged_in_page():
                        composer._save_session()
                        print("LOGIN OK - session saved to data/web_session.json")
                        saved = True
                        break
                except Exception:
                    continue
            if not saved:
                print("Timed out waiting for manual login (10 minutes).")
            return

        composer.ensure_logged_in()
        print("LOGIN OK - session saved to data/web_session.json")
        if mode == "post":
            text = sys.argv[2] if len(sys.argv) > 2 else "Test tweet from my news bot"
            result = composer.post_single(text)
            print(f"POST OK: {result}")
        elif mode == "thread":
            texts = [
                "Test thread part 1",
                "Test thread part 2",
                "Test thread part 3",
            ]
            result = composer.post_thread(texts)
            print(f"THREAD OK: {result}")
    finally:
        composer.close()


if __name__ == "__main__":
    main()
