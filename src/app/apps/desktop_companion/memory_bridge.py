"""Conservative Desktop Companion -> Memory v2 evidence bridge.

Only high-confidence, high-importance observations from an explicitly memory-enabled
persistent Chat are retained. Raw frames and raw visible screen text are never stored.
Every persisted semantic proposition remains external/untrusted and sensitive so VLM
transformations cannot launder screen evidence into trusted or less-sensitive memory.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.platform.assistant_memory.contracts import (
    MemorySpaceKey,
    ObservationAppendRequest,
    ObservationProvenance,
    PostgresMemoryV2Runtime,
    VisibilityScope,
    resolve_session_memory_scope,
)
from app.platform.chat.contracts import default_chat_store
from app.runtime.tenant_context import current_tenant

from .models import DesktopObservation
from .observation import screen_prompt_injection_observed

MemoryBridgeStatus = Literal["recorded", "skipped", "error"]

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_TOKENISH = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


@dataclass(frozen=True, slots=True)
class DesktopMemoryBridgeOutcome:
    status: MemoryBridgeStatus
    reason: str
    memory_observation_id: str | None = None


class DesktopCompanionMemoryBridge:
    """Append bounded desktop evidence without delaying the interactive response path."""

    def __init__(
        self,
        *,
        chat_store_factory: Callable[[], Any] = default_chat_store,
        memory_runtime_factory: Callable[[], Any] = PostgresMemoryV2Runtime,
        minimum_importance: float = 0.72,
        minimum_confidence: float = 0.70,
    ) -> None:
        self._chat_store_factory = chat_store_factory
        self._memory_runtime_factory = memory_runtime_factory
        self._minimum_importance = minimum_importance
        self._minimum_confidence = minimum_confidence

    def record(self, observation: DesktopObservation) -> DesktopMemoryBridgeOutcome:
        session = self._chat_store_factory().get_session(observation.session_id)
        if session is None:
            return DesktopMemoryBridgeOutcome("skipped", "session_not_found")

        reason = self._eligibility_reason(observation, session)
        if reason is not None:
            return DesktopMemoryBridgeOutcome("skipped", reason)

        runtime = self._memory_runtime_factory()
        context = resolve_session_memory_scope(session)
        # Memory v2 spaces belong to the tenant workspace (as Chat reads them).
        space = MemorySpaceKey(
            principal_id=current_tenant().workspace_id,
            owner_type=context.owner_type,
            owner_id=context.owner_id,
        )
        visibility = (
            VisibilityScope(kind="project", scope_id=context.project_id)
            if context.project_id
            else VisibilityScope(kind="session", scope_id=context.session_id)
        )
        provider = observation.diagnostics.get("model")
        provenance = ObservationProvenance(
            source_type="external",
            source_id=f"desktop-companion:{observation.source_fingerprint}"[:240],
            trust_level="external_untrusted",
            session_id=observation.session_id,
            model_id=str(provider)[:200] if provider else None,
        )
        request = ObservationAppendRequest(
            space=space,
            visibility_scope=visibility,
            event_type="external_observed",
            occurred_at=observation.observed_at,
            provenance=provenance,
            idempotency_key=f"desktop-companion:{observation.observation_id}"[:240],
            payload=_memory_payload(
                observation,
                minimum_confidence=self._minimum_confidence,
            ),
            sensitivity="sensitive",
            correlation_id=observation.observation_id,
            schema_version="desktop-companion-memory@2",
        )
        stored = runtime.append_authoritative_next(request)
        return DesktopMemoryBridgeOutcome(
            "recorded",
            "salient_desktop_observation_recorded",
            memory_observation_id=stored.observation_id,
        )

    def _eligibility_reason(self, observation: DesktopObservation, session: Any) -> str | None:
        if getattr(session, "transcript_policy", "persistent") != "persistent":
            return "private_or_temporary_session"
        if getattr(session, "interaction_mode", "system") == "character":
            if not bool(getattr(session, "write_memory", False)):
                return "character_memory_write_disabled"
            authoritative_character = (
                str(getattr(session, "character_id", "") or "").strip() or None
            )
            if authoritative_character and observation.character_id not in {
                None,
                authoritative_character,
            }:
                return "character_identity_mismatch"
        elif not bool(getattr(session, "memory_enabled", False)):
            return "system_memory_disabled"
        if observation.change_kind == "none":
            return "no_visual_change"
        if observation.importance < self._minimum_importance:
            return "importance_below_memory_threshold"
        if not _has_qualifying_proposition(observation, self._minimum_confidence):
            return "confidence_below_memory_threshold"
        if screen_prompt_injection_observed(_all_screen_derived_text(observation)):
            return "screen_prompt_injection_detected"
        return None


def _all_screen_derived_text(observation: DesktopObservation) -> list[str]:
    values = [*observation.visible_text, observation.current_scene.value]
    values.extend(item.event for item in observation.visible_changes)
    values.extend(item.event for item in observation.possible_events)
    if observation.plain_text_fallback:
        values.append(observation.plain_text_fallback)
    if observation.activity.hypothesis:
        values.append(observation.activity.hypothesis)
    return [value for value in values if value]


def _has_qualifying_proposition(
    observation: DesktopObservation,
    minimum_confidence: float,
) -> bool:
    if observation.current_scene.value and observation.current_scene.confidence >= minimum_confidence:
        return True
    if any(item.confidence >= minimum_confidence for item in observation.visible_changes):
        return True
    return any(item.confidence >= minimum_confidence for item in observation.possible_events)


def _safe_text(value: str, maximum: int = 420) -> str:
    compact = " ".join(str(value or "").split())
    compact = _URL.sub("[url]", compact)
    compact = _EMAIL.sub("[email]", compact)
    compact = _TOKENISH.sub("[token]", compact)
    compact = _LONG_NUMBER.sub("[number]", compact)
    return compact[:maximum]


def _proposition(
    *,
    proposition_id: str,
    predicate: str,
    value: str,
    confidence: float,
    observation: DesktopObservation,
) -> dict[str, Any]:
    return {
        "proposition_id": proposition_id,
        "subject": f"desktop-session:{observation.session_id}",
        "predicate": predicate,
        "value": _safe_text(value),
        "source_kind": "external",
        "trust_level": "external_untrusted",
        "confidence": round(confidence, 4),
        "sensitivity": "sensitive",
        "evidence_refs": [observation.observation_id],
        "derivation_refs": [],
        "observed_at": observation.observed_at.isoformat(),
        "generation": observation.capture_generation,
        "schema_version": "desktop-evidence-proposition@1",
    }


def _memory_payload(
    observation: DesktopObservation,
    *,
    minimum_confidence: float,
) -> dict[str, Any]:
    propositions: list[dict[str, Any]] = []
    scene = ""
    if (
        observation.current_scene.value
        and observation.current_scene.confidence >= minimum_confidence
    ):
        scene = _safe_text(observation.current_scene.value)
        propositions.append(
            _proposition(
                proposition_id=f"{observation.observation_id}:scene",
                predicate="current_scene",
                value=observation.current_scene.value,
                confidence=observation.current_scene.confidence,
                observation=observation,
            )
        )

    visible_changes = []
    for index, item in enumerate(observation.visible_changes[:4]):
        if item.confidence < minimum_confidence:
            continue
        event = _safe_text(item.event)
        visible_changes.append(
            {
                "event": event,
                "confidence": round(item.confidence, 4),
                "trust_level": "external_untrusted",
                "sensitivity": "sensitive",
            }
        )
        propositions.append(
            _proposition(
                proposition_id=f"{observation.observation_id}:change:{index}",
                predicate="visible_change",
                value=item.event,
                confidence=item.confidence,
                observation=observation,
            )
        )

    possible_events = []
    for index, item in enumerate(observation.possible_events[:3]):
        if item.confidence < minimum_confidence:
            continue
        event = _safe_text(item.event)
        possible_events.append(
            {
                "event": event,
                "confidence": round(item.confidence, 4),
                "trust_level": "external_untrusted",
                "sensitivity": "sensitive",
            }
        )
        propositions.append(
            _proposition(
                proposition_id=f"{observation.observation_id}:possible:{index}",
                predicate="possible_event",
                value=item.event,
                confidence=item.confidence,
                observation=observation,
            )
        )

    semantic_text = _safe_text(
        "; ".join(
            value
            for value in [
                scene,
                *(item["event"] for item in visible_changes),
                *(item["event"] for item in possible_events),
            ]
            if value
        ),
        maximum=1200,
    )
    persisted_confidence = min(
        (float(item["confidence"]) for item in propositions),
        default=0.0,
    )
    return {
        "kind": "desktop_activity_episode",
        "text": semantic_text,
        "scene": scene,
        "visible_changes": visible_changes,
        "possible_events": possible_events,
        "propositions": propositions,
        "evidence_policy": {
            "source_kind": "external",
            "trust_level": "external_untrusted",
            "sensitivity": "sensitive",
            "trust_monotonic": True,
            "sensitivity_monotonic": True,
        },
        "importance": round(observation.importance, 4),
        "confidence": round(persisted_confidence, 4),
        "activity": observation.activity.activity,
        "activity_hypothesis": observation.activity.hypothesis,
        "activity_confidence": round(observation.activity.confidence, 4),
        "behavior_pattern": observation.behavior.current_pattern,
        "change_kind": observation.change_kind,
        "desktop_observation_id": observation.observation_id,
        "capture_generation": observation.capture_generation,
        "character_id": observation.character_id,
        "memory_hints": {
            "preferred_domains": ["episode", "open_loop"],
            "treat_as_external_observation": True,
        },
    }


_default_bridge: DesktopCompanionMemoryBridge | None = None


def default_desktop_companion_memory_bridge() -> DesktopCompanionMemoryBridge:
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = DesktopCompanionMemoryBridge()
    return _default_bridge


__all__ = [
    "DesktopCompanionMemoryBridge",
    "DesktopMemoryBridgeOutcome",
    "default_desktop_companion_memory_bridge",
]
