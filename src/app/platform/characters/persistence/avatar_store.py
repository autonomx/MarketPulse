from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.platform.characters.avatar_models import CharacterAvatarPack, UpsertCharacterAvatarPackRequest
from app.platform.characters.repository import CharacterConflictError

from app.persistence.database import PostgresDatabase, default_database
from app.persistence.module_repositories import PostgresModuleRecordRepository
from app.persistence.document_schemas import register_document_schema
from app.security.tenant_context import RequestTenant


_MODULE = "character-avatar"
_RECORD_TYPE = "avatar-pack"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()



class PostgresCharacterAvatarRepositoryAdapter:
    """Tenant-scoped avatar-pack repository over bounded PostgreSQL documents."""
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def get(self, character_id: str) -> CharacterAvatarPack | None:
        with self.database.connection() as connection:
            payload = PostgresModuleRecordRepository(connection).payload(
                self.context, module=_MODULE, record_type=_RECORD_TYPE, record_id=character_id,
            )
        return CharacterAvatarPack.model_validate(payload) if payload is not None else None

    def upsert(
        self,
        character_id: str,
        request: UpsertCharacterAvatarPackRequest,
    ) -> CharacterAvatarPack:
        with self.database.transaction() as connection:
            current = self._locked_get(connection, character_id)
            current_version = current.version if current else 0
            if (
                request.expected_version is not None
                and request.expected_version != current_version
            ):
                raise CharacterConflictError(
                    "avatar pack version conflict: "
                    f"expected {request.expected_version}, current {current_version}"
                )
            now = _utcnow()
            pack = CharacterAvatarPack(
                character_id=character_id,
                version=current_version + 1,
                render_mode=request.render_mode,
                renderer=request.renderer,
                rig_asset_id=request.rig_asset_id,
                base_asset_id=request.base_asset_id,
                mouth_frames=dict(request.mouth_frames),
                blink_frames=dict(request.blink_frames),
                expression_frames=dict(request.expression_frames),
                outfit_frames=dict(request.outfit_frames),
                background_asset_ids=dict(request.background_asset_ids),
                active_outfit=request.active_outfit,
                active_background=request.active_background,
                mouth_anchor=dict(request.mouth_anchor),
                created_at=current.created_at if current else now,
                updated_at=now,
            )
            self._write(connection, pack)
        return pack

    def import_pack(
        self,
        pack: CharacterAvatarPack,
        *,
        replace: bool = False,
    ) -> CharacterAvatarPack:
        """Import an exact verified pack, refusing non-identical replacement by default."""

        with self.database.transaction() as connection:
            current = self._locked_get(connection, pack.character_id)
            if current is not None:
                if current == pack:
                    return current
                if not replace:
                    raise CharacterConflictError(
                        f"avatar pack already exists: {pack.character_id}"
                    )
            self._write(connection, pack)
        return pack

    def delete(self, character_id: str) -> bool:
        with self.database.transaction() as connection:
            return PostgresModuleRecordRepository(connection).delete(
                self.context, module=_MODULE, record_type=_RECORD_TYPE, record_id=character_id,
            )

    def _locked_get(self, connection: Any, character_id: str) -> CharacterAvatarPack | None:
        payload = PostgresModuleRecordRepository(connection).payload(
            self.context, module=_MODULE, record_type=_RECORD_TYPE, record_id=character_id, lock=True,
        )
        return CharacterAvatarPack.model_validate(payload) if payload is not None else None

    def _write(self, connection: Any, pack: CharacterAvatarPack) -> None:
        PostgresModuleRecordRepository(connection).upsert(
            self.context, module=_MODULE, record_type=_RECORD_TYPE, record_id=pack.character_id,
            payload=pack.model_dump(mode="json"),
        )

__all__ = ["PostgresCharacterAvatarRepositoryAdapter"]


# Document shapes (WP-5.9).
register_document_schema(_MODULE, _RECORD_TYPE, CharacterAvatarPack)


def production_avatar_repository() -> PostgresCharacterAvatarRepositoryAdapter:
    from app.persistence.repository_registry import register_feature_repositories

    register_feature_repositories("characters")
    return PostgresCharacterAvatarRepositoryAdapter()
