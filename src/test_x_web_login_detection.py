import unittest

from src.x_web_publisher import (
    CHALLENGE_INPUT,
    COMPOSER_TEXTAREA,
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
    def __init__(self, *, hydrate_after=0, redirect_to=None):
        self.url = "https://x.com/home"
        self.waits = 0
        self.hydrate_after = hydrate_after
        self.redirect_to = redirect_to

    def goto(self, url, **kwargs):
        self.url = self.redirect_to or url

    def wait_for_timeout(self, milliseconds):
        self.waits += 1

    def locator(self, selector):
        if selector == COMPOSER_TEXTAREA:
            return _FakeLocator(
                lambda: 1
                if self.redirect_to is None and self.waits >= self.hydrate_after
                else 0
            )
        if selector in {USERNAME_SELECTOR, USERNAME_SELECTOR_FALLBACK, CHALLENGE_INPUT}:
            return _FakeLocator(lambda: 0)
        return _FakeLocator(lambda: 0)


class XWebLoginDetectionTests(unittest.TestCase):
    def _composer(self, page):
        composer = object.__new__(_WebComposer)
        composer.page = page
        composer.session_diagnostic = "not checked"
        return composer

    def test_session_validation_waits_for_slow_compose_hydration(self):
        page = _FakePage(hydrate_after=3)
        composer = self._composer(page)

        self.assertTrue(composer._session_is_valid())
        self.assertGreaterEqual(page.waits, 3)
        self.assertIn("path=/compose/post", composer.session_diagnostic)
        self.assertIn("composer=1", composer.session_diagnostic)

    def test_login_redirect_is_not_authenticated(self):
        page = _FakePage(redirect_to="https://x.com/i/flow/login")
        composer = self._composer(page)

        self.assertFalse(composer._session_is_valid())
        self.assertIn("path=/i/flow/login", composer.session_diagnostic)
        self.assertIn("composer=0", composer.session_diagnostic)


if __name__ == "__main__":
    unittest.main()
