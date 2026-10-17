"""Timeline Recorder - Records player choices and consequences permanently.

This module provides the TimelineRecorder class that maintains a
permanent record of all player choices and their consequences.
Entries cannot be removed, ensuring the narrative history is immutable.

Core principle: History cannot be erased. Every choice is recorded
and shapes the future of the world.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .choice_models import ConsequenceRecord, PlayerChoice, TimelineEntry


class TimelineRecorder:
    """Records player choices and consequences permanently.

    The TimelineRecorder maintains an append-only log of player
    decisions. This creates a narrative history that can be used
    for:
    - World state persistence
    - Narrative rendering (telling the story of what happened)
    - Irreversible state guarantees
    - Player achievement tracking

    Usage:
        recorder = TimelineRecorder()
        entry = recorder.record(choice, consequences, world_state)
    """

    def __init__(self, entries: Optional[List[TimelineEntry]] = None):
        """Initialize the TimelineRecorder.

        Args:
            entries: Optional list of existing entries to load.
        """
        self.entries: List[TimelineEntry] = entries if entries else []

    def record(
        self,
        world_state: Dict[str, Any],
        choice: PlayerChoice,
        consequences: List[ConsequenceRecord],
    ) -> TimelineEntry:
        """Record a player choice and its consequences.

        Creates a permanent timeline entry that cannot be undone.

        Args:
            world_state: World state dict (will have timeline updated).
            choice: The player choice being recorded.
            consequences: List of ConsequenceRecord objects.

        Returns:
            New TimelineEntry that was recorded.
        """
        # Convert consequences to serializable dicts
        consequence_dicts = [c.to_dict() for c in consequences]

        # Collect any tags from consequences
        tags = []
        for c in consequences:
            if c.consequence_type == "tag_add":
                tag = c.data.get("tag", "")
                if tag:
                    tags.append(tag)

        # Create timeline entry
        entry = TimelineEntry(
            choice_id=choice.id,
            option_selected=dict(choice.selected_option) if choice.selected_option else {},
            consequences=consequence_dicts,
            tags=tags,
        )

        # Add to internal list
        self.entries.append(entry)

        # Update world state timeline (persistent storage)
        world_state.setdefault("timeline", [])
        world_state["timeline"].append(entry.to_dict())

        # Also update history_flags
        for tag in tags:
            world_state.setdefault("history_flags", set())
            world_state["history_flags"].add(tag)

        return entry

    def get_entry_count(self) -> int:
        """Get the total number of entries.

        Returns:
            Total entry count.
        """
        return len(self.entries)

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the timeline.

        Returns:
            Summary dict with key timeline information.
        """
        return {
            "total_entries": len(self.entries),
            "tags": list(set(
                tag for entry in self.entries for tag in entry.tags
            )),
            "quest_ids": list(set(
                entry.choice_id for entry in self.entries
            )),
        }

    @classmethod
    def from_world_state(
        cls,
        world_state: Dict[str, Any],
    ) -> "TimelineRecorder":
        """Load timeline from world state.

        Args:
            world_state: World state dict with timeline data.

        Returns:
            New TimelineRecorder with loaded entries.
        """
        timeline_data = world_state.get("timeline", [])
        entries = []
        for data in timeline_data:
            entry = TimelineEntry(
                id=data.get("id", ""),
                choice_id=data.get("choice_id", ""),
                option_selected=data.get("option_selected", {}),
                consequences=data.get("consequences", []),
                timestamp=data.get("timestamp", 0),
                tags=data.get("tags", []),
            )
            entries.append(entry)
        return cls(entries=entries)

    def clear(self) -> int:
        """Clear all entries. WARNING: This breaks irreversibility.

        Note: This method exists only for testing. In normal
        gameplay, the timeline should never be cleared.

        Returns:
            Number of entries cleared.
        """
        count = len(self.entries)
        self.entries.clear()
        return count