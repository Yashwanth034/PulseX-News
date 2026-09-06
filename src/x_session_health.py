from pathlib import Path

from src.x_web_publisher import _WebComposer


ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "data" / "web_session.json"


def main():
    if not SESSION.exists() or SESSION.stat().st_size == 0:
        raise SystemExit("X SESSION EXPIRED — saved session is missing")

    composer = _WebComposer("", "", SESSION, headless=True)
    try:
        if not composer._session_is_valid():
            raise SystemExit(
                "X SESSION EXPIRED — re-capture the browser session with scripts/renew_x_session.sh"
            )

        # Preserve any cookies/storage that X refreshed while validating the session.
        composer._save_session()
        print("X SESSION HEALTHY — refreshed browser session saved")
    finally:
        composer.close()


if __name__ == "__main__":
    main()
