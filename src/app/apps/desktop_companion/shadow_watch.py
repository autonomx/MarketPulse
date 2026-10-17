"""Pure eligibility planning for background desktop observations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import DesktopActivitySignal, DesktopBehaviorState, DesktopCompanionPolicy

ShadowWatchAction = Literal["submit", "wait", "suppress"]


@dataclass(frozen=True, slots=True)
class ShadowWatchDecision:
    action: ShadowWatchAction
    reason: str
    eligible_in_ms: int | None = None


def decide_shadow_watch(
    *,
    policy: DesktopCompanionPolicy,
    activity: DesktopActivitySignal,
    behavior: DesktopBehaviorState,
    watch_enabled: bool,
    page_visible: bool,
    provider_ready: bool,
    request_active: bool,
    pending_request: bool,
    now_ms: int,
    last_observation_started_ms: int | None,
    coordinator_eligible_in_ms: int = 0,
) -> ShadowWatchDecision:
    """Decide whether a changed frame is eligible for shadow vision analysis.

    This policy never decides to generate or deliver commentary. Low-confidence
    activity hypotheses are advisory and cannot independently suppress a request.
    """

    if not policy.enabled:
        return _suppress("desktop_companion_disabled")
    if not watch_enabled:
        return _suppress("watch_disabled")
    if not page_visible:
        return _suppress("page_hidden")
    if not provider_ready:
        return _suppress("vision_provider_unavailable")
    if request_active:
        return _wait("vision_request_active")
    if pending_request:
        return _wait("vision_request_pending")
    if activity.activity in {"static", "micro_change", "unknown"}:
        return _suppress("no_meaningful_visual_change")
    if activity.confidence < policy.minimum_change_confidence:
        return _suppress("change_confidence_below_threshold")

    # Only strong, repeated behavioural evidence should delay observation. A
    # single visual typing hypothesis remains eligible because game HUD motion
    # and video overlays can look similar at low resolution.
    if behavior.likely_typing and behavior.sample_count >= 3:
        return _wait("likely_typing")
    if behavior.rapid_browsing and behavior.current_pattern != "settled":
        return _wait("rapid_browsing")

    interval_remaining = 0
    if last_observation_started_ms is not None:
        interval_remaining = max(
            0,
            policy.minimum_observation_interval_ms - (now_ms - last_observation_started_ms),
        )
    remaining = max(interval_remaining, max(0, coordinator_eligible_in_ms))
    if remaining > 0:
        return _wait("observation_interval", remaining)
    return ShadowWatchDecision(action="submit", reason="meaningful_change_available", eligible_in_ms=0)


def _wait(reason: str, eligible_in_ms: int | None = None) -> ShadowWatchDecision:
    return ShadowWatchDecision(action="wait", reason=reason, eligible_in_ms=eligible_in_ms)


def _suppress(reason: str) -> ShadowWatchDecision:
    return ShadowWatchDecision(action="suppress", reason=reason, eligible_in_ms=None)


__all__ = ["ShadowWatchDecision", "decide_shadow_watch"]
