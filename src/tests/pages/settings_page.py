"""
Settings Page Object – provider configuration, system prompts, etc.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class SettingsPage(BasePage):
    """Page Object for the Settings modal."""

    MODAL = "#settingsModal"
    CLOSE_BTN = "#closeSettings"
    SAVE_BTN = "#saveSettings"
    PROVIDER_SELECT = "#providerSelect"
    SYSTEM_PROMPT = "#systemPrompt"
    GLOBAL_SYSTEM_PROMPT = "#globalSystemPrompt"
    SYSTEM_PROMPT_PRESET = "#systemPromptPreset"
    VAD_SENSITIVITY = "#vadSensitivity"

    # Provider-specific sections
    LMSTUDIO_SECTION = "#lmstudioSettings"
    OPENROUTER_SECTION = "#openrouterSettings"
    CEREBRAS_SECTION = "#cerebrasSettings"
    LLAMACPP_SECTION = "#llamacppSettings"

    # Provider fields
    LMSTUDIO_URL = "#lmstudioUrl"
    OPENROUTER_API_KEY = "#openrouterApiKey"
    OPENROUTER_MODEL = "#openrouterModel"
    CEREBRAS_API_KEY = "#cerebrasApiKey"
    CEREBRAS_MODEL = "#cerebrasModel"
    LLAMACPP_URL = "#llamacppUrl"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Modal management
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.by_id("settingsModal").is_visible()

    def close(self) -> None:
        self.by_id("closeSettings").click()

    def save(self) -> None:
        self.by_id("saveSettings").click()

    # ------------------------------------------------------------------
    # Provider
    # ------------------------------------------------------------------

    def get_selected_provider(self) -> str:
        return self.by_id("providerSelect").input_value()

    def select_provider(self, provider: str) -> None:
        self.by_id("providerSelect").select_option(value=provider)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def get_system_prompt(self) -> str:
        return self.by_id("systemPrompt").input_value()

    def set_system_prompt(self, prompt: str) -> None:
        self.by_id("systemPrompt").fill(prompt)

    def get_global_system_prompt(self) -> str:
        return self.by_id("globalSystemPrompt").input_value()

    def set_global_system_prompt(self, prompt: str) -> None:
        self.by_id("globalSystemPrompt").fill(prompt)

    def select_preset(self, preset_value: str) -> None:
        self.by_id("systemPromptPreset").select_option(value=preset_value)

    # ------------------------------------------------------------------
    # VAD
    # ------------------------------------------------------------------

    def get_vad_sensitivity(self) -> str:
        return self.by_id("vadSensitivity").input_value()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_open(self) -> None:
        expect(self.by_id("settingsModal")).to_be_visible()

    def expect_closed(self) -> None:
        expect(self.by_id("settingsModal")).to_be_hidden()

    def expect_provider(self, provider: str) -> None:
        expect(self.by_id("providerSelect")).to_have_value(provider)
