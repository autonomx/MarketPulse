"""Generate and persist expanded viseme frames for an existing character avatar pack.

Production batches and jobs use PostgreSQL. Provider-free tests use an in-memory
batch repository; no SQLite schema or connection remains.
"""
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.jobs import CreateJobRequest, JobStatus, ResourceClass, default_job_store

from .avatar_models import CharacterAvatarPack, UpsertCharacterAvatarPackRequest
from .avatar_service import CharacterAvatarService, default_character_avatar_service
from .repository import default_character_db_path
from .service import CharacterService, default_character_service
from app.prompts import prompt_template


VISEME_PROMPT_TEMPLATE = prompt_template(
    'characters.avatar_viseme_generation.viseme_prompt', "1",
    (
        'A previous attempt was rejected for excessive mouth or teeth movement. Make this '
        'attempt substantially subtler and closer to the closed-mouth portrait. '
    ),
)

VISEME_PROMPT_2_TEMPLATE = prompt_template(
    'characters.avatar_viseme_generation.viseme_prompt_2', "1",
    (
        'Using the supplied canonical portrait of {display_name}, preserve the exact identity, '
        'crop, head position, jawline, chin, cheeks, nose, eyes, hair, clothing, lighting, and '
        'background. {retry_direction}Change only the lips and a minimal amount of inner mouth '
        'to a neutral {articulation_percent}% articulation toward {description}. This is '
        'ordinary quiet conversational speech, not an emotional expression. Keep the jaw almost '
        'fixed, keep the lip corners close to their original position, expose little or no '
        'teeth, and never create a smile, grin, laugh, shout, scream, or dramatic open mouth. '
        'All pixels outside the immediate lip area should remain visually unchanged. No text or '
        'watermark.'
    ),
)


VisemeGenerationStatus = Literal["generating", "completed", "failed"]

_VISEME_PROMPTS = {
    "A": "small vertical opening used for an ah sound",
    "E": "slightly spread lips used for an ee or eh sound",
    "O": "gently rounded lips used for an oh sound",
    "U": "small rounded pursed lips used for an oo sound",
    "MBP": "naturally closed lips for m, b, or p",
    "FV": "upper teeth lightly touching the lower lip for f or v",
    "L": "slightly parted lips with the tongue subtly raised for l",
    "WQ": "small forward rounded lips for w or q",
    "other": "neutral minimally parted consonant lips",
}
_VISEME_MAX_ARTICULATION = {
    "A": 60,
    "E": 50,
    "O": 55,
    "U": 32,
    "MBP": 100,
    "FV": 35,
    "L": 45,
    "WQ": 32,
    "other": 30,
}
_VISEME_PHASES = (
    ("soft", 0.25),
    ("medium", 0.5),
    ("strong", 0.75),
    ("peak", 1.0),
)
_MAX_ACTIVE_VISEME_JOBS = 2
_MAX_QUALITY_ATTEMPTS = 3


class CharacterVisemeGenerationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    character_id: str
    status: VisemeGenerationStatus
    job_ids: dict[str, str] = Field(default_factory=dict)
    asset_ids: dict[str, str] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)
    quality_fallbacks: dict[str, str] = Field(default_factory=dict)
    avatar_pack_version: int | None = None
    error: str = ""
    created_at: str
    updated_at: str


@dataclass
class _State:
    lock: threading.RLock = field(default_factory=threading.RLock)
    batches: dict[str, CharacterVisemeGenerationBatch] = field(default_factory=dict)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.RLock()


def _state(path: str | Path | None) -> _State:
    key = str(path or default_character_db_path())
    with _STATES_LOCK:
        return _STATES.setdefault(key, _State())


class CharacterVisemeGenerationRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_character_db_path()
        self._state = _state(db_path)

    def create(self, character_id: str, job_ids: dict[str, str]) -> CharacterVisemeGenerationBatch:
        now = _utcnow()
        batch = CharacterVisemeGenerationBatch(
            id=f"avatar-visemes:{uuid.uuid4().hex}",
            character_id=character_id,
            status="generating",
            job_ids=dict(job_ids),
            created_at=now,
            updated_at=now,
        )
        with self._state.lock:
            self._state.batches[batch.id] = deepcopy(batch)
        return deepcopy(batch)

    def get(self, batch_id: str) -> CharacterVisemeGenerationBatch | None:
        with self._state.lock:
            value = self._state.batches.get(batch_id)
            return deepcopy(value) if value is not None else None

    def list(self, character_id: str) -> list[CharacterVisemeGenerationBatch]:
        with self._state.lock:
            values = [
                item
                for item in self._state.batches.values()
                if item.character_id == character_id
            ]
            values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
            return deepcopy(values)

    def update(
        self,
        batch_id: str,
        *,
        status: VisemeGenerationStatus | None = None,
        job_ids: dict[str, str] | None = None,
        asset_ids: dict[str, str] | None = None,
        attempts: dict[str, int] | None = None,
        quality_fallbacks: dict[str, str] | None = None,
        avatar_pack_version: int | None = None,
        error: str | None = None,
    ) -> CharacterVisemeGenerationBatch:
        with self._state.lock:
            current = self._state.batches.get(batch_id)
            if current is None:
                raise KeyError(batch_id)
            updated = current.model_copy(
                update={
                    "status": status or current.status,
                    "job_ids": dict(job_ids) if job_ids is not None else current.job_ids,
                    "asset_ids": (
                        dict(asset_ids) if asset_ids is not None else current.asset_ids
                    ),
                    "attempts": dict(attempts) if attempts is not None else current.attempts,
                    "quality_fallbacks": (
                        dict(quality_fallbacks)
                        if quality_fallbacks is not None
                        else current.quality_fallbacks
                    ),
                    "avatar_pack_version": (
                        avatar_pack_version
                        if avatar_pack_version is not None
                        else current.avatar_pack_version
                    ),
                    "error": error if error is not None else current.error,
                    "updated_at": _utcnow(),
                }
            )
            self._state.batches[batch_id] = deepcopy(updated)
            return deepcopy(updated)


class CharacterVisemeGenerationService:
    def __init__(
        self,
        repository: CharacterVisemeGenerationRepository | None = None,
        *,
        character_service: CharacterService | None = None,
        avatar_service: CharacterAvatarService | None = None,
        job_store: Any | None = None,
    ) -> None:
        if repository is None:
            from app.persistence.runtime import uses_postgresql_runtime

            if uses_postgresql_runtime():
                # Feature-owned adapter; the composition root is not imported here.
                from app.platform.characters.persistence.avatar_generation_repository import (
                    PostgresCharacterVisemeGenerationRepositoryAdapter,
                )

                repository = PostgresCharacterVisemeGenerationRepositoryAdapter()
            else:
                repository = CharacterVisemeGenerationRepository()
        self.repository = repository
        self.character_service = character_service or default_character_service()
        self.avatar_service = avatar_service or default_character_avatar_service()
        self.job_store = job_store or default_job_store()

    def create(self, character_id: str) -> CharacterVisemeGenerationBatch:
        self.character_service.get(character_id)
        self.avatar_service.get(character_id)
        batch = self.repository.create(character_id, {})
        return self.get(batch.id)

    def ensure(self, character_id: str) -> CharacterVisemeGenerationBatch | None:
        pack = self.avatar_service.get(character_id)
        batches = self.repository.list(character_id)
        active = next(
            (batch for batch in batches if batch.status == "generating"),
            None,
        )
        if active:
            return active
        if pack.render_mode == "viseme" and _has_phased_visemes(pack):
            return batches[0] if batches else None
        return self.create(character_id)

    def reconcile_character(self, character_id: str) -> None:
        for batch in self.repository.list(character_id):
            if batch.status == "generating":
                self.get(batch.id)

    def _create_job(
        self,
        batch: CharacterVisemeGenerationBatch,
        frame_key: str,
        viseme: str,
        description: str,
        articulation_percent: int,
        attempt: int,
    ) -> str:
        character = self.character_service.get(batch.character_id)
        pack = self.avatar_service.get(batch.character_id)
        reference_asset_id = pack.mouth_frames.get("closed") or pack.base_asset_id
        if not reference_asset_id:
            raise ValueError("character avatar has no canonical portrait")
        job = self.job_store.create_job(
            CreateJobRequest(
                owner_id=f"character:{batch.character_id}",
                module="character-avatar",
                type="image.generate",
                resource_class=ResourceClass.GPU_IMAGE,
                input_payload={
                    "prompt": _viseme_prompt(
                        character.display_name,
                        description,
                        articulation_percent,
                        attempt,
                    ),
                    "negative_prompt": (
                        "text, watermark, face change, hair change, clothing change, "
                        "camera shift, background change, lighting change, extra person, "
                        "exaggerated open mouth, scream, shouting, laugh, grin, smile, "
                        "stretched lips, oversized teeth, exposed gums, distorted jaw, "
                        "lower-face deformation, cheek deformation, chin movement"
                    ),
                    "width": 768,
                    "height": 768,
                    "style": "neutral conservative character speech articulation frame",
                    "reference_asset_ids": [reference_asset_id],
                    "no_cache": True,
                    "metadata": {
                        "character_id": batch.character_id,
                        "avatar_viseme": frame_key,
                        "avatar_viseme_base": viseme,
                        "avatar_viseme_articulation_percent": articulation_percent,
                        "avatar_viseme_quality_attempt": attempt,
                        "avatar_mouth_anchor": dict(pack.mouth_anchor),
                    },
                },
                compat={
                    "character_id": batch.character_id,
                    "avatar_viseme": frame_key,
                    "avatar_viseme_base": viseme,
                    "avatar_viseme_quality_attempt": attempt,
                },
            )
        )
        return job.id

    def get(self, batch_id: str) -> CharacterVisemeGenerationBatch:
        batch = self.repository.get(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if batch.status in {"completed", "failed"}:
            return batch

        specs = _viseme_frame_specs()
        pack = self.avatar_service.get(batch.character_id)
        assets = dict(batch.asset_ids)
        job_ids = dict(batch.job_ids)
        attempts = dict(batch.attempts)
        quality_fallbacks = dict(batch.quality_fallbacks)
        active_jobs = 0
        retry_specs: list[tuple[str, str, str, int]] = []

        for frame_key, viseme, description, articulation_percent in specs:
            if frame_key in assets:
                continue
            job_id = job_ids.get(frame_key)
            if not job_id:
                continue
            job = self.job_store.get_job(job_id)
            if job is None:
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=assets,
                    attempts=attempts,
                    quality_fallbacks=quality_fallbacks,
                    error=f"viseme job missing: {frame_key}",
                )
            if job.status in {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE}:
                if _is_quality_rejection(job):
                    if attempts.get(frame_key, 1) < _MAX_QUALITY_ATTEMPTS:
                        retry_specs.append(
                            (frame_key, viseme, description, articulation_percent)
                        )
                        continue
                    fallback_asset_id = _quality_fallback_asset(
                        frame_key,
                        viseme,
                        assets,
                        pack,
                    )
                    if fallback_asset_id:
                        assets[frame_key] = fallback_asset_id
                        quality_fallbacks[frame_key] = fallback_asset_id
                        continue
                message = (
                    job.error.message
                    if job.error and job.error.message
                    else f"viseme generation failed: {frame_key}"
                )
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=assets,
                    attempts=attempts,
                    quality_fallbacks=quality_fallbacks,
                    error=message,
                )
            if job.status != JobStatus.COMPLETED:
                active_jobs += 1
                continue
            asset_id = next(
                (
                    str(ref.get("asset_id") or "")
                    for ref in job.output_refs
                    if ref.get("asset_id")
                ),
                "",
            )
            if not asset_id:
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=assets,
                    attempts=attempts,
                    quality_fallbacks=quality_fallbacks,
                    error=f"viseme returned no asset: {frame_key}",
                )
            assets[frame_key] = asset_id

        jobs_created = False
        for frame_key, viseme, description, articulation_percent in retry_specs:
            if active_jobs >= _MAX_ACTIVE_VISEME_JOBS:
                break
            attempt = attempts.get(frame_key, 1) + 1
            job_ids[frame_key] = self._create_job(
                batch,
                frame_key,
                viseme,
                description,
                articulation_percent,
                attempt,
            )
            attempts[frame_key] = attempt
            active_jobs += 1
            jobs_created = True

        for frame_key, viseme, description, articulation_percent in specs:
            if active_jobs >= _MAX_ACTIVE_VISEME_JOBS:
                break
            if frame_key in assets or frame_key in job_ids:
                continue
            attempt = 1
            job_ids[frame_key] = self._create_job(
                batch,
                frame_key,
                viseme,
                description,
                articulation_percent,
                attempt,
            )
            attempts[frame_key] = attempt
            active_jobs += 1
            jobs_created = True

        if jobs_created or active_jobs or retry_specs:
            return self.repository.update(
                batch.id,
                job_ids=job_ids,
                asset_ids=assets,
                attempts=attempts,
                quality_fallbacks=quality_fallbacks,
            )
        if any(frame_key not in assets for frame_key, *_rest in specs):
            return self.repository.update(
                batch.id,
                status="failed",
                job_ids=job_ids,
                asset_ids=assets,
                attempts=attempts,
                quality_fallbacks=quality_fallbacks,
                error="viseme generation stopped before every phased frame completed",
            )
        return self._finalize(batch, assets, attempts, quality_fallbacks)

    def _finalize(
        self,
        batch: CharacterVisemeGenerationBatch,
        assets: dict[str, str],
        attempts: dict[str, int],
        quality_fallbacks: dict[str, str],
    ) -> CharacterVisemeGenerationBatch:
        current = self.avatar_service.get(batch.character_id)
        mouth_frames = {
            key: value
            for key, value in current.mouth_frames.items()
            if not _is_precise_viseme_frame_key(key)
        }
        mouth_frames.update(assets)
        mouth_frames.setdefault(
            "silence",
            mouth_frames.get("closed") or current.base_asset_id or "",
        )
        pack = self.avatar_service.upsert(
            batch.character_id,
            UpsertCharacterAvatarPackRequest(
                expected_version=current.version,
                render_mode="viseme",
                renderer=current.renderer,
                rig_asset_id=current.rig_asset_id,
                base_asset_id=current.base_asset_id,
                mouth_frames=mouth_frames,
                blink_frames=current.blink_frames,
                expression_frames=current.expression_frames,
                outfit_frames=current.outfit_frames,
                background_asset_ids=current.background_asset_ids,
                active_outfit=current.active_outfit,
                active_background=current.active_background,
                mouth_anchor=current.mouth_anchor,
            ),
        )
        return self.repository.update(
            batch.id,
            status="completed",
            asset_ids=assets,
            attempts=attempts,
            quality_fallbacks=quality_fallbacks,
            avatar_pack_version=pack.version,
            error="",
        )


def _viseme_frame_specs() -> list[tuple[str, str, str, int]]:
    specs: list[tuple[str, str, str, int]] = []
    for viseme, description in _VISEME_PROMPTS.items():
        maximum = _VISEME_MAX_ARTICULATION[viseme]
        for phase, fraction in _VISEME_PHASES:
            frame_key = viseme if phase == "peak" else f"{viseme}_{phase}"
            articulation_percent = max(5, round(maximum * fraction))
            specs.append((frame_key, viseme, description, articulation_percent))
    return specs


def _viseme_prompt(
    display_name: str,
    description: str,
    articulation_percent: int,
    attempt: int,
) -> str:
    retry_direction = ""
    if attempt > 1:
        retry_direction = (
            VISEME_PROMPT_TEMPLATE.text
        )
    return (
        VISEME_PROMPT_2_TEMPLATE.format(
            display_name=display_name,
            retry_direction=retry_direction,
            articulation_percent=articulation_percent,
            description=description,
        )
    )


def _is_quality_rejection(job: Any) -> bool:
    error = getattr(job, "error", None)
    if error is None:
        return False
    code = str(getattr(error, "code", "") or "")
    message = str(getattr(error, "message", "") or "")
    return code == "avatar_frame_quality_rejected" or message.startswith(
        "avatar_frame_quality_rejected:"
    )


def _quality_fallback_asset(
    frame_key: str,
    viseme: str,
    assets: dict[str, str],
    pack: CharacterAvatarPack,
) -> str:
    ordered_keys = [
        key
        for key, candidate_viseme, *_rest in _viseme_frame_specs()
        if candidate_viseme == viseme
    ]
    try:
        index = ordered_keys.index(frame_key)
    except ValueError:
        index = 0
    for candidate in reversed(ordered_keys[:index]):
        asset_id = assets.get(candidate)
        if asset_id:
            return asset_id
    return pack.mouth_frames.get("closed") or pack.base_asset_id or ""


def _is_precise_viseme_frame_key(frame_key: str) -> bool:
    base = str(frame_key or "").split("_", 1)[0]
    return base in _VISEME_PROMPTS


def _has_phased_visemes(pack: CharacterAvatarPack) -> bool:
    return all(
        frame_key in pack.mouth_frames and bool(pack.mouth_frames[frame_key])
        for frame_key, *_rest in _viseme_frame_specs()
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["CharacterVisemeGenerationBatch", "CharacterVisemeGenerationService"]
