"""Page Object Models for Omnix UI."""

from .base_page import BasePage
from .chat_page import ChatPage
from .header_page import HeaderPage
from .podcast_page import PodcastPage
from .search_page import SearchPage
from .settings_page import SettingsPage
from .sidebar_page import SidebarPage
from .voice_clone_page import VoiceClonePage
from .voice_studio_page import VoiceStudioPage

__all__ = [
    "BasePage",
    "ChatPage",
    "SidebarPage",
    "HeaderPage",
    "SettingsPage",
    "PodcastPage",
    "VoiceStudioPage",
    "VoiceClonePage",
    "SearchPage",
]
