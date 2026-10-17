"""Choice data models for the Irreversible Consequence Engine.

This module defines data structures for player choices and their lifecycle:
- PlayerChoice: A decision point with options presented to the player
- ConsequenceRecord: A recorded consequence of a player's choice
- TimelineEntry: A permanent entry in the world's history timeline

These models support the design principle that player choices create
irreversible world mutations and belief shifts.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PlayerChoice:
    """A meaningful decision presented to the player.

    PlayerChoices are generated when quests reach decision stages
    (e.g., escalation, confrontation). Each choice presents multiple
    options that will lead to different consequences.

    Attributes:
        id: Unique identifier for this choice instance.
        quest_id: ID of the quest this choice belongs to.
        stage: The quest stage during which this choice was made.
        description: Human-readable description of the decision context.
        options: List of available options with IDs and display text.
        created_at: Timestamp when this choice was created.
        resolved: Whether the player has made a selection.
        selected_option: The option the player chose (None until resolved).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    quest_id: str = ""
    stage: str = ""
    description: str = ""
    options: List[Dict[str, Any]] = field(default_factory=list)

    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    selected_option: Optional[Dict[str, Any]] = None

    def select_option(self, option_id: str) -> Optional[Dict[str, Any]]:
        """Select an option by its ID.

        Args:
            option_id: The ID of the option to select.

        Returns:
            The selected option dict, or None if option not found.
        """
        for opt in self.options:
            if opt.get("id") == option_id:
                self.selected_option = opt
                self.resolved = True
                return opt
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert choice to dict for serialization.

        Returns:
            Dict with all choice attributes.
        """
        return {
            "id": self.id,
            "quest_id": self.quest_id,
            "stage": self.stage,
            "description": self.description,
            "options": self.options,
            "created_at": self.created_at,
            "resolved": self.resolved,
            "selected_option": self.selected_option,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlayerChoice":
        """Create a PlayerChoice from a dict.

        Args:
            data: Dict with choice attributes.

        Returns:
            New PlayerChoice instance.
        """
        choice = cls(
            id=data.get("id", str(uuid.uuid4())),
            quest_id=data.get("quest_id", ""),
            stage=data.get("stage", ""),
            description=data.get("description", ""),
            options=data.get("options", []),
            created_at=data.get("created_at", time.time()),
            resolved=data.get("resolved", False),
            selected_option=data.get("selected_option"),
        )
        return choice


@dataclass
class ConsequenceRecord:
    """A recorded consequence from a player's choice.

    Each consequence has a type that determines how it affects
    the world state, beliefs, or narrative.

    Attributes:
        id: Unique identifier for this consequence record.
        choice_id: ID of the choice that produced this consequence.
        consequence_type: Type of consequence (e.g., "faction_power_shift", "belief_update").
        data: The consequence data (e.g., actor, target, delta for power shifts).
        applied: Whether this consequence has been applied to the world.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    choice_id: str = ""
    consequence_type: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert consequence to dict for serialization.

        Returns:
            Dict with all consequence attributes.
        """
        return {
            "id": self.id,
            "choice_id": self.choice_id,
            "consequence_type": self.consequence_type,
            "data": self.data,
            "applied": self.applied,
        }


@dataclass
class TimelineEntry:
    """A permanent entry in the world's history timeline.

    Timeline entries record player choices and their consequences.
    They cannot be removed or undone, ensuring irreversible narrative.

    Attributes:
        id: Unique identifier for this timeline entry.
        choice_id: ID of the choice being recorded.
        option_selected: The option the player chose.
        consequences: List of consequence records.
        timestamp: When this entry was recorded.
        tags: Irreversible flags for the world state (e.g., "faction_destroyed").
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    choice_id: str = ""
    option_selected: Dict[str, Any] = field(default_factory=dict)
    consequences: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert timeline entry to dict for serialization.

        Returns:
            Dict with all timeline entry attributes.
        """
        return {
            "id": self.id,
            "choice_id": self.choice_id,
            "option_selected": self.option_selected,
            "consequences": self.consequences,
            "timestamp": self.timestamp,
            "tags": self.tags,
        }