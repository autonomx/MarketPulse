"""Character repository compatibility boundary.

PostgreSQL is installed for production. Provider-free tests use a deterministic
in-memory repository; no SQLite schema or connection remains.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import re
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    CharacterProfile,
    CharacterProfileVersion,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)

CHARACTER_SCHEMA_VERSION = 1


class CharacterNotFoundError(KeyError):
    pass


class CharacterConflictError(RuntimeError):
    pass


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    profiles: dict[str, CharacterProfile] = field(default_factory=dict)
    versions: dict[str, list[CharacterProfileVersion]] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def default_character_db_path() -> Path:
    """Return a stable test-double namespace without opening the legacy file.

    Existing tests and local compatibility callers already provide
    ``OMNIX_CHARACTER_DB_PATH`` to isolate one application instance from
    another. The in-memory repository uses that value only as a namespace key;
    it never reads or writes the path.
    """

    override = (_env_str("OMNIX_CHARACTER_DB_PATH") or "").strip()
    return Path(override) if override else Path(":memory:characters")


def _state(path: str | Path | None) -> _State:
    key = str(path or default_character_db_path())
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class InMemoryCharacterRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_character_db_path()
        self._state = _state(self.db_path)

    def create(self, request: CreateCharacterRequest) -> CharacterProfile:
        now = _utcnow()
        character_id = _normalize_character_id(request.id or request.display_name)
        profile = CharacterProfile(
            id=character_id,
            display_name=request.display_name.strip(),
            description=request.description.strip(),
            personality_prompt=request.personality_prompt.strip(),
            default_greeting=request.default_greeting.strip(),
            default_voice_asset_id=request.default_voice_asset_id,
            speech_style=dict(request.speech_style),
            identity_policy=dict(request.identity_policy),
            shared_memory_policy=dict(request.shared_memory_policy),
            active_version=1,
            enabled=request.enabled,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._state.lock:
            if character_id in self._state.profiles:
                raise CharacterConflictError(f"character already exists: {character_id}")
            self._state.profiles[character_id] = deepcopy(profile)
            self._state.versions[character_id] = [_profile_version(profile)]
        return deepcopy(profile)

    def get(self, character_id: str, *, include_archived: bool = False) -> CharacterProfile | None:
        with self._state.lock:
            value = self._state.profiles.get(character_id)
            if value is None or (not include_archived and value.status != "active"):
                return None
            return deepcopy(value)

    def list(self, *, include_archived: bool = False) -> list[CharacterProfile]:
        with self._state.lock:
            values = [
                item
                for item in self._state.profiles.values()
                if include_archived or item.status == "active"
            ]
            values.sort(key=lambda item: (item.display_name.casefold(), item.id))
            return deepcopy(values)

    def update(self, character_id: str, request: UpdateCharacterRequest) -> CharacterProfile:
        with self._state.lock:
            current = self._state.profiles.get(character_id)
            if current is None or current.status != "active":
                raise CharacterNotFoundError(character_id)
            if current.active_version != request.expected_version:
                raise CharacterConflictError(
                    f"character version conflict: expected {request.expected_version}, current {current.active_version}"
                )
            changes = request.model_dump(exclude={"expected_version"}, exclude_none=True)
            if changes.pop("clear_default_voice", False):
                changes["default_voice_asset_id"] = None
            if not changes:
                return deepcopy(current)
            payload = current.model_dump(mode="python")
            payload.update(changes)
            payload["active_version"] = current.active_version + 1
            payload["updated_at"] = _utcnow()
            updated = CharacterProfile(**payload)
            self._state.profiles[character_id] = deepcopy(updated)
            self._state.versions.setdefault(character_id, []).append(_profile_version(updated))
            return deepcopy(updated)

    def archive(self, character_id: str) -> CharacterProfile:
        with self._state.lock:
            current = self._state.profiles.get(character_id)
            if current is None:
                raise CharacterNotFoundError(character_id)
            archived = current.model_copy(
                update={"status": "archived", "enabled": False, "updated_at": _utcnow()}
            )
            self._state.profiles[character_id] = deepcopy(archived)
            return deepcopy(archived)

    def versions(self, character_id: str) -> list[CharacterProfileVersion]:
        with self._state.lock:
            if character_id not in self._state.profiles:
                raise CharacterNotFoundError(character_id)
            values = list(self._state.versions.get(character_id, []))
            values.sort(key=lambda item: item.version, reverse=True)
            return deepcopy(values)



CharacterRepository = InMemoryCharacterRepository


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_character_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        normalized = f"character-{uuid.uuid4().hex[:12]}"
    return normalized[:160].rstrip("-")


def _profile_version(profile: CharacterProfile) -> CharacterProfileVersion:
    return CharacterProfileVersion(
        character_id=profile.id,
        version=profile.active_version,
        display_name=profile.display_name,
        description=profile.description,
        personality_prompt=profile.personality_prompt,
        default_greeting=profile.default_greeting,
        default_voice_asset_id=profile.default_voice_asset_id,
        speech_style=dict(profile.speech_style),
        identity_policy=dict(profile.identity_policy),
        shared_memory_policy=dict(profile.shared_memory_policy),
        created_at=profile.updated_at,
    )
