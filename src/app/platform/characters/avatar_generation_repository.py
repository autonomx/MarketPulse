"""Multi-job character-avatar generation batch compatibility repository.

Production batches are PostgreSQL job/module records. Provider-free tests use a
process-local in-memory repository; no SQLite schema remains.
"""
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .avatar_generation_models import (
    AvatarGenerationStatus,
    CharacterAvatarGenerationBatch,
    CreateCharacterAvatarGenerationRequest,
)
from .repository import default_character_db_path


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    batches: dict[str, CharacterAvatarGenerationBatch] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state(path: str | Path | None) -> _State:
    key = str(path or default_character_db_path())
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class CharacterAvatarGenerationRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_character_db_path()
        self._state = _state(db_path)

    def create(
        self,
        character_id: str,
        request: CreateCharacterAvatarGenerationRequest,
        base_job_id: str,
    ) -> CharacterAvatarGenerationBatch:
        now = _utcnow()
        batch = CharacterAvatarGenerationBatch(
            id=f"avatar-generation:{uuid.uuid4().hex}",
            character_id=character_id,
            status="generating_base",
            request=request,
            base_job_id=base_job_id,
            created_at=now,
            updated_at=now,
        )
        with self._state.lock:
            self._state.batches[batch.id] = deepcopy(batch)
        return deepcopy(batch)

    def get(self, batch_id: str) -> CharacterAvatarGenerationBatch | None:
        with self._state.lock:
            value = self._state.batches.get(batch_id)
            return deepcopy(value) if value is not None else None

    def list(self, character_id: str) -> list[CharacterAvatarGenerationBatch]:
        with self._state.lock:
            values = [item for item in self._state.batches.values() if item.character_id == character_id]
            values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
            return deepcopy(values)

    def update(
        self,
        batch_id: str,
        *,
        status: AvatarGenerationStatus | None = None,
        variant_job_ids: dict[str, str] | None = None,
        asset_ids: dict[str, str] | None = None,
        avatar_pack_version: int | None = None,
        error: str | None = None,
    ) -> CharacterAvatarGenerationBatch:
        with self._state.lock:
            current = self._state.batches.get(batch_id)
            if current is None:
                raise KeyError(batch_id)
            updated = current.model_copy(
                update={
                    "status": status or current.status,
                    "variant_job_ids": dict(variant_job_ids) if variant_job_ids is not None else current.variant_job_ids,
                    "asset_ids": dict(asset_ids) if asset_ids is not None else current.asset_ids,
                    "avatar_pack_version": avatar_pack_version if avatar_pack_version is not None else current.avatar_pack_version,
                    "error": error if error is not None else current.error,
                    "updated_at": _utcnow(),
                }
            )
            self._state.batches[batch_id] = deepcopy(updated)
            return deepcopy(updated)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["CharacterAvatarGenerationRepository"]
