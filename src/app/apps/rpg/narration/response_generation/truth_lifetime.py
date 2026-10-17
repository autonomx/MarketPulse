from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping


class TruthClass(str, Enum):
    CONFIRMED_FACT = "confirmed_fact"
    RETRIEVED_LORE = "retrieved_lore"
    NPC_BELIEF = "npc_belief"
    RUMOR = "rumor"
    INFERENCE = "inference"
    GENERATED_PROPOSAL = "generated_proposal"
    UNVERIFIED_PLAYER_CLAIM = "unverified_player_claim"
    HIDDEN_FACT = "hidden_fact"


class TruthLifetime(str, Enum):
    TURN = "turn"
    SCENE = "scene"
    PERSISTENT = "persistent"


@dataclass(frozen=True)
class LifetimeTransition:
    from_lifetime: TruthLifetime
    to_lifetime: TruthLifetime
    turn_id: str
    reason: str
    event_id: str = ""


@dataclass(frozen=True)
class SoftTruthRecord:
    truth_ref: str
    truth_class: TruthClass
    content: Any
    provenance_refs: tuple[str, ...] = ()
    visibility: str = "player_visible"
    confidence: float = 0.5
    lifetime: TruthLifetime = TruthLifetime.TURN
    created_turn: int = 0
    created_turn_id: str = ""
    scene_id: str = ""
    expires_turn: int | None = None
    source: str = "response_generation"
    promotion_history: tuple[LifetimeTransition, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def persistent(self) -> bool:
        return self.lifetime is TruthLifetime.PERSISTENT

    @property
    def hidden(self) -> bool:
        return self.truth_class is TruthClass.HIDDEN_FACT or self.visibility == "hidden"

    def is_expired(self, *, current_turn: int, scene_id: str = "") -> bool:
        if self.lifetime is TruthLifetime.PERSISTENT:
            return False
        if self.expires_turn is not None and current_turn > self.expires_turn:
            return True
        if self.lifetime is TruthLifetime.TURN and current_turn > self.created_turn:
            return True
        if self.lifetime is TruthLifetime.SCENE and self.scene_id and scene_id:
            return self.scene_id != scene_id
        return False

    def promote(
        self,
        lifetime: TruthLifetime,
        *,
        turn_id: str,
        reason: str,
        event_id: str = "",
        expires_turn: int | None = None,
    ) -> "SoftTruthRecord":
        if _lifetime_rank(lifetime) < _lifetime_rank(self.lifetime):
            raise ValueError("truth lifetime cannot be demoted through promote")
        if lifetime is self.lifetime:
            return self
        transition = LifetimeTransition(
            from_lifetime=self.lifetime,
            to_lifetime=lifetime,
            turn_id=turn_id,
            reason=reason,
            event_id=event_id,
        )
        return replace(
            self,
            lifetime=lifetime,
            expires_turn=expires_turn,
            promotion_history=(*self.promotion_history, transition),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "truth_ref": self.truth_ref,
            "truth_class": self.truth_class.value,
            "content": self.content,
            "provenance_refs": list(self.provenance_refs),
            "visibility": self.visibility,
            "confidence": self.confidence,
            "lifetime": self.lifetime.value,
            "created_turn": self.created_turn,
            "created_turn_id": self.created_turn_id,
            "scene_id": self.scene_id,
            "expires_turn": self.expires_turn,
            "source": self.source,
            "promotion_history": [
                {
                    "from_lifetime": transition.from_lifetime.value,
                    "to_lifetime": transition.to_lifetime.value,
                    "turn_id": transition.turn_id,
                    "reason": transition.reason,
                    "event_id": transition.event_id,
                }
                for transition in self.promotion_history
            ],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SoftTruthRecord":
        history = tuple(
            LifetimeTransition(
                from_lifetime=TruthLifetime(str(row.get("from_lifetime") or "turn")),
                to_lifetime=TruthLifetime(str(row.get("to_lifetime") or "turn")),
                turn_id=str(row.get("turn_id") or ""),
                reason=str(row.get("reason") or ""),
                event_id=str(row.get("event_id") or ""),
            )
            for row in payload.get("promotion_history", ())
            if isinstance(row, Mapping)
        )
        return cls(
            truth_ref=str(payload.get("truth_ref") or ""),
            truth_class=TruthClass(str(payload.get("truth_class") or "generated_proposal")),
            content=payload.get("content"),
            provenance_refs=tuple(
                str(item) for item in payload.get("provenance_refs", ()) if str(item)
            ),
            visibility=str(payload.get("visibility") or "player_visible"),
            confidence=float(payload.get("confidence") or 0.0),
            lifetime=TruthLifetime(str(payload.get("lifetime") or "turn")),
            created_turn=int(payload.get("created_turn") or 0),
            created_turn_id=str(payload.get("created_turn_id") or ""),
            scene_id=str(payload.get("scene_id") or ""),
            expires_turn=(
                int(payload["expires_turn"])
                if payload.get("expires_turn") is not None
                else None
            ),
            source=str(payload.get("source") or "response_generation"),
            promotion_history=history,
            metadata=(
                dict(payload.get("metadata") or {})
                if isinstance(payload.get("metadata"), Mapping)
                else {}
            ),
        )


def _lifetime_rank(lifetime: TruthLifetime) -> int:
    return {
        TruthLifetime.TURN: 0,
        TruthLifetime.SCENE: 1,
        TruthLifetime.PERSISTENT: 2,
    }[lifetime]
