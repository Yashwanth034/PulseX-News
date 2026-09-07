import unittest

from src.x_web_publisher import (
    CHALLENGE_INPUT,
    LOGGED_IN_MARKER,
    USERNAME_SELECTOR,
    USERNAME_SELECTOR_FALLBACK,
    _WebComposer,
)


class _FakeLocator:
    def __init__(self, count_fn):
        self._count_fn = count_fn

    def count(self):
        return self._count_fn()


class _FakePage:
    def __init__(self, *, hydrate_after=0):
        self.url = "https://x.com/home"
        self.waits = 0
        self.hydrate_after = hydrate_after

    def goto(self, url, **kwargs):
        self.url = url

    def wait_for_timeout(self, milliseconds):
        self.waits += 1

    def locator(self, selector):
        if selector == LOGGED_IN_MARKER:
            return _FakeLocator(lambda: 1 if self.waits >= self.hydrate_after else 0)
        if selector in {USERNAME_SELECTOR, USERNAME_SELECTOR_FALLBACK, CHALLENGE_INPUT}:
            return _FakeLocator(lambda: 0)
        return _FakeLocator(lambda: 0)


class XWebLoginDetectionTests(unittest.TestCase):
    def _composer(self, page):
        composer = object.__new__(_WebComposer)
        composer.page = page
        composer.session_diagnostic = "not checked"
        return composer

    def test_home_shell_with_current_logged_in_marker_is_authenticated(self):
        composer = self._composer(_FakePage(hydrate_after=0))
        self.assertTrue(composer._is_logged_in_page())

    def test_session_validation_waits_for_slow_home_hydration(self):
        page = _FakePage(hydrate_after=3)
        composer = self._composer(page)

        self.assertTrue(composer._session_is_valid())
        self.assertGreaterEqual(page.waits, 3)
        self.assertIn("path=/home", composer.session_diagnostic)
        self.assertIn("logged_in_marker=1", composer.session_diagnostic)

    def test_login_flow_is_never_treated_as_authenticated(self):
        page = _FakePage(hydrate_after=0)
        page.url = "https://x.com/i/flow/login"
        composer = self._composer(page)
        self.assertFalse(composer._is_logged_in_page())


if __name__ == "__main__":
    unittest.main()
