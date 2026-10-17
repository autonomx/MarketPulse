from __future__ import annotations

from typing import Any, Mapping, cast

from app.apps.rpg.narration.runtime_narration_legacy import (
    build_runtime_narration_payload as build_legacy_runtime_narration_payload,
)

from .context_compiler import EvidenceCard
from .contracts import ResponseRequest
from .production_pipeline import (
    ProfileBoundProvider,
    RpgProductionResponsePipeline,
    _authoritative_result,
    _known_entities,
    _known_locations,
    _provider_policy,
    _recovery_history,
    _recovery_needed,
    _response_mode,
    _retrieval_sources,
    _speaker_id,
    _turn_id,
)
from .strict_proposal_policy import StrictProposalPolicy


class AuthoritativeProfileBoundProvider(ProfileBoundProvider):
    """Bind every supported provider parameter before the provider is invoked."""

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.provider, method_name)
        effective = dict(kwargs)
        effective["max_tokens"] = self.profile.max_tokens
        effective.setdefault("temperature", self.profile.temperature)
        effective.setdefault("model", self.profile.model)
        effective.setdefault("timeout", self.profile.timeout_seconds)
        effective.setdefault("timeout_seconds", self.profile.timeout_seconds)
        self.calls.append({"method": method_name, **effective})
        variants: tuple[dict[str, Any], ...] = (
            effective,
            {key: value for key, value in effective.items() if key != "timeout_seconds"},
            {key: value for key, value in effective.items() if key not in {"timeout", "timeout_seconds"}},
            {key: value for key, value in effective.items() if key not in {"model", "timeout", "timeout_seconds"}},
            {
                key: value
                for key, value in effective.items()
                if key not in {"model", "temperature", "timeout", "timeout_seconds"}
            },
            {"max_tokens": self.profile.max_tokens},
            {},
        )
        last_error: TypeError | None = None
        for candidate in variants:
            try:
                return method(*args, **candidate)
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return method(*args)


class StrictRpgProductionResponsePipeline(RpgProductionResponsePipeline):
    """Production-safe pipeline with fail-closed proposals and exact shadow output."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("proposal_policy", StrictProposalPolicy())
        super().__init__(**kwargs)

    def prepare_generation_inputs(
        self,
        *,
        player_input: str,
        simulation_state: Mapping[str, Any] | None = None,
        turn_contract: Mapping[str, Any] | None = None,
        legacy_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compile intent, retrieval, claims, forward motion, and profile before prose."""

        state = dict(simulation_state or {})
        contract = dict(turn_contract or {})
        base_result = _authoritative_result(contract, state)
        mode = _response_mode(base_result)
        recovery_needed = _recovery_needed(base_result, mode)
        profile, ignored = self.profile_registry.resolve_from_request(
            mode,
            _provider_policy(contract, state),
            recovery_needed=recovery_needed,
        )
        request = ResponseRequest(
            turn_id=_turn_id(contract, state, player_input),
            player_input=player_input,
            authoritative_turn_result=base_result,
            session_id=str(base_result.get("session_id") or state.get("session_id") or ""),
            world_id=str(base_result.get("world_id") or state.get("world_id") or ""),
            scene_id=str(base_result.get("scene_id") or state.get("scene_id") or ""),
            speaker_id=_speaker_id(dict(legacy_payload or {}), base_result),
            runtime_mode="pre_generation",
            provider_policy=_provider_policy(contract, state),
            feature_flags={"strict_claim_refs": True},
            legacy_payload=dict(legacy_payload or {}),
        )
        analysis = self.recovery.analyze(
            player_input,
            # Name tuples: the classifier reads mappings, so these are not used yet.
            known_entities=cast(Any, _known_entities(state)),
            known_locations=cast(Any, _known_locations(state)),
            supported_mechanics=tuple(
                str(value)
                for value in contract.get("supported_mechanics", ())
                if str(value)
            ),
            retrieval_sources=_retrieval_sources(dict(legacy_payload or {}), state),
            speaker_id=request.speaker_id,
            narrator_mode=not bool(request.speaker_id),
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
            request,
            visible_state=state,
            evidence=evidence_cards,
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
        evidence = [
            {
                "evidence_id": row.evidence_id,
                "source": row.source,
                "content": row.content,
                "confidence": row.confidence,
            }
            for row in analysis.retrieval.evidence
            if row.visibility != "hidden"
        ]
        enriched_contract = dict(contract)
        enriched_contract["narration_brief"] = {
            "schema_version": "rpg_pre_generation_brief_v1",
            "must_answer": context.must_answer,
            "response_mode": context.response_mode.value,
            "selected_intent": analysis.intent.selected.intent,
            "selected_affordance": analysis.intent.selected.affordance,
            "intent_confidence": analysis.intent.selected.confidence,
            "forward_strategy": forward_plan.strategy,
            "forward_outcome": forward_plan.outcome,
            "allowed_options": list(forward_plan.options),
            "allowed_claim_refs": list(context.claim_ledger.allowed_claim_refs),
            "prohibited_claim_refs": list(context.claim_ledger.prohibited_claim_refs),
            "visible_facts": dict(context.visible_facts),
            "evidence": evidence,
            "agency_constraints": list(context.agency_constraints),
            "word_budget": list(context.word_budget),
            "profile": profile.debug_payload(),
        }
        enriched_contract["allowed_facts"] = [
            row["content"] for row in evidence if str(row.get("content") or "").strip()
        ]
        enriched_contract["allowed_leads"] = list(forward_plan.options)
        enriched_contract["suggested_actions"] = list(forward_plan.options)
        enriched_contract["strict_claim_refs"] = True
        enriched_contract["grounding_required"] = True

        enriched_state = dict(state)
        runtime_settings = dict(enriched_state.get("runtime_settings") or {})
        runtime_settings["response_generation_profile"] = profile.debug_payload()
        runtime_settings["canonical_context_compiled_before_generation"] = True
        enriched_state["runtime_settings"] = runtime_settings
        return {
            "simulation_state": enriched_state,
            "turn_contract": enriched_contract,
            "profile": profile,
            "ignored_profile_overrides": ignored,
            "context": context,
            "analysis": analysis,
            "forward_plan": forward_plan,
        }

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
        prepared = self.prepare_generation_inputs(
            player_input=player_action,
            simulation_state=simulation_state,
            turn_contract=turn_contract,
        )
        profile = prepared["profile"]
        deferred = profile.execution_mode == "deferred"
        wrapped_provider = (
            AuthoritativeProfileBoundProvider(provider, profile)
            if provider is not None
            and prefer_provider
            and profile.use_provider
            and not deferred
            else None
        )
        legacy_payload = build_legacy_runtime_narration_payload(
            provider=wrapped_provider,
            player_action=player_action,
            simulation_state=prepared["simulation_state"],
            turn_contract=prepared["turn_contract"],
            prefer_provider=bool(wrapped_provider),
            max_tokens=profile.max_tokens,
            max_provider_attempts=profile.retry_count + 1,
        )
        if deferred:
            legacy_payload = dict(legacy_payload)
            legacy_payload["deferred"] = True
            legacy_payload["narration_status"] = "pending"
        return self.finalize_payload(
            legacy_payload,
            player_input=player_action,
            authoritative_turn_result=_authoritative_result(
                prepared["turn_contract"],
                prepared["simulation_state"],
            ),
            simulation_state=prepared["simulation_state"],
            turn_contract=prepared["turn_contract"],
            profile=profile,
            ignored_profile_overrides=prepared["ignored_profile_overrides"],
            provider_profile_applied=(
                dict(wrapped_provider.applied) if wrapped_provider is not None else {}
            ),
            runtime_mode="runtime_deferred" if deferred else "runtime",
        )

    def finalize_payload(
        self,
        legacy_payload: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = super().finalize_payload(legacy_payload, **kwargs)
        canonical = (
            result.get("canonical_response", {})
            if isinstance(result.get("canonical_response"), Mapping)
            else {}
        )
        rollout = (
            canonical.get("rollout", {})
            if isinstance(canonical.get("rollout"), Mapping)
            else {}
        )
        if isinstance(canonical, dict):
            metadata = (
                canonical.get("metadata", {})
                if isinstance(canonical.get("metadata"), Mapping)
                else {}
            )
            intent = (
                metadata.get("intent_analysis", {})
                if isinstance(metadata.get("intent_analysis"), Mapping)
                else {}
            )
            selected = (
                intent.get("selected", {})
                if isinstance(intent.get("selected"), Mapping)
                else {}
            )
            canonical["developer_trace"] = {
                "trace_version": "rpg_response_trace_v1",
                "turn_id": str(metadata.get("turn_id") or ""),
                "raw_player_input": str(kwargs.get("player_input") or ""),
                "interpreted_intents": [
                    dict(selected),
                    *[
                        dict(row)
                        for row in intent.get("alternatives", ())
                        if isinstance(row, Mapping)
                    ],
                ],
                "selected_affordance": str(selected.get("affordance") or ""),
                "resolver_result": {
                    "mechanic_resolved": bool(
                        kwargs.get("authoritative_turn_result", {}).get("mechanic_resolved")
                        if isinstance(kwargs.get("authoritative_turn_result"), Mapping)
                        else False
                    )
                },
                "retrieval_sources": list(
                    metadata.get("retrieval", {}).get("evidence", ())
                    if isinstance(metadata.get("retrieval"), Mapping)
                    else ()
                ),
                "visibility_decisions": [
                    {"evidence_id": evidence_id, "decision": "excluded_hidden"}
                    for evidence_id in (
                        metadata.get("retrieval", {}).get("hidden_evidence_ids", ())
                        if isinstance(metadata.get("retrieval"), Mapping)
                        else ()
                    )
                ],
                "hermes": dict(metadata.get("hermes") or {}),
                "recovery_plan": dict(metadata.get("recovery_plan") or {}),
                "claim_ledger": dict(metadata.get("claim_ledger") or {}),
                "hard_gates": [
                    dict(row)
                    for row in metadata.get("hard_gate_decisions", ())
                    if isinstance(row, Mapping)
                ],
                "candidate_ranking": [
                    {"candidate_id": candidate_id, "rank": index}
                    for index, candidate_id in enumerate(
                        metadata.get("ranked_candidate_ids", ()),
                        1,
                    )
                ],
                "quality": dict(canonical.get("quality_report") or {}),
                "response_mode": str(canonical.get("mode") or ""),
                "truth_records": list(metadata.get("truth_records") or ()),
                "profile": dict(metadata.get("response_profile") or {}),
                "rollout": dict(rollout),
                "final_visible_response": str(canonical.get("text") or ""),
            }
            result["canonical_response"] = canonical
        if not bool(rollout.get("publishes_canonical")):
            legacy_visible = str(legacy_payload.get("legacy_visible_text") or "").strip()
            if legacy_visible:
                result["narration"] = legacy_visible
        return result


__all__ = [
    "AuthoritativeProfileBoundProvider",
    "StrictRpgProductionResponsePipeline",
]
