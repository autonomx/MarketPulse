"""
Sidebar Page Object – navigation and session management.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class SidebarPage(BasePage):
    """Page Object for the collapsible sidebar."""

    SIDEBAR = "#sidebar"
    SIDEBAR_LOGO_BTN = "#sidebarLogoBtn"
    SIDEBAR_COLLAPSE_BTN = "#sidebarCollapseBtn"
    EXPAND_SIDEBAR_BTN = "#expandSidebarBtn"
    SESSION_LIST = "#sessionList"

    # Option buttons (expanded)
    NEW_CHAT_BTN = "#newChatBtnOption"
    SEARCH_CHAT_BTN = "#searchChatBtnOption"
    HISTORY_BTN = "#historyBtnOption"
    SETTINGS_BTN = "#settingsBtnOption"
    VOICE_CLONE_BTN = "#voiceCloneBtnOption"
    AUDIOBOOK_BTN = "#audiobookBtnOption"
    PODCAST_BTN = "#podcastBtnOption"
    VOICE_STUDIO_BTN = "#voiceStudioBtnOption"

    # Collapsed icon buttons
    NEW_CHAT_BTN_COLLAPSED = "#newChatBtnCollapsed"
    SEARCH_CHAT_BTN_COLLAPSED = "#searchChatBtnCollapsed"
    HISTORY_BTN_COLLAPSED = "#historyBtnCollapsed"
    SETTINGS_BTN_COLLAPSED = "#settingsBtnCollapsed"
    VOICE_CLONE_BTN_COLLAPSED = "#voiceCloneBtnCollapsed"
    AUDIOBOOK_BTN_COLLAPSED = "#audiobookBtnCollapsed"
    PODCAST_BTN_COLLAPSED = "#podcastBtnCollapsed"
    VOICE_STUDIO_BTN_COLLAPSED = "#voiceStudioBtnCollapsed"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    @property
    def sidebar(self):
        return self.by_id("sidebar")

    # ------------------------------------------------------------------
    # State checks
    # ------------------------------------------------------------------

    def is_collapsed(self) -> bool:
        classes = self.sidebar.get_attribute("class") or ""
        return "collapsed" in classes

    def is_expanded(self) -> bool:
        return not self.is_collapsed()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def expand(self) -> None:
        """Expand the sidebar if it's collapsed."""
        if self.is_collapsed():
            expand_btn = self.by_id("expandSidebarBtn")
            if expand_btn.is_visible():
                expand_btn.click()
            else:
                self.by_id("sidebarLogoBtn").click()

    def collapse(self) -> None:
        """Collapse the sidebar if it's expanded."""
        if self.is_expanded():
            self.by_id("sidebarCollapseBtn").click()

    def click_new_chat(self) -> None:
        if self.is_expanded():
            self.by_id("newChatBtnOption").click()
        else:
            self.by_id("newChatBtnCollapsed").click()

    def click_search(self) -> None:
        if self.is_expanded():
            self.by_id("searchChatBtnOption").click()
        else:
            self.by_id("searchChatBtnCollapsed").click()

    def click_history(self) -> None:
        if self.is_expanded():
            self.by_id("historyBtnOption").click()
        else:
            self.by_id("historyBtnCollapsed").click()

    def click_settings(self) -> None:
        if self.is_expanded():
            self.by_id("settingsBtnOption").click()
        else:
            self.by_id("settingsBtnCollapsed").click()

    def click_voice_clone(self) -> None:
        if self.is_expanded():
            self.by_id("voiceCloneBtnOption").click()
        else:
            self.by_id("voiceCloneBtnCollapsed").click()

    def click_audiobook(self) -> None:
        if self.is_expanded():
            self.by_id("audiobookBtnOption").click()
        else:
            self.by_id("audiobookBtnCollapsed").click()

    def click_podcast(self) -> None:
        if self.is_expanded():
            self.by_id("podcastBtnOption").click()
        else:
            self.by_id("podcastBtnCollapsed").click()

    def click_voice_studio(self) -> None:
        if self.is_expanded():
            self.by_id("voiceStudioBtnOption").click()
        else:
            self.by_id("voiceStudioBtnCollapsed").click()

    def get_session_count(self) -> int:
        return self.by_id("sessionList").locator("> *").count()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_collapsed(self) -> None:
        expect(self.sidebar).to_have_class(r".*collapsed.*")

    def expect_expanded(self) -> None:
        expect(self.sidebar).not_to_have_class(r".*collapsed.*")
