import json
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "data" / "web_session.json"


class XSessionHealthError(Exception):
    pass


def validate_saved_session(path=SESSION, now=None):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise XSessionHealthError("X SESSION INVALID — saved session is missing")

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise XSessionHealthError(f"X SESSION INVALID — malformed saved session: {exc}") from exc

    cookies = state.get("cookies") if isinstance(state, dict) else None
    if not isinstance(cookies, list) or not cookies:
        raise XSessionHealthError("X SESSION INVALID — saved session contains no cookies")

    auth_cookie = next(
        (
            cookie
            for cookie in cookies
            if isinstance(cookie, dict)
            and cookie.get("name") == "auth_token"
            and cookie.get("value")
            and str(cookie.get("domain", "")).endswith("x.com")
        ),
        None,
    )
    if auth_cookie is None:
        raise XSessionHealthError("X SESSION INVALID — X auth_token cookie is missing")

    current_time = time.time() if now is None else float(now)
    expires = auth_cookie.get("expires")
    try:
        expires = float(expires)
    except (TypeError, ValueError):
        expires = -1.0

    # Playwright uses -1 for a session cookie. Positive timestamps are absolute
    # Unix expiry times, so an already-expired auth cookie can be rejected
    # without making an extra request to X.
    if expires > 0 and expires <= current_time:
        raise XSessionHealthError("X SESSION EXPIRED — auth_token cookie is past its expiry time")

    return {
        "cookie_count": len(cookies),
        "auth_cookie_expires": expires,
    }


def main():
    try:
        result = validate_saved_session()
    except XSessionHealthError as exc:
        raise SystemExit(str(exc))

    print(
        "X SESSION STRUCTURE HEALTHY — "
        f"{result['cookie_count']} cookies present; live validity will be confirmed by the publisher"
    )


if __name__ == "__main__":
    main()
