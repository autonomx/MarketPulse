"""
Voice Studio Page Object – TTS with emotion, speed, and pitch controls.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class VoiceStudioPage(BasePage):
    """Page Object for the Voice Studio modal."""

    MODAL = "#voiceStudioModal"
    CLOSE_BTN = "#closeVoiceStudio"
    TEXT_INPUT = "#vs-text"
    CHAR_COUNT = "#vs-char-count"
    VOICE_SELECT = "#vs-voice"
    EMOTION_SELECT = "#vs-emotion"
    SPEED_SLIDER = "#vs-speed"
    SPEED_VALUE = "#vs-speed-val"
    PITCH_SLIDER = "#vs-pitch"
    PITCH_VALUE = "#vs-pitch-val"
    GENERATE_BTN = "#vs-generate"
    ERROR_DISPLAY = "#vs-error"
    PLAYER_SECTION = "#vs-player-section"
    PLAYER = "#vs-player"
    DOWNLOAD_BTN = "#vs-download"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Modal management
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.by_id("voiceStudioModal").is_visible()

    def close(self) -> None:
        self.by_id("closeVoiceStudio").click()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def set_text(self, text: str) -> None:
        self.by_id("vs-text").fill(text)

    def get_char_count(self) -> str:
        return self.by_id("vs-char-count").inner_text()

    # ------------------------------------------------------------------
    # Voice configuration
    # ------------------------------------------------------------------

    def select_voice(self, voice: str) -> None:
        self.by_id("vs-voice").select_option(value=voice)

    def select_emotion(self, emotion: str) -> None:
        self.by_id("vs-emotion").select_option(value=emotion)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def click_generate(self) -> None:
        self.by_id("vs-generate").click()

    def click_download(self) -> None:
        self.by_id("vs-download").click()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def has_error(self) -> bool:
        return self.by_id("vs-error").is_visible()

    def get_error_text(self) -> str:
        return self.by_id("vs-error").inner_text()

    def is_player_visible(self) -> bool:
        return self.by_id("vs-player-section").is_visible()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_open(self) -> None:
        expect(self.by_id("voiceStudioModal")).to_be_visible()

    def expect_closed(self) -> None:
        expect(self.by_id("voiceStudioModal")).to_be_hidden()
