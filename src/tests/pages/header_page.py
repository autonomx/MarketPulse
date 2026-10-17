"""
Header Page Object – status indicators, model selection, and controls.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class HeaderPage(BasePage):
    """Page Object for the application header bar."""

    STATUS_DOT = "#statusDot"
    STATUS_TEXT = "#statusText"
    XTTS_STATUS_DOT = "#xttsStatusDot"
    XTTS_STATUS_TEXT = "#xttsStatusText"
    STT_STATUS_DOT = "#sttStatusDot"
    STT_STATUS_TEXT = "#sttStatusText"
    MODEL_SELECT = "#modelSelect"
    LLM_MODEL_BTN = "#llmModelBtn"
    TOKEN_COUNTER = "#tokenCounter"
    TOKEN_RATE = "#tokenRate"
    THEME_TOGGLE = "#themeToggle"
    TTS_SPEAKER = "#ttsSpeaker"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_connection_status(self) -> str:
        return self.by_id("statusText").inner_text()

    def get_tts_status(self) -> str:
        return self.by_id("xttsStatusText").inner_text()

    def get_stt_status(self) -> str:
        return self.by_id("sttStatusText").inner_text()

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def get_selected_model(self) -> str:
        return self.by_id("modelSelect").input_value()

    def select_model(self, model_name: str) -> None:
        self.by_id("modelSelect").select_option(label=model_name)

    def get_model_options(self) -> list[str]:
        return self.by_id("modelSelect").locator("option").all_inner_texts()

    def open_model_manager(self) -> None:
        self.by_id("llmModelBtn").click()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def toggle_theme(self) -> None:
        self.by_id("themeToggle").click()

    # ------------------------------------------------------------------
    # Token display
    # ------------------------------------------------------------------

    def get_token_counter_text(self) -> str:
        return self.by_id("tokenCounter").inner_text()

    def get_token_rate_text(self) -> str:
        return self.by_id("tokenRate").inner_text()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_status_dot_visible(self) -> None:
        expect(self.by_id("statusDot")).to_be_visible()

    def expect_model_select_visible(self) -> None:
        expect(self.by_id("modelSelect")).to_be_visible()
