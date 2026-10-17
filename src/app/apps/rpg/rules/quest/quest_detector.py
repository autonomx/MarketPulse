"""Quest Detector for the Quest Emergence Engine.

This module provides the QuestDetector class that identifies
when events should trigger new quests based on event properties
and world state conditions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .quest_models import Quest


class QuestDetector:
    """Detects when events should trigger new quests.

    The QuestDetector examines events and world state to determine
    if a new quest should be created. It uses event type mapping
    and importance thresholds to make detection decisions.

    Usage:
        detector = QuestDetector()
        quest = detector.detect(event, world_state)
    """

    # Minimum importance for quest generation
    IMPORTANCE_THRESHOLD = 0.3

    # Event type keywords that map to quest types
    EVENT_QUEST_MAP = {
        "attack": "war",
        "battle": "war",
        "war": "war",
        "conflict": "conflict",
        "fight": "conflict",
        "betray": "betrayal",
        "betrayal": "betrayal",
        "treason": "betrayal",
        "suspicious": "betrayal",
        "shortage": "supply",
        "supply": "supply",
        "trade": "trade",
        "alliance": "alliance",
        "diplomacy": "diplomacy",
        "rebellion": "rebellion",
        "coup": "rebellion",
        "uprising": "rebellion",
    }

    def __init__(self, importance_threshold: float = IMPORTANCE_THRESHOLD):
        """Initialize the QuestDetector.

        Args:
            importance_threshold: Minimum event importance to trigger quest.
        """
        self.importance_threshold = importance_threshold

    def detect(
        self,
        event: Dict[str, Any],
        world_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[Quest]:
        """Detect if an event should trigger a new quest.

        Examines the event type and importance against thresholds
        and world state to determine if a quest should be created.

        Args:
            event: Event dict with type and optional importance.
            world_state: Current world state (optional).

        Returns:
            New Quest if event triggers quest, None otherwise.
        """
        if not event:
            return None

        event_type = event.get("type", "")
        importance = event.get("importance", 0.5)

        # Check importance threshold
        if importance < self.importance_threshold:
            return None

        # Map event type to quest type
        quest_type = self._map_event_to_quest_type(event_type)
        if not quest_type:
            return None

        # Create quest based on event
        quest = Quest(
            title=self._generate_title(event_type, quest_type),
            description=event.get("description", f"A {quest_type} has been detected"),
            type=quest_type,
        )

        return quest

    def _map_event_to_quest_type(self, event_type: str) -> Optional[str]:
        """Map event type to quest type.

        Args:
            event_type: Event type string.

        Returns:
            Quest type string or None if no mapping.
        """
        event_lower = event_type.lower()

        # Direct mapping
        if event_lower in self.EVENT_QUEST_MAP:
            return self.EVENT_QUEST_MAP[event_lower]

        # Partial matching
        for keyword, quest_type in self.EVENT_QUEST_MAP.items():
            if keyword in event_lower or event_lower in keyword:
                return quest_type

        return None

    def _generate_title(self, event_type: str, quest_type: str) -> str:
        """Generate a quest title from event type.

        Args:
            event_type: Original event type.
            quest_type: Mapped quest type.

        Returns:
            Human-readable quest title.
        """
        title_templates = {
            "war": "The {event} War",
            "conflict": "Shadows of {event}",
            "betrayal": "The {event} Conspiracy",
            "supply": "The {event} Shortage",
            "trade": "Trade Winds of {event}",
            "alliance": "Diplomacy of {event}",
            "diplomacy": "The {event} Negotiations",
            "rebellion": "The {event} Uprising",
        }

        template = title_templates.get(quest_type, "The {event} Quest")
        return template.format(event=event_type.title())

