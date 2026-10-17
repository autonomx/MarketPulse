"""
Voice Clone Page Object – voice recording/uploading and cloning.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class VoiceClonePage(BasePage):
    """Page Object for the Voice Clone modal."""

    MODAL = "#voiceCloneModal"
    CLOSE_BTN = "#closeVoiceClone"
    VOICE_NAME = "#voiceName"
    CLONE_LANGUAGE = "#cloneLanguage"
    VOICE_GENDER = "#voiceGender"
    RECORD_TAB_BTN = "#recordTabBtn"
    UPLOAD_TAB_BTN = "#uploadTabBtn"
    RECORD_TAB = "#recordTab"
    UPLOAD_TAB = "#uploadTab"
    RECORD_VOICE_BTN = "#recordVoiceBtn"
    RECORDING_STATUS = "#recordingStatus"
    RECORDED_PREVIEW = "#recordedPreview"
    UPLOAD_DROP_ZONE = "#uploadDropZone"
    AUDIO_FILE_INPUT = "#audioFileInput"
    BROWSE_AUDIO_BTN = "#browseAudioBtn"
    UPLOADED_FILE_INFO = "#uploadedFileInfo"
    UPLOADED_FILE_NAME = "#uploadedFileName"
    REMOVE_UPLOADED_FILE = "#removeUploadedFile"
    SAVE_VOICE_BTN = "#saveVoiceBtn"
    SAVED_VOICES_LIST = "#savedVoicesList"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Modal management
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.by_id("voiceCloneModal").is_visible()

    def close(self) -> None:
        self.by_id("closeVoiceClone").click()

    # ------------------------------------------------------------------
    # Voice configuration
    # ------------------------------------------------------------------

    def set_voice_name(self, name: str) -> None:
        self.by_id("voiceName").fill(name)

    def select_language(self, language: str) -> None:
        self.by_id("cloneLanguage").select_option(value=language)

    def select_gender(self, gender: str) -> None:
        self.by_id("voiceGender").select_option(value=gender)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def switch_to_record(self) -> None:
        self.by_id("recordTabBtn").click()

    def switch_to_upload(self) -> None:
        self.by_id("uploadTabBtn").click()

    def is_record_tab_active(self) -> bool:
        return self.by_id("recordTab").is_visible()

    def is_upload_tab_active(self) -> bool:
        return self.by_id("uploadTab").is_visible()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_audio_file(self, file_path: str) -> None:
        self.by_id("audioFileInput").set_input_files(file_path)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def click_save(self) -> None:
        self.by_id("saveVoiceBtn").click()

    def get_saved_voices_count(self) -> int:
        return self.by_id("savedVoicesList").locator("> *").count()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_open(self) -> None:
        expect(self.by_id("voiceCloneModal")).to_be_visible()

    def expect_closed(self) -> None:
        expect(self.by_id("voiceCloneModal")).to_be_hidden()
