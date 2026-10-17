"""Render an already-authorized delivery intent into channel actions.

This layer never decides whether an intent deserves delivery. It requires a matching,
still-active initiative lease and a presence decision, then produces a deterministic plan.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .cognition import DeliveryIntent, DeliveryIntentKind
from .contracts import FrozenContract
from .initiative import (
    CompanionInitiativeAuthorityStore,
    InitiativeLease,
    default_companion_initiative_authority,
)
from .presence import CompanionPresenceDecision

ChannelActionKind = Literal["text", "avatar", "voice", "notification"]


class ChannelAction(FrozenContract):
    channel: ChannelActionKind
    intent_id: str = Field(min_length=1, max_length=240)
    semantic_action: DeliveryIntentKind
    grounding_proposition_ids: tuple[str, ...] = ()
    primary: bool = False


class ChannelPlan(FrozenContract):
    intent_id: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=200)
    lease_id: str | None = Field(default=None, max_length=240)
    actions: tuple[ChannelAction, ...] = ()
    suppressed_reason: str | None = Field(default=None, max_length=240)


class CompanionChannelCoordinator:
    """Coordinate rendering without bypassing presence or initiative authority."""

    def __init__(
        self,
        authority: CompanionInitiativeAuthorityStore | None = None,
    ) -> None:
        self._authority = authority or default_companion_initiative_authority()

    def coordinate(
        self,
        *,
        intent: DeliveryIntent,
        lease: InitiativeLease | None,
        presence: CompanionPresenceDecision,
        now: datetime,
    ) -> ChannelPlan:
        if intent.kind == "IGNORE":
            return ChannelPlan(
                intent_id=intent.intent_id,
                session_id=intent.session_id,
                suppressed_reason="intent_ignore",
            )
        if lease is None:
            return ChannelPlan(
                intent_id=intent.intent_id,
                session_id=intent.session_id,
                suppressed_reason="initiative_lease_required",
            )
        if lease.session_id != intent.session_id or lease.intent_id != intent.intent_id:
            raise ValueError("initiative lease does not authorize this delivery intent")
        if not self._authority.authorizes(lease, now=now):
            return ChannelPlan(
                intent_id=intent.intent_id,
                session_id=intent.session_id,
                lease_id=lease.lease_id,
                suppressed_reason="initiative_lease_inactive",
            )

        allowed = {
            "text": presence.can_text,
            "avatar": presence.can_avatar,
            "voice": presence.can_voice,
            "notification": presence.can_notify,
        }
        if not allowed[lease.channel]:
            return ChannelPlan(
                intent_id=intent.intent_id,
                session_id=intent.session_id,
                lease_id=lease.lease_id,
                suppressed_reason=f"presence_disallows_{lease.channel}",
            )

        actions = [
            ChannelAction(
                channel=lease.channel,
                intent_id=intent.intent_id,
                semantic_action=intent.kind,
                grounding_proposition_ids=intent.grounding_proposition_ids,
                primary=True,
            )
        ]
        if lease.channel != "avatar" and presence.can_avatar:
            actions.append(
                ChannelAction(
                    channel="avatar",
                    intent_id=intent.intent_id,
                    semantic_action=intent.kind,
                    grounding_proposition_ids=intent.grounding_proposition_ids,
                    primary=False,
                )
            )
        return ChannelPlan(
            intent_id=intent.intent_id,
            session_id=intent.session_id,
            lease_id=lease.lease_id,
            actions=tuple(actions),
        )


__all__ = ["ChannelAction", "ChannelActionKind", "ChannelPlan", "CompanionChannelCoordinator"]
