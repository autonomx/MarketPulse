"""Character profile management with shared voice-asset validation."""
from __future__ import annotations

from app.assets.protocol import AssetStore
import logging
from collections.abc import Callable
from typing import Any, Protocol

from app.assets.content import asset_available
from app.assets import (
    AssetRecord,
    AssetType,
    default_asset_store,
    discover_canonical_voice_clone_assets,
    iter_assets,
)

from .models import (
    ArchiveCharacterResponse,
    CharacterListResponse,
    CharacterProfile,
    CharacterVersionListResponse,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)
from app.runtime.ports import Port, implementations

from .repository import (
    CharacterConflictError,
    CharacterNotFoundError,
    CharacterRepository,
)
from .voice_consent import governance_from_asset

LOGGER = logging.getLogger("uvicorn.error")


class CharacterSnapshotObserver(Protocol):
    """Follows character snapshots and changes in this process (ADR-0016 port, PA-3.4).

    Called right after a snapshot is resolved or a character changes; an
    observer that fails is logged and never fails the character operation.
    """

    def on_resolve(self, snapshot: Any) -> None:
        """A version-pinned snapshot was resolved."""

    def on_change(self, character_id: str) -> None:
        """The character was created, updated or archived."""


CHARACTER_SNAPSHOT_OBSERVERS: Port[CharacterSnapshotObserver] = Port(
    "characters.snapshot_observers", CharacterSnapshotObserver, "many",
)


def _publish_snapshot(snapshot: Any) -> None:
    for observer in implementations(CHARACTER_SNAPSHOT_OBSERVERS):
        try:
            observer.on_resolve(snapshot)
        except Exception:
            LOGGER.warning("character snapshot cache observer failed", exc_info=True)


def _invalidate_snapshot_caches(character_id: str) -> None:
    for observer in implementations(CHARACTER_SNAPSHOT_OBSERVERS):
        try:
            observer.on_change(character_id)
        except Exception:
            LOGGER.warning("character snapshot cache invalidator failed", exc_info=True)


class CharacterVoiceAssetError(ValueError):
    pass


class CharacterService:
    def __init__(
        self,
        repository: CharacterRepository | None = None,
        *,
        asset_store_factory: Callable[[], AssetStore] = default_asset_store,
    ) -> None:
        if repository is None:
            from app.persistence.runtime import uses_postgresql_runtime
            if uses_postgresql_runtime():
                from app.platform.characters.persistence.character_store import production_character_repository
                repository = production_character_repository()
            else:
                repository = CharacterRepository()
        self.repository = repository
        self.asset_store_factory = asset_store_factory

    def list(self, *, include_archived: bool = False) -> CharacterListResponse:
        return CharacterListResponse(
            characters=self.repository.list(include_archived=include_archived)
        )

    def get(self, character_id: str, *, include_archived: bool = False) -> CharacterProfile:
        profile = self.repository.get(character_id, include_archived=include_archived)
        if profile is None:
            raise CharacterNotFoundError(character_id)
        return profile

    def create(self, request: CreateCharacterRequest) -> CharacterProfile:
        asset = self._validate_voice_asset(request.default_voice_asset_id)
        if asset is not None and request.default_voice_asset_id != asset.id:
            request = request.model_copy(update={"default_voice_asset_id": asset.id})
        result = self.repository.create(request)
        _invalidate_snapshot_caches(result.id)
        return result

    def update(self, character_id: str, request: UpdateCharacterRequest) -> CharacterProfile:
        if request.default_voice_asset_id is not None:
            asset = self._validate_voice_asset(request.default_voice_asset_id)
            if asset is not None and request.default_voice_asset_id != asset.id:
                request = request.model_copy(update={"default_voice_asset_id": asset.id})
        result = self.repository.update(character_id, request)
        _invalidate_snapshot_caches(character_id)
        return result

    def archive(self, character_id: str) -> ArchiveCharacterResponse:
        result = ArchiveCharacterResponse(character=self.repository.archive(character_id))
        _invalidate_snapshot_caches(character_id)
        return result

    def versions(self, character_id: str) -> CharacterVersionListResponse:
        return CharacterVersionListResponse(versions=self.repository.versions(character_id))

    def resolve_snapshot(self, character_id: str):
        snapshot = self.get(character_id).snapshot()
        _publish_snapshot(snapshot)
        return snapshot

    def resolve_voice_asset(self, asset_id: str | None) -> AssetRecord | None:
        """Resolve a voice from the shared store or canonical clone directory.

        ``/api/voice-library`` reads ``resources/voice_clones`` directly. Character
        validation must use the same source even when a manifest-backed store in a
        different process has not imported that record. A unique case-insensitive
        match also repairs older profiles that preserved speaker casing in the
        governed asset ID. The returned asset ID remains canonical, while exact TTS
        speaker casing continues to come from asset metadata.
        """

        normalized_id = str(asset_id or "").strip()
        if not normalized_id:
            return None

        candidates: dict[str, AssetRecord] = {}
        try:
            for item in iter_assets(self.asset_store_factory()):
                candidates.setdefault(item.id, item)
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning(
                "[Character Voice] shared asset discovery failed asset_id=%s error=%s",
                normalized_id,
                exc,
            )
        try:
            for item in discover_canonical_voice_clone_assets():
                candidates.setdefault(item.id, item)
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning(
                "[Character Voice] canonical voice discovery failed asset_id=%s error=%s",
                normalized_id,
                exc,
            )

        exact = candidates.get(normalized_id)
        if exact is not None:
            return exact
        folded_id = normalized_id.casefold()
        matches = [item for item in candidates.values() if item.id.casefold() == folded_id]
        return matches[0] if len(matches) == 1 else None

    def validate_voice_for_use(self, asset_id: str, use: str = "character") -> None:
        del use
        asset = self.resolve_voice_asset(asset_id)
        if asset is None:
            raise CharacterVoiceAssetError(f"voice asset not found: {asset_id}")
        # Voice governance is automatic for every local clone. Resolving directly
        # from the canonical asset avoids a second manifest-only lookup that can
        # disagree with /api/voice-library across processes.
        governance_from_asset(asset)

    def _validate_voice_asset(self, asset_id: str | None) -> AssetRecord | None:
        if not asset_id:
            return None
        asset = self.resolve_voice_asset(asset_id)
        if asset is None:
            raise CharacterVoiceAssetError(f"voice asset not found: {asset_id}")
        if asset.type != AssetType.VOICE_PROFILE:
            raise CharacterVoiceAssetError(
                f"asset is not a voice profile: {asset_id} ({asset.type.value})"
            )
        if asset.mime_type == "audio/wav" and not asset_available(asset):
            raise CharacterVoiceAssetError(f"voice profile audio is missing: {asset.id}")
        self.validate_voice_for_use(asset.id, "character")
        return asset


def default_character_service() -> CharacterService:
    return CharacterService()


__all__ = [
    "CHARACTER_SNAPSHOT_OBSERVERS",
    "CharacterSnapshotObserver",
    "CharacterConflictError",
    "CharacterNotFoundError",
    "CharacterService",
    "CharacterVoiceAssetError",
    "default_character_service",
]
