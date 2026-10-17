"""Quest Arc Builder for the Quest Emergence Engine.

This module provides the QuestArcBuilder class that constructs
multi-stage quest arcs from templates based on detected events.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from .quest_models import Quest, QuestObjective, QuestStage
from .quest_templates import QUEST_ARCS


class QuestArcBuilder:
    """Builds multi-stage quest arcs from templates.

    The QuestArcBuilder takes an event and arc type, then constructs
    a complete quest with all stages, objectives, and world effects
    based on the predefined quest arc templates.

    Usage:
        builder = QuestArcBuilder()
        quest = builder.build_arc(event, "conflict")
    """

    def build_arc(self, event: Dict[str, Any], arc_type: str) -> Quest:
        """Build a complete quest arc from template.

        Args:
            event: Event dict that triggered this quest.
            arc_type: Type of quest arc (e.g., "conflict", "betrayal").

        Returns:
            Fully constructed Quest with all stages and objectives.

        Raises:
            KeyError: If arc_type is not found in QUEST_ARCS.
        """
        if arc_type not in QUEST_ARCS:
            raise KeyError(f"Unknown quest arc type: {arc_type}")

        arc_def = QUEST_ARCS[arc_type]
        quest_id = str(uuid.uuid4())

        quest = Quest(
            id=quest_id,
            title=self._generate_title(arc_type, event),
            description="",
            type=arc_type,
            arc_stage="setup",
        )

        stages = []
        for stage_def in arc_def:
            objectives = [
                QuestObjective(
                    description=obj_desc,
                )
                for obj_desc in stage_def.get("objectives", [])
            ]

            stage = QuestStage(
                name=stage_def["name"],
                description=stage_def.get("description", ""),
                objectives=objectives,
                completion_trigger={"type": event.get("type", arc_type)},
                world_effects=stage_def.get("world_effects", {}),
            )
            stages.append(stage)

        quest.stages = stages
        quest.arc_stage = stages[0].name if stages else "setup"

        return quest

    def _generate_title(self, arc_type: str, event: Dict[str, Any]) -> str:
        """Generate a quest title from arc type and event context.

        Args:
            arc_type: Type of quest arc.
            event: Event that triggered the quest.

        Returns:
            Human-readable quest title.
        """
        event_type = event.get("type", "unknown")

        titles = {
            "conflict": f"Shadows of {event_type.title()}",
            "betrayal": f"The {event_type.title()} Plot",
            "supply": f"The {event_type.title()} Crisis",
            "alliance": f"Diplomacy of {event_type.title()}",
            "rebellion": f"The {event_type.title()} Uprising",
        }

        return titles.get(arc_type, f"The {event_type.title()} Quest")

