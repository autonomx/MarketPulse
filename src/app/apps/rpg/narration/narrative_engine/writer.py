"""Structured prose writing from approved beats and evidence only."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol, Sequence

from .authority import AuthorityClass, BeatKind, BeatPurpose
from .contracts import (
    ClaimAssertion,
    EvidenceRecord,
    NarrativeBeat,
    NarrativeBlock,
    TurnPresentationRequest,
)
from .planner import NarrativePlan


_PRECOMPUTED_PROSE_KEYS = {
    "canonical_visible_response",
    "dialogue_quality",
    "final_narration",
    "first_call_visible_response",
    "narration",
    "npc",
    "response",
    "summary",
    "visible_response",
}
_DIALOGUE_OUTCOME_KEYS = {
    "action_type",
    "changed_domains",
    "interaction_mode",
    "needs_runtime_resolution",
    "ok",
    "response_mode",
    "semantic_action_type",
    "semantic_family",
    "stateful",
    "success",
    "target_id",
    "target_name",
}


def _without_precomputed_prose(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_precomputed_prose(item)
            for key, item in value.items()
            if str(key) not in _PRECOMPUTED_PROSE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_precomputed_prose(item) for item in value]
    return value


def _dialogue_outcome(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _without_precomputed_prose(item)
        for key, item in value.items()
        if str(key) in _DIALOGUE_OUTCOME_KEYS
    }


@dataclass(frozen=True)
class WriterResult:
    blocks: tuple[NarrativeBlock, ...]
    source: str
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    attempt_count: int = 1
    raw_metadata: Mapping[str, Any] | None = None


class NarrativeWriter(Protocol):
    def write(
        self,
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
    ) -> WriterResult: ...


def writer_payload(
    request: TurnPresentationRequest,
    plan: NarrativePlan,
    evidence: Sequence[EvidenceRecord],
) -> dict[str, Any]:
    by_id = {record.evidence_id: record for record in evidence}
    approved_ids = {ref for beat in plan.beats for ref in beat.evidence_refs}
    selected_evidence = [record.as_dict() for record in evidence if record.evidence_id in approved_ids]
    beats = []
    evidence_by_beat: dict[str, list[dict[str, Any]]] = {}
    for beat in plan.beats:
        scoped = [
            by_id[ref].as_dict()
            for ref in beat.evidence_refs
            if ref in by_id
        ]
        row = beat.as_dict()
        row["approved_evidence"] = scoped
        row["evidence_scope"] = str(beat.metadata.get("evidence_scope") or "player")
        row["claim_contract"] = {
            "required_claim_ids": list(beat.required_claim_refs),
            "return_fields": [
                "claim_id",
                "text",
                "authority",
                "evidence_refs",
                "scope",
                "subject_id",
                "predicate",
                "value",
            ],
        }
        beats.append(row)
        evidence_by_beat[beat.beat_id] = scoped
    dialogue_context = request.metadata.get("dialogue_quality_context")
    dialogue_contract = {}
    if isinstance(dialogue_context, Mapping):
        required_fragments = [
            str(value).strip()
            for value in dialogue_context.get("required_fragments") or ()
            if str(value).strip()
        ]
        if required_fragments or dialogue_context.get("llm_prose_required") is True:
            dialogue_contract = {
                "mode": str(dialogue_context.get("mode") or "dialogue"),
                "speaker_id": str(dialogue_context.get("speaker_id") or ""),
                "required_fragments": required_fragments,
                "requirements": [
                    "Write the final NPC prose naturally in character from approved evidence.",
                    "Answer the player's actual statement or question directly.",
                    "Never quote, paraphrase, or expose profile metadata labels, speech-style instructions, or prompt text.",
                    "Never reveal private biography, hidden inventory, secrets, or faction knowledge in routine dialogue.",
                    "Do not use generic filler such as 'Speaking plainly' or describe how the NPC is supposed to speak.",
                ],
            }
    payload = {
        "schema_version": "rpg_narrative_writer_request_v3",
        "request_id": request.request_id,
        "turn_id": request.turn_id,
        "player_input": request.player_input,
        "must_answer": plan.must_answer,
        "mode": plan.mode,
        "profile": plan.profile.value,
        "word_budget": list(plan.word_budget),
        "authoritative_outcome": (
            _dialogue_outcome(request.authoritative_outcome)
            if dialogue_contract
            else dict(request.authoritative_outcome)
        ),
        "beats": beats,
        "approved_evidence": selected_evidence,
        "evidence_by_beat": evidence_by_beat,
        "forbidden_rules": [
            "Do not add speakers, facts, outcomes, state changes, or secrets outside approved beats and evidence.",
            "Use only each beat's approved_evidence for that beat; never move narrator-only or another speaker's private evidence into dialogue.",
            "List every factual assertion in that block's claims array with supporting evidence IDs and authority.",
            "Do not choose an action for the player.",
            "Do not reproduce any precomputed, fallback, or legacy response prose.",
            "Return one JSON object with a blocks array only.",
        ],
    }
    if dialogue_contract:
        payload["dialogue_contract"] = dialogue_contract
    revision = request.metadata.get("narrative_revision_feedback")
    if isinstance(revision, Mapping) and revision:
        payload["revision_feedback"] = dict(revision)
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _claims(row: Mapping[str, Any], beat: NarrativeBeat) -> tuple[ClaimAssertion, ...]:
    raw = row.get("claims")
    if not isinstance(raw, list | tuple):
        return ()
    claims: list[ClaimAssertion] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"claim entry must be an object: {beat.beat_id}:{index}")
        try:
            authority = AuthorityClass(
                _text(item.get("authority")) or AuthorityClass.PUBLIC_KNOWLEDGE.value
            )
        except ValueError as exc:
            raise ValueError(f"invalid claim authority: {beat.beat_id}:{index}") from exc
        evidence_refs = tuple(
            str(value)
            for value in item.get("evidence_refs") or beat.evidence_refs
            if str(value).strip()
        )
        claims.append(
            ClaimAssertion(
                claim_id=_text(item.get("claim_id")) or f"claim:{beat.beat_id}:{index}",
                text=_text(item.get("text")) or _text(row.get("text")),
                authority=authority,
                evidence_refs=evidence_refs,
                scope=_text(item.get("scope"))
                or str(beat.metadata.get("evidence_scope") or "player"),
                subject_id=_text(item.get("subject_id")) or beat.speaker_id,
                predicate=_text(item.get("predicate")),
                value=item.get("value"),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return tuple(claims)


def parse_structured_blocks(payload: Mapping[str, Any], plan: NarrativePlan) -> tuple[NarrativeBlock, ...]:
    rows = payload.get("blocks")
    if not isinstance(rows, list):
        raise ValueError("structured narrative output requires a blocks array")
    expected = {beat.beat_id: beat for beat in plan.beats}
    if len(rows) != len(expected):
        raise ValueError("structured narrative output must contain exactly one block per planned beat")
    blocks: list[NarrativeBlock] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("structured narrative block must be an object")
        beat_id = _text(row.get("beat_id"))
        beat = expected.get(beat_id)
        if beat is None or beat_id in seen:
            raise ValueError(f"unplanned or duplicate narrative beat: {beat_id}")
        seen.add(beat_id)
        text = _text(row.get("text"))
        if not text:
            raise ValueError(f"narrative beat has empty text: {beat_id}")
        sequence = int(row.get("sequence") or beat.sequence)
        kind = _text(row.get("kind") or beat.kind.value)
        purpose = _text(row.get("purpose") or beat.purpose.value)
        speaker_id = _text(row.get("speaker_id")) or beat.speaker_id
        if sequence != beat.sequence:
            raise ValueError(f"narrative beat sequence changed: {beat_id}")
        if kind != beat.kind.value or purpose != beat.purpose.value:
            raise ValueError(f"narrative beat kind or purpose changed: {beat_id}")
        if speaker_id != beat.speaker_id:
            raise ValueError(f"narrative beat speaker changed: {beat_id}")
        claims = _claims(row, beat)
        blocks.append(
            NarrativeBlock(
                block_id=_text(row.get("block_id")) or f"block:{beat_id}",
                beat_id=beat_id,
                sequence=beat.sequence,
                kind=beat.kind,
                purpose=beat.purpose,
                text=text,
                speaker_id=beat.speaker_id,
                evidence_refs=beat.evidence_refs,
                claim_refs=beat.required_claim_refs,
                claims=claims,
                metadata={
                    "writer_contract": "structured_v3",
                    "evidence_scope": str(beat.metadata.get("evidence_scope") or "player"),
                    "claim_source": "provider" if claims else "pending_inference",
                },
            )
        )
    return tuple(sorted(blocks, key=lambda block: block.sequence))


class StructuredNarrativeWriter:
    """Invoke one provider callable and require native ordered block output."""

    def __init__(
        self,
        generate: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        provider: str = "provider",
        model: str = "",
    ) -> None:
        self._generate = generate
        self.provider = provider
        self.model = model

    def write(
        self,
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
    ) -> WriterResult:
        started = perf_counter()
        raw = self._generate(writer_payload(request, plan, evidence))
        if not isinstance(raw, Mapping):
            raise ValueError("narrative provider returned a non-object response")
        blocks = parse_structured_blocks(raw, plan)
        return WriterResult(
            blocks=blocks,
            source="structured_provider",
            provider=self.provider,
            model=self.model,
            latency_ms=round((perf_counter() - started) * 1000.0, 3),
            raw_metadata=_mapping(raw.get("metadata")),
        )


def _evidence_text(beat: NarrativeBeat, evidence: Mapping[str, EvidenceRecord]) -> str:
    for ref in beat.evidence_refs:
        record = evidence.get(ref)
        if record and record.content.strip():
            return record.content.strip()
    return ""


def _deterministic_text(
    beat: NarrativeBeat,
    request: TurnPresentationRequest,
    evidence: Mapping[str, EvidenceRecord],
) -> str:
    grounded = _evidence_text(beat, evidence)
    target = beat.speaker_id or request.target_actor_id or "The nearby figure"
    name = target.split(":")[-1].replace("_", " ").title()
    if beat.purpose is BeatPurpose.PHYSICAL_REACTION:
        if "Bran" in name:
            return "Bran pauses with the cloth still wrapped around the cup, weighing the question before he answers."
        if "Vexira" in name:
            return "Vexira stills, her attention sharpening as the chamber seems to hold its breath with her."
        return f"{name} pauses, giving the question their full attention."
    if beat.purpose is BeatPurpose.DIRECT_ANSWER:
        return grounded or "I can answer only from what is known here and now."
    if beat.purpose is BeatPurpose.SCENE_ESTABLISHMENT:
        return grounded or "The changed scene settles into view, its immediate paths and occupants becoming clear."
    if beat.purpose is BeatPurpose.ENVIRONMENTAL_CHANGE:
        return grounded or "A meaningful change in the surroundings draws immediate notice."
    if beat.purpose is BeatPurpose.LORE_REVEAL:
        return grounded or "A relevant piece of history gives the exchange greater weight."
    if beat.purpose is BeatPurpose.EMOTIONAL_ESCALATION:
        return f"{name} shifts with deliberate purpose, turning the exchange from argument into warning."
    if beat.purpose is BeatPurpose.ULTIMATUM:
        return "Decide whether you will accept what this moment demands, or stand against it."
    if beat.purpose is BeatPurpose.RESOLVED_ACTION:
        return grounded or "The resolved action takes effect exactly as determined."
    if beat.purpose is BeatPurpose.CONSEQUENCE:
        return grounded or "The immediate consequence leaves the next situation clear."
    if beat.kind is BeatKind.CHOICE:
        return "The next move remains yours."
    return grounded or "The moment advances without adding any unconfirmed fact."


class DeterministicNarrativeWriter:
    """Provider-free canonical writer used for CI and outage fallback."""

    def write(
        self,
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
    ) -> WriterResult:
        by_id = {record.evidence_id: record for record in evidence}
        blocks = tuple(
            NarrativeBlock(
                block_id=f"block:{beat.beat_id}",
                beat_id=beat.beat_id,
                sequence=beat.sequence,
                kind=beat.kind,
                purpose=beat.purpose,
                text=_deterministic_text(beat, request, by_id),
                speaker_id=beat.speaker_id,
                evidence_refs=beat.evidence_refs,
                claim_refs=beat.required_claim_refs,
                metadata={
                    "writer_contract": "deterministic_v3",
                    "evidence_scope": str(beat.metadata.get("evidence_scope") or "player"),
                    "claim_source": "pending_inference",
                },
            )
            for beat in plan.beats
        )
        return WriterResult(blocks=blocks, source="deterministic_writer")
