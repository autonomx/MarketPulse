"""Same-model, information-preserving recovery for World Forge responses."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ValidationError

from app.providers.base import ChatMessage
from app.providers.structured import StructuredContract, StructuredOutputGateway
from app.providers.structured.errors import StructuredOutputError
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.generation_first_pass_provider import (
    FirstPassWorldForgeTopicGenerator,
    _identity_contract,
    _authored_contract,
    _authored_system_prompt,
)
from app.apps.rpg.genesis.worlds.generation_contract_bundle import build_topic_contract_bundle
from app.apps.rpg.genesis.worlds.generation_failure_artifact import build_failure_artifact
from app.apps.rpg.genesis.worlds.generation_strategy import world_forge_strategy_identity
from app.apps.rpg.genesis.worlds.generation_recovery_registry import StructuredRegistryRecoveryMixin
from app.apps.rpg.genesis.worlds.generation_recovery_review import StructuredRecoveryReviewMixin
from app.apps.rpg.genesis.worlds.generation_structured_recovery import (
    CapturingStructuredProvider,
    apply_missing_field_patches,
    decode_candidate,
    deterministic_repair,
    merge_diagnostics,
    minimum_viability_candidate,
    missing_field_paths,
    missing_field_patch_contract,
    missing_field_patch_messages,
    recovery_messages,
    semantic_correction_messages,
    validate_payload,
)
from app.apps.rpg.genesis.worlds.providers.world_forge import (
    WorldForgeTopicResponse,
    _payload,
    _token_estimate,
)
from app.apps.rpg.genesis.worlds.providers.single_pass import (
    SinglePassWorldForgeProviderError,
    _definitions,
    _field_contract,
    _one_call_budget,
)


@dataclass(frozen=True)
class _RecoveredValue:
    value: BaseModel
    diagnostics: Mapping[str, Any]
    prompt_tokens: int
    completion_tokens: int


class RecoveringFirstPassWorldForgeTopicGenerator(
    StructuredRecoveryReviewMixin,
    StructuredRegistryRecoveryMixin,
    FirstPassWorldForgeTopicGenerator,
):
    """Use the configured model once more only to restructure its own candidate."""

    _recovered_value_type = _RecoveredValue

    def _provider_call(
        self,
        messages: list[ChatMessage],
        *,
        contract: StructuredContract[Any],
        max_tokens: int,
        temperature: float,
    ):
        capture = CapturingStructuredProvider(self.provider)
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(capture)
        with self._limiter():
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    contract,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                model=self.config.model or None,
                retry_budget=_one_call_budget(self.config.timeout_seconds),
            )
        return outcome, capture.raw_text

    @staticmethod
    def _validate_recovered_candidate(
        *,
        contract: StructuredContract[Any],
        payload: Mapping[str, Any] | None,
        field_definitions: Sequence[Mapping[str, Any]],
        expected_topic_id: str,
        allocated_entity_ids: tuple[str, ...],
        expected_entity_kind: str,
    ) -> tuple[BaseModel | None, Exception | None]:
        del (
            field_definitions,
            expected_topic_id,
            allocated_entity_ids,
            expected_entity_kind,
        )
        return validate_payload(contract, payload)

    def _recover(
        self,
        *,
        contract: StructuredContract[Any],
        outcome: Any,
        raw_text: str,
        original_messages: list[ChatMessage],
        expected_topic_id: str,
        allocated_entity_ids: tuple[str, ...],
        allowed_reference_ids: frozenset[str],
        expected_entity_kind: str,
        field_definitions: Sequence[Mapping[str, Any]],
        max_tokens: int,
        retain_invalid_kind: str,
    ) -> _RecoveredValue:
        original_error = outcome.error
        assert isinstance(original_error, Exception)
        if not isinstance(original_error, StructuredOutputError) or not raw_text:
            raise SinglePassWorldForgeProviderError(
                expected_topic_id,
                original_error,
                outcome.diagnostics.as_dict(),
                unit=retain_invalid_kind,
            ) from original_error

        decoded = decode_candidate(raw_text)
        include_provenance = "provenance" in contract.output_model.model_fields
        repaired = deterministic_repair(
            decoded,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
            include_provenance=include_provenance,
            allowed_root_fields=frozenset(contract.output_model.model_fields),
            allowed_reference_ids=allowed_reference_ids,
        )
        value, repair_error = self._validate_recovered_candidate(
            contract=contract,
            payload=repaired.payload,
            field_definitions=field_definitions,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
        )
        original_prompt_tokens = sum(
            _token_estimate(message.content) for message in original_messages
        )
        original_completion_tokens = _token_estimate(raw_text)
        if value is not None:
            diagnostics = merge_diagnostics(
                outcome.diagnostics.as_dict(),
                None,
                method="deterministic_normalisation",
                repair_codes=repaired.codes,
                raw_text=raw_text,
                error=original_error,
            )
            return _RecoveredValue(
                value,
                diagnostics,
                original_prompt_tokens,
                original_completion_tokens,
            )

        correction_error = repair_error or original_error
        missing_paths = missing_field_paths(correction_error)
        if missing_paths:
            patch_contract = missing_field_patch_contract(missing_paths)
            patch_messages = missing_field_patch_messages(
                raw_text=raw_text,
                payload=repaired.payload,
                paths=missing_paths,
            )
            patched, patched_raw = self._provider_call(
                patch_messages,
                contract=patch_contract,
                max_tokens=min(max_tokens, 1024),
                temperature=0.0,
            )
            prompt_tokens = original_prompt_tokens + sum(
                _token_estimate(message.content) for message in patch_messages
            )
            completion_tokens = original_completion_tokens + (
                _token_estimate(patched_raw) if patched_raw else 0
            )
            if patched.error is None and patched.value is not None:
                patched_payload = apply_missing_field_patches(
                    repaired.payload,
                    patched.value.patches,
                )
                patched_value, patched_error = self._validate_recovered_candidate(
                    contract=contract,
                    payload=patched_payload,
                    field_definitions=field_definitions,
                    expected_topic_id=expected_topic_id,
                    allocated_entity_ids=allocated_entity_ids,
                    expected_entity_kind=expected_entity_kind,
                )
                if patched_value is not None:
                    diagnostics = merge_diagnostics(
                        outcome.diagnostics.as_dict(),
                        patched.diagnostics.as_dict(),
                        method="targeted_missing_field_patch",
                        repair_codes=(*repaired.codes, *missing_paths),
                        raw_text=raw_text,
                        error=original_error,
                    )
                    return _RecoveredValue(
                        patched_value,
                        diagnostics,
                        prompt_tokens,
                        completion_tokens,
                    )
                correction_error = patched_error or correction_error
            else:
                correction_error = patched.error or correction_error
        correction_messages = recovery_messages(
            contract=contract,
            raw_text=raw_text,
            decoded_payload=repaired.payload or decoded,
            error=correction_error,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
        )
        corrected, corrected_raw = self._provider_call(
            correction_messages,
            contract=contract,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        prompt_tokens = original_prompt_tokens + sum(
            _token_estimate(message.content) for message in correction_messages
        )
        completion_tokens = original_completion_tokens + (
            _token_estimate(corrected_raw) if corrected_raw else 0
        )
        if corrected.error is None and corrected.value is not None:
            diagnostics = merge_diagnostics(
                outcome.diagnostics.as_dict(),
                corrected.diagnostics.as_dict(),
                method="same_model_extraction",
                repair_codes=repaired.codes,
                raw_text=raw_text,
                error=original_error,
            )
            return _RecoveredValue(
                corrected.value,
                diagnostics,
                prompt_tokens,
                completion_tokens,
            )

        corrected_decoded = decode_candidate(corrected_raw)
        corrected_repair = deterministic_repair(
            corrected_decoded,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
            include_provenance=include_provenance,
            allowed_root_fields=frozenset(contract.output_model.model_fields),
            allowed_reference_ids=allowed_reference_ids,
        )
        corrected_value, corrected_validation_error = self._validate_recovered_candidate(
            contract=contract,
            payload=corrected_repair.payload,
            field_definitions=field_definitions,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
        )
        if corrected_value is not None:
            diagnostics = merge_diagnostics(
                outcome.diagnostics.as_dict(),
                corrected.diagnostics.as_dict(),
                method="same_model_extraction",
                repair_codes=(*repaired.codes, *corrected_repair.codes),
                raw_text=raw_text,
                error=original_error,
            )
            return _RecoveredValue(
                corrected_value,
                diagnostics,
                prompt_tokens,
                completion_tokens,
            )

        # The structural recovery itself can expose a semantic violation, such as
        # copying an existing long field into a required dossier section. Give the
        # model one narrowly-scoped chance to correct that known violation.
        if (
            corrected_repair.payload is not None
            and corrected_validation_error is not None
            and not isinstance(corrected_validation_error, ValidationError)
        ):
            semantic_correction_source_error = corrected_validation_error
            recovery_diagnostics = merge_diagnostics(
                outcome.diagnostics.as_dict(),
                corrected.diagnostics.as_dict(),
                method="same_model_extraction",
                repair_codes=(*repaired.codes, *corrected_repair.codes),
                raw_text=raw_text,
                error=original_error,
            )
            semantic_messages = semantic_correction_messages(
                contract=contract,
                invalid_candidate=corrected_repair.payload,
                error=corrected_validation_error,
            )
            semantically_corrected, semantic_raw = self._provider_call(
                semantic_messages,
                contract=contract,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            prompt_tokens += sum(
                _token_estimate(message.content) for message in semantic_messages
            )
            completion_tokens += _token_estimate(semantic_raw) if semantic_raw else 0
            if (
                semantically_corrected.error is None
                and semantically_corrected.value is not None
            ):
                diagnostics = merge_diagnostics(
                    recovery_diagnostics,
                    semantically_corrected.diagnostics.as_dict(),
                    method="same_model_semantic_correction",
                    repair_codes=(*repaired.codes, *corrected_repair.codes),
                    raw_text=raw_text,
                    error=original_error,
                )
                return _RecoveredValue(
                    semantically_corrected.value,
                    diagnostics,
                    prompt_tokens,
                    completion_tokens,
                )

            semantic_decoded = decode_candidate(semantic_raw)
            semantic_repair = deterministic_repair(
                semantic_decoded,
                expected_topic_id=expected_topic_id,
                allocated_entity_ids=allocated_entity_ids,
                expected_entity_kind=expected_entity_kind,
                include_provenance=include_provenance,
                allowed_root_fields=frozenset(contract.output_model.model_fields),
                allowed_reference_ids=allowed_reference_ids,
            )
            semantic_value, semantic_validation_error = self._validate_recovered_candidate(
                contract=contract,
                payload=semantic_repair.payload,
                field_definitions=field_definitions,
                expected_topic_id=expected_topic_id,
                allocated_entity_ids=allocated_entity_ids,
                expected_entity_kind=expected_entity_kind,
            )
            if semantic_value is not None:
                diagnostics = merge_diagnostics(
                    recovery_diagnostics,
                    semantically_corrected.diagnostics.as_dict(),
                    method="same_model_semantic_correction",
                    repair_codes=(
                        *repaired.codes,
                        *corrected_repair.codes,
                        *semantic_repair.codes,
                    ),
                    raw_text=raw_text,
                    error=original_error,
                )
                return _RecoveredValue(
                    semantic_value,
                    diagnostics,
                    prompt_tokens,
                    completion_tokens,
                )
            corrected_validation_error = (
                semantic_validation_error
                or semantically_corrected.error
                or corrected_validation_error
            )

            viability_candidates = (
                (
                    semantic_repair.payload,
                    semantic_validation_error,
                    (*repaired.codes, *corrected_repair.codes, *semantic_repair.codes),
                ),
                (
                    corrected_repair.payload,
                    semantic_correction_source_error,
                    (*repaired.codes, *corrected_repair.codes),
                ),
            )
            for viability_payload, viability_error, viability_codes in viability_candidates:
                viability_value, viability = minimum_viability_candidate(
                    contract,
                    viability_payload,
                    viability_error,
                )
                if viability_value is None or viability is None:
                    continue
                diagnostics = merge_diagnostics(
                    recovery_diagnostics,
                    semantically_corrected.diagnostics.as_dict(),
                    method="minimum_viability_quarantine",
                    repair_codes=viability_codes,
                    raw_text=raw_text,
                    error=original_error,
                )
                recovery = dict(diagnostics.get("structured_recovery") or {})
                recovery["minimum_viability"] = viability
                diagnostics["structured_recovery"] = recovery
                return _RecoveredValue(
                    viability_value,
                    diagnostics,
                    prompt_tokens,
                    completion_tokens,
                )

        final_error = (
            corrected_validation_error
            or corrected.error
            or correction_error
        )
        diagnostics = merge_diagnostics(
            outcome.diagnostics.as_dict(),
            corrected.diagnostics.as_dict(),
            method="same_model_extraction_failed",
            repair_codes=repaired.codes,
            raw_text=raw_text,
            error=final_error,
        )
        artifact = build_failure_artifact(
            topic_id=expected_topic_id,
            stage="recovery_exhausted",
            error=final_error,
            raw_text=corrected_raw or raw_text,
            diagnostics=diagnostics,
            deterministic_repairs=repaired.codes,
            correction_attempted=True,
            correction_result="failed",
        )
        diagnostics["failure_artifact"] = artifact.model_dump(mode="json")
        raise SinglePassWorldForgeProviderError(
            expected_topic_id,
            final_error,
            diagnostics,
            unit=retain_invalid_kind,
        ) from final_error

    def _generate_response(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
        expected_entity_count: int | None = None,
        expected_entity_ids: tuple[str, ...] = (),
        expected_entity_names: tuple[str, ...] = (),
        batch_index: int | None = None,
        batch_count: int | None = None,
        existing_entities: tuple[Mapping[str, str], ...] = (),
        assigned_entity_ids: tuple[str, ...] = (),
        assigned_entities: tuple[Mapping[str, str], ...] = (),
    ) -> tuple[WorldForgeTopicResponse, Mapping[str, Any], int, int]:
        count = expected_entity_count or node.target_count
        ids = expected_entity_ids or self._assigned_entity_ids(
            node,
            batch_index=batch_index or 0,
            requested_count=count,
        )
        index = batch_index if batch_index is not None else 0
        total = batch_count if batch_count is not None else 1
        bundle = build_topic_contract_bundle(
            node,
            allocated_entity_ids=ids,
            dependencies=dependency_topics,
            expected_entity_count=count,
        )
        prompt = _authored_system_prompt(
            node,
            bundle,
            batch_index=index,
            batch_count=total,
            existing_entities=existing_entities,
            assigned_entity_ids=ids,
            assigned_entities=assigned_entities,
        )
        request = _payload(
            node,
            seed=seed,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
            batch_index=index,
            batch_count=total,
            existing_entities=existing_entities,
            assigned_entity_ids=ids,
            assigned_entities=assigned_entities,
        )
        request["required_output"] = {
            **dict(bundle.prompt_contract),
            "identity_contract": _identity_contract(node.topic_id, ids),
            "profile_field_contract": _field_contract(
                node,
                allocated_ids=ids,
                dependencies=dependency_topics,
            ),
        }
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(
                role="user",
                content=json.dumps(request, ensure_ascii=False, sort_keys=True),
            ),
        ]
        contract = _authored_contract(bundle)
        outcome, raw_text = self._provider_call(
            messages,
            contract=contract,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        if outcome.error is None and outcome.value is not None:
            recovered = _RecoveredValue(
                outcome.value,
                outcome.diagnostics.as_dict(),
                sum(_token_estimate(message.content) for message in messages),
                _token_estimate(raw_text),
            )
        else:
            try:
                recovered = self._recover(
                    contract=contract,
                    outcome=outcome,
                    raw_text=raw_text,
                    original_messages=messages,
                    expected_topic_id=node.topic_id,
                    allocated_entity_ids=ids,
                    allowed_reference_ids=bundle.allowed_reference_ids,
                    expected_entity_kind=str(
                        node.metadata.get("entity_kind") or node.topic_id
                    ),
                    field_definitions=_definitions(node),
                    max_tokens=self.config.max_tokens,
                    retain_invalid_kind="topic",
                )
            except SinglePassWorldForgeProviderError as exc:
                diagnostics = {**bundle.descriptor(), **dict(exc.diagnostics)}
                strategy_identity = world_forge_strategy_identity(
                    provider=str(diagnostics.get("provider") or self.config.provider),
                    model=str(diagnostics.get("model") or self.config.model),
                    selected_mode=str(diagnostics.get("selected_mode") or ""),
                    prompt_version=self.config.prompt_version,
                    contract_descriptor=bundle.descriptor(),
                )
                diagnostics["strategy_identity"] = strategy_identity
                artifact = dict(diagnostics.get("failure_artifact") or {})
                artifact.update(
                    {
                        "strategy_identity": strategy_identity,
                        "canonical_contract_hash": bundle.canonical_contract_hash,
                    }
                )
                diagnostics["failure_artifact"] = artifact
                raise SinglePassWorldForgeProviderError(
                    node.topic_id,
                    exc.error,
                    diagnostics,
                    unit=exc.unit,
                ) from exc
        recovered_diagnostics = dict(recovered.diagnostics)
        try:
            value = bundle.materializer(recovered.value)
        except Exception as exc:
            diagnostics = {**bundle.descriptor(), **recovered_diagnostics}
            failure_artifact = build_failure_artifact(
                topic_id=node.topic_id,
                stage="materialization",
                error=exc,
                raw_text=raw_text,
                diagnostics=diagnostics,
            )
            diagnostics["failure_artifact"] = failure_artifact.model_dump(mode="json")
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                exc,
                diagnostics,
                unit="topic",
            ) from exc
        strategy_identity = world_forge_strategy_identity(
            provider=str(recovered_diagnostics.get("provider") or self.config.provider),
            model=str(recovered_diagnostics.get("model") or self.config.model),
            selected_mode=str(recovered_diagnostics.get("selected_mode") or ""),
            prompt_version=self.config.prompt_version,
            contract_descriptor=bundle.descriptor(),
        )
        provenance = dict(value.provenance)
        receipt = dict(provenance.get("authoritative_contract_receipt") or {})
        receipt["provider_wire_schema_hash"] = str(
            recovered_diagnostics.get("provider_schema_hash") or ""
        )
        receipt["strategy_identity"] = strategy_identity
        provenance.update(
            {
                "authoritative_contract_receipt": receipt,
                "strategy_identity": strategy_identity,
            }
        )
        value = value.model_copy(update={"provenance": provenance})
        value = self._apply_registry_slots(value, assigned_entities)
        return (
            value,
            {
                **bundle.descriptor(),
                **recovered_diagnostics,
                "strategy_identity": strategy_identity,
            },
            recovered.prompt_tokens,
            recovered.completion_tokens,
        )


__all__ = ["RecoveringFirstPassWorldForgeTopicGenerator"]
