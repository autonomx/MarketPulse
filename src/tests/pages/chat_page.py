"""
Chat Page Object – interactions with the main chat area.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .base_page import BasePage


class ChatPage(BasePage):
    """Page Object for the main chat interface."""

    # Selectors
    MESSAGE_INPUT = "#messageInput"
    SEND_BTN = "#sendBtn"
    MESSAGES_CONTAINER = "#messages"
    CHAT_CONTAINER = "#chatContainer"
    WELCOME_MESSAGE = "#welcomeMessage"
    TYPING_INDICATOR = "#typingIndicator"
    CLEAR_BTN = "#clearBtn"
    STOP_GENERATION_BTN = "#stopGenerationBtn"
    MIC_BTN = "#micBtn"
    FILE_BTN = "#fileBtn"
    FILE_INPUT = "#fileInput"
    ATTACHMENT_PREVIEW = "#attachmentPreview"
    CONVERSATION_TOGGLE = "#conversationToggle"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    def open(self) -> "ChatPage":
        """Navigate to the main chat page."""
        self.navigate("/")
        return self

    # ------------------------------------------------------------------
    # Element properties
    # ------------------------------------------------------------------

    @property
    def message_input(self):
        return self.by_id("messageInput")

    @property
    def send_button(self):
        return self.by_id("sendBtn")

    @property
    def messages_container(self):
        return self.by_id("messages")

    @property
    def welcome_message(self):
        return self.by_id("welcomeMessage")

    @property
    def typing_indicator(self):
        return self.by_id("typingIndicator")

    @property
    def clear_button(self):
        return self.by_id("clearBtn")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def type_message(self, text: str) -> None:
        """Type a message into the chat input."""
        self.message_input.fill(text)

    def send_message(self, text: str) -> None:
        """Type and send a chat message."""
        self.type_message(text)
        self.send_button.click()

    def clear_chat(self) -> None:
        """Click the clear chat button."""
        self.clear_button.click()

    def stop_generation(self) -> None:
        """Click the stop generation button."""
        self.by_id("stopGenerationBtn").click()

    def get_message_count(self) -> int:
        """Return the number of message bubbles visible."""
        return self.messages_container.locator(".message").count()

    def get_last_message_text(self) -> str:
        """Return text of the last message in the chat."""
        messages = self.messages_container.locator(".message")
        return messages.last.inner_text()

    def get_all_messages(self) -> list[dict]:
        """Return all messages as ``[{role, content}]``."""
        messages = []
        for el in self.messages_container.locator(".message").all():
            role = "user" if "user" in (el.get_attribute("class") or "") else "assistant"
            content = el.inner_text()
            messages.append({"role": role, "content": content})
        return messages

    def is_welcome_visible(self) -> bool:
        return self.welcome_message.is_visible()

    def is_typing(self) -> bool:
        return self.typing_indicator.is_visible()

    def is_send_enabled(self) -> bool:
        return self.send_button.is_enabled()

    def toggle_voice_mode(self) -> None:
        """Toggle voice conversation mode."""
        self.by_id("conversationToggle").click()

    def attach_file(self, file_path: str) -> None:
        """Attach a file to the chat."""
        self.by_id("fileInput").set_input_files(file_path)

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def expect_welcome_visible(self) -> None:
        expect(self.welcome_message).to_be_visible()

    def expect_message_count(self, count: int, timeout: float = 10_000) -> None:
        expect(self.messages_container.locator(".message")).to_have_count(count, timeout=timeout)

    def expect_typing_visible(self) -> None:
        expect(self.typing_indicator).to_be_visible()

    def expect_typing_hidden(self, timeout: float = 30_000) -> None:
        expect(self.typing_indicator).to_be_hidden(timeout=timeout)
