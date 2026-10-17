"""Deterministic narrative beat planning before prose generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .authority import (
    AuthorityClass,
    BeatKind,
    BeatPurpose,
    NarrativeSignificance,
    PresentationProfile,
)
from .contracts import EvidenceRecord, NarrativeBeat, TurnPresentationRequest
from .evidence import EvidenceGrantSet
from .profiles import NarrativeProfilePolicy, adaptive_profile, profile_policy


@dataclass(frozen=True)
class NarrativePlan:
    request_id: str
    mode: str
    profile: PresentationProfile
    word_budget: tuple[int, int]
    beats: tuple[NarrativeBeat, ...]
    must_answer: str
    metadata: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "mode": self.mode,
            "profile": self.profile.value,
            "word_budget": list(self.word_budget),
            "beats": [beat.as_dict() for beat in self.beats],
            "must_answer": self.must_answer,
            "metadata": dict(self.metadata),
        }


def _response_mode(request: TurnPresentationRequest) -> str:
    metadata_mode = str(request.metadata.get("response_mode") or "").strip().casefold()
    outcome = request.authoritative_outcome
    resolved = str(
        outcome.get("response_mode")
        or outcome.get("semantic_family")
        or outcome.get("action_type")
        or metadata_mode
        or "action"
    ).strip().casefold()
    aliases = {
        "social": "dialogue",
        "conversation": "dialogue",
        "look": "observation",
        "inspect": "observation",
        "move": "travel",
        "service": "transaction",
        "shop": "transaction",
        "fight": "combat",
    }
    return aliases.get(resolved, resolved)


def _evidence_ids(evidence: Sequence[EvidenceRecord], *entity_hints: str) -> tuple[str, ...]:
    hints = {hint for hint in entity_hints if hint}
    if not hints:
        return tuple(record.evidence_id for record in evidence[:3])
    matching = [record.evidence_id for record in evidence if hints.intersection(record.entity_refs)]
    return tuple(matching[:4]) or tuple(record.evidence_id for record in evidence[:3])


def _authoritative_evidence_ids(evidence: Sequence[EvidenceRecord]) -> tuple[str, ...]:
    authoritative = tuple(
        record.evidence_id
        for record in evidence
        if record.authority is AuthorityClass.CONFIRMED_TURN
    )
    return authoritative[:4] or tuple(record.evidence_id for record in evidence[:4])


def _claim_refs(request: TurnPresentationRequest) -> tuple[str, ...]:
    raw = request.authoritative_outcome.get("allowed_claim_refs") or request.metadata.get("allowed_claim_refs") or ()
    return tuple(str(value) for value in raw if str(value).strip())


def _beat(
    sequence: int,
    kind: BeatKind,
    purpose: BeatPurpose,
    *,
    speaker_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    claim_refs: tuple[str, ...] = (),
    instructions: str,
    evidence_scope: str = "player",
    required: bool = True,
) -> NarrativeBeat:
    return NarrativeBeat(
        beat_id=f"beat:{sequence}:{purpose.value}",
        sequence=sequence,
        kind=kind,
        purpose=purpose,
        speaker_id=speaker_id,
        evidence_refs=evidence_refs,
        required_claim_refs=claim_refs,
        instructions=instructions,
        required=required,
        metadata={"evidence_scope": evidence_scope},
    )


def _append_scene_beats(
    beats: list[NarrativeBeat],
    request: TurnPresentationRequest,
    narrator_evidence: Sequence[EvidenceRecord],
) -> None:
    purposes: list[BeatPurpose] = []
    for change in request.scene_changes:
        if change.kind in {"new_game", "location_changed", "region_changed", "changed_return_visit"}:
            purposes.append(BeatPurpose.SCENE_ESTABLISHMENT)
        purposes.append(BeatPurpose.ENVIRONMENTAL_CHANGE)
    narrator_ids = {record.evidence_id for record in narrator_evidence}
    for purpose in dict.fromkeys(purposes):
        explicit = tuple(
            ref
            for ref in dict.fromkeys(
                ref for change in request.scene_changes for ref in change.evidence_refs
            )
            if ref in narrator_ids
        )
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.NARRATION,
                purpose,
                evidence_refs=explicit or _evidence_ids(narrator_evidence, *request.actor_ids),
                instructions=(
                    "Establish the changed scene using concrete spatial and sensory evidence."
                    if purpose is BeatPurpose.SCENE_ESTABLISHMENT
                    else "Describe only the meaningful environmental change relevant to the current turn."
                ),
                evidence_scope="narrator",
            )
        )


def _dialogue_beats(
    beats: list[NarrativeBeat],
    request: TurnPresentationRequest,
    narrator_evidence: Sequence[EvidenceRecord],
    grants: EvidenceGrantSet,
    policy: NarrativeProfilePolicy,
) -> None:
    speaker = request.target_actor_id or str(request.metadata.get("speaker_id") or "") or None
    speakers = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in request.metadata.get("dialogue_speaker_ids") or (speaker,)
            if str(value or "").strip()
        )
    )
    narrator_refs = _evidence_ids(narrator_evidence, *speakers)
    beats.append(
        _beat(
            len(beats) + 1,
            BeatKind.NARRATION,
            BeatPurpose.PHYSICAL_REACTION,
            evidence_refs=narrator_refs,
            instructions="Show one brief, character-specific physical or environmental reaction before the reply.",
            evidence_scope="narrator",
        )
    )
    for dialogue_speaker in speakers or (speaker,):
        speaker_evidence = grants.for_speaker(dialogue_speaker)
        spoken_refs = _evidence_ids(speaker_evidence, dialogue_speaker or "")
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.DIALOGUE,
                BeatPurpose.DIRECT_ANSWER,
                speaker_id=dialogue_speaker,
                evidence_refs=spoken_refs,
                claim_refs=_claim_refs(request),
                instructions=(
                    "Answer the player's latest statement or question directly in this "
                    "speaker's established voice and perspective."
                ),
                evidence_scope="speaker",
            )
        )
    speaker_evidence = grants.for_speaker(speaker)
    spoken_refs = _evidence_ids(speaker_evidence, speaker or "")
    if policy.allow_lore_expansion and len(beats) < policy.maximum_beats:
        lore_ids = tuple(
            record.evidence_id
            for record in narrator_evidence
            if record.evidence_id not in narrator_refs
        )[:4]
        if lore_ids:
            beats.append(
                _beat(
                    len(beats) + 1,
                    BeatKind.NARRATION,
                    BeatPurpose.LORE_REVEAL,
                    evidence_refs=lore_ids,
                    instructions="Connect one relevant memory, relationship, or lore detail without turning the reply into an exposition dump.",
                    evidence_scope="narrator",
                    required=False,
                )
            )
    if request.significance is NarrativeSignificance.MAJOR and len(beats) < policy.maximum_beats:
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.NARRATION,
                BeatPurpose.EMOTIONAL_ESCALATION,
                evidence_refs=narrator_refs,
                instructions="Escalate through a concrete movement, gesture, or use of the environment.",
                evidence_scope="narrator",
            )
        )
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.DIALOGUE,
                BeatPurpose.ULTIMATUM,
                speaker_id=speaker,
                evidence_refs=tuple(record.evidence_id for record in speaker_evidence[:5]),
                instructions="End with a consequential demand, boundary, or question that follows from the character's goals.",
                evidence_scope="speaker",
                required=False,
            )
        )
    elif len(speakers) <= 1 and policy.allow_forward_hook and len(beats) < policy.maximum_beats:
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.DIALOGUE,
                BeatPurpose.CONTINUATION,
                speaker_id=speaker,
                evidence_refs=spoken_refs,
                instructions="Add a natural continuation only when it follows from the answer; do not choose for the player.",
                evidence_scope="speaker",
                required=False,
            )
        )


def _action_beats(
    beats: list[NarrativeBeat],
    request: TurnPresentationRequest,
    player_evidence: Sequence[EvidenceRecord],
    mode: str,
    policy: NarrativeProfilePolicy,
) -> None:
    claims = _claim_refs(request)
    authoritative_refs = _authoritative_evidence_ids(player_evidence)
    action_kind = BeatKind.ACTION if mode not in {"transaction", "failure"} else BeatKind.RESULT
    beats.append(
        _beat(
            len(beats) + 1,
            action_kind,
            BeatPurpose.RESOLVED_ACTION,
            evidence_refs=authoritative_refs,
            claim_refs=claims,
            instructions="Describe the authoritative resolved action without changing or extending its outcome.",
            evidence_scope="player",
        )
    )
    if mode in {"combat", "transaction", "failure", "travel"} or request.significance is not NarrativeSignificance.ROUTINE:
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.RESULT,
                BeatPurpose.CONSEQUENCE,
                evidence_refs=authoritative_refs,
                claim_refs=claims,
                instructions="Present the immediate consequence and current actionable situation using only resolved facts.",
                evidence_scope="player",
            )
        )
    if policy.allow_forward_hook and len(beats) < policy.maximum_beats:
        beats.append(
            _beat(
                len(beats) + 1,
                BeatKind.CHOICE,
                BeatPurpose.OFFERED_CHOICE,
                evidence_refs=authoritative_refs,
                instructions="Offer a grounded next option only when useful; preserve player agency.",
                evidence_scope="player",
                required=False,
            )
        )


class DeterministicBeatPlanner:
    """Build ordered meaning from an authoritative turn before prose exists."""

    def plan(
        self,
        request: TurnPresentationRequest,
        evidence: Sequence[EvidenceRecord],
        *,
        grants: EvidenceGrantSet | None = None,
    ) -> NarrativePlan:
        mode = _response_mode(request)
        resolved_profile = adaptive_profile(request.presentation_profile, request.significance)
        policy = profile_policy(resolved_profile)
        speaker = request.target_actor_id or str(request.metadata.get("speaker_id") or "") or None
        if grants is None:
            all_evidence = tuple(evidence)
            grants = EvidenceGrantSet(
                player=all_evidence,
                narrator=all_evidence,
                speakers={speaker: all_evidence} if speaker else {},
            )
        player_evidence = grants.player
        narrator_evidence = grants.narrator

        beats: list[NarrativeBeat] = []
        _append_scene_beats(beats, request, narrator_evidence)
        if mode == "dialogue" or request.target_actor_id:
            _dialogue_beats(
                beats,
                request,
                narrator_evidence,
                grants,
                policy,
            )
        elif mode in {"observation", "investigation"}:
            beats.append(
                _beat(
                    len(beats) + 1,
                    BeatKind.NARRATION,
                    BeatPurpose.DIRECT_ANSWER,
                    evidence_refs=tuple(record.evidence_id for record in player_evidence[:5]),
                    claim_refs=_claim_refs(request),
                    instructions="Answer what the player can presently observe or infer, distinguishing certainty from uncertainty.",
                    evidence_scope="player",
                )
            )
        else:
            _action_beats(beats, request, player_evidence, mode, policy)

        dialogue_speaker_count = len(
            tuple(request.metadata.get("dialogue_speaker_ids") or ())
        )
        maximum_beats = max(
            policy.maximum_beats,
            1 + dialogue_speaker_count if dialogue_speaker_count else 0,
        )
        beats = beats[:maximum_beats]
        must_answer = str(request.metadata.get("must_answer") or request.player_input).strip()
        return NarrativePlan(
            request_id=request.request_id,
            mode=mode,
            profile=resolved_profile,
            word_budget=(policy.minimum_words, policy.maximum_words),
            beats=tuple(beats),
            must_answer=must_answer,
            metadata={
                "significance": request.significance.value,
                "scene_change_count": len(request.scene_changes),
                "evidence_count": len(evidence),
                "player_evidence_count": len(player_evidence),
                "narrator_evidence_count": len(narrator_evidence),
                "speaker_evidence_count": len(grants.for_speaker(speaker)),
                "dialogue_speaker_count": dialogue_speaker_count,
            },
        )
