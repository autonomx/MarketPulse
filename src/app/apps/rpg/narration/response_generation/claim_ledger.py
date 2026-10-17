from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ClaimRecord:
    claim_ref: str
    claim_type: str
    value: Any = None
    visibility: str = "player_visible"
    source: str = "resolved_turn"
    speaker_ids: tuple[str, ...] = ()
    persistent: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_ref": self.claim_ref,
            "claim_type": self.claim_type,
            "value": self.value,
            "visibility": self.visibility,
            "source": self.source,
            "speaker_ids": list(self.speaker_ids),
            "persistent": self.persistent,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ClaimLedger:
    schema_version: str
    turn_id: str
    records: tuple[ClaimRecord, ...]
    prohibited_claim_refs: tuple[str, ...] = ()
    grounding_required: bool = True

    @property
    def record_by_ref(self) -> dict[str, ClaimRecord]:
        return {record.claim_ref: record for record in self.records}

    @property
    def allowed_claim_refs(self) -> tuple[str, ...]:
        return tuple(record.claim_ref for record in self.records if record.visibility != "hidden")

    @property
    def hidden_claim_refs(self) -> tuple[str, ...]:
        return tuple(record.claim_ref for record in self.records if record.visibility == "hidden")

    @property
    def allowed_speakers(self) -> tuple[str, ...]:
        values: list[str] = []
        for record in self.records:
            for speaker_id in record.speaker_ids:
                if speaker_id and speaker_id not in values:
                    values.append(speaker_id)
        return tuple(values)

    @property
    def speaker_knowledge_refs(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = {}
        for record in self.records:
            for speaker_id in record.speaker_ids:
                if record.visibility == "hidden":
                    continue
                result.setdefault(speaker_id, []).append(record.claim_ref)
        return {key: tuple(dict.fromkeys(values)) for key, values in result.items()}

    def contains(self, claim_ref: str, *, allow_hidden: bool = False) -> bool:
        record = self.record_by_ref.get(claim_ref)
        if record is None:
            return False
        return allow_hidden or record.visibility != "hidden"

    def as_policy_payload(self) -> dict[str, Any]:
        return {
            "claim_ledger_version": self.schema_version,
            "claim_records": [record.as_dict() for record in self.records],
            "allowed_claim_refs": list(self.allowed_claim_refs),
            "prohibited_claim_refs": list(self.prohibited_claim_refs),
            "hidden_fact_refs": list(self.hidden_claim_refs),
            "allowed_speakers": list(self.allowed_speakers),
            "speaker_knowledge_refs": {
                key: list(values) for key, values in self.speaker_knowledge_refs.items()
            },
            "strict_claim_refs": self.grounding_required,
            "grounding_required": self.grounding_required,
        }


def derive_claim_ledger(
    turn_id: str,
    authoritative_turn_result: Mapping[str, Any] | None,
    *,
    visible_state: Mapping[str, Any] | None = None,
) -> ClaimLedger:
    result = _mapping(authoritative_turn_result)
    resolved = _mapping(
        result.get("resolved_result")
        or result.get("result")
        or result.get("resolved_action")
    )
    state = _mapping(visible_state)
    records: list[ClaimRecord] = []

    explicit_records = result.get("claim_records") or resolved.get("claim_records")
    if isinstance(explicit_records, Iterable) and not isinstance(explicit_records, (str, bytes, Mapping)):
        for row in explicit_records:
            if isinstance(row, Mapping) and str(row.get("claim_ref") or "").strip():
                records.append(_record_from_mapping(row))

    explicit_allowed = _strings(
        result.get("allowed_claim_refs")
        or resolved.get("allowed_claim_refs")
        or result.get("allowed_claims")
    )
    for claim_ref in explicit_allowed:
        _append_unique(records, ClaimRecord(claim_ref, "explicit_allowed"))

    hidden_refs = _strings(
        result.get("hidden_fact_refs")
        or result.get("hidden_claim_refs")
        or state.get("hidden_fact_refs")
    )
    for claim_ref in hidden_refs:
        _append_unique(
            records,
            ClaimRecord(claim_ref, "hidden_fact", visibility="hidden", source="hidden_state"),
        )

    speakers = _strings(
        result.get("allowed_speakers")
        or result.get("present_npcs")
        or state.get("present_npcs")
    )
    for speaker_id in speakers:
        _append_unique(
            records,
            ClaimRecord(
                f"speaker.{speaker_id}.present",
                "speaker_presence",
                value=True,
                speaker_ids=(speaker_id,),
            ),
        )

    current_location = (
        resolved.get("current_location")
        or resolved.get("current_location_id")
        or result.get("current_location")
        or state.get("current_location")
        or state.get("location_id")
    )
    if current_location:
        _append_unique(
            records,
            ClaimRecord("location.current", "location", value=current_location),
        )
    if bool(resolved.get("location_changed") or result.get("location_changed")):
        _append_unique(
            records,
            ClaimRecord("location.changed", "location_change", value=True),
        )

    _append_delta_records(records, "currency", resolved.get("currency_delta") or result.get("currency_delta"))
    _append_delta_records(records, "inventory", resolved.get("inventory_delta") or result.get("inventory_delta"))
    _append_delta_records(records, "combat", resolved.get("combat_delta") or result.get("combat_delta"))
    _append_delta_records(records, "quest", resolved.get("quest_log_delta") or result.get("quest_log_delta"))
    _append_delta_records(records, "relationship", resolved.get("relationship_delta") or result.get("relationship_delta"))

    for fact in _strings(resolved.get("new_facts") or result.get("new_facts")):
        _append_unique(records, ClaimRecord(f"fact.{_slug(fact)}", "discovered_fact", value=fact))
    for proposal in _strings(result.get("approved_proposal_refs") or resolved.get("approved_proposal_refs")):
        _append_unique(
            records,
            ClaimRecord(proposal, "approved_proposal", value=True, source="proposal_policy"),
        )

    grounding_setting = result.get("grounding_required")
    grounding_required = True if grounding_setting is None else bool(grounding_setting)
    if result.get("production_rpg_response") and grounding_required is False:
        grounding_required = True

    return ClaimLedger(
        schema_version="rpg_claim_ledger_v1",
        turn_id=turn_id,
        records=tuple(records),
        prohibited_claim_refs=_strings(
            result.get("prohibited_claim_refs")
            or result.get("forbidden_claim_refs")
        ),
        grounding_required=grounding_required,
    )


def _record_from_mapping(row: Mapping[str, Any]) -> ClaimRecord:
    return ClaimRecord(
        claim_ref=str(row.get("claim_ref") or "").strip(),
        claim_type=str(row.get("claim_type") or "fact").strip(),
        value=row.get("value"),
        visibility=str(row.get("visibility") or "player_visible").strip(),
        source=str(row.get("source") or "resolved_turn").strip(),
        speaker_ids=_strings(row.get("speaker_ids")),
        persistent=bool(row.get("persistent", False)),
        metadata=_mapping(row.get("metadata")),
    )


def _append_delta_records(records: list[ClaimRecord], prefix: str, value: Any) -> None:
    if isinstance(value, Mapping):
        for key, delta in value.items():
            _append_unique(
                records,
                ClaimRecord(f"{prefix}.{_slug(key)}", f"{prefix}_delta", value=delta),
            )
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for index, row in enumerate(value):
            _append_unique(
                records,
                ClaimRecord(f"{prefix}.item_{index}", f"{prefix}_delta", value=row),
            )
    elif value not in (None, "", 0, False):
        _append_unique(records, ClaimRecord(f"{prefix}.changed", f"{prefix}_delta", value=value))


def _append_unique(records: list[ClaimRecord], record: ClaimRecord) -> None:
    if record.claim_ref and all(existing.claim_ref != record.claim_ref for existing in records):
        records.append(record)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(str(key) for key, enabled in value.items() if enabled)
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError:
        return ()


def _slug(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return "_".join(part for part in "".join(char if char.isalnum() else " " for char in text).split())
