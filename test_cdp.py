import os
import sys
from pathlib import Path

DEBUG_URL = os.getenv("X_CDP_URL", "http://localhost:9222")
ROOT = Path(__file__).resolve().parent

COMPOSER_TEXTAREA = '[data-testid="tweetTextarea_0"]'
THREAD_ADD_BUTTON = '[data-testid="createThreadButton"]'
THREAD_ADD_BUTTON_FALLBACK = 'button[aria-label="Add another tweet"]'
POST_BUTTON = '[data-testid="tweetButton"]'
POST_BUTTON_FALLBACK = '[data-testid="tweetButtonInline"]'
TOAST = '[data-testid="toast"]'
LOGGED_IN_MARKER = '[data-testid="SideNav_NewTweet_Button"], a[href="/compose/post"]'


class CdpComposer:
    """Controls the user's real Chrome via CDP - no login automation."""

    def __init__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(DEBUG_URL)
        self.context = self.browser.contexts[0]

    def _find_or_open_x(self):
        for page in self.context.pages:
            if "x.com" in (page.url or ""):
                return page
        page = self.context.new_page()
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        return page

    def ensure_logged_in(self):
        self.page = self._find_or_open_x()
        self.page.wait_for_timeout(4000)
        if not self._is_logged_in():
            print("NOT LOGGED IN - open x.com in your Chrome and log in manually.")
            print("Waiting up to 10 minutes for manual login...")
            for _ in range(120):
                self.page.wait_for_timeout(5000)
                if self._is_logged_in():
                    print("LOGIN DETECTED")
                    return
            raise RuntimeError("Timed out waiting for manual login")
        print("ALREADY LOGGED IN")

    def _is_logged_in(self):
        if "x.com/login" in (self.page.url or ""):
            return False
        return self.page.locator(LOGGED_IN_MARKER).count() > 0

    def _open_composer(self):
        self.page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
        try:
            self.page.wait_for_selector(COMPOSER_TEXTAREA, timeout=30000)
        except Exception:
            self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(4000)
            self.page.locator('a[href="/compose/post"]').first.click()
            self.page.wait_for_selector(COMPOSER_TEXTAREA, timeout=30000)

    def _type_text(self, text):
        textarea = self.page.locator(COMPOSER_TEXTAREA).first
        textarea.click()
        self.page.keyboard.type(text)

    def _click_post(self):
        button = self.page.locator(POST_BUTTON)
        if button.count() == 0:
            button = self.page.locator(POST_BUTTON_FALLBACK)
        button.click()
        try:
            self.page.wait_for_selector(TOAST, timeout=30000)
        except Exception:
            self.page.wait_for_timeout(5000)

    def post_single(self, text):
        self._open_composer()
        self._type_text(text)
        self._click_post()
        return {"cdp": True, "text": text}

    def post_thread(self, texts):
        self._open_composer()
        for index, text in enumerate(texts):
            if index > 0:
                add = self.page.locator(THREAD_ADD_BUTTON)
                if add.count() == 0:
                    add = self.page.locator(THREAD_ADD_BUTTON_FALLBACK)
                add.click()
                self.page.wait_for_timeout(800)
            self._type_text(text)
        self._click_post()
        return [{"cdp": True, "text": text} for text in texts]

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    composer = CdpComposer()
    try:
        composer.ensure_logged_in()
        if mode == "post":
            text = sys.argv[2] if len(sys.argv) > 2 else "Test tweet from my news bot"
            print("POST OK:", composer.post_single(text))
        elif mode == "thread":
            print("THREAD OK:", composer.post_thread([
                "Test thread part 1",
                "Test thread part 2",
                "Test thread part 3",
            ]))
        else:
            print("Unknown mode:", mode)
    finally:
        composer.close()


if __name__ == "__main__":
    main()
