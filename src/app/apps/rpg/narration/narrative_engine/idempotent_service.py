"""Repository-first idempotency for canonical narrative generation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from .authority import PresentationProfile
from .contracts import (
    CanonicalNarrativeResponse,
    EvidenceRecord,
    NarrativeBeat,
    TurnPresentationRequest,
    stable_hash,
)
from .evidence import EvidenceGrantSet, EvidenceRetrievalResult, RetrievalTrace
from .persistence_policy import repository_save_deferred
from .planner import NarrativePlan
from .repository import NarrativeResponseConflict
from .service import NarrativeEngineResult, NarrativeEngineService as _NarrativeEngineService
from .validation import PROVIDER_REQUIRED_FALLBACK, NarrativeProviderRequiredError, ValidatedWriterResult
from .writer import WriterResult


class NarrativeTurnIdentityConflict(NarrativeResponseConflict):
    """Raised when one turn is replayed under a different request identity."""


def _metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _request_identity_hash(request: TurnPresentationRequest) -> str:
    payload = request.as_dict()
    payload.pop("delivery_mode", None)
    return stable_hash(payload)


def _profile(
    response: CanonicalNarrativeResponse,
    request: TurnPresentationRequest,
) -> PresentationProfile:
    raw = str(response.metadata.get("profile") or "").strip().casefold()
    try:
        return PresentationProfile(raw) if raw else request.presentation_profile
    except ValueError:
        return request.presentation_profile


def _word_budget(response: CanonicalNarrativeResponse) -> tuple[int, int]:
    raw = response.metadata.get("word_budget")
    if isinstance(raw, list | tuple) and len(raw) == 2:
        try:
            minimum = max(0, int(raw[0]))
            maximum = max(minimum, int(raw[1]))
            return minimum, maximum
        except (TypeError, ValueError):
            pass
    words = sum(len(block.text.split()) for block in response.blocks)
    return 0, max(1, words)


def _replay_plan(
    response: CanonicalNarrativeResponse,
    request: TurnPresentationRequest,
) -> NarrativePlan:
    beats = tuple(
        NarrativeBeat(
            beat_id=block.beat_id,
            sequence=block.sequence,
            kind=block.kind,
            purpose=block.purpose,
            speaker_id=block.speaker_id,
            evidence_refs=block.evidence_refs,
            required_claim_refs=block.claim_refs,
            instructions="Replay the already approved canonical block without regeneration.",
            metadata={"idempotent_replay": True},
        )
        for block in response.blocks
    )
    return NarrativePlan(
        request_id=request.request_id,
        mode=str(response.metadata.get("mode") or request.metadata.get("response_mode") or "action"),
        profile=_profile(response, request),
        word_budget=_word_budget(response),
        beats=beats,
        must_answer="",
        metadata={"idempotent_replay": True, "response_id": response.response_id},
    )


def _replay_result(
    service: _NarrativeEngineService,
    request: TurnPresentationRequest,
    response: CanonicalNarrativeResponse,
) -> NarrativeEngineResult:
    stored_identity_hash = str(response.metadata.get("request_identity_hash") or "")
    if (
        response.request_id != request.request_id
        or (
            stored_identity_hash
            and stored_identity_hash != _request_identity_hash(request)
        )
    ):
        raise NarrativeTurnIdentityConflict(
            "turn already belongs to a different presentation request: "
            f"{request.campaign_id}/{request.turn_id}"
        )
    writer_metadata = response.generation.metadata.get("writer_raw_metadata") or {}
    if (
        request.metadata.get("llm_prose_required") is True
        and response.generation.source != "structured_provider"
        # A recorded runtime fallback (the provider failed this turn) replays as published.
        and writer_metadata.get(PROVIDER_REQUIRED_FALLBACK) is not True
    ):
        raise NarrativeProviderRequiredError(
            "LLM-authored dialogue is required; stored deterministic prose is not publishable"
        )

    trace = RetrievalTrace(
        query=request.player_input,
        selected_ids=tuple(response.evidence_used),
        excluded=(),
        candidate_count=len(response.evidence_used),
    )
    retrieval = EvidenceRetrievalResult(evidence=(), trace=trace)
    grants = EvidenceGrantSet(traces={"repository_replay": trace})
    plan = _replay_plan(response, request)
    delivered = service.delivery.prepare(response, request.delivery_mode)
    delivered = replace(
        delivered,
        metadata={
            **_metadata(delivered.metadata),
            "idempotent_replay": True,
            "replay_source": "canonical_repository",
        },
    )
    writer_result = WriterResult(
        blocks=delivered.blocks,
        source="persisted_canonical_replay",
        provider=response.generation.provider,
        model=response.generation.model,
        latency_ms=0.0,
        attempt_count=0,
        raw_metadata={
            "idempotent_replay": True,
            "original_generation_source": response.generation.source,
            "content_hash": response.content_hash,
        },
    )
    validated = ValidatedWriterResult(
        writer_result=writer_result,
        validation=response.validation,
        fallback_used=bool(response.generation.metadata.get("fallback_used")),
    )
    return NarrativeEngineResult(
        request=request,
        retrieval=retrieval,
        grants=grants,
        plan=plan,
        response=delivered,
        writer_result=validated,
    )


class NarrativeEngineService(_NarrativeEngineService):
    """Generate at most once per campaign turn and replay durable canon thereafter."""

    @staticmethod
    def _assemble(
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
        validated: ValidatedWriterResult,
    ) -> CanonicalNarrativeResponse:
        response = _NarrativeEngineService._assemble(
            request,
            plan,
            evidence,
            validated,
        )
        from app.apps.rpg.narration.presentation.dialogue_quality import (
            repair_canonical_dialogue_response,
        )

        response = repair_canonical_dialogue_response(
            response,
            _metadata(request.metadata.get("dialogue_quality_context")),
        )
        return replace(
            response,
            metadata={
                **_metadata(response.metadata),
                "request_identity_hash": _request_identity_hash(request),
            },
        ).with_content_hash()

    def generate(self, request: TurnPresentationRequest) -> NarrativeEngineResult:
        deferred = (
            repository_save_deferred()
            or request.metadata.get("defer_repository_save") is True
        )
        if deferred:
            return super().generate(request)

        existing = self.repository.get_for_turn(request.campaign_id, request.turn_id)
        if existing is not None:
            return _replay_result(self, request, existing)

        try:
            return super().generate(request)
        except NarrativeResponseConflict:
            winner = self.repository.get_for_turn(request.campaign_id, request.turn_id)
            if winner is None:
                raise
            return _replay_result(self, request, winner)
