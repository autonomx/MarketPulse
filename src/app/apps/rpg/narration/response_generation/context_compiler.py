from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .claim_ledger import ClaimLedger, derive_claim_ledger
from .contracts import RESPONSE_WORD_BUDGETS, ResponseMode, ResponseRequest, coerce_response_mode


@dataclass(frozen=True)
class EvidenceCard:
    evidence_id: str
    source: str
    content: Any
    visibility: str = "player_visible"
    confidence: float = 1.0
    entity_ids: tuple[str, ...] = ()
    timestamp: str = ""


@dataclass(frozen=True)
class ContextTrace:
    omitted_fields: tuple[str, ...] = ()
    truncated_fields: tuple[str, ...] = ()
    hidden_evidence_ids: tuple[str, ...] = ()
    included_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NarrationContext:
    schema_version: str
    turn_id: str
    player_input: str
    must_answer: str
    response_mode: ResponseMode
    resolved_result: Mapping[str, Any]
    visible_facts: Mapping[str, Any]
    scene_card: Mapping[str, Any]
    entity_cards: tuple[Mapping[str, Any], ...]
    speaker_card: Mapping[str, Any]
    evidence: tuple[EvidenceCard, ...]
    claim_ledger: ClaimLedger
    continuity: Mapping[str, Any]
    agency_constraints: tuple[str, ...]
    style_profile: Mapping[str, Any]
    word_budget: tuple[int, int]
    trace: ContextTrace = field(default_factory=ContextTrace)

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "current_turn": {
                "player_input": self.player_input,
                "must_answer": self.must_answer,
                "response_mode": self.response_mode.value,
            },
            "resolved_result": dict(self.resolved_result),
            "visible_facts": dict(self.visible_facts),
            "scene": dict(self.scene_card),
            "entities": [dict(card) for card in self.entity_cards],
            "speaker": dict(self.speaker_card),
            "evidence": [
                {
                    "evidence_id": row.evidence_id,
                    "source": row.source,
                    "content": row.content,
                    "confidence": row.confidence,
                    "entity_ids": list(row.entity_ids),
                    "timestamp": row.timestamp,
                }
                for row in self.evidence
            ],
            "allowed_claim_refs": list(self.claim_ledger.allowed_claim_refs),
            "forbidden_claim_refs": list(self.claim_ledger.prohibited_claim_refs),
            "continuity": dict(self.continuity),
            "agency_constraints": list(self.agency_constraints),
            "style_profile": dict(self.style_profile),
            "word_budget": list(self.word_budget),
        }

    def as_dict(self) -> dict[str, Any]:
        """Stable developer-trace representation of the compact prompt context."""

        payload = self.as_prompt_payload()
        payload["trace"] = {
            "omitted_fields": list(self.trace.omitted_fields),
            "truncated_fields": list(self.trace.truncated_fields),
            "hidden_evidence_ids": list(self.trace.hidden_evidence_ids),
            "included_evidence_ids": list(self.trace.included_evidence_ids),
        }
        return payload


class NarrationContextCompiler:
    def __init__(self, *, max_entities: int = 8, max_evidence: int = 12, max_mapping_keys: int = 24) -> None:
        self.max_entities = max_entities
        self.max_evidence = max_evidence
        self.max_mapping_keys = max_mapping_keys

    def compile(
        self,
        request: ResponseRequest,
        *,
        visible_state: Mapping[str, Any] | None = None,
        evidence: Iterable[EvidenceCard | Mapping[str, Any]] = (),
    ) -> NarrationContext:
        result = _mapping(request.authoritative_turn_result)
        state = _mapping(visible_state or result.get("visible_state") or result.get("simulation_state"))
        resolved = _mapping(
            result.get("resolved_result")
            or result.get("result")
            or result.get("resolved_action")
        )
        mode = coerce_response_mode(
            result.get("response_mode")
            or resolved.get("response_mode")
            or result.get("semantic_family")
            or resolved.get("semantic_family")
            or result.get("action_type")
            or resolved.get("action_type"),
            ResponseMode.ACTION,
        )
        trace_omitted = [
            "full_runtime_state",
            "full_session",
            "full_transcript",
            "full_memory_store",
            "hidden_world_log",
            "raw_debug_artifacts",
        ]
        truncated: list[str] = []
        hidden_ids: list[str] = []
        included_ids: list[str] = []

        entity_rows = _entity_cards(result, state)
        if len(entity_rows) > self.max_entities:
            truncated.append("entity_cards")
        entities = tuple(
            _compact_mapping(row, self.max_mapping_keys)
            for row in entity_rows[: self.max_entities]
        )

        normalized_evidence: list[EvidenceCard] = []
        for index, row in enumerate(evidence):
            card = row if isinstance(row, EvidenceCard) else _evidence_from_mapping(row, index)
            if card.visibility == "hidden":
                hidden_ids.append(card.evidence_id)
                continue
            if len(normalized_evidence) >= self.max_evidence:
                truncated.append("evidence")
                break
            normalized_evidence.append(card)
            included_ids.append(card.evidence_id)

        visible_facts = _compact_mapping(
            _mapping(result.get("visible_facts") or state.get("visible_facts")),
            self.max_mapping_keys,
        )
        ledger = derive_claim_ledger(request.turn_id, result, visible_state=state)
        must_answer = str(
            result.get("must_answer")
            or resolved.get("must_answer")
            or request.player_input
        ).strip()
        return NarrationContext(
            schema_version="rpg_narration_context_v1",
            turn_id=request.turn_id,
            player_input=request.player_input,
            must_answer=must_answer,
            response_mode=mode,
            resolved_result=_compact_mapping(resolved, self.max_mapping_keys),
            visible_facts=visible_facts,
            scene_card=_compact_mapping(_scene_card(result, state), self.max_mapping_keys),
            entity_cards=entities,
            speaker_card=_compact_mapping(_speaker_card(request, result, state), self.max_mapping_keys),
            evidence=tuple(normalized_evidence),
            claim_ledger=ledger,
            continuity=_compact_mapping(_mapping(result.get("continuity")), 12),
            agency_constraints=tuple(
                result.get("agency_constraints")
                or (
                    "do_not_accept_paths_for_player",
                    "clarify_low_confidence_intent",
                    "irreversible_changes_require_authority",
                )
            ),
            style_profile=_compact_mapping(_mapping(result.get("style_profile")), 12),
            word_budget=RESPONSE_WORD_BUDGETS[mode],
            trace=ContextTrace(
                omitted_fields=tuple(trace_omitted),
                truncated_fields=tuple(dict.fromkeys(truncated)),
                hidden_evidence_ids=tuple(hidden_ids),
                included_evidence_ids=tuple(included_ids),
            ),
        )


def _scene_card(result: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(result.get("scene_card") or state.get("scene") or {
        "scene_id": result.get("scene_id") or state.get("scene_id"),
        "location_id": result.get("current_location") or state.get("location_id"),
        "location_name": result.get("current_location_name") or state.get("location_name"),
        "time": state.get("time"),
        "weather": state.get("weather"),
    })


def _speaker_card(request: ResponseRequest, result: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    speaker = _mapping(result.get("speaker_card"))
    if speaker:
        return speaker
    speakers = _mapping(state.get("npc_cards"))
    return _mapping(speakers.get(request.speaker_id)) if request.speaker_id else {}


def _entity_cards(result: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("entity_cards") or state.get("entity_cards") or state.get("present_entities") or []
    if isinstance(rows, Mapping):
        return [dict(value, entity_id=key) if isinstance(value, Mapping) else {"entity_id": key, "value": value} for key, value in rows.items()]
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _evidence_from_mapping(row: Mapping[str, Any], index: int) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=str(row.get("evidence_id") or f"evidence-{index}"),
        source=str(row.get("source") or "unknown"),
        content=row.get("content"),
        visibility=str(row.get("visibility") or "player_visible"),
        confidence=float(row.get("confidence") or 0.0),
        entity_ids=tuple(str(item) for item in row.get("entity_ids", ()) if str(item)),
        timestamp=str(row.get("timestamp") or ""),
    )


def _compact_mapping(value: Mapping[str, Any], max_keys: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, key in enumerate(sorted(value, key=str)):
        if index >= max_keys:
            result["_truncated_keys"] = len(value) - max_keys
            break
        item = value[key]
        if isinstance(item, Mapping):
            result[str(key)] = _compact_mapping(item, min(max_keys, 12))
        elif isinstance(item, list):
            result[str(key)] = item[:8]
            if len(item) > 8:
                result[f"{key}_truncated_count"] = len(item) - 8
        elif isinstance(item, str) and len(item) > 600:
            result[str(key)] = item[:560] + "…"
        else:
            result[str(key)] = item
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
