from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from .contracts import RenderedResponse
from .profiles import DeliveryMode, ResponseGenerationProfile


class DeliveryState(str, Enum):
    PREPARED = "prepared"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ApprovedDeliveryUnit:
    unit_id: str
    index: int
    text: str
    kind: DeliveryMode
    checksum: str
    approved: bool = True


@dataclass(frozen=True)
class DeliveryCheckpoint:
    state: DeliveryState
    prepared_unit_ids: tuple[str, ...]
    delivered_unit_ids: tuple[str, ...]
    delivered_text: str
    next_index: int
    interruption_reason: str = ""
    validation_token: str = ""


@dataclass
class ValidatedDeliverySession:
    response: RenderedResponse
    profile: ResponseGenerationProfile
    validation_token: str
    units: tuple[ApprovedDeliveryUnit, ...]
    state: DeliveryState = DeliveryState.PREPARED
    delivered_unit_ids: list[str] = field(default_factory=list)
    interruption_reason: str = ""

    @classmethod
    def prepare(
        cls,
        response: RenderedResponse,
        profile: ResponseGenerationProfile,
    ) -> "ValidatedDeliverySession":
        issues = validate_publishable_response(response)
        if issues:
            raise ValueError("response is not publishable: " + ",".join(issues))
        token = _validation_token(response)
        texts = _unit_texts(response, profile.delivery_mode)
        units = tuple(
            ApprovedDeliveryUnit(
                unit_id=f"{token[:16]}:{index}",
                index=index,
                text=text,
                kind=profile.delivery_mode,
                checksum=_checksum(text),
            )
            for index, text in enumerate(texts)
        )
        return cls(
            response=response,
            profile=profile,
            validation_token=token,
            units=units,
        )

    def next_unit(self) -> ApprovedDeliveryUnit | None:
        if self.state in {
            DeliveryState.COMPLETED,
            DeliveryState.INTERRUPTED,
            DeliveryState.CANCELLED,
            DeliveryState.REJECTED,
        }:
            return None
        index = len(self.delivered_unit_ids)
        if index >= len(self.units):
            self.state = DeliveryState.COMPLETED
            return None
        self.state = DeliveryState.DELIVERING
        return self.units[index]

    def acknowledge(self, unit: ApprovedDeliveryUnit) -> None:
        if self.state not in {DeliveryState.PREPARED, DeliveryState.DELIVERING}:
            raise RuntimeError(f"cannot acknowledge while {self.state.value}")
        expected_index = len(self.delivered_unit_ids)
        if unit.index != expected_index or unit != self.units[expected_index]:
            raise ValueError("delivery unit is not the next approved unit")
        if not unit.approved or unit.checksum != _checksum(unit.text):
            raise ValueError("delivery unit approval integrity failed")
        self.delivered_unit_ids.append(unit.unit_id)
        if len(self.delivered_unit_ids) == len(self.units):
            self.state = DeliveryState.COMPLETED

    def interrupt(self, reason: str = "player_interruption") -> DeliveryCheckpoint:
        if self.state is DeliveryState.COMPLETED:
            return self.checkpoint()
        self.state = DeliveryState.INTERRUPTED
        self.interruption_reason = reason
        return self.checkpoint()

    def cancel(self, reason: str = "cancelled") -> DeliveryCheckpoint:
        if self.state is DeliveryState.COMPLETED:
            return self.checkpoint()
        self.state = DeliveryState.CANCELLED
        self.interruption_reason = reason
        return self.checkpoint()

    def checkpoint(self) -> DeliveryCheckpoint:
        delivered = self.units[: len(self.delivered_unit_ids)]
        return DeliveryCheckpoint(
            state=self.state,
            prepared_unit_ids=tuple(unit.unit_id for unit in self.units),
            delivered_unit_ids=tuple(self.delivered_unit_ids),
            delivered_text=" ".join(unit.text for unit in delivered).strip(),
            next_index=len(self.delivered_unit_ids),
            interruption_reason=self.interruption_reason,
            validation_token=self.validation_token,
        )

    def restore(self, checkpoint: DeliveryCheckpoint) -> None:
        if checkpoint.validation_token != self.validation_token:
            raise ValueError("checkpoint belongs to a different validated response")
        expected = tuple(unit.unit_id for unit in self.units)
        if checkpoint.prepared_unit_ids != expected:
            raise ValueError("checkpoint unit set differs from approved response")
        prefix = expected[: len(checkpoint.delivered_unit_ids)]
        if checkpoint.delivered_unit_ids != prefix:
            raise ValueError("checkpoint delivery history is not an approved prefix")
        self.delivered_unit_ids = list(checkpoint.delivered_unit_ids)
        self.state = checkpoint.state
        self.interruption_reason = checkpoint.interruption_reason


def validate_publishable_response(response: RenderedResponse) -> tuple[str, ...]:
    issues: list[str] = []
    if not response.text.strip():
        issues.append("empty_response")
    quality = dict(response.quality_report or {})
    if quality.get("ok") is not True:
        issues.append("quality_not_approved")
    decisions = response.metadata.get("hard_gate_decisions", ())
    if not isinstance(decisions, Iterable) or isinstance(decisions, (str, bytes, Mapping)):
        issues.append("hard_gate_decisions_missing")
    else:
        rows = tuple(row for row in decisions if isinstance(row, Mapping))
        if not rows:
            issues.append("hard_gate_decisions_missing")
        for row in rows:
            if row.get("passed") is not True:
                issues.append(f"hard_gate_failed:{row.get('gate') or 'unknown'}")
    if response.metadata.get("rewrite_attempted") and response.metadata.get("rewrite_accepted"):
        if quality.get("ok") is not True:
            issues.append("accepted_rewrite_not_revalidated")
    return tuple(dict.fromkeys(issues))


def approved_delivery_units(
    response: RenderedResponse,
    profile: ResponseGenerationProfile,
) -> tuple[ApprovedDeliveryUnit, ...]:
    return ValidatedDeliverySession.prepare(response, profile).units


def _unit_texts(
    response: RenderedResponse,
    delivery_mode: DeliveryMode,
) -> tuple[str, ...]:
    if delivery_mode is DeliveryMode.COMPLETE:
        return (response.text.strip(),)
    units = tuple(unit.strip() for unit in response.delivery_units if unit.strip())
    return units or (response.text.strip(),)


def _validation_token(response: RenderedResponse) -> str:
    payload = "\u241f".join(
        (
            response.mode.value,
            response.text,
            *response.approved_section_ids,
            *response.resolved_claim_refs,
            str(response.quality_report),
            str(response.metadata.get("hard_gate_decisions", ())),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
