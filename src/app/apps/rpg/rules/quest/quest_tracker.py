"""Quest Tracker for the Quest Emergence Engine.

This module provides the QuestTracker class that manages
active and completed quests, providing lookup and filtering.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional


class QuestTracker:
    """Tracks active and completed quests.

    The QuestTracker maintains collections of active, completed,
    and failed quests, providing methods to add, remove, and query
    quests by various criteria.

    Usage:
        tracker = QuestTracker()
        tracker.add(quest)
        active = tracker.get_active_quests()
    """

    def __init__(self, max_active: int = 10):
        """Initialize the QuestTracker.

        Args:
            max_active: Maximum number of active quests allowed.
        """
        self.active_quests: OrderedDict[str, Any] = OrderedDict()
        self.completed_quests: List[Any] = []
        self.failed_quests: List[Any] = []
        self.max_active = max_active

    def add(self, quest: Any) -> bool:
        """Add a quest to active tracking.

        Args:
            quest: Quest object to track.

        Returns:
            True if quest was added, False if limit reached or duplicate.
        """
        if quest.id in self.active_quests:
            return False

        if len(self.active_quests) >= self.max_active:
            return False

        self.active_quests[quest.id] = quest
        return True

    def complete(self, quest_id: str) -> Optional[Any]:
        """Move a quest from active to completed.

        Args:
            quest_id: ID of quest to complete.

        Returns:
            Completed quest or None if not found.
        """
        quest = self.active_quests.pop(quest_id, None)
        if quest:
            quest.complete()
            self.completed_quests.append(quest)
        return quest

    def fail(self, quest_id: str, reason: str = "") -> Optional[Any]:
        """Move a quest from active to failed.

        Args:
            quest_id: ID of quest to fail.
            reason: Reason for failure.

        Returns:
            Failed quest or None if not found.
        """
        quest = self.active_quests.pop(quest_id, None)
        if quest:
            quest.fail(reason)
            self.failed_quests.append(quest)
        return quest

    def remove(self, quest_id: str) -> Optional[Any]:
        """Remove a quest from tracking entirely.

        Args:
            quest_id: ID of quest to remove.

        Returns:
            Removed quest or None if not found.
        """
        quest = self.active_quests.pop(quest_id, None)
        if quest:
            return quest

        for i, q in enumerate(self.completed_quests):
            if q.id == quest_id:
                return self.completed_quests.pop(i)

        for i, q in enumerate(self.failed_quests):
            if q.id == quest_id:
                return self.failed_quests.pop(i)

        return None

    def get_active_quests(self) -> List[Any]:
        """Get all active quests.

        Returns:
            List of active quest objects.
        """
        return list(self.active_quests.values())

    def get_completed_quests(self) -> List[Any]:
        """Get all completed quests.

        Returns:
            List of completed quest objects.
        """
        return self.completed_quests.copy()

    def get_failed_quests(self) -> List[Any]:
        """Get all failed quests.

        Returns:
            List of failed quest objects.
        """
        return self.failed_quests.copy()

    def get_quest(self, quest_id: str) -> Optional[Any]:
        """Get a quest by ID from any collection.

        Args:
            quest_id: ID of quest to find.

        Returns:
            Quest object or None if not found.
        """
        quest = self.active_quests.get(quest_id)
        if quest:
            return quest

        for q in self.completed_quests:
            if q.id == quest_id:
                return q

        for q in self.failed_quests:
            if q.id == quest_id:
                return q

        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics.

        Returns:
            Dict with counts and capacity info.
        """
        return {
            "active": len(self.active_quests),
            "completed": len(self.completed_quests),
            "failed": len(self.failed_quests),
            "total": len(self.active_quests) + len(self.completed_quests) + len(self.failed_quests),
            "max_active": self.max_active,
            "capacity_remaining": self.max_active - len(self.active_quests),
        }