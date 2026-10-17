"""Image-generation orchestration for Character Mode live avatars."""
from __future__ import annotations

from app.assets.protocol import AssetStore
import hashlib
import re
from collections.abc import Callable
from typing import Any

from app.assets import AssetRecord, AssetType, default_asset_store, iter_assets
from app.platform.image.contracts import (
    ImageReferenceError,
    close_image_references,
    load_image_reference_assets,
)
from app.jobs import CreateJobRequest, JobRecord, JobStatus, ResourceClass, default_job_store

from .avatar_generation_models import (
    BackfillClonedVoiceCharactersRequest,
    BackfillClonedVoiceCharactersResponse,
    CharacterAvatarGenerationBatch,
    CharacterAvatarGenerationListResponse,
    ClonedVoiceCharacterBackfillItem,
    CreateCharacterAvatarGenerationRequest,
)
from .avatar_generation_repository import CharacterAvatarGenerationRepository
from .avatar_models import UpsertCharacterAvatarPackRequest
from .avatar_service import CharacterAvatarService, default_character_avatar_service
from .models import CreateCharacterRequest
from .repository import CharacterConflictError
from .service import CharacterService, default_character_service
from .voice_consent import VoiceConsentError, VoiceProfileGovernanceService
from app.prompts import prompt_template


VARIANT_PROMPTS_TEMPLATE = prompt_template(
    'characters.avatar_generation_service.variant_prompts', "1",
    (
        'Change only the clothing to: {strip}. Preserve face, hair, pose, camera, lighting, and '
        'mouth.'
    ),
)

VARIANT_PROMPTS_2_TEMPLATE = prompt_template(
    'characters.avatar_generation_service.variant_prompts_2', "1",
    'Create a clean matching background: {strip}. Do not include a person or text.',
)

BASE_PROMPT_TEMPLATE = prompt_template(
    'characters.avatar_generation_service.base_prompt', "1",
    (
        'Create one original fictional character portrait for {display_name}. '
        '{gender_direction}{custom}Front-facing head-and-shoulders composition, centered, eyes '
        'open, neutral relaxed expression, mouth fully closed, consistent hair and clothing, '
        'clean even lighting, no text. This canonical portrait will be reused as a locked '
        'reference for live-chat animation frames. Do not depict or imitate a real public person.'
    ),
)

UPLOADED_BASE_PROMPT_TEMPLATE = prompt_template(
    'characters.avatar_generation_service.uploaded_base_prompt', "1",
    (
        'Using the supplied user-provided image as the authoritative identity reference for '
        '{display_name}, create one faithful front-facing head-and-shoulders live-avatar '
        "portrait. Preserve the person's recognizable facial identity, skin tone, face "
        'proportions, hair, and other defining features. Do not replace them with a different '
        'person. {custom}Center the face, keep both eyes open, use a neutral relaxed expression '
        'and a fully closed mouth, with clean even lighting and no text. This canonical portrait '
        'will be reused as a locked reference for mouth, blink, expression, and viseme frames.'
    ),
)

VARIANT_PROMPT_TEMPLATE = prompt_template(
    'characters.avatar_generation_service.variant_prompt', "1",
    (
        'Using the supplied canonical portrait of {display_name}, preserve the exact identity, '
        'crop, head position, hair, clothing, lighting, and background. {change} Keep all '
        'unrelated visual details unchanged. No text or watermark.'
    ),
)


_VARIANT_PROMPTS: dict[str, str] = {
    "mouth_small": "Change only the mouth to a small slightly open speaking shape.",
    "mouth_medium": "Change only the mouth to a natural medium-open speaking shape.",
    "mouth_wide": "Change only the mouth to a wider energetic speaking shape.",
    "blink_closed": "Change only the eyelids so both eyes are naturally closed in a brief blink.",
    "expression_listening": "Give the character a subtle attentive listening expression; preserve identity and pose.",
    "expression_thinking": "Give the character a subtle thoughtful expression; preserve identity and pose.",
    "expression_happy": "Give the character a warm restrained smile; preserve identity and pose.",
}
_REFERENCE_NAME_PATTERN = re.compile(r"(?:^|[\s_-])(default|ref|reference|sample|test)(?:$|[\s_-])", re.IGNORECASE)


class CharacterAvatarGenerationNotFoundError(KeyError):
    pass


class CharacterAvatarGenerationService:
    def __init__(
        self,
        repository: CharacterAvatarGenerationRepository | None = None,
        *,
        character_service_factory: Callable[[], CharacterService] = default_character_service,
        avatar_service_factory: Callable[[], CharacterAvatarService] = default_character_avatar_service,
        job_store_factory: Callable[[], Any] = default_job_store,
        asset_store_factory: Callable[[], AssetStore] = default_asset_store,
    ) -> None:
        if repository is None:
            from app.persistence.runtime import uses_postgresql_runtime

            if uses_postgresql_runtime():
                # Feature-owned adapter; the composition root is not imported here.
                from app.platform.characters.persistence.avatar_generation_repository import (
                    PostgresCharacterAvatarGenerationRepositoryAdapter,
                )

                repository = PostgresCharacterAvatarGenerationRepositoryAdapter()
            else:
                repository = CharacterAvatarGenerationRepository()
        self.repository = repository
        self.character_service_factory = character_service_factory
        self.avatar_service_factory = avatar_service_factory
        self.job_store_factory = job_store_factory
        self.asset_store_factory = asset_store_factory

    def create(
        self,
        character_id: str,
        request: CreateCharacterAvatarGenerationRequest,
    ) -> CharacterAvatarGenerationBatch:
        character = self.character_service_factory().get(character_id)
        source_asset_id = request.source_asset_id.strip()
        reference_asset_ids: list[str] = []
        prompt = _base_prompt(
            character.display_name,
            request.appearance_prompt,
            str(character.speech_style.get("gender") or ""),
        )
        if source_asset_id:
            self._govern_uploaded_source(
                character.id,
                source_asset_id,
                consent_confirmed=request.source_image_consent_confirmed,
            )
            reference_asset_ids = [source_asset_id]
            prompt = _uploaded_base_prompt(character.display_name, request.appearance_prompt)
        job = self.job_store_factory().create_job(
            self._image_job_request(
                character_id=character.id,
                variant="base",
                prompt=prompt,
                request=request,
                reference_asset_ids=reference_asset_ids,
            )
        )
        return self.repository.create(character.id, request, job.id)

    def get(self, batch_id: str) -> CharacterAvatarGenerationBatch:
        batch = self.repository.get(batch_id)
        if batch is None:
            raise CharacterAvatarGenerationNotFoundError(batch_id)
        return self._reconcile(batch)

    def list(self, character_id: str) -> CharacterAvatarGenerationListResponse:
        self.character_service_factory().get(character_id, include_archived=True)
        return CharacterAvatarGenerationListResponse(
            batches=[self._reconcile(batch) for batch in self.repository.list(character_id)]
        )

    def backfill_cloned_voices(
        self,
        request: BackfillClonedVoiceCharactersRequest,
    ) -> BackfillClonedVoiceCharactersResponse:
        character_service = self.character_service_factory()
        avatar_service = self.avatar_service_factory()
        voices = sorted(
            iter_assets(self.asset_store_factory(), asset_type=AssetType.VOICE_PROFILE.value),
            key=lambda asset: (_voice_display_name(asset).lower(), asset.id),
        )
        existing = character_service.list(include_archived=True).characters
        by_voice = {
            profile.default_voice_asset_id: profile
            for profile in existing
            if profile.default_voice_asset_id
        }
        governance = VoiceProfileGovernanceService(asset_store_factory=self.asset_store_factory)
        items: list[ClonedVoiceCharacterBackfillItem] = []

        for voice in voices:
            display_name = _voice_display_name(voice)
            if not request.include_reference_profiles and _looks_like_reference_voice(display_name, voice.id):
                items.append(
                    ClonedVoiceCharacterBackfillItem(
                        voice_asset_id=voice.id,
                        display_name=display_name,
                        result="skipped",
                        reason="reference/default/test voice profiles are excluded by default",
                    )
                )
                continue
            try:
                governance.validate_use(voice.id, "character")
                governance.validate_use(voice.id, "live_call")
            except VoiceConsentError as exc:
                items.append(
                    ClonedVoiceCharacterBackfillItem(
                        voice_asset_id=voice.id,
                        display_name=display_name,
                        result="skipped",
                        reason=str(exc),
                    )
                )
                continue

            profile = by_voice.get(voice.id)
            created = False
            if profile is None:
                character_id = _available_character_id(display_name, voice.id, character_service)
                try:
                    profile = character_service.create(
                        CreateCharacterRequest(
                            id=character_id,
                            display_name=display_name,
                            description=(
                                "Character profile created from a governed cloned voice. "
                                "Identity, memory, voice, and avatar remain independently replaceable."
                            ),
                            personality_prompt=(
                                f"You are {display_name}, an original fictional conversational companion. "
                                "Be consistent, warm, and responsive. Never claim to be a real public person."
                            ),
                            default_greeting=f"Hello, I'm {display_name}. What would you like to talk about?",
                            default_voice_asset_id=voice.id,
                            speech_style={"expressiveness": "conversational", "emotion": "neutral"},
                        )
                    )
                    created = True
                    by_voice[voice.id] = profile
                except (CharacterConflictError, ValueError) as exc:
                    items.append(
                        ClonedVoiceCharacterBackfillItem(
                            voice_asset_id=voice.id,
                            display_name=display_name,
                            result="failed",
                            reason=str(exc),
                        )
                    )
                    continue

            assert profile is not None
            if avatar_service.resolve(profile.id) is not None:
                items.append(
                    ClonedVoiceCharacterBackfillItem(
                        voice_asset_id=voice.id,
                        display_name=display_name,
                        character_id=profile.id,
                        result="already_has_avatar",
                        reason="character already has an avatar pack",
                    )
                )
                continue

            generation_batch_id: str | None = None
            reason = "character profile created" if created else "character profile already existed"
            if request.queue_avatar_generation:
                try:
                    batch = self.create(
                        profile.id,
                        CreateCharacterAvatarGenerationRequest(
                            appearance_prompt=request.appearance_template,
                            style=request.style,
                            provider_id=request.provider_id,
                        ),
                    )
                    generation_batch_id = batch.id
                    reason += "; avatar generation queued"
                except Exception as exc:
                    items.append(
                        ClonedVoiceCharacterBackfillItem(
                            voice_asset_id=voice.id,
                            display_name=display_name,
                            character_id=profile.id,
                            result="failed",
                            reason=f"{reason}; {exc}",
                        )
                    )
                    continue
            items.append(
                ClonedVoiceCharacterBackfillItem(
                    voice_asset_id=voice.id,
                    display_name=display_name,
                    character_id=profile.id,
                    result="created" if created else "existing",
                    generation_batch_id=generation_batch_id,
                    reason=reason,
                )
            )
        return BackfillClonedVoiceCharactersResponse(items=items)

    def _reconcile(self, batch: CharacterAvatarGenerationBatch) -> CharacterAvatarGenerationBatch:
        if batch.status in {"completed", "failed"}:
            return batch
        job_store = self.job_store_factory()
        base_job = job_store.get_job(batch.base_job_id)
        if base_job is None:
            return self.repository.update(batch.id, status="failed", error="base image job is missing")
        if base_job.status in {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE}:
            return self.repository.update(
                batch.id,
                status="failed",
                error=_job_error(base_job, "base image generation failed"),
            )
        if base_job.status != JobStatus.COMPLETED:
            return self.repository.update(batch.id, status="generating_base")

        base_asset_id = _job_asset_id(base_job)
        if not base_asset_id:
            return self.repository.update(batch.id, status="failed", error="base image job returned no asset")
        asset_ids = dict(batch.asset_ids)
        asset_ids.setdefault("base", base_asset_id)
        asset_ids.setdefault("mouth_closed", base_asset_id)

        variant_job_ids = dict(batch.variant_job_ids)
        variant_prompts = self._variant_prompts(batch.request)
        if not variant_prompts:
            return self._finalize(batch, asset_ids)

        character = None
        for variant, prompt in variant_prompts.items():
            job_id = variant_job_ids.get(variant)
            if not job_id:
                character = character or self.character_service_factory().get(batch.character_id)
                job = job_store.create_job(
                    self._image_job_request(
                        character_id=batch.character_id,
                        variant=variant,
                        prompt=_variant_prompt(character.display_name, prompt),
                        request=batch.request,
                        reference_asset_ids=[base_asset_id],
                    )
                )
                variant_job_ids[variant] = job.id
                return self.repository.update(
                    batch.id,
                    status="generating_variants",
                    variant_job_ids=variant_job_ids,
                    asset_ids=asset_ids,
                )

            job = job_store.get_job(job_id)
            if job is None:
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=asset_ids,
                    error=f"variant image job is missing: {variant}",
                )
            if job.status in {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STALE}:
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=asset_ids,
                    error=_job_error(job, f"avatar variant failed: {variant}"),
                )
            if job.status != JobStatus.COMPLETED:
                return self.repository.update(
                    batch.id,
                    status="generating_variants",
                    variant_job_ids=variant_job_ids,
                    asset_ids=asset_ids,
                )
            asset_id = _job_asset_id(job)
            if not asset_id:
                return self.repository.update(
                    batch.id,
                    status="failed",
                    asset_ids=asset_ids,
                    error=f"avatar variant returned no asset: {variant}",
                )
            asset_ids[variant] = asset_id

        return self._finalize(batch, asset_ids)

    def _finalize(
        self,
        batch: CharacterAvatarGenerationBatch,
        asset_ids: dict[str, str],
    ) -> CharacterAvatarGenerationBatch:
        current = self.avatar_service_factory().resolve(batch.character_id)
        pack = self.avatar_service_factory().upsert(
            batch.character_id,
            UpsertCharacterAvatarPackRequest(
                expected_version=current.version if current else None,
                render_mode="audio_envelope",
                base_asset_id=asset_ids.get("base"),
                mouth_frames={
                    "closed": asset_ids["mouth_closed"],
                    **{key.removeprefix("mouth_"): value for key, value in asset_ids.items() if key in {"mouth_small", "mouth_medium", "mouth_wide"}},
                },
                blink_frames={"closed": asset_ids["blink_closed"]} if asset_ids.get("blink_closed") else {},
                expression_frames={
                    key.removeprefix("expression_"): value
                    for key, value in asset_ids.items()
                    if key.startswith("expression_")
                },
                outfit_frames={"alternate": asset_ids["outfit_alternate"]} if asset_ids.get("outfit_alternate") else {},
                background_asset_ids={"alternate": asset_ids["background_alternate"]} if asset_ids.get("background_alternate") else {},
            ),
        )
        completed = self.repository.update(
            batch.id,
            status="completed",
            asset_ids=asset_ids,
            avatar_pack_version=pack.version,
            error="",
        )
        from .avatar_viseme_generation import CharacterVisemeGenerationService

        viseme_repository = None
        repository_path = getattr(self.repository, "db_path", None)
        if repository_path is not None:
            from .avatar_viseme_generation import CharacterVisemeGenerationRepository

            viseme_repository = CharacterVisemeGenerationRepository(repository_path)

        CharacterVisemeGenerationService(
            viseme_repository,
            character_service=self.character_service_factory(),
            avatar_service=self.avatar_service_factory(),
            job_store=self.job_store_factory(),
        ).ensure(batch.character_id)
        return completed

    def _variant_prompts(
        self,
        request: CreateCharacterAvatarGenerationRequest,
    ) -> dict[str, str]:
        prompts = {
            key: value
            for key, value in _VARIANT_PROMPTS.items()
            if not key.startswith("blink_") or request.include_blink
            if not key.startswith("expression_") or request.include_expressions
        }
        if request.include_outfit and request.outfit_prompt.strip():
            prompts["outfit_alternate"] = (
                VARIANT_PROMPTS_TEMPLATE.format(strip=request.outfit_prompt.strip())
            )
        if request.include_background and request.background_prompt.strip():
            prompts["background_alternate"] = (
                VARIANT_PROMPTS_2_TEMPLATE.format(strip=request.background_prompt.strip())
            )
        return prompts

    def _govern_uploaded_source(
        self,
        character_id: str,
        source_asset_id: str,
        *,
        consent_confirmed: bool,
    ) -> AssetRecord:
        if not consent_confirmed:
            raise ValueError("avatar_source_consent_required")
        store = self.asset_store_factory()
        asset = store.get_asset(source_asset_id)
        if asset is None:
            raise ValueError(f"avatar_source_not_found:{source_asset_id}")
        if asset.type != AssetType.IMAGE:
            raise ValueError(f"avatar_source_not_image:{source_asset_id}")
        if not bool(asset.metadata.get("reference_upload")):
            raise ValueError(f"avatar_source_must_be_uploaded_image:{source_asset_id}")
        images: list[Any] = []
        try:
            images = load_image_reference_assets([source_asset_id], store=store)
        except ImageReferenceError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            close_image_references(images)
        metadata = dict(asset.metadata)
        linked_character_ids = [
            str(value)
            for value in metadata.get("linked_character_ids", [])
            if str(value).strip()
        ]
        if character_id not in linked_character_ids:
            linked_character_ids.append(character_id)
        metadata.update(
            {
                "character_avatar_source": True,
                "source_image_consent_confirmed": True,
                "linked_character_ids": linked_character_ids,
            }
        )
        compat = dict(asset.compat)
        compat["character_avatar_source"] = True
        governed = asset.model_copy(
            update={
                "owner_id": asset.owner_id or "user:local",
                "metadata": metadata,
                "compat": compat,
            }
        )
        return store.upsert_asset(governed)

    def _image_job_request(
        self,
        *,
        character_id: str,
        variant: str,
        prompt: str,
        request: CreateCharacterAvatarGenerationRequest,
        reference_asset_ids: list[str],
    ) -> CreateJobRequest:
        return CreateJobRequest(
            owner_id=f"character:{character_id}",
            module="character-avatar",
            type="image.generate",
            resource_class=ResourceClass.GPU_IMAGE,
            priority=0,
            input_payload={
                "prompt": prompt,
                "negative_prompt": (
                    "text, watermark, logo, duplicate face, extra person, extra limbs, "
                    "cropped face, inconsistent hair, inconsistent clothing, camera shift"
                ),
                "provider_id": request.provider_id,
                "width": request.width,
                "height": request.height,
                "style": request.style,
                "reference_asset_ids": reference_asset_ids,
                "seed": request.seed,
                "steps": request.steps,
                "guidance_scale": request.guidance_scale,
                "unload_after_generation": request.unload_after_generation,
                "no_cache": bool(reference_asset_ids),
                "metadata": {
                    "character_id": character_id,
                    "avatar_variant": variant,
                    "avatar_generation_contract": "character_avatar_v1",
                    "source_asset_id": request.source_asset_id or None,
                },
            },
            compat={"character_id": character_id, "avatar_variant": variant},
        )


def _base_prompt(display_name: str, appearance_prompt: str, gender: str = "") -> str:
    custom = appearance_prompt.strip()
    gender_direction = (
        f"The character's gender presentation is {gender.strip()}. " if gender.strip() else ""
    )
    return (
        BASE_PROMPT_TEMPLATE.format(
            display_name=display_name,
            gender_direction=gender_direction,
            custom=custom + ' ' if custom else '',
        )
    )


def _uploaded_base_prompt(display_name: str, appearance_prompt: str) -> str:
    custom = appearance_prompt.strip()
    return (
        UPLOADED_BASE_PROMPT_TEMPLATE.format(
            display_name=display_name,
            custom=custom + ' ' if custom else '',
        )
    )


def _variant_prompt(display_name: str, change: str) -> str:
    return (
        VARIANT_PROMPT_TEMPLATE.format(display_name=display_name, change=change)
    )


def _job_asset_id(job: JobRecord) -> str | None:
    for output in job.output_refs:
        asset_id = str(output.get("asset_id") or "").strip()
        if asset_id:
            return asset_id
    return None


def _job_error(job: JobRecord, fallback: str) -> str:
    return job.error.message if job.error and job.error.message else fallback


def _voice_display_name(asset: AssetRecord) -> str:
    metadata = asset.metadata
    for key in ("character_name", "display_name", "title", "voice_clone_id", "speaker_name", "name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    tail = asset.id.split(":")[-1].replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in tail.split()) or "Voice Character"


def _looks_like_reference_voice(display_name: str, asset_id: str) -> bool:
    return bool(_REFERENCE_NAME_PATTERN.search(f"{display_name} {asset_id}"))


def _available_character_id(display_name: str, voice_asset_id: str, service: CharacterService) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "voice-character"
    candidate = slug[:150]
    if service.repository.get(candidate, include_archived=True) is None:
        return candidate
    suffix = hashlib.sha256(voice_asset_id.encode("utf-8")).hexdigest()[:8]
    return f"{candidate[:140]}-{suffix}"


def default_character_avatar_generation_service() -> CharacterAvatarGenerationService:
    return CharacterAvatarGenerationService()


__all__ = [
    "CharacterAvatarGenerationNotFoundError",
    "CharacterAvatarGenerationService",
    "default_character_avatar_generation_service",
]
