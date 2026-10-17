"""PostgreSQL-backed avatar generation batch repositories."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.platform.characters.avatar_generation_models import (
    CharacterAvatarGenerationBatch,
    CreateCharacterAvatarGenerationRequest,
)
from app.platform.characters.avatar_viseme_generation import CharacterVisemeGenerationBatch
from app.persistence.document_schemas import register_document_schema
from app.persistence.database import PostgresDatabase, default_database
from app.persistence.module_repositories import PostgresModuleRecordRepository
from app.security.tenant_context import RequestTenant


_MODULE = "character-avatar"
_AVATAR_BATCH_TYPE = "generation-batch"
_VISEME_BATCH_TYPE = "viseme-generation-batch"



def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresCharacterAvatarGenerationRepositoryAdapter:
    """Persist avatar generation batch state in tenant-scoped module records."""
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def create(
        self,
        character_id: str,
        request: CreateCharacterAvatarGenerationRequest,
        base_job_id: str,
    ) -> CharacterAvatarGenerationBatch:
        batch = CharacterAvatarGenerationBatch(
            id=f"avatar-generation:{uuid.uuid4().hex}",
            character_id=character_id,
            status="generating_base",
            request=request,
            base_job_id=base_job_id,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self.database.transaction() as connection:
            self._write(connection, _AVATAR_BATCH_TYPE, batch.id, batch)
        return batch

    def get(self, batch_id: str) -> CharacterAvatarGenerationBatch | None:
        payload = self._get(_AVATAR_BATCH_TYPE, batch_id)
        return CharacterAvatarGenerationBatch.model_validate(payload) if payload else None

    def list(self, character_id: str) -> list[CharacterAvatarGenerationBatch]:
        payloads = self._list(_AVATAR_BATCH_TYPE, character_id)
        return [CharacterAvatarGenerationBatch.model_validate(item) for item in payloads]

    def update(
        self,
        batch_id: str,
        *,
        status: str | None = None,
        variant_job_ids: dict[str, str] | None = None,
        asset_ids: dict[str, str] | None = None,
        avatar_pack_version: int | None = None,
        error: str | None = None,
    ) -> CharacterAvatarGenerationBatch:
        with self.database.transaction() as connection:
            current = self._locked_get(connection, _AVATAR_BATCH_TYPE, batch_id)
            if current is None:
                raise KeyError(batch_id)
            updated = CharacterAvatarGenerationBatch.model_validate(current).model_copy(
                update={
                    "status": status or current["status"],
                    "variant_job_ids": (
                        dict(variant_job_ids)
                        if variant_job_ids is not None
                        else current.get("variant_job_ids", {})
                    ),
                    "asset_ids": dict(asset_ids) if asset_ids is not None else current.get("asset_ids", {}),
                    "avatar_pack_version": (
                        avatar_pack_version
                        if avatar_pack_version is not None
                        else current.get("avatar_pack_version")
                    ),
                    "error": error if error is not None else current.get("error", ""),
                    "updated_at": _utcnow(),
                }
            )
            self._write(connection, _AVATAR_BATCH_TYPE, batch_id, updated)
        return updated

    def _get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            return PostgresModuleRecordRepository(connection).payload(
                self.context, module=_MODULE, record_type=record_type, record_id=record_id,
            )

    def _list(self, record_type: str, character_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            return PostgresModuleRecordRepository(connection).payloads_where(
                self.context, module=_MODULE, record_type=record_type,
                field="character_id", value=character_id, newest_first_by="created_at",
            )

    def _locked_get(self, connection: Any, record_type: str, record_id: str) -> dict[str, Any] | None:
        return PostgresModuleRecordRepository(connection).payload(
            self.context, module=_MODULE, record_type=record_type, record_id=record_id, lock=True,
        )

    def _write(self, connection: Any, record_type: str, record_id: str, record: Any) -> None:
        PostgresModuleRecordRepository(connection).upsert(
            self.context, module=_MODULE, record_type=record_type, record_id=record_id,
            payload=record.model_dump(mode="json"),
        )

class PostgresCharacterVisemeGenerationRepositoryAdapter:
    """Persist generated viseme batches in the shared module-record authority."""
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def create(
        self,
        character_id: str,
        job_ids: dict[str, str],
    ) -> CharacterVisemeGenerationBatch:
        batch = CharacterVisemeGenerationBatch(
            id=f"avatar-visemes:{uuid.uuid4().hex}",
            character_id=character_id,
            status="generating",
            job_ids=dict(job_ids),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self.database.transaction() as connection:
            self._write(connection, batch.id, batch)
        return batch

    def get(self, batch_id: str) -> CharacterVisemeGenerationBatch | None:
        with self.database.connection() as connection:
            payload = PostgresModuleRecordRepository(connection).payload(
                self.context, module=_MODULE, record_type=_VISEME_BATCH_TYPE, record_id=batch_id,
            )
        return CharacterVisemeGenerationBatch.model_validate(payload) if payload is not None else None

    def list(self, character_id: str) -> list[CharacterVisemeGenerationBatch]:
        with self.database.connection() as connection:
            payloads = PostgresModuleRecordRepository(connection).payloads_where(
                self.context, module=_MODULE, record_type=_VISEME_BATCH_TYPE,
                field="character_id", value=character_id, newest_first_by="created_at",
            )
        return [CharacterVisemeGenerationBatch.model_validate(item) for item in payloads]

    def update(
        self,
        batch_id: str,
        *,
        status: str | None = None,
        job_ids: dict[str, str] | None = None,
        asset_ids: dict[str, str] | None = None,
        attempts: dict[str, int] | None = None,
        quality_fallbacks: dict[str, str] | None = None,
        avatar_pack_version: int | None = None,
        error: str | None = None,
    ) -> CharacterVisemeGenerationBatch:
        with self.database.transaction() as connection:
            current = PostgresModuleRecordRepository(connection).payload(
                self.context, module=_MODULE, record_type=_VISEME_BATCH_TYPE, record_id=batch_id, lock=True,
            )
            if current is None:
                raise KeyError(batch_id)
            updated = CharacterVisemeGenerationBatch.model_validate(current).model_copy(
                update={
                    "status": status or current["status"],
                    "job_ids": dict(job_ids) if job_ids is not None else current.get("job_ids", {}),
                    "asset_ids": dict(asset_ids) if asset_ids is not None else current.get("asset_ids", {}),
                    "attempts": dict(attempts) if attempts is not None else current.get("attempts", {}),
                    "quality_fallbacks": (
                        dict(quality_fallbacks)
                        if quality_fallbacks is not None
                        else current.get("quality_fallbacks", {})
                    ),
                    "avatar_pack_version": (
                        avatar_pack_version
                        if avatar_pack_version is not None
                        else current.get("avatar_pack_version")
                    ),
                    "error": error if error is not None else current.get("error", ""),
                    "updated_at": _utcnow(),
                }
            )
            self._write(connection, batch_id, updated)
        return updated

    def _write(self, connection: Any, record_id: str, record: Any) -> None:
        PostgresModuleRecordRepository(connection).upsert(
            self.context, module=_MODULE, record_type=_VISEME_BATCH_TYPE, record_id=record_id,
            payload=record.model_dump(mode="json"),
        )

__all__ = [
    "PostgresCharacterAvatarGenerationRepositoryAdapter",
    "PostgresCharacterVisemeGenerationRepositoryAdapter",
]


# Document shapes (WP-5.9).
register_document_schema(_MODULE, _AVATAR_BATCH_TYPE, CharacterAvatarGenerationBatch)
register_document_schema(_MODULE, _VISEME_BATCH_TYPE, CharacterVisemeGenerationBatch)
