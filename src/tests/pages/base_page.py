"""
Base Page Object for Omnix Playwright Testing Framework.

All page objects inherit from BasePage, which provides common
navigation, element interaction, and assertion utilities.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect


class BasePage:
    """Base class for all Page Object Models.

    Provides shared helpers for navigation, waiting, screenshots,
    and common element interactions.
    """

    BASE_URL = "http://localhost:5000"

    def __init__(self, page: Page) -> None:
        self.page = page

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, path: str = "/") -> None:
        """Navigate to *path* relative to ``BASE_URL``."""
        self.page.goto(f"{self.BASE_URL}{path}", wait_until="domcontentloaded")

    def reload(self) -> None:
        """Reload the current page."""
        self.page.reload(wait_until="domcontentloaded")

    @property
    def title(self) -> str:
        return self.page.title()

    @property
    def url(self) -> str:
        return self.page.url

    # ------------------------------------------------------------------
    # Element helpers
    # ------------------------------------------------------------------

    def locator(self, selector: str) -> Locator:
        """Return a :class:`Locator` for *selector*."""
        return self.page.locator(selector)

    def by_id(self, element_id: str) -> Locator:
        """Return a locator targeting ``#element_id``."""
        return self.page.locator(f"#{element_id}")

    def by_test_id(self, test_id: str) -> Locator:
        """Return a locator targeting ``[data-testid=...]``."""
        return self.page.get_by_test_id(test_id)

    def by_role(self, role: str, **kwargs) -> Locator:
        """Return a locator targeting an ARIA role."""
        return self.page.get_by_role(role, **kwargs)

    def by_text(self, text: str, exact: bool = False) -> Locator:
        """Return a locator targeting visible text."""
        return self.page.get_by_text(text, exact=exact)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def click(self, selector: str, **kwargs) -> None:
        self.page.locator(selector).click(**kwargs)

    def fill(self, selector: str, value: str) -> None:
        self.page.locator(selector).fill(value)

    def type_text(self, selector: str, text: str, delay: int = 50) -> None:
        """Type text character-by-character (for inputs that react to keystrokes)."""
        self.page.locator(selector).type(text, delay=delay)

    def press_key(self, selector: str, key: str) -> None:
        self.page.locator(selector).press(key)

    def get_text(self, selector: str) -> str:
        return self.page.locator(selector).inner_text()

    def get_value(self, selector: str) -> str:
        return self.page.locator(selector).input_value()

    def get_attribute(self, selector: str, attr: str) -> str | None:
        return self.page.locator(selector).get_attribute(attr)

    def is_visible(self, selector: str) -> bool:
        return self.page.locator(selector).is_visible()

    def is_enabled(self, selector: str) -> bool:
        return self.page.locator(selector).is_enabled()

    def count(self, selector: str) -> int:
        return self.page.locator(selector).count()

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    def wait_for(self, selector: str, state: str = "visible", timeout: float = 10_000) -> None:
        self.page.locator(selector).wait_for(state=state, timeout=timeout)

    def wait_for_url(self, url_pattern: str, timeout: float = 10_000) -> None:
        self.page.wait_for_url(url_pattern, timeout=timeout)

    def wait_for_load(self) -> None:
        self.page.wait_for_load_state("networkidle")

    def wait_ms(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    # ------------------------------------------------------------------
    # Assertions (using Playwright's expect)
    # ------------------------------------------------------------------

    def expect_visible(self, selector: str, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_be_visible(timeout=timeout)

    def expect_hidden(self, selector: str, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_be_hidden(timeout=timeout)

    def expect_text(self, selector: str, text: str, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_have_text(text, timeout=timeout)

    def expect_to_contain_text(self, selector: str, text: str, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_contain_text(text, timeout=timeout)

    def expect_value(self, selector: str, value: str, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_have_value(value, timeout=timeout)

    def expect_count(self, selector: str, count: int, timeout: float = 10_000) -> None:
        expect(self.page.locator(selector)).to_have_count(count, timeout=timeout)

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------

    def screenshot(self, path: str | None = None, full_page: bool = False) -> bytes:
        return self.page.screenshot(path=path, full_page=full_page)

    # ------------------------------------------------------------------
    # JavaScript evaluation
    # ------------------------------------------------------------------

    def evaluate(self, expression: str):
        """Execute JavaScript in the browser context."""
        return self.page.evaluate(expression)

    def evaluate_handle(self, expression: str):
        return self.page.evaluate_handle(expression)

    # ------------------------------------------------------------------
    # Console & error capture
    # ------------------------------------------------------------------

    def collect_console_messages(self):
        """Return lists ``(errors, warnings, logs)`` collected from the console."""
        errors: list[str] = []
        warnings: list[str] = []
        logs: list[str] = []

        def _on_console(msg):
            if msg.type == "error":
                errors.append(msg.text)
            elif msg.type == "warning":
                warnings.append(msg.text)
            else:
                logs.append(msg.text)

        self.page.on("console", _on_console)
        return errors, warnings, logs

    def collect_page_errors(self):
        """Return a list populated with uncaught page errors."""
        page_errors: list[str] = []

        def _on_page_error(err):
            page_errors.append(str(err))

        self.page.on("pageerror", _on_page_error)
        return page_errors
