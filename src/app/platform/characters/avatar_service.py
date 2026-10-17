"""Character avatar-pack management backed by shared assets."""
from __future__ import annotations

from app.assets.protocol import AssetStore
from collections.abc import Callable

from app.assets.content import asset_available
from app.assets import AssetRecord, AssetType, default_asset_store

from .avatar_models import CharacterAvatarPack, UpsertCharacterAvatarPackRequest
from .avatar_repository import CharacterAvatarRepository
from .repository import CharacterNotFoundError
from .service import CharacterService, default_character_service


class CharacterAvatarAssetError(ValueError):
    pass


class CharacterAvatarService:
    def __init__(
        self,
        repository: CharacterAvatarRepository | None = None,
        *,
        character_service_factory: Callable[[], CharacterService] = default_character_service,
        asset_store_factory: Callable[[], AssetStore] = default_asset_store,
    ) -> None:
        if repository is None:
            from app.persistence.runtime import uses_postgresql_runtime
            if uses_postgresql_runtime():
                from app.platform.characters.persistence.avatar_store import production_avatar_repository
                repository = production_avatar_repository()
            else:
                repository = CharacterAvatarRepository()
        self.repository = repository
        self.character_service_factory = character_service_factory
        self.asset_store_factory = asset_store_factory

    def get(self, character_id: str) -> CharacterAvatarPack:
        self.character_service_factory().get(character_id, include_archived=True)
        pack = self.repository.get(character_id)
        if pack is None:
            raise CharacterNotFoundError(f"character avatar pack not found: {character_id}")
        return pack

    def optional_get(self, character_id: str) -> CharacterAvatarPack | None:
        """Return a pack when present without treating normal absence as an error."""
        self.character_service_factory().get(character_id, include_archived=True)
        return self.repository.get(character_id)

    def resolve(self, character_id: str | None) -> CharacterAvatarPack | None:
        if not character_id:
            return None
        return self.repository.get(character_id)

    def upsert(
        self,
        character_id: str,
        request: UpsertCharacterAvatarPackRequest,
    ) -> CharacterAvatarPack:
        self.character_service_factory().get(character_id, include_archived=True)
        self._validate_assets(request)
        return self.repository.upsert(character_id, request)

    def delete(self, character_id: str) -> bool:
        self.character_service_factory().get(character_id, include_archived=True)
        return self.repository.delete(character_id)

    def _validate_assets(self, request: UpsertCharacterAvatarPackRequest) -> None:
        image_asset_ids = {
            asset_id
            for asset_id in [
                request.base_asset_id,
                *request.mouth_frames.values(),
                *request.blink_frames.values(),
                *request.expression_frames.values(),
                *request.outfit_frames.values(),
                *request.background_asset_ids.values(),
            ]
            if asset_id
        }
        if request.renderer == "sprite" and not image_asset_ids:
            raise CharacterAvatarAssetError("sprite avatar pack requires at least one image asset")
        store = self.asset_store_factory()
        for asset_id in sorted(image_asset_ids):
            asset = store.get_asset(asset_id)
            if asset is None:
                raise CharacterAvatarAssetError(f"avatar image asset not found: {asset_id}")
            if asset.type != AssetType.IMAGE:
                raise CharacterAvatarAssetError(
                    f"avatar asset is not an image: {asset_id} ({asset.type.value})"
                )
            _require_file(asset, "avatar image")

        if request.renderer != "sprite":
            rig_asset_id = request.rig_asset_id
            if not rig_asset_id:
                raise CharacterAvatarAssetError("rigged avatar pack requires a rig asset")
            rig_asset = store.get_asset(rig_asset_id)
            if rig_asset is None:
                raise CharacterAvatarAssetError(f"avatar rig asset not found: {rig_asset_id}")
            allowed_rig_types = {AssetType.EXPORT, AssetType.SETTINGS_ARTIFACT}
            if rig_asset.type not in allowed_rig_types:
                raise CharacterAvatarAssetError(
                    f"avatar rig asset has unsupported type: {rig_asset_id} ({rig_asset.type.value})"
                )
            _require_file(rig_asset, "avatar rig")


def _require_file(asset: AssetRecord, label: str) -> None:
    try:
        available = asset_available(asset)
    except OSError:
        available = False
    if not available:
        raise CharacterAvatarAssetError(f"{label} file is missing: {asset.id}")


def default_character_avatar_service() -> CharacterAvatarService:
    return CharacterAvatarService()


__all__ = [
    "CharacterAvatarAssetError",
    "CharacterAvatarService",
    "default_character_avatar_service",
]
