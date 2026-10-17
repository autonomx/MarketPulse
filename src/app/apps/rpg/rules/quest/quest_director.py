"""Quest Director for the Quest Emergence Engine.

This module provides the QuestDirector class that generates
narrative descriptions for quests based on their current stage.
"""

from __future__ import annotations

from typing import Any, Dict


class QuestDirector:
    """Generates narrative descriptions for quests.

    The QuestDirector provides human-readable quest descriptions
    that update based on the current stage, including objectives
    and narrative context.

    Usage:
        director = QuestDirector()
        desc = director.generate_description(quest)
    """

    def generate_description(self, quest: Any) -> str:
        """Generate a narrative description for the quest.

        Creates a formatted description showing the quest title,
        current stage, stage description, and active objectives.

        Args:
            quest: Quest object to describe.

        Returns:
            Formatted quest description string.
        """
        if not quest.stages:
            return f"[{quest.title}] - A quest with no stages"

        stage = quest.stages[quest.current_stage_index] if quest.current_stage_index < len(quest.stages) else quest.stages[-1]

        active_objectives = [o for o in stage.objectives if not o.completed]

        description = f"""[{quest.title} — {stage.name.upper()}]

{stage.description}

Objectives:
- """ + "\n- ".join(o.description for o in active_objectives) if active_objectives else "Objectives:\n- Complete remaining tasks"

        return description

    def generate_summary(self, quest: Any) -> Dict[str, Any]:
        """Generate a structured summary dict for the quest.

        Args:
            quest: Quest object to summarize.

        Returns:
            Dict with quest summary information.
        """
        stage = quest.current_stage

        return {
            "id": quest.id,
            "title": quest.title,
            "type": quest.type,
            "stage": stage.name if stage else "none",
            "arc_progress": quest.arc_progress,
            "status": quest.status,
            "active_objectives": len(quest.active_objectives),
            "total_objectives": sum(len(s.objectives) for s in quest.stages),
            "description": self.generate_description(quest),
        }

