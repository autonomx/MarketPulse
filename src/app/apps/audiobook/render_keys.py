"""Deterministic TTS identities and selective dependency invalidation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .hashing import object_hash


RENDER_KEY_VERSION = "audiobook-render-v1"


@dataclass(frozen=True, slots=True)
class RenderIdentity:
    source_span_hash: str
    annotation_revision: str
    speaker_id: str
    casting_revision: str
    voice_revision: str
    reference_audio_hash: str | None
    provider_id: str
    model_id: str
    model_revision: str
    language: str
    delivery_instruction: str
    speech_plan_hash: str
    generation_parameters: dict[str, Any]
    seed: int | None

    def key(self) -> str:
        return object_hash({"version": RENDER_KEY_VERSION, **asdict(self)})


@dataclass(frozen=True, slots=True)
class Invalidation:
    analyze: str
    render: str
    master: str
    export: bool = True


INVALIDATION: dict[str, Invalidation] = {
    "cover": Invalidation("none", "none", "none"),
    "book_metadata": Invalidation("none", "none", "none"),
    "canonical_source": Invalidation("all", "all", "all"),
    "span_annotation": Invalidation("affected_spans", "affected_spans", "affected_chapters"),
    "confirmed_alias": Invalidation("affected_interpretation", "if_casting_changes", "affected_chapters"),
    "casting": Invalidation("none", "affected_speaker_spans", "affected_chapters"),
    "delivery": Invalidation("none", "affected_spans", "affected_chapters"),
    "pronunciation": Invalidation("none", "affected_speech_plans", "affected_chapters"),
    "tts_settings": Invalidation("none", "affected_key_space", "affected_chapters"),
    "pause_policy": Invalidation("none", "none", "affected_chapters"),
    "mastering": Invalidation("none", "none", "all"),
    "export_settings": Invalidation("none", "none", "none"),
}


def invalidation_for(change: str) -> Invalidation:
    return INVALIDATION[change]
