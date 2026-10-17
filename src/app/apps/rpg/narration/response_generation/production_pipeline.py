from __future__ import annotations

import logging

import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from app.apps.rpg.narration.runtime_narration_legacy import (
    build_runtime_narration_payload as build_legacy_runtime_narration_payload,
)

from .claim_ledger import ClaimLedger
from .context_compiler import EvidenceCard, NarrationContext, NarrationContextCompiler
from .contracts import (
    CandidateSource,
    ResponseCandidate,
    ResponseMode,
    ResponseRequest,
    SectionType,
    SemanticResponsePlan,
    SemanticSection,
    coerce_response_mode,
)
from .fallback_library import DeterministicFallbackLibrary, FallbackInput
from .forward_motion import ForwardMotionPlan, ForwardMotionPolicy, RecoveryHistoryEntry
from .hermes_adapter import HermesRecoveryResult, RpgHermesRecoveryAdapter
from .orchestration import RpgResponseGenerator, semantic_plan_from_legacy_payload
from .profiles import ResponseGenerationProfile, ResponseProfileRegistry
from .proposal_policy import (
    ProposalBudget,
    ProposalPolicy,
    ProposalRisk,
    ProposalStore,
    WorldProposal,
)
from .recovery import LocalRecoveryAnalysis, LocalRecoveryCoordinator
from .retrieval import EvidenceRecord, build_retrieval_sources
from .rollout import ResponseRolloutController, rollout_stage_from_context
from .truth_lifetime import TruthLifetime
from .validated_delivery import ValidatedDeliverySession

logger = logging.getLogger(__name__)


CANONICAL_NARRATION_SOURCE = "rpg_response_generator_v1"
_HARD_CLAIM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "currency": ("gold", "silver", "copper", "coin", "coins", "paid", "payment"),
    "inventory": ("inventory", "item", "items", "sword", "torch", "rope", "key"),
    "combat": ("damage", "wound", "wounded", "blood", "dead", "dies", "defeated", "killed", "attack"),
    "location": ("arrive", "arrives", "travel", "travels", "enter", "enters", "leave", "leaves", "reach", "reaches"),
    "quest": ("quest", "objective", "mission", "completed", "complete"),
    "relationship": ("trust", "loyalty", "reputation", "relationship", "faction"),
}
_SOURCE_TO_TRUTH = {
    "resolved_turn": "confirmed_fact",
    "scene": "confirmed_fact",
    "speaker": "npc_belief",
    "party": "npc_belief",
    "journal": "retrieved_lore",
    "campaign": "retrieved_lore",
    "lorebook": "retrieved_lore",
    "approved_proposal": "generated_proposal",
}


class ProfileBoundProvider:
    """Apply the resolved authoritative profile before calling a provider."""

    def __init__(self, provider: Any, profile: ResponseGenerationProfile) -> None:
        self.provider = provider
        self.profile = profile
        self.applied: dict[str, Any] = {
            "provider": profile.provider,
            "model": profile.model,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "timeout_seconds": profile.timeout_seconds,
            "retry_count": profile.retry_count,
        }
        self.calls: list[dict[str, Any]] = []
        self._apply_attributes()

    def _apply_attributes(self) -> None:
        for name, value in (
            ("model", self.profile.model),
            ("model_name", self.profile.model),
            ("temperature", self.profile.temperature),
            ("timeout", self.profile.timeout_seconds),
            ("timeout_seconds", self.profile.timeout_seconds),
        ):
            if not hasattr(self.provider, name):
                continue
            try:
                setattr(self.provider, name, value)
            except Exception:
                logger.debug("suppressed error in %s", "ProfileBoundProvider._apply_attributes", exc_info=True)
                continue

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.provider, method_name)
        effective = dict(kwargs)
        effective["max_tokens"] = self.profile.max_tokens
        effective.setdefault("temperature", self.profile.temperature)
        effective.setdefault("model", self.profile.model)
        self.calls.append({"method": method_name, **effective})
        attempts: tuple[dict[str, Any], ...] = (
            effective,
            {key: value for key, value in effective.items() if key != "model"},
            {
                key: value
                for key, value in effective.items()
                if key not in {"model", "temperature"}
            },
            {"max_tokens": self.profile.max_tokens},
            {},
        )
        last_error: Exception | None = None
        for candidate in attempts:
            try:
                return method(*args, **candidate)
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return method(*args)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.provider, name)
        if callable(value):
            return lambda *args, **kwargs: self._call(name, *args, **kwargs)
        return value


class HermesSidecarRecoveryClient:
    """Adapt the existing proposal-only Hermes RPG endpoint to the recovery protocol."""

    def __init__(self, sidecar: Any | None = None) -> None:
        if sidecar is None:
            from app.providers.hermes_client import HermesSidecarClient

            sidecar = HermesSidecarClient(timeout=4.0)
        self._sidecar = sidecar

    def plan(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        base_url = str(getattr(self._sidecar, "base_url", "") or "").rstrip("/")
        if not base_url:
            result = self._sidecar.rpg_plan({"context": dict(payload)})
            return dict(result) if isinstance(result, Mapping) else {}
        from app.runtime.http_client import shared_http_client

        headers = {"Content-Type": "application/json"}
        api_key = getattr(self._sidecar, "api_key", None)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request_payload = {
            "model": "hermes-agent",
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return one JSON object only. You are a read-only RPG lore "
                        "researcher and proposal generator. Never execute tools, mutate "
                        "state, expose hidden facts, or choose for the player. Use only "
                        "these fields: evidence, inferences, uncertainty, "
                        "forward_strategies, proposals, executes, state_mutation_allowed."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(dict(payload), sort_keys=True),
                },
            ],
        }
        response = shared_http_client("hermes").post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            data=json.dumps(request_payload),
            timeout=min(float(timeout_seconds), 4.0),
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return json.loads(content.strip().strip("`").removeprefix("json").strip())
        return dict(content) if isinstance(content, Mapping) else {}


class ProductionCandidateAdapter:
    """Build provider, local-recovery, Hermes, and deterministic candidates."""

    def __init__(
        self,
        *,
        legacy_payload: Mapping[str, Any],
        context: NarrationContext,
        analysis: LocalRecoveryAnalysis,
        forward_plan: ForwardMotionPlan,
        hermes_result: HermesRecoveryResult | None,
        truth_records: Sequence[Mapping[str, Any]],
    ) -> None:
        self.legacy_payload = dict(legacy_payload)
        self.context = context
        self.analysis = analysis
        self.forward_plan = forward_plan
        self.hermes_result = hermes_result
        self.truth_records = tuple(dict(row) for row in truth_records)

    def __call__(self, request: ResponseRequest) -> Sequence[ResponseCandidate]:
        candidates: list[ResponseCandidate] = []
        legacy = self._legacy_candidate(request)
        if legacy is not None:
            candidates.append(legacy)
        hermes = self._hermes_candidate(request)
        if hermes is not None:
            candidates.append(hermes)
        candidates.append(self._fallback_candidate(request))
        return tuple(candidates)

    def _legacy_candidate(self, request: ResponseRequest) -> ResponseCandidate | None:
        plan = semantic_plan_from_legacy_payload(
            self.legacy_payload,
            mode=self.context.response_mode,
        )
        sections = tuple(
            self._normalize_section(section, request)
            for section in plan.sections
            if section.text.strip()
        )
        if not sections:
            return None
        recovery_mode = self.context.response_mode in {
            ResponseMode.RECOVERY,
            ResponseMode.INVESTIGATION,
        }
        return ResponseCandidate(
            candidate_id=f"{request.turn_id}:provider-or-legacy",
            plan=replace(
                plan,
                sections=sections,
                forward_strategy=self.forward_plan.strategy,
                metadata={
                    **dict(plan.metadata),
                    "takes_player_choice": False,
                    "recovery_mode": recovery_mode,
                },
            ),
            source=(
                CandidateSource.PROVIDER
                if str(self.legacy_payload.get("source") or "").startswith("provider")
                else CandidateSource.LEGACY_RUNTIME
            ),
            current_turn_relevance=0.45 if recovery_mode else 0.9,
            forward_motion=0.35 if recovery_mode else 0.75,
            specificity=0.7,
            naturalness=0.75,
            provider_metadata={
                "legacy_source": self.legacy_payload.get("source") or "",
                "grounded_safe_fallback": bool(
                    self.legacy_payload.get("grounding_fallback")
                ),
            },
        )

    def _normalize_section(
        self,
        section: SemanticSection,
        request: ResponseRequest,
    ) -> SemanticSection:
        speaker_id = section.speaker_id
        if section.section_type is SectionType.NPC_DIALOGUE:
            speaker_id = _resolve_speaker_id(
                speaker_id,
                request,
                self.context.claim_ledger,
            )
        claim_refs = list(section.claim_refs)
        allowed = set(self.context.claim_ledger.allowed_claim_refs)
        if section.section_type in {
            SectionType.ACTION,
            SectionType.RESULT,
            SectionType.STATE_CHANGE,
        } and "turn.resolved" in set(
            request.authoritative_turn_result.get("allowed_claim_refs", ())
        ):
            claim_refs.append("turn.resolved")
        claim_refs.extend(
            _hard_claim_refs_from_text(
                section.text,
                allowed_claim_refs=allowed,
                turn_id=request.turn_id,
            )
        )
        factual = bool(claim_refs) or section.section_type in {
            SectionType.ACTION,
            SectionType.RESULT,
            SectionType.STATE_CHANGE,
        }
        return replace(
            section,
            speaker_id=speaker_id,
            claim_refs=tuple(dict.fromkeys(claim_refs)),
            metadata={**dict(section.metadata), "factual": factual},
        )

    def _hermes_candidate(self, request: ResponseRequest) -> ResponseCandidate | None:
        result = self.hermes_result
        if result is None or not result.ok:
            return None
        sections: list[SemanticSection] = []
        for index, inference in enumerate(result.inferences[:2]):
            sections.append(
                SemanticSection(
                    section_id=f"hermes.inference.{index}",
                    section_type=SectionType.NARRATION,
                    text=inference,
                    soft_truth_refs=(f"hermes.inference.{index}",),
                    metadata={"factual": False, "source": "hermes"},
                )
            )
        for index, strategy in enumerate(result.forward_strategies[:2]):
            sections.append(
                SemanticSection(
                    section_id=f"hermes.strategy.{index}",
                    section_type=SectionType.CHOICE,
                    text=strategy,
                    metadata={"factual": False, "offer_only": True},
                )
            )
        if not sections:
            return None
        return ResponseCandidate(
            candidate_id=f"{request.turn_id}:hermes",
            plan=SemanticResponsePlan(
                mode=self.context.response_mode,
                sections=tuple(sections),
                forward_strategy=self.forward_plan.strategy,
                proposal_refs=tuple(
                    proposal.proposal_id for proposal in result.proposals
                ),
                metadata={"takes_player_choice": False, "source": "hermes"},
            ),
            source=CandidateSource.HERMES_ASSISTED,
            current_turn_relevance=0.82,
            forward_motion=0.85,
            specificity=0.72,
            naturalness=0.68,
        )

    def _fallback_candidate(self, request: ResponseRequest) -> ResponseCandidate:
        evidence_map = {
            record.evidence_id: str(record.content)
            for record in self.analysis.retrieval.evidence
            if str(record.content or "").strip()
        }
        selected_refs = tuple(
            ref
            for ref in self.forward_plan.answer_evidence_ids
            if ref in evidence_map
        )
        visible_facts = {
            ref: evidence_map[ref]
            for ref in selected_refs
        }
        fallback = DeterministicFallbackLibrary().candidate(
            FallbackInput(
                turn_id=request.turn_id,
                player_input=request.player_input,
                mode=self.context.response_mode,
                forward_plan=self.forward_plan,
                speaker_id=request.speaker_id,
                visible_facts=visible_facts,
                soft_truth_refs=selected_refs,
            )
        )
        return replace(
            fallback,
            plan=replace(
                fallback.plan,
                metadata={
                    **dict(fallback.plan.metadata),
                    "takes_player_choice": False,
                },
            ),
        )


class RpgProductionResponsePipeline:
    """Canonical production integration for runtime, scene, and recovery responses."""

    def __init__(
        self,
        *,
        context_compiler: NarrationContextCompiler | None = None,
        recovery: LocalRecoveryCoordinator | None = None,
        forward_policy: ForwardMotionPolicy | None = None,
        profile_registry: ResponseProfileRegistry | None = None,
        proposal_policy: ProposalPolicy | None = None,
        hermes_adapter_factory: Callable[[float], RpgHermesRecoveryAdapter] | None = None,
    ) -> None:
        self.context_compiler = context_compiler or NarrationContextCompiler()
        self.recovery = recovery or LocalRecoveryCoordinator()
        self.forward_policy = forward_policy or ForwardMotionPolicy()
        self.profile_registry = profile_registry or ResponseProfileRegistry()
        self.proposal_policy = proposal_policy or ProposalPolicy(ProposalBudget())
        self.hermes_adapter_factory = hermes_adapter_factory

    def build_runtime_payload(
        self,
        *,
        provider: Any = None,
        player_action: str,
        simulation_state: Mapping[str, Any] | None = None,
        turn_contract: Mapping[str, Any] | None = None,
        prefer_provider: bool = True,
        max_tokens: int | None = None,
        max_provider_attempts: int | None = None,
    ) -> dict[str, Any]:
        state = _mapping(simulation_state)
        contract = _mapping(turn_contract)
        turn_id = _turn_id(contract, state, player_action)
        base_result = _authoritative_result(contract, state)
        mode = _response_mode(base_result)
        recovery_needed = _recovery_needed(base_result, mode)
        profile, ignored = self.profile_registry.resolve_from_request(
            mode,
            _provider_policy(contract, state),
            recovery_needed=recovery_needed,
        )
        wrapped_provider = (
            ProfileBoundProvider(provider, profile)
            if provider is not None and prefer_provider and profile.use_provider
            else None
        )
        legacy_payload = build_legacy_runtime_narration_payload(
            provider=wrapped_provider,
            player_action=player_action,
            simulation_state=state,
            turn_contract=contract,
            prefer_provider=bool(wrapped_provider),
            max_tokens=profile.max_tokens,
            max_provider_attempts=profile.retry_count + 1,
        )
        return self.finalize_payload(
            legacy_payload,
            player_input=player_action,
            authoritative_turn_result=base_result,
            simulation_state=simulation_state,
            turn_contract=contract,
            profile=profile,
            ignored_profile_overrides=ignored,
            provider_profile_applied=(
                dict(wrapped_provider.applied) if wrapped_provider is not None else {}
            ),
            runtime_mode="runtime",
        )

    def finalize_payload(
        self,
        legacy_payload: Mapping[str, Any],
        *,
        player_input: str,
        authoritative_turn_result: Mapping[str, Any] | None = None,
        simulation_state: Mapping[str, Any] | None = None,
        turn_contract: Mapping[str, Any] | None = None,
        profile: ResponseGenerationProfile | None = None,
        ignored_profile_overrides: Sequence[str] = (),
        provider_profile_applied: Mapping[str, Any] | None = None,
        runtime_mode: str = "canonical",
        on_chunk: Callable[[str], None] | None = None,
        hermes_client: Any | None = None,
    ) -> dict[str, Any]:
        original = dict(legacy_payload or {})
        state = _mapping(simulation_state)
        contract = _mapping(turn_contract)
        turn_id = _turn_id(contract, state, player_input)
        base_result = {
            **_authoritative_result(contract, state),
            **_mapping(authoritative_turn_result),
        }
        preliminary_request = ResponseRequest(
            turn_id=turn_id,
            player_input=player_input,
            authoritative_turn_result=base_result,
            session_id=str(base_result.get("session_id") or state.get("session_id") or ""),
            world_id=str(base_result.get("world_id") or state.get("world_id") or ""),
            scene_id=str(base_result.get("scene_id") or state.get("scene_id") or ""),
            speaker_id=_speaker_id(original, base_result),
            runtime_mode=runtime_mode,
            provider_policy=_provider_policy(contract, state),
            feature_flags={"strict_claim_refs": True},
            legacy_payload=dict(legacy_payload),
        )
        evidence_sources = _retrieval_sources(original, state)
        analysis = self.recovery.analyze(
            player_input,
            # Name tuples: the classifier reads mappings, so these are not used yet.
            known_entities=cast(Any, _known_entities(state)),
            known_locations=cast(Any, _known_locations(state)),
            supported_mechanics=tuple(
                str(value)
                for value in original.get("supported_mechanics", ())
                if str(value)
            ),
            retrieval_sources=evidence_sources,
            speaker_id=preliminary_request.speaker_id,
            narrator_mode=not bool(preliminary_request.speaker_id),
            hermes_allowed=True,
        )
        evidence_cards = tuple(
            EvidenceCard(
                evidence_id=row.evidence_id,
                source=row.source,
                content=row.content,
                visibility=row.visibility,
                confidence=row.confidence,
                entity_ids=row.entity_ids,
                timestamp=row.timestamp,
            )
            for row in analysis.retrieval.evidence
        )
        context = self.context_compiler.compile(
            preliminary_request,
            visible_state=state,
            evidence=evidence_cards,
        )
        policy_result = {
            **original,
            **context.claim_ledger.as_policy_payload(),
            "production_rpg_response": True,
            "strict_claim_refs": True,
            "grounding_required": True,
            "allowed_claim_refs": [
                *context.claim_ledger.allowed_claim_refs,
                "turn.resolved",
            ],
        }
        mode = context.response_mode
        recovery_needed = _recovery_needed(policy_result, mode)
        if profile is None:
            profile, ignored_profile_overrides = self.profile_registry.resolve_from_request(
                mode,
                preliminary_request.provider_policy,
                recovery_needed=recovery_needed,
            )
        forward_plan = self.forward_policy.select(
            analysis,
            history=_recovery_history(state),
            target=str(
                analysis.intent.selected.entities[0]
                if analysis.intent.selected.entities
                else ""
            ),
            clear_player_intent=bool(base_result.get("clear_player_intent")),
            mechanic_resolved=bool(base_result.get("mechanic_resolved")),
        )
        hermes_result = self._hermes_result(
            analysis,
            forward_plan,
            context,
            profile,
            state,
            explicit_client=hermes_client,
        )
        store, truth_records, proposal_results = self._apply_proposals(
            state,
            hermes_result,
            turn_id=turn_id,
            scene_id=preliminary_request.scene_id,
        )
        policy_result.update(
            {
                "approved_proposal_refs": [
                    result.truth.truth_ref
                    for result in proposal_results
                    if result.truth is not None
                ],
                "recovery_needed": recovery_needed,
                "resolver_status": (
                    "unresolved" if recovery_needed else "resolved"
                ),
                "response_mode": mode.value,
            }
        )
        request = replace(
            preliminary_request,
            authoritative_turn_result=policy_result,
            runtime_mode=(
                "supported_mechanic"
                if base_result.get("mechanic_resolved")
                else runtime_mode
            ),
        )
        adapter = ProductionCandidateAdapter(
            legacy_payload=original,
            context=context,
            analysis=analysis,
            forward_plan=forward_plan,
            hermes_result=hermes_result,
            truth_records=truth_records,
        )
        rendered = RpgResponseGenerator(candidate_adapter=adapter).generate(request)
        rendered = replace(
            rendered,
            truth_classes=tuple(
                dict.fromkeys(
                    str(row.get("truth_class") or "")
                    for row in truth_records
                    if row.get("truth_class")
                )
            ),
            lifetimes=tuple(
                dict.fromkeys(
                    str(row.get("lifetime") or "")
                    for row in truth_records
                    if row.get("lifetime")
                )
            ),
            metadata={
                **dict(rendered.metadata),
                "response_profile": profile.debug_payload(),
                "ignored_runtime_profile_overrides": list(
                    ignored_profile_overrides
                ),
                "provider_profile_applied": dict(provider_profile_applied or {}),
                "narration_context": context.as_dict(),
                "claim_ledger": context.claim_ledger.as_policy_payload(),
                "intent_analysis": _intent_payload(analysis),
                "retrieval": _retrieval_payload(analysis),
                "recovery_plan": _forward_payload(forward_plan),
                "hermes": _hermes_payload(hermes_result),
                "proposal_results": [
                    _proposal_result_payload(result)
                    for result in proposal_results
                ],
                "truth_records": list(truth_records),
            },
        )
        rollout = ResponseRolloutController().config(
            rollout_stage_from_context({**state, **contract})
        )
        selected_text = (
            rendered.text
            if rollout.publishes_canonical and rendered.text.strip()
            else str(original.get("narration") or "").strip()
        )
        delivery = ValidatedDeliverySession.prepare(rendered, profile)
        if rollout.publishes_canonical and on_chunk is not None:
            while (unit := delivery.next_unit()) is not None:
                on_chunk(unit.text)
                delivery.acknowledge(unit)
        checkpoint = delivery.checkpoint()
        payload = dict(original)
        if selected_text:
            payload["narration"] = selected_text
        payload["source"] = (
            "provider_runtime_narration"
            if rollout.publishes_canonical
            else "canonical_shadow_runtime_narration"
        )
        payload["canonical_response_source"] = CANONICAL_NARRATION_SOURCE
        payload["canonical_response"] = {
            "source": CANONICAL_NARRATION_SOURCE,
            "mode": rendered.mode.value,
            "text": rendered.text,
            "approved_section_ids": list(rendered.approved_section_ids),
            "resolved_claim_refs": list(rendered.resolved_claim_refs),
            "quality_report": dict(rendered.quality_report),
            "repair_history": list(rendered.repair_history),
            "delivery_units": [unit.text for unit in delivery.units],
            "delivery_checkpoint": {
                "state": checkpoint.state.value,
                "prepared_unit_ids": list(checkpoint.prepared_unit_ids),
                "delivered_unit_ids": list(checkpoint.delivered_unit_ids),
                "next_index": checkpoint.next_index,
                "interruption_reason": checkpoint.interruption_reason,
                "validation_token": checkpoint.validation_token,
            },
            "metadata": dict(rendered.metadata),
            "rollout": rollout.as_dict(),
        }
        payload["rollout_comparison"] = ResponseRolloutController().compare(
            turn_id=turn_id,
            legacy_text=str(original.get("narration") or ""),
            canonical_text=rendered.text,
            authoritative_state_hash_before="unchanged",
            authoritative_state_hash_after="unchanged",
        )
        payload["response_soft_truth"] = store.as_dict()
        return payload

    def _hermes_result(
        self,
        analysis: LocalRecoveryAnalysis,
        forward_plan: ForwardMotionPlan,
        context: NarrationContext,
        profile: ResponseGenerationProfile,
        state: Mapping[str, Any],
        *,
        explicit_client: Any | None,
    ) -> HermesRecoveryResult | None:
        if not profile.allow_hermes or not analysis.hermes_eligible:
            return None
        client = explicit_client
        if client is None:
            if not _hermes_enabled(state):
                return None
            client = HermesSidecarRecoveryClient()
        factory = self.hermes_adapter_factory or (
            lambda timeout: RpgHermesRecoveryAdapter(
                client,
                timeout_seconds=min(timeout, 4.0),
            )
        )
        adapter = factory(profile.timeout_seconds)
        return adapter.recover(
            campaign_id=str(state.get("campaign_id") or "runtime"),
            lore_version=str(state.get("lore_version") or "v1"),
            query=context.player_input,
            unresolved_question=context.must_answer,
            evidence=tuple(
                {
                    "evidence_id": row.evidence_id,
                    "source": row.source,
                    "content": row.content,
                    "visibility": row.visibility,
                    "confidence": row.confidence,
                }
                for row in analysis.retrieval.evidence
            ),
            local_strategy=forward_plan.strategy,
        )

    def _apply_proposals(
        self,
        state: Mapping[str, Any],
        hermes_result: HermesRecoveryResult | None,
        *,
        turn_id: str,
        scene_id: str,
    ) -> tuple[ProposalStore, tuple[dict[str, Any], ...], tuple[Any, ...]]:
        store_payload = _mapping(state.get("response_soft_truth"))
        store = ProposalStore.from_dict(store_payload) if store_payload else ProposalStore()
        turn_number = int(state.get("turn_index") or state.get("tick") or 0)
        store.garbage_collect(current_turn=turn_number, scene_id=scene_id)
        results = []
        if hermes_result is not None and hermes_result.ok:
            for row in hermes_result.proposals:
                proposal = WorldProposal(
                    proposal_id=row.proposal_id,
                    proposal_type=row.proposal_type,
                    summary=row.summary,
                    content=row.content,
                    risk=ProposalRisk(str(row.risk or "low")),
                    requested_lifetime=TruthLifetime(
                        str(row.lifetime or "turn")
                    ),
                    source="hermes_recovery",
                    seed=_stable_seed(turn_id, row.proposal_id),
                    provenance_refs=tuple(row.evidence_refs),
                    scene_id=scene_id,
                    created_turn=turn_number,
                    created_turn_id=turn_id,
                    visibility=row.visibility,
                    confidence=row.confidence,
                    world_consistent=not bool(row.conflicts),
                    metadata={"proposal_only": True},
                )
                result = self.proposal_policy.evaluate(
                    proposal,
                    existing=store.truths.values(),
                    turn_id=turn_id,
                )
                results.append(result)
                store.apply(result)
        if isinstance(state, dict):
            state["response_soft_truth"] = store.as_dict()
        truth_records = tuple(row.as_dict() for row in store.truths.values())
        return store, truth_records, tuple(results)


def _authoritative_result(
    contract: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = _mapping(
        contract.get("resolved_result")
        or contract.get("resolved_action")
        or contract.get("result")
    )
    state_delta = _mapping(contract.get("state_delta") or resolved.get("state_delta"))
    mechanic_resolved = bool(
        contract.get("ok")
        or resolved.get("ok")
        or state_delta
    )
    response_mode = (
        contract.get("response_mode")
        or contract.get("semantic_family")
        or resolved.get("response_mode")
        or resolved.get("semantic_family")
        or contract.get("action_type")
        or resolved.get("action_type")
        or "recovery"
    )
    return {
        **dict(contract),
        **resolved,
        "resolved_result": resolved,
        "state_delta": state_delta,
        "mechanic_resolved": mechanic_resolved,
        "response_mode": str(response_mode),
        "production_rpg_response": True,
        "session_id": state.get("session_id") or contract.get("session_id"),
        "scene_id": state.get("scene_id") or state.get("location_id"),
    }


def _response_mode(result: Mapping[str, Any]) -> ResponseMode:
    if _recovery_needed(result, None):
        return ResponseMode.RECOVERY
    return coerce_response_mode(
        result.get("response_mode")
        or result.get("semantic_family")
        or result.get("action_type"),
        ResponseMode.ACTION,
    )


def _recovery_needed(
    result: Mapping[str, Any],
    mode: ResponseMode | None,
) -> bool:
    if result.get("recovery_needed"):
        return True
    if str(result.get("resolver_status") or "").casefold() in {
        "unresolved",
        "partial",
        "unsupported",
        "unknown",
    }:
        return True
    if mode in {ResponseMode.RECOVERY, ResponseMode.INVESTIGATION}:
        return not bool(result.get("mechanic_resolved"))
    return not bool(result.get("mechanic_resolved"))


def _turn_id(
    contract: Mapping[str, Any],
    state: Mapping[str, Any],
    player_input: str,
) -> str:
    direct = contract.get("turn_id") or state.get("turn_id")
    if direct:
        return str(direct)
    payload = json.dumps(
        [
            state.get("session_id") or "runtime",
            state.get("tick") or state.get("turn_index") or 0,
            player_input,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"turn:{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


def _provider_policy(
    contract: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **_mapping(state.get("response_profile")),
        **_mapping(contract.get("response_profile")),
        **_mapping(contract.get("provider_policy")),
    }


def _retrieval_sources(
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Iterable[EvidenceRecord | Mapping[str, Any]]]:
    explicit = _mapping(state.get("response_retrieval_sources"))
    if explicit:
        return build_retrieval_sources(
            resolved_turn=_records(explicit.get("resolved_turn")),
            scene=_records(explicit.get("scene")),
            speaker=_records(explicit.get("speaker")),
            party=_records(explicit.get("party")),
            journal=_records(explicit.get("journal")),
            campaign=_records(explicit.get("campaign")),
            lorebook=_records(explicit.get("lorebook")),
            approved_proposals=_records(explicit.get("approved_proposal")),
        )
    rows: dict[str, list[dict[str, Any]]] = {
        "resolved_turn": [],
        "scene": [],
        "speaker": [],
        "party": [],
        "journal": [],
        "campaign": [],
        "lorebook": [],
        "approved_proposal": [],
    }
    narration = str(payload.get("narration") or "").strip()
    if narration:
        rows["resolved_turn"].append(
            {
                "evidence_id": "legacy.visible_narration",
                "content": narration,
                "visibility": "player_visible",
                "confidence": 0.8,
            }
        )
    for source, key in (
        ("scene", "visible_scene_facts"),
        ("speaker", "speaker_knowledge"),
        ("party", "party_knowledge"),
        ("journal", "journal_entries"),
        ("campaign", "campaign_history"),
        ("lorebook", "lorebook"),
    ):
        for index, value in enumerate(_iter_values(state.get(key))):
            if isinstance(value, Mapping):
                row = dict(value)
                row.setdefault("evidence_id", f"{source}.{index}")
                row.setdefault("content", value.get("text") or value.get("summary") or value)
            else:
                row = {"evidence_id": f"{source}.{index}", "content": value}
            rows[source].append(row)
    soft_store = _mapping(state.get("response_soft_truth"))
    for row in soft_store.get("truths", ()):
        if isinstance(row, Mapping) and row.get("visibility") != "hidden":
            rows["approved_proposal"].append(
                {
                    "evidence_id": row.get("truth_ref"),
                    "content": row.get("content"),
                    "visibility": row.get("visibility") or "player_visible",
                    "confidence": row.get("confidence") or 0.5,
                    "metadata": row.get("metadata") or {},
                }
            )
    return build_retrieval_sources(**rows)


def _records(value: Any) -> tuple[Any, ...]:
    return tuple(_iter_values(value))


def _iter_values(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(value.values())
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _known_entities(state: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for source in (
        state.get("npc_index"),
        state.get("entities"),
        state.get("present_npcs"),
    ):
        if isinstance(source, Mapping):
            for key, row in source.items():
                values.append(str(key))
                if isinstance(row, Mapping):
                    values.append(str(row.get("name") or ""))
        else:
            values.extend(str(value) for value in _iter_values(source))
    return tuple(dict.fromkeys(value for value in values if value))


def _known_locations(state: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for source in (
        state.get("locations"),
        state.get("location_index"),
        state.get("known_locations"),
    ):
        if isinstance(source, Mapping):
            for key, row in source.items():
                values.append(str(key))
                if isinstance(row, Mapping):
                    values.append(str(row.get("name") or ""))
        else:
            values.extend(str(value) for value in _iter_values(source))
    current = state.get("location_id") or state.get("current_location")
    if current:
        values.append(str(current))
    return tuple(dict.fromkeys(value for value in values if value))


def _speaker_id(
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
) -> str:
    npc = _mapping(payload.get("npc"))
    return str(
        npc.get("speaker_id")
        or npc.get("speaker")
        or result.get("speaker_id")
        or result.get("target_id")
        or ""
    )


def _resolve_speaker_id(
    value: str,
    request: ResponseRequest,
    ledger: ClaimLedger,
) -> str:
    if value in set(ledger.allowed_speakers):
        return value
    normalized = _slug(value)
    for speaker in ledger.allowed_speakers:
        if _slug(speaker) == normalized or _slug(speaker).endswith(normalized):
            return speaker
    return request.speaker_id or value


def _hard_claim_refs_from_text(
    text: str,
    *,
    allowed_claim_refs: set[str],
    turn_id: str,
) -> tuple[str, ...]:
    lowered = str(text or "").casefold()
    refs: list[str] = []
    for family, keywords in _HARD_CLAIM_KEYWORDS.items():
        if not any(keyword in lowered for keyword in keywords):
            continue
        matching = sorted(
            ref for ref in allowed_claim_refs if ref.startswith(f"{family}.")
        )
        if matching:
            refs.extend(matching)
        else:
            refs.append(f"unsupported.{family}.{_slug(turn_id)}")
    return tuple(dict.fromkeys(refs))


def _recovery_history(state: Mapping[str, Any]) -> tuple[RecoveryHistoryEntry, ...]:
    rows = []
    for row in state.get("response_recovery_history", ()):
        if not isinstance(row, Mapping):
            continue
        rows.append(
            RecoveryHistoryEntry(
                turn_id=str(row.get("turn_id") or ""),
                strategy=str(row.get("strategy") or ""),
                target=str(row.get("target") or ""),
                produced_progress=bool(row.get("produced_progress")),
            )
        )
    return tuple(rows)


def _hermes_enabled(state: Mapping[str, Any]) -> bool:
    explicit = state.get("hermes_enabled")
    if explicit is not None:
        return bool(explicit)
    try:
        from app.providers.hermes_status import hermes_runtime_config

        return bool(hermes_runtime_config().enabled)
    except Exception:
        return False


def _intent_payload(analysis: LocalRecoveryAnalysis) -> dict[str, Any]:
    return {
        "selected": {
            "intent": analysis.intent.selected.intent,
            "affordance": analysis.intent.selected.affordance,
            "confidence": analysis.intent.selected.confidence,
            "ambiguity": analysis.intent.selected.ambiguity,
            "entities": list(analysis.intent.selected.entities),
            "underlying_goal": analysis.intent.selected.underlying_goal,
        },
        "alternatives": [
            {
                "intent": row.intent,
                "affordance": row.affordance,
                "confidence": row.confidence,
            }
            for row in analysis.intent.hypotheses[1:]
        ],
    }


def _retrieval_payload(analysis: LocalRecoveryAnalysis) -> dict[str, Any]:
    return {
        "knowledge_status": analysis.retrieval.knowledge_status,
        "local_hit": analysis.retrieval.local_hit,
        "evidence": [
            {
                "evidence_id": row.evidence_id,
                "source": row.source,
                "visibility": row.visibility,
                "confidence": row.confidence,
            }
            for row in analysis.retrieval.evidence
        ],
        "hidden_evidence_ids": list(analysis.retrieval.hidden_evidence_ids),
        "conflicting_evidence_ids": list(
            analysis.retrieval.conflicting_evidence_ids
        ),
        "trace": list(analysis.retrieval.trace),
    }


def _forward_payload(plan: ForwardMotionPlan) -> dict[str, Any]:
    return {
        "forward_strategy": plan.strategy,
        "outcome": plan.outcome,
        "rationale": plan.rationale,
        "options": list(plan.options),
        "offer_only": plan.offer_only,
        "starts_path": plan.starts_path,
        "irreversible": plan.irreversible,
        "requires_player_confirmation": plan.requires_player_confirmation,
        "state_mutation_allowed": plan.state_mutation_allowed,
    }


def _hermes_payload(result: HermesRecoveryResult | None) -> dict[str, Any]:
    if result is None:
        return {"status": "not_invoked", "state_changed": False}
    return {
        "status": result.status,
        "ok": result.ok,
        "reason": result.reason,
        "evidence": [row.as_dict() for row in result.evidence],
        "inferences": list(result.inferences),
        "uncertainty": list(result.uncertainty),
        "forward_strategies": list(result.forward_strategies),
        "proposals": [row.as_dict() for row in result.proposals],
        "state_changed": False,
    }


def _proposal_result_payload(result: Any) -> dict[str, Any]:
    return {
        "decision": result.decision.value,
        "proposal_id": result.proposal.proposal_id,
        "accepted": result.accepted,
        "persistent": result.persistent,
        "reason": result.reason,
        "truth_ref": result.truth.truth_ref if result.truth is not None else "",
        "event_id": result.event.event_id if result.event is not None else "",
    }


def _stable_seed(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps([str(value) for value in values], separators=(",", ":")).encode()
    ).hexdigest()


def _slug(value: Any) -> str:
    return "_".join(
        part
        for part in "".join(
            char if char.isalnum() else " "
            for char in str(value or "").casefold()
        ).split()
        if part
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
