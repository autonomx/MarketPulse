from __future__ import annotations

from typing import Any

from app.platform.characters.models import (
    CharacterProfile,
    CharacterProfileVersion,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)
from app.platform.characters.repository import CharacterConflictError, CharacterNotFoundError

from app.persistence.database import PostgresDatabase, default_database
from app.persistence.errors import EntityNotFound, RevisionConflict
from app.security.tenant_context import RequestTenant
from app.persistence.unit_of_work import unit_of_work
from app.persistence.repository_registry import install_repository_specs
from app.runtime.pagination import MAX_PAGE_SIZE
from app.platform.characters.persistence.repository_specs import CHARACTER_REPOSITORY_SPECS

class PostgresCharacterRepositoryAdapter:
    context = RequestTenant()
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        install_repository_specs(CHARACTER_REPOSITORY_SPECS)
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def create(self, request: CreateCharacterRequest) -> CharacterProfile:
        character_id = self._normalize_id(request.id or request.display_name)
        try:
            with unit_of_work(self.database) as work:
                record = work.characters.create(
                    self.context,
                    character_id=character_id,
                    profile=self._request_profile(request),
                    visibility="private",
                    enabled=request.enabled,
                )
                work.commit()
        except Exception as exc:
            if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
                raise CharacterConflictError(f"character already exists: {character_id}") from exc
            raise
        return self._profile(record)

    def get(self, character_id: str, *, include_archived: bool = False) -> CharacterProfile | None:
        with unit_of_work(self.database) as work:
            record = work.characters.get_character(
                self.context,
                character_id,
                include_archived=include_archived,
            )
            work.rollback()
        return self._profile(record) if record is not None else None

    def list(self, *, include_archived: bool = False) -> list[CharacterProfile]:
        """Every character, by name, read one page at a time (WP-5.5)."""
        records: list[dict[str, Any]] = []
        after_id: str | None = None
        while True:
            with unit_of_work(self.database) as work:
                page = work.characters.list_characters(
                    self.context,
                    include_archived=include_archived,
                    limit=MAX_PAGE_SIZE,
                    after_id=after_id,
                )
                work.rollback()
            records.extend(page)
            if len(page) < MAX_PAGE_SIZE:
                return [self._profile(record) for record in records]
            after_id = str(page[-1]["id"])

    def update(self, character_id: str, request: UpdateCharacterRequest) -> CharacterProfile:
        current = self.get(character_id)
        if current is None:
            raise CharacterNotFoundError(character_id)
        changes = request.model_dump(exclude={"expected_version"}, exclude_none=True)
        if changes.pop("clear_default_voice", False):
            changes["default_voice_asset_id"] = None
        profile = {
            "display_name": changes.get("display_name", current.display_name),
            "description": changes.get("description", current.description),
            "personality_prompt": changes.get(
                "personality_prompt", current.personality_prompt
            ),
            "default_greeting": changes.get("default_greeting", current.default_greeting),
            "default_voice_asset_id": changes.get(
                "default_voice_asset_id", current.default_voice_asset_id
            ),
            "speech_style": changes.get("speech_style", dict(current.speech_style)),
            "identity_policy": changes.get("identity_policy", dict(current.identity_policy)),
            "shared_memory_policy": changes.get(
                "shared_memory_policy", dict(current.shared_memory_policy)
            ),
        }
        try:
            with unit_of_work(self.database) as work:
                record = work.characters.update(
                    self.context,
                    character_id=character_id,
                    profile=profile,
                    expected_version=request.expected_version,
                )
                work.commit()
        except EntityNotFound as exc:
            raise CharacterNotFoundError(character_id) from exc
        except RevisionConflict as exc:
            raise CharacterConflictError(str(exc)) from exc
        return self._profile(record)

    def archive(self, character_id: str) -> CharacterProfile:
        current = self.get(character_id, include_archived=True)
        if current is None:
            raise CharacterNotFoundError(character_id)
        try:
            with unit_of_work(self.database) as work:
                record = work.characters.archive(
                    self.context,
                    character_id=character_id,
                    expected_revision=current.active_version,
                )
                work.commit()
        except RevisionConflict as exc:
            raise CharacterConflictError(str(exc)) from exc
        return self._profile(record)

    def versions(self, character_id: str) -> list[CharacterProfileVersion]:
        try:
            with unit_of_work(self.database) as work:
                records = work.characters.versions(self.context, character_id)
                work.rollback()
        except EntityNotFound as exc:
            raise CharacterNotFoundError(character_id) from exc
        return [
            CharacterProfileVersion(
                character_id=record["character_id"],
                version=record["version"],
                **record["profile"],
                created_at=record["created_at"],
            )
            for record in records
        ]

    @staticmethod
    def _normalize_id(value: str) -> str:
        import re

        normalized = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
        if not normalized:
            raise ValueError("character id is required")
        return normalized

    @staticmethod
    def _request_profile(request: CreateCharacterRequest) -> dict[str, Any]:
        return {
            "display_name": request.display_name.strip(),
            "description": request.description.strip(),
            "personality_prompt": request.personality_prompt.strip(),
            "default_greeting": request.default_greeting.strip(),
            "default_voice_asset_id": request.default_voice_asset_id,
            "speech_style": dict(request.speech_style),
            "identity_policy": dict(request.identity_policy),
            "shared_memory_policy": dict(request.shared_memory_policy),
        }

    @staticmethod
    def _profile(record: dict[str, Any]) -> CharacterProfile:
        return CharacterProfile(
            id=record["id"],
            **record["profile"],
            active_version=record["active_version"],
            enabled=record["enabled"],
            status=record["status"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )



def production_character_repository() -> PostgresCharacterRepositoryAdapter:
    from app.persistence.repository_registry import register_feature_repositories

    register_feature_repositories("characters")
    return PostgresCharacterRepositoryAdapter()
