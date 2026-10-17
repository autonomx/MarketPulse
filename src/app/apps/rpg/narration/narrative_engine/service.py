"""Canonical orchestration service for the isolated Narrative Engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .authority import DeliveryMode
from .contracts import (
    CanonicalNarrativeResponse,
    DeliveryMetadata,
    EvidenceRecord,
    GenerationMetadata,
    TurnPresentationRequest,
)
from .delivery import NarrativeDeliveryCoordinator
from .evidence import (
    EvidenceAccessContext,
    EvidenceBroker,
    EvidenceGrantSet,
    EvidenceQuery,
    EvidenceRetrievalResult,
    RetrievalTrace,
)
from .persistence_policy import repository_save_deferred
from .planner import DeterministicBeatPlanner, NarrativePlan
from .repository import NarrativeResponseRepository
from .validation import ValidatedWriterResult, write_validate_repair
from .writer import NarrativeWriter


def _production_writer() -> NarrativeWriter:
    from app.apps.rpg.narration.narrative_provider import build_production_narrative_writer

    return build_production_narrative_writer()


def _production_repository() -> NarrativeResponseRepository:
    from app.apps.rpg.narration.narrative_repository import build_production_narrative_repository

    return build_production_narrative_repository()


@dataclass(frozen=True)
class NarrativeEngineResult:
    request: TurnPresentationRequest
    retrieval: EvidenceRetrievalResult
    grants: EvidenceGrantSet
    plan: NarrativePlan
    response: CanonicalNarrativeResponse
    writer_result: ValidatedWriterResult


class NarrativeEngineService:
    """Retrieve, plan, write, validate, persist, and deliver one response."""

    def __init__(
        self,
        *,
        evidence_broker: EvidenceBroker,
        writer: NarrativeWriter | None = None,
        planner: DeterministicBeatPlanner | None = None,
        repository: NarrativeResponseRepository | None = None,
        delivery: NarrativeDeliveryCoordinator | None = None,
    ) -> None:
        self.evidence_broker = evidence_broker
        self.writer = writer or _production_writer()
        self.planner = planner or DeterministicBeatPlanner()
        self.repository = repository or _production_repository()
        self.delivery = delivery or NarrativeDeliveryCoordinator()

    def generate(self, request: TurnPresentationRequest) -> NarrativeEngineResult:
        grants, retrieval = self._retrieve_grants(request)
        evidence = grants.all_records()
        plan = self.planner.plan(request, evidence, grants=grants)
        validated = write_validate_repair(
            request,
            plan,
            evidence,
            self.writer,
            allow_deterministic_fallback=(
                request.metadata.get("llm_prose_required") is not True
            ),
        )
        if not validated.validation.passed:
            codes = ",".join(issue.code for issue in validated.validation.issues)
            raise RuntimeError(f"canonical narrative failed validation: {codes}")
        canonical = self._assemble(request, plan, evidence, validated)
        persist_now = not (
            repository_save_deferred()
            or request.metadata.get("defer_repository_save") is True
        )
        persisted = self.repository.save(canonical) if persist_now else canonical
        delivered = self.delivery.prepare(persisted, request.delivery_mode)
        return NarrativeEngineResult(
            request=request,
            retrieval=retrieval,
            grants=grants,
            plan=plan,
            response=delivered,
            writer_result=validated,
        )

    @staticmethod
    def _speaker(request: TurnPresentationRequest) -> str | None:
        return (
            request.target_actor_id
            or str(request.metadata.get("speaker_id") or "")
            or None
        )

    @staticmethod
    def _entity_ids(request: TurnPresentationRequest) -> tuple[str, ...]:
        grounding_ids = request.metadata.get("grounding_entity_ids") or ()
        if isinstance(grounding_ids, str):
            grounding_ids = (grounding_ids,)
        return tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (
                    *request.actor_ids,
                    request.target_actor_id or "",
                    *grounding_ids,
                )
                if str(value or "").strip()
            )
        )

    def _retrieve(
        self,
        request: TurnPresentationRequest,
        access: EvidenceAccessContext,
    ) -> EvidenceRetrievalResult:
        return self.evidence_broker.retrieve(
            EvidenceQuery(
                text=request.player_input,
                entity_ids=self._entity_ids(request),
                limit=int(request.metadata.get("evidence_limit") or 12),
                access=access,
            )
        )

    def _retrieve_grants(
        self,
        request: TurnPresentationRequest,
    ) -> tuple[EvidenceGrantSet, EvidenceRetrievalResult]:
        speaker = self._speaker(request)
        player_id = str(request.metadata.get("player_id") or "player")
        actor_ids = request.actor_ids
        speaker_factions = tuple(request.metadata.get("faction_ids") or ())
        player_factions = tuple(request.metadata.get("player_faction_ids") or ())

        player_result = self._retrieve(
            request,
            EvidenceAccessContext(
                player_id=player_id,
                actor_ids=actor_ids,
                faction_ids=player_factions,
                narrator_mode=False,
            ),
        )
        narrator_result = self._retrieve(
            request,
            EvidenceAccessContext(
                player_id=player_id,
                speaker_id=speaker,
                actor_ids=actor_ids,
                faction_ids=speaker_factions,
                narrator_mode=True,
            ),
        )
        speakers: dict[str, tuple[EvidenceRecord, ...]] = {}
        traces: dict[str, RetrievalTrace] = {
            "player": player_result.trace,
            "narrator": narrator_result.trace,
        }
        speaker_ids = tuple(
            dict.fromkeys(
                value
                for value in (*request.actor_ids, speaker or "")
                if value
            )
        )
        for speaker_id in speaker_ids:
            speaker_result = self._retrieve(
                request,
                EvidenceAccessContext(
                    player_id=player_id,
                    speaker_id=speaker_id,
                    actor_ids=actor_ids,
                    faction_ids=speaker_factions,
                    narrator_mode=False,
                ),
            )
            speakers[speaker_id] = speaker_result.evidence
            traces[f"speaker:{speaker_id}"] = speaker_result.trace

        grants = EvidenceGrantSet(
            player=player_result.evidence,
            narrator=narrator_result.evidence,
            speakers=speakers,
            traces=traces,
        )
        all_records = grants.all_records()
        excluded = tuple(
            sorted(
                {
                    row
                    for trace in traces.values()
                    for row in trace.excluded
                }
            )
        )
        aggregate = EvidenceRetrievalResult(
            evidence=all_records,
            trace=RetrievalTrace(
                query=request.player_input,
                selected_ids=tuple(record.evidence_id for record in all_records),
                excluded=excluded,
                candidate_count=max(
                    (trace.candidate_count for trace in traces.values()),
                    default=len(all_records),
                ),
            ),
        )
        return grants, aggregate

    @staticmethod
    def _assemble(
        request: TurnPresentationRequest,
        plan: NarrativePlan,
        evidence: Sequence[EvidenceRecord],
        validated: ValidatedWriterResult,
    ) -> CanonicalNarrativeResponse:
        writer_result = validated.writer_result
        evidence_used = tuple(
            dict.fromkeys(
                ref
                for block in writer_result.blocks
                for ref in block.evidence_refs
            )
        )
        revision = max(
            1,
            int(request.metadata.get("presentation_revision") or 1),
        )
        response_id = str(
            request.metadata.get("response_id")
            or f"narrative:{request.campaign_id}:{request.turn_id}:{revision}"
        )
        grounding_metadata = {
            "hermes_research_id": str(
                request.metadata.get("hermes_research_id") or ""
            ),
            "canon_topic_count": int(
                request.metadata.get("canon_topic_count") or 0
            ),
            "canon_topic_titles": list(
                request.metadata.get("canon_topic_titles") or ()
            ),
            "lore_search_required": request.metadata.get("lore_search_required") is True,
            "lore_search_performed": request.metadata.get("lore_search_performed") is True,
            "lore_topics_researched": int(
                request.metadata.get("lore_topics_researched") or 0
            ),
            "lore_topic_titles": list(
                request.metadata.get("lore_topic_titles") or ()
            ),
            "grounding_entity_ids": list(
                request.metadata.get("grounding_entity_ids") or ()
            ),
            "hermes_source_ids": list(
                request.metadata.get("hermes_source_ids") or ()
            ),
            "campaign_bible_revision": int(
                request.metadata.get("campaign_bible_revision") or 0
            ),
            "campaign_bible_hash": str(
                request.metadata.get("campaign_bible_hash") or ""
            ),
            "campaign_bible_evidence_count": int(
                request.metadata.get("campaign_bible_evidence_count") or 0
            ),
            "runtime_evidence_count": int(
                request.metadata.get("runtime_evidence_count") or 0
            ),
            "hermes_evidence_count": int(
                request.metadata.get("hermes_evidence_count") or 0
            ),
            "grounding_passed": validated.validation.passed,
        }
        return CanonicalNarrativeResponse(
            response_id=response_id,
            request_id=request.request_id,
            turn_id=request.turn_id,
            campaign_id=request.campaign_id,
            revision=revision,
            blocks=writer_result.blocks,
            evidence_used=evidence_used,
            validation=validated.validation,
            generation=GenerationMetadata(
                source=writer_result.source,
                provider=writer_result.provider,
                model=writer_result.model,
                latency_ms=writer_result.latency_ms,
                attempt_count=writer_result.attempt_count,
                evidence_count=len(evidence),
                beat_count=len(plan.beats),
                hermes_used=request.metadata.get("hermes_used") is True,
                metadata={
                    "fallback_used": validated.fallback_used,
                    "retrieval_selected_ids": list(evidence_used),
                    "writer_raw_metadata": dict(writer_result.raw_metadata or {}),
                    "evidence_grants": {
                        "player": int(plan.metadata.get("player_evidence_count") or 0),
                        "narrator": int(plan.metadata.get("narrator_evidence_count") or 0),
                        "speaker": int(plan.metadata.get("speaker_evidence_count") or 0),
                    },
                    **grounding_metadata,
                },
            ),
            delivery=DeliveryMetadata(
                mode=DeliveryMode.BLOCKING,
                status="approved",
            ),
            metadata={
                "mode": plan.mode,
                "profile": plan.profile.value,
                "word_budget": list(plan.word_budget),
                "grounding": grounding_metadata,
            },
        ).with_content_hash()
