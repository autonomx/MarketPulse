"""Provider-free shadow generation for migration telemetry."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import json
from time import perf_counter
from typing import Any, Mapping

from .authority import AuthorityClass, DeliveryMode, PresentationProfile, VisibilityClass
from .contracts import EvidenceRecord, TurnPresentationRequest
from .evidence import EvidenceBroker, InMemoryEvidenceSource
from .renderer import CanonicalNarrativeRenderer
from .service import NarrativeEngineService


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _sample_rate() -> float:
    # Off by default (WP-7.1): a shadow report re-runs narrative work on every
    # sampled turn. Set OMNIX_RPG_NARRATIVE_SHADOW_SAMPLE_RATE to enable it.
    raw = _env_str("OMNIX_RPG_NARRATIVE_SHADOW_SAMPLE_RATE", "0").strip()
    try:
        return max(0.0, min(float(raw), 1.0))
    except ValueError:
        return 0.0


def shadow_selected(turn_id: str, sample_rate: float | None = None) -> bool:
    rate = _sample_rate() if sample_rate is None else max(0.0, min(float(sample_rate), 1.0))
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(turn_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return bucket < rate


def _scene(result: Mapping[str, Any]) -> dict[str, Any]:
    session = _mapping(result.get("session"))
    state = _mapping(result.get("simulation_state") or session.get("simulation_state") or session.get("state"))
    runtime = _mapping(result.get("runtime_state") or session.get("runtime_state"))
    return _mapping(
        result.get("scene")
        or state.get("scene")
        or runtime.get("scene")
        or runtime.get("current_scene")
    )


def _speaker(result: Mapping[str, Any]) -> tuple[str | None, str]:
    npc = _mapping(result.get("npc"))
    visible = _mapping(result.get("visible_response"))
    visible_npc = _mapping(visible.get("npc"))
    speaker = _first_text(
        npc.get("speaker_id"),
        npc.get("speaker"),
        visible_npc.get("speaker_id"),
        visible_npc.get("speaker"),
        result.get("target_id"),
    )
    if speaker and not speaker.startswith("npc:"):
        speaker = f"npc:{speaker.casefold().replace(' ', '_')}"
    name = _first_text(npc.get("speaker"), visible_npc.get("speaker"), speaker)
    return (speaker or None, name)


def runtime_evidence(result: Mapping[str, Any]) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    scene = _scene(result)
    location = _first_text(scene.get("location_name"), scene.get("name"), scene.get("title"))
    summary = _first_text(scene.get("summary"), scene.get("description"))
    if location or summary:
        records.append(
            EvidenceRecord(
                evidence_id="runtime:scene:current",
                content=". ".join(value for value in (location, summary) if value),
                authority=AuthorityClass.SCENE_OBSERVATION,
                visibility=VisibilityClass.PUBLIC,
                entity_refs=(f"location:{location.casefold().replace(' ', '_')}",) if location else (),
                source_revision=int(result.get("state_revision") or 0),
            )
        )
    effects = _mapping(
        result.get("canonical_effects")
        or _mapping(result.get("result")).get("canonical_effects")
        or _mapping(result.get("authoritative")).get("canonical_effects")
    )
    if effects:
        records.append(
            EvidenceRecord(
                evidence_id="runtime:turn:effects",
                content=json.dumps(effects, sort_keys=True, ensure_ascii=False),
                authority=AuthorityClass.CONFIRMED_TURN,
                visibility=VisibilityClass.PLAYER_KNOWN,
                source_revision=int(result.get("state_revision") or 0),
                claim_refs=tuple(str(key) for key in sorted(effects)),
            )
        )
    speaker_id, speaker_name = _speaker(result)
    diagnostics = _mapping(result.get("first_call_grounding_diagnostics"))
    packet = _mapping(diagnostics.get("turn_grounding_packet"))
    npc_context = _mapping(packet.get("npc_context"))
    addressed = raw_addressed_npcs if isinstance(raw_addressed_npcs := npc_context.get("addressed_npcs"), list) else []
    profiles = [item for item in addressed if isinstance(item, Mapping)]
    if not profiles:
        session = _mapping(result.get("session"))
        simulation = _mapping(
            result.get("simulation_state") or session.get("simulation_state")
        )
        profile_containers = (
            _mapping(simulation.get("npc_index")),
            _mapping(simulation.get("npcs")),
        )
        for candidate_id in result.get("dialogue_speaker_ids") or ():
            candidate = next(
                (
                    _mapping(container.get(str(candidate_id)))
                    for container in profile_containers
                    if isinstance(container.get(str(candidate_id)), Mapping)
                ),
                {},
            )
            if candidate:
                profiles.append(candidate)
    if not profiles and speaker_id:
        profiles = [{"id": speaker_id, "name": speaker_name}]
    for profile in profiles:
        profile_id = _first_text(profile.get("id"), profile.get("npc_id"))
        if profile_id and not profile_id.startswith("npc:"):
            profile_id = f"npc:{profile_id.casefold().replace(' ', '_')}"
        if not profile_id:
            continue
        profile_name = _first_text(profile.get("name"), profile_id)
        biography = profile.get("biography")
        public_biography = (
            _first_text(_mapping(biography).get("public"))
            if isinstance(biography, Mapping)
            else _first_text(biography, profile.get("description"))
        )
        profile_parts = [
            profile_name,
            public_biography,
            _first_text(_mapping(profile.get("personality_profile")).get("summary"), profile.get("personality")),
            _first_text(profile.get("speech_style")),
        ]
        content = ". ".join(part for part in profile_parts if part)
        if content:
            records.append(
                EvidenceRecord(
                    evidence_id=f"runtime:{profile_id}:profile",
                    content=content,
                    authority=AuthorityClass.OBJECTIVE_CANON,
                    visibility=VisibilityClass.NARRATOR_ONLY,
                    known_by=(profile_id,),
                    entity_refs=(profile_id,),
                    source_revision=int(result.get("state_revision") or 0),
                )
            )
    if not records:
        records.append(
            EvidenceRecord(
                evidence_id="runtime:turn:known",
                content="The current turn has no additional visible world evidence beyond its authoritative result.",
                authority=AuthorityClass.CONFIRMED_TURN,
                visibility=VisibilityClass.PLAYER_KNOWN,
            )
        )
    return tuple(records)


def _response_mode(result: Mapping[str, Any], speaker_id: str | None) -> str:
    resolved = _mapping(result.get("resolved_result") or result.get("result"))
    return _first_text(
        resolved.get("response_mode"),
        resolved.get("semantic_family"),
        resolved.get("action_type"),
        "dialogue" if speaker_id else "action",
    )


def _legacy_text(result: Mapping[str, Any]) -> str:
    npc = _mapping(result.get("npc"))
    line = _first_text(npc.get("line"), npc.get("text"))
    narration = _first_text(result.get("narration"), result.get("final_narration"), result.get("summary"))
    return "\n\n".join(value for value in (narration, line) if value)


def build_shadow_report(
    result: Mapping[str, Any],
    *,
    session_id: str,
    player_input: str,
    sample_rate: float | None = None,
) -> dict[str, Any]:
    turn_id = _first_text(result.get("turn_id"), f"turn:{result.get('tick', 0)}")
    if not shadow_selected(turn_id, sample_rate):
        return {"selected": False, "turn_id": turn_id, "source": "narrative_engine_shadow_v1"}
    started = perf_counter()
    evidence = runtime_evidence(result)
    speaker_id, _ = _speaker(result)
    request = TurnPresentationRequest(
        request_id=f"shadow:{session_id}:{turn_id}",
        turn_id=turn_id,
        campaign_id=session_id,
        player_input=player_input,
        authoritative_outcome=_mapping(result.get("resolved_result") or result.get("result")),
        scene_snapshot=_scene(result),
        actor_ids=(speaker_id,) if speaker_id else (),
        target_actor_id=speaker_id,
        presentation_profile=PresentationProfile.IMMERSIVE,
        delivery_mode=DeliveryMode.BLOCKING,
        metadata={
            "response_mode": _response_mode(result, speaker_id),
            "response_id": f"shadow-response:{session_id}:{turn_id}",
        },
    )
    try:
        generated = NarrativeEngineService(
            evidence_broker=EvidenceBroker([InMemoryEvidenceSource(evidence, source_id="runtime_shadow")])
        ).generate(request)
        canonical_text = CanonicalNarrativeRenderer().render(generated.response).text
        legacy_text = _legacy_text(result)
        return {
            "selected": True,
            "ok": True,
            "source": "narrative_engine_shadow_v1",
            "turn_id": turn_id,
            "response_id": generated.response.response_id,
            "content_hash": generated.response.content_hash,
            "canonical_text": canonical_text,
            "legacy_text": legacy_text,
            "visible_text_changed": canonical_text.strip() != legacy_text.strip(),
            "evidence_ids": list(generated.retrieval.trace.selected_ids),
            "beat_purposes": [beat.purpose.value for beat in generated.plan.beats],
            "validation": generated.response.validation.as_dict(),
            "latency_ms": round((perf_counter() - started) * 1000.0, 3),
            "authoritative_state_unchanged": True,
        }
    except Exception as exc:
        return {
            "selected": True,
            "ok": False,
            "source": "narrative_engine_shadow_v1",
            "turn_id": turn_id,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((perf_counter() - started) * 1000.0, 3),
            "authoritative_state_unchanged": True,
        }


def attach_shadow_report(
    result: dict[str, Any],
    *,
    session_id: str,
    player_input: str,
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("ok") is not True:
        return result
    result["narrative_engine_shadow"] = build_shadow_report(
        result,
        session_id=session_id,
        player_input=player_input,
    )
    return result
