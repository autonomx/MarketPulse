"""Repository registrations owned by the chat feature."""

from app.persistence.repository_registry import RepositorySpec
from app.platform.chat.persistence.repository import PostgresChatRepository

CHAT_REPOSITORY_SPECS = (
    RepositorySpec(PostgresChatRepository, PostgresChatRepository, "chats"),
)
