from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GMDirective:
    directive_id: str
    directive_type: str
    scope: str = "global"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GMDirective":
        return cls(**data)


@dataclass
class InjectEventDirective(GMDirective):
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PinThreadDirective(GMDirective):
    thread_id: str = ""
    priority: str = "high"


@dataclass
class RetconDirective(GMDirective):
    subject: str = ""
    predicate: str = ""
    value: Any = None
    reason: str = ""


@dataclass
class CanonOverrideDirective(GMDirective):
    fact_id: str = ""
    subject: str = ""
    predicate: str = ""
    value: Any = None
    reason: str = ""


@dataclass
class PacingDirective(GMDirective):
    style: str = "balanced"


@dataclass
class ToneDirective(GMDirective):
    tone: str = "neutral"
    target_scope: str = "scene"


@dataclass
class DangerDirective(GMDirective):
    level: str = "medium"
    target_scope: str = "scene"


@dataclass
class TargetNPCDirective(GMDirective):
    npc_id: str = ""
    instruction: str = ""


@dataclass
class TargetFactionDirective(GMDirective):
    faction_id: str = ""
    instruction: str = ""


@dataclass
class TargetLocationDirective(GMDirective):
    location_id: str = ""
    instruction: str = ""


@dataclass
class RevealDirective(GMDirective):
    reveal_type: str = ""
    target_id: str = ""
    timing: str = "soon"


@dataclass
class OptionFramingDirective(GMDirective):
    directive_type: str = "option_framing"
    scope: str = "scene"
    enabled: bool = True
    force: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecapDirective(GMDirective):
    directive_type: str = "recap"
    scope: str = "scene"
    enabled: bool = True
    force: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


DIRECTIVE_TYPES = {
    "inject_event": InjectEventDirective,
    "pin_thread": PinThreadDirective,
    "retcon": RetconDirective,
    "canon_override": CanonOverrideDirective,
    "pacing": PacingDirective,
    "tone": ToneDirective,
    "danger": DangerDirective,
    "target_npc": TargetNPCDirective,
    "target_faction": TargetFactionDirective,
    "target_location": TargetLocationDirective,
    "reveal": RevealDirective,
    "option_framing": OptionFramingDirective,
    "recap": RecapDirective,
}


class GMDirectiveState:
    def __init__(self) -> None:
        self.directives: dict[str, GMDirective] = {}

    def list_directives(self) -> list[GMDirective]:
        return list(self.directives.values())

    def get_active_directives(self) -> list[GMDirective]:
        return [d for d in self.directives.values() if d.enabled]

    def get_pending_injected_events(self) -> list[dict]:
        """Return deterministic event payloads for active inject-event directives.

        The game loop is responsible for actually emitting these into the EventBus.
        Returned items include directive identity and scope so the loop can
        clear only successfully emitted scene-scoped directives.
        """
        events: list[dict] = []
        for directive in self.get_active_directives():
            if isinstance(directive, InjectEventDirective):
                events.append(
                    {
                        "directive_id": directive.directive_id,
                        "scope": directive.scope,
                        "event_type": directive.event_type,
                        "payload": copy.deepcopy(directive.payload),
                    }
                )
        return events

    def remove_directives(self, directive_ids: list[str]) -> None:
        """Remove a specific set of directives by id."""
        for directive_id in directive_ids:
            self.directives.pop(directive_id, None)

    def apply_to_coherence(self, coherence_core: Any) -> None:
        from app.apps.rpg.narration.coherence.models import FactRecord

        for directive in self.get_active_directives():
            if isinstance(directive, RetconDirective):
                coherence_core.upsert_fact(
                    FactRecord(
                        fact_id=f"gm_retcon:{directive.directive_id}",
                        category="world",
                        subject=directive.subject,
                        predicate=directive.predicate,
                        value=directive.value,
                        authority="creator_canon",
                        status="confirmed",
                        metadata={"directive_id": directive.directive_id, **directive.metadata},
                    )
                )
            elif isinstance(directive, CanonOverrideDirective):
                subject = directive.subject or (
                    directive.fact_id.split(":", 1)[0] if ":" in directive.fact_id else directive.fact_id
                )
                predicate = directive.predicate or (
                    directive.fact_id.split(":", 1)[1] if ":" in directive.fact_id else "value"
                )
                coherence_core.upsert_fact(
                    FactRecord(
                        fact_id=directive.fact_id,
                        category="world",
                        subject=subject,
                        predicate=predicate,
                        value=directive.value,
                        authority="creator_canon",
                        status="confirmed",
                        metadata={
                            "directive_id": directive.directive_id,
                            "directive_type": "canon_override",
                            **directive.metadata,
                        },
                    )
                )

    def build_director_context(self) -> dict:
        active = self.get_active_directives()
        return {
            "active_directives": [self._directive_to_dict(d) for d in active],
            "pacing": [d.style for d in active if isinstance(d, PacingDirective)],
            "tone": [d.tone for d in active if isinstance(d, ToneDirective)],
            "danger": [d.level for d in active if isinstance(d, DangerDirective)],
            "pinned_threads": [d.thread_id for d in active if isinstance(d, PinThreadDirective)],
        }

    # ------------------------------------------------------------------
    # Entity-targeted query helpers (Phase 7.1)
    # ------------------------------------------------------------------

    def find_directives_for_npc(self, npc_id: str) -> list[GMDirective]:
        """Return all active directives that target a specific NPC."""
        results: list[GMDirective] = []
        for d in self.get_active_directives():
            if isinstance(d, TargetNPCDirective) and d.npc_id == npc_id:
                results.append(d)
            elif isinstance(d, RevealDirective) and d.target_id == npc_id:
                results.append(d)
        return results

    # ------------------------------------------------------------------
    # Gameplay-control query helpers (Phase 7.2)
    # ------------------------------------------------------------------

    def has_forced_option_framing(self) -> bool:
        """Return True if any active directive forces option framing."""
        for d in self.get_active_directives():
            if isinstance(d, OptionFramingDirective) and d.force:
                return True
        return False

    def has_forced_recap(self) -> bool:
        """Return True if any active directive forces a recap."""
        for d in self.get_active_directives():
            if isinstance(d, RecapDirective) and d.force:
                return True
        return False

    def get_focus_target(self) -> dict | None:
        """Return the current focus target from directives, if any."""
        for d in self.get_active_directives():
            if isinstance(d, TargetNPCDirective) and d.npc_id:
                return {"target_type": "npc", "target_id": d.npc_id}
            if isinstance(d, TargetFactionDirective) and d.faction_id:
                return {"target_type": "faction", "target_id": d.faction_id}
            if isinstance(d, TargetLocationDirective) and d.location_id:
                return {"target_type": "location", "target_id": d.location_id}
            if isinstance(d, PinThreadDirective) and d.thread_id:
                return {"target_type": "thread", "target_id": d.thread_id}
        return None

    def serialize_state(self) -> dict:
        return {
            "directives": {
                k: self._directive_to_dict(v) for k, v in self.directives.items()
            }
        }

    def deserialize_state(self, data: dict) -> None:
        self.directives = {}
        for directive_id, payload in data.get("directives", {}).items():
            cls = DIRECTIVE_TYPES.get(payload.get("directive_type"), GMDirective)
            self.directives[directive_id] = cls(**payload)

    def _directive_to_dict(self, directive: GMDirective) -> dict:
        return asdict(directive)
