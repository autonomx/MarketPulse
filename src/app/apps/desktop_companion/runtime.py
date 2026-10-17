"""End-to-end shadow observation orchestration for Desktop Companion."""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.desktop_vision import (
    CodexDesktopVisionClient,
    DesktopVisionClient,
    default_desktop_vision_client,
)

from .attention import DesktopAttentionContext, decide_desktop_attention
from .coordinator import DesktopVisionCoordinator
from .memory import DesktopSceneMemory
from .models import (
    CompanionAttentionDecision,
    DesktopActivitySignal,
    DesktopBehaviorState,
    DesktopCompanionPolicy,
    DesktopObservation,
)
from .observation import (
    parse_desktop_observation,
    screen_prompt_injection_observed,
    structured_observation_prompt,
)

ObservationRuntimeStatus = Literal["completed", "deferred", "suppressed", "error"]
Clock = Callable[[], float]
VisionClientFactory = Callable[[], DesktopVisionClient | CodexDesktopVisionClient]


class DesktopCompanionObserveRequest(BaseModel):
    """One browser-authorized, bounded background observation request."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=160)
    character_id: str | None = Field(default=None, max_length=160)
    capture_generation: str = Field(min_length=1, max_length=160)
    source_fingerprint: str = Field(min_length=1, max_length=240)
    client_sequence: int = Field(ge=0)
    captured_at: datetime
    current_image_data_url: str = Field(min_length=1, max_length=8_000_000)
    history_image_data_url: str | None = Field(default=None, max_length=8_000_000)
    combined_image_data_url: str | None = Field(default=None, max_length=8_000_000)
    history_timestamps: list[float] = Field(default_factory=list, max_length=8)
    desktop_history_timestamps: list[float] = Field(default_factory=list, max_length=8, exclude=True)
    capture_mode: Literal["single", "temporal"] = "single"
    vision_model_id: str | None = Field(default=None, max_length=240)
    activity: DesktopActivitySignal = Field(default_factory=DesktopActivitySignal)
    behavior: DesktopBehaviorState = Field(default_factory=DesktopBehaviorState)
    policy: DesktopCompanionPolicy = Field(default_factory=DesktopCompanionPolicy)
    user_floor_active: bool = False
    assistant_busy: bool = False
    request_in_flight: bool = False
    seconds_since_comment: float | None = Field(default=None, ge=0)
    visual_reaction_streak: int = Field(default=0, ge=0)
    ignored_streak: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def normalize_history_timestamp_alias(self) -> DesktopCompanionObserveRequest:
        if not self.history_timestamps and self.desktop_history_timestamps:
            self.history_timestamps = list(self.desktop_history_timestamps)
        if (
            self.history_timestamps
            and self.desktop_history_timestamps
            and self.history_timestamps != self.desktop_history_timestamps
        ):
            raise ValueError("desktop history timestamp aliases disagree")
        return self


class DesktopCompanionObserveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ObservationRuntimeStatus
    reason: str
    observation: DesktopObservation | None = None
    attention: CompanionAttentionDecision | None = None
    scene_summary: str = ""
    activity_summary: str = Field(default="", max_length=1800)
    activity_intent: str | None = Field(default=None, max_length=32)
    activity_grounding_ids: list[str] = Field(default_factory=list, max_length=32)
    activity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    activity_salience: float = Field(default=0.0, ge=0.0, le=1.0)
    delivery_eligible: bool = False
    evaluation_scenario: Literal["screen-prompt-injection"] | None = None
    coordinator: dict[str, int | str | None] = Field(default_factory=dict)


class DesktopCompanionOrchestrator:
    """Serialize provider use and compose vision, memory, and attention.

    The browser remains responsible for capture and activity sampling. This service
    never persists raw frames and never creates a visible or durable user message.
    """

    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        vision_client_factory: VisionClientFactory = default_desktop_vision_client,
        coordinator: DesktopVisionCoordinator | None = None,
        scene_memory: DesktopSceneMemory | None = None,
    ) -> None:
        self._clock = clock
        self._vision_client_factory = vision_client_factory
        self._coordinator = coordinator or DesktopVisionCoordinator(clock=clock)
        self._scene_memory = scene_memory or DesktopSceneMemory()
        self._execution_lock = threading.Lock()

    def observe(self, request: DesktopCompanionObserveRequest) -> DesktopCompanionObserveResponse:
        if not request.policy.enabled:
            return self._response("suppressed", "desktop_companion_disabled")
        if request.activity.activity in {"static", "micro_change", "unknown"}:
            return self._response("suppressed", "no_meaningful_visual_change")
        if request.activity.confidence < request.policy.minimum_change_confidence:
            return self._response("suppressed", "change_confidence_below_threshold")

        acquired = self._execution_lock.acquire(blocking=False)
        if not acquired:
            return self._response("deferred", "provider_busy")
        lease = None
        try:
            work = self._coordinator.submit(
                session_id=request.session_id,
                capture_generation=request.capture_generation,
                client_sequence=request.client_sequence,
                priority="background",
                ttl_seconds=request.policy.observation_ttl_ms / 1000,
                payload=request,
                source_fingerprint=request.source_fingerprint,
            )
            lease = self._coordinator.claim_next()
            if lease is None or lease.work.request_id != work.request_id:
                return self._response("deferred", "coordinator_budget_or_priority")

            started_at = self._clock()
            item = self._vision_client_factory().describe(
                request.current_image_data_url,
                structured_observation_prompt(),
                request.vision_model_id,
                history_image_data_url=request.history_image_data_url,
                combined_image_data_url=request.combined_image_data_url,
                history_timestamps=request.history_timestamps,
                capture_mode=request.capture_mode,
            )
            if not self._coordinator.accepts_result(lease):
                self._coordinator.abandon(lease.lease_id)
                lease = None
                return self._response("suppressed", "observation_stale_or_canceled")

            observation = parse_desktop_observation(
                item.content,
                observation_id=f"desktop-observation:{uuid.uuid4().hex}",
                session_id=request.session_id,
                character_id=request.character_id,
                capture_generation=request.capture_generation,
                source_fingerprint=request.source_fingerprint,
                client_sequence=request.client_sequence,
                captured_at=request.captured_at,
                activity=request.activity,
                behavior=request.behavior,
                ttl_seconds=request.policy.observation_ttl_ms / 1000,
                diagnostics={
                    "model": item.metadata.get("model"),
                    "fallback_mode": item.metadata.get("fallback_mode"),
                    "image_count": item.metadata.get("image_count"),
                    "latency_ms": round((self._clock() - started_at) * 1000, 3),
                },
            )
            if not self._scene_memory.record(observation):
                self._coordinator.abandon(lease.lease_id)
                lease = None
                return self._response("suppressed", "observation_expired_before_record")
            scene = self._scene_memory.snapshot(request.session_id)
            scene_age = 0.0
            if scene.last_observed_at is not None:
                scene_age = max(0.0, (observation.observed_at - scene.last_observed_at).total_seconds())
            attention = decide_desktop_attention(
                observation,
                policy=request.policy,
                context=DesktopAttentionContext(
                    scene_age_seconds=scene_age,
                    seconds_since_comment=request.seconds_since_comment,
                    visual_reaction_streak=request.visual_reaction_streak,
                    ignored_streak=request.ignored_streak,
                    user_floor_active=request.user_floor_active,
                    assistant_busy=request.assistant_busy,
                    request_in_flight=request.request_in_flight,
                    commentary_enabled=not request.policy.shadow_mode,
                    speech_muted=not request.policy.speech_enabled,
                ),
            )
            self._coordinator.complete(lease.lease_id)
            lease = None
            return DesktopCompanionObserveResponse(
                status="completed",
                reason="observation_completed",
                observation=observation,
                attention=attention,
                scene_summary=scene.compact_summary(max_chars=1500),
                delivery_eligible=attention.should_generate and not request.policy.shadow_mode,
                evaluation_scenario=(
                    "screen-prompt-injection"
                    if screen_prompt_injection_observed(observation.visible_text)
                    else None
                ),
                coordinator=self._coordinator_payload(),
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary returns structured errors
            if lease is not None:
                try:
                    self._coordinator.abandon(lease.lease_id)
                except RuntimeError:
                    pass
            return self._response("error", f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self._execution_lock.release()

    def reset(self, session_id: str, capture_generation: str | None = None) -> None:
        self._scene_memory.reset(session_id)
        if capture_generation:
            self._coordinator.cancel_generation(
                session_id=session_id,
                capture_generation=capture_generation,
            )

    def _response(self, status: ObservationRuntimeStatus, reason: str) -> DesktopCompanionObserveResponse:
        return DesktopCompanionObserveResponse(
            status=status,
            reason=reason,
            coordinator=self._coordinator_payload(),
        )

    def _coordinator_payload(self) -> dict[str, int | str | None]:
        snapshot = self._coordinator.snapshot()
        return {
            "active_request_id": snapshot.active_request_id,
            "active_priority": snapshot.active_priority,
            "foreground_pending": snapshot.foreground_pending,
            "background_pending": snapshot.background_pending,
            "background_calls_in_window": snapshot.background_calls_in_window,
            "dropped": snapshot.dropped,
            "coalesced": snapshot.coalesced,
            "canceled": snapshot.canceled,
            "stale": snapshot.stale,
        }


_default_orchestrator: DesktopCompanionOrchestrator | None = None
_default_lock = threading.Lock()


def default_desktop_companion_orchestrator() -> DesktopCompanionOrchestrator:
    global _default_orchestrator
    if _default_orchestrator is None:
        with _default_lock:
            if _default_orchestrator is None:
                _default_orchestrator = DesktopCompanionOrchestrator()
    return _default_orchestrator


__all__ = [
    "DesktopCompanionObserveRequest",
    "DesktopCompanionObserveResponse",
    "DesktopCompanionOrchestrator",
    "default_desktop_companion_orchestrator",
]
