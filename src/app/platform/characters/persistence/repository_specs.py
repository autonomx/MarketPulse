"""Repository registrations owned by the characters feature."""

from app.persistence.repository_registry import RepositorySpec
from app.platform.characters.persistence.repository import PostgresCharacterRepository

CHARACTER_REPOSITORY_SPECS = (
    RepositorySpec(
        PostgresCharacterRepository,
        PostgresCharacterRepository,
        "characters",
    ),
)
