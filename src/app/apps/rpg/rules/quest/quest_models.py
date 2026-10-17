"""Quest data models for the Quest Emergence Engine.

This module defines the core data structures for multi-stage narrative quests:
- Quest: A quest with arc progression, stages, and history
- QuestStage: A stage within a quest with objectives and world effects
- QuestObjective: An individual objective within a quest stage
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QuestObjective:
    """An individual objective within a quest stage.

    Attributes:
        id: Unique identifier for this objective.
        description: Human-readable description of the objective.
        progress: Progress toward completion (0.0 to 1.0).
        completed: Whether this objective has been completed.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    progress: float = 0.0
    completed: bool = False

    def update_progress(self, amount: float) -> None:
        """Update progress by the given amount.

        Args:
            amount: Amount to add to current progress.
        """
        self.progress = min(1.0, self.progress + amount)
        if self.progress >= 1.0:
            self.completed = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert objective to dict for serialization.

        Returns:
            Dict with all objective attributes.
        """
        return {
            "id": self.id,
            "description": self.description,
            "progress": self.progress,
            "completed": self.completed,
        }


@dataclass
class QuestStage:
    """A stage within a quest arc (e.g., setup, escalation, climax, resolution).

    Attributes:
        name: Stage name (e.g., "setup", "climax").
        description: Human-readable description of this stage.
        objectives: List of objectives that must be completed for this stage.
        completion_trigger: Event type that triggers stage completion.
        world_effects: Dictionary of world state changes when stage completes.
    """

    name: str = ""
    description: str = ""
    objectives: List[QuestObjective] = field(default_factory=list)
    completion_trigger: Dict[str, Any] = field(default_factory=dict)
    world_effects: Dict[str, Any] = field(default_factory=dict)

    @property
    def all_completed(self) -> bool:
        """Check if all objectives in this stage are complete."""
        return len(self.objectives) > 0 and all(o.completed for o in self.objectives)

    def to_dict(self) -> Dict[str, Any]:
        """Convert stage to dict for serialization.

        Returns:
            Dict with all stage attributes.
        """
        return {
            "name": self.name,
            "description": self.description,
            "objectives": [o.to_dict() for o in self.objectives],
            "completion_trigger": self.completion_trigger,
            "world_effects": self.world_effects,
        }


@dataclass
class Quest:
    """A multi-stage narrative quest with arc progression.

    Quests have:
    - Act-based structure (setup → escalation → climax → resolution)
    - Reactive progression based on events/player choices
    - World-impacting effects when stages complete
    - History tracking for narrative continuity

    Attributes:
        id: Unique quest identifier.
        title: Human-readable quest title.
        description: Current quest description (updates per stage).
        type: Quest arc type (e.g., "conflict", "betrayal").
        arc_stage: Current arc stage name.
        arc_progress: Overall quest progress (0.0 to 1.0).
        stages: List of quest stages.
        current_stage_index: Index of the currently active stage.
        history: List of completed stage records.
        status: Quest status ("active", "completed", "failed").
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    type: str = ""

    # Narrative structure
    arc_stage: str = "setup"
    arc_progress: float = 0.0
    stages: List[QuestStage] = field(default_factory=list)
    current_stage_index: int = 0

    # Story memory
    history: List[Dict[str, Any]] = field(default_factory=list)

    # Status
    status: str = "active"

    @property
    def current_stage(self) -> Optional[QuestStage]:
        """Get the currently active stage.

        Returns:
            Current QuestStage or None if no stages exist.
        """
        if self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None

    @property
    def next_stage(self) -> Optional[QuestStage]:
        """Get the next stage in the quest.

        Returns:
            Next QuestStage or None if at the end.
        """
        next_index = self.current_stage_index + 1
        if next_index < len(self.stages):
            return self.stages[next_index]
        return None

    @property
    def active_objectives(self) -> List[QuestObjective]:
        """Get objectives from the current stage that are not yet completed.

        Returns:
            List of incomplete objectives.
        """
        stage = self.current_stage
        if stage:
            return [o for o in stage.objectives if not o.completed]
        return []

    def complete(self) -> None:
        """Mark the quest as completed and update metadata."""
        self.status = "completed"
        self.arc_progress = 1.0
        self.arc_stage = "completed"
        self.current_stage_index = len(self.stages)

    def fail(self, reason: str = "") -> None:
        """Mark the quest as failed.

        Args:
            reason: Optional reason for failure.
        """
        self.status = "failed"
        if reason:
            self.history.append({
                "action": "failed",
                "reason": reason,
            })

    def to_dict(self) -> Dict[str, Any]:
        """Convert quest to dict for serialization.

        Returns:
            Dict with all quest attributes.
        """
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "arc_stage": self.arc_stage,
            "arc_progress": self.arc_progress,
            "stages": [s.to_dict() for s in self.stages],
            "current_stage_index": self.current_stage_index,
            "history": self.history,
            "status": self.status,
        }