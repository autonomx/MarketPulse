"""
Search Page Object – chat search and history modals.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class SearchPage(BasePage):
    """Page Object for the Search and History modals."""

    # Search modal
    SEARCH_MODAL = "#searchChatModal"
    CLOSE_SEARCH = "#closeSearchChat"
    SEARCH_INPUT = "#searchChatInput"
    SEARCH_RESULTS = "#searchChatResults"

    # History modal
    HISTORY_MODAL = "#historyModal"
    CLOSE_HISTORY = "#closeHistory"
    HISTORY_LIST = "#historyList"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Search modal
    # ------------------------------------------------------------------

    def is_search_open(self) -> bool:
        return self.by_id("searchChatModal").is_visible()

    def close_search(self) -> None:
        self.by_id("closeSearchChat").click()

    def search(self, query: str) -> None:
        self.by_id("searchChatInput").fill(query)

    def get_search_results_count(self) -> int:
        return self.by_id("searchChatResults").locator("> *").count()

    def get_search_results_text(self) -> str:
        return self.by_id("searchChatResults").inner_text()

    # ------------------------------------------------------------------
    # History modal
    # ------------------------------------------------------------------

    def is_history_open(self) -> bool:
        return self.by_id("historyModal").is_visible()

    def close_history(self) -> None:
        self.by_id("closeHistory").click()

    def get_history_count(self) -> int:
        return self.by_id("historyList").locator("> *").count()

    def click_history_item(self, index: int) -> None:
        self.by_id("historyList").locator("> *").nth(index).click()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_search_open(self) -> None:
        expect(self.by_id("searchChatModal")).to_be_visible()

    def expect_search_closed(self) -> None:
        expect(self.by_id("searchChatModal")).to_be_hidden()

    def expect_history_open(self) -> None:
        expect(self.by_id("historyModal")).to_be_visible()

    def expect_history_closed(self) -> None:
        expect(self.by_id("historyModal")).to_be_hidden()
