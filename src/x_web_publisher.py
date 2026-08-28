import json
import os
import tempfile
import time
from pathlib import Path

from src.media import download_media
from src.x_publisher import XPublisher, XPublisherError


def _load_env_file():
    """Load .env into os.environ without overriding existing values."""
    env_path = (
        Path(__file__).resolve().parents[1]
        / ".env"
    )
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

LOGIN_URL = "https://x.com/login"
COMPOSE_URL = "https://x.com/compose/post"

USERNAME_SELECTOR = 'input[autocomplete="username"]'
USERNAME_SELECTOR_FALLBACK = 'input[name="username_or_email"]'
PASSWORD_SELECTOR = 'input[name="password"]'
NEXT_BUTTON = '[data-testid="ocfEnterTextNextButton"]'
NEXT_BUTTON_FALLBACK = 'button svg[data-icon="icon-arrow-right"]'
LOGIN_BUTTON = '[data-testid="LoginForm_Login_Button"]'
LOGIN_BUTTON_FALLBACK = 'button svg[data-icon="icon-arrow-right"]'
CHALLENGE_INPUT = 'input[name="challenge_response"]'
COMPOSER_TEXTAREA = '[data-testid="tweetTextarea_0"]'
MEDIA_FILE_INPUT = (
    'input[data-testid="fileInput"], '
    'input[type="file"][accept*="image"], '
    'input[type="file"][accept*="video"]'
)
MEDIA_PREVIEW = (
    '[data-testid="attachments"], '
    '[data-testid="mediaContainer"]'
)
THREAD_ADD_BUTTON = '[data-testid="createThreadButton"]'
THREAD_ADD_BUTTON_FALLBACK = 'button[aria-label="Add another tweet"]'
POST_BUTTON = '[data-testid="tweetButton"]'
POST_BUTTON_FALLBACK = '[data-testid="tweetButtonInline"]'
TOAST = '[data-testid="toast"]'
LOGGED_IN_MARKER = (
    '[data-testid="SideNav_NewTweet_Button"], '
    'a[href="/compose/post"]'
)
SKIP_BUTTONS = [
    'text=Skip for now',
    'text=Not now',
    'text=Skip',
    'button:has-text("Skip")',
    '[data-testid="app-bar-close"]',
    'text=Maybe later',
    'text=Continue without follow',
]


class _WebComposer:
    """Owns one browser context and performs the actual login + posting."""

    def __init__(self, username, password, session_file, headless=True, otp=""):
        from playwright.sync_api import sync_playwright

        self.username = username
        self.password = password
        self.session_file = session_file
        self.headless = headless
        self.otp = otp
        self._pw = sync_playwright().start()
        launch_options = {"headless": self.headless}
        browser_channel = os.getenv("X_BROWSER_CHANNEL", "").strip()
        if browser_channel:
            launch_options["channel"] = browser_channel
        self.browser = self._pw.chromium.launch(**launch_options)
        storage = None
        if session_file.exists():
            try:
                storage = json.loads(session_file.read_text())
            except Exception:
                storage = None
        self.context = self.browser.new_context(storage_state=storage)
        self.page = self.context.new_page()

    # -------------------------------------------------
    # LOGIN
    # -------------------------------------------------

    def _is_logged_in(self):
        return (
            "x.com/login" not in self.page.url
            and self.page.locator(POST_BUTTON).count() == 0
        )

    def ensure_logged_in(self):
        if self._session_is_valid():
            return

        # A saved browser session is the normal unattended path.
        # Username/password are only an optional local recovery path.
        # GitHub Actions intentionally runs without them so it can never
        # fall back to repeated password logins when a session expires.
        if not self.username or not self.password:
            raise XPublisherError(
                "Saved X session is missing or expired. "
                "Re-capture the one-time browser session."
            )

        self._login()
        self._skip_onboarding()
        self._save_session()

    def _is_logged_in_page(self):
        if (
            "x.com/login" in self.page.url
            or "i/flow" in self.page.url
            or "onboarding" in self.page.url
        ):
            return False
        return (
            self.page.locator(
                LOGGED_IN_MARKER
            ).count()
            > 0
        )

    def _session_is_valid(self):
        try:
            self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_timeout(4000)
            return self._is_logged_in_page()
        except Exception:
            return False

    def _login(self):
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        # -------------------------------------------------
        # New X login form (2026):
        # username + password on one page, arrow button.
        # Old form: username first, then Next, then password.
        # -------------------------------------------------

        try:
            self.page.wait_for_selector(USERNAME_SELECTOR, timeout=10000)
            self.page.locator(USERNAME_SELECTOR).fill(self.username)
            self.page.locator(NEXT_BUTTON).click()
            self.page.wait_for_selector(PASSWORD_SELECTOR, timeout=30000)
        except Exception:
            self.page.wait_for_selector(USERNAME_SELECTOR_FALLBACK, timeout=30000)
            self.page.locator(USERNAME_SELECTOR_FALLBACK).first.fill(self.username)
            self.page.wait_for_timeout(1500)
            self.page.locator(NEXT_BUTTON_FALLBACK).first.evaluate(
                "el => el.closest('button').click()"
            )
            self.page.wait_for_timeout(3000)

        self.page.locator(PASSWORD_SELECTOR).first.fill(self.password)
        self.page.wait_for_timeout(1500)

        if self.page.locator(LOGIN_BUTTON).count() > 0:
            self.page.locator(LOGIN_BUTTON).click()
        else:
            self.page.locator(LOGIN_BUTTON_FALLBACK).first.evaluate(
                "el => el.closest('button').click()"
            )

        try:
            self.page.wait_for_selector(CHALLENGE_INPUT, timeout=5000)
            if not self.otp:
                raise XPublisherError(
                    "X requested extra verification. "
                    "Set X_OTP to your code, or log in "
                    "manually and provide a saved session."
                )
            self.page.locator(CHALLENGE_INPUT).fill(self.otp)
            self.page.wait_for_timeout(1000)
            self.page.locator(CHALLENGE_INPUT).press("Enter")
        except XPublisherError:
            raise
        except Exception:
            pass

        try:
            self.page.wait_for_selector(
                LOGGED_IN_MARKER,
                timeout=60000
            )
        except Exception:
            self._skip_onboarding()
            self.page.wait_for_selector(
                LOGGED_IN_MARKER,
                timeout=60000
            )

    def _skip_onboarding(self):
        """Dismiss the first-login onboarding screens if they appear."""
        for _ in range(5):
            try:
                self.page.wait_for_url("**/home", timeout=8000)
                return
            except Exception:
                pass
            for selector in SKIP_BUTTONS:
                try:
                    button = self.page.locator(selector).first
                    if button.count() > 0 and button.is_visible():
                        button.click(timeout=3000)
                        self.page.wait_for_timeout(2000)
                        break
                except Exception:
                    continue

    def _save_session(self):
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps(self.context.storage_state(), indent=2)
        )

    # -------------------------------------------------
    # POSTING
    # -------------------------------------------------

    def _open_composer(self):
        self.page.goto(COMPOSE_URL, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_selector(COMPOSER_TEXTAREA, timeout=30000)

    def _type_text(self, text):
        textarea = self.page.locator(COMPOSER_TEXTAREA).first
        textarea.click()
        self.page.keyboard.type(text)

    def _attach_media(self, media_path):
        """Attach one local image/video. Any failure returns False for text fallback."""
        if not media_path:
            return False

        file_input = None
        try:
            file_input = self.page.locator(MEDIA_FILE_INPUT).first
            if file_input.count() == 0:
                return False

            file_input.set_input_files(media_path)

            # Prefer an explicit preview marker when X exposes one. The file
            # input check keeps this resilient to harmless test-id changes.
            try:
                self.page.wait_for_selector(MEDIA_PREVIEW, timeout=12000)
            except Exception:
                selected = file_input.evaluate(
                    "el => Boolean(el.files && el.files.length > 0)"
                )
                if not selected:
                    return False
                self.page.wait_for_timeout(1800)

            return True
        except Exception:
            if file_input is not None:
                try:
                    file_input.set_input_files([])
                except Exception:
                    pass
            return False

    def _click_post(self):
        button = self.page.locator(POST_BUTTON)
        if button.count() == 0:
            button = self.page.locator(POST_BUTTON_FALLBACK)
        button.click()
        self.page.wait_for_selector(TOAST, timeout=30000)

    def _tweet_id_from_toast(self):
        try:
            link = self.page.locator(f"{TOAST} a[href*='/status/']").first
            href = link.get_attribute("href", timeout=2000) or ""
            return href.rstrip("/").split("/")[-1]
        except Exception:
            return None

    def post_single(self, text, media_path=None):
        self._open_composer()
        self._type_text(text)
        media_attached = self._attach_media(media_path)
        self._click_post()
        tweet_id = self._tweet_id_from_toast()
        return {
            "web": True,
            "text": text,
            "tweet_id": tweet_id,
            "media_attached": media_attached,
        }

    def post_thread(self, texts, media_path=None):
        self._open_composer()
        media_attached = False
        for index, text in enumerate(texts):
            if index > 0:
                add = self.page.locator(THREAD_ADD_BUTTON)
                if add.count() == 0:
                    add = self.page.locator(THREAD_ADD_BUTTON_FALLBACK)
                add.click()
                self.page.wait_for_timeout(800)
            self._type_text(text)
            if index == 0:
                media_attached = self._attach_media(media_path)
        self._click_post()
        results = []
        for index, text in enumerate(texts):
            results.append(
                {
                    "web": True,
                    "text": text,
                    "media_attached": bool(media_attached and index == 0),
                }
            )
        return results

    def close(self):
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


class XWebPublisher(XPublisher):
    """
    X publisher that posts through the x.com web interface using Playwright.

    The preferred unattended mode reuses a previously captured browser
    session. Username/password are optional only for local recovery when a
    saved session is unavailable; GitHub Actions does not receive them.

    Safety rules are identical to XPublisher:

        - Live publishing disabled by default.
        - Kill switch blocks publishing.
        - Production controller must allow live publishing.
        - Daily, half-hour, and hourly posting limits apply.
        - Failed posts are NOT counted.
        - Dry-run mode never opens a browser.

    Environment variables:

        X_USERNAME          optional local recovery username/email
        X_PASSWORD          optional local recovery password
        X_OTP               optional local recovery verification code
        X_HEADLESS          "false" to watch the browser (default "true")
        X_PUBLISH_ENABLED   "true" to enable live publishing

    The normal unattended path uses data/web_session.json captured once from
    an already logged-in browser. If that saved session expires and no local
    recovery credentials are present, publishing fails safely instead of
    attempting another login.
    """

    def __init__(self):
        super().__init__()
        self.username = os.getenv("X_USERNAME", "").strip()
        self.password = os.getenv("X_PASSWORD", "").strip()
        self.otp = os.getenv("X_OTP", "").strip()
        self.headless = os.getenv("X_HEADLESS", "true").lower() != "false"
        self.session_file = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "web_session.json"
        )

    def publish(self, item):
        fmt = item.get("format")

        # =================================================
        # DRY RUN
        # =================================================

        if not self.enabled:
            if fmt == "single":
                return [
                    {
                        "mode": "dry_run",
                        "text": item.get("post", ""),
                    }
                ]
            return [
                {
                    "mode": "dry_run",
                    "text": text,
                }
                for text in item.get("thread", [])
            ]

        # =================================================
        # SINGLE POST
        # =================================================

        if fmt == "single":
            text = (item.get("post") or "").strip()
            if not text:
                raise XPublisherError("Cannot publish empty post")
            self._check_allowed(required_posts=1)
            composer = _WebComposer(
                self.username,
                self.password,
                self.session_file,
                headless=self.headless,
                otp=self.otp,
            )
            try:
                composer.ensure_logged_in()
                with tempfile.TemporaryDirectory(prefix="pulsex-media-") as media_dir:
                    media_path = download_media(
                        item.get("media"),
                        directory=media_dir,
                    )
                    result = composer.post_single(
                        text,
                        media_path=media_path,
                    )
            except Exception as exc:
                if isinstance(exc, XPublisherError):
                    raise
                raise XPublisherError(f"X web post failed: {exc}")
            finally:
                composer.close()
            self._record_success()
            return [result]

        # =================================================
        # THREAD
        # =================================================

        if fmt != "thread":
            raise XPublisherError(f"Unknown format: {fmt}")

        thread = [
            (text or "").strip()
            for text in item.get("thread", [])
            if (text or "").strip()
        ]

        if not thread:
            raise XPublisherError("Cannot publish empty thread")

        required_posts = len(thread)
        self._check_allowed(required_posts=required_posts)

        composer = _WebComposer(
            self.username,
            self.password,
            self.session_file,
            headless=self.headless,
            otp=self.otp,
        )
        try:
            composer.ensure_logged_in()
            with tempfile.TemporaryDirectory(prefix="pulsex-media-") as media_dir:
                media_path = download_media(
                    item.get("media"),
                    directory=media_dir,
                )
                results = composer.post_thread(
                    thread,
                    media_path=media_path,
                )
        except Exception as exc:
            if isinstance(exc, XPublisherError):
                raise
            raise XPublisherError(f"X web thread failed: {exc}")
        finally:
            composer.close()

        for _ in thread:
            self._record_success()

        return results
