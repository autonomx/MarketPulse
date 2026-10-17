"""
Podcast Page Object – podcast episode generation and management.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class PodcastPage(BasePage):
    """Page Object for the Podcast modal."""

    MODAL = "#podcast-modal"
    TITLE_INPUT = "#podcast-title"
    FORMAT_SELECT = "#podcast-format"
    TOPIC_INPUT = "#podcast-topic"
    LENGTH_SELECT = "#podcast-length"
    POINTS_INPUT = "#podcast-points"
    SPEAKERS_LIST = "#podcast-speakers-list"
    ADD_SPEAKER_BTN = "#podcast-add-speaker-btn"
    GENERATE_BTN = "#podcast-generate-btn"
    GENERATE_OUTLINE_BTN = "#podcast-generate-outline-btn"
    PROGRESS = "#podcast-progress"
    PROGRESS_BAR = "#podcast-progress-bar"
    TRANSCRIPT_PREVIEW = "#podcast-transcript-preview"
    AUDIO_PLAYER = "#podcast-audio"
    PLAYER = "#podcast-player"
    EPISODES_LIST = "#podcast-episodes-list"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ------------------------------------------------------------------
    # Modal management
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        return self.locator(self.MODAL).is_visible()

    # ------------------------------------------------------------------
    # Episode setup
    # ------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        self.by_id("podcast-title").fill(title)

    def set_topic(self, topic: str) -> None:
        self.by_id("podcast-topic").fill(topic)

    def select_format(self, fmt: str) -> None:
        self.by_id("podcast-format").select_option(value=fmt)

    def set_points(self, points: str) -> None:
        self.by_id("podcast-points").fill(points)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def add_speaker(self) -> None:
        self.by_id("podcast-add-speaker-btn").click()

    def click_generate(self) -> None:
        self.by_id("podcast-generate-btn").click()

    def click_generate_outline(self) -> None:
        self.by_id("podcast-generate-outline-btn").click()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_progress_visible(self) -> bool:
        return self.by_id("podcast-progress").is_visible()

    def get_episode_count(self) -> int:
        return self.by_id("podcast-episodes-list").locator("> *").count()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_open(self) -> None:
        expect(self.locator(self.MODAL)).to_be_visible()

    def expect_closed(self) -> None:
        expect(self.locator(self.MODAL)).to_be_hidden()
