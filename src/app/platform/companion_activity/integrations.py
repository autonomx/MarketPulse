"""Adapters from machine/user integrations into Companion evidence propositions.

Integrations are producers only. They do not receive or mutate CompanionActivityState; the
field-specific authority reducer remains the sole current-state authority.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.conversation.memory_policy import Sensitivity, TrustLevel

from .contracts import EvidenceProposition, EvidenceSourceKind, FrozenContract

IntegrationKind = Literal[
    "runtime_state",
    "process_integration",
    "game_telemetry",
    "browser_metadata",
    "user_explicit",
]


class IntegrationEvidenceInput(FrozenContract):
    integration_kind: IntegrationKind
    source_id: str = Field(min_length=1, max_length=160)
    event_id: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=240)
    predicate: str = Field(min_length=1, max_length=160)
    value: Any
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    observed_at: datetime
    generation: str | None = Field(default=None, max_length=160)
    sensitivity: Sensitivity | None = None


class CompanionIntegrationEvidenceAdapter:
    """Normalize trusted runtime facts and untrusted external metadata into evidence."""

    def proposition(self, input: IntegrationEvidenceInput) -> EvidenceProposition:
        source_kind, trust_level, default_sensitivity = _source_policy(input.integration_kind)
        return EvidenceProposition(
            proposition_id=_proposition_id(input),
            subject=input.subject,
            predicate=input.predicate,
            value=input.value,
            source_kind=source_kind,
            trust_level=trust_level,
            confidence=input.confidence,
            sensitivity=input.sensitivity or default_sensitivity,
            observed_at=input.observed_at,
            valid_from=input.observed_at,
            generation=input.generation,
            schema_version="companion-integration-evidence@1",
        )

    def runtime_fact(
        self,
        *,
        source_id: str,
        event_id: str,
        subject: str,
        predicate: str,
        value: Any,
        observed_at: datetime,
        generation: str | None = None,
    ) -> EvidenceProposition:
        return self.proposition(
            IntegrationEvidenceInput(
                integration_kind="runtime_state",
                source_id=source_id,
                event_id=event_id,
                subject=subject,
                predicate=predicate,
                value=value,
                observed_at=observed_at,
                generation=generation,
            )
        )

    def process_fact(
        self,
        *,
        source_id: str,
        event_id: str,
        subject: str,
        predicate: str,
        value: Any,
        observed_at: datetime,
        generation: str | None = None,
    ) -> EvidenceProposition:
        return self.proposition(
            IntegrationEvidenceInput(
                integration_kind="process_integration",
                source_id=source_id,
                event_id=event_id,
                subject=subject,
                predicate=predicate,
                value=value,
                observed_at=observed_at,
                generation=generation,
            )
        )

    def telemetry(
        self,
        *,
        source_id: str,
        event_id: str,
        subject: str,
        predicate: str,
        value: Any,
        observed_at: datetime,
        generation: str | None = None,
    ) -> EvidenceProposition:
        return self.proposition(
            IntegrationEvidenceInput(
                integration_kind="game_telemetry",
                source_id=source_id,
                event_id=event_id,
                subject=subject,
                predicate=predicate,
                value=value,
                observed_at=observed_at,
                generation=generation,
            )
        )

    def browser_metadata(
        self,
        *,
        source_id: str,
        event_id: str,
        subject: str,
        predicate: str,
        value: Any,
        observed_at: datetime,
        generation: str | None = None,
    ) -> EvidenceProposition:
        return self.proposition(
            IntegrationEvidenceInput(
                integration_kind="browser_metadata",
                source_id=source_id,
                event_id=event_id,
                subject=subject,
                predicate=predicate,
                value=value,
                observed_at=observed_at,
                generation=generation,
            )
        )

    def user_explicit(
        self,
        *,
        source_id: str,
        event_id: str,
        subject: str,
        predicate: str,
        value: Any,
        observed_at: datetime,
        generation: str | None = None,
        sensitivity: Sensitivity = "normal",
    ) -> EvidenceProposition:
        return self.proposition(
            IntegrationEvidenceInput(
                integration_kind="user_explicit",
                source_id=source_id,
                event_id=event_id,
                subject=subject,
                predicate=predicate,
                value=value,
                observed_at=observed_at,
                generation=generation,
                sensitivity=sensitivity,
            )
        )


def _proposition_id(input: IntegrationEvidenceInput) -> str:
    material = (
        f"{input.integration_kind}\x1f{input.source_id}\x1f{input.event_id}"
        f"\x1f{input.subject}\x1f{input.predicate}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    source_prefix = "".join(
        char if char.isalnum() or char in "-_." else "-" for char in input.source_id
    )[:48]
    return f"integration:{source_prefix}:{digest}"


def _source_policy(kind: IntegrationKind) -> tuple[EvidenceSourceKind, TrustLevel, Sensitivity]:
    if kind == "runtime_state":
        return "runtime", "system_trusted", "normal"
    if kind == "process_integration":
        return "system", "system_trusted", "normal"
    if kind == "game_telemetry":
        return "telemetry", "system_trusted", "normal"
    if kind == "browser_metadata":
        # Titles/URLs are machine-collected but controlled by external page content and can
        # reveal private activity, so collection does not turn them into trusted evidence.
        return "external", "external_untrusted", "sensitive"
    return "user", "user_explicit", "normal"


__all__ = [
    "CompanionIntegrationEvidenceAdapter",
    "IntegrationEvidenceInput",
    "IntegrationKind",
]
