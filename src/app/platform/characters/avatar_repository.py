"""Versioned character avatar-pack compatibility repository.

PostgreSQL asset/character metadata is authoritative in production. Tests use a
process-local in-memory repository; no SQLite table remains.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .avatar_models import CharacterAvatarPack, UpsertCharacterAvatarPackRequest
from .repository import CharacterConflictError, default_character_db_path


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    packs: dict[str, CharacterAvatarPack] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state(path: str | Path | None) -> _State:
    key = str(path or default_character_db_path())
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class CharacterAvatarRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_character_db_path()
        self._state = _state(db_path)

    def get(self, character_id: str) -> CharacterAvatarPack | None:
        with self._state.lock:
            value = self._state.packs.get(character_id)
            return deepcopy(value) if value is not None else None

    def upsert(
        self,
        character_id: str,
        request: UpsertCharacterAvatarPackRequest,
    ) -> CharacterAvatarPack:
        now = _utcnow()
        with self._state.lock:
            current = self._state.packs.get(character_id)
            if request.expected_version is not None:
                current_version = current.version if current else 0
                if request.expected_version != current_version:
                    raise CharacterConflictError(
                        f"avatar pack version conflict: expected {request.expected_version}, current {current_version}"
                    )
            pack = CharacterAvatarPack(
                character_id=character_id,
                version=(current.version + 1) if current else 1,
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
            self._state.packs[character_id] = deepcopy(pack)
            return deepcopy(pack)

    def delete(self, character_id: str) -> bool:
        with self._state.lock:
            return self._state.packs.pop(character_id, None) is not None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["CharacterAvatarRepository"]
