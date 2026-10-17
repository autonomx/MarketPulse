"""Presence policy decides which companion channels are currently allowed."""
from __future__ import annotations

from typing import Literal

from .contracts import FrozenContract

PresenceMode = Literal["off", "quiet", "balanced", "chatty"]


class CompanionPresenceInput(FrozenContract):
    mode: PresenceMode = "balanced"
    tab_visible: bool = True
    text_enabled: bool = True
    avatar_available: bool = False
    voice_call_connected: bool = False
    voice_output_enabled: bool = False
    notifications_enabled: bool = False
    sensitive_activity: bool = False


class CompanionPresenceDecision(FrozenContract):
    can_text: bool
    can_avatar: bool
    can_voice: bool
    can_notify: bool
    reasons: tuple[str, ...] = ()


class CompanionPresencePolicy:
    """Keep text/avatar presence independent from voice-call/microphone state."""

    def decide(self, input: CompanionPresenceInput) -> CompanionPresenceDecision:
        if input.mode == "off":
            return CompanionPresenceDecision(
                can_text=False,
                can_avatar=False,
                can_voice=False,
                can_notify=False,
                reasons=("presence_disabled",),
            )

        can_text = input.text_enabled and input.tab_visible
        can_avatar = input.avatar_available and input.tab_visible
        can_voice = (
            input.voice_call_connected
            and input.voice_output_enabled
            and input.mode != "quiet"
            and not input.sensitive_activity
        )
        can_notify = (
            input.notifications_enabled
            and not input.tab_visible
            and input.mode == "chatty"
            and not input.sensitive_activity
        )
        reasons: list[str] = []
        if not input.tab_visible:
            reasons.append("tab_hidden")
        if not input.voice_call_connected:
            reasons.append("voice_call_not_connected")
        if input.sensitive_activity:
            reasons.append("sensitive_activity_limits_externalized_channels")
        if input.mode == "quiet":
            reasons.append("quiet_presence_mode")
        return CompanionPresenceDecision(
            can_text=can_text,
            can_avatar=can_avatar,
            can_voice=can_voice,
            can_notify=can_notify,
            reasons=tuple(reasons),
        )


__all__ = [
    "CompanionPresenceDecision",
    "CompanionPresenceInput",
    "CompanionPresencePolicy",
    "PresenceMode",
]
